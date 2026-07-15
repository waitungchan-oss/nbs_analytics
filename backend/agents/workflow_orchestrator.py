from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from .evidence_collector import EvidencePolicy
from .context_agent_service import context_bundle_from_payload
from .implementation_models import ImplementationTaskContract
from .workflow_models import (
    APPROVAL_SCHEMA,
    EVENT_SCHEMA,
    MANIFEST_SCHEMA,
    STATUS_SCHEMA,
    TERMINAL_STATUSES,
    WorkflowEvent,
    WorkflowApproval,
    WorkflowManifest,
    WorkflowStatus,
)
from .workflow_notifications import WorkflowNotifier, build_notifier
from .workflow_store import WorkflowLockedError, WorkflowStore


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
    def run_json(self, argv: tuple[str, ...], *, timeout: int, require_json: bool = True) -> StageResult: ...


class SubprocessStageExecutor:
    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.python = self.project_root / ".venv" / "bin" / "python"
        if not self.python.is_file():
            raise FileNotFoundError(f"Repository Python was not found: {self.python}")

    def run_json(self, argv: tuple[str, ...], *, timeout: int, require_json: bool = True) -> StageResult:
        started = time.monotonic()
        process = subprocess.Popen(
            list(argv),
            cwd=self.project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=os.name == "posix",
        )
        stdout_tail = bytearray()
        stderr_tail = bytearray()
        stdout_spool = tempfile.TemporaryFile(mode="w+b")
        readers = (
            threading.Thread(target=_drain_stdout, args=(process.stdout, stdout_spool, stdout_tail), daemon=True),
            threading.Thread(target=_drain_tail, args=(process.stderr, stderr_tail), daemon=True),
        )
        for reader in readers:
            reader.start()
        timeout_error: subprocess.TimeoutExpired | None = None
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            timeout_error = exc
            if os.name == "posix":
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        finally:
            for reader in readers:
                reader.join(timeout=1)
            if any(reader.is_alive() for reader in readers):
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
                for reader in readers:
                    reader.join(timeout=0.5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        if timeout_error is not None:
            stdout_spool.close()
            raise timeout_error
        stdout_text = bytes(stdout_tail).decode("utf-8", errors="replace")
        stderr_text = bytes(stderr_tail).decode("utf-8", errors="replace")
        payload: dict = {}
        try:
            stdout_spool.seek(0)
            decoded = json.load(stdout_spool)
            if not isinstance(decoded, dict):
                raise ValueError("stage output must be a JSON object")
            payload = decoded
        except (json.JSONDecodeError, ValueError):
            if process.returncode == 0 and require_json:
                raise ValueError("stage output is not a JSON object")
        finally:
            stdout_spool.close()
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
        fingerprint = payload.get("contextFingerprint") or payload.get("bundleFingerprint")
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

        collect_only_valid = False
        if payload.get("schemaVersion") == "context-evidence-v1":
            try:
                context_bundle_from_payload(payload)
                collect_only_valid = True
            except (TypeError, ValueError):
                collect_only_valid = False
        context_succeeded = result.exit_code == 0 and (
            payload.get("status") == "ready" or collect_only_valid
        )
        if not context_succeeded:
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

    def approve(
        self,
        run_id: str,
        contract_path: Path,
        *,
        implementation_agent_command: str,
        review_agent_command: str,
        notify: bool = True,
    ) -> WorkflowStatus:
        authorization = self._authorize_approval(
            run_id,
            contract_path,
            implementation_agent_command=implementation_agent_command,
            review_agent_command=review_agent_command,
            notify=notify,
        )
        if authorization is None:
            return self.store.load_status(run_id)
        if isinstance(authorization, WorkflowStatus):
            return authorization
        status, manifest, contract_file, contract, implementation_runner = authorization

        try:
            implementation = self.stage_executor.run_json(
                self._implementation_argv(contract_file, implementation_runner), timeout=CONTEXT_TIMEOUT,
            )
        except Exception as exc:
            return self._transition(
                run_id, status, "failed", str(exc), error_code="failed_implementation_executor",
                notify=notify, stage="implementation",
            )
        self.store.write_artifact(run_id, "implementation.json", implementation.payload)
        try:
            self._validate_implementation_report(implementation.payload, contract, manifest)
        except ValueError as exc:
            return self._transition(
                run_id, self.store.load_status(run_id), "failed", str(exc),
                error_code="failed_implementation_report", notify=notify, stage="implementation",
            )
        if implementation.exit_code != 0 or implementation.payload.get("status") != "completed":
            return self._transition(
                run_id, self.store.load_status(run_id), "failed", "Implementation agent failed",
                error_code="failed_implementation", notify=notify, stage="implementation",
            )
        self._notify(notify, "Implementation completed", f"Implementation completed for {run_id}")
        try:
            verification = {"commands": self._normalize_targeted_evidence(implementation.payload)}
        except (TypeError, ValueError) as exc:
            return self._transition(
                run_id, self.store.load_status(run_id), "failed", str(exc),
                error_code="failed_targeted_evidence", notify=notify, stage="targeted_verification",
            )
        status = self._transition(
            run_id, self.store.load_status(run_id), "targeted_verification_running",
            "Targeted verification evidence recorded", notify=False, stage="targeted_verification",
        )
        self.store.write_artifact(run_id, "targeted-verification.json", verification)
        status = self._transition(
            run_id, self.store.load_status(run_id), "review_running", "Review agent started",
            notify=False, stage="review",
        )
        try:
            review = self.stage_executor.run_json(
                self._review_argv(manifest, run_id, review_agent_command), timeout=CONTEXT_TIMEOUT,
            )
        except Exception as exc:
            return self._transition(
                run_id, status, "failed", str(exc), error_code="failed_review_executor",
                notify=notify, stage="review",
            )
        self.store.write_artifact(run_id, "review.json", review.payload)
        verdict = review.payload.get("verdict")
        if verdict == "changes_required":
            return self._transition(
                run_id, self.store.load_status(run_id), "changes_required", "Review changes required",
                error_code="review_changes_required", notify=notify, stage="review",
            )
        if review.exit_code != 0 or verdict != "pass":
            target = "blocked" if verdict in {"blocked", "invalid_bundle", "context_overflow"} else "failed"
            return self._transition(
                run_id, self.store.load_status(run_id), target, "Review agent did not pass",
                error_code=f"review_{target}", notify=notify, stage="review",
            )
        self._notify(notify, "Review passed", f"Review passed for {run_id}; full verification is pending")
        return self._run_final_gates(run_id, notify=notify)

    def _run_final_gates(self, run_id: str, *, notify: bool) -> WorkflowStatus:
        status = self._transition(
            run_id, self.store.load_status(run_id), "full_verification_running",
            "Full verification started", notify=False, stage="full_verification",
        )
        verification: dict[str, dict] = {}
        try:
            full_pytest = self.stage_executor.run_json(
                self._full_pytest_argv(), timeout=CONTEXT_TIMEOUT, require_json=False,
            )
        except Exception as exc:
            return self._transition(
                run_id, status, "blocked", str(exc), error_code="full_verification_blocked",
                notify=notify, stage="full_verification",
            )
        verification["fullPytest"] = {
            "exitCode": full_pytest.exit_code,
            "stdoutTail": full_pytest.stdout_tail,
            "stderrTail": full_pytest.stderr_tail,
            "payload": full_pytest.payload,
        }
        self.store.write_artifact(run_id, "full-verification.json", verification)
        if full_pytest.exit_code != 0:
            return self._transition(
                run_id, self.store.load_status(run_id), "blocked", "Full pytest did not pass",
                error_code="full_verification_blocked", notify=notify, stage="full_verification",
            )
        try:
            acceptance = self.stage_executor.run_json(self._acceptance_argv(), timeout=CONTEXT_TIMEOUT)
        except Exception as exc:
            return self._transition(
                run_id, self.store.load_status(run_id), "blocked", str(exc), error_code="full_verification_blocked",
                notify=notify, stage="full_verification",
            )
        verification["acceptance"] = acceptance.payload
        self.store.write_artifact(run_id, "full-verification.json", verification)
        if acceptance.exit_code != 0 or acceptance.payload.get("status") != "passed":
            return self._transition(
                run_id, self.store.load_status(run_id), "blocked", "System acceptance did not pass",
                error_code="full_verification_blocked", notify=notify, stage="full_verification",
            )

        status = self._transition(
            run_id, self.store.load_status(run_id), "hermes_running", "Hermes started",
            notify=False, stage="hermes",
        )
        try:
            hermes = self.stage_executor.run_json(self._hermes_argv(), timeout=CONTEXT_TIMEOUT)
        except Exception as exc:
            return self._transition(
                run_id, status, "blocked", str(exc), error_code="hermes_blocked", notify=notify, stage="hermes",
            )
        self.store.write_artifact(run_id, "hermes.json", hermes.payload)
        if hermes.exit_code != 0 or hermes.payload.get("overallStatus") != "pass":
            self._notify(notify, "Hermes failed", f"Hermes did not pass for {run_id}")
            return self._transition(
                run_id, self.store.load_status(run_id), "blocked", "Hermes did not pass",
                error_code="hermes_blocked", notify=notify, stage="hermes",
            )
        self._notify(notify, "Hermes passed", f"Hermes passed for {run_id}")
        return self._transition(
            run_id, self.store.load_status(run_id), "completed", "Workflow completed",
            notify=notify, stage="hermes",
        )

    def _authorize_approval(
        self,
        run_id: str,
        contract_path: Path,
        *,
        implementation_agent_command: str,
        review_agent_command: str,
        notify: bool,
    ) -> tuple[WorkflowStatus, WorkflowManifest, Path, ImplementationTaskContract, tuple[str, ...]] | WorkflowStatus | None:
        try:
            with self.store.run_lock(run_id):
                status = self.store.load_status(run_id)
                if status.status != "awaiting_authorization":
                    return status
                try:
                    implementation_runner = self._approved_runner(implementation_agent_command)
                    self._approved_runner(review_agent_command)
                    manifest = self.store.load_manifest(run_id)
                    contract_file = Path(contract_path).resolve()
                    contract = ImplementationTaskContract.from_dict(
                        json.loads(contract_file.read_text(encoding="utf-8"))
                    )
                    self._validate_approval_identity(manifest, contract, contract_file)
                    approval = WorkflowApproval(
                        schema_version=APPROVAL_SCHEMA,
                        run_id=run_id,
                        contract_path=self._relative_or_absolute(contract_file),
                        contract_fingerprint=contract.fingerprint,
                        approved_base_sha=contract.approved_base_sha,
                        approved_at=_now(),
                        authorization_status="approved",
                    )
                    if not self._write_approval_locked(run_id, approval):
                        return status
                except (OSError, ValueError, json.JSONDecodeError, PermissionError) as exc:
                    blocked = self._transition_locked(
                        run_id, status, "blocked", str(exc), error_code="blocked_authorization", stage="authorization",
                    )
                    self._notify(notify, "Workflow blocked", str(exc))
                    return blocked
                running = self._transition_locked(
                    run_id, status, "implementation_running", "Implementation agent started",
                    error_code=None, stage="implementation",
                )
                return running, manifest, contract_file, contract, implementation_runner
        except WorkflowLockedError:
            return None

    def _write_approval_locked(self, run_id: str, approval: WorkflowApproval) -> bool:
        approval_path = self.store._run_file(run_id, "approval.json")
        if approval_path.is_symlink():
            raise PermissionError("approval target must not be a symlink")
        if approval_path.exists():
            return False
        self.store._atomic_json(approval_path, approval.to_dict())
        return True

    def _transition_locked(
        self, run_id: str, current: WorkflowStatus, target: str, message: str, *, error_code: str | None, stage: str,
    ) -> WorkflowStatus:
        now = _now()
        completed_at = now if target in TERMINAL_STATUSES else None
        status = WorkflowStatus(
            STATUS_SCHEMA, run_id, stage, target, current.started_at, now, completed_at,
            message, error_code, current.artifact_bytes,
        )
        event = WorkflowEvent(
            EVENT_SCHEMA, run_id, f"event-{uuid4().hex}", "status_transition", current.status,
            target, now, message, {"stage": stage},
        )
        self.store._append_event(run_id, event)
        self.store._atomic_json(self.store._run_file(run_id, "status.json"), status.to_dict())
        return status

    def _validate_implementation_report(
        self, payload: dict, contract: ImplementationTaskContract, manifest: WorkflowManifest,
    ) -> None:
        expected = {
            "schemaVersion": "implementation-run-report-v1",
            "taskId": contract.task_id,
            "contractFingerprint": contract.fingerprint,
            "startHead": manifest.git_head,
        }
        for key, value in expected.items():
            if payload.get(key) != value:
                raise ValueError(f"implementation report {key} does not match approved identity")

    def _validate_approval_identity(
        self, manifest: WorkflowManifest, contract: ImplementationTaskContract, contract_file: Path,
    ) -> None:
        brief = self.policy.resolve_read_path(self.project_root / manifest.brief_path)
        if hashlib.sha256(brief.read_bytes()).hexdigest() != manifest.brief_sha256:
            raise ValueError("brief identity changed since context collection")
        current = self._git_identity()
        if (
            current["branch"] != manifest.git_branch
            or current["head"] != manifest.git_head
            or current["dirtyFiles"] != list(manifest.dirty_files)
        ):
            raise ValueError("Git identity changed since context collection")
        if Path(contract.approved_worktree).resolve() != self.project_root:
            raise ValueError("contract approvedWorktree does not match this worktree")
        if contract.approved_base_sha != manifest.git_head:
            raise ValueError("contract approvedBaseSha does not match run base")
        plan = Path(contract.plan_path)
        if not plan.is_absolute():
            plan = self.project_root / plan
        plan = self.policy.resolve_read_path(plan)
        if hashlib.sha256(plan.read_bytes()).hexdigest() != contract.plan_fingerprint:
            raise ValueError("contract planFingerprint does not match plan content")
        if not contract_file.is_file():
            raise ValueError("implementation contract is not a regular file")

    def _approved_runner(self, command: str) -> tuple[str, ...]:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("agent command is required")
        argv = tuple(shlex.split(command))
        if not argv:
            raise ValueError("agent command is required")
        executable = Path(argv[0]).name
        if executable not in self.policy.agent_executables:
            raise PermissionError("agent command is not allowlisted")
        return argv

    def _implementation_argv(self, contract_file: Path, runner: tuple[str, ...]) -> tuple[str, ...]:
        return (
            str(self.project_root / ".venv" / "bin" / "python"),
            "scripts/implementation_agent.py", "--contract", str(contract_file),
            "--agent-command", *runner,
        )

    def _review_argv(self, manifest: WorkflowManifest, run_id: str, command: str) -> tuple[str, ...]:
        run_dir = self.store.runs_root / run_id
        return (
            str(self.project_root / ".venv" / "bin" / "python"),
            "scripts/review_agent.py", "--brief", manifest.brief_path,
            "--base", manifest.git_head, "--head", "WORKTREE",
            "--context", str(run_dir / "context.json"),
            "--verification", str(run_dir / "targeted-verification.json"),
            "--agent-command", command, "--strict",
        )

    def _full_pytest_argv(self) -> tuple[str, ...]:
        return (str(self.project_root / ".venv" / "bin" / "python"), "-m", "pytest", "-q")

    def _acceptance_argv(self) -> tuple[str, ...]:
        return (str(self.project_root / ".venv" / "bin" / "python"), "scripts/system_manager.py", "acceptance")

    def _hermes_argv(self) -> tuple[str, ...]:
        return (
            str(self.project_root / ".venv" / "bin" / "python"),
            "scripts/hermes_post_change_check.py", "--skip-monitor", "--json",
        )

    def _normalize_targeted_evidence(self, payload: dict) -> list[dict]:
        commands: list[dict] = []
        for field in ("redEvidence", "greenEvidence"):
            evidence = payload.get(field)
            if not isinstance(evidence, list):
                raise ValueError(f"{field} must be a list")
            for item in evidence:
                if not isinstance(item, dict):
                    raise ValueError("targeted evidence item must be an object")
                command_id, argv, exit_code = item.get("commandId"), item.get("argv"), item.get("exitCode")
                if not isinstance(command_id, str) or not command_id:
                    raise ValueError("targeted evidence commandId is invalid")
                if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
                    raise ValueError("targeted evidence argv is invalid")
                if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                    raise ValueError("targeted evidence exitCode is invalid")
                stdout, stderr = item.get("stdout", ""), item.get("stderr", "")
                if not isinstance(stdout, str) or not isinstance(stderr, str):
                    raise ValueError("targeted evidence output is invalid")
                commands.append({
                    "label": command_id, "argv": argv, "exitCode": exit_code,
                    "stdoutTail": stdout[-OUTPUT_TAIL:], "stderrTail": stderr[-OUTPUT_TAIL:],
                })
        return commands

    def _relative_or_absolute(self, path: Path) -> str:
        try:
            return path.relative_to(self.project_root).as_posix()
        except ValueError:
            return str(path)

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

    def _transition(self, run_id: str, current: WorkflowStatus, target: str, message: str, *, error_code: str | None = None, notify: bool = False, stage: str = "context") -> WorkflowStatus:
        now = _now()
        completed_at = now if target in TERMINAL_STATUSES else None
        status = WorkflowStatus(STATUS_SCHEMA, run_id, stage, target, current.started_at, now, completed_at, message, error_code, current.artifact_bytes)
        event = WorkflowEvent(EVENT_SCHEMA, run_id, f"event-{uuid4().hex}", "status_transition", current.status, target, now, message, {"stage": stage})
        self.store.transition(run_id, status, event)
        if notify:
            titles = {
                "awaiting_authorization": "Awaiting authorization",
                "changes_required": "Review changes required",
                "blocked": "Workflow blocked",
                "failed": "Workflow failed",
                "completed": "Workflow completed",
            }
            self._notify(True, titles.get(target, "Workflow update"), message)
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
        try:
            chunk = stream.read(8192)
        except (OSError, ValueError):
            return
        if not chunk:
            return
        tail.extend(chunk)
        if len(tail) > OUTPUT_TAIL:
            del tail[:-OUTPUT_TAIL]


def _drain_stdout(stream, spool, tail: bytearray) -> None:
    while True:
        try:
            chunk = stream.read(8192)
        except (OSError, ValueError):
            return
        if not chunk:
            return
        try:
            spool.write(chunk)
        except (OSError, ValueError):
            return
        tail.extend(chunk)
        if len(tail) > OUTPUT_TAIL:
            del tail[:-OUTPUT_TAIL]
