from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(function_name: str, path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    start = source.index(f"def {function_name}")
    tail = source[start:]
    next_def = tail.find("\ndef ", 4)
    return tail if next_def < 0 else tail[:next_def]


def test_export_status_card_surfaces_stage_timings_and_bounded_states():
    source = _source("_render_export_status_card", ROOT / "streamlit_rendering.py")

    assert "export_fast_timings" in source
    assert "PREPARING" in source
    assert "VERIFYING" in source
    assert "serialization_ms" in source
    assert "package_ms" in source


def test_export_download_path_uses_verified_artifact_without_rebuilding():
    source = (ROOT / "app_pages.py").read_text(encoding="utf-8")

    workflow_source = (ROOT / "app_workflows.py").read_text(encoding="utf-8")
    assert "verify_export_package" in workflow_source
    assert "export_fast_package_path" in source
    assert "export_fast_package_verified" in source
    assert "_compute_export_workbooks" not in _source("_render_ai_and_exports", ROOT / "app_pages.py")
