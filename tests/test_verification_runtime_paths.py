from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.services.verification_runtime_paths import (
    VerificationRuntimePathError,
    load_verification_runtime_profile,
)
from scripts.build_verification_runtime_profile import build_verification_profile


def test_profile_resolves_only_to_its_isolated_artifacts(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    import subprocess
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    db = project / "nbs_marketing_data.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table sample (value text)")
    runtime = project / ".nbs_runtime"
    runtime.mkdir()
    (runtime / "data_generation.json").write_text(json.dumps({
        "generation": 1, "operationId": None, "status": "accepted", "updatedAt": "2026-08-17",
        "dbSignature": {"sizeBytes": db.stat().st_size, "modifiedNs": db.stat().st_mtime_ns, "sha256": "a" * 64},
    }), encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "monthly_revenue_baselines.json").write_text("{}", encoding="utf-8")
    profile_path = build_verification_profile(
        project_root=project, source_db=db, source_runtime=runtime,
        output_root=project / ".nbs_agent_runtime" / "verification", git_head="a" * 40,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )
    profile, paths = load_verification_runtime_profile(profile_path, project_root=project, expected_git_head="a" * 40)
    assert paths.db_path.name == "snapshot.sqlite"
    assert paths.generation_path.name == "generation.json"
    assert paths.runtime_dir == profile_path.parent
    assert paths.db_path != db


def test_profile_path_rejects_missing_artifacts(tmp_path: Path) -> None:
    with pytest.raises(VerificationRuntimePathError, match="missing"):
        from backend.services.verification_runtime_profile import VerificationRuntimeProfile
        VerificationRuntimeProfile  # keep import explicit for contract readability
        load_verification_runtime_profile(tmp_path / "missing.json", project_root=tmp_path)


def test_nested_profile_refs_resolve_without_basename_substitution(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    import subprocess
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    db = project / "nbs_marketing_data.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table sample (value text)")
    runtime = project / ".nbs_runtime"
    runtime.mkdir()
    (runtime / "data_generation.json").write_text(json.dumps({
        "generation": 1, "operationId": None, "status": "accepted", "updatedAt": "2026-08-17",
        "dbSignature": {"sizeBytes": db.stat().st_size, "modifiedNs": db.stat().st_mtime_ns, "sha256": "a" * 64},
    }), encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "monthly_revenue_baselines.json").write_text("{}", encoding="utf-8")
    profile_path = build_verification_profile(
        project_root=project, source_db=db, source_runtime=runtime,
        output_root=project / ".nbs_agent_runtime" / "verification", git_head="a" * 40,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profile_id = payload["profileId"]
    nested = profile_path.parent / "nested"
    nested.mkdir()
    (nested / "snapshot.sqlite").write_bytes((profile_path.parent / "snapshot.sqlite").read_bytes())
    (nested / "generation.json").write_bytes((profile_path.parent / "generation.json").read_bytes())
    payload["database"]["snapshotRef"] = f"verification/{profile_id}/nested/snapshot.sqlite"
    payload["runtime"]["generationRef"] = f"verification/{profile_id}/nested/generation.json"
    unsigned = {key: value for key, value in payload.items() if key != "profileFingerprint"}
    from backend.agents.evidence_models import canonical_fingerprint
    payload["profileFingerprint"] = canonical_fingerprint(unsigned)
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    _, paths = load_verification_runtime_profile(profile_path, project_root=project, expected_git_head="a" * 40)
    assert paths.db_path == nested / "snapshot.sqlite"
    assert paths.generation_path == nested / "generation.json"


def test_symlinked_verification_root_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    import subprocess
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    db = project / "nbs_marketing_data.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table sample (value text)")
    runtime = project / ".nbs_runtime"
    runtime.mkdir()
    (runtime / "data_generation.json").write_text(json.dumps({
        "generation": 1, "operationId": None, "status": "accepted", "updatedAt": "2026-08-17",
        "dbSignature": {"sizeBytes": db.stat().st_size, "modifiedNs": db.stat().st_mtime_ns, "sha256": "a" * 64},
    }), encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "monthly_revenue_baselines.json").write_text("{}", encoding="utf-8")
    profile_path = build_verification_profile(
        project_root=project, source_db=db, source_runtime=runtime,
        output_root=project / ".nbs_agent_runtime" / "verification", git_head="a" * 40,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )
    verification_root = project / ".nbs_agent_runtime" / "verification"
    real_root = project / ".nbs_agent_runtime" / "verification-real"
    verification_root.rename(real_root)
    verification_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(VerificationRuntimePathError, match="symlink"):
        load_verification_runtime_profile(profile_path, project_root=project, expected_git_head="a" * 40)


def test_nested_artifact_symlink_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    import subprocess
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    db = project / "nbs_marketing_data.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table sample (value text)")
    runtime = project / ".nbs_runtime"
    runtime.mkdir()
    (runtime / "data_generation.json").write_text(json.dumps({
        "generation": 1, "operationId": None, "status": "accepted", "updatedAt": "2026-08-17",
        "dbSignature": {"sizeBytes": db.stat().st_size, "modifiedNs": db.stat().st_mtime_ns, "sha256": "a" * 64},
    }), encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "monthly_revenue_baselines.json").write_text("{}", encoding="utf-8")
    profile_path = build_verification_profile(
        project_root=project, source_db=db, source_runtime=runtime,
        output_root=project / ".nbs_agent_runtime" / "verification", git_head="a" * 40,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )
    profile_dir = profile_path.parent
    nested = profile_dir / "nested"
    nested.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "snapshot.sqlite").write_bytes((profile_dir / "snapshot.sqlite").read_bytes())
    (outside / "generation.json").write_bytes((profile_dir / "generation.json").read_bytes())
    (nested / "snapshot.sqlite").symlink_to(outside / "snapshot.sqlite")
    (nested / "generation.json").symlink_to(outside / "generation.json")
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profile_id = payload["profileId"]
    payload["database"]["snapshotRef"] = f"verification/{profile_id}/nested/snapshot.sqlite"
    payload["runtime"]["generationRef"] = f"verification/{profile_id}/nested/generation.json"
    from backend.agents.evidence_models import canonical_fingerprint
    unsigned = {key: value for key, value in payload.items() if key != "profileFingerprint"}
    payload["profileFingerprint"] = canonical_fingerprint(unsigned)
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(VerificationRuntimePathError, match="symlink"):
        load_verification_runtime_profile(profile_path, project_root=project, expected_git_head="a" * 40)


