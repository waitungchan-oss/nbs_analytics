from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .evidence_models import canonical_fingerprint
from .memory_hub_catalog import MemoryCatalog, MemoryHubCatalogError
from .memory_hub_models import MemoryHubSchemaError, MemoryQuery, RuntimeIdentity
from .memory_hub_service import MemoryHubService


CatalogProvider = Callable[[], MemoryCatalog | None]


@dataclass(frozen=True)
class MemoryHubUiReadModel:
    status: str
    catalog: dict[str, object]
    records: tuple[dict[str, object], ...]
    decisions: tuple[dict[str, object], ...]
    source: dict[str, object] | None
    diagnostics: tuple[str, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, str) or not self.status:
            raise ValueError("UI status is invalid")
        if not isinstance(self.records, tuple) or len(self.records) > 3:
            raise ValueError("UI records exceed the bounded item cap")
        if not isinstance(self.decisions, tuple) or len(self.decisions) > 16:
            raise ValueError("UI decisions exceed the bounded item cap")
        if not isinstance(self.diagnostics, tuple) or len(self.diagnostics) > 8:
            raise ValueError("UI diagnostics exceed the bounded item cap")
        if not isinstance(self.fingerprint, str) or len(self.fingerprint) != 64:
            raise ValueError("UI fingerprint is invalid")

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schemaVersion": "memory-hub-ui-read-model-v1",
            "status": self.status,
            "catalog": self.catalog,
            "records": list(self.records),
            "decisions": list(self.decisions),
            "source": self.source,
            "diagnostics": list(self.diagnostics),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "fingerprint": self.fingerprint}


def _model(
    status: str,
    *,
    catalog: dict[str, object] | None = None,
    records: tuple[dict[str, object], ...] = (),
    decisions: tuple[dict[str, object], ...] = (),
    source: dict[str, object] | None = None,
    diagnostics: tuple[str, ...] = (),
) -> MemoryHubUiReadModel:
    unsigned = {
        "schemaVersion": "memory-hub-ui-read-model-v1",
        "status": status,
        "catalog": catalog or {},
        "records": list(records),
        "decisions": list(decisions),
        "source": source,
        "diagnostics": list(diagnostics),
    }
    return MemoryHubUiReadModel(status, catalog or {}, records, decisions, source, diagnostics, canonical_fingerprint(unsigned))


def _catalog_metadata(catalog: MemoryCatalog) -> dict[str, object]:
    return {
        "catalogFingerprint": catalog.catalog_fingerprint,
        "builtFromHead": catalog.built_from_head,
        "sourceSetFingerprint": catalog.source_set_fingerprint,
        "policyFingerprint": catalog.policy_fingerprint,
        "sourceCount": len(catalog.sources),
        "recordCount": len(catalog.records),
    }


def _record_rows(records: tuple) -> tuple[dict[str, object], ...]:
    def bounded_summary(value: str) -> str:
        encoded = value.encode("utf-8")
        if len(encoded) <= 512:
            return value
        return encoded[:509].decode("utf-8", errors="ignore") + "..."

    return tuple(
        {
            "memoryId": record.memory_id,
            "memoryKind": record.memory_kind,
            "summary": bounded_summary(record.summary),
            "owner": record.owner,
            "scope": record.scope,
            "freshness": record.freshness,
            "status": record.status,
            "sourceCount": len(record.source_refs),
            "recordFingerprint": record.record_fingerprint,
            "sourceIds": tuple(source.source_id for source in record.source_refs),
        }
        for record in records
    )


