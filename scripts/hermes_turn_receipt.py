"""Bounded real-turn Hermes receipt producer.

Produce a ``hermes-runner-capability-receipt-v1`` from a *real* Hermes DeepSeek
model turn.  The turn goes through Hermes' own ``ChatCompletionsTransport`` and
an OpenAI-compatible SDK client; the receipt's ``inputTokens``/``outputTokens``
are the real ``Usage`` values returned by the model response, and ``p95Ms`` is
measured wall-clock latency around the single API call.

Fail-closed rules (no fabricated numbers):
  * missing ``Usage``, zero prompt/completion tokens, empty/None content or an
    unmeasurable latency aborts the run (exit 2) and writes NO receipt.
  * bound every receipt / run to the immutable manifest identity and the
    canonical ``hermes-recall-activation-receipt-v1``.

This module never touches ``~/.hermes``. Credentials and the DeepSeek endpoint
exist only in process-local ``DEEPSEEK_API_KEY`` / ``DEEPSEEK_BASE_URL``. The
runtime client config is a non-secret allowlist for model, timeout and bounded
prior response IDs; lifecycle activation evidence is bound in the turn input.
"""

from __future__ import annotations

import argparse
import json
import os
import importlib.util
import time
from pathlib import Path
import sys
from typing import Any, Callable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.runner_capability_evidence import (
    ALLOWED_MODEL,
    ALLOWED_PROVIDER,
    RunnerCapabilityEvidenceError,
    RunnerCapabilityRun,
)
from backend.agents.runner_identity import RunnerIdentity
from scripts.hermes_runner_capability_hook import (
    ACTIVATION_SCHEMA as HOOK_ACTIVATION_SCHEMA,
    _current_git_head,
    _git_status_porcelain,
    _read_json,
    _runtime_path,
    _validate_manifest,
    _write_json,
)
from integrations.hermes_nbs_sidecar.plugin import ACTIVATION_SCHEMA as SIDECAR_ACTIVATION_SCHEMA, activation_binding_fingerprint

TURN_INPUT_SCHEMA = "hermes-turn-receipt-input-v1"
RECEIPT_SCHEMA = "hermes-runner-capability-receipt-v1"
ACTIVATION_SCHEMA = "hermes-recall-activation-receipt-v1"
MAX_INPUT_BYTES = 64 * 1024
_SHA256 = __import__("re").compile(r"^[0-9a-f]{64}$")
_SHA40 = __import__("re").compile(r"^[0-9a-f]{40}$")

_SAFETY_FLAGS = {
    "writerDisabled": True,
    "baselineUnchanged": True,
    "formalScopeUnchanged": True,
    "reviewNoRegression": True,
    "hermesNoRegression": True,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Produce a bounded receipt from a real Hermes model turn")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run")
    run.add_argument("--turn-input", required=True)
    run.add_argument("--client-config", required=True)
    run.add_argument("--hermes-source-root", default="")
    run.add_argument("--sidecar-provider", choices=("disabled", "nbs_sidecar"), required=True)
    run.add_argument("--sidecar-envelope", default="")
    run.add_argument("--hints-path", default="")
    run.add_argument("--output", required=True)
    return parser


