import os
import json
import subprocess
import sys
import pytest
from pathlib import Path

from backend.services.dashboard_service import build_dashboard_summary
from scripts.prepare_release_gate_fixtures import build_release_gate_fixtures
from scripts.prepare_release_gate_fixtures import main as prepare_fixtures_main


def test_release_fixture_preserves_baseline_and_provisions_gmv_cache(tmp_path, monkeypatch):
    fixture = build_release_gate_fixtures(tmp_path)
    monkeypatch.setenv("NBS_ANALYTICS_DB_FILE", str(fixture.db_path))
    monkeypatch.setenv("NBS_ANALYTICS_CACHE_DIR", str(fixture.cache_dir))
    summary = build_dashboard_summary(
        {"years": [2026], "months": ["2026-05"], "dateRange": ["2026-05-01", "2026-05-31"], "branch": "全部分社", "salesGroup": "全部銷售組"},
        db_path=fixture.db_path,
        read_only=True,
    )
    assert summary["stabilityBaseline"]["formattedActualTotal"] == "HKD 12,057,968"
    assert summary["dataFreshness"]["analysisRows"] == 26640
    assert summary["dataFreshness"]["excludedRows"] == 545
    assert fixture.active_version_id
    assert fixture.cache_dir.is_dir()


def test_release_fixture_covers_hermes_phase2_baseline_dimensions(tmp_path, monkeypatch):
    fixture = build_release_gate_fixtures(tmp_path)
    monkeypatch.setenv("NBS_ANALYTICS_DB_FILE", str(fixture.db_path))
    summary = build_dashboard_summary(
        {"years": [2026], "months": ["2026-05"], "dateRange": ["2026-05-01", "2026-05-31"], "branch": "全部分社", "salesGroup": "全部銷售組"},
        db_path=fixture.db_path,
        read_only=True,
    )
    assert round(summary["revenueTotals"]["branchRevenue"]) == 6_658_144
    assert round(summary["revenueTotals"]["specialistRevenue"]) == 5_399_824
    assert [(row["branch"], round(row["totalRevenue"])) for row in summary["branchRanking"][:5]] == [
        ("17荃灣綠楊坊分社", 1_705_339),
        ("36旺角銀行中心分社", 1_146_543),
        ("19沙田分社", 737_527),
        ("33銅鑼灣分社", 704_358),
        ("27屯門市廣場分社", 673_995),
    ]
    assert [(row["specialist"], round(row["totalRevenue"])) for row in summary["specialistRanking"][:4]] == [
        ("YTLAU 刘元太", 4_421_710),
        ("SOGOR 苏清秩", 444_608),
        ("ELSA 谢玲玲", 329_056),
        ("JIA 江嘉韵", 204_450),
    ]
    assert summary["dataFreshness"]["minDate"] == "2025-01-01"
    assert summary["dataFreshness"]["maxDate"] >= "2026-06-22"


def test_release_fixture_drives_real_gmv_ui_smoke(tmp_path, monkeypatch):
    source_fingerprint = "b" * 64
    fixture = build_release_gate_fixtures(
        tmp_path, profile="ui", source_fingerprint=source_fingerprint,
    )
    output = tmp_path / "ui-evidence.json"
    env = {
        **os.environ,
        "NBS_ANALYTICS_DB_FILE": str(fixture.db_path),
        "NBS_ANALYTICS_CACHE_DIR": str(fixture.cache_dir),
        "NBS_ANALYTICS_COORDINATION_DB": str(tmp_path / "upload_coordination.db"),
    }
    completed = subprocess.run(
        [sys.executable, "scripts/streamlit_ui_smoke.py", "--project-root", str(Path.cwd()), "--route", "http://127.0.0.1:8765/", "--commit-sha", "a" * 40, "--source-fingerprint", source_fingerprint, "--output", str(output), "--timeout", "60"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    evidence = __import__("json").loads(output.read_text(encoding="utf-8"))
    assert evidence["activeVersionId"] == evidence["refreshedVersionId"]
    assert evidence["activeVersionId"] != fixture.active_version_id
    assert evidence["uiSmoke"]["mergeFlow"]["initialVersionId"] == fixture.active_version_id
    assert set(evidence["downloadedArtifacts"]) == {"total.detail", "paid.detail"}
    assert all(size > 0 for size in evidence["downloadedArtifacts"].values())
    assert all(item["validated"] for item in evidence["uiSmoke"]["downloads"].values())


def test_release_fixture_writes_bounded_timing_to_disposable_root(tmp_path):
    output_root = tmp_path / "fixture"
    timing_path = output_root / "fixture-timing.json"

    assert prepare_fixtures_main([
        "--output", str(output_root),
        "--commit-sha", "a" * 40,
        "--source-fingerprint", "b" * 64,
        "--timing-output", str(timing_path),
    ]) == 0

    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    assert set(timing) == {"schemaVersion", "commitSha", "sourceFingerprint", "durationSeconds"}
    assert timing["schemaVersion"] == "acceptance-fixture-timing-v1"
    assert timing["commitSha"] == "a" * 40
    assert timing["sourceFingerprint"] == "b" * 64
    assert timing["durationSeconds"] >= 0


def test_release_fixture_rejects_timing_path_outside_disposable_root(tmp_path):
    with pytest.raises(ValueError, match="timing output"):
        prepare_fixtures_main([
            "--output", str(tmp_path / "fixture"),
            "--timing-output", str(tmp_path / "outside" / "fixture-timing.json"),
        ])


def test_release_fixture_does_not_follow_timing_output_symlink(tmp_path):
    output_root = tmp_path / "fixture"
    output_root.mkdir()
    target = output_root / "real-timing.json"
    target.write_text("original", encoding="utf-8")
    link = output_root / "fixture-timing.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        prepare_fixtures_main([
            "--output", str(output_root),
            "--commit-sha", "a" * 40,
            "--source-fingerprint", "b" * 64,
            "--timing-output", str(link),
        ])
    assert target.read_text(encoding="utf-8") == "original"
