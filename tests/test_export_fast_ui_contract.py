from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_export_workflow_exposes_rollout_gate_and_manifest_loader():
    source = (ROOT / "app_workflows.py").read_text(encoding="utf-8")

    assert 'NBS_EXPORT_FAST_PATH_MODE' in source
    assert "build_fast_export_job" in source
    assert "load_ready_export_manifest" in source
    assert "_load_fast_export_artifacts" in source
    assert 'fast_mode in {ExportRolloutMode.OPT_IN.value, ExportRolloutMode.DEFAULT.value}' in source


def test_export_center_has_zip_download_without_rebuild_on_download():
    source = (ROOT / "app_pages.py").read_text(encoding="utf-8")

    assert 'export_fast_package_path' in source
    assert '一鍵下載完整報表包 ZIP' in source
    assert 'fast_package_path.read_bytes()' in source
    assert '不會重新 aggregation' in source
