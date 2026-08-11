from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_sidecar_hint_models import MemoryHint, MemoryHints
from backend.agents.runner_capability_evidence import RunnerCapabilityEvidenceError
from integrations.hermes_nbs_sidecar.plugin import ACTIVATION_SCHEMA, activation_binding_fingerprint
from scripts.hermes_runner_capability_hook import _current_git_head, _git_status_porcelain, _read_json, _validate_manifest, _validate_receipt, _write_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a bounded per-session Hermes NBS sidecar activation")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--manifest", required=True)
    create.add_argument("--receipt", required=True)
    create.add_argument("--query", required=True)
    create.add_argument("--hints-output", required=True)
    create.add_argument("--output", required=True)
    probe = commands.add_parser("probe")
    probe.add_argument("--envelope", required=True)
    probe.add_argument("--query", required=True)
    probe.add_argument("--session-id", default="")
    probe.add_argument("--hermes-source-root", required=True)
    probe.add_argument("--output", required=True)
    return parser


def _workspace_fingerprint(project_root: Path, manifest: dict[str, Any]) -> str:
    return canonical_fingerprint({
        "projectRoot": str(project_root.resolve()), "projectId": manifest["projectId"],
        "workspaceKind": manifest["workspaceKind"],
    })


def create(args: argparse.Namespace, *, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    root = Path(project_root)
    if _git_status_porcelain(root).strip():
        raise RunnerCapabilityEvidenceError("Git worktree must be clean before activating sidecar")
    manifest = _validate_manifest(_read_json(root, args.manifest))
    if _current_git_head(root) != manifest["gitHead"]:
        raise RunnerCapabilityEvidenceError("current Git HEAD does not match manifest")
    if manifest["recallMode"] != "on" or manifest["sequence"] != 2 or manifest["provider"] != "hermes" or manifest["model"] != "deepseek-v4-flash" or manifest["reasoningProfile"] != "max" or manifest["writerDisabled"] is not True:
        raise RunnerCapabilityEvidenceError("manifest is not an eligible recall-on treatment")
    if manifest["workspaceFingerprint"] != _workspace_fingerprint(root, manifest):
        raise RunnerCapabilityEvidenceError("manifest workspace fingerprint does not match current root")
    receipt = _read_json(root, args.receipt)
    if _validate_receipt(manifest, receipt):
        raise RunnerCapabilityEvidenceError("recall-on activation receipt is missing or invalid")
    if not isinstance(args.query, str) or not args.query or len(args.query) > 512:
        raise RunnerCapabilityEvidenceError("query must be bounded")
    envelope = {
        "schemaVersion": ACTIVATION_SCHEMA, "manifestId": manifest["manifestId"], "activationId": "",
        "sessionId": receipt["sessionId"], "recallMode": "on", "gitHead": manifest["gitHead"],
        "projectId": manifest["projectId"], "workspaceKind": manifest["workspaceKind"],
        "workspaceFingerprint": manifest["workspaceFingerprint"], "taskFingerprint": manifest["taskFingerprint"],
        "briefFingerprint": manifest["briefFingerprint"], "allowedFilesFingerprint": manifest["allowedFilesFingerprint"],
        "commandsFingerprint": manifest["commandsFingerprint"], "provider": "hermes", "model": "deepseek-v4-flash",
        "reasoningProfile": "max", "hintsPath": args.hints_output, "writerDisabled": True,
    }
    envelope["activationId"] = activation_binding_fingerprint(envelope)
    hints = MemoryHints(
        query_fingerprint=canonical_fingerprint({"query": args.query}), status="ready",
        hints=(MemoryHint(canonical_fingerprint({"activationId": envelope["activationId"], "kind": "verification_pattern"}), "Use only bounded, non-authoritative verification context.", ("sidecar-activation.json",), "fresh", "high", (envelope["activationId"],)),),
    )
    _write_json(root, args.hints_output, hints.to_dict())
    _write_json(root, args.output, envelope)
    return envelope


def probe(args: argparse.Namespace, *, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Run the provider lifecycle through Hermes' real MemoryProvider ABC.

    This is a compatibility/activation probe, not a model call. It is only
    reachable with an explicit envelope and therefore cannot enable ordinary
    development sessions.
    """
    root = Path(project_root)
    envelope = _read_json(root, args.envelope)
    hermes_root = Path(args.hermes_source_root).resolve(strict=True)
    if not (hermes_root / "agent" / "memory_provider.py").is_file():
        raise RunnerCapabilityEvidenceError("Hermes source root is invalid")
    if str(hermes_root) not in sys.path:
        sys.path.insert(0, str(hermes_root))
    importlib.invalidate_caches()
    module = importlib.import_module("integrations.hermes_nbs_sidecar.plugin")
    module = importlib.reload(module)
    from agent.memory_provider import MemoryProvider
    provider = module.NbsHermesSidecarProvider(root, envelope)
    provider._current_git_head = lambda: _current_git_head(root)
    provider._git_status_porcelain = lambda: _git_status_porcelain(root)
    if not isinstance(provider, MemoryProvider):
        raise RunnerCapabilityEvidenceError("provider is not a Hermes MemoryProvider")
    session_id = args.session_id or str(envelope.get("sessionId", ""))
    provider.initialize(session_id)
    value = provider.prefetch(args.query, session_id=session_id)
    if not provider.is_available() or not value:
        raise RunnerCapabilityEvidenceError("sidecar activation probe is unavailable")
    provider.sync_turn("input", "output", session_id=session_id)
    telemetry = {
        "schemaVersion": "hermes-nbs-sidecar-probe-v1",
        "provider": envelope.get("provider"),
        "providerName": provider.name,
        "model": envelope.get("model"),
        "reasoningProfile": envelope.get("reasoningProfile"),
        "sessionId": session_id,
        "activationId": envelope.get("activationId"),
        "prefetchBytes": len(value.encode("utf-8")),
        "writerDisabled": envelope.get("writerDisabled") is True,
        "syncTurn": "no_op",
    }
    _write_json(root, args.output, telemetry)
    return telemetry


def main(argv: list[str] | None = None, *, project_root: Path = PROJECT_ROOT) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            create(args, project_root=Path(project_root))
        else:
            probe(args, project_root=Path(project_root))
    except (OSError, RunnerCapabilityEvidenceError, ValueError) as exc:
        print(f"hermes sidecar activation: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
