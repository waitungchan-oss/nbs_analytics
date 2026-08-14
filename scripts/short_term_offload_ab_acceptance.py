#!/usr/bin/env python3
"""Read-only acceptance for three independent Short-term Offload A/B pairs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from backend.agents.short_term_offload_ab_models import ShortTermOffloadABEvidence, ShortTermOffloadABEvidenceError
from backend.agents.short_term_offload_ab_service import compare_short_term_offload_runs


def _expected(item: ShortTermOffloadABEvidence) -> ShortTermOffloadABEvidence:
    return compare_short_term_offload_runs(
        item.control, item.treatment, workload_fingerprint=item.workload_fingerprint,
        control_receipt_ref=item.control_receipt_ref, treatment_receipt_ref=item.treatment_receipt_ref,
        provenance_refs=item.provenance_refs,
    )


def _output_path(path_value: str | Path, root: Path) -> Path:
    raw = Path(path_value)
    if raw.is_symlink():
        raise ValueError("acceptance output is symlinked")
    candidate = raw if raw.is_absolute() else root / raw
    try:
        lexical = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("acceptance output is outside evidence root") from exc
    if any(part in {"", ".", ".."} for part in lexical.parts):
        raise ValueError("acceptance output path is unsafe")
    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("acceptance output is outside evidence root") from exc
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("acceptance output path contains symlink")
    if candidate.exists() and (not candidate.is_file() or candidate.is_symlink()):
        raise ValueError("acceptance output is unavailable")
    return candidate


def evaluate_three_pairs(evidence_paths: tuple[str | Path, ...], *, output_path: str | Path | None = None) -> dict[str, Any]:
    if len(evidence_paths) != 3:
        return {"schemaVersion": "short-term-offload-ab-acceptance-v1", "status": "blocked_runner_capability", "reason": "three_independent_pairs_required"}
    evidences: list[ShortTermOffloadABEvidence] = []
    try:
        seen: set[str] = set()
        for path_value in evidence_paths:
            path = Path(path_value)
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024:
                raise ValueError("evidence file unavailable")
            resolved_path = path.resolve(strict=True)
            if resolved_path.parent != Path(evidence_paths[0]).resolve(strict=True).parent:
                raise ValueError("evidence files must share an isolated root")
            evidence = ShortTermOffloadABEvidence.from_dict(json.loads(resolved_path.read_text(encoding="utf-8")))
            if evidence.to_dict() != _expected(evidence).to_dict():
                raise ValueError("evidence is not derivable from its live runs")
            if evidence.evidence_fingerprint in seen:
                raise ValueError("evidence pair reused")
            seen.add(evidence.evidence_fingerprint)
            evidences.append(evidence)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, ShortTermOffloadABEvidenceError) as exc:
        result = {"schemaVersion": "short-term-offload-ab-acceptance-v1", "status": "blocked_runner_capability", "reason": str(exc)[:256]}
    else:
        identities = tuple((item.workload_fingerprint, item.control.git_head, item.control.project_id,
                            item.control.workspace_kind, item.control.workspace_fingerprint,
                            item.control.task_fingerprint, item.control.brief_fingerprint,
                            item.control.allowed_files_fingerprint, item.control.commands_fingerprint,
                            item.control.provider, item.control.model, item.control.reasoning_profile,
                            item.control.clean_worktree_fingerprint, item.provenance_refs) for item in evidences)
        if len(set(identities)) != 1:
            result = {"schemaVersion": "short-term-offload-ab-acceptance-v1", "status": "blocked_runner_capability", "reason": "immutable_identity_mismatch"}
        elif any(item.result != "pass" for item in evidences):
            result = {"schemaVersion": "short-term-offload-ab-acceptance-v1", "status": "acceptance_rejected", "reason": "pair_not_pass", "pairResults": [item.result for item in evidences]}
        else:
            ratios = [item.token_reduction_ratio for item in evidences]
            result = {
                "schemaVersion": "short-term-offload-ab-acceptance-v1", "status": "pass",
                "pairCount": 3, "tokenReductionRatios": ratios,
                "meanTokenReductionRatio": sum(ratios) / len(ratios),
                "evidenceFingerprints": [item.evidence_fingerprint for item in evidences],
                "pairs": [{
                    "evidenceFingerprint": item.evidence_fingerprint,
                    "controlRunId": item.control.run_id, "treatmentRunId": item.treatment.run_id,
                    "controlReceiptRef": item.control_receipt_ref, "treatmentReceiptRef": item.treatment_receipt_ref,
                    "controlInputTokens": item.control.input_tokens, "controlOutputTokens": item.control.output_tokens,
                    "controlTotalTokens": item.control.input_tokens + item.control.output_tokens,
                    "treatmentInputTokens": item.treatment.input_tokens, "treatmentOutputTokens": item.treatment.output_tokens,
                    "treatmentTotalTokens": item.treatment.input_tokens + item.treatment.output_tokens,
                    "controlP95Ms": item.control.p95_ms, "treatmentP95Ms": item.treatment.p95_ms,
                    "tokenReductionRatio": item.token_reduction_ratio, "latencyDeltaRatio": item.latency_delta_ratio,
                } for item in evidences],
            }
    if output_path is not None:
        if not evidence_paths:
            raise ValueError("evidence root is unavailable")
        root = Path(evidence_paths[0]).resolve(strict=True).parent
        output = _output_path(output_path, root)
        serialized = json.dumps(result, sort_keys=True, separators=(",", ":"))
        if output.exists() and output.read_text(encoding="utf-8") != serialized:
            raise ValueError("acceptance output already exists with different content")
        if not output.exists():
            output.write_text(serialized, encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only three-pair offload A/B acceptance")
    parser.add_argument("--evidence", action="append", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    result = evaluate_three_pairs(tuple(args.evidence), output_path=args.output)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
