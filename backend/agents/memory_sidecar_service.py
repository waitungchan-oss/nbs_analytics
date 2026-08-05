from __future__ import annotations

from queue import Empty, Queue
from threading import BoundedSemaphore, Thread
from typing import Protocol

from .evidence_models import canonical_fingerprint
from .memory_sidecar_adapter import MemorySidecarProvider, MemorySidecarProviderError, RecallLimits
from .memory_sidecar_hint_models import MemoryHints
from .memory_sidecar_policy import MemorySidecarPolicy


class _RecallProvider(Protocol):
    def recall(self, *, query: str, query_fingerprint: str, limits: RecallLimits) -> MemoryHints:
        ...


class MemorySidecarService:
    def __init__(self, policy: MemorySidecarPolicy) -> None:
        self.policy = policy
        self._recall_gate = BoundedSemaphore(1)

    def recall(self, *, query: str, provider: MemorySidecarProvider) -> MemoryHints:
        if not isinstance(query, str) or not query.strip() or len(query) > 512:
            raise ValueError("memory recall query must be a bounded non-empty string")
        normalized_query = query.strip()
        query_fingerprint = canonical_fingerprint({"query": normalized_query})
        limits = RecallLimits(
            max_items=self.policy.max_items,
            max_bytes=self.policy.max_bytes,
            timeout_ms=self.policy.timeout_ms,
        )
        if not self._recall_gate.acquire(blocking=False):
            return MemoryHints.empty(query_fingerprint=query_fingerprint, status="timeout")
        result: Queue[tuple[str, object]] = Queue(maxsize=1)

        def invoke() -> None:
            try:
                result.put(("ok", provider.recall(
                    query=normalized_query,
                    query_fingerprint=query_fingerprint,
                    limits=limits,
                )))
            except BaseException as exc:  # transport provider failures across the daemon boundary
                result.put(("error", exc))
            finally:
                self._recall_gate.release()

        Thread(target=invoke, name="memory-sidecar-recall", daemon=True).start()
        try:
            outcome, raw = result.get(timeout=limits.timeout_ms / 1000)
        except Empty:
            return MemoryHints.empty(query_fingerprint=query_fingerprint, status="timeout")
        if outcome == "error":
            exc = raw
            if isinstance(exc, MemorySidecarProviderError):
                status = "timeout" if exc.code == "timeout" else "degraded"
                return MemoryHints.empty(query_fingerprint=query_fingerprint, status=status)
            return MemoryHints.empty(query_fingerprint=query_fingerprint, status="degraded")

        try:
            payload = raw.to_dict() if isinstance(raw, MemoryHints) else raw
            hints = MemoryHints.from_dict(payload)
        except Exception:
            return MemoryHints.empty(query_fingerprint=query_fingerprint, status="degraded")
        if hints.query_fingerprint != query_fingerprint:
            return MemoryHints.empty(query_fingerprint=query_fingerprint, status="degraded")
        if hints.status != "ready":
            return MemoryHints.empty(query_fingerprint=query_fingerprint, status=hints.status)
        if any(item.freshness != "fresh" for item in hints.hints):
            return MemoryHints.empty(query_fingerprint=query_fingerprint, status="empty")
        bounded = []
        for item in hints.hints:
            if len(bounded) >= self.policy.max_items:
                break
            candidate = MemoryHints(query_fingerprint, "ready", tuple([*bounded, item]))
            if candidate.serialized_size_bytes() > self.policy.max_bytes:
                break
            bounded.append(item)
        if not bounded:
            return MemoryHints.empty(query_fingerprint=query_fingerprint, status="empty")
        return MemoryHints(query_fingerprint, "ready", tuple(bounded))
