from datetime import date

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
