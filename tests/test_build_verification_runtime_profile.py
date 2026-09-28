from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from backend.services.cache_generation_service import GENERATION_SCHEMA_VERSION, GENERATION_SIGNATURE_SCOPE
from backend.services.gmv_refund_models import canonical_payload_sha256
from backend.services.revenue_generation_service import (
    CORE_REVENUE_SIGNATURE_SCHEMA,
    CORE_REVENUE_SOURCE_TABLES,
    CORE_REVENUE_TOKEN_PREFIX,
    REVENUE_SCOPE_CONTRACT_VERSION,
)
from backend.services.revenue_scope_service import REVENUE_SCOPE_LABEL
from backend.services.verification_runtime_profile import VerificationRuntimeProfile
from scripts.build_verification_runtime_profile import (
    VerificationRuntimeProfileBuildError,
    build_verification_profile,
)


def _db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("create table sample (value text)")
        conn.execute("insert into sample values ('source')")


def _core_revenue_signature() -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": CORE_REVENUE_SIGNATURE_SCHEMA,
        "scopeLabel": REVENUE_SCOPE_LABEL,
        "scopeContractVersion": REVENUE_SCOPE_CONTRACT_VERSION,
        "sourceTables": list(CORE_REVENUE_SOURCE_TABLES),
        "rowCounts": {"tour_data": 1, "others_data": 2},
        "rawTour": "a" * 64,
        "rawOthers": "b" * 64,
        "formalTour": "c" * 64,
        "formalOthers": "d" * 64,
    }
    digest = canonical_payload_sha256(payload)
    return {**payload, "sha256": digest, "token": f"{CORE_REVENUE_TOKEN_PREFIX}:{digest}"}


def _generation_v2_payload(source_db: Path) -> dict[str, object]:
    return {
        "schemaVersion": GENERATION_SCHEMA_VERSION,
        "signatureScope": GENERATION_SIGNATURE_SCOPE,
        "generation": 8,
        "operationId": "op-8",
        "status": "accepted",
        "updatedAt": "2026-09-24T10:00:00+08:00",
        "dbSignature": {
            "sizeBytes": source_db.stat().st_size,
            "modifiedNs": source_db.stat().st_mtime_ns,
            "sha256": "e" * 64,
        },
        "coreRevenueSignature": _core_revenue_signature(),
    }


def _generation_profile_project(tmp_path: Path, generation: dict[str, object]) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    source_db = project / "nbs_marketing_data.db"
    _db(source_db)
    runtime = project / ".nbs_runtime"
    runtime.mkdir()
    (runtime / "data_generation.json").write_text(json.dumps(generation), encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "monthly_revenue_baselines.json").write_text("{}", encoding="utf-8")
    return project, source_db, runtime


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
    assert (profile_path.parent / "cache").is_dir()
    assert list((profile_path.parent / "cache").iterdir()) == []
    assert source_db.is_file()
    assert source_db.stat().st_size == source_stat.st_size
    assert source_db.stat().st_mtime_ns == source_stat.st_mtime_ns


def test_builder_preserves_current_generation_v2_signature(tmp_path: Path) -> None:
    project, source_db, runtime = _generation_profile_project(tmp_path, {})
    generation = _generation_v2_payload(source_db)
    (runtime / "data_generation.json").write_text(json.dumps(generation), encoding="utf-8")

    profile = build_verification_profile(
        project_root=project, source_db=source_db, source_runtime=runtime,
        output_root=project / ".nbs_agent_runtime" / "verification", git_head="a" * 40,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )

    copied = json.loads(profile.parent.joinpath("generation.json").read_text(encoding="utf-8"))
    assert copied == generation


@pytest.mark.parametrize("invalid_hash", ["g" * 64, "A" * 64])
def test_builder_rejects_noncanonical_database_signature_hash(tmp_path: Path, invalid_hash: str) -> None:
    project, source_db, runtime = _generation_profile_project(tmp_path, {})
    generation = _generation_v2_payload(source_db)
    generation["dbSignature"]["sha256"] = invalid_hash
    (runtime / "data_generation.json").write_text(json.dumps(generation), encoding="utf-8")

    with pytest.raises(VerificationRuntimeProfileBuildError, match="dbSignature hash"):
        build_verification_profile(
            project_root=project, source_db=source_db, source_runtime=runtime,
            output_root=project / ".nbs_agent_runtime" / "verification", git_head="c" * 40,
            ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        )
    assert not (project / ".nbs_agent_runtime" / "verification" / f"profile-{'c' * 12}").exists()


def test_builder_rejects_unknown_generation_v2_fields(tmp_path: Path) -> None:
    project, source_db, runtime = _generation_profile_project(tmp_path, {})
    generation = _generation_v2_payload(source_db)
    generation["unexpected"] = "must fail closed"
    (runtime / "data_generation.json").write_text(json.dumps(generation), encoding="utf-8")

    with pytest.raises(VerificationRuntimeProfileBuildError, match="generation metadata keys"):
        build_verification_profile(
            project_root=project, source_db=source_db, source_runtime=runtime,
            output_root=project / ".nbs_agent_runtime" / "verification", git_head="b" * 40,
            ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        )
    assert not (project / ".nbs_agent_runtime" / "verification" / f"profile-{'b' * 12}").exists()


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
