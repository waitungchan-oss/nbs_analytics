from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.agents.workflow_models import WorkflowManifest, WorkflowStatus
from backend.agents.workflow_retention import RetentionPolicy


SNAPSHOT_SCHEMA = "agent-operations-snapshot-v1"


class AgentOperationsService:
    def __init__(self, project_root: Path, runtime_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        candidate = Path(runtime_root) if runtime_root is not None else self.project_root / ".nbs_agent_runtime"
        self.runtime_root = self._safe_root(candidate)
        self.runs_root = self.runtime_root / "runs"
        self.retention_path = self.project_root / "agent_config" / "workflow_retention.json"

    def build_snapshot(self) -> dict[str, Any]:
        generated_at = datetime.now(timezone.utc).isoformat()
        diagnostics: list[dict[str, str]] = []
        runs = self._load_runs(diagnostics)
        runs.sort(key=lambda item: (item["updatedAt"], item["runId"]), reverse=True)
        return {
            "schemaVersion": SNAPSHOT_SCHEMA,
            "generatedAt": generated_at,
            "summary": self._summary(runs),
            "runs": runs,
            "retention": self._retention(diagnostics),
            "diagnostics": diagnostics,
        }

    def _safe_root(self, candidate: Path) -> Path:
        candidate = candidate.expanduser()
        if candidate.exists() and candidate.is_symlink():
            raise ValueError("runtime root must not be a symlink")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("runtime root must be inside project root") from exc
        return resolved

    def _load_runs(self, diagnostics: list[dict[str, str]]) -> list[dict[str, Any]]:
        if not self.runs_root.exists():
            return []
        if self.runs_root.is_symlink() or not self.runs_root.is_dir():
            diagnostics.append(self._diagnostic(self.runs_root, "runs root is not a regular directory"))
            return []

        runs = []
        for run_path in sorted(self.runs_root.iterdir(), key=lambda item: item.name):
            if run_path.is_symlink() or not run_path.is_dir():
                diagnostics.append(self._diagnostic(run_path, "run path is not a regular directory"))
                continue
            try:
                manifest = WorkflowManifest.from_dict(self._read_json(run_path / "manifest.json"))
                status = WorkflowStatus.from_dict(self._read_json(run_path / "status.json"))
                if manifest.run_id != status.run_id or manifest.run_id != run_path.name:
                    raise ValueError("runId does not match manifest, status, and directory")
                runs.append(self._compact_run(manifest, status))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                diagnostics.append(self._diagnostic(run_path, str(exc)))
        return runs

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{path.name} is not a regular file")
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(f"{path.name} must contain an object")
        return payload

    @staticmethod
    def _compact_run(manifest: WorkflowManifest, status: WorkflowStatus) -> dict[str, Any]:
        started = datetime.fromisoformat(status.started_at)
        ended = datetime.fromisoformat(status.completed_at or status.updated_at)
        return {
            "runId": manifest.run_id,
            "briefName": Path(manifest.brief_path).name,
            "gitBranch": manifest.git_branch,
            "gitHeadShort": manifest.git_head[:8],
            "createdAt": manifest.created_at,
            "updatedAt": status.updated_at,
            "completedAt": status.completed_at,
            "stage": status.stage,
            "status": status.status,
            "message": status.message,
            "errorCode": status.error_code,
            "artifactBytes": status.artifact_bytes,
            "durationMs": round((ended - started).total_seconds() * 1000),
        }

    @staticmethod
    def _summary(runs: list[dict[str, Any]]) -> dict[str, int]:
        statuses = [run["status"] for run in runs]
        return {
            "runCount": len(runs),
            "activeCount": sum(status not in {"completed", "changes_required", "blocked", "failed"} for status in statuses),
            "awaitingAuthorizationCount": statuses.count("awaiting_authorization"),
            "completedCount": statuses.count("completed"),
            "changesRequiredCount": statuses.count("changes_required"),
            "blockedCount": statuses.count("blocked"),
            "failedCount": statuses.count("failed"),
        }

    def _retention(self, diagnostics: list[dict[str, str]]) -> dict[str, int] | None:
        if not self.retention_path.is_file() or self.retention_path.is_symlink():
            return None
        try:
            policy = RetentionPolicy.from_path(self.retention_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            diagnostics.append(self._diagnostic(self.retention_path, str(exc)))
            return None
        return {
            "retainDays": policy.retain_days,
            "retainLatestTerminalRuns": policy.retain_latest_terminal_runs,
            "stageArtifactMaxBytes": policy.stage_artifact_max_bytes,
            "runArtifactSoftCapBytes": policy.run_artifact_soft_cap_bytes,
            "commandOutputTailCharacters": policy.command_output_tail_characters,
        }

    def _diagnostic(self, path: Path, reason: str) -> dict[str, str]:
        try:
            display_path = str(path.resolve(strict=False).relative_to(self.project_root))
        except ValueError:
            display_path = path.name
        return {"path": display_path, "reason": reason}
