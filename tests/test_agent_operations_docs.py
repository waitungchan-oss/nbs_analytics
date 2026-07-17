from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_architecture_docs_lock_agent_operations_contract():
    architecture = (ROOT / "docs/agents/NBS_AGENT_ARCHITECTURE.md").read_text(encoding="utf-8")

    assert "`agent-operations-snapshot-v1`。它不是第二個 source of truth" in architecture
    assert "使用者只能透過「手動重新整理」更新 session-scoped snapshot；重新整理不清除 dashboard caches" in architecture
    assert "UI 不得批准、執行、停止、刪除或 prune workflow" in architecture
    assert "Token usage 只有在 telemetry supplied 時顯示；未提供時顯示 `未提供`" in architecture


def test_dispatch_docs_lock_agent_operations_contract():
    dispatch = (ROOT / "docs/agents/CODEX_AGENT_DISPATCH.md").read_text(encoding="utf-8")

    assert "Agent Operations 只讀 Phase 1 artifacts，不是第二個 source of truth" in dispatch
    assert "UI 僅支援「手動重新整理」session-scoped snapshot，且不清除 dashboard caches" in dispatch
    assert "不得批准、執行、停止、刪除或 prune workflow" in dispatch
    assert "Token usage 僅在 supplied 時顯示，否則顯示 `未提供`" in dispatch


def test_hermes_docs_lock_agent_operations_monitoring_boundary():
    hermes = (ROOT / "NBS_HERMES_MONITORING.md").read_text(encoding="utf-8")

    assert "`agent-operations-snapshot-v1`；它不是第二個 source of truth" in hermes
    assert "UI 的「手動重新整理」不清除 dashboard caches" in hermes
    assert "不得寫入 UI artifacts、批准、執行、停止、刪除或 prune workflow" in hermes
    assert "也不得操作 retention" in hermes
    assert "Token usage 未提供時顯示" in hermes
    assert "`未提供`" in hermes
