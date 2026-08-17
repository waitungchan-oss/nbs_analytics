from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_phase2j_profile_mode_passes_snapshot_db(monkeypatch, tmp_path: Path) -> None:
    import scripts.phase2j_baseline_check as phase2j
    paths = SimpleNamespace(db_path=tmp_path / "snapshot.sqlite")
    monkeypatch.setattr(phase2j, "load_verification_runtime_profile", lambda *args, **kwargs: (SimpleNamespace(), paths))
    captured = {}
    monkeypatch.setattr(phase2j, "build_dashboard_summary", lambda filters, **kwargs: captured.update(kwargs) or {"stabilityBaseline": {"status": "matched", "baselineMonth": "2026-05", "formattedExpectedTotal": "HKD 12,057,968", "formattedActualTotal": "HKD 12,057,968", "deltaAmount": 0, "coreValidation": {"checks": []}}, "dataFreshness": {"maxDate": "2026-08-17"}})
    assert phase2j.main(["--verification-profile", "profile.json"]) == 0
    assert captured["db_path"] == paths.db_path


def test_monthly_profile_mode_is_explicit_and_avoids_primary_history(monkeypatch, tmp_path: Path) -> None:
    import scripts.monthly_baseline_check as monthly
    paths = SimpleNamespace(db_path=tmp_path / "snapshot.sqlite")
    monkeypatch.setattr(monthly, "load_verification_runtime_profile", lambda *args, **kwargs: (SimpleNamespace(), paths))
    captured = {}
    monkeypatch.setattr(monthly, "build_monthly_baseline_report", lambda **kwargs: captured.update(kwargs) or {"blockingStatus": "matched"})
    assert monthly.main(["--verification-profile", "profile.json"]) == 0
    assert captured == {"db_path": paths.db_path, "verification_mode": True}