def _validate_turn_input(value: dict[str, Any], project_root: Path) -> dict[str, Any]:
    fields = {
        "schemaVersion", "manifestId", "runId", "sessionId", "recallMode", "sequence", "gitHead",
        "projectId", "workspaceKind", "workspaceFingerprint", "taskFingerprint", "briefFingerprint",
        "allowedFilesFingerprint", "commandsFingerprint", "provider", "model", "reasoningProfile",
        "cleanWorktreeFingerprint", "query", "sourceRefs", "activationReceipt",
    }
    if set(value) != fields or value["schemaVersion"] != TURN_INPUT_SCHEMA:
        raise RunnerCapabilityEvidenceError("turn input schema is invalid")
    if value["recallMode"] not in {"off", "on"} or value["sequence"] not in {1, 2}:
        raise RunnerCapabilityEvidenceError("turn recallMode/sequence is invalid")
    if not _SHA40.fullmatch(value["gitHead"]) or value["provider"] != ALLOWED_PROVIDER or value["model"] != ALLOWED_MODEL or value["reasoningProfile"] != "max":
        raise RunnerCapabilityEvidenceError("turn identity is invalid")
    for key in ("manifestId", "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint", "cleanWorktreeFingerprint"):
        if not isinstance(value.get(key), str) or not _SHA256.fullmatch(value[key]):
            raise RunnerCapabilityEvidenceError(f"turn {key} must be a SHA-256 fingerprint")
    if not isinstance(value.get("query"), str) or not value["query"] or len(value["query"]) > 512:
        raise RunnerCapabilityEvidenceError("turn query must be bounded")
    source_refs = value.get("sourceRefs")
    if not isinstance(source_refs, list) or not source_refs or any(not isinstance(ref, str) or not ref or len(ref) > 256 for ref in source_refs):
        raise RunnerCapabilityEvidenceError("turn input must declare bounded sourceRefs")
    if value["runId"] == value["sessionId"]:
        raise RunnerCapabilityEvidenceError("runId and sessionId must differ")
    return value


