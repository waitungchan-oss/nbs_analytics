from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
E4_FILES = [
    ROOT / "backend/agents/governance_graph_management_summary_models.py",
    ROOT / "backend/agents/governance_graph_management_summary_adapters.py",
    ROOT / "backend/agents/governance_graph_management_summary_service.py",
    ROOT / "backend/agents/governance_graph_management_summary_export.py",
]


def test_e4_modules_do_not_import_write_or_decision_paths():
    forbidden = ("subprocess", "sqlite3", "decision_service", "target_governance", "forecast", "attainment")
    for path in E4_FILES:
        text = path.read_text(encoding="utf-8").lower()
        assert not any(token in text for token in forbidden), path


def test_e4_modules_are_present_and_bounded_to_expected_files():
    assert all(path.is_file() for path in E4_FILES)
    assert not (ROOT / "backend/agents/governance_graph_management_summary_writer.py").exists()

