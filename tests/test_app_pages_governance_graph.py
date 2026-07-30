from __future__ import annotations

import json


def test_agent_operations_injects_read_only_lineage_callback(monkeypatch):
    import app_pages

    session = {}
    captured = {}
    lineage_calls = []

    class FakeOperations:
        def __init__(self, root):
            pass

        def build_snapshot(self):
            return {"schemaVersion": "agent-operations-snapshot-v1", "runs": []}

    class FakeQuery:
        def __init__(self, root):
            pass

        def query(self, **kwargs):
            return type("Result", (), {"to_dict": lambda self: {"status": "unavailable"}})()

    class FakeLineage:
        def __init__(self, root):
            pass

        def resolve(self, request):
            lineage_calls.append(request)
            return type("Result", (), {"to_dict": lambda self: {"schemaVersion": "governance-graph-evidence-lineage-v1", "status": "available", "evidence": [], "links": [], "diagnostics": []}})()

    monkeypatch.setattr(app_pages.st, "session_state", session)
    monkeypatch.setattr(app_pages, "AgentOperationsService", FakeOperations)
    monkeypatch.setattr(app_pages, "GovernanceGraphQueryService", FakeQuery)
    monkeypatch.setattr(app_pages, "GovernanceGraphEvidenceLineageService", FakeLineage)
    monkeypatch.setattr(
        app_pages,
        "render_agent_operations",
        lambda snapshot, *, on_refresh, query_graph, lineage_lookup: captured.update(lineage=lineage_lookup, query=query_graph),
    )

    app_pages._render_agent_operations_tab()
    request = {
        "schemaVersion": "governance-graph-evidence-lineage-input-v1",
        "runId": "run-123", "snapshotFingerprint": "a" * 64,
        "source": {"kind": "node", "identity": "protected_incident"},
        "evidence": {"path": "protected-incident.json", "sha256": "b" * 64},
    }
    result = captured["lineage"](request)

    assert result["schemaVersion"] == "governance-graph-evidence-lineage-v1"
    assert len(lineage_calls) == 1
    assert lineage_calls[0].to_dict() == request


def test_lineage_callback_rejects_malformed_request_without_writer(monkeypatch):
    import app_pages

    class FakeLineage:
        def __init__(self, root):
            pass

        def resolve(self, request):
            raise AssertionError("invalid request must not reach service")

    monkeypatch.setattr(app_pages, "GovernanceGraphEvidenceLineageService", FakeLineage)
    callback = lambda request: None
    captured = {}
    monkeypatch.setattr(app_pages.st, "session_state", {})
    monkeypatch.setattr(app_pages, "AgentOperationsService", lambda root: type("S", (), {"build_snapshot": lambda self: {"schemaVersion": "agent-operations-snapshot-v1", "runs": []}})())
    monkeypatch.setattr(app_pages, "GovernanceGraphQueryService", lambda root: type("Q", (), {"query": lambda self, **kwargs: type("R", (), {"to_dict": lambda self: {"status": "unavailable"}})()})())
    monkeypatch.setattr(app_pages, "GovernanceGraphEvidenceLineageService", FakeLineage)
    monkeypatch.setattr(app_pages, "render_agent_operations", lambda snapshot, *, on_refresh, query_graph, lineage_lookup: captured.update(lineage=lineage_lookup))
    app_pages._render_agent_operations_tab()

    try:
        captured["lineage"]({"schemaVersion": "bad"})
    except ValueError:
        pass
    else:
        raise AssertionError("malformed lineage request was accepted")
