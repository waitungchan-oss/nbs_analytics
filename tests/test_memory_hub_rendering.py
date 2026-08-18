from __future__ import annotations

from backend.agents.memory_hub_ui_service import MemoryHubUiReadModel
from memory_hub_rendering import _catalog_status_rows, _record_rows, render_memory_hub


class FakeStreamlit:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.tables: list[object] = []
        self.buttons_with_write_intent: list[str] = []

    def title(self, value):
        self.messages.append(str(value))

    def info(self, value):
        self.messages.append(str(value))

    def warning(self, value):
        self.messages.append(str(value))

    def caption(self, value):
        self.messages.append(str(value))

    def markdown(self, value, **kwargs):
        self.messages.append(str(value))

    def dataframe(self, value, **kwargs):
        self.tables.append(value)

    def text_input(self, label, **kwargs):
        return "governance" if "Query" in label else "review-agent"

    def selectbox(self, label, options, **kwargs):
        return options[0]

    def multiselect(self, label, options, **kwargs):
        return [options[0]]

    def button(self, label, **kwargs):
        return True


def _model(*, status: str, records=(), decisions=(), source=None, diagnostics=()):
    return MemoryHubUiReadModel(
        status=status, catalog={"sourceCount": 1, "recordCount": len(records)},
        records=tuple(records), decisions=tuple(decisions), source=source,
        diagnostics=tuple(diagnostics), fingerprint="a" * 64,
    )


def test_missing_catalog_copy_is_read_only() -> None:
    fake = FakeStreamlit()
    render_memory_hub(_model(status="catalog_missing", diagnostics=("catalog_missing",)), query_callback=None, source_callback=None, st_module=fake)
    assert "尚無已建 Memory Hub catalog；此頁不會自行建立或更新 catalog。" in fake.messages
    assert fake.buttons_with_write_intent == []


def test_ready_rows_hide_absolute_paths_and_raw_content() -> None:
    model = _model(
        status="ready",
        records=({
            "memoryId": "a" * 64, "memoryKind": "governance", "summary": "bounded summary",
            "owner": "governance", "scope": "project", "freshness": "fresh", "status": "ready",
            "sourceCount": 1, "recordFingerprint": "b" * 64, "sourceIds": ("c" * 64,),
        },),
    )
    rows = _record_rows(model)
    assert rows[0]["Memory ID"]
    assert "artifact content" not in str(rows)
    assert all("/Users/" not in str(row) for row in rows)
    assert _catalog_status_rows(model)[0]["Status"] == "ready"


def test_status_mapping_does_not_promote_degraded_to_ready() -> None:
    assert _catalog_status_rows(_model(status="degraded"))[0]["Status"] == "degraded"


def test_rendering_exposes_policy_gate_as_observation_only() -> None:
    fake = FakeStreamlit()
    render_memory_hub(_model(status="catalog_missing", diagnostics=("catalog_missing",)), query_callback=None, source_callback=None, st_module=fake)
    assert any("Policy gate：not_configured" in message for message in fake.messages)
    assert all("dispatch" not in message.lower() or "不提供" in message for message in fake.messages)


def test_query_renders_acl_failure_and_source_drilldown() -> None:
    model = _model(status="ready", records=({
        "memoryId": "a" * 64, "memoryKind": "governance", "summary": "bounded",
        "owner": "governance", "scope": "project", "freshness": "fresh", "status": "ready",
        "sourceCount": 1, "recordFingerprint": "b" * 64, "sourceIds": ("c" * 64,),
    },))
    queried = _model(
        status="blocked",
        decisions=({"decision": "blocked", "reason": "scope_mismatch"},),
        diagnostics=("scope_mismatch",),
    )
    resolved = _model(status="ready", source={"sourceId": "c" * 64, "sourceKind": "governance_document", "artifactRef": "docs/guide.md", "artifactSha256": "d" * 64, "sourceFingerprint": "e" * 64, "status": "ready"})
    fake = FakeStreamlit()
    calls = []
    render_memory_hub(model, query_callback=lambda **kwargs: queried, source_callback=lambda **kwargs: (calls.append(kwargs) or resolved), st_module=fake)
    assert any("scope_mismatch" in message for message in fake.messages)
    assert not calls
    assert any("Decision" in str(table) for table in fake.tables)
    fake = FakeStreamlit()
    render_memory_hub(model, query_callback=lambda **kwargs: model, source_callback=lambda **kwargs: (calls.append(kwargs) or resolved), st_module=fake)
    assert any("3 筆" in message and "6,000 bytes" in message and "800 ms" in message for message in fake.messages)
    assert calls and calls[0]["source_id"] == "c" * 64
