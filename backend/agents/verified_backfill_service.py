from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from .verified_backfill_models import VerifiedBackfillManifest
from .review_agent_service import _validate_report
from .workflow_models import (
    APPROVAL_SCHEMA,
    MANIFEST_SCHEMA,
    STATUS_SCHEMA,
    WorkflowApproval,
    WorkflowManifest,
    WorkflowStatus,
    canonical_sha256,
)
from .workflow_store import WorkflowStore


PROJECT_PYTHON = sys.executable
# Hermes runs its full read-only targeted pack and can exceed two minutes on the local DB.
GATE_TIMEOUT = 300
BRIEF_PATH = ".nbs_agent_runtime/reports/verified-backfill-task2-brief.md"
REVIEW_EVIDENCE_PATH = ".nbs_agent_runtime/reports/verified-backfill-task2-review-evidence.json"
REVIEW_REPORT_PATH = ".nbs_agent_runtime/reports/verified-backfill-task2-review-local.json"
_GATE_COMMANDS = (
    ("pytest", (PROJECT_PYTHON, "-m", "pytest", "tests/test_documentation_evidence.py", "-q")),
    ("systemAcceptance", (PROJECT_PYTHON, "scripts/system_manager.py", "acceptance")),
    ("hermes", (PROJECT_PYTHON, "scripts/hermes_post_change_check.py", "--skip-monitor", "--json")),
    ("baseline", (PROJECT_PYTHON, "-m", "pytest", "tests/test_phase2_precheck_acceptance.py", "-q")),
)


