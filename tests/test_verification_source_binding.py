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
    _legacy_worktree_fingerprint,
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
    payload["worktreeFingerprint"] = _legacy_worktree_fingerprint(
        profile_path.parents[3], payload["gitHead"]
    )
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


def test_v2_builder_binds_primary_source_head_separately_from_code_worktree_head(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    subprocess.run(
        [
            "git", "-C", str(source.primary), "-c", "user.email=fixture@example.invalid",
            "-c", "user.name=fixture", "commit", "--quiet", "--allow-empty",
            "-m", "advance primary source only",
        ],
        check=True,
    )

    profile_path = _build_profile(source)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))

    assert payload["gitHead"] == _git(source.project, "rev-parse", "HEAD")
    assert payload["sourceBinding"]["sourceHead"] == _git(source.primary, "rev-parse", "HEAD")
    assert payload["gitHead"] != payload["sourceBinding"]["sourceHead"]


def test_v2_acceptance_rejects_caller_override_of_stable_project_identity(
    tmp_path: Path,
) -> None:
    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload["projectId"] = "caller-selected-project"
    _refresh_profile_fingerprint(payload)
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _accept(
        profile_path,
        source.project,
        expected_project_id="caller-selected-project",
        source_root=source.primary,
    )

    assert result.ready is False
    assert "identity_drift:projectId" in result.blocked_reasons


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
    ("field", "value"),
    [("generation", -1), ("operationId", "x" * 129), ("status", ""), ("updatedAt", 7)],
)
def test_source_inspection_rejects_invalid_generation_metadata_values(
    tmp_path: Path, field: str, value: object
) -> None:
    source = _source_fixture(tmp_path)
    generation_path = source.runtime / "data_generation.json"
    payload = json.loads(generation_path.read_text(encoding="utf-8"))
    payload[field] = value
    generation_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(SourceBindingInspectionError, match="source_binding:generation_invalid"):
        inspect_primary_source(source.project, source.primary, expected_project_id=source.project.name)


def test_source_inspection_rejects_noncanonical_project_identity(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)

    with pytest.raises(SourceBindingInspectionError, match="project_identity_mismatch"):
        inspect_primary_source(
            source.project,
            source.primary,
            expected_project_id="caller-selected-project",
        )


