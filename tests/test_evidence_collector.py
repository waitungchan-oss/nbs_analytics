import subprocess
from pathlib import Path

import pytest

from backend.agents.evidence_collector import EvidenceCollector, EvidencePolicy


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def write_configs(root: Path, *, context_input_tokens: int = 12000) -> None:
    (root / "agent_config").mkdir()
    (root / "agent_config/evidence_allowlist.json").write_text(
        '{"readRoots":["docs","backend","tests"],"rootFiles":["AGENTS.md",".gitignore"],'
        '"defaultContextFiles":[],"extensions":[".md",".py"],'
        '"denyPatterns":["*.db",".env","outputs/**"],"agentExecutables":["codex"]}',
        encoding="utf-8",
    )
    (root / "agent_config/token_budgets.json").write_text(
        f'{{"context":{{"inputTokens":{context_input_tokens},"outputTokens":1500}},'
        '"review":{"inputTokens":16000,"outputTokens":2000},'
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
    (tmp_path / "docs").mkdir()
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
    report = tmp_path / ".superpowers/sdd/task-report.md"
    report.write_text("process-only report", encoding="utf-8")
    secret = tmp_path / "backend/secret.db"
    secret.write_text("formal rows", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    root_source.write_text("VALUE = 2\n", encoding="utf-8")
    report.write_text("process-only report changed", encoding="utf-8")
    secret.write_text("formal rows changed", encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")

    assert [item.source for item in bundle.evidence] == ["worker.py"]
    assert "+VALUE = 2" in bundle.evidence[0].content
    serialized = str(bundle.to_dict())
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
