"""Versioned local export artifacts and ZIP publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from dataclasses import dataclass
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
    payload = {
        "schema": EXPORT_MANIFEST_SCHEMA,
        "status": "READY" if equivalence_status == "PASS" else "FAILED",
        "generation_token": str(generation_token),
        "rules_fingerprint": str(rules_fingerprint),
        "export_schema_version": str(export_schema_version),
        "equivalence_status": str(equivalence_status),
        "artifacts": manifest_artifacts,
    }
    manifest_path = root / "manifest.json"
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
        return ExportManifest(payload["schema"], payload["status"], payload["generation_token"], payload["rules_fingerprint"], payload["export_schema_version"], payload["equivalence_status"], artifacts, path)
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
        os.replace(temporary_path, package_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return package_path
