from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from backend.agents.short_term_offload_models import ShortTermOffloadArtifact, ShortTermOffloadReference
from backend.agents.short_term_offload_projection import project_mermaid_node, project_offload_reference


def test_projection_is_reference_only_and_has_no_inferred_edges() -> None:
    content = "result"
    artifact = ShortTermOffloadArtifact(
        "short-term-offload-v1", "ref-1", "run-1", "session-1", "tool_output", "summary", content,
        sha256(content.encode()).hexdigest(), datetime(2026, 8, 14, 12, tzinfo=timezone.utc),
        datetime(2026, 8, 14, 12, 30, tzinfo=timezone.utc), sha256(content.encode()).hexdigest(), "clean", "ready"
    )
    reference = ShortTermOffloadReference.from_artifact(artifact)
    projected = project_offload_reference(reference)
    assert set(projected) == {"nodeId", "refId", "summary", "contentSha256", "expiresAt"}
    assert project_mermaid_node(reference)["edges"] == ()
