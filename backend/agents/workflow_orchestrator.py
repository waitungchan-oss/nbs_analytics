from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from .evidence_collector import EvidencePolicy
from .workflow_models import (
    EVENT_SCHEMA,
    MANIFEST_SCHEMA,
    STATUS_SCHEMA,
    WorkflowEvent,
    WorkflowManifest,
    WorkflowStatus,
)
from .workflow_notifications import WorkflowNotifier, build_notifier
from .workflow_store import WorkflowStore


OUTPUT_TAIL = 12000
CONTEXT_TIMEOUT = 120


@dataclass(frozen=True)
class StageResult:
    exit_code: int
    payload: dict
    stdout_tail: str
    stderr_tail: str
    duration_ms: int


class StageExecutor(Protocol):
    def run_json(self, argv: tuple[str, ...], *, timeout: int) -> StageResult: ...


class SubprocessStageExecutor:
    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.python = self.project_root / ".venv" / "bin" / "python"
        if not self.python.is_file():
            raise FileNotFoundError(f"Repository Python was not found: {self.python}")

    def run_json(self, argv: tuple[str, ...], *, timeout: int) -> StageResult:
        started = time.monotonic()
        process = subprocess.Popen(
            list(argv),
            cwd=self.project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        stdout_tail = bytearray()
        stderr_tail = bytearray()
        readers = (
            threading.Thread(target=_drain_tail, args=(process.stdout, stdout_tail), daemon=True),
            threading.Thread(target=_drain_tail, args=(process.stderr, stderr_tail), daemon=True),
        )
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        finally:
            for reader in readers:
                reader.join()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        stdout_text = bytes(stdout_tail).decode("utf-8", errors="replace")
        stderr_text = bytes(stderr_tail).decode("utf-8", errors="replace")
        payload: dict = {}
        try:
            decoded = json.loads(stdout_text)
            if not isinstance(decoded, dict):
                raise ValueError("stage output must be a JSON object")
            payload = decoded
        except (json.JSONDecodeError, ValueError):
            if process.returncode == 0:
                raise ValueError("stage output is not a JSON object")
        return StageResult(
            exit_code=process.returncode,
            payload=payload,
            stdout_tail=stdout_text,
            stderr_tail=stderr_text,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


class WorkflowOrchestrator:
    def __init__(
        self,
        project_root: Path,
        *,
        store: WorkflowStore | None = None,
        stage_executor: StageExecutor | None = None,
        notifier: WorkflowNotifier | None = None,
        housekeeping: Callable[[], object] | None = None,
        warning_sink: Callable[[str], object] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.policy = EvidencePolicy.from_project(self.project_root)
        self.store = store or WorkflowStore(self.project_root)
        self.stage_executor = stage_executor or SubprocessStageExecutor(self.project_root)
        self.notifier = notifier or build_notifier()
        self.housekeeping = housekeeping
        self.warning_sink = warning_sink or (lambda warning: None)

    def start(
        self,
        brief_path: Path,
        *,
        context_agent_command: str | None = None,
        notify: bool = True,
    ) -> WorkflowStatus:
        brief = Path(brief_path)
        if not brief.is_absolute():
            brief = self.project_root / brief
        try:
            brief = self.policy.resolve_read_path(brief)
            brief_bytes = brief.read_bytes()
        except (FileNotFoundError, PermissionError, OSError) as exc:
            return self._blocked_without_run("blocked_missing_brief", str(exc))

        identity = self._git_identity()
        brief_relative = brief.relative_to(self.project_root).as_posix()
        argv = self._context_argv(brief_relative, context_agent_command)
        self._notify(notify, "Context started", f"Collecting context for {brief_relative}")
        try:
            result = self.stage_executor.run_json(argv, timeout=CONTEXT_TIMEOUT)
        except Exception as exc:
            return self._finish_failed(
                brief_relative, brief_bytes, identity, None, "failed_context_executor", str(exc), notify
            )

        payload = result.payload
        fingerprint = payload.get("contextFingerprint")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in fingerprint
        ):
            fingerprint = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
        run_id = f"run-{uuid4().hex}"
        started_at = _now()
        manifest = WorkflowManifest(
            schema_version=MANIFEST_SCHEMA,
            run_id=run_id,
            brief_path=brief_relative,
            brief_sha256=hashlib.sha256(brief_bytes).hexdigest(),
            git_branch=identity["branch"],
            git_head=identity["head"],
            dirty_files=tuple(identity["dirtyFiles"]),
            created_at=started_at,
            context_fingerprint=fingerprint,
        )
        status = WorkflowStatus(
            schema_version=STATUS_SCHEMA,
            run_id=run_id,
            stage="context",
            status="created",
            started_at=started_at,
            updated_at=started_at,
            completed_at=None,
            message="Workflow run created",
            error_code=None,
            artifact_bytes=0,
        )
        self.store.create_run(manifest, status)
        self._transition(run_id, status, "context_running", "Context collection started")
        self.store.write_artifact(run_id, "context.json", payload)

        if result.exit_code != 0 or payload.get("status") != "ready":
            target = "blocked" if result.exit_code != 0 else "failed"
            message = payload.get("status") or "Context stage failed"
            return self._transition(
                run_id, self.store.load_status(run_id), target, str(message),
                error_code=f"context_{target}", notify=notify,
            )

        status = self._transition(
            run_id, self.store.load_status(run_id), "awaiting_authorization",
            "Context ready; explicit authorization required", notify=notify,
        )
        self._run_housekeeping()
        return status

    def _context_argv(self, brief_relative: str, command: str | None) -> tuple[str, ...]:
        argv = (
            str(self.project_root / ".venv" / "bin" / "python"),
            str(self.project_root / "scripts" / "context_agent.py"),
            "--brief", brief_relative,
        )
        if command is None:
            return (*argv, "--collect-only")
        if not command.strip():
            raise ValueError("Context agent command cannot be empty")
        return (*argv, "--agent-command", command)

    def _git_identity(self) -> dict:
        def run(*args: str) -> str:
            completed = subprocess.run(
                ["git", *args], cwd=self.project_root, capture_output=True, text=True,
                encoding="utf-8", errors="replace", shell=False, check=True,
            )
            return completed.stdout.strip()

        dirty: list[dict[str, str]] = []
        for line in run("status", "--porcelain", "--untracked-files=all").splitlines():
            relative = line[3:]
            path = self.project_root / relative
            if path.is_file():
                dirty.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        return {"head": run("rev-parse", "HEAD"), "branch": run("branch", "--show-current") or "(detached)", "dirtyFiles": dirty}

    def _transition(self, run_id: str, current: WorkflowStatus, target: str, message: str, *, error_code: str | None = None, notify: bool = False) -> WorkflowStatus:
        now = _now()
        status = WorkflowStatus(STATUS_SCHEMA, run_id, "context", target, current.started_at, now, None, message, error_code, current.artifact_bytes)
        event = WorkflowEvent(EVENT_SCHEMA, run_id, f"event-{uuid4().hex}", "status_transition", current.status, target, now, message, {"stage": "context"})
        self.store.transition(run_id, status, event)
        if notify:
            self._notify(True, "Awaiting authorization" if target == "awaiting_authorization" else "Context failed", message)
        return status

    def _finish_failed(self, brief: str, content: bytes, identity: dict, fingerprint: str | None, code: str, message: str, notify: bool) -> WorkflowStatus:
        run_id = f"run-{uuid4().hex}"
        now = _now()
        manifest = WorkflowManifest(MANIFEST_SCHEMA, run_id, brief, hashlib.sha256(content).hexdigest(), identity["branch"], identity["head"], tuple(identity["dirtyFiles"]), now, fingerprint or hashlib.sha256(message.encode()).hexdigest())
        status = WorkflowStatus(STATUS_SCHEMA, run_id, "context", "created", now, now, None, "Workflow run created", None, 0)
        self.store.create_run(manifest, status)
        return self._transition(run_id, status, "failed", message, error_code=code, notify=notify)

    def _blocked_without_run(self, code: str, message: str) -> WorkflowStatus:
        now = _now()
        return WorkflowStatus(STATUS_SCHEMA, f"run-{uuid4().hex}", "context", "blocked", now, now, now, message, code, 0)

    def _notify(self, enabled: bool, title: str, message: str) -> None:
        if not enabled:
            return
        try:
            result = self.notifier.send(title, message)
            if result.warning:
                self.warning_sink(f"notification warning: {result.warning}")
        except Exception as exc:
            self.warning_sink(f"notification warning: {exc}")

    def _run_housekeeping(self) -> None:
        if self.housekeeping is None:
            return
        try:
            self.housekeeping()
        except Exception as exc:
            self.warning_sink(f"housekeeping warning: {exc}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _drain_tail(stream, tail: bytearray) -> None:
    while True:
        chunk = stream.read(8192)
        if not chunk:
            return
        tail.extend(chunk)
        if len(tail) > OUTPUT_TAIL:
            del tail[:-OUTPUT_TAIL]
