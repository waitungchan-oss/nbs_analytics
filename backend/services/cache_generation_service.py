from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Mapping

from .revenue_generation_service import (
    CoreRevenueSignature,
    build_core_revenue_signature,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GENERATION_PATH = PROJECT_ROOT / ".nbs_runtime" / "data_generation.json"
GENERATION_SCHEMA_VERSION = "nbs-data-generation-v2"
GENERATION_SIGNATURE_SCOPE = "CORE_REVENUE"


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
        "currentDbSignature": {},
        "signatureMatched": False,
        "fileSignatureMatched": False,
        "coreRevenueToken": None,
        "cacheToken": "0:missing",
        "legacyMode": False,
        "migrationRequired": False,
    }


def _load_payload(target: Path) -> tuple[dict, str | None]:
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (json.JSONDecodeError, OSError) as exc:
        return {}, f"generation metadata unavailable: {type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return {}, "generation metadata must be a JSON object"
    return value, None


def _core_signature_dict(value: CoreRevenueSignature | Mapping[str, object]) -> dict:
    if isinstance(value, CoreRevenueSignature):
        return value.to_dict()
    return dict(value)


def _load_current_core_signature(
    db_path: str | Path | None,
    *,
    read_only: bool = False,
) -> tuple[dict, str | None]:
    if db_path is None:
        return {}, "core revenue database path is unavailable"
    try:
        if read_only:
            return build_core_revenue_signature(db_path, read_only=True).to_dict(), None
        return build_core_revenue_signature(db_path).to_dict(), None
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        return {}, f"core revenue signature unavailable: {type(exc).__name__}: {exc}"


def _derived_generation(
    value: dict,
    *,
    current_file_signature: dict,
    current_core_signature: dict,
    core_signature_error: str | None,
    metadata_error: str | None,
) -> dict:
    schema_version = value.get("schemaVersion")
    is_v2 = schema_version == GENERATION_SCHEMA_VERSION
    is_uninitialized = (
        value.get("generation", 0) in (0, None)
        and value.get("status") == "uninitialized"
        and not value.get("dbSignature")
        and not value.get("coreRevenueSignature")
    )
    is_legacy = bool(value) and not is_v2 and not is_uninitialized
    stored_file_signature = value.get("dbSignature")
    if not isinstance(stored_file_signature, dict):
        stored_file_signature = {}
    stored_core_signature = value.get("coreRevenueSignature")
    if not isinstance(stored_core_signature, dict):
        stored_core_signature = {}

    file_matched = bool(current_file_signature) and (
        stored_file_signature.get("sha256") == current_file_signature.get("sha256")
    )
    if is_v2:
        signature_matched = bool(current_core_signature) and (
            stored_core_signature.get("token") == current_core_signature.get("token")
        )
        current_token = str(current_core_signature.get("token") or "missing")
        core_revenue_token = current_core_signature.get("token")
    else:
        signature_matched = file_matched
        current_token = str(current_file_signature.get("sha256") or "missing")
        core_revenue_token = None

    generation = int(value.get("generation", 0) or 0)
    result = {
        **value,
        "currentDbSignature": current_file_signature,
        "signatureMatched": signature_matched,
        "fileSignatureMatched": file_matched,
        "coreRevenueToken": core_revenue_token,
        "cacheToken": f"{generation}:{current_token}",
        "legacyMode": is_legacy,
        "migrationRequired": is_legacy or bool(metadata_error) or (
            bool(schema_version) and not is_v2
        ),
    }
    if is_v2:
        result["currentCoreRevenueSignature"] = current_core_signature
        result["coreSignatureError"] = core_signature_error
    if metadata_error:
        result["metadataError"] = metadata_error
    return result


def load_cache_generation(
    path: str | Path | None = None,
    *,
    db_path: str | Path | None = None,
    read_only: bool = False,
) -> dict:
    target = Path(path or DEFAULT_GENERATION_PATH)
    value, metadata_error = _load_payload(target)
    current_file_signature = _db_signature(db_path)
    if not value and metadata_error is None:
        return _derived_generation(
            _empty_generation(),
            current_file_signature=current_file_signature,
            current_core_signature={},
            core_signature_error=None,
            metadata_error=None,
        )

    current_core_signature, core_signature_error = (
        _load_current_core_signature(db_path, read_only=read_only)
        if value.get("schemaVersion") == GENERATION_SCHEMA_VERSION
        else ({}, None)
    )
    return _derived_generation(
        value or _empty_generation(),
        current_file_signature=current_file_signature,
        current_core_signature=current_core_signature,
        core_signature_error=core_signature_error,
        metadata_error=metadata_error,
    )


def _atomic_write_json(target: Path, value: Mapping[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def _v2_payload(
    *,
    generation: int,
    operation_id: str | None,
    status: str,
    db_signature: dict,
    core_signature: CoreRevenueSignature | Mapping[str, object],
) -> dict:
    return {
        "schemaVersion": GENERATION_SCHEMA_VERSION,
        "signatureScope": GENERATION_SIGNATURE_SCOPE,
        "generation": generation,
        "operationId": operation_id,
        "status": status,
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dbSignature": dict(db_signature),
        "coreRevenueSignature": _core_signature_dict(core_signature),
    }


def advance_cache_generation(
    *,
    db_path: str | Path,
    operation_id: str,
    status: str,
    path: str | Path | None = None,
    core_signature: CoreRevenueSignature | Mapping[str, object] | None = None,
) -> dict:
    target = Path(path or DEFAULT_GENERATION_PATH)
    db = Path(db_path)
    previous = load_cache_generation(target, db_path=db)
    signature = core_signature or build_core_revenue_signature(db)
    value = _v2_payload(
        generation=int(previous.get("generation", 0)) + 1,
        operation_id=str(operation_id),
        status=str(status),
        db_signature=_db_signature(db),
        core_signature=signature,
    )
    _atomic_write_json(target, value)
    return load_cache_generation(target, db_path=db)

def refresh_cache_generation_signature(
    *,
    db_path: str | Path,
    path: str | Path | None = None,
    core_signature: CoreRevenueSignature | Mapping[str, object] | None = None,
) -> dict:
    target = Path(path or DEFAULT_GENERATION_PATH)
    db = Path(db_path)
    file_signature = _db_signature(db)
    if not file_signature:
        raise FileNotFoundError(f"database signature unavailable: {db}")
    previous = load_cache_generation(target, db_path=db)
    signature = core_signature or build_core_revenue_signature(db)
    value = _v2_payload(
        generation=int(previous.get("generation", 0)),
        operation_id=previous.get("operationId"),
        status=str(previous.get("status") or "uninitialized"),
        db_signature=file_signature,
        core_signature=signature,
    )
    _atomic_write_json(target, value)
    return load_cache_generation(target, db_path=db)
