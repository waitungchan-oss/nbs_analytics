from datetime import date

import agent_operations_rendering
from agent_operations_rendering import filter_agent_runs, token_usage_label


RUNS = [
    {
        "runId": "new",
        "briefName": "Upload lock",
        "status": "completed",
        "updatedAt": "2026-07-16T10:00:00+08:00",
    },
    {
        "runId": "old",
        "briefName": "Forecast",
        "status": "blocked",
        "updatedAt": "2026-07-01T10:00:00+08:00",
    },
]


def _empty_snapshot() -> dict:
    return {
        "schemaVersion": "agent-operations-snapshot-v1",
        "generatedAt": "2026-07-17T00:00:00+08:00",
        "summary": {
            "runCount": 0,
            "activeCount": 0,
            "awaitingAuthorizationCount": 0,
            "completedCount": 0,
            "changesRequiredCount": 0,
            "blockedCount": 0,
            "failedCount": 0,
        },
        "runs": [],
        "retention": {"status": "unavailable"},
        "diagnostics": [],
    }


class FakeStreamlit:
    def __init__(self, *, button_result=False, calls=None):
        self.button_result = button_result
        self.calls = calls if calls is not None else []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "button":
                return self.button_result
            if name == "selectbox":
                options = args[1] if len(args) > 1 else kwargs.get("options", [])
                return options[0] if options else None
            if name == "multiselect":
                return kwargs.get("default", args[1] if len(args) > 1 else [])
            if name == "date_input":
                return kwargs.get("value")
            if name == "text_input":
                return ""
            if name == "columns":
                return [self for _ in range(args[0] if args else 1)]
            if name == "expander":
                return self
            return None

        return record

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_filter_agent_runs_is_local_and_deterministic():
    result = filter_agent_runs(RUNS, {"completed"}, date(2026, 7, 10), date(2026, 7, 20), "upload")
    assert [item["runId"] for item in result] == ["new"]
    assert RUNS[0]["runId"] == "new"


def test_token_usage_label_never_estimates_missing_usage():
    assert token_usage_label(None) == "未提供"
    assert token_usage_label({"totalTokens": 150}) == "150 tokens"


def test_render_empty_snapshot_and_refresh_callback(monkeypatch):
    import agent_operations_rendering

    calls = []
    fake = FakeStreamlit(button_result=True, calls=calls)
    monkeypatch.setattr(agent_operations_rendering, "st", fake)
    refreshed = []

    agent_operations_rendering.render_agent_operations(
        _empty_snapshot(), on_refresh=lambda: refreshed.append(True)
    )

    assert refreshed == [True]
    assert any(call[0] == "info" and "尚無 Agent" in call[1][0] for call in calls)
    assert not any(call[0] == "json" for call in calls)


def test_render_non_empty_snapshot_covers_main_ui_contract(monkeypatch):
    import agent_operations_rendering

    snapshot = _empty_snapshot()
    snapshot["summary"].update({
        "runCount": 1,
        "activeCount": 0,
        "completedCount": 1,
    })
    snapshot["runs"] = [{
        "runId": "run-1",
        "briefName": "Upload lock",
        "status": "completed",
        "stage": "hermes",
        "updatedAt": "2026-07-16T10:00:00+08:00",
        "durationMs": 1200,
        "stages": {"implementation": {"available": True, "durationMs": 900}},
        "findings": {"count": 1, "highestSeverity": "medium", "items": []},
        "verification": {"status": "pass"},
        "hermes": {"status": "pass"},
        "tokenUsage": {"totalTokens": 150},
    }]
    snapshot["retention"] = {"retainDays": 90, "retainLatestTerminalRuns": 30}
    snapshot["diagnostics"] = [{"code": "invalid_run", "reason": "test"}]

    calls = []
    monkeypatch.setattr(agent_operations_rendering, "st", FakeStreamlit(calls=calls))
    agent_operations_rendering.render_agent_operations(snapshot, on_refresh=lambda: None)

    metric_calls = [call for call in calls if call[0] == "metric"]
    dataframe_calls = [call for call in calls if call[0] == "dataframe"]
    assert len(metric_calls) == 5
    assert len(dataframe_calls) >= 2
    assert any(call[0] == "selectbox" and call[2].get("key") == "AGENT_OPERATIONS_SELECTED_RUN_ID" for call in calls)
    assert any(call[0] == "multiselect" and call[2].get("key") == "AGENT_OPERATIONS_STATUS_FILTER" for call in calls)
    assert any(call[0] == "date_input" and call[2].get("key") == "AGENT_OPERATIONS_DATE_FROM" for call in calls)
    assert any(call[0] == "date_input" and call[2].get("key") == "AGENT_OPERATIONS_DATE_TO" for call in calls)
    assert any(call[0] == "text_input" and call[2].get("key") == "AGENT_OPERATIONS_BRIEF_FILTER" for call in calls)
    assert any(call[0] == "subheader" and call[1][0] == "Selected run" for call in calls)
    assert any(call[0] == "subheader" and call[1][0] == "Retention and diagnostics" for call in calls)
    assert any(call[0] == "warning" and "Diagnostics: 1" in call[1][0] for call in calls)


