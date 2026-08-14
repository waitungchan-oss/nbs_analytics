from __future__ import annotations

from typing import Any

from .memory_hub_models import MemoryQueryResult
from .memory_sidecar_hint_models import MemoryHint, MemoryHints
from .memory_sidecar_models import MemorySidecarSchemaError


MEMORY_HUB_PROJECTION_SCHEMA = "memory-hub-projection-v1"
_NON_READY_HINT_STATUS = "degraded"


def _empty_hints(result: MemoryQueryResult) -> MemoryHints:
    status = result.status if result.status in {"empty", "timeout", "degraded"} else _NON_READY_HINT_STATUS
    return MemoryHints.empty(query_fingerprint=result.query_fingerprint, status=status)


def project_memory_result(result: MemoryQueryResult | None) -> MemoryHints | None:
    """Project a validated Hub result into the existing read-only hint contract.

    This function is deliberately a one-way projection: it never writes a catalog,
    changes provider defaults, or turns a blocked/failed query into recall content.
    """
    if result is None:
        return None

    if result.status == "ready":
        hints = tuple(
            MemoryHint(
                memory_id=record.memory_id,
                summary=record.summary,
                source_refs=tuple(source.artifact_ref for source in record.source_refs),
                source_fingerprints=tuple(source.artifact_sha256 for source in record.source_refs),
                freshness=record.freshness,
                confidence="high",
            )
            for record in result.records
            if record.status == "ready"
            and record.freshness == "fresh"
            and all(source.status == "verified" for source in record.source_refs)
        )
        try:
            sidecar_hints = MemoryHints(
                query_fingerprint=result.query_fingerprint,
                status="ready" if hints else _NON_READY_HINT_STATUS,
                hints=hints,
            )
        except MemorySidecarSchemaError:
            sidecar_hints = MemoryHints.empty(query_fingerprint=result.query_fingerprint, status=_NON_READY_HINT_STATUS)
    else:
        sidecar_hints = _empty_hints(result)

    # Context Agent adds the non-authoritative authority label when it serializes
    # a MemoryHints instance. Returning the existing model keeps this adapter
    # compatible with both sidecar and Context Agent consumers.
    return sidecar_hints