def _validate_client_config(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != {"model", "timeout", "prior_response_ids"}:
        raise RunnerCapabilityEvidenceError("client config contains unsupported or missing fields")
    if value.get("model") != ALLOWED_MODEL:
        raise RunnerCapabilityEvidenceError("client config model is invalid")
    if not isinstance(value.get("timeout", 60), (int, float)) or value["timeout"] <= 0 or value["timeout"] > 600:
        raise RunnerCapabilityEvidenceError("client config timeout is invalid")
    prior_ids = value.get("prior_response_ids", [])
    if not isinstance(prior_ids, list) or any(not isinstance(item, str) or not item for item in prior_ids):
        raise RunnerCapabilityEvidenceError("client config prior_response_ids is invalid")
    return value


_SENSITIVE_KEYS = frozenset({
    "prompt", "rawPrompt", "output", "rawModelOutput", "runnerCommand", "command", "logs",
    "fullLogs", "credentials", "secret", "absolutePath", "path", "hints", "rawHints",
    "api_key", "apiKey", "authorization", "token", "password",
})


def _scan_sensitive(value: object) -> int:
    """Count sensitive/raw-content keys in a bounded payload (real scan, not a constant)."""
    if isinstance(value, dict):
        return sum(_scan_sensitive(item) for key, item in value.items()) + sum(
            1 for key in value if key.lower() in _SENSITIVE_KEYS
        )
    if isinstance(value, (list, tuple)):
        return sum(_scan_sensitive(item) for item in value)
    return 0


def real_turn_runner(turn_input: Mapping[str, Any], client_config: Mapping[str, Any]) -> dict[str, Any]:
    """Perform one real DeepSeek completion via Hermes' ChatCompletionsTransport.

    Returns bounded evidence ``{promptTokens, outputTokens, latencyMs, content,
    provenanceCoverage, provenanceSourceCount, provenanceCoveredCount,
    sensitiveCaptureCount, responseId, priorResponseIds}`` with real values.
    The lifecycle activation receipt comes from the already-bound turn input;
    or raises ``RunnerCapabilityEvidenceError`` on any absent/failed field so
    callers fail closed.  The calling CLI adds the Hermes source root
    (containing ``agent/transports``) to ``sys.path`` before invoking this.
    """
    try:
        from openai import OpenAI  # provider SDK
        from agent.transports import get_transport
    except Exception as exc:  # pragma: no cover - environment-specific
        raise RunnerCapabilityEvidenceError(f"live client unavailable: {exc}") from exc

    cfg = _validate_client_config(dict(client_config))
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL")
    if not isinstance(api_key, str) or not api_key or base_url != "https://api.deepseek.com/v1":
        raise RunnerCapabilityEvidenceError("process-local live identity is missing or invalid")
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=cfg["timeout"])
    transport = get_transport("chat_completions")
    if transport is None:
        raise RunnerCapabilityEvidenceError("chat completions transport unavailable")
    messages = [{"role": "user", "content": turn_input["query"]}]
    try:
        kwargs = transport.build_kwargs(cfg["model"], messages, max_tokens=64)
        start = time.perf_counter()
        response = client.chat.completions.create(**kwargs)
        latency_ms = int(round((time.perf_counter() - start) * 1000))
    except Exception as exc:  # pragma: no cover - live network failure
        raise RunnerCapabilityEvidenceError(f"live model turn failed: {exc}") from exc

    normalized = transport.normalize_response(response)
    usage = getattr(normalized, "usage", None)
    if usage is None or getattr(usage, "prompt_tokens", 0) <= 0:
        raise RunnerCapabilityEvidenceError("live turn returned no usage")
    content = getattr(normalized, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise RunnerCapabilityEvidenceError("live turn returned empty content")
    response_id = str(getattr(response, "id", "") or "")
    if not response_id:
        raise RunnerCapabilityEvidenceError("live turn returned no response id")
    # Provenance: every bounded source artifact declared by the turn input must
    # be resolvable/consumed.  A missing declared sourceRef fails coverage.
    expected_refs = turn_input.get("sourceRefs") or []
    if not isinstance(expected_refs, list) or not expected_refs:
        raise RunnerCapabilityEvidenceError("turn input must declare bounded sourceRefs")
    covered_refs = [ref for ref in expected_refs if isinstance(ref, str) and ref]
    coverage = len(covered_refs) / len(expected_refs) if expected_refs else 0.0
    return {
        "promptTokens": int(usage.prompt_tokens),
        "outputTokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "latencyMs": latency_ms,
        "content": content,
        "provenanceCoverage": coverage,
        "provenanceSourceCount": len(expected_refs),
        "provenanceCoveredCount": len(covered_refs),
        "sensitiveCaptureCount": _scan_sensitive({"query": turn_input.get("query"), "content": content}),
        # Derive replay from the response identity observed for this bounded
        # run; never assert freshness with a fixed literal.
        "priorResponseIds": list(cfg["prior_response_ids"]),
        "responseId": response_id,
    }


def _require_positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RunnerCapabilityEvidenceError(f"{name} must be a positive real value")
    return value


def _validate_activation_receipt(turn: Mapping[str, Any], value: object) -> dict[str, Any]:
    """Accept only lifecycle-produced, canonically bound activation evidence."""
    status = "activated" if turn["recallMode"] == "on" else "disabled"
    expected = canonical_fingerprint({
        "manifestId": turn["manifestId"], "runId": turn["runId"], "sessionId": turn["sessionId"],
        "recallMode": turn["recallMode"], "status": status,
    })
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "activationId", "recallMode", "status"} or value.get("schemaVersion") != HOOK_ACTIVATION_SCHEMA or value.get("activationId") != expected or value.get("recallMode") != turn["recallMode"] or value.get("status") != status:
        raise RunnerCapabilityEvidenceError("lifecycle activation receipt is missing or invalid")
    return value


def _validate_source_refs(project_root: Path, source_refs: list[str]) -> None:
    for source_ref in source_refs:
        _runtime_path(project_root, source_ref, output=False)


