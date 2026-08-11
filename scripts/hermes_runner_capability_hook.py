from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.runner_capability_evidence import RunnerCapabilityEvidenceError, RunnerCapabilityRun


MANIFEST_SCHEMA = "hermes-runner-capability-manifest-v1"
RECEIPT_SCHEMA = "hermes-runner-capability-receipt-v1"
ACTIVATION_SCHEMA = "hermes-recall-activation-receipt-v1"
MAX_INPUT_BYTES = 64 * 1024
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and record bounded Hermes runner capability receipts")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--recall-mode", choices=("off", "on"), required=True)
    prepare.add_argument("--sequence", choices=(1, 2), type=int, required=True)
    for flag in ("git-head", "project-id", "workspace-kind", "workspace-fingerprint", "task-fingerprint", "brief-fingerprint", "allowed-files-fingerprint", "commands-fingerprint", "output"):
        prepare.add_argument(f"--{flag}", required=True)
    record = commands.add_parser("record")
    record.add_argument("--manifest", required=True)
    record.add_argument("--receipt", required=True)
    record.add_argument("--output", required=True)
    return parser


def _current_git_head(project_root: Path) -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RunnerCapabilityEvidenceError("unable to read immutable Git HEAD")
    return result.stdout.strip()


def _git_status_porcelain(project_root: Path) -> str:
    result = subprocess.run(["git", "status", "--porcelain"], cwd=project_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RunnerCapabilityEvidenceError("unable to verify clean Git worktree")
    return result.stdout


def _runtime_path(project_root: Path, raw_path: str, *, output: bool) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise RunnerCapabilityEvidenceError("runtime paths must be relative")
    root = project_root.resolve(strict=False) / ".nbs_agent_runtime"
    if root.is_symlink():
        raise RunnerCapabilityEvidenceError("runtime root must not be a symlink")
    path = root / candidate
    current = root
    for part in candidate.parts:
        if part in {"", ".", ".."}:
            raise RunnerCapabilityEvidenceError("runtime path traversal is not allowed")
        current = current / part
        if current.exists() and current.is_symlink():
            raise RunnerCapabilityEvidenceError("runtime path must not contain symlinks")
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise RunnerCapabilityEvidenceError("runtime path is out of root") from exc
    if output:
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise RunnerCapabilityEvidenceError("output must be a regular file")
    elif not path.exists() or path.is_symlink() or not path.is_file():
        raise RunnerCapabilityEvidenceError("input must be an existing regular file")
    return path


def _read_json(project_root: Path, raw_path: str) -> dict[str, Any]:
    path = _runtime_path(project_root, raw_path, output=False)
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise RunnerCapabilityEvidenceError("runtime input exceeds byte cap")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerCapabilityEvidenceError("runtime input is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RunnerCapabilityEvidenceError("runtime input must be a JSON object")
    return value


def _write_json(project_root: Path, raw_path: str, value: dict[str, Any]) -> None:
    path = _runtime_path(project_root, raw_path, output=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RunnerCapabilityEvidenceError("output must not be a symlink")
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _require_fingerprint(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise RunnerCapabilityEvidenceError(f"{name} must be a SHA-256 fingerprint")
    return value


def _manifest_unsigned(args: argparse.Namespace) -> dict[str, Any]:
    if not isinstance(args.git_head, str) or not _SHA40.fullmatch(args.git_head):
        raise RunnerCapabilityEvidenceError("git head must be a full immutable SHA")
    if not isinstance(args.project_id, str) or not _IDENTIFIER.fullmatch(args.project_id):
        raise RunnerCapabilityEvidenceError("project id is invalid")
    if args.workspace_kind not in {"repo", "isolated_worktree"}:
        raise RunnerCapabilityEvidenceError("workspace kind is invalid")
    for attr, label in (("workspace_fingerprint", "workspace fingerprint"), ("task_fingerprint", "task fingerprint"), ("brief_fingerprint", "brief fingerprint"), ("allowed_files_fingerprint", "allowed files fingerprint"), ("commands_fingerprint", "commands fingerprint")):
        _require_fingerprint(getattr(args, attr), label)
    return {
        "schemaVersion": MANIFEST_SCHEMA, "recallMode": args.recall_mode, "sequence": args.sequence,
        "gitHead": args.git_head, "projectId": args.project_id, "workspaceKind": args.workspace_kind, "workspaceFingerprint": args.workspace_fingerprint,
        "taskFingerprint": args.task_fingerprint, "briefFingerprint": args.brief_fingerprint,
        "allowedFilesFingerprint": args.allowed_files_fingerprint, "commandsFingerprint": args.commands_fingerprint,
        "provider": "hermes", "model": "deepseek-v4-flash", "reasoningProfile": "max",
        "cleanWorktreeFingerprint": canonical_fingerprint({"gitHead": args.git_head, "gitStatusPorcelain": ""}),
        "writerDisabled": True,
    }


def _validate_manifest(value: dict[str, Any]) -> dict[str, Any]:
    fields = {"schemaVersion", "manifestId", "recallMode", "sequence", "gitHead", "projectId", "workspaceKind", "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint", "provider", "model", "reasoningProfile", "cleanWorktreeFingerprint", "writerDisabled"}
    if set(value) != fields or value.get("schemaVersion") != MANIFEST_SCHEMA or not isinstance(value.get("manifestId"), str):
        raise RunnerCapabilityEvidenceError("manifest schema is invalid")
    unsigned = {key: item for key, item in value.items() if key != "manifestId"}
    if value["manifestId"] != canonical_fingerprint(unsigned):
        raise RunnerCapabilityEvidenceError("manifest fingerprint is invalid")
    if value["recallMode"] not in {"off", "on"} or value["sequence"] not in {1, 2} or not _SHA40.fullmatch(value["gitHead"]):
        raise RunnerCapabilityEvidenceError("manifest identity is invalid")
    if value["provider"] != "hermes" or value["model"] != "deepseek-v4-flash" or value["reasoningProfile"] != "max" or value["writerDisabled"] is not True:
        raise RunnerCapabilityEvidenceError("manifest runner identity is invalid")
    expected_clean = canonical_fingerprint({"gitHead": value["gitHead"], "gitStatusPorcelain": ""})
    if value["cleanWorktreeFingerprint"] != expected_clean:
        raise RunnerCapabilityEvidenceError("manifest clean worktree fingerprint is invalid")
    _manifest_unsigned(argparse.Namespace(
        recall_mode=value["recallMode"], sequence=value["sequence"], git_head=value["gitHead"], project_id=value["projectId"], workspace_kind=value["workspaceKind"], workspace_fingerprint=value["workspaceFingerprint"],
        task_fingerprint=value["taskFingerprint"], brief_fingerprint=value["briefFingerprint"], allowed_files_fingerprint=value["allowedFilesFingerprint"], commands_fingerprint=value["commandsFingerprint"],
    ))
    return value


def _activation_is_valid(manifest: dict[str, Any], receipt: dict[str, Any]) -> bool:
    value = receipt["activationReceipt"]
    status = "activated" if manifest["recallMode"] == "on" else "disabled"
    expected = canonical_fingerprint({"manifestId": manifest["manifestId"], "runId": receipt["runId"], "sessionId": receipt["sessionId"], "recallMode": manifest["recallMode"], "status": status})
    return isinstance(value, dict) and set(value) == {"schemaVersion", "activationId", "recallMode", "status"} and value.get("schemaVersion") == ACTIVATION_SCHEMA and value.get("activationId") == expected and value.get("recallMode") == manifest["recallMode"] and value.get("status") == status


def _validate_receipt(manifest: dict[str, Any], receipt: dict[str, Any]) -> bool:
    fields = {"schemaVersion", "manifestId", "runId", "sessionId", "provider", "model", "reasoningProfile", "cleanWorktreeFingerprint", "recallMode", "sequence", "status", "inputTokens", "outputTokens", "p95Ms", "provenanceCoverage", "provenanceSourceCount", "provenanceCoveredCount", "responseId", "priorResponseIds", "sensitiveCaptureCount", "cacheReplayDetected", "writerDisabled", "baselineUnchanged", "formalScopeUnchanged", "reviewNoRegression", "hermesNoRegression", "activationReceipt"}
    if set(receipt) != fields or receipt.get("schemaVersion") != RECEIPT_SCHEMA:
        raise RunnerCapabilityEvidenceError("receipt schema is invalid")
    for field in ("manifestId", "runId", "sessionId", "provider", "model", "recallMode", "status"):
        if not isinstance(receipt.get(field), str):
            raise RunnerCapabilityEvidenceError("receipt identity is invalid")
    if receipt["manifestId"] != manifest["manifestId"] or receipt["provider"] != "hermes" or receipt["model"] != "deepseek-v4-flash" or receipt["reasoningProfile"] != "max" or receipt["cleanWorktreeFingerprint"] != manifest["cleanWorktreeFingerprint"] or receipt["recallMode"] != manifest["recallMode"] or receipt["sequence"] != manifest["sequence"] or receipt["runId"] == receipt["sessionId"] or receipt["status"] != "completed":
        raise RunnerCapabilityEvidenceError("receipt immutable identity is invalid")
    if not _IDENTIFIER.fullmatch(receipt["responseId"]) or not isinstance(receipt["priorResponseIds"], list) or len(receipt["priorResponseIds"]) > 128 or any(not isinstance(item, str) or not _IDENTIFIER.fullmatch(item) for item in receipt["priorResponseIds"]):
        raise RunnerCapabilityEvidenceError("receipt response replay evidence is invalid")
    replay = receipt["responseId"] in set(receipt["priorResponseIds"])
    if receipt["cacheReplayDetected"] is not replay or replay:
        raise RunnerCapabilityEvidenceError("receipt cache replay evidence is invalid")
    if not isinstance(receipt["provenanceSourceCount"], int) or isinstance(receipt["provenanceSourceCount"], bool) or receipt["provenanceSourceCount"] <= 0 or not isinstance(receipt["provenanceCoveredCount"], int) or isinstance(receipt["provenanceCoveredCount"], bool) or receipt["provenanceCoveredCount"] != receipt["provenanceSourceCount"] or receipt["provenanceCoverage"] != 1.0:
        raise RunnerCapabilityEvidenceError("receipt provenance evidence is invalid")
    for field in ("inputTokens", "outputTokens"):
        if isinstance(receipt[field], bool) or not isinstance(receipt[field], int) or not 0 < receipt[field] <= 10_000_000:
            raise RunnerCapabilityEvidenceError("receipt token usage is invalid")
    if isinstance(receipt["p95Ms"], bool) or not isinstance(receipt["p95Ms"], int) or not 0 <= receipt["p95Ms"] <= 3_600_000:
        raise RunnerCapabilityEvidenceError("receipt latency evidence is invalid")
    if isinstance(receipt["sensitiveCaptureCount"], bool) or not isinstance(receipt["sensitiveCaptureCount"], int) or receipt["sensitiveCaptureCount"] != 0:
        raise RunnerCapabilityEvidenceError("receipt sensitive capture evidence is invalid")
    if not all(receipt.get(name) is True for name in ("writerDisabled", "baselineUnchanged", "formalScopeUnchanged", "reviewNoRegression", "hermesNoRegression")):
        raise RunnerCapabilityEvidenceError("receipt safety evidence is invalid")
    if not _activation_is_valid(manifest, receipt):
        raise RunnerCapabilityEvidenceError("receipt activation evidence is invalid")
    return False


def _prepare(args: argparse.Namespace, project_root: Path) -> dict[str, Any]:
    unsigned = _manifest_unsigned(args)
    if _git_status_porcelain(project_root).strip():
        raise RunnerCapabilityEvidenceError("Git worktree must be clean before preparing capability evidence")
    if _current_git_head(project_root) != args.git_head:
        raise RunnerCapabilityEvidenceError("current Git HEAD does not match supplied immutable head")
    manifest = {**unsigned, "manifestId": canonical_fingerprint(unsigned)}
    _write_json(project_root, args.output, manifest)
    return manifest


def _record(args: argparse.Namespace, project_root: Path) -> dict[str, Any]:
    manifest = _validate_manifest(_read_json(project_root, args.manifest))
    if _git_status_porcelain(project_root).strip() or _current_git_head(project_root) != manifest["gitHead"]:
        raise RunnerCapabilityEvidenceError("Git identity changed before recording capability evidence")
    if canonical_fingerprint({"gitHead": manifest["gitHead"], "gitStatusPorcelain": ""}) != manifest["cleanWorktreeFingerprint"]:
        raise RunnerCapabilityEvidenceError("clean worktree fingerprint changed before recording")
    receipt = _read_json(project_root, args.receipt)
    activation_missing = _validate_receipt(manifest, receipt)
    run = RunnerCapabilityRun(
        run_id=receipt["runId"], sequence=receipt["sequence"], recall_mode=receipt["recallMode"], git_head=manifest["gitHead"], project_id=manifest["projectId"], workspace_kind=manifest["workspaceKind"],
        workspace_fingerprint=manifest["workspaceFingerprint"], task_fingerprint=manifest["taskFingerprint"], brief_fingerprint=manifest["briefFingerprint"], allowed_files_fingerprint=manifest["allowedFilesFingerprint"], commands_fingerprint=manifest["commandsFingerprint"], provider=receipt["provider"], model=receipt["model"], reasoning_profile=receipt["reasoningProfile"], clean_worktree_fingerprint=receipt["cleanWorktreeFingerprint"], status=receipt["status"], cache_replay_detected=receipt["cacheReplayDetected"], input_tokens=receipt["inputTokens"], output_tokens=receipt["outputTokens"], p95_ms=receipt["p95Ms"], provenance_coverage=receipt["provenanceCoverage"], sensitive_capture_count=receipt["sensitiveCaptureCount"], writer_disabled=receipt["writerDisabled"], baseline_unchanged=receipt["baselineUnchanged"], formal_scope_unchanged=receipt["formalScopeUnchanged"], review_no_regression=receipt["reviewNoRegression"], hermes_no_regression=receipt["hermesNoRegression"],
    )
    payload = run.to_dict()
    _write_json(project_root, args.output, payload)
    return payload


def main(argv: list[str] | None = None, *, project_root: Path = PROJECT_ROOT) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            _prepare(args, Path(project_root))
        else:
            _record(args, Path(project_root))
    except (OSError, RunnerCapabilityEvidenceError, ValueError) as exc:
        print(f"hermes runner capability hook: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
