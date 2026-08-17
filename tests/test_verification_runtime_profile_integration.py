from pathlib import Path
from types import SimpleNamespace

from scripts import hermes_post_change_check as hermes


def test_profile_plan_routes_identity_and_is_skips_mutating_monitor():
    plan = hermes.build_check_plan(
        include_monitor=True,
        include_tests=False,
        project_root=Path("/project"),
        verification_profile=".nbs_agent_runtime/verification/profile-test/profile.json",
    )
    commands = {step.label: step.command for step in plan}
    assert "--verification-profile" in commands["system-status"]
    assert "--verification-profile" in commands["system-acceptance"]
    assert commands["phase2-baseline"][1] == "scripts/phase2j_baseline_check.py"
    assert "--verification-profile" in commands["monthly-baseline-governance"]
    assert "system-monitor" not in {step.label for step in plan}


def test_profile_validation_failure_is_bounded_blocked(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "backend.services.verification_runtime_paths.load_verification_runtime_profile",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("invalid profile")),
    )
    report = hermes.run_checks(
        include_monitor=False,
        include_tests=False,
        project_root=tmp_path,
        verification_profile="missing.json",
    )
    assert report["overallStatus"] == "fail"
    assert report["verificationProfile"]["status"] == "blocked_runner_capability"
    assert report["results"] == []


def test_valid_profile_emits_bounded_identity(monkeypatch, tmp_path: Path):
    profile = SimpleNamespace(
        profile_id="profile-test",
        project_id="nbs_analytics",
        git_head="a" * 40,
        database=SimpleNamespace(snapshot_fingerprint="b" * 64, source_fingerprint="c" * 64),
    )
    paths = SimpleNamespace(runtime_dir=tmp_path / "profile")
    monkeypatch.setattr(
        "backend.services.verification_runtime_paths.load_verification_runtime_profile",
        lambda *args, **kwargs: (profile, paths),
    )
    monkeypatch.setattr(hermes, "run_step", lambda step, project_root: {"label": step.label, "exitCode": 0, "stdout": "", "stderr": "", "required": step.required})
    report = hermes.run_checks(include_monitor=False, include_tests=False, project_root=tmp_path, verification_profile="profile.json")
    assert report["overallStatus"] == "pass"
    assert report["verificationProfile"]["profileId"] == "profile-test"
    assert report["verificationProfile"]["snapshotFingerprint"] == "b" * 64