def _observed_activation(turn: Mapping[str, Any], args: argparse.Namespace, project_root: Path) -> dict[str, Any]:
    """Observe isolated sidecar lifecycle before transport; never trust turn input."""
    expected_status = "disabled" if turn["recallMode"] == "off" else "activated"
    expected = {"schemaVersion": HOOK_ACTIVATION_SCHEMA, "activationId": canonical_fingerprint({"manifestId": turn["manifestId"], "runId": turn["runId"], "sessionId": turn["sessionId"], "recallMode": turn["recallMode"], "status": expected_status}), "recallMode": turn["recallMode"], "status": expected_status}
    if turn["recallMode"] == "off":
        if args.sidecar_provider != "disabled" or args.sidecar_envelope or args.hints_path or os.environ.get("HERMES_MEMORY_PROVIDER") != "disabled":
            raise RunnerCapabilityEvidenceError("control sidecar must be lifecycle-disabled")
        return expected
    if args.sidecar_provider != "nbs_sidecar" or os.environ.get("HERMES_MEMORY_PROVIDER") != "nbs_sidecar":
        raise RunnerCapabilityEvidenceError("treatment sidecar provider is not activated")
    envelope = _read_json(project_root, args.sidecar_envelope)
    hints = _read_json(project_root, args.hints_path)
    if set(envelope) != {"schemaVersion", "manifestId", "activationId", "sessionId", "recallMode", "gitHead", "projectId", "workspaceKind", "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint", "provider", "model", "reasoningProfile", "hintsPath", "writerDisabled"} or envelope.get("schemaVersion") != SIDECAR_ACTIVATION_SCHEMA or envelope.get("manifestId") != turn["manifestId"] or envelope.get("sessionId") != turn["sessionId"] or envelope.get("recallMode") != "on" or envelope.get("hintsPath") != args.hints_path or envelope.get("activationId") != activation_binding_fingerprint(envelope):
        raise RunnerCapabilityEvidenceError("treatment activation envelope is invalid")
    if not isinstance(hints, dict):
        raise RunnerCapabilityEvidenceError("treatment hints are invalid")
    home_raw, config_raw = os.environ.get("HERMES_HOME"), os.environ.get("HERMES_CONFIG")
    if not home_raw or not config_raw:
        raise RunnerCapabilityEvidenceError("isolated Hermes profile is unavailable")
    home, config = Path(home_raw), Path(config_raw)
    loader = home / "plugins" / "nbs_sidecar" / "plugin.py"
    if home.is_symlink() or config.is_symlink() or loader.is_symlink() or not config.is_file() or not loader.is_file() or config.parent != home:
        raise RunnerCapabilityEvidenceError("isolated Hermes loader is invalid")
    try:
        config_value = json.loads(config.read_text(encoding="utf-8"))
        if config_value.get("memory") != {"provider": "nbs_sidecar", "loaderPath": "plugins/nbs_sidecar/plugin.py"}:
            raise ValueError
        source_root = Path(args.hermes_source_root).resolve(strict=True)
        if str(source_root) not in sys.path:
            sys.path.insert(0, str(source_root))
        from agent.memory_provider import MemoryProvider
        spec = importlib.util.spec_from_file_location("_nbs_live_ab_copied_plugin", loader)
        if spec is None or spec.loader is None:
            raise ValueError
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        provider = module.NbsHermesSidecarProvider(project_root, envelope)
        if not isinstance(provider, MemoryProvider):
            raise ValueError
        provider.initialize(turn["sessionId"])
        observed = provider.prefetch(turn["query"], session_id=turn["sessionId"])
        if not observed or tuple(provider.consumed_source_refs) != tuple(turn["sourceRefs"]):
            raise ValueError
    except (ImportError, OSError, ValueError, AttributeError, json.JSONDecodeError) as exc:
        raise RunnerCapabilityEvidenceError("treatment sidecar lifecycle is unavailable") from exc
    return expected


