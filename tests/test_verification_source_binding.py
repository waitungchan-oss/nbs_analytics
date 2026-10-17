from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.services.verification_profile_acceptance import (
    ServiceIdentityEvidence,
    SourceBindingInspectionError,
    accept_profile_file,
    inspect_primary_source,
)
from backend.services.verification_runtime_profile import VerificationRuntimeProfile
from scripts.build_verification_runtime_profile import (
    VerificationRuntimeProfileBuildError,
    build_verification_profile,
)


@dataclass(frozen=True)
class SourceFixture:
    primary: Path
    project: Path
    database: Path
    runtime: Path


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_fingerprint(primary: Path) -> str:
    common_dir = Path(
        _git(primary, "rev-parse", "--path-format=absolute", "--git-common-dir")
    ).resolve(strict=True)
    return hashlib.sha256(str(common_dir).encode("utf-8")).hexdigest()


def _source_fixture(
    tmp_path: Path,
    *,
    primary_name: str = "nbs_analytics",
    worktree_name: str = "nbs_analytics",
) -> SourceFixture:
    primary = tmp_path / primary_name
    primary.mkdir(parents=True)
    subprocess.run(["git", "-C", str(primary), "init", "-q"], check=True)
    (primary / ".gitignore").write_text(
        "nbs_marketing_data.db\n.nbs_runtime/\n.nbs_agent_runtime/\n.nbs_runtime_cache/\n",
        encoding="utf-8",
    )
    (primary / "data").mkdir()
    baseline = primary / "data" / "monthly_revenue_baselines.json"
    baseline.write_text("{}\n", encoding="utf-8")
    _git(primary, "add", ".gitignore", "data/monthly_revenue_baselines.json")
    subprocess.run(
        [
            "git", "-C", str(primary), "-c", "user.email=fixture@example.invalid",
            "-c", "user.name=fixture", "commit", "--quiet", "-m", "fixture baseline",
        ],
        check=True,
    )

    database = primary / "nbs_marketing_data.db"
    with sqlite3.connect(database) as conn:
        conn.execute("create table source_fixture (value text)")
        conn.execute("insert into source_fixture values ('fixture-only')")
    runtime = primary / ".nbs_runtime"
    runtime.mkdir()
    (runtime / "data_generation.json").write_text(
        json.dumps(
            {
                "generation": 1,
                "operationId": None,
                "status": "accepted",
                "updatedAt": "2026-09-30T10:00:00+08:00",
                "dbSignature": {
                    "sizeBytes": database.stat().st_size,
                    "modifiedNs": database.stat().st_mtime_ns,
                    "sha256": _sha256(database),
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    project = tmp_path / "worktrees" / worktree_name
    project.parent.mkdir(parents=True)
    _git(primary, "worktree", "add", "--quiet", "--detach", str(project), "HEAD")
    return SourceFixture(primary, project, database, runtime)


def _build_profile(source: SourceFixture, *, project_root: Path | None = None) -> Path:
    project = project_root or source.project
    return build_verification_profile(
        project_root=project,
        source_root=source.primary,
        output_root=project / ".nbs_agent_runtime" / "verification",
        git_head=_git(project, "rev-parse", "HEAD"),
        ports={"api": 28601, "streamlit": 28502, "vue": 25173},
    )


def _refresh_profile_fingerprint(payload: dict[str, object]) -> None:
    unsigned = {key: value for key, value in payload.items() if key != "profileFingerprint"}
    payload["profileFingerprint"] = canonical_fingerprint(unsigned)


def _bind_v2(profile_path: Path, primary: Path) -> None:
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    if payload["schemaVersion"] == "verification-runtime-profile-v2":
        return
    payload["schemaVersion"] = "verification-runtime-profile-v2"
    payload["sourceBinding"] = {
        "schemaVersion": "verification-source-binding-v1",
        "sourceRole": "primary-checkout",
        "repositoryFingerprint": _repo_fingerprint(primary),
        "sourceHead": _git(primary, "rev-parse", "HEAD"),
        "databaseFingerprint": payload["database"]["sourceFingerprint"],
        "generationFingerprint": payload["runtime"]["generationFingerprint"],
        "baselineRegistryFingerprint": payload["baseline"]["registryFingerprint"],
    }
    _refresh_profile_fingerprint(payload)
    profile_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _bind_v1(profile_path: Path) -> None:
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload.pop("sourceBinding", None)
    payload["schemaVersion"] = "verification-runtime-profile-v1"
    _refresh_profile_fingerprint(payload)
    profile_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _accept(profile_path: Path, project_root: Path, **kwargs):
    return accept_profile_file(
        profile_path,
        project_root=project_root,
        service_identity=ServiceIdentityEvidence(True, "service_identity_verified"),
        **kwargs,
    )


def test_v2_profile_builds_from_explicit_primary_source(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    before = {
        "database": (_sha256(source.database), source.database.stat().st_size, source.database.stat().st_mtime_ns),
        "generation": _sha256(source.runtime / "data_generation.json"),
        "baseline": _sha256(source.primary / "data/monthly_revenue_baselines.json"),
        "gitStatus": _git(source.primary, "status", "--porcelain"),
    }

    profile_path = _build_profile(source)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))

    assert payload["schemaVersion"] == "verification-runtime-profile-v2"
    assert payload["sourceBinding"]["sourceRole"] == "primary-checkout"
    assert payload["sourceBinding"]["repositoryFingerprint"] == _repo_fingerprint(source.primary)
    assert payload["sourceBinding"]["sourceHead"] == _git(source.primary, "rev-parse", "HEAD")
    assert payload["database"]["readOnly"] is True
    assert (profile_path.parent / "snapshot.sqlite").is_file()
    assert str(source.primary) not in json.dumps(payload)
    after = {
        "database": (_sha256(source.database), source.database.stat().st_size, source.database.stat().st_mtime_ns),
        "generation": _sha256(source.runtime / "data_generation.json"),
        "baseline": _sha256(source.primary / "data/monthly_revenue_baselines.json"),
        "gitStatus": _git(source.primary, "status", "--porcelain"),
    }
    assert after == before


def test_builder_and_v2_acceptance_use_stable_project_id_for_differently_named_worktree(
    tmp_path: Path,
) -> None:
    source = _source_fixture(tmp_path, worktree_name="codex-source-binding")

    profile_path = _build_profile(source)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    result = _accept(profile_path, source.project, source_root=source.primary)

    assert payload["projectId"] == "nbs_analytics"
    assert result.ready is True
    assert result.project_id == "nbs_analytics"


def test_successful_source_inspection_returns_immutable_generation_payload(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)

    inspection = inspect_primary_source(
        source.project,
        source.primary,
        expected_project_id=source.project.name,
    )

    with pytest.raises(TypeError):
        inspection.generation_payload["generation"] = 99


@pytest.mark.parametrize(
    ("relative_path", "parent_name", "leaf_name"),
    [
        ("data/monthly_revenue_baselines.json", "data", "monthly_revenue_baselines.json"),
        (".nbs_runtime/data_generation.json", ".nbs_runtime", "data_generation.json"),
    ],
)
def test_source_inspection_does_not_follow_swapped_parent_directory(
    tmp_path: Path,
    monkeypatch,
    relative_path: str,
    parent_name: str,
    leaf_name: str,
) -> None:
    import backend.agents.safe_repo_file as safe_repo_file
    import backend.services.verification_profile_acceptance as acceptance

    source = _source_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    original_parent = source.primary / parent_name
    original_leaf = original_parent / leaf_name
    external_leaf = outside / leaf_name
    shutil.copyfile(original_leaf, external_leaf)
    moved_parent = tmp_path / f"{parent_name.strip('.').replace('/', '-')}-original"
    real_open = safe_repo_file.open_repo_file_nofollow
    intercepted = False

    @contextmanager
    def swap_parent_before_open(root: Path, requested_path: str):
        nonlocal intercepted
        if root == source.primary and requested_path == relative_path:
            intercepted = True
            original_parent.rename(moved_parent)
            original_parent.symlink_to(outside, target_is_directory=True)
            try:
                with real_open(root, requested_path) as descriptor:
                    yield descriptor
            finally:
                if original_parent.is_symlink():
                    original_parent.unlink()
                moved_parent.rename(original_parent)
            return
        with real_open(root, requested_path) as descriptor:
            yield descriptor

    monkeypatch.setattr(
        acceptance,
        "open_repo_file_nofollow",
        swap_parent_before_open,
        raising=False,
    )

    with pytest.raises(SourceBindingInspectionError, match="source_file_invalid"):
        inspect_primary_source(
            source.project,
            source.primary,
            expected_project_id=source.project.name,
        )

    assert intercepted
    assert not original_parent.is_symlink()


def test_safe_repo_file_closes_child_descriptor_if_fstat_raises(tmp_path: Path, monkeypatch) -> None:
    import backend.agents.safe_repo_file as safe_repo_file

    root = tmp_path / "repo"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "file.txt").write_text("fixture", encoding="utf-8")
    real_open = os.open
    real_fstat = os.fstat
    child_descriptors: list[int] = []

    def track_open(path, flags, *args, **kwargs):
        descriptor = real_open(path, flags, *args, **kwargs)
        if path == "nested" and "dir_fd" in kwargs:
            child_descriptors.append(descriptor)
        return descriptor

    def fail_child_fstat(descriptor: int):
        if descriptor in child_descriptors:
            raise OSError("injected fstat failure")
        return real_fstat(descriptor)

    monkeypatch.setattr(safe_repo_file.os, "open", track_open)
    monkeypatch.setattr(safe_repo_file.os, "fstat", fail_child_fstat)

    with pytest.raises(OSError, match="injected fstat failure"):
        with safe_repo_file.open_repo_file_nofollow(root, "nested/file.txt"):
            pytest.fail("file should not be yielded after child fstat fails")

    assert len(child_descriptors) == 1
    with pytest.raises(OSError):
        real_fstat(child_descriptors[0])


def test_v2_acceptance_passes_from_same_repository_linked_worktree(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    _bind_v2(profile_path, source.primary)

    result = _accept(profile_path, source.project, source_root=source.primary)

    assert result.ready is True
    assert result.blocked_reasons == ()
    assert result.to_dict()["sourceBinding"]["status"] == "accepted"
    assert str(source.primary) not in json.dumps(result.to_dict())


def test_v2_acceptance_blocks_missing_source_root(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    _bind_v2(profile_path, source.primary)

    result = _accept(profile_path, source.project)

    assert result.ready is False
    assert "source_binding:source_root_required" in result.blocked_reasons


def test_v2_acceptance_blocks_unrelated_repository(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    other = _source_fixture(tmp_path / "other")
    profile_path = _build_profile(source)
    _bind_v2(profile_path, source.primary)

    result = _accept(profile_path, source.project, source_root=other.primary)

    assert result.ready is False
    assert any(reason.startswith("source_binding:repository") for reason in result.blocked_reasons)


def test_v2_acceptance_rejects_linked_worktree_as_primary_source(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    _bind_v2(profile_path, source.primary)

    result = _accept(profile_path, source.project, source_root=source.project)

    assert result.ready is False
    assert any(reason.startswith("source_binding:primary_checkout") for reason in result.blocked_reasons)


def test_builder_rejects_source_project_id_mismatch(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path, primary_name="official-source")

    with pytest.raises(VerificationRuntimeProfileBuildError, match="project"):
        _build_profile(source)


def test_builder_rejects_symlinked_generation_parent_before_read(tmp_path: Path, monkeypatch) -> None:
    source = _source_fixture(tmp_path)
    actual_runtime = tmp_path / "runtime-target"
    source.runtime.rename(actual_runtime)
    (source.primary / ".nbs_runtime").symlink_to(actual_runtime, target_is_directory=True)
    import backend.services.verification_profile_acceptance as acceptance

    original = acceptance._read_generation_nofollow
    calls = 0

    def track_read(root: Path, path: Path):
        nonlocal calls
        calls += 1
        return original(root, path)

    monkeypatch.setattr(acceptance, "_read_generation_nofollow", track_read)

    with pytest.raises(VerificationRuntimeProfileBuildError, match="source_binding:source_file_invalid"):
        _build_profile(source)

    assert calls == 0


def test_source_inspection_detects_same_size_content_change_with_restored_mtime(tmp_path: Path, monkeypatch) -> None:
    source = _source_fixture(tmp_path)
    import backend.services.verification_profile_acceptance as acceptance

    original = acceptance._hash_file_nofollow
    database_stat = source.database.stat()
    original_bytes = source.database.read_bytes()
    changed_bytes = original_bytes.replace(b"fixture-only", b"fixture-many")
    assert len(changed_bytes) == len(original_bytes)
    calls = 0

    def change_database_after_initial_hash(root: Path, path: Path):
        nonlocal calls
        result = original(root, path)
        if path == source.database and calls == 0:
            source.database.write_bytes(changed_bytes)
            os.utime(source.database, ns=(database_stat.st_atime_ns, database_stat.st_mtime_ns))
        calls += 1
        return result

    monkeypatch.setattr(acceptance, "_hash_file_nofollow", change_database_after_initial_hash)

    with pytest.raises(SourceBindingInspectionError, match="source_changed_during_inspection"):
        inspect_primary_source(source.project, source.primary, expected_project_id=source.project.name)


def test_source_inspection_detects_primary_head_change_during_file_checks(tmp_path: Path, monkeypatch) -> None:
    source = _source_fixture(tmp_path)
    import backend.services.verification_profile_acceptance as acceptance

    original = acceptance._repo_identity
    source_identity_reads = 0

    def change_primary_head_on_second_read(root: Path, *, primary: bool):
        nonlocal source_identity_reads
        identity = original(root, primary=primary)
        if root == source.primary:
            source_identity_reads += 1
            if source_identity_reads == 2:
                return identity[0], "f" * 40
        return identity

    monkeypatch.setattr(acceptance, "_repo_identity", change_primary_head_on_second_read)

    with pytest.raises(SourceBindingInspectionError, match="source_changed_during_inspection"):
        inspect_primary_source(source.project, source.primary, expected_project_id=source.project.name)


def test_builder_discards_profile_when_source_changes_during_snapshot(tmp_path: Path, monkeypatch) -> None:
    source = _source_fixture(tmp_path)
    import scripts.build_verification_runtime_profile as builder

    original = builder.build_read_only_snapshot

    def mutate_generation_after_snapshot(database: Path, destination: Path):
        evidence = original(database, destination)
        generation_path = source.runtime / "data_generation.json"
        generation = json.loads(generation_path.read_text(encoding="utf-8"))
        generation["generation"] = 2
        generation_path.write_text(json.dumps(generation, sort_keys=True), encoding="utf-8")
        return evidence

    monkeypatch.setattr(builder, "build_read_only_snapshot", mutate_generation_after_snapshot)

    with pytest.raises(VerificationRuntimeProfileBuildError, match="source changed during profile build"):
        _build_profile(source)

    assert not (source.project / ".nbs_agent_runtime" / "verification" / f"profile-{_git(source.project, 'rev-parse', 'HEAD')[:12]}").exists()


def test_source_root_cli_flags_are_explicit_and_local_only(tmp_path: Path):
    from scripts import hermes_post_change_check, verification_profile_acceptance

    source_root = str(tmp_path / "official-checkout")
    acceptance_args = verification_profile_acceptance.build_parser().parse_args(
        ["--profile", "profile.json", "--source-root", source_root]
    )
    hermes_args = hermes_post_change_check.parse_args(
        ["--verification-profile", "profile.json", "--source-root", source_root]
    )

    assert acceptance_args.source_root == source_root
    assert hermes_args.source_root == source_root


@pytest.mark.parametrize("drift", ["database", "generation", "baseline", "head"])
def test_v2_acceptance_blocks_primary_source_drift(tmp_path: Path, drift: str) -> None:
    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    _bind_v2(profile_path, source.primary)
    if drift == "database":
        with sqlite3.connect(source.database) as conn:
            conn.execute("insert into source_fixture values ('drift')")
    elif drift == "generation":
        generation_path = source.runtime / "data_generation.json"
        generation = json.loads(generation_path.read_text(encoding="utf-8"))
        generation["generation"] = 2
        generation_path.write_text(json.dumps(generation, sort_keys=True), encoding="utf-8")
    elif drift == "baseline":
        (source.primary / "data/monthly_revenue_baselines.json").write_text('{"drift":true}\n', encoding="utf-8")
    else:
        subprocess.run(
            [
                "git", "-C", str(source.primary), "-c", "user.email=fixture@example.invalid",
                "-c", "user.name=fixture", "commit", "--quiet", "--allow-empty", "-m", "source head drift",
            ],
            check=True,
        )

    result = _accept(profile_path, source.project, source_root=source.primary)

    assert result.ready is False
    assert any(reason.startswith("source_binding:") for reason in result.blocked_reasons)


def test_v2_acceptance_blocks_generation_database_signature_mismatch(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    generation_path = source.runtime / "data_generation.json"
    generation = json.loads(generation_path.read_text(encoding="utf-8"))
    generation["dbSignature"]["sha256"] = "0" * 64
    generation_path.write_text(json.dumps(generation, sort_keys=True), encoding="utf-8")

    result = _accept(profile_path, source.project, source_root=source.primary)

    assert result.ready is False
    assert "source_binding:generation_database_signature_mismatch" in result.blocked_reasons


def test_v2_acceptance_blocks_code_head_different_from_primary_source_head(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    subprocess.run(
        [
            "git", "-C", str(source.project), "-c", "user.email=fixture@example.invalid",
            "-c", "user.name=fixture", "commit", "--quiet", "--allow-empty",
            "-m", "code checkout head drift",
        ],
        check=True,
    )
    from scripts.build_verification_runtime_profile import _worktree_fingerprint

    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload["gitHead"] = _git(source.project, "rev-parse", "HEAD")
    payload["worktreeFingerprint"] = _worktree_fingerprint(source.project, payload["gitHead"])
    _refresh_profile_fingerprint(payload)
    profile_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    result = _accept(profile_path, source.project, source_root=source.primary)

    assert result.ready is False
    assert "source_binding:source_head_project_mismatch" in result.blocked_reasons


def test_v2_acceptance_blocks_symlinked_source_database(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    _bind_v2(profile_path, source.primary)
    target = source.database.with_name("database-target.sqlite")
    source.database.rename(target)
    source.database.symlink_to(target.name)

    result = _accept(profile_path, source.project, source_root=source.primary)

    assert result.ready is False
    assert "source_binding:source_file_invalid" in result.blocked_reasons


def test_v1_acceptance_ignores_external_source_root_and_remains_local_only(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source, project_root=source.primary)
    _bind_v1(profile_path)

    local_result = _accept(profile_path, source.primary)
    external_result = _accept(profile_path, source.primary, source_root=source.project)

    assert local_result.ready is True
    assert external_result.ready is True
    assert external_result.blocked_reasons == local_result.blocked_reasons


def test_hermes_requires_v2_source_binding_and_never_serializes_source_root(tmp_path: Path, monkeypatch) -> None:
    from scripts import hermes_post_change_check

    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    monkeypatch.setattr(
        hermes_post_change_check,
        "run_step",
        lambda step, **_: {
            "label": step.label, "command": step.command, "required": step.required,
            "timeout": step.timeout, "exitCode": 0, "stdout": "ok", "stderr": "",
        },
    )

    report = hermes_post_change_check.run_checks(
        include_monitor=False,
        include_tests=False,
        include_system_acceptance=False,
        project_root=source.project,
        verification_profile=str(profile_path),
        source_root=source.primary,
        hermes_profile="isolated-profile",
    )
    check = next(item for item in report["results"] if item["label"] == "source-binding-acceptance")

    assert check["required"] is True
    assert check["exitCode"] == 0
    assert str(source.primary) not in json.dumps(report)


def test_hermes_v2_source_binding_fails_closed_without_source_root(tmp_path: Path, monkeypatch) -> None:
    from scripts import hermes_post_change_check

    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    executed_steps = []

    def record_run_step(step, **_):
        executed_steps.append(step.label)
        return {
            "label": step.label, "command": step.command, "required": step.required,
            "timeout": step.timeout, "exitCode": 0, "stdout": "ok", "stderr": "",
        }

    monkeypatch.setattr(hermes_post_change_check, "run_step", record_run_step)

    report = hermes_post_change_check.run_checks(
        include_monitor=False,
        include_tests=False,
        include_system_acceptance=False,
        project_root=source.project,
        verification_profile=str(profile_path),
        hermes_profile="isolated-profile",
    )
    check = next(item for item in report["results"] if item["label"] == "source-binding-acceptance")

    assert check["required"] is True
    assert check["exitCode"] != 0
    assert "source_binding:source_root_required" in check["stdout"]
    assert executed_steps == []
