from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_dispatch_documents_codex_owned_checkpoint_and_agent_boundary() -> None:
    source = read("docs/agents/CODEX_AGENT_DISPATCH.md")

    assert "task-checkpoint-evidence-v1" in source
    assert "NBS-Checkpoint-Version: 1" in source
    assert "Implementation Agent" in source and "不得 commit" in source
    assert "one approved Task" in source or "一個 approved Task" in source


def test_hermes_documents_read_only_checkpoint_reporting_and_final_gate_separation() -> None:
    source = read("NBS_HERMES_MONITORING.md")

    assert "task-checkpoint-evidence-v1" in source
    assert "Final-Acceptance: pending" in source
    assert "read-only" in source
    assert "不得 stage、commit、push、merge、revert" in source


def test_handoff_documents_checkpoint_lineage_without_auto_push_or_merge() -> None:
    source = read("NBS_ANALYTICS_HANDOFF.md")

    assert "checkpoint" in source.lower()
    assert "push" in source.lower() and "merge" in source.lower()
    assert "Final-Acceptance" in source
    assert "Governance Graph" in source
    assert "Memory Sidecar" in source