def test_render_run_details_includes_compact_documentation_status(monkeypatch):
    calls = []

    class FakeStreamlit:
        def __getattr__(self, name):
            def method(*args, **kwargs):
                calls.append((name, args, kwargs))
                return False if name == "button" else None
            return method

    monkeypatch.setattr(agent_operations_rendering, "st", FakeStreamlit())
    agent_operations_rendering._render_run_details({
        "runId": "run-1",
        "briefName": "brief.md",
        "status": "completed",
        "stage": "hermes",
        "updatedAt": "2026-07-18T12:00:00+08:00",
        "stages": {},
        "findings": {},
        "verification": {},
        "hermes": {},
        "tokenUsage": None,
        "documentation": {
            "status": "applied",
            "proposalCount": 2,
            "appliedTargetCount": 1,
            "pendingApprovalCount": 1,
            "updatedAt": "2026-07-18T12:00:00+08:00",
        },
    })

    rendered = " ".join(str(args) for name, args, _ in calls if name in {"caption", "write", "subheader"})
    assert "Documentation" in rendered
    assert "applied" in rendered
    assert "2" in rendered and "1" in rendered


def _render_details_with_graph(graph, monkeypatch):
    calls = []
    if isinstance(graph, dict) and graph.get("status") == "available":
        graph.setdefault("snapshotFingerprint", "a" * 64)
    monkeypatch.setattr(agent_operations_rendering, "st", FakeStreamlit(calls=calls))
    agent_operations_rendering._render_run_details({
        "runId": "graph-ready",
        "briefName": "phase-b.md",
        "status": "completed",
        "stage": "hermes",
        "updatedAt": "2026-07-27T10:00:00+00:00",
        "stages": {},
        "findings": {},
        "verification": {},
        "hermes": {},
        "tokenUsage": None,
        "documentation": {"status": "not_requested"},
        "governanceGraph": graph,
    })
    return calls


def test_render_run_details_includes_compact_governance_graph(monkeypatch):
    calls = _render_details_with_graph({
        "status": "available",
        "overallStatus": "blocked",
        "freshness": "stale",
        "nodeStatuses": [{"nodeId": "hermes", "status": "blocked", "reasonCode": "stale_artifact"}],
        "blockers": [{"code": "stale_artifact", "nodeId": "hermes"}],
        "diagnostics": [],
        "evidence": [{
            "nodeId": "hermes", "artifact": "hermes.json", "sha256": "a" * 64,
            "status": "blocked",
        }],
    }, monkeypatch)

    text = " ".join(
        str(args) for name, args, _ in calls
        if name in {"subheader", "caption", "write", "warning"}
    )
    assert "Governance Graph" in text
    assert "stale" in text and "stale_artifact" in text
    assert any(
        name == "dataframe" and "hermes.json" in str(args)
        for name, args, _ in calls
    )
    assert not any(
        name in {"button", "download_button"} and "Graph" in str(args)
        for name, args, _ in calls
    )


