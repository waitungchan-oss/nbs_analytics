"""Bounded, isolated control/treatment launcher for the live Hermes A/B gate.

This module deliberately does not perform a live call by itself.  It prepares
two tightly bound child invocations; a missing/invalid child receipt blocks the
run and leaves only redacted, bounded diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Mapping
from uuid import uuid4

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_sidecar_hint_models import MemoryHint, MemoryHints
from integrations.hermes_nbs_sidecar.plugin import ACTIVATION_SCHEMA, activation_binding_fingerprint
from backend.agents.runner_capability_evidence import RunnerCapabilityEvidenceError
from backend.agents.hermes_cli_transport import CliInvokeRequest, HermesCliTransportAdapter
from backend.agents.hermes_cli_transport_receipt import CliTransportReceipt, write_cli_transport_receipt
from scripts.hermes_isolated_profile import IsolatedHermesProfile
from scripts.hermes_runner_capability_hook import _current_git_head, _git_status_porcelain, _validate_manifest
from backend.agents.short_term_offload_policy import ShortTermOffloadPolicy
from backend.agents.short_term_offload_service import persist_tool_output
from backend.agents.short_term_offload_store import ShortTermOffloadStore


_ENDPOINT = "https://api.deepseek.com/v1"
_MAX_DIAGNOSTIC_BYTES = 512
_CHILD_TIMEOUT_SECONDS = 90


@dataclass(frozen=True)
class LiveABRunResult:
    status: str
    reason: str
    control_receipt_path: str | None
    treatment_receipt_path: str | None
    diagnostic_path: str | None


@dataclass(frozen=True)
class LocalCliTransportResult:
    status: str
    reason: str
    receipt_path: str | None


def run_local_cli_transport(
    request: CliInvokeRequest,
    *,
    output_path: Path,
    adapter: HermesCliTransportAdapter | None = None,
) -> LocalCliTransportResult:
    """Explicitly run one Local CLI request; existing Remote API callers are untouched."""
    result = (adapter or HermesCliTransportAdapter()).invoke(request)
    if result.status != "ready":
        return LocalCliTransportResult(result.status, result.reason or "cli_transport_failed", None)
    try:
        receipt = CliTransportReceipt.from_result(
            result,
            source_fingerprint=request.source_fingerprint,
            command_shape_fingerprint=request.command_shape_fingerprint or "0" * 64,
        )
        write_cli_transport_receipt(output_path, receipt)
    except (OSError, ValueError, TypeError) as exc:
        return LocalCliTransportResult("invalid_evidence", "receipt_write_failed", None)
    return LocalCliTransportResult("ready", "", str(output_path))


def _immutable_identity(project_root: Path) -> tuple[str, str]:
    if _git_status_porcelain(project_root).strip():
        raise RunnerCapabilityEvidenceError("Git worktree is not clean")
    head = _current_git_head(project_root)
    return head, canonical_fingerprint({"gitHead": head, "gitStatusPorcelain": ""})


def _runtime_relative(project_root: Path, path: Path) -> str:
    runtime = project_root / ".nbs_agent_runtime"
    return str(path.relative_to(runtime))


def _workspace_fingerprint(project_root: Path, manifest: Mapping[str, object]) -> str:
    return canonical_fingerprint({"projectRoot": str(project_root.resolve()), "projectId": manifest["projectId"], "workspaceKind": manifest["workspaceKind"]})


def _safe_source_refs(project_root: Path, source_refs: list[str]) -> list[tuple[str, str]]:
    runtime = project_root / ".nbs_agent_runtime"
    if runtime.is_symlink():
        raise RunnerCapabilityEvidenceError("runtime root is unsafe")
    resolved_root = runtime.resolve(strict=False)
    result: list[tuple[str, str]] = []
    for ref in source_refs:
        candidate = Path(ref)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise RunnerCapabilityEvidenceError("source ref is unsafe")
        path = runtime / candidate
        current = runtime
        for part in candidate.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise RunnerCapabilityEvidenceError("source ref is unsafe")
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise RunnerCapabilityEvidenceError("source ref is unavailable") from exc
        if not resolved.is_file() or resolved.is_symlink() or resolved.stat().st_size > 64 * 1024:
            raise RunnerCapabilityEvidenceError("source ref is unavailable")
        import hashlib
        result.append((ref, hashlib.sha256(resolved.read_bytes()).hexdigest()))
    return result


def _activation_envelope(manifest: Mapping[str, object], session_id: str, hints_path: str) -> dict[str, object]:
    envelope = {"schemaVersion": ACTIVATION_SCHEMA, "manifestId": manifest["manifestId"], "activationId": "", "sessionId": session_id, "recallMode": "on", "gitHead": manifest["gitHead"], "projectId": manifest["projectId"], "workspaceKind": manifest["workspaceKind"], "workspaceFingerprint": manifest["workspaceFingerprint"], "taskFingerprint": manifest["taskFingerprint"], "briefFingerprint": manifest["briefFingerprint"], "allowedFilesFingerprint": manifest["allowedFilesFingerprint"], "commandsFingerprint": manifest["commandsFingerprint"], "provider": "hermes", "model": "deepseek-v4-flash", "reasoningProfile": "max", "hintsPath": hints_path, "writerDisabled": True}
    envelope["activationId"] = activation_binding_fingerprint(envelope)
    return envelope


def _arm_manifest(base: Mapping[str, object], recall_mode: str, sequence: int) -> dict[str, object]:
    unsigned = {key: value for key, value in base.items() if key != "manifestId"}
    unsigned.update({"recallMode": recall_mode, "sequence": sequence})
    return {**unsigned, "manifestId": canonical_fingerprint(unsigned)}


def _turn_input(manifest: Mapping[str, object], query: str, source_refs: list[str], arm: str) -> dict[str, object]:
    run_id = f"live-{arm}-{uuid4().hex}"
    session_id = f"session-{arm}-{uuid4().hex}"
    status = "disabled" if manifest["recallMode"] == "off" else "activated"
    activation = {
        "schemaVersion": "hermes-recall-activation-receipt-v1",
        "activationId": canonical_fingerprint({"manifestId": manifest["manifestId"], "runId": run_id, "sessionId": session_id, "recallMode": manifest["recallMode"], "status": status}),
        "recallMode": manifest["recallMode"],
        "status": status,
    }
    return {
        "schemaVersion": "hermes-turn-receipt-input-v1",
        **manifest,
        "runId": run_id,
        "sessionId": session_id,
        "query": query,
        "sourceRefs": source_refs,
        "activationReceipt": activation,
    }


def _redact(value: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value[:_MAX_DIAGNOSTIC_BYTES]


def _blocked(project_root: Path, root: Path, reason: str, stdout: str = "", stderr: str = "", secrets: tuple[str, ...] = ()) -> LiveABRunResult:
    for receipt in (root / "control" / "receipt.json", root / "treatment" / "receipt.json"):
        if receipt.is_file() and not receipt.is_symlink():
            receipt.unlink()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / "blocked.json"
    marker.write_text(json.dumps({"status": "blocked_runner_capability", "reason": reason, "stdout": _redact(stdout, secrets), "stderr": _redact(stderr, secrets)}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return LiveABRunResult("blocked_runner_capability", reason, None, None, _runtime_relative(project_root, marker))


def _default_child(command: list[str], *, env: Mapping[str, str], timeout: int) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(command, env=dict(env), capture_output=True, text=True, timeout=timeout, check=False)
        return completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, str(exc.stdout or ""), str(exc.stderr or "")


def _persist_child_output(project_root: Path, *, run_id: str, session_id: str, arm: str, stdout: str) -> str | None:
    bounded = stdout.encode("utf-8")[: ShortTermOffloadPolicy().max_content_bytes].decode("utf-8", errors="ignore")
    fingerprint = sha256(bounded.encode("utf-8")).hexdigest()
    store = ShortTermOffloadStore(project_root, policy=ShortTermOffloadPolicy())
    result = persist_tool_output(
        store, run_id=run_id, session_id=session_id, ref_id=f"{arm}-child-output",
        content=bounded, summary=f"Hermes {arm} bounded child output",
        source_fingerprint=fingerprint, now=datetime.now(timezone.utc),
    )
    return result.reference.ref_id if result.reference is not None else None


def run_live_ab(
    profile: IsolatedHermesProfile,
    manifest: Mapping[str, object],
    query: str,
    source_refs: list[str],
    *,
    project_root: str | Path,
    env: Mapping[str, str],
    child_runner: Callable[..., tuple[int, str, str]] | None = None,
    timeout_seconds: int = _CHILD_TIMEOUT_SECONDS,
    short_term_offload: str = "off",
) -> LiveABRunResult:
    """Run exactly one control and one treatment child, otherwise fail closed."""
    if short_term_offload not in {"off", "on"}:
        raise ValueError("short_term_offload must be off or on")
    root = Path(project_root).resolve(strict=False)
    fallback_root = root / ".nbs_agent_runtime" / "live-ab" / "blocked"
    if profile.status != "ready" or profile.home_dir is None or profile.config_path is None or profile.plugin_dir is None or profile.home_dir.is_symlink() or profile.config_path.is_symlink() or profile.plugin_dir.is_symlink() or not profile.config_path.is_file() or not profile.plugin_dir.is_dir() or not isinstance(query, str) or not query or len(query) > 512 or not isinstance(source_refs, list) or not source_refs or any(not isinstance(item, str) or not item or len(item) > 256 for item in source_refs):
        return _blocked(root, fallback_root, "completion_missing")
    try:
        base = _validate_manifest(dict(manifest))
    except (RunnerCapabilityEvidenceError, ValueError, TypeError):
        return _blocked(root, fallback_root, "identity_mismatch")
    api_key, base_url = env.get("DEEPSEEK_API_KEY"), env.get("DEEPSEEK_BASE_URL")
    if not isinstance(api_key, str) or not api_key or base_url != _ENDPOINT or set(profile.credential_env_names) != {"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"}:
        return _blocked(root, fallback_root, "live_identity_missing")
    acceptance_root = profile.home_dir.parent
    if acceptance_root.parent.name != "live-ab" or acceptance_root.is_symlink() or not acceptance_root.is_dir():
        return _blocked(root, fallback_root, "isolated_home_unavailable")
    try:
        sources = _safe_source_refs(root, source_refs)
    except RunnerCapabilityEvidenceError:
        return _blocked(root, acceptance_root, "completion_missing")
    child = child_runner or _default_child
    child_env = {"HERMES_HOME": str(profile.home_dir), "DEEPSEEK_API_KEY": api_key, "DEEPSEEK_BASE_URL": base_url, "PYTHONDONTWRITEBYTECODE": "1", "NBS_SHORT_TERM_OFFLOAD": short_term_offload}
    hermes_source_root = env.get("HERMES_SOURCE_ROOT", "")
    source_root_path = Path(hermes_source_root) if isinstance(hermes_source_root, str) else Path()
    if not isinstance(hermes_source_root, str) or not source_root_path.is_absolute() or source_root_path.is_symlink() or not (source_root_path / "agent" / "memory_provider.py").is_file() or (source_root_path / "agent" / "memory_provider.py").is_symlink():
        return _blocked(root, acceptance_root, "isolated_home_unavailable", secrets=(api_key,))
    hermes_source_root = str(source_root_path.resolve(strict=True))
    secrets = (api_key,)
    for arm, recall_mode, sequence in (("control", "off", 1), ("treatment", "on", 2)):
        arm_manifest = _arm_manifest(base, recall_mode, sequence)
        turn = _turn_input(arm_manifest, query, list(source_refs), arm)
        arm_root = acceptance_root / arm
        if arm_root.exists() and (arm_root.is_symlink() or not arm_root.is_dir()):
            return _blocked(root, acceptance_root, "isolated_home_unavailable", secrets=secrets)
        arm_root.mkdir(parents=True, exist_ok=True)
        turn_path = arm_root / "turn-input.json"
        config_path = arm_root / "client-config.json"
        receipt_path = arm_root / "receipt.json"
        hints_path = arm_root / "hints.json"
        envelope_path = arm_root / "activation-envelope.json"
        sidecar_env = {"HERMES_MEMORY_PROVIDER": "disabled"}
        sidecar_args = ["--sidecar-provider", "disabled"]
        if recall_mode == "on":
            hints = MemoryHints(canonical_fingerprint({"query": query}), "ready", (MemoryHint(canonical_fingerprint({"manifestId": arm_manifest["manifestId"], "kind": "live_ab"}), "Use only bounded, non-authoritative verification context.", tuple(ref for ref, _ in sources), "fresh", "high", tuple(fingerprint for _, fingerprint in sources)),))
            hints_path.write_text(json.dumps(hints.to_dict(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
            envelope = _activation_envelope(arm_manifest, str(turn["sessionId"]), _runtime_relative(root, hints_path))
            envelope_path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")), encoding="utf-8")
            sidecar_env = {"HERMES_MEMORY_PROVIDER": "nbs_sidecar", "HERMES_SIDECAR_ACTIVATION_ENVELOPE": _runtime_relative(root, envelope_path), "HERMES_SIDECAR_HINTS_PATH": _runtime_relative(root, hints_path)}
            sidecar_args = ["--sidecar-provider", "nbs_sidecar", "--sidecar-envelope", _runtime_relative(root, envelope_path), "--hints-path", _runtime_relative(root, hints_path)]
        turn_path.write_text(json.dumps(turn, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        config_path.write_text(json.dumps({"model": "deepseek-v4-flash", "timeout": timeout_seconds, "prior_response_ids": []}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        command = [sys.executable, str(root / "scripts" / "hermes_turn_receipt.py"), "run", "--turn-input", _runtime_relative(root, turn_path), "--client-config", _runtime_relative(root, config_path), "--output", _runtime_relative(root, receipt_path), *( ["--hermes-source-root", hermes_source_root] if hermes_source_root else []), *sidecar_args]
        try:
            head, fingerprint = _immutable_identity(root)
        except RunnerCapabilityEvidenceError:
            return _blocked(root, acceptance_root, "identity_mismatch", secrets=secrets)
        if head != base["gitHead"] or fingerprint != base["cleanWorktreeFingerprint"]:
            return _blocked(root, acceptance_root, "identity_mismatch", secrets=secrets)
        if _workspace_fingerprint(root, arm_manifest) != arm_manifest["workspaceFingerprint"]:
            return _blocked(root, acceptance_root, "identity_mismatch", secrets=secrets)
        returncode, stdout, stderr = child(command, env={**child_env, **sidecar_env, "HERMES_CONFIG": str(profile.config_path)}, timeout=timeout_seconds)
        if short_term_offload == "on" and returncode == 0:
            try:
                _persist_child_output(root, run_id=str(turn["runId"]), session_id=str(turn["sessionId"]), arm=arm, stdout=stdout)
            except (OSError, ValueError, TypeError):
                # Optional offload must never change the underlying Hermes verdict.
                pass
        if returncode != 0 or not receipt_path.is_file() or receipt_path.is_symlink():
            return _blocked(root, acceptance_root, "completion_missing", stdout, stderr, secrets)
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            immutable = ("gitHead", "projectId", "workspaceKind", "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint", "provider", "model", "reasoningProfile", "cleanWorktreeFingerprint")
            if receipt.get("status") != "completed" or receipt.get("runId") != turn["runId"] or receipt.get("sessionId") != turn["sessionId"] or receipt.get("recallMode") != recall_mode or receipt.get("sequence") != sequence or receipt.get("activationReceipt") != turn["activationReceipt"] or any(receipt.get(key) != turn[key] for key in immutable):
                raise ValueError("incomplete receipt")
        except (OSError, ValueError, json.JSONDecodeError):
            return _blocked(root, acceptance_root, "completion_missing", stdout, stderr, secrets)
    return LiveABRunResult("completed", "", _runtime_relative(root, acceptance_root / "control" / "receipt.json"), _runtime_relative(root, acceptance_root / "treatment" / "receipt.json"), None)
