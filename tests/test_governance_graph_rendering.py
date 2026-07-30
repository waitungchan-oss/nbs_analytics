from __future__ import annotations

import governance_graph_rendering as rendering


class FakeStreamlit:
    def __init__(self):
        self.calls = []
        self.session_state = {}

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "selectbox":
                options = args[1] if len(args) > 1 else kwargs.get("options", [])
                return options[0] if options else None
            if name == "columns":
                return [self for _ in range(args[0] if args else 1)]
            return None
        return record

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _run(graph):
    return {"runId": "run-123", "governanceGraph": graph}


def _graph(artifact="protected-incident.json"):
    return {
        "status": "available",
        "snapshotFingerprint": "a" * 64,
        "overallStatus": "awaiting_authorization",
        "freshness": "fresh",
        "nodes": [{"nodeId": "protected_incident", "status": "available", "reasonCode": None}],
        "blockers": [],
        "diagnostics": [],
        "evidence": [{"nodeId": "protected_incident", "artifact": artifact, "sha256": "b" * 64, "status": "available"}],
    }


def test_canonical_node_selection_builds_exact_e1_request(monkeypatch):
    fake = FakeStreamlit()
    monkeypatch.setattr(rendering, "st", fake)
    requests = []

    def lineage_lookup(request):
        requests.append(request)
        return {"schemaVersion": "governance-graph-evidence-lineage-v1", "status": "available", "evidence": [], "links": [], "diagnostics": []}

    rendering.render_governance_graph_workspace(_run(_graph()), lineage_lookup=lineage_lookup)

    assert requests == [{
        "schemaVersion": "governance-graph-evidence-lineage-input-v1",
        "runId": "run-123",
        "snapshotFingerprint": "a" * 64,
        "source": {"kind": "node", "identity": "protected_incident"},
        "evidence": {"path": "protected-incident.json", "sha256": "b" * 64},
    }]
    assert fake.session_state["AGENT_OPERATIONS_SELECTED_EVIDENCE"]["nodeId"] == "protected_incident"


def test_non_canonical_graph_artifact_is_disabled_without_lookup(monkeypatch):
    fake = FakeStreamlit()
    monkeypatch.setattr(rendering, "st", fake)
    called = []

    rendering.render_governance_graph_workspace(_run(_graph("hermes.json")), lineage_lookup=lambda request: called.append(request))

    assert called == []
    rendered = " ".join(str(args) for _, args, _ in fake.calls)
    assert "canonical" in rendered.lower() or "unavailable" in rendered.lower()


def test_unavailable_derived_callbacks_are_explicit_and_no_raw_leak(monkeypatch):
    fake = FakeStreamlit()
    monkeypatch.setattr(rendering, "st", fake)
    graph = _graph()
    graph["evidence"][0]["artifact"] = "/private/tmp/secret.json"
    graph["diagnostics"] = [{"code": "invalid", "summary": "sk-secretvalue"}]

    rendering.render_governance_graph_workspace(_run(graph))

    rendered = " ".join(str(args) for _, args, _ in fake.calls)
    assert "unavailable" in rendered
    assert "/private/tmp/secret.json" not in rendered
    assert "sk-secretvalue" not in rendered


def test_invalid_lineage_result_is_isolated(monkeypatch):
    fake = FakeStreamlit()
    monkeypatch.setattr(rendering, "st", fake)

    rendering.render_governance_graph_workspace(
        _run(_graph()),
        lineage_lookup=lambda request: {"schemaVersion": "bad", "status": "invalid", "raw": {"secret": "x"}},
    )

    rendered = " ".join(str(args) for _, args, _ in fake.calls)
    assert "invalid" in rendered
    assert '"secret"' not in rendered
