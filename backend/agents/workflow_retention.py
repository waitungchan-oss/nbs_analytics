from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .workflow_models import MANIFEST_SCHEMA, STATUS_SCHEMA, TERMINAL_STATUSES
from .workflow_store import WorkflowLockedError, WorkflowStore


RETENTION_SCHEMA = "agent-workflow-retention-v1"
STAGE_ARTIFACTS = frozenset(
    {
        "context.json",
        "implementation.json",
        "targeted-verification.json",
        "review.json",
        "full-verification.json",
        "hermes.json",
    }
)
PERMANENT_ARTIFACTS = frozenset({"manifest.json", "status.json", "approval.json", "events.jsonl", ".lock"})


@dataclass(frozen=True)
class RetentionPolicy:
    retain_days: int
    retain_latest_terminal_runs: int
    stage_artifact_max_bytes: int
    run_artifact_soft_cap_bytes: int
    command_output_tail_characters: int

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RetentionPolicy":
        required = {
            "schemaVersion",
            "retainDays",
            "retainLatestTerminalRuns",
            "stageArtifactMaxBytes",
            "runArtifactSoftCapBytes",
            "commandOutputTailCharacters",
        }
        if set(payload) != required or payload.get("schemaVersion") != RETENTION_SCHEMA:
            raise ValueError("retention policy schema is invalid")
        values = {key: payload[key] for key in required - {"schemaVersion"}}
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values.values()):
            raise ValueError("retention policy limits must be non-negative integers")
        if values["retainDays"] == 0 or values["retainLatestTerminalRuns"] == 0:
            raise ValueError("retention policy must retain a positive time and terminal set")
        return cls(
            retain_days=values["retainDays"],
            retain_latest_terminal_runs=values["retainLatestTerminalRuns"],
            stage_artifact_max_bytes=values["stageArtifactMaxBytes"],
            run_artifact_soft_cap_bytes=values["runArtifactSoftCapBytes"],
            command_output_tail_characters=values["commandOutputTailCharacters"],
        )

    @classmethod
    def from_path(cls, path: Path) -> "RetentionPolicy":
        with Path(path).open(encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))


@dataclass(frozen=True)
class RetentionCandidate:
    run_id: str
    action: str
    reasons: tuple[str, ...]
    delete_paths: tuple[str, ...]
    estimated_bytes: int


@dataclass(frozen=True)
class RetentionReport:
    generated_at: str
    candidates: tuple[RetentionCandidate, ...]
    skipped: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class _RunInfo:
    run_id: str
    path: Path
    created_at: datetime
    status: str


