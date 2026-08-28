"""Deterministic Verification Chain controller (`verification-gate-result-v1`).

Task 5 of the strict review verification chain. ``VerificationChain``
orchestrates the exact, monotonic gate state machine on top of an immutable
:class:`~backend.agents.verification_session.VerificationSession`:

    sealed -> pre_review -> strict_review -> full_pytest -> hermes -> complete

Design rules enforced here:

- Fail closed: runner capability/transport failures, review changes, context
  overflow, full verification failures, Hermes failures and source drift each
  map to the exact terminal session status from the spec error matrix.
- Monotonic: every gate has exact preconditions (``run_full_verification``
  requires ``review_passed``, ``run_hermes`` requires
  ``full_verification_passed``); a failed later gate never rewrites an earlier
  PASS into ``complete``.
- Freshness: a source probe (injectable, deterministic) is re-checked at every
  gate boundary; any drift writes a ``stale_source`` terminal and blocks the
  next gate.
- Atomic artifacts: ``session.json``, per-gate ``gate.json``/``verification.json``
  (via the existing evidence writer), batch ``review-report.json``,
  ``hermes-result.json``, ``terminal.json`` and ``completion.json`` all stay
  below ``.nbs_agent_runtime/verification_sessions/<sessionId>/``.
- Completion attestation is deterministic and never calls an LLM; only
  ``completion-attestation-v1`` with status ``complete`` may be displayed as
  round completion.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

from backend.agents.review_runner_profile import (
    ALLOWED_RECEIPT_STATUSES,
    RunnerCapabilityReceipt,
)
from backend.agents.verification_evidence_writer import (
    _resolve_session_output_dir,
    write_gate_evidence,
)
from backend.agents.verification_session import (
    StaleVerificationSession,
    VerificationSession,
    read_session,
    write_session,
)

GATE_RESULT_SCHEMA = "verification-gate-result-v1"
TERMINAL_SCHEMA = "verification-terminal-v1"
COMPLETION_SCHEMA = "completion-attestation-v1"

_GATE_NAMES = {
    "pre_review": "preReview",
    "strict_review": "strictReview",
    "full_pytest": "fullPytest",
    "hermes": "hermes",
    "completion": "completion",
}

_GATE_STATUS_BY_SESSION_STATUS = {
    "review_passed": "pass",
    "full_verification_passed": "pass",
    "hermes_passed": "pass",
    "stale_source": "stale",
    "blocked_runner_capability": "blocked",
    "blocked_runner_transport": "blocked",
    "review_changes_required": "failed",
    "context_overflow": "blocked",
    "verification_failed": "failed",
    "hermes_failed": "failed",
    "invalid_evidence": "blocked",
}

_REVIEW_REPORT_SCHEMA = "review-report-v1"
_REVIEW_REPORT_KEYS = {
    "schemaVersion", "verdict", "findings", "requirementCoverage", "testCoverage",
    "baselineRisk", "residualRisk", "hermesRequiredChecks", "reviewFingerprint",
}
_REVIEW_VERDICTS = {"pass", "changes_required", "blocked", "context_overflow", "invalid_bundle"}

_VERDICT_TO_SESSION_STATUS = {
    "pass": "review_passed",
    "changes_required": "review_changes_required",
    "context_overflow": "context_overflow",
    "blocked": "blocked_runner_transport",
    "invalid_bundle": "invalid_evidence",
}

HERMES_PROFILES = {"primary-runtime", "isolated-profile"}

SourceProbe = Callable[[], dict]


class InvalidGateTransition(ValueError):
    """Raised when a gate runs before its required predecessor state."""


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class GateResult:
    """Bounded outcome of one chain gate.

    ``status`` is the session-level outcome (for example ``review_passed`` or
    ``blocked_runner_transport``); ``gate_status`` is the gate-level verdict
    from ``pass|blocked|failed|stale``.
    """

    session_id: str
    gate: str
    status: str
    gate_status: str
    source_fingerprint: str
    evidence_fingerprint: str
    started_at: str
    finished_at: str
    diagnostics: tuple[str, ...] = ()
    recovery: tuple[str, ...] = ()
    schema_version: str = GATE_RESULT_SCHEMA

    def to_dict(self) -> dict:
        return {
            "schemaVersion": self.schema_version,
            "sessionId": self.session_id,
            "gate": self.gate,
            "status": self.status,
            "gateStatus": self.gate_status,
            "sourceFingerprint": self.source_fingerprint,
            "evidenceFingerprint": self.evidence_fingerprint,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "diagnostics": list(self.diagnostics),
            "recovery": list(self.recovery),
        }


@dataclass(frozen=True)
class CompletionAttestation:
    """Deterministic completion attestation (`completion-attestation-v1`)."""

    session_id: str
    status: str
    required_gates: dict
    source_fingerprint: str
    artifact_fingerprints: dict
    diagnostics: tuple[str, ...]
    generated_at: str
    schema_version: str = COMPLETION_SCHEMA

    def to_dict(self) -> dict:
        return {
            "schemaVersion": self.schema_version,
            "sessionId": self.session_id,
            "status": self.status,
            "requiredGates": dict(self.required_gates),
            "sourceFingerprint": self.source_fingerprint,
            "artifactFingerprints": dict(self.artifact_fingerprints),
            "diagnostics": list(self.diagnostics),
            "generatedAt": self.generated_at,
        }


def git_source_probe(
    project_root: Path | str,
    *,
    brief_path: str,
    base_sha: str,
    head_ref: str = "WORKTREE",
) -> dict:
    """Compute the four source-seal fingerprints from the live repository.

    Uses the same approved command shapes as the strict review provenance
    checks: ``git rev-parse HEAD``, the brief bytes SHA-256, the filtered
    porcelain worktree fingerprint and the base/head diff fingerprint.
    """
    project_root = Path(project_root)

    def _run(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, cwd=project_root, capture_output=True, text=True, check=True
        )

    head_sha = _run(["git", "rev-parse", "HEAD"]).stdout.strip()
    brief = (project_root / brief_path).resolve()
    brief_fingerprint = sha256(brief.read_bytes()).hexdigest()
    worktree = _run([
        "sh", "-c",
        "git status --porcelain --untracked-files=all -- . "
        "':(exclude)docs/superpowers' ':(exclude).superpowers' | shasum -a 256",
    ]).stdout.strip().split(maxsplit=1)[0].lower()
    if head_ref == "WORKTREE":
        diff_stdout = _run(["git", "diff", "--no-ext-diff", base_sha]).stdout
    else:
        diff_stdout = _run(["git", "diff", "--no-ext-diff", f"{base_sha}...{head_ref}"]).stdout
    diff_fingerprint = sha256(diff_stdout.encode("utf-8")).hexdigest()
    return {
        "head_sha": head_sha,
        "brief_fingerprint": brief_fingerprint,
        "worktree_fingerprint": worktree,
        "diff_fingerprint": diff_fingerprint,
    }


class VerificationChain:
    """Deterministic gate controller bound to one immutable session."""

    def __init__(
        self,
        session: VerificationSession,
        *,
        runtime_root: Path | str,
        source_probe: SourceProbe | None = None,
    ) -> None:
        if not isinstance(session, VerificationSession):
            raise ValueError("VerificationChain requires a VerificationSession")
        self._session = session
        self._source_probe = source_probe
        self._session_dir = _resolve_session_output_dir(runtime_root, session)
        self._last_result: GateResult | None = None

    # ------------------------------------------------------------------ seal

    @classmethod
    def seal(
        cls,
        session: VerificationSession,
        *,
        runtime_root: Path | str,
        source_probe: SourceProbe | None = None,
    ) -> "VerificationChain":
        """Persist a sealed session and bind the chain to its session directory.

        The source probe (when provided) must match the session at seal time;
        sealing a drifted source fails closed with ``StaleVerificationSession``.
        """
        if not isinstance(session, VerificationSession):
            raise ValueError("seal requires a VerificationSession")
        if session.status != "sealed":
            raise ValueError("seal requires a session with status 'sealed'")
        chain = cls(session, runtime_root=runtime_root, source_probe=source_probe)
        if source_probe is not None:
            try:
                session.assert_fresh(**source_probe())
            except StaleVerificationSession as exc:
                raise StaleVerificationSession(f"cannot seal stale source: {exc}") from exc
        chain._persist()
        return chain

    @classmethod
    def load(
        cls,
        session_id: str,
        *,
        runtime_root: Path | str,
        source_probe: SourceProbe | None = None,
    ) -> "VerificationChain":
        """Reconstruct a chain from a persisted session manifest."""
        sessions_root = Path(runtime_root)
        session_path = sessions_root / session_id / "session.json"
        session = read_session(session_path)
        if session.session_id != session_id:
            raise ValueError(
                "session manifest sessionId does not match the requested session"
            )
        return cls(session, runtime_root=session_path.parent, source_probe=source_probe)

    # ------------------------------------------------------------ properties

    @property
    def session(self) -> VerificationSession:
        return self._session

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    def current_result_is_previous_session(self) -> bool:
        """True when the latest terminal artifact belongs to a different session."""
        terminal = self._session_dir / "terminal.json"
        if not terminal.exists() or terminal.is_symlink():
            return False
        try:
            payload = json.loads(terminal.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        return payload.get("sessionId") != self._session.session_id

    # ------------------------------------------------------------ internals

    def _gate_dir(self, gate: str) -> Path:
        return self._session_dir / "gates" / gate

    def _persist(self) -> None:
        write_session(self._session_dir / "session.json", self._session)

    def _record_gate(
        self,
        name: str,
        *,
        gate_status: str,
        evidence_fingerprint: str,
        extra: dict | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "gateStatus": gate_status,
            "evidenceFingerprint": evidence_fingerprint,
            "sourceFingerprint": self._session.source_fingerprint,
        }
        if extra:
            entry.update(extra)
        self._session = replace(
            self._session, gates={**self._session.gates, name: entry}
        )

    def _require_status(self, allowed: set[str], *, gate: str) -> None:
        if self._session.status not in allowed:
            raise InvalidGateTransition(
                f"{gate} gate cannot run from session status "
                f"{self._session.status!r}; requires one of {sorted(allowed)}"
            )

    def _check_fresh(self, *, gate: str) -> bool:
        """Return True when the current source still matches the seal.

        On drift, write the ``stale_source`` terminal artifact and return False
        so the caller never runs the next gate.
        """
        if self._source_probe is None:
            return True
        try:
            current = self._source_probe()
            self._session.assert_fresh(**current)
            return True
        except StaleVerificationSession as exc:
            self._enter_terminal(
                "stale_source",
                gate=gate,
                gate_status="stale",
                diagnostics=(str(exc),),
                recovery=("Re-seal a fresh session from the current source state.",),
            )
            return False

    def _build_result(
        self,
        status: str,
        *,
        gate: str,
        gate_status: str,
        evidence_fingerprint: str,
        diagnostics: tuple[str, ...],
        recovery: tuple[str, ...],
        started_at: str,
    ) -> GateResult:
        return GateResult(
            session_id=self._session.session_id,
            gate=gate,
            status=status,
            gate_status=gate_status,
            source_fingerprint=self._session.source_fingerprint,
            evidence_fingerprint=evidence_fingerprint,
            started_at=started_at,
            finished_at=_now_rfc3339(),
            diagnostics=tuple(diagnostics),
            recovery=tuple(recovery),
        )

    def _advance(
        self,
        status: str,
        *,
        gate: str,
        gate_status: str,
        evidence_fingerprint: str,
        diagnostics: tuple[str, ...],
        recovery: tuple[str, ...],
        started_at: str,
        extra_gate: dict | None = None,
    ) -> GateResult:
        self._record_gate(
            _GATE_NAMES[gate],
            gate_status=gate_status,
            evidence_fingerprint=evidence_fingerprint,
            extra=extra_gate,
        )
        self._session = replace(self._session, status=status)
        self._persist()
        result = self._build_result(
            status, gate=gate, gate_status=gate_status,
            evidence_fingerprint=evidence_fingerprint,
            diagnostics=diagnostics, recovery=recovery, started_at=started_at,
        )
        self._last_result = result
        return result

    def _enter_terminal(
        self,
        status: str,
        *,
        gate: str,
        gate_status: str,
        evidence_fingerprint: str = "",
        diagnostics: tuple[str, ...] = (),
        recovery: tuple[str, ...] = (),
        started_at: str | None = None,
        extra_gate: dict | None = None,
    ) -> GateResult:
        self._record_gate(
            _GATE_NAMES[gate],
            gate_status=gate_status,
            evidence_fingerprint=evidence_fingerprint,
            extra=extra_gate,
        )
        self._session = replace(self._session, status=status)
        self._persist()
        self._write_terminal(
            gate=gate, status=status, diagnostics=diagnostics, recovery=recovery
        )
        result = self._build_result(
            status, gate=gate, gate_status=gate_status,
            evidence_fingerprint=evidence_fingerprint,
            diagnostics=diagnostics, recovery=recovery,
            started_at=started_at or _now_rfc3339(),
        )
        self._last_result = result
        return result

    def _write_terminal(
        self,
        *,
        gate: str,
        status: str,
        diagnostics: tuple[str, ...],
        recovery: tuple[str, ...],
    ) -> None:
        payload = {
            "schemaVersion": TERMINAL_SCHEMA,
            "sessionId": self._session.session_id,
            "status": status,
            "gate": gate,
            "sourceFingerprint": self._session.source_fingerprint,
            "diagnostics": list(diagnostics),
            "recovery": list(recovery),
            "generatedAt": _now_rfc3339(),
        }
        _write_json_atomic(self._session_dir / "terminal.json", payload)

    def _normalize_capability(
        self, capability: RunnerCapabilityReceipt | dict | str | None
    ) -> tuple[str, tuple[str, ...]]:
        if capability is None:
            return "turn_ready", ()
        if isinstance(capability, RunnerCapabilityReceipt):
            status, diagnostics = capability.status, capability.diagnostics
        elif isinstance(capability, dict):
            try:
                receipt = RunnerCapabilityReceipt.from_dict(capability)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"capability receipt is invalid: {exc}") from exc
            status, diagnostics = receipt.status, receipt.diagnostics
        elif isinstance(capability, str):
            status, diagnostics = capability, ()
        else:
            raise ValueError(
                "capability must be a RunnerCapabilityReceipt, dict, or status string"
            )
        if status not in ALLOWED_RECEIPT_STATUSES:
            raise ValueError(f"capability status is invalid: {status}")
        if status in {"static_ready", "blocked_runner_capability"}:
            return "blocked_runner_capability", diagnostics or (
                "runner is not live-turn ready",
            )
        if status == "blocked_runner_transport":
            return "blocked_runner_transport", diagnostics
        return "turn_ready", ()

    def _validate_review_report(self, report: object) -> dict:
        if not isinstance(report, dict):
            raise ValueError("review report must be an object")
        if set(report) != _REVIEW_REPORT_KEYS:
            raise ValueError("review report schema keys are invalid")
        if report.get("schemaVersion") != _REVIEW_REPORT_SCHEMA:
            raise ValueError("review report schemaVersion is invalid")
        if report.get("verdict") not in _REVIEW_VERDICTS:
            raise ValueError("review report verdict is invalid")
        if not isinstance(report.get("findings"), list):
            raise ValueError("review report findings must be a list")
        if not isinstance(report.get("reviewFingerprint"), str) or not report["reviewFingerprint"]:
            raise ValueError("review report reviewFingerprint is invalid")
        return report

    # ---------------------------------------------------------------- gates

    def run_pre_review(
        self,
        commands: list[dict] | None = None,
        *,
        runner: Callable[[], list[dict]] | None = None,
        diagnostics: tuple[str, ...] = (),
        recovery: tuple[str, ...] = (),
    ) -> GateResult:
        """Write the pre-review targeted verification gate evidence.

        A failing targeted command is recorded as failed evidence and becomes
        the terminal ``verification_failed`` state; the session stays ``sealed``
        on pass until Strict Review starts.
        """
        started_at = _now_rfc3339()
        self._require_status({"sealed"}, gate="pre_review")
        if not self._check_fresh(gate="pre_review"):
            return self._last_result
        if commands is None:
            if runner is None:
                raise ValueError("pre_review requires commands or a runner callable")
            commands = runner()
        evidence = write_gate_evidence(
            self._session, "pre_review", list(commands), self._gate_dir("pre_review")
        )
        if evidence.status != "pass":
            return self._enter_terminal(
                "verification_failed",
                gate="pre_review",
                gate_status="failed",
                evidence_fingerprint=evidence.evidence_fingerprint,
                diagnostics=("pre-review targeted verification failed",),
                recovery=(
                    "Fix the failing targeted check, then re-seal a fresh session.",
                ),
                started_at=started_at,
            )
        return self._advance(
            "sealed",
            gate="pre_review",
            gate_status="pass",
            evidence_fingerprint=evidence.evidence_fingerprint,
            diagnostics=diagnostics,
            recovery=recovery,
            started_at=started_at,
        )

    def run_strict_review(
        self,
        *,
        runner: Callable[[VerificationSession], dict] | None = None,
        capability: RunnerCapabilityReceipt | dict | str | None = None,
        diagnostics: tuple[str, ...] = (),
        recovery: tuple[str, ...] = (),
    ) -> GateResult:
        """Run the Strict Review gate against the sealed source.

        The runner callback receives the current session and must return the
        merged ``review-report-v1``. Capability failures fail closed before the
        runner is invoked; a transport-level failure (timeout / spawn error)
        maps to ``blocked_runner_transport`` and may be resumed on this session.
        """
        started_at = _now_rfc3339()
        self._require_status(
            {"sealed", "blocked_runner_capability", "blocked_runner_transport"},
            gate="strict_review",
        )
        pre = self._session.gates.get("preReview")
        if not isinstance(pre, dict) or pre.get("gateStatus") != "pass":
            raise InvalidGateTransition(
                "strict_review requires a passing pre_review gate"
            )
        if not self._check_fresh(gate="strict_review"):
            return self._last_result
        cap_status, cap_diagnostics = self._normalize_capability(capability)
        if cap_status == "blocked_runner_capability":
            return self._enter_terminal(
                "blocked_runner_capability",
                gate="strict_review",
                gate_status="blocked",
                diagnostics=cap_diagnostics or ("runner capability is blocked",),
                recovery=(
                    "Repair the runner cache/profile, then rerun strict review on this session.",
                ),
                started_at=started_at,
            )
        if cap_status == "blocked_runner_transport":
            return self._enter_terminal(
                "blocked_runner_transport",
                gate="strict_review",
                gate_status="blocked",
                diagnostics=cap_diagnostics or ("runner transport is blocked",),
                recovery=(
                    "Check runner transport/session/config, then rerun strict review on this session.",
                ),
                started_at=started_at,
            )
        if runner is None:
            raise ValueError("strict_review requires a runner or review callback")
        try:
            report = runner(self._session)
        except subprocess.TimeoutExpired:
            return self._enter_terminal(
                "blocked_runner_transport",
                gate="strict_review",
                gate_status="blocked",
                diagnostics=("strict review runner timed out",),
                recovery=(
                    "Check runner transport/session/config, then rerun strict review on this session.",
                ),
                started_at=started_at,
            )
        except OSError as exc:
            return self._enter_terminal(
                "blocked_runner_transport",
                gate="strict_review",
                gate_status="blocked",
                diagnostics=(f"strict review runner could not start: {exc}",),
                recovery=(
                    "Check runner transport/session/config, then rerun strict review on this session.",
                ),
                started_at=started_at,
            )
        try:
            report = self._validate_review_report(report)
        except ValueError as exc:
            return self._enter_terminal(
                "invalid_evidence",
                gate="strict_review",
                gate_status="blocked",
                diagnostics=(f"strict review report is invalid: {exc}",),
                recovery=(
                    "Fix the review runner output contract, then rerun strict review.",
                ),
                started_at=started_at,
            )

        verdict = report["verdict"]
        status = _VERDICT_TO_SESSION_STATUS[verdict]
        gate_status = _GATE_STATUS_BY_SESSION_STATUS[status]
        _write_json_atomic(self._session_dir / "review-report.json", report)
        exit_code = 0 if verdict == "pass" else 1
        evidence = write_gate_evidence(
            self._session,
            "strict_review",
            [{
                "label": "strict-review",
                "argv": ["strict-review-gate"],
                "exitCode": exit_code,
                "stdoutTail": verdict,
                "stderrTail": "",
            }],
            self._gate_dir("strict_review"),
        )
        if status == "review_passed":
            return self._advance(
                "review_passed",
                gate="strict_review",
                gate_status="pass",
                evidence_fingerprint=evidence.evidence_fingerprint,
                diagnostics=diagnostics,
                recovery=recovery,
                started_at=started_at,
            )
        return self._enter_terminal(
            status,
            gate="strict_review",
            gate_status=gate_status,
            evidence_fingerprint=evidence.evidence_fingerprint,
            diagnostics=diagnostics or (f"strict review ended with {verdict}",),
            recovery=recovery or (
                "Fix review findings or split the payload, then rerun strict review.",
            ),
            started_at=started_at,
        )

    def run_full_verification(
        self,
        commands: list[dict] | None = None,
        *,
        runner: Callable[[], list[dict]] | None = None,
        diagnostics: tuple[str, ...] = (),
        recovery: tuple[str, ...] = (),
    ) -> GateResult:
        """Run the full verification gate after Strict Review PASS.

        Any nonzero command records failed evidence and becomes the terminal
        ``verification_failed`` state; the earlier ``review_passed`` gate status
        is preserved in ``session.gates``.
        """
        started_at = _now_rfc3339()
        self._require_status({"review_passed"}, gate="full_pytest")
        if not self._check_fresh(gate="full_pytest"):
            return self._last_result
        if commands is None:
            if runner is None:
                raise ValueError("full verification requires commands or a runner callable")
            commands = runner()
        evidence = write_gate_evidence(
            self._session, "full_pytest", list(commands), self._gate_dir("full_pytest")
        )
        if evidence.status != "pass":
            return self._enter_terminal(
                "verification_failed",
                gate="full_pytest",
                gate_status="failed",
                evidence_fingerprint=evidence.evidence_fingerprint,
                diagnostics=("full verification failed",),
                recovery=(
                    "Fix the failing verification, then create a new session.",
                ),
                started_at=started_at,
            )
        return self._advance(
            "full_verification_passed",
            gate="full_pytest",
            gate_status="pass",
            evidence_fingerprint=evidence.evidence_fingerprint,
            diagnostics=diagnostics,
            recovery=recovery,
            started_at=started_at,
        )

    def run_hermes(
        self,
        *,
        runner: Callable[[VerificationSession], dict] | None = None,
        result: dict | None = None,
        profile: str | None = None,
        diagnostics: tuple[str, ...] = (),
        recovery: tuple[str, ...] = (),
    ) -> GateResult:
        """Run the Hermes acceptance gate after full verification PASS.

        Hermes remains read-only; the chain records the bounded gate artifact
        and the explicit ``primary-runtime`` or ``isolated-profile`` mode.
        Evidence bound to a different session/source fingerprint is rejected as
        ``hermes_failed``, and a no-profile resume cannot overwrite a profile
        failure.
        """
        started_at = _now_rfc3339()
        self._require_status({"full_verification_passed", "hermes_failed"}, gate="hermes")
        if self._session.status == "hermes_failed" and profile is None:
            raise InvalidGateTransition(
                "resuming hermes from hermes_failed requires an explicit profile"
            )
        if profile is not None and profile not in HERMES_PROFILES:
            raise ValueError("hermes profile must be primary-runtime or isolated-profile")
        if not self._check_fresh(gate="hermes"):
            return self._last_result
        if result is None:
            if runner is None:
                raise ValueError("hermes requires a runner callable or a result dict")
            result = runner(self._session)
        if not isinstance(result, dict):
            return self._enter_terminal(
                "hermes_failed",
                gate="hermes",
                gate_status="failed",
                diagnostics=("hermes result is not an object",),
                recovery=("Rerun hermes with an explicit profile and bounded result.",),
                started_at=started_at,
            )
        if result.get("sourceFingerprint") not in (None, self._session.source_fingerprint):
            return self._enter_terminal(
                "hermes_failed",
                gate="hermes",
                gate_status="failed",
                diagnostics=("hermes evidence source fingerprint does not match the session",),
                recovery=("Bind hermes evidence to this session's source fingerprint and rerun.",),
                started_at=started_at,
            )
        if result.get("sessionId") not in (None, self._session.session_id):
            return self._enter_terminal(
                "hermes_failed",
                gate="hermes",
                gate_status="failed",
                diagnostics=("hermes evidence sessionId does not match the session",),
                recovery=("Bind hermes evidence to this session and rerun.",),
                started_at=started_at,
            )
        hermes_status = result.get("overallStatus") or result.get("status")
        passed = hermes_status == "pass"
        exit_code = 0 if passed else 1
        evidence = write_gate_evidence(
            self._session,
            "hermes",
            [{
                "label": "hermes-acceptance",
                "argv": ["hermes-gate"],
                "exitCode": exit_code,
                "stdoutTail": str(hermes_status or "failed"),
                "stderrTail": "",
            }],
            self._gate_dir("hermes"),
        )
        _write_json_atomic(self._session_dir / "hermes-result.json", result)
        if not passed:
            return self._enter_terminal(
                "hermes_failed",
                gate="hermes",
                gate_status="failed",
                evidence_fingerprint=evidence.evidence_fingerprint,
                diagnostics=("hermes acceptance failed",),
                recovery=("Choose an explicit profile and rerun hermes on this session.",),
                started_at=started_at,
                extra_gate={"profile": profile},
            )
        return self._advance(
            "hermes_passed",
            gate="hermes",
            gate_status="pass",
            evidence_fingerprint=evidence.evidence_fingerprint,
            diagnostics=diagnostics,
            recovery=recovery,
            started_at=started_at,
            extra_gate={"profile": profile},
        )

    def attest(self) -> CompletionAttestation:
        """Deterministic completion attestation; never calls an LLM.

        Requires Strict Review PASS, full pytest PASS, Hermes PASS, a matching
        source fingerprint and an explicit Hermes profile. Writes
        ``completion.json``; only a matching ``complete`` may be displayed as
        round completion.
        """
        generated_at = _now_rfc3339()
        if not self._check_fresh(gate="completion"):
            required = self._required_gate_statuses()
            diagnostics = [
                f"{name} gate is not pass (status: {outcome or 'missing'})"
                for name, outcome in required.items()
                if outcome != "pass"
            ]
            if "stale_source" not in diagnostics:
                diagnostics.insert(0, "completion cannot attest a stale source")
            attestation = CompletionAttestation(
                session_id=self._session.session_id,
                status="blocked",
                required_gates=required,
                source_fingerprint=self._session.source_fingerprint,
                artifact_fingerprints=self._artifact_fingerprints(),
                diagnostics=tuple(diagnostics),
                generated_at=generated_at,
            )
            _write_json_atomic(self._session_dir / "completion.json", attestation.to_dict())
            return attestation

        required = self._required_gate_statuses()
        diagnostics: list[str] = []
        for name, outcome in required.items():
            if outcome != "pass":
                diagnostics.append(f"{name} gate is not pass (status: {outcome or 'missing'})")
        hermes_profile = self._session.gates.get("hermes", {}).get("profile")
        if required["hermes"] == "pass" and hermes_profile not in HERMES_PROFILES:
            diagnostics.append("hermes profile is ambiguous or missing")

        if diagnostics:
            status = "blocked"
        else:
            status = "complete"
            self._session = replace(self._session, status="complete")
            self._persist()

        attestation = CompletionAttestation(
            session_id=self._session.session_id,
            status=status,
            required_gates=required,
            source_fingerprint=self._session.source_fingerprint,
            artifact_fingerprints=self._artifact_fingerprints(),
            diagnostics=tuple(diagnostics),
            generated_at=generated_at,
        )
        _write_json_atomic(self._session_dir / "completion.json", attestation.to_dict())
        return attestation

    def _required_gate_statuses(self) -> dict:
        return {
            "strictReview": self._session.gates.get("strictReview", {}).get("gateStatus"),
            "fullPytest": self._session.gates.get("fullPytest", {}).get("gateStatus"),
            "hermes": self._session.gates.get("hermes", {}).get("gateStatus"),
        }

    def _artifact_fingerprints(self) -> dict:
        artifacts: dict[str, str] = {}
        session_path = self._session_dir / "session.json"
        if session_path.is_file() and not session_path.is_symlink():
            artifacts["session.json"] = sha256(session_path.read_bytes()).hexdigest()
        for gate in ("preReview", "strictReview", "fullPytest", "hermes"):
            evidence = self._session.gates.get(gate, {}).get("evidenceFingerprint")
            if isinstance(evidence, str) and evidence:
                artifacts[gate] = evidence
        return artifacts
