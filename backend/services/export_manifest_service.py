"""Versioned local export artifacts and ZIP publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Mapping


EXPORT_MANIFEST_SCHEMA = "export-manifest-v2"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    key: str
    filename: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ManifestArtifact:
    key: str
    filename: str
    path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class ExportManifest:
    schema: str
    status: str
    generation_token: str
    rules_fingerprint: str
    export_schema_version: str
    equivalence_status: str
    artifacts: Mapping[str, ManifestArtifact]
    path: Path
    telemetry: Mapping[str, object] = field(default_factory=dict)
    reference: Mapping[str, object] = field(default_factory=dict)


def _safe(value: str) -> str:
    return _SAFE_NAME.sub("-", str(value)).strip(".-") or "export"


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def publish_export_manifest(
    root: Path,
    *,
    generation_token: str,
    rules_fingerprint: str,
    export_schema_version: str,
    artifacts: Mapping[str, ExportArtifact],
    equivalence_status: str,
    telemetry: Mapping[str, object] | None = None,
    equivalence_report: Mapping[str, object] | None = None,
    reference: Mapping[str, object] | None = None,
) -> Path:
    root = Path(root)
    artifact_dir = root / "artifacts" / _safe(generation_token)
    manifest_artifacts = {}
    for key, artifact in sorted(artifacts.items()):
        filename = _safe(artifact.filename)
        relative_path = Path("artifacts") / _safe(generation_token) / filename
        _atomic_write(root / relative_path, artifact.content)
        manifest_artifacts[key] = {
            "key": key,
            "filename": filename,
            "path": relative_path.as_posix(),
            "sha256": hashlib.sha256(artifact.content).hexdigest(),
            "size": len(artifact.content),
        }
    telemetry_payload = dict(telemetry or {})
    payload = {
        "schema": EXPORT_MANIFEST_SCHEMA,
        "status": "READY" if equivalence_status == "PASS" else "FAILED",
        "generation_token": str(generation_token),
        "rules_fingerprint": str(rules_fingerprint),
        "export_schema_version": str(export_schema_version),
        "equivalence_status": str(equivalence_status),
        "artifacts": manifest_artifacts,
        "telemetry": telemetry_payload,
        "reference": dict(reference or {}),
    }
    _atomic_write(
        root / "equivalence-report.json",
        json.dumps(dict(equivalence_report or {"status": equivalence_status, "mismatch_count": 0}), ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
    )
    manifest_path = root / "manifest.json"
    staging_manifest_path = root / ".manifest-staging.json"
    staging_artifacts = {
        key: ManifestArtifact(
            key=item["key"], filename=item["filename"], path=item["path"],
            sha256=item["sha256"], size=int(item["size"]),
        )
        for key, item in manifest_artifacts.items()
    }
    _atomic_write(staging_manifest_path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
    staging_manifest = ExportManifest(
        EXPORT_MANIFEST_SCHEMA, payload["status"], str(generation_token),
        str(rules_fingerprint), str(export_schema_version), str(equivalence_status),
        staging_artifacts, staging_manifest_path, telemetry_payload,
        dict(reference or {}),
    )
    package_started = time.perf_counter()
    package_path = build_export_package(staging_manifest, root)
    telemetry_payload["package_ms"] = round((time.perf_counter() - package_started) * 1000)
    payload["telemetry"] = telemetry_payload
    _atomic_write(staging_manifest_path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
    package_path = build_export_package(staging_manifest, root)
    if not verify_export_package(package_path, staging_manifest):
        staging_manifest_path.unlink(missing_ok=True)
        raise ValueError("export package verification failed")
    staging_manifest_path.unlink(missing_ok=True)
    _atomic_write(manifest_path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
    return manifest_path


def load_ready_export_manifest(path: Path) -> ExportManifest | None:
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != EXPORT_MANIFEST_SCHEMA or payload.get("status") != "READY" or payload.get("equivalence_status") != "PASS":
            return None
        root = path.parent.resolve()
        artifacts = {}
        for key, item in payload["artifacts"].items():
            artifact_path = (root / item["path"]).resolve()
            if root not in artifact_path.parents or not artifact_path.is_file():
                return None
            content = artifact_path.read_bytes()
            if hashlib.sha256(content).hexdigest() != item["sha256"] or len(content) != int(item["size"]):
                return None
            artifacts[key] = ManifestArtifact(key, item["filename"], item["path"], item["sha256"], int(item["size"]))
        return ExportManifest(payload["schema"], payload["status"], payload["generation_token"], payload["rules_fingerprint"], payload["export_schema_version"], payload["equivalence_status"], artifacts, path, payload.get("telemetry") or {}, payload.get("reference") or {})
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def build_export_package(manifest: ExportManifest, root: Path | None = None) -> Path:
    root = Path(root) if root is not None else manifest.path.parent
    package_path = root / f"export-package-{_safe(manifest.generation_token)}.zip"
    with NamedTemporaryFile(dir=root, prefix=".export-package.", suffix=".zip", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for artifact in manifest.artifacts.values():
                package.write(root / artifact.path, arcname=artifact.filename)
            package.write(manifest.path, arcname="export-manifest.json")
            equivalence_path = root / "equivalence-report.json"
            if not equivalence_path.is_file():
                _atomic_write(
                    equivalence_path,
                    json.dumps({"status": manifest.equivalence_status, "mismatch_count": 0}, sort_keys=True).encode("utf-8"),
                )
            package.write(equivalence_path, arcname="equivalence-report.json")
        os.replace(temporary_path, package_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return package_path


def verify_export_package(package_path: Path, manifest: ExportManifest) -> bool:
    """Verify package membership and workbook bytes against a READY manifest."""
    try:
        expected = {artifact.filename for artifact in manifest.artifacts.values()} | {
            "export-manifest.json", "equivalence-report.json",
        }
        with zipfile.ZipFile(package_path) as package:
            if set(package.namelist()) != expected:
                return False
            packaged_manifest = json.loads(package.read("export-manifest.json"))
            if packaged_manifest.get("schema") != EXPORT_MANIFEST_SCHEMA or packaged_manifest.get("status") != "READY":
                return False
            for artifact in manifest.artifacts.values():
                if hashlib.sha256(package.read(artifact.filename)).hexdigest() != artifact.sha256:
                    return False
        return True
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError, zipfile.BadZipFile):
        return False
