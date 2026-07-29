from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .governance_graph_models import GovernanceGraphSchemaError, GovernanceGraphSnapshot


GRAPH_FILE = "governance-graph.json"
MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024
_STATUSES = frozenset({"available", "unavailable", "invalid"})


@dataclass(frozen=True)
class SnapshotReadResult:
    status: str
    snapshot: GovernanceGraphSnapshot | None
    snapshot_identity: dict[str, Any] | None
    diagnostics: tuple[dict[str, str], ...]

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError("snapshot read status is invalid")
        if self.status == "available" and self.snapshot is None:
            raise ValueError("available snapshot result must include a snapshot")
        if self.status != "available" and self.snapshot is not None:
            raise ValueError("unavailable or invalid snapshot result cannot include a snapshot")


class GovernanceGraphSnapshotReader:
    """Read and validate an existing run-contained graph snapshot without writes."""

    def __init__(self, project_root: Path, runtime_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        self.runtime_root = Path(runtime_root) if runtime_root is not None else self.project_root / ".nbs_agent_runtime"
        self._assert_directory(self.runtime_root, "runtime root")
        self.runs_root = self.runtime_root / "runs"
        self._assert_directory(self.runs_root, "runs root")

    def read(self, run_id: str, expected_fingerprint: str | None = None) -> SnapshotReadResult:
        try:
            run_dir = self._run_dir(run_id)
        except ValueError:
            return self._invalid("invalid_run_id")
        if not run_dir.exists():
            return self._unavailable("missing_run")
        snapshot_path = run_dir / GRAPH_FILE
        if snapshot_path.is_symlink():
            return self._invalid("unsafe_snapshot")
        if not snapshot_path.exists():
            return self._unavailable("missing_snapshot")
        try:
            self._assert_regular_file(snapshot_path, "graph snapshot")
            if snapshot_path.stat().st_size > MAX_SNAPSHOT_BYTES:
                raise ValueError("graph snapshot exceeds hard cap")
            snapshot = GovernanceGraphSnapshot.from_dict(self._read_json(snapshot_path))
            if snapshot.run_id != run_id:
                raise ValueError("graph snapshot run ID does not match run ID")
            if expected_fingerprint is not None and snapshot.graph_fingerprint != expected_fingerprint:
                raise ValueError("snapshot fingerprint does not match expected fingerprint")
        except (OSError, ValueError, GovernanceGraphSchemaError, json.JSONDecodeError):
            return self._invalid("invalid_snapshot")
        identity = {
            "runId": snapshot.run_id,
            "graphFingerprint": snapshot.graph_fingerprint,
            "generatedAt": snapshot.generated_at,
            "freshness": snapshot.freshness["status"],
        }
        return SnapshotReadResult("available", snapshot, identity, ())

    def _run_dir(self, run_id: str) -> Path:
        if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
            raise ValueError("runId must be a safe single path component")
        path = self.runs_root / run_id
        try:
            path.resolve(strict=False).relative_to(self.runs_root.resolve())
        except ValueError as exc:
            raise ValueError("runId escapes runs root") from exc
        if path.is_symlink():
            raise ValueError("run directory must not be a symlink")
        if path.exists():
            self._assert_directory(path, "run directory")
        return path

    @staticmethod
    def _assert_directory(path: Path, label: str) -> None:
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f"{label} must be a regular directory")

    @staticmethod
    def _assert_regular_file(path: Path, label: str) -> None:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{label} must be a regular file")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        def reject_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value

            return result

        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle, object_pairs_hook=reject_duplicates)
        if not isinstance(payload, dict):
            raise ValueError("graph snapshot must be an object")
        return payload

    @staticmethod
    def _unavailable(code: str) -> SnapshotReadResult:
        return SnapshotReadResult("unavailable", None, None, ({"code": code},))

    @staticmethod
    def _invalid(code: str) -> SnapshotReadResult:
        return SnapshotReadResult("invalid", None, None, ({"code": code},))


__all__ = ["GovernanceGraphSnapshotReader", "SnapshotReadResult"]
