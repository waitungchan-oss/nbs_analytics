#!/usr/bin/env python3
"""Bounded operator for comparing one live Short-term Offload A/B pair."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.runner_capability_evidence import RunnerCapabilityEvidenceError, live_receipt_to_run
from backend.agents.short_term_offload_ab_models import ShortTermOffloadABEvidenceError
from backend.agents.short_term_offload_ab_service import compare_short_term_offload_runs
from scripts.hermes_live_ab_runner import LiveABRunResult, run_live_ab


_MAX_RECEIPT_BYTES = 256 * 1024


def run_bounded_ab_workload(
    profile: Any,
    manifest: Mapping[str, object],
    query: str,
    source_refs: list[str],
    *,
    project_root: str | Path,
    env: Mapping[str, str],
    evidence_root: str | Path,
    provenance_refs: tuple[str, ...],
    short_term_offload: str = "off",
    child_runner: Any = None,
) -> tuple[LiveABRunResult, Path | None]:
    """Run one explicit off/on mode and derive evidence only from live receipts."""
    if short_term_offload not in {"off", "on"}:
        raise ValueError("short_term_offload must be off or on")
    result = run_live_ab(
        profile, manifest, query, source_refs, project_root=project_root, env=env,
        child_runner=child_runner, short_term_offload=short_term_offload,
    )
    if result.status != "completed" or not result.control_receipt_path or not result.treatment_receipt_path:
        return result, None
    root = Path(evidence_root)
    if not root.is_absolute():
        root = Path.cwd() / root
    if root.is_symlink() or not root.is_dir():
        raise ValueError("evidence root is unavailable")
    control = root / result.control_receipt_path
    treatment = root / result.treatment_receipt_path
    output = record_ab_evidence(
        control, treatment, evidence_root=root,
        workload_fingerprint=canonical_fingerprint({
            "gitHead": manifest.get("gitHead"), "taskFingerprint": manifest.get("taskFingerprint"),
            "briefFingerprint": manifest.get("briefFingerprint"),
            "allowedFilesFingerprint": manifest.get("allowedFilesFingerprint"),
            "commandsFingerprint": manifest.get("commandsFingerprint"),
        }), provenance_refs=provenance_refs,
    )
    return result, output


def _safe_file(path: Path, root: Path) -> Path:
    raw_root = root
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise ValueError("evidence root is unavailable")
    root = raw_root.resolve(strict=True)
    candidate = path if path.is_absolute() else root / path
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise ValueError("receipt is outside evidence root")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("receipt path contains symlink")
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > _MAX_RECEIPT_BYTES:
        raise ValueError("receipt is unavailable")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("receipt is outside evidence root") from exc
    return resolved


def _read_receipt(path: Path, root: Path) -> Mapping[str, Any]:
    safe = _safe_file(path, root)
    payload = json.loads(safe.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("receipt must be an object")
    return payload


def record_ab_evidence(
    control_receipt: str | Path,
    treatment_receipt: str | Path,
    *,
    evidence_root: str | Path,
    workload_fingerprint: str,
    provenance_refs: tuple[str, ...],
) -> Path:
    """Validate two immutable live receipts and write one derived evidence file."""
    raw_root = Path(evidence_root)
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise ValueError("evidence root is unavailable")
    root = raw_root.resolve(strict=True)
    control_path, treatment_path = Path(control_receipt), Path(treatment_receipt)
    control_payload = _read_receipt(control_path, root)
    treatment_payload = _read_receipt(treatment_path, root)
    control, _ = live_receipt_to_run(control_payload)
    treatment, _ = live_receipt_to_run(treatment_payload)
    evidence = compare_short_term_offload_runs(
        control, treatment, workload_fingerprint=workload_fingerprint,
        control_receipt_ref=str(control_path.resolve().relative_to(root)),
        treatment_receipt_ref=str(treatment_path.resolve().relative_to(root)),
        provenance_refs=provenance_refs,
    )
    output = root / "short-term-offload-ab" / f"{evidence.evidence_fingerprint}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output_parent = output.parent
    current = root
    for part in output_parent.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("evidence output path contains symlink")
    if output.is_symlink():
        raise ValueError("evidence output is symlinked")
    serialized = json.dumps(evidence.to_dict(), sort_keys=True, separators=(",", ":"))
    if output.exists():
        if not output.is_file() or output.read_text(encoding="utf-8") != serialized:
            raise ValueError("evidence output already exists with different content")
        return output
    output.write_text(serialized, encoding="utf-8")
    return output


def compare_receipts(
    control_receipt: str | Path,
    treatment_receipt: str | Path,
    *,
    evidence_root: str | Path,
    provenance_refs: tuple[str, ...],
) -> Path:
    control_payload = _read_receipt(Path(control_receipt), Path(evidence_root))
    workload_fingerprint = canonical_fingerprint({
        "gitHead": control_payload.get("gitHead"),
        "taskFingerprint": control_payload.get("taskFingerprint"),
        "briefFingerprint": control_payload.get("briefFingerprint"),
        "allowedFilesFingerprint": control_payload.get("allowedFilesFingerprint"),
        "commandsFingerprint": control_payload.get("commandsFingerprint"),
    })
    return record_ab_evidence(
        control_receipt, treatment_receipt, evidence_root=evidence_root,
        workload_fingerprint=workload_fingerprint, provenance_refs=provenance_refs,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two bounded live offload receipts")
    parser.add_argument("--control-receipt", required=True)
    parser.add_argument("--treatment-receipt", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--provenance-ref", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        output = compare_receipts(args.control_receipt, args.treatment_receipt,
                                  evidence_root=args.evidence_root,
                                  provenance_refs=tuple(args.provenance_ref))
        print(json.dumps({"status": "ready", "evidencePath": str(output)}, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            RunnerCapabilityEvidenceError, ShortTermOffloadABEvidenceError) as exc:
        print(json.dumps({"status": "blocked_runner_capability", "reason": str(exc)[:256]}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