def test_source_inspection_fails_closed_on_symlink_loop(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    loop = tmp_path / "source-loop"
    loop.symlink_to(loop)

    with pytest.raises(SourceBindingInspectionError, match="repository_identity_mismatch"):
        inspect_primary_source(
            source.project,
            loop,
            expected_project_id="nbs_analytics",
        )


def test_source_inspection_rejects_primary_checkout_as_isolated_project(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)

    with pytest.raises(SourceBindingInspectionError, match="primary_checkout_isolation_required"):
        inspect_primary_source(
            source.primary,
            source.primary,
            expected_project_id=source.primary.name,
        )


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


def test_safe_repo_file_closes_root_descriptor_if_dup_raises(tmp_path: Path, monkeypatch) -> None:
    import backend.agents.safe_repo_file as safe_repo_file

    root = tmp_path / "repo"
    root.mkdir()
    (root / "file.txt").write_text("fixture", encoding="utf-8")
    real_open = os.open
    real_close = os.close
    root_descriptors: list[int] = []
    closed_descriptors: list[int] = []

    def track_root(_project_root, *, nofollow: int, directory: int):
        descriptor = real_open(root, os.O_RDONLY | directory | nofollow)
        root_descriptors.append(descriptor)
        return descriptor

    def fail_dup(_descriptor: int) -> int:
        raise OSError("injected dup failure")

    def track_close(descriptor: int) -> None:
        closed_descriptors.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(safe_repo_file, "_open_root_directory_nofollow", track_root)
    monkeypatch.setattr(safe_repo_file.os, "dup", fail_dup)
    monkeypatch.setattr(safe_repo_file.os, "close", track_close)

    with pytest.raises(OSError, match="injected dup failure"):
        with safe_repo_file.open_repo_file_nofollow(root, "file.txt"):
            pytest.fail("file should not be yielded when root duplication fails")

    assert root_descriptors
    assert root_descriptors[0] in closed_descriptors


def test_root_fd_file_open_preserves_dup_error_without_masking(tmp_path: Path, monkeypatch) -> None:
    import backend.agents.safe_repo_file as safe_repo_file

    root = tmp_path / "repo"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    monkeypatch.setattr(safe_repo_file.os, "dup", lambda _fd: (_ for _ in ()).throw(OSError("injected dup failure")))

    try:
        with pytest.raises(OSError, match="injected dup failure"):
            with safe_repo_file.open_repo_file_from_root_fd(root_fd, "file.txt"):
                pytest.fail("file should not be yielded when root duplication fails")
        os.fstat(root_fd)
    finally:
        os.close(root_fd)


def test_open_repo_file_nofollow_rejects_symlinked_project_root(tmp_path: Path) -> None:
    import backend.agents.safe_repo_file as safe_repo_file

    real_root = tmp_path / "repo"
    real_root.mkdir()
    (real_root / "file.txt").write_text("fixture", encoding="utf-8")
    linked_root = tmp_path / "repo-link"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(PermissionError, match="Repository root cannot be opened safely"):
        with safe_repo_file.open_repo_file_nofollow(linked_root, "file.txt"):
            pytest.fail("a symlinked project root must be rejected")


def test_git_source_probe_rejects_symlinked_project_root(tmp_path: Path) -> None:
    from backend.agents.verification_chain import git_source_probe

    source = _source_fixture(tmp_path)
    brief = source.project / "brief.md"
    brief.write_text("approved brief\n", encoding="utf-8")
    linked_root = tmp_path / "linked-project-root"
    linked_root.symlink_to(source.project, target_is_directory=True)

    with pytest.raises(PermissionError, match="root.*safely|symlink"):
        git_source_probe(
            linked_root,
            brief_path="brief.md",
            base_sha=_git(source.project, "rev-parse", "HEAD"),
        )


def test_git_source_probe_keeps_validated_root_identity_during_inspection(
    tmp_path: Path, monkeypatch,
) -> None:
    import backend.agents.verification_chain as verification_chain

    source = _source_fixture(tmp_path)
    brief = source.project / "brief.md"
    brief.write_text("approved brief\n", encoding="utf-8")
    expected_head = _git(source.project, "rev-parse", "HEAD")
    original_run = verification_chain.run_bounded_subprocess
    replaced = False

    def replace_path_before_git(argv, **kwargs):
        nonlocal replaced
        if not replaced and argv == ["git", "rev-parse", "HEAD"]:
            replaced = True
            moved = tmp_path / "validated-repository"
            source.project.rename(moved)
            source.project.mkdir()
            subprocess.run(["git", "-C", str(source.project), "init", "-q"], check=True)
            (source.project / "brief.md").write_text("replacement brief\n", encoding="utf-8")
        return original_run(argv, **kwargs)

    monkeypatch.setattr(verification_chain, "run_bounded_subprocess", replace_path_before_git)

    with pytest.raises(ValueError, match="repository root identity changed"):
        verification_chain.git_source_probe(
            source.project,
            brief_path="brief.md",
            base_sha=expected_head,
        )

    assert replaced is True


def test_v2_acceptance_passes_from_same_repository_linked_worktree(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    _bind_v2(profile_path, source.primary)

    result = _accept(profile_path, source.project, source_root=source.primary)

    assert result.ready is True
    assert result.blocked_reasons == ()
    assert result.to_dict()["sourceBinding"]["status"] == "accepted"
    assert str(source.primary) not in json.dumps(result.to_dict())


def test_source_binding_evidence_remains_local_when_other_acceptance_check_blocks(
    tmp_path: Path,
) -> None:
    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    _bind_v2(profile_path, source.primary)
    profile_payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profile_payload["gitHead"] = "a" * 40
    _refresh_profile_fingerprint(profile_payload)
    profile_path.write_text(json.dumps(profile_payload, sort_keys=True), encoding="utf-8")

    result = _accept(profile_path, source.project, source_root=source.primary)

    assert result.ready is False
    assert "identity_drift:gitHead" in result.blocked_reasons
    assert result.to_dict()["sourceBinding"]["status"] == "accepted"


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


def test_builder_uses_stable_project_id_not_primary_checkout_directory_name(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path, primary_name="official-source")

    profile_path = _build_profile(source)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))

    assert payload["projectId"] == "nbs_analytics"


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
                return identity[0], "f" * 40, identity[2]
        return identity

    monkeypatch.setattr(acceptance, "_repo_identity", change_primary_head_on_second_read)

    with pytest.raises(SourceBindingInspectionError, match="source_changed_during_inspection"):
        inspect_primary_source(source.project, source.primary, expected_project_id=source.project.name)