def test_cache_internal_symlink_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    import subprocess
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    db = project / "nbs_marketing_data.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table sample (value text)")
    runtime = project / ".nbs_runtime"
    runtime.mkdir()
    (runtime / "data_generation.json").write_text(json.dumps({
        "generation": 1, "operationId": None, "status": "accepted", "updatedAt": "2026-08-17",
        "dbSignature": {"sizeBytes": db.stat().st_size, "modifiedNs": db.stat().st_mtime_ns, "sha256": "a" * 64},
    }), encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "monthly_revenue_baselines.json").write_text("{}", encoding="utf-8")
    profile_path = build_verification_profile(
        project_root=project, source_db=db, source_runtime=runtime,
        output_root=project / ".nbs_agent_runtime" / "verification", git_head="a" * 40,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )
    cache = profile_path.parent / "cache"
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    (cache / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(VerificationRuntimePathError, match="symlink"):
        load_verification_runtime_profile(profile_path, project_root=project, expected_git_head="a" * 40)


def test_health_profile_mode_passes_all_explicit_paths(monkeypatch, tmp_path: Path) -> None:
    import backend.routers.health as health
    from types import SimpleNamespace
    paths = SimpleNamespace(db_path=tmp_path / "snapshot.sqlite", cache_path=tmp_path / "cache", runtime_dir=tmp_path, generation_path=tmp_path / "generation.json", profile_path=tmp_path / "profile.json")
    profile = SimpleNamespace(profile_id="profile-test")
    captured = {}
    monkeypatch.setattr(health, "load_verification_runtime_profile", lambda *args, **kwargs: (profile, paths))
    monkeypatch.setattr(health, "build_system_health", lambda **kwargs: captured.update(kwargs) or {"status": "degraded"})
    result = health.health_check(verification_profile="profile.json")
    assert result["verificationProfile"]["profileId"] == "profile-test"
    assert captured == {"db_path": paths.db_path, "cache_path": paths.cache_path, "runtime_dir": paths.runtime_dir, "generation_path": paths.generation_path, "read_only": True}


def test_health_profile_mode_skips_mutating_stability_history(monkeypatch, tmp_path: Path) -> None:
    import backend.services.system_health_service as service
    db = tmp_path / "snapshot.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("create table sample (value text)")
    generation = tmp_path / "generation.json"
    generation.write_text(json.dumps({"generation": 0}), encoding="utf-8")
    monkeypatch.setattr(service, "list_stability_history", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not read mutating history")))
    result = service.build_system_health(
        db_path=db,
        cache_path=tmp_path / "cache",
        runtime_dir=tmp_path,
        generation_path=generation,
        read_only=True,
    )
    assert result["latestAcceptance"] is None


def test_health_without_profile_keeps_primary_defaults(monkeypatch, tmp_path: Path) -> None:
    import backend.routers.health as health
    captured = {}
    monkeypatch.setattr(health, "DB_FILE", str(tmp_path / "primary.sqlite"))
    monkeypatch.setattr(health, "build_system_health", lambda **kwargs: captured.update(kwargs) or {"status": "degraded"})
    health.health_check()
    assert captured["db_path"] == Path(health.DB_FILE)


def test_runtime_dir_subdir_ref_resolves_under_profile_dir(tmp_path: Path) -> None:
    import json
    import sqlite3
    import subprocess
    from pathlib import Path

    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    db = project / "nbs_marketing_data.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table sample (value text)")
    runtime = project / ".nbs_runtime"
    runtime.mkdir()
    (runtime / "data_generation.json").write_text(json.dumps({
        "generation": 1, "operationId": None, "status": "accepted", "updatedAt": "2026-08-17",
        "dbSignature": {"sizeBytes": db.stat().st_size, "modifiedNs": db.stat().st_mtime_ns, "sha256": "a" * 64},
    }), encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "monthly_revenue_baselines.json").write_text("{}", encoding="utf-8")
    profile_path = build_verification_profile(
        project_root=project, source_db=db, source_runtime=runtime,
        output_root=project / ".nbs_agent_runtime" / "verification", git_head="a" * 40,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profile_id = payload["profileId"]
    payload["runtime"]["runtimeDir"] = f"verification/{profile_id}/runtime"
    unsigned = {key: value for key, value in payload.items() if key != "profileFingerprint"}
    from backend.agents.evidence_models import canonical_fingerprint
    payload["profileFingerprint"] = canonical_fingerprint(unsigned)
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    _, paths = load_verification_runtime_profile(profile_path, project_root=project, expected_git_head="a" * 40)
    assert paths.runtime_dir == profile_path.parent / "runtime"
    assert paths.db_path == profile_path.parent / "snapshot.sqlite"
