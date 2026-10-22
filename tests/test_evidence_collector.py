import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from backend.agents.evidence_collector import CommandEvidence, EvidenceCollector, EvidencePolicy


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def write_configs(
    root: Path,
    *,
    context_input_tokens: int = 12000,
    review_command_characters: int | None = None,
    default_context_files: tuple[str, ...] = (),
) -> None:
    (root / "agent_config").mkdir()
    (root / "agent_config/evidence_allowlist.json").write_text(
        '{"readRoots":["docs","backend","tests","Summay"],"rootFiles":["AGENTS.md",".gitignore"],'
        f'"defaultContextFiles":{json.dumps(list(default_context_files))},"extensions":[".md",".py"],'
        '"denyPatterns":["*.db",".env","outputs/**"],"agentExecutables":["codex"]}',
        encoding="utf-8",
    )
    (root / "agent_config/token_budgets.json").write_text(
        f'{{"context":{{"inputTokens":{context_input_tokens},"outputTokens":1500}},'
        '"review":{"inputTokens":16000,"outputTokens":2000'
        + (
            f',"maxCommandCharacters":{review_command_characters}'
            if review_command_characters is not None
            else ""
        )
        + '},'
        '"excerpt":{"maxFileLines":5,"symbolContextLines":2,"maxCommandCharacters":200}}',
        encoding="utf-8",
    )


def test_policy_rejects_path_escape_and_denied_data(tmp_path):
    write_configs(tmp_path)
    policy = EvidencePolicy.from_project(tmp_path)
    with pytest.raises(PermissionError):
        policy.resolve_read_path(tmp_path.parent / "outside.md")
    denied = tmp_path / "secret.db"
    denied.write_text("x", encoding="utf-8")
    with pytest.raises(PermissionError):
        policy.resolve_read_path(denied)


def test_policy_rejects_summary_path_before_reading(tmp_path, monkeypatch):
    write_configs(tmp_path)
    summary = tmp_path / "Summay/private.md"
    summary.parent.mkdir()
    summary.write_text("fixture only", encoding="utf-8")
    policy = EvidencePolicy.from_project(tmp_path)
    original_read_text = Path.read_text

    def guarded_read_text(path, *args, **kwargs):
        if path.is_relative_to(tmp_path / "Summay"):
            raise AssertionError("Summary fixture content must not be opened")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    with pytest.raises(PermissionError, match="Excluded"):
        policy.resolve_read_path(summary)


