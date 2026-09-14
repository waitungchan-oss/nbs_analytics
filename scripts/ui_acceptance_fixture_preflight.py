"""Read-only source and cache identity checks for UI acceptance fixtures."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from backend.agents.acceptance_paths import is_temporary_path
from backend.services.gmv_export_cache_service import (
    CANONICAL_CACHE_ARTIFACT_KEYS,
    gmv_export_cache_key,
)


_SOURCE_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


class UiFixturePreflightError(ValueError):
    """Raised only when the fixture identity gate blocks UI execution."""


def build_ui_fixture_blocker(
    reason: str,
    *,
    active_version_id: str | None = None,
    revenue_generation_token: str | None = None,
    rule_version: str | None = None,
    project_root: Path | None = None,
    source_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Build a bounded blocker without including raw business data."""
    payload: dict[str, Any] = {
        "schemaVersion": "ui-fixture-preflight-v1",
        "status": "BLOCKED",
        "failureCode": "ui_fixture_cache_mismatch",
        "reason": str(reason),
    }
    if active_version_id is not None:
        payload["activeVersionId"] = active_version_id
    if revenue_generation_token is not None:
        payload["revenueGenerationToken"] = revenue_generation_token
    if rule_version is not None:
        payload["ruleVersion"] = rule_version
    if project_root is not None:
        payload["projectRoot"] = str(project_root)
    if source_fingerprint is not None:
        payload["sourceFingerprint"] = source_fingerprint
    return payload


def _path_reason(path: Path, label: str) -> str | None:
    if path.is_symlink():
        return "fixture_path_symlink"
    if not is_temporary_path(path):
        return "fixture_path_not_temporary"
    if any(marker in str(path).lower() for marker in ("nbs_marketing_data.db", "production.db", ".nbs_runtime")):
        return f"{label}_production_path"
    return None


def _active_scope(db_path: Path) -> tuple[dict[str, str] | None, str | None]:
    if not db_path.is_file():
        return None, "missing_active_version"
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT version_id, revenue_generation_token, rule_version "
                "FROM gmv_scope_versions WHERE status = 'ACTIVE'"
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.DatabaseError):
        return None, "missing_active_version"
    if len(row) != 1 or any(not str(value).strip() for value in row[0]):
        return None, "missing_active_version"
    return {
        "versionId": str(row[0][0]),
        "revenueGenerationToken": str(row[0][1]),
        "ruleVersion": str(row[0][2]),
    }, None


def _safe_child(root: Path, relative: str, label: str) -> Path:
    candidate = root / relative
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes root") from exc
    current = root
    for component in Path(relative).parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"{label} cannot traverse a symlink")
    candidate = candidate.resolve()
    candidate.relative_to(root.resolve())
    return candidate


