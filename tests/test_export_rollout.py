import os


def test_resolve_export_mode_is_allowlisted_and_defaults_to_shadow(monkeypatch):
    from app_workflows import resolve_export_mode

    monkeypatch.delenv("NBS_EXPORT_FAST_PATH_MODE", raising=False)
    assert resolve_export_mode() == "shadow"
    monkeypatch.setenv("NBS_EXPORT_FAST_PATH_MODE", "invalid")
    assert resolve_export_mode() == "shadow"
    monkeypatch.setenv("NBS_EXPORT_FAST_PATH_MODE", "opt_in")
    assert resolve_export_mode() == "opt_in"


def test_fast_publication_requires_equivalence_and_baseline_pass():
    from app_workflows import should_publish_fast_export

    assert not should_publish_fast_export(mode="shadow", equivalence_status="PASS", baseline_status="PASS")
    assert not should_publish_fast_export(mode="opt_in", equivalence_status="FAIL", baseline_status="PASS")
    assert not should_publish_fast_export(mode="default", equivalence_status="PASS", baseline_status="DRIFT")
    assert should_publish_fast_export(mode="opt_in", equivalence_status="PASS", baseline_status="PASS")


def test_export_ui_surfaces_bounded_fallback_and_zip_status():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app_pages.py").read_text(encoding="utf-8")
    assert "高速匯出驗證失敗，已使用相容匯出路徑" in source
    assert "export_fast_status" in source