def test_context_collection_skips_summary_defaults_and_prunes_queries(tmp_path, monkeypatch):
    init_repo(tmp_path)
    write_configs(
        tmp_path,
        default_context_files=("Summay/private.md", "docs/allowed.md"),
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "Summay").mkdir()
    brief = tmp_path / "docs/brief.md"
    allowed = tmp_path / "docs/allowed.md"
    summary = tmp_path / "Summay/private.md"
    brief.write_text("objective", encoding="utf-8")
    allowed.write_text("needle in allowlisted doc", encoding="utf-8")
    summary.write_text("needle in excluded fixture", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    original_read_text = Path.read_text

    def guarded_read_text(path, *args, **kwargs):
        if path.is_relative_to(tmp_path / "Summay"):
            raise AssertionError("Context collection attempted to read an excluded fixture")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    bundle = EvidenceCollector(tmp_path).collect_context(
        brief, base_ref="HEAD", queries=("needle",),
    )

    sources = [item.source for item in bundle.evidence]
    assert sources == ["docs/brief.md", "docs/allowed.md"]
    assert all("Summay/" not in source for source in sources)


def test_context_default_symlink_into_summary_is_rejected_before_file_probe(tmp_path, monkeypatch):
    init_repo(tmp_path)
    write_configs(
        tmp_path,
        default_context_files=("docs/shortcut/private.md",),
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    summary = tmp_path / "Summay"
    summary.mkdir()
    (summary / "private.md").write_text("private fixture", encoding="utf-8")
    (docs / "shortcut").symlink_to("../Summay", target_is_directory=True)
    brief = docs / "brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    original_is_file = Path.is_file
    excluded_file_probes = []

    def record_excluded_file_probe(path):
        if path.resolve().is_relative_to(summary.resolve()):
            excluded_file_probes.append(path)
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", record_excluded_file_probe)

    with pytest.raises(PermissionError, match="Excluded"):
        EvidenceCollector(tmp_path).collect_context(brief, base_ref="HEAD")

    assert excluded_file_probes == []


def test_candidate_inventory_does_not_walk_configured_summary_read_root(tmp_path, monkeypatch):
    from dataclasses import replace

    write_configs(tmp_path)
    summary_root = tmp_path / "sUmMaY"
    summary_root.mkdir()
    (summary_root / "private.md").write_text("private fixture", encoding="utf-8")
    policy = replace(
        EvidencePolicy.from_project(tmp_path),
        read_roots=(Path("sUmMaY"),),
        root_files=(),
    )
    collector = EvidenceCollector(tmp_path, policy=policy)

    def forbidden_walk(root, *args, **kwargs):
        pytest.fail(f"excluded Summary root was traversed: {root}")

    monkeypatch.setattr("backend.agents.evidence_collector.os.walk", forbidden_walk)

    assert collector._candidate_paths() == ()


def test_candidate_inventory_does_not_walk_symlinked_read_root(tmp_path, monkeypatch):
    from dataclasses import replace

    write_configs(tmp_path)
    outside = tmp_path / "private-root"
    outside.mkdir()
    (outside / "private.md").write_text("private fixture", encoding="utf-8")
    linked_root = tmp_path / "linked-docs"
    linked_root.symlink_to(outside, target_is_directory=True)
    policy = replace(
        EvidencePolicy.from_project(tmp_path),
        read_roots=("linked-docs",),
        root_files=(),
    )
    collector = EvidenceCollector(tmp_path, policy=policy)

    def forbidden_walk(root, *args, **kwargs):
        pytest.fail(f"symlinked read root was traversed: {root}")

    monkeypatch.setattr("backend.agents.evidence_collector.os.walk", forbidden_walk)

    assert collector._candidate_paths() == ()


def test_project_policy_allows_only_named_root_worker_governance_file():
    policy = EvidencePolicy.from_project(PROJECT_ROOT)

    allowed = policy.resolve_read_path(PROJECT_ROOT / "NBS_CODEX_WORKER_WORKFLOW.md")

    assert allowed == PROJECT_ROOT / "NBS_CODEX_WORKER_WORKFLOW.md"
    with pytest.raises(PermissionError, match="allowlisted"):
        policy.resolve_read_path(PROJECT_ROOT / "UNLISTED_ROOT_GOVERNANCE.md")


def test_context_collection_truncates_documents_and_never_reads_db(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs/superpowers").mkdir(parents=True)
    brief = tmp_path / "docs/brief.md"
    brief.write_text("\n".join(f"line-{i}" for i in range(20)), encoding="utf-8")
    (tmp_path / "secret.db").write_text("formal rows", encoding="utf-8")
    subprocess.run(["git", "add", "docs/brief.md", "agent_config"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    bundle = EvidenceCollector(tmp_path).collect_context(brief, base_ref="HEAD")

    document = next(item for item in bundle.evidence if item.source == "docs/brief.md")
    assert len(document.content.splitlines()) == 5
    assert document.metadata["truncated"] is True
    assert "formal rows" not in str(bundle.to_dict())
    assert bundle.repository["head"]


def test_context_collection_includes_only_allowlisted_explicit_and_query_matches(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "backend").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("snapshot objective", encoding="utf-8")
    related = tmp_path / "backend/snapshot.py"
    related.write_text("def build_snapshot():\n    return 'snapshot'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    bundle = EvidenceCollector(tmp_path).collect_context(
        brief, base_ref="HEAD", include_paths=(related,), queries=("build_snapshot",),
    )

    sources = [item.source for item in bundle.evidence]
    assert sources == ["docs/brief.md", "backend/snapshot.py"]
    assert any(item.label.startswith("rg-query-") for item in bundle.commands)


def test_query_searches_only_policy_approved_files(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    allowed = tmp_path / "docs/allowed.md"
    allowed.write_text("needle", encoding="utf-8")
    for name in ("secret.db", ".env", "secret.xlsx", "secret.log"):
        (tmp_path / "docs" / name).write_text("needle", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    collector = EvidenceCollector(tmp_path)
    commands = []
    original_run = collector._run

    def capture(label, argv, **kwargs):
        result = original_run(label, argv, **kwargs)
        if label.startswith("rg-query-"):
            commands.append(result)
        return result

    collector._run = capture
    bundle = collector.collect_context(
        tmp_path / "docs/allowed.md", base_ref="HEAD", queries=("needle",),
    )

    assert [item.source for item in bundle.evidence] == ["docs/allowed.md"]
    assert commands
    query_argv = commands[0].argv
    assert "docs" not in query_argv
    assert "docs/allowed.md" in query_argv
    assert not any(name in query_argv for name in ("secret.db", ".env", "secret.xlsx", "secret.log"))


def test_query_batches_many_candidates_without_missing_matches(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    matching_sources = []
    for index in range(70):
        name = f"candidate-{index:03d}.md"
        path = tmp_path / "docs" / name
        path.write_text("batch-needle" if index < 5 else "other", encoding="utf-8")
        if index < 5:
            matching_sources.append(path.relative_to(tmp_path).as_posix())
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    collector = EvidenceCollector(tmp_path)
    rg_commands = []
    original_run = collector._run

    def capture(label, argv, **kwargs):
        result = original_run(label, argv, **kwargs)
        if label.startswith("rg-query-"):
            rg_commands.append(result)
        return result

    collector._run = capture
    bundle = collector.collect_context(
        brief, base_ref="HEAD", queries=("batch-needle",),
    )

    sources = [item.source for item in bundle.evidence]
    assert len(rg_commands) > 1
    assert set(matching_sources).issubset(sources)
    for command in rg_commands:
        candidate_args = command.argv[5:]
        assert len(candidate_args) <= collector._QUERY_MAX_FILES_PER_BATCH
        assert sum(len(argument) + 1 for argument in command.argv) <= (
            collector._QUERY_MAX_ARGUMENT_CHARACTERS
        )


def test_context_rejects_option_like_base_ref_without_creating_output(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    output = tmp_path / "should-not-exist"

    with pytest.raises(ValueError, match="ref"):
        EvidenceCollector(tmp_path).collect_context(
            brief, base_ref=f"--output={output}",
        )

    assert not output.exists()


def test_review_rejects_option_like_head_ref(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    with pytest.raises(ValueError, match="ref"):
        EvidenceCollector(tmp_path).collect_review(
            brief, base_ref="HEAD", head_ref="--output=should-not-exist",
        )


def test_review_collection_uses_argv_and_captures_changed_files(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    brief.write_text("objective changed", encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")

    assert bundle.repository["dirtyFiles"] == ["docs/brief.md"]
    assert any(item.label.startswith("git-diff-file-") for item in bundle.commands)
    changed = next(item for item in bundle.commands if item.label == "git-diff-name-only")
    assert changed.argv[:2] == ("git", "diff")
    assert "--name-only" in changed.argv
    diff_commands = [item for item in bundle.commands if item.argv[:2] == ("git", "diff")]
    assert diff_commands
    assert all("--no-textconv" in item.argv for item in diff_commands)
    assert all("--no-ext-diff" in item.argv for item in diff_commands)
    from backend.agents.repository_scope import repository_git_pathspecs

    tracked_patch_commands = [
        item for item in diff_commands if item.label.startswith("git-diff-file-")
    ]
    for exclusion in repository_git_pathspecs()[1:]:
        assert all(exclusion in item.argv for item in tracked_patch_commands)
    assert all(":(literal)docs/brief.md" in item.argv for item in tracked_patch_commands)
    base = next(item for item in bundle.commands if item.label == "git-base")
    assert base.argv[:4] == ("git", "rev-parse", "--verify", "--end-of-options")
    assert len(bundle.repository["baseSha"]) == 40


def test_review_collection_treats_tracked_patch_paths_as_literal_pathspecs(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    brief = docs / "brief.md"
    wildcard_path = docs / "literal*.md"
    matching_path = docs / "literal-other.md"
    brief.write_text("objective\n", encoding="utf-8")
    wildcard_path.write_text("wildcard original\n", encoding="utf-8")
    matching_path.write_text("match original\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    wildcard_path.write_text("wildcard target\n", encoding="utf-8")
    matching_path.write_text("matching target\n", encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(
        brief, base_ref="HEAD", head_ref="WORKTREE",
    )

    wildcard_patches = [item for item in bundle.evidence if item.source == "docs/literal*.md"]
    assert wildcard_patches
    assert all("wildcard target" in item.content for item in wildcard_patches)
    assert all("matching target" not in item.content for item in wildcard_patches)
    patch_commands = [
        command for command in bundle.commands
        if command.label.startswith("git-diff-file-")
    ]
    assert any(":(literal)docs/literal*.md" in command.argv for command in patch_commands)
    assert all("." not in command.argv for command in patch_commands)


def test_repository_status_rejects_malformed_porcelain_record(tmp_path, monkeypatch):
    init_repo(tmp_path)
    write_configs(tmp_path)
    collector = EvidenceCollector(tmp_path)

    def malformed_status(label, argv, **kwargs):
        output = " Mfile.py" if label == "git-status" else "fixture"
        return CommandEvidence(label, tuple(argv), 0, output, "", truncated=False)

    monkeypatch.setattr(collector, "_run", malformed_status)

    with pytest.raises(ValueError, match="malformed git status record"):
        collector._repository()


def test_review_collection_excludes_summary_roots_from_inventory_and_patches(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "backend").mkdir()
    (tmp_path / "docs/Summay").mkdir()
    (tmp_path / "Summay").mkdir()
    brief = tmp_path / "docs/brief.md"
    allowed = tmp_path / "backend/allowed.py"
    summary = tmp_path / "docs/Summay/private.md"
    brief.write_text("objective", encoding="utf-8")
    allowed.write_text("VALUE = 'before'\n", encoding="utf-8")
    summary.write_text("private fixture before\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    allowed.write_text("VALUE = 'after'\n", encoding="utf-8")
    summary.write_text("private fixture after\n", encoding="utf-8")
    (tmp_path / "Summay/untracked.md").write_text("untracked private fixture\n", encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(
        brief, base_ref="HEAD", head_ref="WORKTREE",
    )

    assert bundle.repository["dirtyFiles"] == ["backend/allowed.py"]
    assert [item.source for item in bundle.evidence] == ["backend/allowed.py"]
    assert all("Summay/" not in item.source for item in bundle.evidence)
    assert all("private fixture" not in item.content for item in bundle.evidence)
    for label in ("git-status", "review-launch-worktree-fingerprint", "git-diff-name-only", "git-untracked-files"):
        command = next(item for item in bundle.commands if item.label == label)
        assert "Summay/private.md" not in command.stdout
        assert "Summay/untracked.md" not in command.stdout


def test_review_diff_uses_review_only_excerpt_budget_without_broadening_context(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path, review_command_characters=1200)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    changed = tmp_path / "docs/changed.md"
    changed.write_text("before", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    changed.write_text("x" * 500, encoding="utf-8")

    collector = EvidenceCollector(tmp_path)
    review = collector.collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")
    ordinary = collector._run("git-diff-ordinary", ["git", "diff", "--", "docs/changed.md"])

    patch = next(item for item in review.evidence if item.source == "docs/changed.md")
    assert patch.metadata["truncated"] is False
    assert "x" * 500 in patch.content
    assert ordinary.truncated is True
    assert len(ordinary.stdout) == 200


def test_review_launch_fingerprint_uses_full_status_when_excerpt_is_truncated(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path, review_command_characters=40)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    for index in range(20):
        (tmp_path / "docs" / f"changed-{index:02d}.md").write_text("new", encoding="utf-8")

    collector = EvidenceCollector(tmp_path)
    bundle = collector.collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")
    raw = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--no-renames", "-z", "--",
         ".", ":(exclude,icase,glob)**/Summay/**", ":(exclude,icase,glob)**/Summay", ":(exclude)docs/superpowers", ":(exclude).superpowers"],
        cwd=tmp_path, text=True, capture_output=True, check=True,
    ).stdout

    assert next(item for item in bundle.commands if item.label == "review-launch-worktree-fingerprint").truncated
    assert bundle.repository["reviewLaunchWorktreeFingerprint"] == __import__("hashlib").sha256(
        raw.encode()
    ).hexdigest()


def test_review_launch_fingerprint_excludes_forced_tracked_runtime_artifacts(tmp_path):
    from backend.agents.repository_scope import verification_source_git_pathspecs

    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / ".gitignore").write_text(".nbs_agent_runtime/\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "backend").mkdir()
    runtime = tmp_path / ".nbs_agent_runtime"
    runtime.mkdir()
    brief = tmp_path / "docs/brief.md"
    source = tmp_path / "backend/worker.py"
    artifact = runtime / "session.json"
    brief.write_text("objective\n", encoding="utf-8")
    source.write_text("VALUE = 1\n", encoding="utf-8")
    artifact.write_text('{"status":"sealed"}\n', encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", "agent_config", "docs", "backend"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-f", ".nbs_agent_runtime/session.json"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    source.write_text("VALUE = 2\n", encoding="utf-8")
    artifact.write_text('{"status":"review"}\n', encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")
    scoped = subprocess.run(
        [
            "git", "status", "--porcelain=v1", "--untracked-files=all", "--no-renames", "-z", "--",
            *verification_source_git_pathspecs(
                ":(exclude)docs/superpowers", ":(exclude).superpowers",
            ),
        ],
        cwd=tmp_path, capture_output=True, check=True,
    ).stdout

    assert bundle.repository["reviewLaunchWorktreeFingerprint"] == hashlib.sha256(scoped).hexdigest()


def test_full_review_command_capture_bounds_excerpt_and_hashes_complete_output(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    for index in range(20):
        (tmp_path / f"untracked-{index:02d}.md").write_text("content", encoding="utf-8")

    argv = ["git", "status", "--porcelain", "--untracked-files=all"]
    raw = subprocess.run(argv, cwd=tmp_path, capture_output=True, check=True).stdout
    streamed = []
    result = EvidenceCollector(tmp_path)._run_full_stdout(
        argv,
        stdout_consumer=streamed.append,
        max_stdout_bytes=32,
    )

    assert result.stdout_truncated is True
    assert result.stdout_bytes == len(raw)
    assert result.stdout_sha256 == hashlib.sha256(raw).hexdigest()
    assert len(result.stdout_excerpt.encode("utf-8")) <= 32
    assert "".join(streamed).encode("utf-8") == raw[:32]
    assert len(result.stdout_sha256) == 64


def test_review_collection_fails_closed_when_patch_capture_budget_is_exceeded(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    changed = tmp_path / "docs/changed.md"
    brief.write_text("objective", encoding="utf-8")
    changed.write_text("before", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    changed.write_text("after" * 100, encoding="utf-8")
    collector = EvidenceCollector(tmp_path)
    collector._MAX_REVIEW_EVIDENCE_BYTES = 48

    with pytest.raises(RuntimeError, match="bounded capture budget"):
        collector.collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")


def test_review_file_lists_use_review_excerpt_budget_for_tracked_and_untracked_paths(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path, review_command_characters=1200)
    (tmp_path / "docs" / "tracked").mkdir(parents=True)
    brief = tmp_path / "docs" / "brief.md"
    brief.write_text("objective", encoding="utf-8")
    tracked = []
    for index in range(20):
        path = tmp_path / "docs" / "tracked" / f"entry-{index:02d}.md"
        path.write_text("before", encoding="utf-8")
        tracked.append(path)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    for path in tracked:
        path.write_text("after", encoding="utf-8")
    (tmp_path / "docs" / "untracked").mkdir()
    for index in range(20):
        (tmp_path / "docs" / "untracked" / f"entry-{index:02d}.md").write_text(
            "new", encoding="utf-8",
        )

    bundle = EvidenceCollector(tmp_path).collect_review(
        brief, base_ref="HEAD", head_ref="WORKTREE",
    )

    changed = next(item for item in bundle.commands if item.label == "git-diff-name-only")
    untracked = next(item for item in bundle.commands if item.label == "git-untracked-files")
    assert len(changed.stdout) > 200
    assert len(untracked.stdout) > 200
    assert changed.truncated is False
    assert untracked.truncated is False
    assert len(bundle.evidence) == 40


def test_untracked_diff_inspection_is_recorded_as_successful_verification(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    (tmp_path / "docs/new-file.md").write_text("new evidence", encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")

    command = next(item for item in bundle.commands if item.label == "git-diff-untracked-file-0")
    patch = next(item for item in bundle.evidence if item.source == "docs/new-file.md")
    assert command.exit_code == 0
    assert patch.metadata["untracked"] is True


def test_review_collector_splits_large_patch_within_configured_excerpt_limit(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path, review_command_characters=24000)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    change = tmp_path / "docs/large.py"
    content = "+review-line-0123456789\n" * 1800
    change.write_text(content, encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")

    patches = [item for item in bundle.evidence if item.source == "docs/large.py"]
    full_diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--no-index", "--", "/dev/null", "docs/large.py"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    ).stdout
    assert len(patches) > 1
    assert "".join(item.content for item in patches) == full_diff
    assert all(len(item.content) <= 24000 for item in patches)
    assert all(item.metadata["truncated"] is False for item in patches)
    assert all(item.metadata["segmentCount"] == len(patches) for item in patches)


def test_review_collection_fails_closed_when_launch_worktree_probe_fails(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    collector = EvidenceCollector(tmp_path)
    from backend.agents.evidence_collector import FullCommandOutput

    collector._run_full_stdout = lambda argv, **kwargs: FullCommandOutput(
        returncode=128,
        stdout_excerpt="",
        stderr_excerpt="simulated git status failure",
        stdout_sha256="0" * 64,
        stdout_bytes=0,
        stdout_truncated=False,
    )

    with pytest.raises(RuntimeError, match="launch worktree probe failed"):
        collector.collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")


def test_review_diff_falls_back_to_ordinary_excerpt_budget_when_config_omits_review_limit(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    changed = tmp_path / "docs/changed.md"
    changed.write_text("before", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    changed.write_text("x" * 500, encoding="utf-8")

    policy = replace(EvidencePolicy.from_project(tmp_path), review_max_command_characters=None)
    review = EvidenceCollector(tmp_path, policy=policy).collect_review(
        brief, base_ref="HEAD", head_ref="WORKTREE",
    )

    patches = [item for item in review.evidence if item.source == "docs/changed.md"]
    patch_commands = [item for item in review.commands if item.label.startswith("git-diff-file-")]
    assert len(patches) > 1
    assert all(item.metadata["truncated"] is False for item in patches)
    assert all(len(item.content) <= 200 for item in patches)
    assert "x" * 500 in "".join(item.content for item in patches)
    assert len(patch_commands) == 1
    assert len(patch_commands[0].stdout) <= 200
    assert patch_commands[0].truncated is True


@pytest.mark.parametrize("head_ref", ["worktree", "working-tree", "working_tree", "WORKTREE"])
def test_review_collection_accepts_worktree_head_aliases(tmp_path, head_ref):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    brief.write_text("objective changed", encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(
        brief, base_ref="HEAD", head_ref=head_ref,
    )

    assert bundle.repository["headRef"] == "WORKTREE"
    assert any(item.source == "docs/brief.md" for item in bundle.evidence)


def test_review_collection_includes_allowlisted_untracked_files_but_not_denied_data(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "backend").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    untracked = tmp_path / "backend/new_agent.py"
    untracked.write_text("VALUE = 1\n", encoding="utf-8")
    denied = tmp_path / "backend/secret.db"
    denied.write_text("formal rows", encoding="utf-8")

    first = EvidenceCollector(tmp_path).collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")
    second = EvidenceCollector(tmp_path).collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")

    assert [item.source for item in first.evidence] == ["backend/new_agent.py"]
    assert first.repository["dirtyFiles"] == ["backend/new_agent.py", "backend/secret.db"]
    assert first.evidence[0].content == second.evidence[0].content
    assert "VALUE = 1" in first.evidence[0].content
    assert "formal rows" not in str(first.to_dict())


def test_review_collection_skips_tracked_process_reports_and_denied_data_without_reading_them(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "backend").mkdir()
    (tmp_path / ".superpowers/sdd").mkdir(parents=True)
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    report = tmp_path / ".superpowers/sdd/task-report.md"
    report.write_text("process-only report", encoding="utf-8")
    secret = tmp_path / "backend/secret.db"
    secret.write_text("formal rows", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    report.write_text("process-only report changed", encoding="utf-8")
    secret.write_text("formal rows changed", encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")

    assert bundle.repository["dirtyFiles"] == [".superpowers/sdd/task-report.md", "backend/secret.db"]
    assert not bundle.evidence
    serialized = str(bundle.to_dict())
    assert "process-only report" not in serialized
    assert "formal rows" not in serialized


def test_review_collection_marks_denied_superpowers_artifacts_as_preserved(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / ".superpowers/sdd").mkdir(parents=True)
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    report = tmp_path / ".superpowers/sdd/task-report.md"
    report.write_text("process-only report", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    report.write_text("process-only report changed", encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")

    assert bundle.repository["dirtyFiles"] == [".superpowers/sdd/task-report.md"]
    assert bundle.repository["preservedDirtyFiles"] == [".superpowers/sdd/task-report.md"]


def test_review_collection_only_preserves_explicit_process_path(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs/superpowers").mkdir(parents=True)
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    process_note = tmp_path / "docs/superpowers/plan.md"
    process_note.write_text("process-only", encoding="utf-8")

    collector = EvidenceCollector(tmp_path)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    source_identity = collector._review_source_probe(
        brief, base_sha=base_sha, head_ref="WORKTREE",
    )
    bundle = collector.collect_review(
        brief,
        base_ref="HEAD",
        head_ref="WORKTREE",
        preserve_dirty_paths=("docs/superpowers/plan.md",),
    )

    assert bundle.repository["preservedDirtyFiles"] == ["docs/superpowers/plan.md"]
    assert not bundle.evidence


def test_review_collection_only_preserves_explicit_tracked_process_path(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs/superpowers").mkdir(parents=True)
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    process_note = tmp_path / "docs/superpowers/process.md"
    process_note.write_text("process-only", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    process_note.write_text("process-only changed", encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(
        brief,
        base_ref="HEAD",
        head_ref="WORKTREE",
        preserve_dirty_paths=("docs/superpowers/process.md",),
    )

    assert bundle.repository["preservedDirtyFiles"] == ["docs/superpowers/process.md"]
    assert not bundle.evidence


def test_review_collection_preserves_explicit_allowlisted_dirty_file(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    user_file = tmp_path / "AGENTS.md"
    user_file.write_text("# local note\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    user_file.write_text("# local note changed\n", encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(
        brief,
        base_ref="HEAD",
        head_ref="WORKTREE",
        preserve_dirty_paths=("AGENTS.md",),
    )

    assert bundle.repository["preservedDirtyFiles"] == ["AGENTS.md"]
    assert not bundle.evidence


def test_review_collection_rejects_preserve_allowlist_outside_process_artifacts(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    with pytest.raises(PermissionError, match="allowlisted"):
        EvidenceCollector(tmp_path).collect_review(
            brief,
            base_ref="HEAD",
            head_ref="WORKTREE",
            preserve_dirty_paths=("outside.md",),
        )


def test_review_collection_rejects_preserve_source_file(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    user_file = tmp_path / "scripts/user-owned.py"
    user_file.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    user_file.write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="Tracked source and test files"):
        EvidenceCollector(tmp_path).collect_review(
            brief,
            base_ref="HEAD",
            head_ref="WORKTREE",
            preserve_dirty_paths=("scripts/user-owned.py",),
        )


def test_review_collection_omits_only_hash_pinned_tracked_carry_forward_files(tmp_path, monkeypatch):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "backend").mkdir()
    (tmp_path / "tests").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    carry = tmp_path / "tests/test_legacy.py"
    carry.write_text("legacy = 1\n", encoding="utf-8")
    source = tmp_path / "backend/new.py"
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    carry.write_text("legacy = 2\n", encoding="utf-8")
    source.write_text("value = 2\n", encoding="utf-8")
    carry_hash = hashlib.sha256(carry.read_bytes()).hexdigest()

    from backend.agents import evidence_collector

    tracked_lookups = []
    real_bounded_run = evidence_collector.run_bounded_subprocess

    def capture_tracked_lookup(argv, *args, **kwargs):
        if argv[:2] == ["git", "ls-files"] and "--error-unmatch" in argv:
            tracked_lookups.append((argv, kwargs))
        return real_bounded_run(argv, *args, **kwargs)

    monkeypatch.setattr(evidence_collector, "run_bounded_subprocess", capture_tracked_lookup)

    collector = EvidenceCollector(tmp_path)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    source_identity = collector._review_source_probe(
        brief, base_sha=base_sha, head_ref="WORKTREE",
    )
    bundle = collector.collect_review(
        brief,
        base_ref="HEAD",
        head_ref="WORKTREE",
        carry_forward_files=(("tests/test_legacy.py", carry_hash),),
    )

    assert [item.source for item in bundle.evidence] == ["backend/new.py"]
    assert bundle.repository["carryForwardFiles"] == [
        {"path": "tests/test_legacy.py", "sha256": carry_hash},
    ]
    assert "tests/test_legacy.py" in bundle.repository["dirtyFiles"]
    assert "tests/test_legacy.py" not in bundle.repository["preservedDirtyFiles"]
    assert bundle.repository["reviewLaunchWorktreeFingerprint"]
    from backend.agents.evidence_models import canonical_fingerprint

    assert bundle.repository["reviewWorktreeFingerprint"] == canonical_fingerprint({
        "sourceWorktreeFingerprint": source_identity["worktree_fingerprint"],
        "carryForwardFiles": [{"path": "tests/test_legacy.py", "sha256": carry_hash}],
    })
    from backend.agents.repository_scope import repository_git_pathspecs

    assert len(tracked_lookups) == 1
    argv, kwargs = tracked_lookups[0]
    assert list(repository_git_pathspecs()[1:]) == argv[-2:]
    assert kwargs["timeout_seconds"] <= 60
    assert kwargs["max_output_bytes"] <= 16 * 1024


def test_review_collection_rejects_more_than_50_carry_forward_files_before_repository_reads(tmp_path, monkeypatch):
    init_repo(tmp_path)
    write_configs(tmp_path)
    brief = tmp_path / "brief.md"
    brief.write_text("objective", encoding="utf-8")
    collector = EvidenceCollector(tmp_path)

    def unexpected_repository_read():
        pytest.fail("carry-forward limit must be checked before repository collection")

    monkeypatch.setattr(collector, "_repository", unexpected_repository_read)
    entries = tuple(
        (f"tests/test_{index}.py", "a" * 64)
        for index in range(51)
    )

    with pytest.raises(ValueError, match="maximum of 50"):
        collector.collect_review(brief, carry_forward_files=entries)


@pytest.mark.parametrize("mutation", ["hash", "untracked", "clean", "symlink"])
def test_review_collection_rejects_invalid_carry_forward_file(tmp_path, mutation):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    carry = tmp_path / "tests/test_legacy.py"
    carry.write_text("legacy = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    carry_hash = hashlib.sha256(carry.read_bytes()).hexdigest()
    carry.write_text("legacy = 2\n", encoding="utf-8")
    if mutation == "hash":
        carry_hash = "0" * 64
    elif mutation == "untracked":
        subprocess.run(["git", "rm", "--cached", "-q", "tests/test_legacy.py"], cwd=tmp_path, check=True)
    elif mutation == "clean":
        carry.write_text("legacy = 1\n", encoding="utf-8")
    elif mutation == "symlink":
        carry.unlink()
        carry.symlink_to(brief)

    with pytest.raises((PermissionError, ValueError), match="carry-forward"):
        EvidenceCollector(tmp_path).collect_review(
            brief,
            base_ref="HEAD",
            head_ref="WORKTREE",
            carry_forward_files=(("tests/test_legacy.py", carry_hash),),
        )


@pytest.mark.parametrize("control", ["\n", "\t", "\x1f"])
def test_review_rejects_control_character_carry_forward_before_git_identity_probe(tmp_path, monkeypatch, control):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    relative = f"tests/test_legacy{control}marker.py"
    carry = tmp_path / relative
    carry.write_text("legacy = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    carry.write_text("legacy = 2\n", encoding="utf-8")
    carry_hash = hashlib.sha256(carry.read_bytes()).hexdigest()
    real_run = subprocess.run

    def guarded_run(argv, *args, **kwargs):
        if argv[:2] == ["git", "ls-files"] and "--error-unmatch" in argv:
            raise AssertionError("unsafe carry-forward path reached the Git identity probe")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    with pytest.raises(PermissionError, match="carry-forward path is unsafe"):
        EvidenceCollector(tmp_path).collect_review(
            brief, base_ref="HEAD", head_ref="WORKTREE",
            carry_forward_files=((relative, carry_hash),),
        )


def test_review_collection_fails_if_carry_forward_changes_during_collection(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    carry = tmp_path / "tests/test_legacy.py"
    carry.write_text("legacy = 1\n", encoding="utf-8")
    source = tmp_path / "docs/changed.md"
    source.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    carry.write_text("legacy = 2\n", encoding="utf-8")
    source.write_text("after\n", encoding="utf-8")
    expected_hash = hashlib.sha256(carry.read_bytes()).hexdigest()
    collector = EvidenceCollector(tmp_path)
    run_full_stdout = collector._run_full_stdout

    def mutate_after_diff(argv, **kwargs):
        result = run_full_stdout(argv, **kwargs)
        if argv[:3] == ["git", "diff", "--no-textconv"] and any(
            value.endswith("docs/changed.md") for value in argv
        ):
            carry.write_text("legacy = 3\n", encoding="utf-8")
        return result

    collector._run_full_stdout = mutate_after_diff

    with pytest.raises(RuntimeError, match="carry-forward file changed during Review collection"):
        collector.collect_review(
            brief,
            base_ref="HEAD",
            head_ref="WORKTREE",
            carry_forward_files=(("tests/test_legacy.py", expected_hash),),
        )


def test_review_collection_rejects_worktree_drift_during_patch_collection(tmp_path, monkeypatch):
    init_repo(tmp_path)
    write_configs(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    brief = docs / "brief.md"
    changed = docs / "changed.md"
    other = docs / "other.md"
    brief.write_text("objective", encoding="utf-8")
    changed.write_text("before\n", encoding="utf-8")
    other.write_text("stable\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    changed.write_text("reviewed patch\n", encoding="utf-8")

    collector = EvidenceCollector(tmp_path)
    run_full_stdout = collector._run_full_stdout
    mutated = False

    def mutate_after_patch_capture(argv, **kwargs):
        nonlocal mutated
        result = run_full_stdout(argv, **kwargs)
        if (
            argv[:2] == ["git", "diff"]
            and any(value.endswith("docs/changed.md") for value in argv)
            and not mutated
        ):
            other.write_text("changed during collection\n", encoding="utf-8")
            mutated = True
        return result

    monkeypatch.setattr(collector, "_run_full_stdout", mutate_after_patch_capture)

    with pytest.raises(RuntimeError, match="source changed during Review collection"):
        collector.collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")

    assert mutated


def test_hash_pinned_carry_forward_uses_open_directory_descriptors_during_parent_swap(tmp_path, monkeypatch):
    import os

    from backend.agents import evidence_collector

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    source = tests_dir / "test_legacy.py"
    source.write_text("inside repository\n", encoding="utf-8")
    outside_dir = tmp_path / "outside-target"
    outside_dir.mkdir()
    (outside_dir / "test_legacy.py").write_text("outside repository\n", encoding="utf-8")
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    moved_dir = tmp_path / "tests-held"
    collector = EvidenceCollector.__new__(EvidenceCollector)
    collector.project_root_lexical = tmp_path
    collector.project_root = tmp_path
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if Path(path).name == "test_legacy.py" and not swapped:
            os.rename(tests_dir, moved_dir)
            tests_dir.symlink_to(outside_dir, target_is_directory=True)
            swapped = True
            try:
                return real_open(path, flags, *args, **kwargs)
            finally:
                tests_dir.unlink()
                os.rename(moved_dir, tests_dir)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(evidence_collector.os, "open", swapping_open)

    actual_hash = collector._hash_review_carry_forward_file("tests/test_legacy.py")

    assert swapped
    assert actual_hash == expected_hash
    assert not tests_dir.is_symlink()


def test_hash_pinned_carry_forward_rejects_symlinked_project_root(tmp_path):
    real_root = tmp_path / "real-repository"
    real_root.mkdir()
    (real_root / "tests").mkdir()
    write_configs(real_root)
    carry = real_root / "tests/test_legacy.py"
    carry.write_text("legacy = 1\n", encoding="utf-8")
    linked_root = tmp_path / "linked-repository"
    linked_root.symlink_to(real_root, target_is_directory=True)
    collector = EvidenceCollector(linked_root)

    with pytest.raises(PermissionError, match="safely"):
        collector._hash_review_carry_forward_file("tests/test_legacy.py")


def test_review_collection_allows_explicit_untracked_source_boundary(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    allowlist = tmp_path / "agent_config/evidence_allowlist.json"
    allowlist.write_text(
        allowlist.read_text(encoding="utf-8").replace(
            '"readRoots":["docs","backend","tests","Summay"]',
            '"readRoots":["docs","backend","scripts","tests","Summay"]',
        ),
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    user_file = tmp_path / "scripts/user-owned.py"
    user_file.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "docs/brief.md", "agent_config"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    bundle = EvidenceCollector(tmp_path).collect_review(
        brief,
        base_ref="HEAD",
        head_ref="WORKTREE",
        preserve_dirty_paths=("scripts/user-owned.py",),
    )

    assert bundle.repository["preservedDirtyFiles"] == ["scripts/user-owned.py"]
    assert not bundle.evidence


def test_review_collection_keeps_legal_tracked_root_source_while_skipping_process_and_sensitive_paths(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "backend").mkdir()
    (tmp_path / ".superpowers/sdd").mkdir(parents=True)
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    root_source = tmp_path / "worker.py"
    root_source.write_text("VALUE = 1\n", encoding="utf-8")
    root_note = tmp_path / "notes.md"
    root_note.write_text("process note", encoding="utf-8")
    report = tmp_path / ".superpowers/sdd/task-report.md"
    report.write_text("process-only report", encoding="utf-8")
    secret = tmp_path / "backend/secret.db"
    secret.write_text("formal rows", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    root_source.write_text("VALUE = 2\n", encoding="utf-8")
    root_note.write_text("process note changed", encoding="utf-8")
    report.write_text("process-only report changed", encoding="utf-8")
    secret.write_text("formal rows changed", encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")

    assert [item.source for item in bundle.evidence] == ["worker.py"]
    assert "+VALUE = 2" in bundle.evidence[0].content
    serialized = str(bundle.to_dict())
    assert "process note" not in serialized
    assert "process-only report" not in serialized
    assert "formal rows" not in serialized


def test_allowlisted_extensionless_root_file_is_readable(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / ".gitignore").write_text(".nbs_agent_runtime/\n", encoding="utf-8")

    policy = EvidencePolicy.from_project(tmp_path)

    assert policy.resolve_read_path(tmp_path / ".gitignore").name == ".gitignore"


def test_context_reports_deterministic_input_token_overflow(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path, context_input_tokens=5)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("a large enough objective to overflow the input budget", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    bundle = EvidenceCollector(tmp_path).collect_context(brief, base_ref="HEAD")

    assert bundle.repository["estimatedInputTokens"] > 5
    assert bundle.repository["contextOverflow"] is True


def test_command_output_truncation_is_visible(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    collector = EvidenceCollector(tmp_path)

    result = collector._run("git-log", ["git", "log", "--oneline"])

    assert len(result.stdout) <= 200
    assert isinstance(result.truncated, bool)


@pytest.mark.parametrize("label", [
    "git-status", "review-launch-worktree-fingerprint",
    "git-diff-name-only", "git-untracked-files",
])
def test_review_inventory_transport_fails_before_exceeding_shared_byte_budget(tmp_path, label):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/long-name-for-bounded-output.md").write_text("content", encoding="utf-8")
    collector = EvidenceCollector(tmp_path)
    collector._MAX_REVIEW_STATUS_BYTES = 8

    with pytest.raises(RuntimeError, match="bounded output budget"):
        collector._run(label, ["git", "ls-files", "--others", "--exclude-standard", "-z"])


def test_repository_collection_rejects_truncated_git_status(tmp_path, monkeypatch):
    from dataclasses import replace

    init_repo(tmp_path)
    write_configs(tmp_path)
    collector = EvidenceCollector(tmp_path)
    original_run = collector._run

    def truncated_status(label, argv, **kwargs):
        result = original_run(label, argv, **kwargs)
        if label == "git-status":
            return replace(result, stdout="?? partial", truncated=True)
        return result

    monkeypatch.setattr(collector, "_run", truncated_status)

    with pytest.raises(ValueError, match="git status output was truncated"):
        collector._repository()


def test_review_collection_rejects_truncated_untracked_enumeration(tmp_path, monkeypatch):
    from dataclasses import replace

    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    (tmp_path / "docs/new-file.md").write_text("complete path", encoding="utf-8")
    collector = EvidenceCollector(tmp_path)
    original_run = collector._run

    def truncated_untracked(label, argv, **kwargs):
        result = original_run(label, argv, **kwargs)
        if label == "git-untracked-files":
            return replace(result, stdout="docs/new-file.md\0docs/partial", truncated=True)
        return result

    monkeypatch.setattr(collector, "_run", truncated_untracked)

    with pytest.raises(RuntimeError, match="untracked path enumeration was truncated"):
        collector.collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")


def test_review_collection_rejects_truncated_tracked_diff_enumeration(tmp_path, monkeypatch):
    from dataclasses import replace

    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    tracked = tmp_path / "tracked.py"
    tracked.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    tracked.write_text("VALUE = 2\n", encoding="utf-8")
    collector = EvidenceCollector(tmp_path)
    original_run = collector._run

    def truncated_diff(label, argv, **kwargs):
        result = original_run(label, argv, **kwargs)
        if label == "git-diff-name-only":
            return replace(result, stdout="tracked.py", truncated=True)
        return result

    monkeypatch.setattr(collector, "_run", truncated_diff)

    with pytest.raises(RuntimeError, match="tracked diff file enumeration was truncated"):
        collector.collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")
