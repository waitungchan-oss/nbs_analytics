from __future__ import annotations


def test_memory_hub_tab_uses_explicit_missing_catalog_and_read_only_callbacks(monkeypatch):
    import app_pages

    captured = {}

    class FakeService:
        def __init__(self, provider, *, project_id):
            captured["provider"] = provider
            captured["project_id"] = project_id

        def catalog_status(self):
            return "status-model"

        def query(self, **kwargs):
            return "query-result"

        def resolve_source(self, **kwargs):
            return "source-result"

    monkeypatch.setattr(app_pages, "MemoryHubUiService", FakeService)
    monkeypatch.setattr(
        app_pages,
        "render_memory_hub",
        lambda model, *, query_callback, source_callback: captured.update(
            model=model, query=query_callback, source=source_callback
        ),
    )

    app_pages._render_memory_hub_tab()

    assert captured["provider"] is None
    assert captured["project_id"] == "nbs_analytics"
    assert captured["model"] == "status-model"
    assert captured["query"] is not None
    assert captured["source"] is not None
