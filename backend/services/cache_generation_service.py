from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GENERATION_PATH = PROJECT_ROOT / ".nbs_runtime" / "data_generation.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _db_signature(db_path: str | Path | None) -> dict:
    if db_path is None:
        return {}
    db = Path(db_path)
    if not db.exists() or not db.is_file():
        return {}
    return {
        "sizeBytes": db.stat().st_size,
        "modifiedNs": db.stat().st_mtime_ns,
        "sha256": _sha256(db),
    }


def _empty_generation() -> dict:
    return {
        "generation": 0,
        "operationId": None,
        "status": "uninitialized",
        "dbSignature": {},
    }


def load_cache_generation(
    path: str | Path | None = None,
    *,
    db_path: str | Path | None = None,
) -> dict:
    target = Path(path or DEFAULT_GENERATION_PATH)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        value = _empty_generation()
    if not isinstance(value, dict):
        value = _empty_generation()

    current_signature = _db_signature(db_path)
    stored_signature = value.get("dbSignature")
    if not isinstance(stored_signature, dict):
        stored_signature = {}
    return {
        **value,
        "currentDbSignature": current_signature,
        "signatureMatched": bool(current_signature)
        and stored_signature.get("sha256") == current_signature.get("sha256"),
        "cacheToken": f"{int(value.get('generation', 0))}:{current_signature.get('sha256', 'missing')}",
    }


def advance_cache_generation(
    *,
    db_path: str | Path,
    operation_id: str,
    status: str,
    path: str | Path | None = None,
) -> dict:
    target = Path(path or DEFAULT_GENERATION_PATH)
    db = Path(db_path)
    previous = load_cache_generation(target, db_path=db)
    value = {
        "generation": int(previous.get("generation", 0)) + 1,
        "operationId": str(operation_id),
        "status": str(status),
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dbSignature": _db_signature(db),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return load_cache_generation(target, db_path=db)


def refresh_cache_generation_signature(
    *,
    db_path: str | Path,
    path: str | Path | None = None,
) -> dict:
    target = Path(path or DEFAULT_GENERATION_PATH)
    db = Path(db_path)
    previous = load_cache_generation(target, db_path=db)
    signature = _db_signature(db)
    if not signature:
        raise FileNotFoundError(f"database signature unavailable: {db}")
    value = {
        "generation": int(previous.get("generation", 0)),
        "operationId": previous.get("operationId"),
        "status": str(previous.get("status") or "uninitialized"),
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dbSignature": signature,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return load_cache_generation(target, db_path=db)