class WorkflowRetention:
    def __init__(self, project_root: Path, *, policy: RetentionPolicy | None = None, config_path: Path | None = None) -> None:
        self.project_root = Path(project_root)
        self.store = WorkflowStore(self.project_root)
        if policy is not None and config_path is not None:
            raise ValueError("provide policy or config_path, not both")
        self.policy = policy or RetentionPolicy.from_path(
            config_path or self.project_root / "agent_config" / "workflow_retention.json"
        )

    def plan(self, now: datetime | None = None) -> RetentionReport:
        now = _aware(now or datetime.now(timezone.utc))
        run_infos: list[_RunInfo] = []
        skipped: list[dict[str, str]] = []
        for path in sorted(self.store.runs_root.iterdir(), key=lambda item: item.name):
            if path.is_symlink() or not path.is_dir():
                skipped.append({"path": str(path), "reason": "run path is not a regular directory"})
                continue
            try:
                info = self._read_run(path)
                self._assert_run_contents(path)
                self._check_lock(info.run_id, path)
            except (OSError, ValueError, json.JSONDecodeError, WorkflowLockedError) as exc:
                skipped.append({"path": str(path), "reason": str(exc)})
                continue
            run_infos.append(info)

        terminal = sorted(
            (item for item in run_infos if item.status in TERMINAL_STATUSES),
            key=lambda item: (item.created_at, item.run_id),
            reverse=True,
        )
        latest_ids = {item.run_id for item in terminal[: self.policy.retain_latest_terminal_runs]}
        cutoff = now - timedelta(days=self.policy.retain_days)
        candidates = []
        for info in run_infos:
            if info.status != "completed" or info.created_at >= cutoff or info.run_id in latest_ids:
                continue
            delete_paths = []
            for name in sorted(STAGE_ARTIFACTS):
                path = info.path / name
                if path.is_symlink() or not path.exists():
                    if path.is_symlink():
                        delete_paths = []
                        break
                    continue
                if not path.is_file():
                    delete_paths = []
                    break
                delete_paths.append(name)
            if delete_paths:
                candidates.append(
                    RetentionCandidate(
                        run_id=info.run_id,
                        action="compact",
                        reasons=("older_than_retain_days", "completed_stage_reports"),
                        delete_paths=tuple(delete_paths),
                        estimated_bytes=sum((info.path / name).stat().st_size for name in delete_paths),
                    )
                )
        return RetentionReport(generated_at=now.isoformat(), candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, report: RetentionReport, *, dry_run: bool = False) -> RetentionReport:
        if dry_run:
            return report
        for candidate in report.candidates:
            run_dir = self._candidate_run_dir(candidate.run_id)
            for name in candidate.delete_paths:
                if Path(name).name != name or name in PERMANENT_ARTIFACTS or name not in STAGE_ARTIFACTS:
                    raise PermissionError("retention report contains an unsafe artifact path")
            if not run_dir.is_dir() or run_dir.is_symlink():
                raise PermissionError("retention run directory is not safe")
            summary = {
                "schemaVersion": "agent-workflow-archive-summary-v1",
                "runId": candidate.run_id,
                "generatedAt": report.generated_at,
                "action": candidate.action,
                "reasons": list(candidate.reasons),
                "deletedFiles": list(candidate.delete_paths),
                "deletedBytes": candidate.estimated_bytes,
            }
            with self.store.run_lock(candidate.run_id):
                self._write_summary(run_dir / "archive-summary.json", summary)
                for name in candidate.delete_paths:
                    path = self._contained(run_dir / name, run_dir)
                    if path.is_symlink() or not path.is_file():
                        raise PermissionError("stage artifact is not a regular file")
                    path.unlink()
        return report

    @staticmethod
    def _write_summary(path: Path, payload: dict[str, Any]) -> None:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise PermissionError("archive summary target is not a regular file")
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        temporary = path.with_name(f".{path.name}.retention-tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_run(self, path: Path) -> _RunInfo:
        manifest = _read_object(path / "manifest.json")
        status = _read_object(path / "status.json")
        if manifest.get("schemaVersion") != MANIFEST_SCHEMA or status.get("schemaVersion") != STATUS_SCHEMA:
            raise ValueError("unknown workflow schema")
        run_id = manifest.get("runId")
        if not isinstance(run_id, str) or run_id != path.name or status.get("runId") != run_id:
            raise ValueError("workflow run identity is invalid")
        value = _parse_timestamp(manifest.get("createdAt"))
        status_value = status.get("status")
        if not isinstance(status_value, str):
            raise ValueError("workflow status is invalid")
        return _RunInfo(run_id=run_id, path=path, created_at=value, status=status_value)

    def _assert_run_contents(self, run_dir: Path) -> None:
        self._contained(run_dir, self.store.runs_root)
        for child in run_dir.iterdir():
            if child.is_symlink():
                raise PermissionError("run artifact is a symlink")
            self._contained(child, run_dir)

    def _check_lock(self, run_id: str, run_dir: Path) -> None:
        lock = run_dir / ".lock"
        if lock.exists() or lock.is_symlink():
            with self.store.run_lock(run_id):
                pass

    def _candidate_run_dir(self, run_id: str) -> Path:
        if Path(run_id).name != run_id:
            raise PermissionError("retention run path escapes runs directory")
        run_dir = self._contained(self.store.runs_root / run_id, self.store.runs_root)
        return run_dir

    @staticmethod
    def _contained(path: Path, parent: Path) -> Path:
        try:
            path.relative_to(parent)
            path.resolve(strict=False).relative_to(parent.resolve())
        except ValueError as exc:
            raise PermissionError("retention path escapes runs directory") from exc
        return path


def _read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PermissionError(f"workflow artifact is not a regular file: {path.name}")
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("workflow artifact must be an object")
    return value


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("workflow timestamp is invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _aware(parsed)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
