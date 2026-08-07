from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from .memory_sidecar_hint_models import MemoryHints
from .memory_sidecar_models import MemoryCandidate


@dataclass(frozen=True)
class RecallLimits:
    max_items: int = 3
    max_bytes: int = 6000
    timeout_ms: int = 800


@dataclass(frozen=True)
class WriteResult:
    status: str
    memory_id: str | None = None
    error_code: str | None = None


class MemorySidecarProviderError(RuntimeError):
    def __init__(self, code: str, summary: str) -> None:
        self.code = str(code)
        self.summary = str(summary)
        super().__init__(f"{self.code}: {self.summary}")


class MemorySidecarProvider(Protocol):
    def recall(self, *, query: str, query_fingerprint: str, limits: RecallLimits) -> MemoryHints:
        ...

    def write_candidate(self, candidate: MemoryCandidate) -> WriteResult:
        ...


class FakeMemorySidecarProvider:
    def __init__(
        self,
        recall_results: Mapping[str, MemoryHints] | None = None,
        write_results: Mapping[str, WriteResult] | None = None,
        raise_error: MemorySidecarProviderError | None = None,
    ) -> None:
        self.recall_results = dict(recall_results or {})
        self.write_results = dict(write_results or {})
        self.raise_error = raise_error
        self.recall_calls = 0
        self.write_calls = 0
        self.default_limits = RecallLimits()

    def recall(self, *, query: str, query_fingerprint: str, limits: RecallLimits) -> MemoryHints:
        self.recall_calls += 1
        if self.raise_error is not None:
            raise self.raise_error
        return self.recall_results.get(
            query_fingerprint,
            MemoryHints.empty(query_fingerprint=query_fingerprint, status="empty"),
        )

    def write_candidate(self, candidate: MemoryCandidate) -> WriteResult:
        self.write_calls += 1
        return self.write_results.get(candidate.memory_id, WriteResult(status="written", memory_id=candidate.memory_id))

