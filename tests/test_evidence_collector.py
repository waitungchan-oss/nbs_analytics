import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from backend.agents.evidence_collector import EvidenceCollector, EvidencePolicy


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
) -> None:
    (root / "agent_config").mkdir()
    (root / "agent_config/evidence_allowlist.json").write_text(
        '{"readRoots":["docs","backend","tests"],"rootFiles":["AGENTS.md",".gitignore"],'
        '"defaultContextFiles":[],"extensions":[".md",".py"],'
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

    def capture(label, argv):
        result = original_run(label, argv)
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

    def capture(label, argv):
        result = original_run(label, argv)
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
    base = next(item for item in bundle.commands if item.label == "git-base")
    assert base.argv[:4] == ("git", "rev-parse", "--verify", "--end-of-options")
    assert len(bundle.repository["baseSha"]) == 40


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
        ["git", "status", "--porcelain", "--untracked-files=all", "--",
         ".", ":(exclude)docs/superpowers", ":(exclude).superpowers"],
        cwd=tmp_path, text=True, capture_output=True, check=True,
    ).stdout

    assert next(item for item in bundle.commands if item.label == "review-launch-worktree-fingerprint").truncated
    assert bundle.repository["reviewLaunchWorktreeFingerprint"] == __import__("hashlib").sha256(
        raw.encode()
    ).hexdigest()


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

    bundle = EvidenceCollector(tmp_path).collect_review(
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


def test_review_collection_allows_explicit_untracked_source_boundary(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    allowlist = tmp_path / "agent_config/evidence_allowlist.json"
    allowlist.write_text(
        allowlist.read_text(encoding="utf-8").replace(
            '"readRoots":["docs","backend","tests"]',
            '"readRoots":["docs","backend","scripts","tests"]',
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