def test_source_inspection_detects_git_metadata_identity_change_with_same_head(tmp_path: Path, monkeypatch) -> None:
    source = _source_fixture(tmp_path)
    import backend.services.verification_profile_acceptance as acceptance

    head_path = source.primary / ".git" / "HEAD"
    before = head_path.stat()
    original_hash = acceptance._hash_file_nofollow
    changed_metadata = False

    def touch_git_head_metadata(root: Path, path: Path):
        nonlocal changed_metadata
        result = original_hash(root, path)
        if root == source.primary and path == source.database and not changed_metadata:
            os.utime(head_path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
            changed_metadata = True
        return result

    monkeypatch.setattr(acceptance, "_hash_file_nofollow", touch_git_head_metadata)

    with pytest.raises(SourceBindingInspectionError, match="source_changed_during_inspection"):
        inspect_primary_source(source.project, source.primary, expected_project_id=source.project.name)

    assert changed_metadata


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


def test_builder_discards_profile_when_database_inode_changes_during_snapshot(tmp_path: Path, monkeypatch) -> None:
    source = _source_fixture(tmp_path)
    import scripts.build_verification_runtime_profile as builder

    original = builder.build_read_only_snapshot

    def replace_database_after_snapshot(database: Path, destination: Path):
        evidence = original(database, destination)
        replacement = source.primary / "database-replacement.sqlite"
        replacement.write_bytes(source.database.read_bytes())
        os.utime(
            replacement,
            ns=(source.database.stat().st_atime_ns, source.database.stat().st_mtime_ns),
        )
        replacement.replace(source.database)
        return evidence

    monkeypatch.setattr(builder, "build_read_only_snapshot", replace_database_after_snapshot)

    with pytest.raises(VerificationRuntimeProfileBuildError, match="source changed during profile build"):
        _build_profile(source)


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


def test_v2_acceptance_binds_code_and_primary_source_heads_independently(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    subprocess.run(
        [
            "git", "-C", str(source.primary), "-c", "user.email=fixture@example.invalid",
            "-c", "user.name=fixture", "commit", "--quiet", "--allow-empty",
            "-m", "advance primary source only",
        ],
        check=True,
    )
    profile_path = _build_profile(source)
    result = _accept(profile_path, source.project, source_root=source.primary)
    payload = result.to_dict()
    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    assert result.ready is True
    assert payload["identity"]["gitHead"] == profile["gitHead"]
    assert payload["identity"]["gitHead"] == _git(source.project, "rev-parse", "HEAD")
    assert payload["sourceBinding"]["sourceHead"] == profile["sourceBinding"]["sourceHead"]
    assert payload["sourceBinding"]["sourceHead"] == _git(source.primary, "rev-parse", "HEAD")
    assert payload["identity"]["gitHead"] != payload["sourceBinding"]["sourceHead"]


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
    built_profile = _build_profile(source)
    profile_dir = source.primary / ".nbs_agent_runtime" / "verification" / built_profile.parent.name
    shutil.copytree(built_profile.parent, profile_dir)
    profile_path = profile_dir / "profile.json"
    _bind_v1(profile_path)

    local_result = _accept(profile_path, source.primary)
    external_result = _accept(profile_path, source.primary, source_root=source.project)

    assert local_result.ready is True
    assert external_result.ready is True
    assert external_result.blocked_reasons == local_result.blocked_reasons


def test_v1_legacy_worktree_fingerprint_matches_original_porcelain_contract(tmp_path: Path) -> None:
    source = _source_fixture(tmp_path)
    runtime_state = source.project / ".nbs_agent_runtime/legacy-state.json"
    runtime_state.parent.mkdir()
    runtime_state.write_text("baseline\n", encoding="utf-8")
    _git(source.project, "add", "-f", ".nbs_agent_runtime/legacy-state.json")
    subprocess.run(
        ["git", "-C", str(source.project), "-c", "user.email=fixture@example.invalid",
         "-c", "user.name=fixture", "commit", "--quiet", "-m", "runtime fixture"],
        check=True,
    )
    head = _git(source.project, "rev-parse", "HEAD")
    runtime_state.write_text("legacy dirty state\n", encoding="utf-8")
    legacy_status = subprocess.run(
        ["git", "-C", str(source.project), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    expected = canonical_fingerprint({"head": head, "status": legacy_status})

    assert _legacy_worktree_fingerprint(source.project, head) == expected


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
    source_binding = json.loads(check["stdout"])["sourceBinding"]
    expected_binding = json.loads(profile_path.read_text(encoding="utf-8"))["sourceBinding"]

    assert check["required"] is True
    assert check["exitCode"] == 0
    assert report["overallStatus"] == "pass"
    assert source_binding["status"] == "accepted"
    assert source_binding["sourceRole"] == "primary-checkout"
    assert source_binding["repositoryFingerprint"] == expected_binding["repositoryFingerprint"]
    assert source_binding["sourceHead"] == expected_binding["sourceHead"]
    assert source_binding["databaseFingerprint"] == expected_binding["databaseFingerprint"]
    assert source_binding["generationFingerprint"] == expected_binding["generationFingerprint"]
    assert source_binding["baselineRegistryFingerprint"] == expected_binding["baselineRegistryFingerprint"]
    assert str(source.primary) not in json.dumps(report)


def test_hermes_propagates_failed_v2_source_binding_acceptance(tmp_path: Path, monkeypatch) -> None:
    from backend.services import verification_profile_acceptance
    from scripts import hermes_post_change_check

    source = _source_fixture(tmp_path)
    profile_path = _build_profile(source)
    executed_steps = []

    def reject_primary_source(*_args, **_kwargs):
        raise verification_profile_acceptance.SourceBindingInspectionError(
            "source_binding:database_fingerprint_mismatch"
        )

    def record_run_step(step, **_):
        executed_steps.append(step.label)
        return {
            "label": step.label, "command": step.command, "required": step.required,
            "timeout": step.timeout, "exitCode": 0, "stdout": "ok", "stderr": "",
        }

    monkeypatch.setattr(verification_profile_acceptance, "inspect_primary_source", reject_primary_source)
    monkeypatch.setattr(hermes_post_change_check, "run_step", record_run_step)

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
    evidence = json.loads(check["stdout"])

    assert report["overallStatus"] == "fail"
    assert check["required"] is True
    assert check["exitCode"] == 1
    assert evidence["sourceBinding"]["status"] == "blocked"
    assert "source_binding:database_fingerprint_mismatch" in evidence["blockedReasons"]
    assert executed_steps == []


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


def test_hermes_emits_required_source_binding_result_when_profile_load_fails(tmp_path: Path) -> None:
    from scripts import hermes_post_change_check

    project = tmp_path / "project"
    project.mkdir()
    invalid_profile = tmp_path / "invalid-profile.json"
    invalid_profile.write_text("not-json", encoding="utf-8")

    report = hermes_post_change_check.run_checks(
        project_root=project,
        verification_profile=str(invalid_profile),
        hermes_profile="isolated-profile",
    )

    check = next(item for item in report["results"] if item["label"] == "source-binding-acceptance")
    evidence = json.loads(check["stdout"])
    assert report["overallStatus"] == "fail"
    assert check["required"] is True
    assert check["exitCode"] == 1
    assert evidence["sourceBinding"]["status"] == "blocked"
    assert evidence["blockedReasons"] == ["source_binding:profile_load_failed"]
