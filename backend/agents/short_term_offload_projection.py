from __future__ import annotations

from .short_term_offload_models import ShortTermOffloadReference


def project_offload_reference(reference: ShortTermOffloadReference) -> dict[str, object]:
    return {
        "nodeId": reference.node_id,
        "refId": reference.ref_id,
        "summary": reference.summary,
        "contentSha256": reference.content_sha256,
        "expiresAt": reference.expires_at.isoformat(),
    }


def project_mermaid_node(reference: ShortTermOffloadReference) -> dict[str, object]:
    return {"nodeId": reference.node_id, "label": reference.summary, "edges": ()}
