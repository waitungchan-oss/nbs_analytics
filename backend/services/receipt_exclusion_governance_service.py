from __future__ import annotations


def verify_receipt_exclusion_confirmation(
    *,
    canonical_proposal: dict,
    private_evidence: dict,
    submitted_fingerprint: str,
    selected_candidate_ids: list[str],
) -> list[dict]:
    if canonical_proposal.get("proposalFingerprint") != submitted_fingerprint:
        raise ValueError("stale receipt exclusion proposal")
    allowed = {
        str(item["candidateId"]): item
        for item in canonical_proposal.get("candidates", [])
    }
    selected: list[dict] = []
    for candidate_id in selected_candidate_ids:
        if candidate_id not in allowed or candidate_id not in private_evidence:
            raise ValueError("unknown receipt exclusion candidate")
        selected.append({**allowed[candidate_id], **private_evidence[candidate_id]})
    if not selected:
        raise ValueError("at least one receipt exclusion candidate is required")
    return selected
