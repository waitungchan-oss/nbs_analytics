from __future__ import annotations

import hashlib
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
from backend.agents.evidence_models import canonical_fingerprint
from scripts.build_verification_runtime_profile import (
    VerificationRuntimeProfileBuildError,
    _worktree_fingerprint,
    build_verification_profile,
)


def _db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("create table sample (value text)")
        conn.execute("insert into sample values ('source')")


def _init_repo(project: Path) -> str:
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(project), "-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture", "commit", "--allow-empty", "-m", "fixture", "-q"],
        check=True,
    )
    return subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
            "sha256": _sha256(source_db),
        },
        "coreRevenueSignature": _core_revenue_signature(),
    }


def _generation_profile_project(tmp_path: Path, generation: dict[str, object]) -> tuple[Path, Path, Path]:
    primary = tmp_path / "primary" / "nbs_analytics"
    primary.mkdir(parents=True)
    subprocess.run(["git", "-C", str(primary), "init", "-q"], check=True)
    (primary / ".gitignore").write_text("nbs_marketing_data.db\n.nbs_runtime/\n.nbs_agent_runtime/\n", encoding="utf-8")
    (primary / "data").mkdir()
    (primary / "data" / "monthly_revenue_baselines.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(primary), "add", ".gitignore", "data/monthly_revenue_baselines.json"], check=True)
    subprocess.run(
        ["git", "-C", str(primary), "-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture", "commit", "-m", "fixture", "-q"],
        check=True,
    )
    project = tmp_path / "worktrees" / "nbs_analytics"
    project.parent.mkdir()
    subprocess.run(["git", "-C", str(primary), "worktree", "add", "--quiet", "--detach", str(project), "HEAD"], check=True)
    source_db = primary / "nbs_marketing_data.db"
    _db(source_db)
    runtime = primary / ".nbs_runtime"
    runtime.mkdir()
    (runtime / "data_generation.json").write_text(json.dumps(generation), encoding="utf-8")
    return project, source_db, runtime


def test_builder_creates_profile_snapshot_and_generation_metadata(tmp_path: Path) -> None:
    project, source_db, runtime = _generation_profile_project(tmp_path, {})
    assert source_db.parent != project
    assert not (project / "nbs_marketing_data.db").exists()
    assert not (project / ".nbs_runtime").exists()
    assert (source_db.parent / ".nbs_runtime" / "data_generation.json").is_file()
    git_head = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    (runtime / "data_generation.json").write_text(json.dumps({
        "generation": 7,
        "operationId": "op-7",
        "status": "accepted",
        "updatedAt": "2026-08-17T10:00:00+08:00",
        "dbSignature": {"sizeBytes": source_db.stat().st_size, "modifiedNs": source_db.stat().st_mtime_ns, "sha256": _sha256(source_db)},
    }), encoding="utf-8")
    output_root = project / ".nbs_agent_runtime" / "verification"
    source_stat = source_db.stat()

    profile_path = build_verification_profile(
        project_root=project,
        source_root=source_db.parent,
        output_root=output_root,
        git_head=git_head,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )

    profile = VerificationRuntimeProfile.load(profile_path, expected_git_head=git_head)
    assert json.loads(profile_path.read_text(encoding="utf-8"))["schemaVersion"] == "verification-runtime-profile-v2"
    assert profile.database.read_only is True
    assert profile.database.snapshot_ref.startswith("verification/")
    assert (profile_path.parent / "generation.json").is_file()
    assert (profile_path.parent / "cache").is_dir()
    assert list((profile_path.parent / "cache").iterdir()) == []
    assert source_db.is_file()
    assert source_db.stat().st_size == source_stat.st_size
    assert source_db.stat().st_mtime_ns == source_stat.st_mtime_ns


def test_builder_worktree_fingerprint_ignores_summary_only_changes(tmp_path: Path) -> None:
    project = tmp_path / "nbs_analytics"
    project.mkdir()
    git_head = _init_repo(project)
    summary = project / "Summay" / "fixture.md"
    summary.parent.mkdir()
    summary.write_text("fixture one\n", encoding="utf-8")

    first = _worktree_fingerprint(project, git_head)
    summary.write_text("fixture two\n", encoding="utf-8")
    second = _worktree_fingerprint(project, git_head)

    assert first == second
    (project / "ordinary.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert _worktree_fingerprint(project, git_head) != second


def test_builder_worktree_fingerprint_uses_nul_delimited_all_untracked_status(tmp_path: Path) -> None:
    project = tmp_path / "nbs_analytics"
    project.mkdir()
    git_head = _init_repo(project)
    (project / "odd\nname.py").write_text("VALUE = 1\n", encoding="utf-8")
    status = subprocess.run(
        [
            "git", "-C", str(project), "status", "--porcelain=v1",
            "--untracked-files=all", "--no-renames", "-z", "--", ".",
            ":(exclude,icase,glob)**/Summay/**",
            ":(exclude,icase,glob)**/Summay",
            ":(exclude).nbs_agent_runtime",
        ],
        capture_output=True,
        check=True,
    ).stdout
    expected = canonical_fingerprint({
        "head": git_head,
        "statusFingerprint": hashlib.sha256(status).hexdigest(),
    })

    assert _worktree_fingerprint(project, git_head) == expected


def test_builder_preserves_current_generation_v2_signature(tmp_path: Path) -> None:
    project, source_db, runtime = _generation_profile_project(tmp_path, {})
    generation = _generation_v2_payload(source_db)
    (runtime / "data_generation.json").write_text(json.dumps(generation), encoding="utf-8")

    profile = build_verification_profile(
        project_root=project, source_root=source_db.parent,
        output_root=project / ".nbs_agent_runtime" / "verification", git_head=subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip(),
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )

    copied = json.loads(profile.parent.joinpath("generation.json").read_text(encoding="utf-8"))
    assert copied == generation


def test_builder_rechecks_source_after_derived_inventory_before_profile_write(tmp_path: Path, monkeypatch) -> None:
    from scripts import build_verification_runtime_profile as builder

    project, source_db, runtime = _generation_profile_project(tmp_path, {})
    generation = _generation_v2_payload(source_db)
    (runtime / "data_generation.json").write_text(json.dumps(generation), encoding="utf-8")
    git_head = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    baseline = project / "data" / "monthly_revenue_baselines.json"
    original_worktree_fingerprint = builder._worktree_fingerprint

    def mutate_baseline_after_inventory(*args, **kwargs):
        fingerprint = original_worktree_fingerprint(*args, **kwargs)
        baseline.write_text('{"drift": true}\n', encoding="utf-8")
        source_db.parent.joinpath("data", "monthly_revenue_baselines.json").write_text(
            '{"drift": true}\n', encoding="utf-8",
        )
        return fingerprint

    monkeypatch.setattr(builder, "_worktree_fingerprint", mutate_baseline_after_inventory)

    with pytest.raises(VerificationRuntimeProfileBuildError, match="source changed during profile build"):
        build_verification_profile(
            project_root=project,
            source_root=source_db.parent,
            output_root=project / ".nbs_agent_runtime" / "verification",
            git_head=git_head,
            ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        )

    assert not (project / ".nbs_agent_runtime" / "verification" / f"profile-{git_head[:12]}").exists()


def test_builder_rechecks_source_after_profile_publication_before_return(tmp_path: Path, monkeypatch) -> None:
    project, source_db, runtime = _generation_profile_project(tmp_path, {})
    generation = _generation_v2_payload(source_db)
    (runtime / "data_generation.json").write_text(json.dumps(generation), encoding="utf-8")
    git_head = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    source_baseline = source_db.parent / "data" / "monthly_revenue_baselines.json"
    worktree_baseline = project / "data" / "monthly_revenue_baselines.json"
    profile_dir = project / ".nbs_agent_runtime" / "verification" / f"profile-{git_head[:12]}"
    original_write_text = Path.write_text

    def mutate_source_after_profile_write(path, data, *args, **kwargs):
        result = original_write_text(path, data, *args, **kwargs)
        if path.name == "profile.json":
            original_write_text(source_baseline, '{"drift": true}\n', encoding="utf-8")
            original_write_text(worktree_baseline, '{"drift": true}\n', encoding="utf-8")
        return result

    monkeypatch.setattr(Path, "write_text", mutate_source_after_profile_write)

    with pytest.raises(VerificationRuntimeProfileBuildError, match="source changed during profile build"):
        build_verification_profile(
            project_root=project,
            source_root=source_db.parent,
            output_root=project / ".nbs_agent_runtime" / "verification",
            git_head=git_head,
            ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        )

    assert not profile_dir.exists()


def test_builder_publishes_only_after_profile_is_complete(tmp_path: Path, monkeypatch) -> None:
    project, source_db, runtime = _generation_profile_project(tmp_path, {})
    (runtime / "data_generation.json").write_text(
        json.dumps(_generation_v2_payload(source_db)), encoding="utf-8",
    )
    git_head = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    final_dir = project / ".nbs_agent_runtime" / "verification" / f"profile-{git_head[:12]}"
    original_write_text = Path.write_text
    observed_profile_write = False

    def assert_profile_is_staged(path, data, *args, **kwargs):
        nonlocal observed_profile_write
        if path.name == "profile.json":
            observed_profile_write = True
            assert path.parent != final_dir
            assert not final_dir.exists()
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", assert_profile_is_staged)

    profile_path = build_verification_profile(
        project_root=project,
        source_root=source_db.parent,
        output_root=project / ".nbs_agent_runtime" / "verification",
        git_head=git_head,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )

    assert observed_profile_write
    assert profile_path == final_dir / "profile.json"
    assert final_dir.is_dir()


def test_builder_supports_legacy_source_db_and_runtime_keywords(tmp_path: Path) -> None:
    project, source_db, source_runtime = _generation_profile_project(tmp_path, {})
    generation = _generation_v2_payload(source_db)
    (source_runtime / "data_generation.json").write_text(json.dumps(generation), encoding="utf-8")
    git_head = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    profile_path = build_verification_profile(
        project_root=project,
        source_db=source_db,
        source_runtime=source_runtime,
        output_root=project / ".nbs_agent_runtime" / "verification",
        git_head=git_head,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )

    assert json.loads(profile_path.read_text(encoding="utf-8"))["schemaVersion"] == "verification-runtime-profile-v2"


def test_builder_legacy_paths_preserve_v1_generation_defaults(tmp_path: Path) -> None:
    project, source_db, source_runtime = _generation_profile_project(tmp_path, {})
    generation = {
        "generation": 3,
        "operationId": "legacy-op-3",
        "status": "accepted",
        "updatedAt": "2026-08-17T10:00:00+08:00",
        "dbSignature": {
            "sizeBytes": source_db.stat().st_size,
            "modifiedNs": source_db.stat().st_mtime_ns,
            "sha256": _sha256(source_db),
        },
    }
    (source_runtime / "data_generation.json").write_text(
        json.dumps(generation), encoding="utf-8"
    )
    git_head = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    profile_path = build_verification_profile(
        project_root=project,
        source_db=source_db,
        source_runtime=source_runtime,
        output_root=project / ".nbs_agent_runtime" / "verification",
        git_head=git_head,
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
    )

    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    saved_generation = json.loads(
        (profile_path.parent / "generation.json").read_text(encoding="utf-8")
    )
    assert profile["schemaVersion"] == "verification-runtime-profile-v2"
    assert saved_generation == generation
    assert "coreRevenueSignature" not in saved_generation


def test_builder_rejects_legacy_source_paths_from_different_roots(tmp_path: Path) -> None:
    project, source_db, source_runtime = _generation_profile_project(tmp_path, {})
    other_runtime = tmp_path / "other-checkout" / ".nbs_runtime"
    other_runtime.mkdir(parents=True)
    git_head = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    output_root = project / ".nbs_agent_runtime" / "verification"

    with pytest.raises(VerificationRuntimeProfileBuildError, match="same checkout"):
        build_verification_profile(
            project_root=project,
            source_db=source_db,
            source_runtime=other_runtime,
            output_root=output_root,
            git_head=git_head,
            ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        )
    assert not output_root.exists()


@pytest.mark.parametrize("invalid_hash", ["g" * 64, "A" * 64])
def test_builder_rejects_noncanonical_database_signature_hash(tmp_path: Path, invalid_hash: str) -> None:
    project, source_db, runtime = _generation_profile_project(tmp_path, {})
    git_head = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    generation = _generation_v2_payload(source_db)
    generation["dbSignature"]["sha256"] = invalid_hash
    (runtime / "data_generation.json").write_text(json.dumps(generation), encoding="utf-8")

    with pytest.raises(VerificationRuntimeProfileBuildError, match="source_binding:generation_invalid"):
        build_verification_profile(
            project_root=project, source_root=source_db.parent,
            output_root=project / ".nbs_agent_runtime" / "verification", git_head=git_head,
            ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        )
    assert not (project / ".nbs_agent_runtime" / "verification" / f"profile-{git_head[:12]}").exists()


def test_builder_rejects_unknown_generation_v2_fields(tmp_path: Path) -> None:
    project, source_db, runtime = _generation_profile_project(tmp_path, {})
    git_head = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    generation = _generation_v2_payload(source_db)
    generation["unexpected"] = "must fail closed"
    (runtime / "data_generation.json").write_text(json.dumps(generation), encoding="utf-8")

    with pytest.raises(VerificationRuntimeProfileBuildError, match="source_binding:generation_invalid"):
        build_verification_profile(
            project_root=project, source_root=source_db.parent,
            output_root=project / ".nbs_agent_runtime" / "verification", git_head=git_head,
            ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        )
    assert not (project / ".nbs_agent_runtime" / "verification" / f"profile-{git_head[:12]}").exists()


def test_builder_rejects_missing_source_before_output_creation(tmp_path: Path) -> None:
    output_root = tmp_path / "verification"
    with pytest.raises(VerificationRuntimeProfileBuildError):
        build_verification_profile(
            project_root=tmp_path,
            source_root=tmp_path,
            output_root=output_root,
            git_head="a" * 40,
            ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        )
    assert not output_root.exists()


def test_builder_rejects_output_outside_ignored_verification_runtime(tmp_path: Path) -> None:
    project, source_db, runtime = _generation_profile_project(tmp_path, {})
    git_head = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    (runtime / "data_generation.json").write_text(json.dumps({
        "generation": 1, "operationId": None, "status": "accepted", "updatedAt": "2026-08-17",
        "dbSignature": {"sizeBytes": source_db.stat().st_size, "modifiedNs": source_db.stat().st_mtime_ns, "sha256": _sha256(source_db)},
    }), encoding="utf-8")
    with pytest.raises(VerificationRuntimeProfileBuildError, match="verification runtime"):
        build_verification_profile(
            project_root=project, source_root=source_db.parent,
            output_root=tmp_path / "outside", git_head=git_head,
            ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        )
