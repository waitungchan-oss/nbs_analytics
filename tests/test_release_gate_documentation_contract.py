from pathlib import Path

from backend.agents.release_gate_models import GATES


ROOT = Path(__file__).parents[1]


def test_handoff_documents_release_gate_independence_and_formal_scope():
    source = (ROOT / "NBS_ANALYTICS_HANDOFF.md").read_text(encoding="utf-8")
    for phrase in ("Full pytest release gate", "Hermes release gate", "UI acceptance release gate", "same commit", "fresh", "BLOCKED", "HKD 12,057,968", "不含掛賬核銷與 TT 退款轉團款"):
        assert phrase in source


def test_hermes_and_dispatch_preserve_read_only_authority_boundaries():
    hermes = (ROOT / "NBS_HERMES_MONITORING.md").read_text(encoding="utf-8")
    dispatch = (ROOT / "docs/agents/CODEX_AGENT_DISPATCH.md").read_text(encoding="utf-8")
    for source in (hermes, dispatch):
        assert "release gate" in source.lower()
        assert "read-only" in source
        assert "Final-Acceptance: pending" in source
        assert "full pytest" in source.lower()
        assert "UI acceptance" in source
    assert all(token in hermes for token in ("FAIL", "BLOCKED", "MISSING", "stale", "阻擋 release"))
    assert "aggregate" in dispatch.lower()


def test_documentation_v2_hermes_report_is_not_a_release_gate():
    dispatch = (ROOT / "docs/agents/CODEX_AGENT_DISPATCH.md").read_text(encoding="utf-8")

    assert GATES == ("full_pytest", "hermes", "ui_acceptance")
    assert "documentation-hermes-report-v1" in dispatch
    assert "不屬於正式 release gate" in dispatch