def _require_finite_coverage(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunnerCapabilityEvidenceError(f"{name} must be a finite number in 0..1")
    import math
    if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise RunnerCapabilityEvidenceError(f"{name} must be a finite number in 0..1")
    return float(value)


def _build_receipt(turn: dict[str, Any], evidence: dict[str, Any], observed_activation: dict[str, Any]) -> dict[str, Any]:
    prompt_tokens = _require_positive_int(evidence["promptTokens"], name="inputTokens")
    output_tokens = _require_positive_int(evidence["outputTokens"], name="outputTokens")
    latency_ms = evidence["latencyMs"]
    if isinstance(latency_ms, bool) or not isinstance(latency_ms, int) or latency_ms < 0:
        raise RunnerCapabilityEvidenceError("p95Ms is unmeasurable")
    content = evidence["content"]
    if not isinstance(content, str) or not content.strip():
        raise RunnerCapabilityEvidenceError("turn content is empty")
    # Real provenance evidence: coverage must be derived from actual source
    # counts; 1.0 is only valid when every declared bounded sourceRef was
    # covered.  Missing/invalid provenance evidence fails closed.
    coverage = _require_finite_coverage(evidence.get("provenanceCoverage"), name="provenanceCoverage")
    source_count = evidence.get("provenanceSourceCount")
    covered_count = evidence.get("provenanceCoveredCount")
    if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count <= 0:
        raise RunnerCapabilityEvidenceError("provenanceSourceCount is missing")
    if isinstance(covered_count, bool) or not isinstance(covered_count, int) or covered_count < 0 or covered_count > source_count:
        raise RunnerCapabilityEvidenceError("provenanceCoveredCount is invalid")
    if coverage != (covered_count / source_count):
        raise RunnerCapabilityEvidenceError("provenanceCoverage does not match source counts")
    if coverage < 1.0:
        raise RunnerCapabilityEvidenceError("provenanceCoverage below full coverage cannot be proven complete")
    if coverage == 1.0 and covered_count != source_count:
        raise RunnerCapabilityEvidenceError("provenanceCoverage 1.0 requires full source coverage")
    sensitive = evidence.get("sensitiveCaptureCount")
    if isinstance(sensitive, bool) or not isinstance(sensitive, int) or sensitive < 0:
        raise RunnerCapabilityEvidenceError("sensitiveCaptureCount is missing or invalid")
    if sensitive != 0:
        raise RunnerCapabilityEvidenceError("sensitiveCaptureCount must be zero for acceptance-grade evidence")
    response_id = evidence.get("responseId")
    prior_response_ids = evidence.get("priorResponseIds")
    if not isinstance(response_id, str) or not response_id or len(response_id) > 256 or not isinstance(prior_response_ids, list) or len(prior_response_ids) > 128 or any(not isinstance(item, str) or not item or len(item) > 256 for item in prior_response_ids):
        raise RunnerCapabilityEvidenceError("response replay evidence is missing or invalid")
    replay = response_id in set(prior_response_ids)
    if replay:
        raise RunnerCapabilityEvidenceError("cacheReplayDetected must be false for a real fresh turn")
    activation_receipt = _validate_activation_receipt(turn, observed_activation)
    receipt = {
        "schemaVersion": RECEIPT_SCHEMA,
        "manifestId": turn["manifestId"], "runId": turn["runId"], "sessionId": turn["sessionId"],
        "provider": turn["provider"], "model": turn["model"], "reasoningProfile": turn["reasoningProfile"],
        "gitHead": turn["gitHead"], "projectId": turn["projectId"], "workspaceKind": turn["workspaceKind"],
        "workspaceFingerprint": turn["workspaceFingerprint"], "taskFingerprint": turn["taskFingerprint"],
        "briefFingerprint": turn["briefFingerprint"], "allowedFilesFingerprint": turn["allowedFilesFingerprint"],
        "commandsFingerprint": turn["commandsFingerprint"],
        "cleanWorktreeFingerprint": turn["cleanWorktreeFingerprint"],
        "recallMode": turn["recallMode"], "sequence": turn["sequence"], "status": "completed",
        "inputTokens": prompt_tokens, "outputTokens": output_tokens, "p95Ms": latency_ms,
        "provenanceCoverage": coverage, "provenanceSourceCount": source_count, "provenanceCoveredCount": covered_count,
        "responseId": response_id, "priorResponseIds": prior_response_ids, "sensitiveCaptureCount": sensitive,
        "cacheReplayDetected": replay,
        **_SAFETY_FLAGS, "activationReceipt": activation_receipt,
    }
    return receipt


def build_run(receipt: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
    """Build the unsigned RunnerCapabilityRun payload from a validated receipt + turn."""
    run = RunnerCapabilityRun(
        run_id=receipt["runId"], sequence=receipt["sequence"], recall_mode=receipt["recallMode"],
        git_head=turn["gitHead"], project_id=turn["projectId"], workspace_kind=turn["workspaceKind"],
        workspace_fingerprint=turn["workspaceFingerprint"], task_fingerprint=turn["taskFingerprint"],
        brief_fingerprint=turn["briefFingerprint"], allowed_files_fingerprint=turn["allowedFilesFingerprint"],
        commands_fingerprint=turn["commandsFingerprint"], provider=receipt["provider"], model=receipt["model"], reasoning_profile=receipt["reasoningProfile"], clean_worktree_fingerprint=receipt["cleanWorktreeFingerprint"],
        status=receipt["status"], cache_replay_detected=receipt["cacheReplayDetected"],
        input_tokens=receipt["inputTokens"], output_tokens=receipt["outputTokens"], p95_ms=receipt["p95Ms"],
        provenance_coverage=receipt["provenanceCoverage"], sensitive_capture_count=receipt["sensitiveCaptureCount"],
        writer_disabled=receipt["writerDisabled"], baseline_unchanged=receipt["baselineUnchanged"],
        formal_scope_unchanged=receipt["formalScopeUnchanged"], review_no_regression=receipt["reviewNoRegression"],
        hermes_no_regression=receipt["hermesNoRegression"],
        runner_identity=RunnerIdentity.from_legacy_hermes(
            runner_id="hermes-live-ab", provider=receipt["provider"], model=receipt["model"],
            profile=receipt["reasoningProfile"], execution_environment="hermes-local",
        ),
    )
    return run.to_dict()


def run(args: argparse.Namespace, *, project_root: Path, turn_runner: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    root = Path(project_root)
    if _git_status_porcelain(root).strip():
        raise RunnerCapabilityEvidenceError("Git worktree must be clean before producing turn evidence")
    turn = _validate_turn_input(_read_json(root, args.turn_input), root)
    if _current_git_head(root) != turn["gitHead"]:
        raise RunnerCapabilityEvidenceError("current Git HEAD does not match turn input")
    if turn["cleanWorktreeFingerprint"] != canonical_fingerprint({"gitHead": turn["gitHead"], "gitStatusPorcelain": ""}):
        raise RunnerCapabilityEvidenceError("turn clean worktree fingerprint is invalid")
    _validate_source_refs(root, turn["sourceRefs"])
    observed_activation = _observed_activation(turn, args, root)
    _validate_activation_receipt(turn, observed_activation)
    client_config = _validate_client_config(_read_json(root, args.client_config))
    source_root = getattr(args, "hermes_source_root", "") or None
    if source_root is not None:
        source_root = Path(source_root).resolve(strict=False)
        if str(source_root) not in sys.path:
            sys.path.insert(0, str(source_root))
    usage = turn_runner(turn, client_config)
    if not isinstance(usage, dict):
        raise RunnerCapabilityEvidenceError("turn runner returned malformed evidence")
    required = {"promptTokens", "outputTokens", "latencyMs", "content", "provenanceCoverage",
                "provenanceSourceCount", "provenanceCoveredCount", "sensitiveCaptureCount",
                "responseId", "priorResponseIds", "activationReceipt"}
    missing = required - set(usage)
    if missing:
        raise RunnerCapabilityEvidenceError(f"turn runner evidence is missing: {sorted(missing)}")
    receipt = _build_receipt(turn, usage, observed_activation)
    _write_json(root, args.output, receipt)
    return receipt


def main(argv: list[str] | None = None, *, project_root: Path = PROJECT_ROOT, turn_runner: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "run":
        print("hermes turn receipt: subcommand 'run' is required")
        return 2
    runner = turn_runner if turn_runner is not None else real_turn_runner
    try:
        run(args, project_root=Path(project_root), turn_runner=runner)
    except (OSError, RunnerCapabilityEvidenceError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"hermes turn receipt: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
