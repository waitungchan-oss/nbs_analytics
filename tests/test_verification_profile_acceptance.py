from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.services.verification_profile_acceptance import (
    ServiceIdentityEvidence,
    accept_profile_file,
    accept_verification_profile,
    gather_service_identity,
    handoff_evidence,
)
from backend.services.verification_runtime_paths import VerificationRuntimePaths
from backend.services.verification_runtime_profile import VERIFICATION_PROFILE_SCHEMA, VerificationRuntimeProfile
from scripts.build_verification_runtime_profile import build_verification_profile


def _db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("create table sample (value text)")
        conn.execute("insert into sample values ('source')")


def _generation_payload(source_db: Path) -> dict:
    return {
        "generation": 7,
        "operationId": "op-7",
        "status": "accepted",
        "updatedAt": "2026-08-17T10:00:00+08:00",
        "dbSignature": {"sizeBytes": source_db.stat().st_size, "modifiedNs": source_db.stat().st_mtime_ns, "sha256": "a" * 64},
    }


def _git_repo(project: Path) -> str:
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(project), "-c", "user.email=agent@example.com", "-c", "user.name=agent", "commit", "--allow-empty", "-m", "init", "-q"],
        check=True,
    )
    return subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def _build_ready_profile(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    project.mkdir()
    head = _git_repo(project)
    source_db = project / "nbs_marketing_data.db"
    _db(source_db)
    runtime = project / ".nbs_runtime"
    runtime.mkdir()
    (runtime / "data_generation.json").write_text(json.dumps(_generation_payload(source_db)), encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "monthly_revenue_baselines.json").write_text("{}", encoding="utf-8")
    profile_path = build_verification_profile(
        project_root=project,
        source_db=source_db,
        source_runtime=runtime,
        output_root=project / ".nbs_agent_runtime" / "verification",
        git_head=head,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )
    return project, head, profile_path


def _load(project: Path, profile_path: Path, expected_git_head: str):
    from backend.services.verification_runtime_paths import load_verification_runtime_profile

    return load_verification_runtime_profile(profile_path, project_root=project, expected_git_head=expected_git_head)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()


def test_acceptance_ready_when_all_bindings_match(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(
        profile, paths, project_root=project,
        expected_git_head=head, service_identity=ServiceIdentityEvidence(True),
    )
    assert result.ready is True
    assert result.blocked_reasons == ()
    assert result.profile_id == profile.profile_id
    assert result.project_id == "project"
    assert result.git_head == head
    assert result.runtime_dir == f"verification/{profile.profile_id}"


def test_acceptance_auto_detects_git_head_and_worktree(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(profile, paths, project_root=project, service_identity=ServiceIdentityEvidence(True))
    assert result.ready is True


def test_acceptance_blocked_on_project_id_drift(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(
        profile, paths, project_root=project,
        expected_git_head=head, expected_project_id="nbs_analytics",
        service_identity=ServiceIdentityEvidence(True),
    )
    assert result.ready is False
    assert "identity_drift:projectId" in result.blocked_reasons


def test_acceptance_blocked_on_git_head_drift(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(
        profile, paths, project_root=project,
        expected_git_head="b" * 40, service_identity=ServiceIdentityEvidence(True),
    )
    assert result.ready is False
    assert "identity_drift:gitHead" in result.blocked_reasons


def test_acceptance_blocked_on_snapshot_signature_mismatch(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    snapshot = profile_path.parent / "snapshot.sqlite"
    os.chmod(snapshot, 0o644)
    snapshot.write_bytes(snapshot.read_bytes() + b"tamper")
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(
        profile, paths, project_root=project,
        expected_git_head=head, service_identity=ServiceIdentityEvidence(True),
    )
    assert result.ready is False
    assert "signature_mismatch:snapshot" in result.blocked_reasons


def test_acceptance_blocked_on_source_signature_mismatch(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    source_db = project / "nbs_marketing_data.db"
    with sqlite3.connect(source_db) as conn:
        conn.execute("insert into sample values ('drift')")
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(
        profile, paths, project_root=project,
        expected_git_head=head, service_identity=ServiceIdentityEvidence(True),
    )
    assert result.ready is False
    assert "signature_mismatch:source" in result.blocked_reasons


def test_acceptance_blocked_on_generation_signature_mismatch(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    generation = profile_path.parent / "generation.json"
    payload = json.loads(generation.read_text(encoding="utf-8"))
    payload["status"] = "changed"
    generation.write_text(json.dumps(payload), encoding="utf-8")
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(
        profile, paths, project_root=project,
        expected_git_head=head, service_identity=ServiceIdentityEvidence(True),
    )
    assert result.ready is False
    assert "signature_mismatch:generation" in result.blocked_reasons


def test_acceptance_blocked_on_stale_profile_age(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload["createdAt"] = "2020-01-01T00:00:00+00:00"
    unsigned = {key: value for key, value in payload.items() if key != "profileFingerprint"}
    payload["profileFingerprint"] = canonical_fingerprint(unsigned)
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(
        profile, paths, project_root=project,
        expected_git_head=head, max_profile_age=timedelta(hours=1),
        service_identity=ServiceIdentityEvidence(True),
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert result.ready is False
    assert "stale_profile:age" in result.blocked_reasons


def test_acceptance_blocked_on_stale_worktree_drift(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    (project / "new-untracked-file.txt").write_text("drift", encoding="utf-8")
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(
        profile, paths, project_root=project,
        expected_git_head=head, service_identity=ServiceIdentityEvidence(True),
    )
    assert result.ready is False
    assert "stale_worktree:worktree_fingerprint_drift" in result.blocked_reasons


def test_acceptance_blocked_on_runtime_path_mismatch(tmp_path: Path) -> None:
    payload = {
        "schemaVersion": VERIFICATION_PROFILE_SCHEMA,
        "profileId": "profile-x",
        "projectId": "project",
        "gitHead": "a" * 40,
        "worktreeFingerprint": canonical_fingerprint({"head": "a" * 40, "status": ""}),
        "database": {
            "snapshotRef": "verification/profile-x/database.sqlite",
            "sourceFingerprint": "b" * 64,
            "snapshotFingerprint": "c" * 64,
            "readOnly": True,
        },
        "baseline": {"registryFingerprint": "d" * 64, "requiredMay2026Total": "HKD 12,057,968"},
        "runtime": {
            "runtimeDir": "verification/profile-x/runtime",
            "generationRef": "verification/profile-x/generation.json",
            "generationFingerprint": "f" * 64,
            "cacheInventory": {"fileCount": 0, "totalBytes": 0, "fingerprint": "e" * 64},
        },
        "services": {"profileNamespace": "profile-x", "ports": {"api": 18601, "streamlit": 18502, "vue": 15173}},
        "createdAt": "2026-08-17T10:00:00+08:00",
        "profileFingerprint": "",
    }
    unsigned = {key: value for key, value in payload.items() if key != "profileFingerprint"}
    payload["profileFingerprint"] = canonical_fingerprint(unsigned)
    profile = VerificationRuntimeProfile.from_dict(payload, expected_git_head="a" * 40)
    paths = VerificationRuntimePaths(
        profile_path=tmp_path / "profile.json",
        db_path=tmp_path / "profile" / "snapshot.sqlite",
        generation_path=tmp_path / "profile" / "generation.json",
        runtime_dir=tmp_path / "profile",
        cache_path=tmp_path / "profile" / "cache",
    )
    result = accept_verification_profile(
        profile, paths, project_root=tmp_path,
        expected_git_head="a" * 40, service_identity=ServiceIdentityEvidence(True),
    )
    assert "runtime_path_mismatch:resolved" in result.blocked_reasons


def test_acceptance_blocked_when_artifact_outside_runtime_dir(tmp_path: Path) -> None:
    payload = {
        "schemaVersion": VERIFICATION_PROFILE_SCHEMA,
        "profileId": "profile-x",
        "projectId": "project",
        "gitHead": "a" * 40,
        "worktreeFingerprint": canonical_fingerprint({"head": "a" * 40, "status": ""}),
        "database": {
            "snapshotRef": "verification/profile-x/database.sqlite",
            "sourceFingerprint": "b" * 64,
            "snapshotFingerprint": "c" * 64,
            "readOnly": True,
        },
        "baseline": {"registryFingerprint": "d" * 64, "requiredMay2026Total": "HKD 12,057,968"},
        "runtime": {
            "runtimeDir": "verification/profile-x",
            "generationRef": "verification/profile-x/generation.json",
            "generationFingerprint": "f" * 64,
            "cacheInventory": {"fileCount": 0, "totalBytes": 0, "fingerprint": "e" * 64},
        },
        "services": {"profileNamespace": "profile-x", "ports": {"api": 18601, "streamlit": 18502, "vue": 15173}},
        "createdAt": "2026-08-17T10:00:00+08:00",
        "profileFingerprint": "",
    }
    unsigned = {key: value for key, value in payload.items() if key != "profileFingerprint"}
    payload["profileFingerprint"] = canonical_fingerprint(unsigned)
    profile = VerificationRuntimeProfile.from_dict(payload, expected_git_head="a" * 40)
    paths = VerificationRuntimePaths(
        profile_path=tmp_path / "profile.json",
        db_path=tmp_path / "outside" / "snapshot.sqlite",
        generation_path=tmp_path / "outside" / "generation.json",
        runtime_dir=tmp_path / "profile",
        cache_path=tmp_path / "profile" / "cache",
    )
    result = accept_verification_profile(
        profile, paths, project_root=tmp_path,
        expected_git_head="a" * 40, service_identity=ServiceIdentityEvidence(True),
    )
    assert "runtime_path_mismatch:artifact_outside_runtime" in result.blocked_reasons


def test_acceptance_blocked_when_service_identity_unavailable(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(profile, paths, project_root=project, expected_git_head=head)
    assert result.ready is False
    assert "service_identity_unavailable" in result.blocked_reasons
    result = accept_verification_profile(
        profile, paths, project_root=project,
        expected_git_head=head, service_identity=ServiceIdentityEvidence(False, "no pid"),
    )
    assert result.ready is False
    assert "service_identity_unavailable" in result.blocked_reasons


def test_acceptance_blocked_on_missing_profile_fields(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    del payload["runtime"]["generationFingerprint"]
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    result = accept_profile_file(profile_path, project_root=project, service_identity=ServiceIdentityEvidence(True))
    assert result.ready is False
    assert any(reason.startswith("profile_invalid:") for reason in result.blocked_reasons)


def test_acceptance_blocked_on_runtime_dir_path_escape(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload["runtime"]["runtimeDir"] = "verification/other-profile"
    unsigned = {key: value for key, value in payload.items() if key != "profileFingerprint"}
    payload["profileFingerprint"] = canonical_fingerprint(unsigned)
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    result = accept_profile_file(profile_path, project_root=project, service_identity=ServiceIdentityEvidence(True))
    assert result.ready is False
    assert any(reason.startswith("profile_invalid:") for reason in result.blocked_reasons)


def test_acceptance_is_read_only(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    profile, paths = _load(project, profile_path, head)
    before_files = sorted(
        (path.relative_to(project).as_posix(), _file_sha256(path))
        for path in project.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )
    before_status = subprocess.run(
        ["git", "-C", str(project), "status", "--porcelain"], capture_output=True, text=True, check=True,
    ).stdout
    result = accept_verification_profile(
        profile, paths, project_root=project,
        expected_git_head=head, service_identity=ServiceIdentityEvidence(True),
    )
    assert result.ready is True
    after_files = sorted(
        (path.relative_to(project).as_posix(), _file_sha256(path))
        for path in project.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )
    after_status = subprocess.run(
        ["git", "-C", str(project), "status", "--porcelain"], capture_output=True, text=True, check=True,
    ).stdout
    assert after_files == before_files
    assert after_status == before_status


def test_review_and_hermes_handoff_are_bounded(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(
        profile, paths, project_root=project,
        expected_git_head=head, service_identity=ServiceIdentityEvidence(True),
    )
    for consumer in ("review", "hermes"):
        evidence = handoff_evidence(result, consumer=consumer)
        assert set(evidence) == {"schemaVersion", "consumer", "status", "identity", "blockedReasons"}
        assert evidence["schemaVersion"] == "verification-profile-acceptance-v1"
        assert evidence["consumer"] == consumer
        assert evidence["status"] == "ready"
        identity = evidence["identity"]
        assert set(identity) == {
            "profileId", "projectId", "gitHead", "snapshotFingerprint",
            "sourceFingerprint", "generationFingerprint", "runtimeDir", "services", "createdAt",
        }
        assert isinstance(evidence["blockedReasons"], list)
        assert all(isinstance(reason, str) for reason in evidence["blockedReasons"])
        assert identity["runtimeDir"].startswith("verification/")
        assert not Path(identity["runtimeDir"]).is_absolute()
        assert set(identity["services"]) == {"api", "streamlit", "vue"}
        serialized = json.dumps(evidence)
        for forbidden in ("transaction", "excel", "secret", "password", "token", "api_key"):
            assert forbidden.lower() not in serialized.lower()


def test_review_and_hermes_handoff_blocked_reasons_bounded(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(profile, paths, project_root=project, expected_git_head=head)
    for consumer in ("review", "hermes"):
        evidence = handoff_evidence(result, consumer=consumer)
        assert evidence["status"] == "blocked"
        assert "service_identity_unavailable" in evidence["blockedReasons"]
        assert all(":" in reason or reason == "service_identity_unavailable" for reason in evidence["blockedReasons"])


def test_handoff_rejects_unknown_consumer(tmp_path: Path) -> None:
    project, head, profile_path = _build_ready_profile(tmp_path)
    profile, paths = _load(project, profile_path, head)
    result = accept_verification_profile(
        profile, paths, project_root=project,
        expected_git_head=head, service_identity=ServiceIdentityEvidence(True),
    )
    with pytest.raises(ValueError, match="consumer"):
        handoff_evidence(result, consumer="other")


def test_gather_service_identity_parses_ready_records(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
            "status": "ready",
            "services": {
                "api": {"identityMatch": True},
                "streamlit": {"identityMatch": True},
                "vue": {"identityMatch": True},
            },
        }), stderr="")

    monkeypatch.setattr("backend.services.verification_profile_acceptance.subprocess.run", fake_run)
    evidence = gather_service_identity(tmp_path, tmp_path / "profile.json")
    assert evidence.available is True
    assert "scripts/system_manager.py" in captured["command"][1]


def test_gather_service_identity_fails_closed_on_not_ready(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
            "status": "not_ready",
            "services": {"api": {"identityMatch": False}},
        }), stderr="")

    monkeypatch.setattr("backend.services.verification_profile_acceptance.subprocess.run", fake_run)
    evidence = gather_service_identity(tmp_path, tmp_path / "profile.json")
    assert evidence.available is False
    assert "not_ready" in evidence.detail


def test_cli_ready_exit_zero_and_blocked_exit_one(tmp_path: Path, capsys, monkeypatch) -> None:
    import scripts.verification_profile_acceptance as cli

    project, head, profile_path = _build_ready_profile(tmp_path)
    monkeypatch.setattr(
        cli.acceptance, "gather_service_identity",
        lambda *args, **kwargs: ServiceIdentityEvidence(True, "service_identity_verified"),
    )
    assert cli.main(["--profile", str(profile_path), "--project-root", str(project), "--expected-git-head", head]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ready"
    assert out["consumer"] == "hermes"
    assert out["blockedReasons"] == []

    monkeypatch.setattr(
        cli.acceptance, "gather_service_identity",
        lambda *args, **kwargs: ServiceIdentityEvidence(False, "service_identity_unavailable:not_ready"),
    )
    assert cli.main(["--profile", str(profile_path), "--project-root", str(project), "--expected-git-head", head]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "blocked"
    assert "service_identity_unavailable" in out["blockedReasons"]


def test_cli_review_consumer_flag(tmp_path: Path, capsys, monkeypatch) -> None:
    import scripts.verification_profile_acceptance as cli

    project, head, profile_path = _build_ready_profile(tmp_path)
    monkeypatch.setattr(
        cli.acceptance, "gather_service_identity",
        lambda *args, **kwargs: ServiceIdentityEvidence(True, "service_identity_verified"),
    )
    assert cli.main(["--profile", str(profile_path), "--project-root", str(project), "--expected-git-head", head, "--consumer", "review"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["consumer"] == "review"
