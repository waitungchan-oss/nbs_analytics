import subprocess
from pathlib import Path

import pytest

from backend.agents.evidence_collector import EvidenceCollector, EvidencePolicy


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def write_configs(root: Path, *, context_input_tokens: int = 12000) -> None:
    (root / "agent_config").mkdir()
    (root / "agent_config/evidence_allowlist.json").write_text(
        '{"readRoots":["docs","backend","tests"],"rootFiles":["AGENTS.md"],'
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