def test_graph_filters_use_read_only_query_callback(monkeypatch):
    calls = []
    query_calls = []
    graph = {
        "status": "available",
        "overallStatus": "not_started",
        "freshness": "fresh",
        "nodes": [{"nodeId": "risk", "status": "not_started", "reasonCode": None}],
        "blockers": [],
        "diagnostics": [],
        "evidence": [],
    }
    query_result = {
        "schemaVersion": "governance-graph-query-v1",
        "status": "available",
        "snapshotIdentity": {"runId": "graph-ready", "graphFingerprint": "a" * 64, "generatedAt": "2026-07-27T10:00:00+00:00", "freshness": "fresh"},
        "queryFingerprint": "b" * 64,
        "matchedNodes": [], "matchedEdges": [], "evidenceRefs": [],
        "unknownCount": 0, "invalidCount": 0, "blockedCount": 0, "diagnostics": [],
    }
    monkeypatch.setattr(agent_operations_rendering, "st", FakeStreamlit(calls=calls))

    def query_graph(run_id, filters):
        query_calls.append((run_id, filters))
        return query_result

    agent_operations_rendering._render_run_details({
        "runId": "graph-ready", "briefName": "phase-b.md", "status": "completed",
        "stage": "hermes", "updatedAt": "2026-07-27T10:00:00+00:00", "stages": {},
        "findings": {}, "verification": {}, "hermes": {}, "tokenUsage": None,
        "documentation": {"status": "not_requested"}, "governanceGraph": graph,
    }, query_graph=query_graph)

    assert query_calls and query_calls[0][0] == "graph-ready"
    assert query_calls[0][1]["nodeType"] is None
    assert any(name == "subheader" and args[0] == "Graph Query" for name, args, _ in calls)
    assert any(name == "selectbox" and args[0] == "Edge type" for name, args, _ in calls)
    assert any(name == "text_input" and args[0] == "Node ID" for name, args, _ in calls)


def test_graph_query_callback_is_not_required_for_existing_snapshot_render(monkeypatch):
    calls = _render_details_with_graph({"status": "available", "overallStatus": "not_started", "freshness": "fresh", "nodes": [], "blockers": [], "diagnostics": [], "evidence": []}, monkeypatch)
    assert any(name == "subheader" and args[0] == "Governance Graph" for name, args, _ in calls)


def _telemetry(status="available"):
    return {
        "schemaVersion": "governance-telemetry-snapshot-v1",
        "status": status,
        "latestRunUpdatedAt": "2026-07-28T09:03:00+08:00",
        "coverage": {"eligibleRunCount": 2, "includedRunCount": 1, "unknownRunCount": 1, "diagnosticCount": 1},
        "cycleTimes": {"implementation": {"status": "available", "averageMs": 1200, "observedCount": 1, "unknownCount": 1}},
        "gateFailures": {"specGate": {"status": "available", "failed": 0, "blocked": 0, "unknownCount": 0}, "planGate": {"status": "partial", "failed": 1, "blocked": 0, "unknownCount": 1}, "taskGate": {"status": "unknown", "failed": 0, "blocked": 0, "unknownCount": 2}},
        "agentActivity": {"lunaRepair": {"status": "available", "total": 2, "observedCount": 1, "unknownCount": 1}, "terraDiagnosis": {"status": "unknown", "observedCount": 0, "unknownCount": 2}},
        "evidenceHealth": {"stale": {"status": "available", "total": 1, "observedCount": 2, "unknownCount": 0}},
        "protectedIncidents": {"status": "unknown", "observedCount": 0, "unknownCount": 2},
        "tokenUsage": {"totalTokens": 150, "runsWithUsage": 1, "runsWithoutUsage": 1},
    }


def test_render_governance_telemetry_available_uses_only_snapshot_payload(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_operations_rendering, "st", FakeStreamlit(calls=calls))
    snapshot = _empty_snapshot()
    snapshot["governanceTelemetry"] = _telemetry()

    agent_operations_rendering.render_agent_operations(snapshot, on_refresh=lambda: None)

    assert any(name == "subheader" and args[0] == "Governance telemetry" for name, args, _ in calls)
    assert any(name == "metric" and args[0] == "Eligible runs" and args[1] == 2 for name, args, _ in calls)
    assert any(name == "dataframe" and "Plan gate" in str(args) for name, args, _ in calls)
    assert any(name == "caption" and "latest run" in str(args).lower() for name, args, _ in calls)