class MemoryHubUiService:
    """Read-only UI adapter over an explicitly supplied immutable catalog."""

    def __init__(self, catalog_provider: CatalogProvider | None, *, project_id: str) -> None:
        if catalog_provider is not None and not callable(catalog_provider):
            raise ValueError("catalog provider must be callable")
        self._catalog_provider = catalog_provider
        self._project_id = project_id

    def _catalog(self) -> tuple[MemoryCatalog | None, str | None]:
        if self._catalog_provider is None:
            return None, "catalog_missing"
        try:
            catalog = self._catalog_provider()
        except (MemoryHubCatalogError, MemoryHubSchemaError, OSError, ValueError):
            return None, "invalid_catalog"
        if catalog is None:
            return None, "catalog_missing"
        if not isinstance(catalog, MemoryCatalog):
            return None, "invalid_catalog"
        return catalog, None

    def catalog_status(self) -> MemoryHubUiReadModel:
        catalog, diagnostic = self._catalog()
        if catalog is None:
            return _model(diagnostic or "invalid_catalog", diagnostics=(diagnostic or "invalid_catalog",))
        return _model("ready", catalog=_catalog_metadata(catalog))

    def query(
        self, *, query: str, consumer_id: str, scope: str,
        memory_kinds: tuple[str, ...], team_id: str | None,
    ) -> MemoryHubUiReadModel:
        catalog, diagnostic = self._catalog()
        if catalog is None:
            return _model(diagnostic or "invalid_catalog", diagnostics=(diagnostic or "invalid_catalog",))
        try:
            request = MemoryQuery.from_parts(
                query=query, consumer_id=consumer_id, scope=scope,
                memory_kinds=memory_kinds, max_items=3, max_bytes=6000, timeout_ms=800,
            )
            identity = RuntimeIdentity.from_parts(project_id=self._project_id, consumer_id=consumer_id, team_id=team_id)
            result = MemoryHubService(catalog, project_id=self._project_id).query(request, identity)
        except (MemoryHubSchemaError, ValueError):
            return _model("blocked", catalog=_catalog_metadata(catalog), diagnostics=("query_invalid",))
        return _model(
            result.status,
            catalog=_catalog_metadata(catalog),
            records=_record_rows(result.records),
            decisions=tuple(item.to_dict() for item in result.acl_decisions),
            diagnostics=() if result.status in {"ready", "empty"} else (result.status,),
        )

    def resolve_source(self, source_id: str, *, consumer_id: str, team_id: str | None) -> MemoryHubUiReadModel:
        catalog, diagnostic = self._catalog()
        if catalog is None:
            return _model(diagnostic or "invalid_catalog", diagnostics=(diagnostic or "invalid_catalog",))
        try:
            identity = RuntimeIdentity.from_parts(project_id=self._project_id, consumer_id=consumer_id, team_id=team_id)
            resolution = MemoryHubService(catalog, project_id=self._project_id).resolve_source(source_id, identity)
        except (MemoryHubSchemaError, ValueError):
            return _model("blocked", catalog=_catalog_metadata(catalog), diagnostics=("blocked_identity",))
        source = None
        source_record = next((item for item in catalog.sources if item.source_id == source_id), None)
        if resolution.status == "ready":
            if source_record is None:
                return _model("invalid_catalog", catalog=_catalog_metadata(catalog), diagnostics=("source_unavailable",))
            source = {
                "sourceId": resolution.source_id,
                "sourceKind": source_record.source_kind,
                "artifactRef": resolution.artifact_ref,
                "artifactSha256": resolution.artifact_sha256,
                "runId": source_record.run_id,
                "gitHead": source_record.git_head,
                "scope": resolution.scope,
                "owner": source_record.owner,
                "generatedAt": source_record.generated_at,
                "expiresAt": source_record.expires_at,
                "policyVersion": source_record.policy_version,
                "sourceFingerprint": source_record.source_fingerprint,
                "status": resolution.status,
            }
        if resolution.status == "blocked":
            reason = "scope_mismatch"
        elif source_record is None:
            reason = "missing_source"
        elif source_record.status != "verified":
            reason = "stale"
        else:
            reason = "source_unavailable"
        return _model(
            resolution.status,
            catalog=_catalog_metadata(catalog),
            source=source,
            diagnostics=() if resolution.status == "ready" else (reason,),
        )