def _inspect_cache(
    *, cache_dir: Path, version_id: str, revenue_generation_token: str, rule_version: str,
    source_fingerprint: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not cache_dir.is_dir():
        return None, "missing_cache_manifest"
    try:
        version_root = _safe_child(cache_dir, version_id, "cache version path")
        if not version_root.is_dir():
            return None, "missing_cache_manifest"
    except (OSError, ValueError):
        return None, "cache_version_path_invalid"
    pointer_path = version_root / "active.json"
    if not pointer_path.is_file():
        return None, "missing_cache_manifest"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        if not isinstance(pointer, dict):
            return None, "missing_cache_manifest"
        if pointer.get("schemaVersion") != "gmv-formal-export-active-pointer-v1":
            return None, "missing_cache_manifest"
        manifest_path = _safe_child(version_root, str(pointer["manifestPath"]), "manifest path")
        generation_path = _safe_child(version_root, str(pointer["generationPath"]), "generation path")
        if manifest_path.parent != generation_path:
            return None, "artifact_integrity_failure"
        manifest_bytes = manifest_path.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != str(pointer["manifestSha256"]):
            return None, "artifact_integrity_failure"
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if not isinstance(manifest, dict):
            return None, "artifact_integrity_failure"
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None, "missing_cache_manifest"

    expected_key = gmv_export_cache_key(
        version_id=version_id,
        revenue_generation_token=revenue_generation_token,
        rule_version=rule_version,
    )
    if manifest.get("versionId") != version_id:
        return None, "active_version_mismatch"
    if manifest.get("revenueGenerationToken") != revenue_generation_token:
        return None, "revenue_token_mismatch"
    if manifest.get("ruleVersion") != rule_version:
        return None, "rule_version_mismatch"
    if source_fingerprint is not None and manifest.get("sourceFingerprint") != source_fingerprint:
        return None, "source_fingerprint_mismatch"
    if manifest.get("cacheKey") != expected_key or manifest.get("status") != "ready":
        return None, "missing_cache_manifest"
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(CANONICAL_CACHE_ARTIFACT_KEYS):
        return None, "artifact_integrity_failure"
    try:
        for record in artifacts.values():
            if not isinstance(record, dict):
                return None, "artifact_integrity_failure"
            artifact_path = _safe_child(generation_path, str(record["path"]), "artifact path")
            data = artifact_path.read_bytes()
            if len(data) != int(record["bytes"]) or hashlib.sha256(data).hexdigest() != str(record["sha256"]):
                return None, "artifact_integrity_failure"
    except (OSError, KeyError, TypeError, ValueError):
        return None, "artifact_integrity_failure"
    return {
        "versionId": version_id,
        "revenueGenerationToken": revenue_generation_token,
        "ruleVersion": rule_version,
        "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }, None


def inspect_ui_fixture_cache(
    *, db_path: Path, cache_dir: Path, project_root: Path,
    source_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Inspect active DB/cache identity without creating or mutating either path."""
    source_root = Path(project_root).expanduser()
    if source_root.is_symlink() or not source_root.is_dir():
        return build_ui_fixture_blocker("project_root_invalid", source_fingerprint=source_fingerprint)
    source_root = source_root.resolve()
    if source_fingerprint is not None and not _SOURCE_FINGERPRINT.fullmatch(source_fingerprint):
        return build_ui_fixture_blocker(
            "source_fingerprint_invalid",
            project_root=source_root,
            source_fingerprint=source_fingerprint,
        )
    db = Path(db_path).expanduser()
    cache = Path(cache_dir).expanduser()
    for path, label in ((db, "db"), (cache, "cache")):
        reason = _path_reason(path, label)
        if reason:
            return build_ui_fixture_blocker(reason, project_root=source_root, source_fingerprint=source_fingerprint)
    db = db.resolve()
    cache = cache.resolve()
    active, reason = _active_scope(db)
    if reason:
        return build_ui_fixture_blocker(reason, project_root=source_root, source_fingerprint=source_fingerprint)
    assert active is not None
    manifest, reason = _inspect_cache(
        cache_dir=cache,
        version_id=active["versionId"],
        revenue_generation_token=active["revenueGenerationToken"],
        rule_version=active["ruleVersion"],
        source_fingerprint=source_fingerprint,
    )
    if reason:
        return build_ui_fixture_blocker(reason, **{
            "active_version_id": active["versionId"],
            "revenue_generation_token": active["revenueGenerationToken"],
            "rule_version": active["ruleVersion"],
            "project_root": source_root,
            "source_fingerprint": source_fingerprint,
        })
    return {
        "schemaVersion": "ui-fixture-preflight-v1",
        "status": "PASS",
        "reason": None,
        "projectRoot": str(source_root),
        "sourceFingerprint": source_fingerprint,
        "activeVersionId": active["versionId"],
        "revenueGenerationToken": active["revenueGenerationToken"],
        "ruleVersion": active["ruleVersion"],
        "cacheManifestSha256": manifest["manifestSha256"] if manifest else None,
    }


def require_source_matched_ui_fixture(**kwargs: Any) -> None:
    result = inspect_ui_fixture_cache(**kwargs)
    if result["status"] != "PASS":
        raise UiFixturePreflightError(f"UI fixture preflight blocked: {result['reason']}")