class _Blocked(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class SubprocessRunner:
    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()

    def run(self, argv: tuple[str, ...], *, timeout: int) -> dict[str, Any]:
        completed = subprocess.run(
            list(argv),
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }


class VerifiedBackfillService:
    def __init__(
        self,
        project_root: Path,
        *,
        store: WorkflowStore | None = None,
        runner: Any | None = None,
        review_provider: Callable[[dict[str, Any], dict[str, dict[str, Any]]], Mapping[str, Any]] | None = None,
        notify: bool = True,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.store = store or WorkflowStore(self.project_root)
        self.runner = runner or SubprocessRunner(self.project_root)
        self.review_provider = review_provider or self._read_review_report
        self.notify = notify

    def create(self, *, source_commit: str, reason: str) -> dict[str, object]:
        try:
            try:
                identity = self._clean_main_identity(source_commit)
            except (OSError, subprocess.TimeoutExpired):
                return self._blocked("identity_check_failed")
            gates = self._run_fixed_gates()
            try:
                self._expected_diff_fingerprint = self._collect_diff_fingerprint(source_commit)
            except (OSError, subprocess.TimeoutExpired):
                return self._blocked("diff_collection_failed")
            try:
                review = self._collect_review(identity, gates)
            except _Blocked as blocked:
                return self._blocked(blocked.reason)
            if not self._all_pass(gates, review):
                raise _Blocked(self._first_failure(gates, review))
            try:
                return self._create_completed_run(identity, gates, review, reason)
            except (OSError, subprocess.TimeoutExpired):
                return self._blocked("artifact_write_failed")
        except _Blocked as blocked:
            return self._blocked(blocked.reason)

    def _clean_main_identity(self, source_commit: str) -> dict[str, Any]:
        status = self._run(("git", "status", "--porcelain=v1"))
        if status["returncode"] != 0:
            raise _Blocked("git_status_failed")
        if status["stdout"].strip():
            raise _Blocked("dirty_worktree")
        branch = self._run(("git", "branch", "--show-current"))
        if branch["returncode"] != 0 or branch["stdout"].strip() != "main":
            raise _Blocked("non_main_branch")
        head = self._run(("git", "rev-parse", "HEAD"))
        actual_head = head["stdout"].strip()
        if head["returncode"] != 0 or actual_head != source_commit:
            raise _Blocked("stale_commit")
        if len(source_commit) != 40 or any(char not in "0123456789abcdef" for char in source_commit):
            raise _Blocked("invalid_source_commit")
        return {"sourceCommit": source_commit, "sourceBranch": "main", "dirtyFiles": []}

    def _collect_diff_fingerprint(self, source_commit: str) -> str:
        result = self._run(("git", "diff", "--binary", "--no-ext-diff", f"{source_commit}^", source_commit))
        if result.get("returncode") != 0:
            raise _Blocked("diff_collection_failed")
        return hashlib.sha256(str(result.get("stdout", "")).encode("utf-8")).hexdigest()

    def _run_fixed_gates(self) -> dict[str, dict[str, Any]]:
        gates = {}
        for name, argv in _GATE_COMMANDS:
            try:
                result = self._run(argv)
            except (OSError, subprocess.TimeoutExpired):
                result = {"returncode": 1, "stdout": "", "stderr": "gate execution failed"}
            gates[name] = self._bounded_result(result)
        return gates

    def _collect_review(self, identity: dict[str, Any], gates: dict[str, dict[str, Any]]) -> dict[str, Any]:
        try:
            raw = self.review_provider(identity, gates)
            if not isinstance(raw, Mapping):
                raise ValueError("review evidence must be an object")
            binding_keys = {"sourceCommit", "sourceBranch", "diffFingerprint"}
            report = {key: value for key, value in raw.items() if key not in binding_keys}
            _validate_report(report, str(raw.get("reviewFingerprint")), strict=True)
            unsigned = {key: value for key, value in raw.items() if key != "reviewFingerprint"}
            if raw.get("sourceCommit") != identity["sourceCommit"] or raw.get("sourceBranch") != "main":
                raise ValueError("review source binding mismatch")
            if raw.get("diffFingerprint") != self._expected_diff_fingerprint:
                raise ValueError("review diff binding mismatch")
            if raw.get("reviewFingerprint") != canonical_sha256(unsigned):
                raise ValueError("review fingerprint mismatch")
        except (OSError, subprocess.TimeoutExpired):
            raise _Blocked("review_file_read_failed")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return {"verdict": "BLOCKED", "findingCount": 0, "evidenceValid": False}
        verdict = raw.get("verdict") if isinstance(raw, Mapping) else None
        findings = raw.get("findings")
        if not isinstance(findings, list):
            return {"verdict": "BLOCKED", "findingCount": 0, "evidenceValid": False}
        return {
            "verdict": verdict if isinstance(verdict, str) else "BLOCKED",
            "findingCount": min(len(findings), 20),
            "sourceCommit": raw["sourceCommit"],
            "sourceBranch": raw["sourceBranch"],
            "diffFingerprint": raw["diffFingerprint"],
            "reviewFingerprint": raw["reviewFingerprint"],
            "evidenceValid": True,
        }

    def _all_pass(self, gates: dict[str, dict[str, Any]], review: dict[str, Any]) -> bool:
        return (
            all(gate["status"] == "pass" for gate in gates.values())
            and review["verdict"].upper() == "PASS"
            and review["findingCount"] == 0
            and review.get("evidenceValid") is True
        )

    def _first_failure(self, gates: dict[str, dict[str, Any]], review: dict[str, Any]) -> str:
        reasons = (
            ("pytest", "pytest_failed"),
            ("systemAcceptance", "system_acceptance_failed"),
            ("hermes", "hermes_failed"),
            ("baseline", "baseline_failed"),
        )
        for name, reason in reasons:
            if gates[name]["status"] != "pass":
                return reason
        if review.get("evidenceValid") is not True:
            return "review_evidence_mismatch"
        if review["verdict"].upper() != "PASS":
            return "review_not_pass"
        if review["findingCount"]:
            return "review_findings_present"
        return "verification_failed"

    def _create_completed_run(
        self, identity: dict[str, Any], gates: dict[str, dict[str, Any]], review: dict[str, Any], reason: str
    ) -> dict[str, object]:
        run_id = f"run-{uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        brief = self.project_root / BRIEF_PATH
        try:
            brief_bytes = brief.read_bytes()
        except (OSError, subprocess.TimeoutExpired):
            raise _Blocked("brief_read_failed")
        context = self.project_root / ".nbs_agent_runtime" / "reports" / "verified-backfill-task2-context.json"
        try:
            context_fingerprint = hashlib.sha256(context.read_bytes()).hexdigest() if context.is_file() else hashlib.sha256(b"missing-context").hexdigest()
        except (OSError, subprocess.TimeoutExpired):
            raise _Blocked("brief_read_failed")
        manifest = WorkflowManifest(
            MANIFEST_SCHEMA, run_id, BRIEF_PATH, hashlib.sha256(brief_bytes).hexdigest(),
            "main", identity["sourceCommit"], (), now, context_fingerprint,
        )
        status = WorkflowStatus(STATUS_SCHEMA, run_id, "hermes", "completed", now, now, now, "Verified backfill completed", None, 0)
        approval = WorkflowApproval(
            APPROVAL_SCHEMA, run_id, ".nbs_agent_runtime/contracts/verified-backfill-task2.json",
            hashlib.sha256(b"verified-backfill-task2-contract-v1").hexdigest(), identity["sourceCommit"], now, "approved",
        )
        implementation = {"schemaVersion": "implementation-report-v1", "status": "completed", "changedPaths": []}
        targeted = {"schemaVersion": "targeted-verification-v1", "status": "passed", "gateCount": len(gates)}
        review_payload = {
            "schemaVersion": "review-evidence-v1", "verdict": review["verdict"],
            "findingCount": review["findingCount"], "sourceCommit": review["sourceCommit"],
            "sourceBranch": review["sourceBranch"], "diffFingerprint": review["diffFingerprint"],
            "reviewFingerprint": review["reviewFingerprint"],
        }
        full = {"schemaVersion": "full-verification-v1", "status": "passed", "gateStatuses": {key: value["status"] for key, value in gates.items()}}
        hermes = {"schemaVersion": "hermes-v1", "overallStatus": "pass"}
        manifest_payload = VerifiedBackfillManifest.from_dict({
            "sourceCommit": identity["sourceCommit"], "sourceBranch": "main", "dirtyFiles": [],
            "gateHashes": {key: canonical_sha256(value) for key, value in gates.items() if key != "baseline"},
            "reviewHash": canonical_sha256(review_payload),
        })
        artifacts = {
            "implementation.json": implementation, "targeted-verification.json": targeted,
            "review.json": review_payload, "full-verification.json": full,
            "hermes.json": hermes, "verified-backfill.json": manifest_payload.to_dict(),
        }
        self._atomic_create_run(run_id, manifest, status, approval, artifacts)
        return {"status": "completed", "runId": run_id, "reason": self._bounded_reason(reason)}

    def _atomic_create_run(
        self, run_id: str, manifest: WorkflowManifest, status: WorkflowStatus,
        approval: WorkflowApproval, artifacts: Mapping[str, dict[str, Any]],
    ) -> None:
        run_dir = self.store.runs_root / run_id
        if run_dir.exists() or run_dir.is_symlink():
            raise FileExistsError(run_dir)
        staging = Path(tempfile.mkdtemp(prefix=f".{run_id}.", dir=self.store.runs_root))
        try:
            payloads = {
                "manifest.json": manifest.to_dict(), "status.json": status.to_dict(),
                "approval.json": approval.to_dict(), **artifacts,
            }
            artifact_bytes = sum(
                len(self.store._json_bytes(payload)) for name, payload in artifacts.items()
                if name != "approval.json"
            )
            payloads["status.json"] = {**status.to_dict(), "artifactBytes": artifact_bytes}
            for name, payload in payloads.items():
                self.store._atomic_json(staging / name, payload)
            directory_fd = os.open(self.store.runs_root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            os.rename(staging, run_dir)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _read_review_report(self, identity: dict[str, Any], gates: dict[str, dict[str, Any]]) -> Mapping[str, Any]:
        path = self.project_root / REVIEW_REPORT_PATH
        if not path.is_file():
            return {"verdict": "BLOCKED", "findings": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def _run(self, argv: tuple[str, ...]) -> dict[str, Any]:
        return self.runner.run(argv, timeout=GATE_TIMEOUT)

    @staticmethod
    def _bounded_result(result: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "status": "pass" if result.get("returncode") == 0 else "fail",
            "exitCode": int(result.get("returncode", 1)),
            "stdoutSha256": hashlib.sha256(str(result.get("stdout", "")).encode()).hexdigest(),
            "stderrSha256": hashlib.sha256(str(result.get("stderr", "")).encode()).hexdigest(),
        }

    @staticmethod
    def _bounded_reason(reason: str) -> str:
        return " ".join(str(reason).split())[:240]

    @classmethod
    def _blocked(cls, reason: str) -> dict[str, object]:
        return {"status": "blocked", "reason": cls._bounded_reason(reason)}


__all__ = ["VerifiedBackfillService"]
