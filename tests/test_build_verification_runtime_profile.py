from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from backend.services.verification_runtime_profile import VerificationRuntimeProfile
from scripts.build_verification_runtime_profile import (
    VerificationRuntimeProfileBuildError,
    build_verification_profile,
)


def _db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("create table sample (value text)")
        conn.execute("insert into sample values ('source')")


def test_builder_creates_profile_snapshot_and_generation_metadata(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    source_db = project / "nbs_marketing_data.db"
    _db(source_db)
    runtime = project / ".nbs_runtime"
    runtime.mkdir()
    (runtime / "data_generation.json").write_text(json.dumps({
        "generation": 7,
        "operationId": "op-7",
        "status": "accepted",
        "updatedAt": "2026-08-17T10:00:00+08:00",
        "dbSignature": {"sizeBytes": source_db.stat().st_size, "modifiedNs": source_db.stat().st_mtime_ns, "sha256": "a" * 64},
    }), encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "monthly_revenue_baselines.json").write_text("{}", encoding="utf-8")
    output_root = project / ".nbs_agent_runtime" / "verification"
    source_stat = source_db.stat()

    profile_path = build_verification_profile(
        project_root=project,
        source_db=source_db,
        source_runtime=runtime,
        output_root=output_root,
        git_head="a" * 40,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )

    profile = VerificationRuntimeProfile.load(profile_path, expected_git_head="a" * 40)
    assert profile.database.read_only is True
    assert profile.database.snapshot_ref.startswith("verification/")
    assert (profile_path.parent / "generation.json").is_file()
    assert not (profile_path.parent / "cache").exists()
    assert source_db.is_file()
    assert source_db.stat().st_size == source_stat.st_size
    assert source_db.stat().st_mtime_ns == source_stat.st_mtime_ns


def test_builder_rejects_missing_source_before_output_creation(tmp_path: Path) -> None:
    output_root = tmp_path / "verification"
    with pytest.raises(VerificationRuntimeProfileBuildError):
        build_verification_profile(
            project_root=tmp_path,
            source_db=tmp_path / "missing.sqlite",
            source_runtime=tmp_path / "runtime",
            output_root=output_root,
            git_head="a" * 40,
            ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        )
    assert not output_root.exists()


def test_builder_rejects_output_outside_ignored_verification_runtime(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    source_db = project / "nbs_marketing_data.db"
    _db(source_db)
    runtime = project / ".nbs_runtime"
    runtime.mkdir()
    (runtime / "data_generation.json").write_text(json.dumps({
        "generation": 1, "operationId": None, "status": "accepted", "updatedAt": "2026-08-17",
        "dbSignature": {"sizeBytes": source_db.stat().st_size, "modifiedNs": source_db.stat().st_mtime_ns, "sha256": "a" * 64},
    }), encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "monthly_revenue_baselines.json").write_text("{}", encoding="utf-8")
    with pytest.raises(VerificationRuntimeProfileBuildError, match="verification runtime"):
        build_verification_profile(
            project_root=project, source_db=source_db, source_runtime=runtime,
            output_root=tmp_path / "outside", git_head="a" * 40,
            ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        )