def test_render_governance_telemetry_partial_and_unavailable_are_explicit(monkeypatch):
    for status, expected in (("partial", "partial"), ("unavailable", "尚無可用")):
        calls = []
        monkeypatch.setattr(agent_operations_rendering, "st", FakeStreamlit(calls=calls))
        snapshot = _empty_snapshot()
        snapshot["governanceTelemetry"] = _telemetry(status) if status == "partial" else None
        agent_operations_rendering.render_agent_operations(snapshot, on_refresh=lambda: None)
        rendered = " ".join(str(args) for name, args, _ in calls if name in {"caption", "info", "warning", "subheader"})
        assert expected in rendered


def test_render_run_details_marks_missing_graph_as_unavailable(monkeypatch):
    calls = _render_details_with_graph({"status": "unavailable"}, monkeypatch)

    assert any(
        name == "info" and "尚無已建 Graph snapshot" in args[0]
        for name, args, _ in calls
    )


def test_invalid_graph_state_is_bounded_and_does_not_render_raw_payload(monkeypatch):
    calls = _render_details_with_graph({
        "status": "invalid",
        "unexpected": {"prompt": "secret", "absolutePath": "/private/tmp/x"},
    }, monkeypatch)

    rendered = " ".join(str(args) for _, args, _ in calls)
    assert "invalid" in rendered
    assert "secret" not in rendered and "/private/tmp/x" not in rendered
    assert not any(name == "json" for name, _, _ in calls)


def test_agent_operations_snapshot_is_session_scoped_and_force_refresh_is_manual(monkeypatch):
    import app_pages

    unrelated = {
        "PROCESSED_DATA_CACHE": object(),
        "AI_FORECAST_CACHE": object(),
        "EXPORT_WORKBOOKS": object(),
        "UPLOAD_LAST_RESULT": object(),
    }
    session_state = dict(unrelated)
    build_calls = []

    class FakeService:
        def __init__(self, project_root):
            self.project_root = project_root

        def build_snapshot(self):
            build_calls.append(self.project_root)
            return {"schemaVersion": "agent-operations-snapshot-v1", "build": len(build_calls)}

    monkeypatch.setattr(app_pages.st, "session_state", session_state)
    monkeypatch.setattr(app_pages, "AgentOperationsService", FakeService)

    first = app_pages._load_agent_operations_snapshot()
    second = app_pages._load_agent_operations_snapshot()
    forced = app_pages._load_agent_operations_snapshot(force=True)

    assert first is second
    assert forced["build"] == 2
    assert len(build_calls) == 2
    for key, value in unrelated.items():
        assert session_state[key] is value


def test_agent_operations_refresh_preserves_selection_until_filtered_runs_validate_it(monkeypatch):
    import app_pages

    session_state = {"AGENT_OPERATIONS_SELECTED_RUN_ID": "stale-run"}
    rendered = {}

    class FakeService:
        def __init__(self, project_root):
            self.project_root = project_root

        def build_snapshot(self):
            return {"schemaVersion": "agent-operations-snapshot-v1", "runs": []}

    class FakeQueryService:
        def __init__(self, project_root):
            self.project_root = project_root

        def query(self, **kwargs):
            return type("Result", (), {"to_dict": lambda self: {"status": "unavailable"}})()

    monkeypatch.setattr(app_pages.st, "session_state", session_state)
    monkeypatch.setattr(app_pages, "AgentOperationsService", FakeService)
    monkeypatch.setattr(app_pages, "GovernanceGraphQueryService", FakeQueryService)
    monkeypatch.setattr(
        app_pages,
        "render_agent_operations",
        lambda snapshot, *, on_refresh, query_graph: rendered.update(refresh=on_refresh, query=query_graph),
    )

    app_pages._render_agent_operations_tab()
    rendered["refresh"]()

    assert session_state["AGENT_OPERATIONS_SELECTED_RUN_ID"] == "stale-run"
