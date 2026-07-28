"""Read final canonical evidence as a bounded, redacted projection."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

from .canonical_evidence_models import CanonicalEvidenceEnvelope, CanonicalEvidenceSchemaError
from .canonical_evidence_registry import CanonicalEvidenceRegistry, CanonicalEvidenceRegistryEntry
from .workflow_models import WorkflowApproval, WorkflowManifest, WorkflowSchemaError


DEFAULT_ARTIFACT_MAX_BYTES = 5 * 1024 * 1024
_COMPACT_KEYS = ("state", "status", "reason", "finalizedAt", "artifact", "sha256")


class CanonicalEvidenceReader:
    """Safely project the fixed registry artifacts for one retained run.

    This reader never exposes canonical payloads, filesystem paths, or parser
    diagnostics.  Callers receive only a fixed-size map keyed by registry kind.
    """

    def __init__(self, project_root: Path, runtime_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        candidate = Path(runtime_root) if runtime_root is not None else self.project_root / ".nbs_agent_runtime"
        self.runtime_root = self._safe_runtime_root(candidate)
        self.runs_root = self.runtime_root / "runs"
        self.registry = CanonicalEvidenceRegistry()

    def read(self, run_dir: Path, hard_cap: int = DEFAULT_ARTIFACT_MAX_BYTES) -> dict[str, dict[str, Any]]:
        entries = self.registry.entries()
        if not isinstance(hard_cap, int) or isinstance(hard_cap, bool) or hard_cap <= 0:
            return {entry.artifact_kind: self._invalid(entry) for entry in entries}
        try:
            self._safe_runs_root()
            safe_run_dir = self._safe_run_dir(Path(run_dir))
            self._load_manifest(safe_run_dir, hard_cap)
        except (OSError, ValueError):
            return {entry.artifact_kind: self._invalid(entry) for entry in entries}
        try:
            approval = self._load_approval(safe_run_dir, hard_cap)
        except (OSError, ValueError, WorkflowSchemaError, json.JSONDecodeError, UnicodeError):
            return {entry.artifact_kind: self._invalid(entry) for entry in entries}
        return {entry.artifact_kind: self._read_entry(safe_run_dir, approval, entry, hard_cap) for entry in entries}

    def _safe_runtime_root(self, candidate: Path) -> Path:
        if candidate.is_symlink():
            raise ValueError("runtime root must not be a symlink")
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(self.project_root)
        return resolved

    def _safe_run_dir(self, candidate: Path) -> Path:
        runs_root = self._safe_runs_root()
        if ".." in candidate.parts or candidate.is_symlink() or not candidate.is_dir():
            raise ValueError("run directory is unsafe")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(runs_root)
        if resolved.parent != runs_root or resolved.name in {"", ".", ".."}:
            raise ValueError("run directory is not a direct child")
        return resolved

    def _safe_runs_root(self) -> Path:
        if self.runs_root.is_symlink() or self.runs_root.exists() and not self.runs_root.is_dir():
            raise ValueError("runs root is unsafe")
        resolved = self.runs_root.resolve(strict=False)
        resolved.relative_to(self.runtime_root)
        return resolved

    def _load_manifest(self, run_dir: Path, hard_cap: int) -> WorkflowManifest:
        payload = self._load_json(run_dir / "manifest.json", run_dir, hard_cap, optional=False)
        manifest = WorkflowManifest.from_dict(payload)
        if manifest.run_id != run_dir.name:
            raise ValueError("manifest run binding is invalid")
        return manifest

    def _load_approval(self, run_dir: Path, hard_cap: int) -> WorkflowApproval:
        payload = self._load_json(run_dir / "approval.json", run_dir, hard_cap, optional=False)
        approval = WorkflowApproval.from_dict(payload)
        if approval.run_id != run_dir.name:
            raise ValueError("approval run binding is invalid")
        return approval

    def _read_entry(
        self,
        run_dir: Path,
        approval: WorkflowApproval,
        entry: CanonicalEvidenceRegistryEntry,
        hard_cap: int,
    ) -> dict[str, Any]:
        try:
            payload = self._load_json(run_dir / entry.filename, run_dir, hard_cap, optional=True)
            if payload is None:
                return self._unknown(entry)
            envelope = CanonicalEvidenceEnvelope.from_dict(payload, expected_kind=entry.artifact_kind)
            if (
                envelope.run_id != run_dir.name
                or envelope.contract_fingerprint != approval.contract_fingerprint
            ):
                return self._invalid(entry)
            if not envelope.is_finalized:
                return self._unknown(entry, reason="not_finalized")
            status = "blocked" if envelope.status == "blocked" else "available"
            return self._compact(
                state=envelope.status,
                status=status,
                reason=envelope.reason_code,
                finalized_at=envelope.lifecycle["finalizedAt"],
                artifact=entry.filename,
                sha256=envelope.evidence_fingerprint,
            )
        except (OSError, ValueError, CanonicalEvidenceSchemaError, json.JSONDecodeError, UnicodeError):
            return self._invalid(entry)

    @staticmethod
    def _load_json(path: Path, container: Path, hard_cap: int, *, optional: bool) -> dict[str, Any] | None:
        try:
            path.resolve(strict=False).relative_to(container.resolve(strict=True))
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("artifact escapes run directory") from exc
        try:
            details = path.lstat()
        except FileNotFoundError:
            if optional:
                return None
            raise
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_size > hard_cap:
            raise ValueError("artifact is unsafe")
        with path.open("rb") as handle:
            raw = handle.read(hard_cap + 1)
        if len(raw) > hard_cap:
            raise ValueError("artifact exceeds cap")
        def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            payload: dict[str, Any] = {}
            for key, value in pairs:
                if key in payload:
                    raise ValueError("duplicate JSON key")
                payload[key] = value
            return payload

        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=object_without_duplicates)
        if not isinstance(payload, dict):
            raise ValueError("artifact must be an object")
        return payload

    @staticmethod
    def _compact(*, state: str, status: str, reason: str | None, finalized_at: str | None, artifact: str, sha256: str | None) -> dict[str, Any]:
        return dict(zip(_COMPACT_KEYS, (state, status, reason, finalized_at, artifact, sha256), strict=True))

    @classmethod
    def _unknown(cls, entry: CanonicalEvidenceRegistryEntry, *, reason: str = "missing") -> dict[str, Any]:
        return cls._compact(state="unknown", status="unknown", reason=reason, finalized_at=None, artifact=entry.filename, sha256=None)

    @classmethod
    def _invalid(cls, entry: CanonicalEvidenceRegistryEntry) -> dict[str, Any]:
        return cls._compact(state="invalid", status="invalid", reason="invalid_evidence", finalized_at=None, artifact=entry.filename, sha256=None)


__all__ = ["CanonicalEvidenceReader", "DEFAULT_ARTIFACT_MAX_BYTES"]
