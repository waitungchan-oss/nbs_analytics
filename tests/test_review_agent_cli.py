import json

from scripts.review_agent import _parser
from scripts.review_agent import _verification_freshness_diagnostics
from scripts import review_agent


def test_review_cli_exposes_runner_preflight_options():
    args = _parser().parse_args([
        "--brief", "docs/brief.md",
        "--agent-command", "codex exec --json -m gpt-5.4",
        "--runner-cache", "/tmp/models_cache.json",
        "--runner-model", "gpt-5.4",
    ])

    assert args.runner_cache == "/tmp/models_cache.json"
    assert args.runner_model == "gpt-5.4"


def test_review_cli_accepts_explicit_dirty_path_allowlist():
    args = _parser().parse_args([
        "--brief", "docs/brief.md",
        "--preserve-dirty-path", "docs/superpowers/plan.md",
    ])

    assert args.preserve_dirty_path == ["docs/superpowers/plan.md"]


def test_review_cli_allows_model_inference_for_absolute_runner():
    args = _parser().parse_args([
        "--brief", "docs/brief.md",
        "--agent-command", "/opt/codex exec --json --model gpt-5.4",
    ])

    command = args.agent_command.split()
    model = next(command[index + 1] for index, value in enumerate(command[:-1]) if value == "--model")
    assert model == "gpt-5.4"


def test_strict_cli_blocks_mismatched_approved_brief(capsys):
    brief = "docs/briefs/2026-08-28-strict-review-runner-runtime-recovery-brief.md"

    result = review_agent.main([
        "--brief", brief,
        "--approved-brief", "docs/briefs/other-approved-brief.md",
        "--agent-command", "codex",
        "--context", ".nbs_agent_runtime/reports/current-context-summary.json",
        "--strict", "--format", "json",
    ])

    assert result == 2
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "blocked"


def test_strict_cli_blocks_without_verification_bundle(capsys):
    brief = "docs/briefs/2026-08-28-strict-review-runner-runtime-recovery-brief.md"

    result = review_agent.main([
        "--brief", brief,
        "--approved-brief", brief,
        "--agent-command", "codex",
        "--context", ".nbs_agent_runtime/reports/current-context-summary.json",
        "--strict", "--format", "json",
    ])

    assert result == 2
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "blocked"
    assert any("verification" in item for item in report["residualRisk"])


def test_verification_freshness_requires_current_provenance():
    class Bundle:
        repository = {"headSha": "a" * 40}

    diagnostics = _verification_freshness_diagnostics(
        Bundle(),
        [{"label": "targeted", "argv": ["pytest"], "exitCode": 0, "stdoutTail": "", "stderrTail": ""}],
        brief_sha="b" * 64,
        worktree_fingerprint="c" * 64,
        current_head_sha="a" * 40,
        brief_path="docs/brief.md",
    )

    assert diagnostics
    assert "provenance" in diagnostics[0].lower()


def test_verification_freshness_accepts_standard_shasum_output():
    class Bundle:
        repository = {"headSha": "a" * 40}

    digest = "b" * 64
    diagnostics = _verification_freshness_diagnostics(
        Bundle(),
        [
            {"label": "review-head-fingerprint", "argv": ["git", "rev-parse", "HEAD"], "exitCode": 0, "stdoutTail": "a" * 40, "stderrTail": ""},
            {"label": "review-brief-fingerprint", "argv": ["shasum", "-a", "256", "docs/brief.md"], "exitCode": 0, "stdoutTail": f"{digest}  docs/brief.md", "stderrTail": ""},
            {"label": "review-worktree-fingerprint", "argv": ["sh", "-c", "git status --porcelain --untracked-files=all -- . ':(exclude)docs/superpowers' ':(exclude).superpowers' | shasum -a 256"], "exitCode": 0, "stdoutTail": digest, "stderrTail": ""},
        ],
        brief_sha=digest,
        worktree_fingerprint=digest,
        current_head_sha="a" * 40,
        brief_path="docs/brief.md",
    )

    assert diagnostics == []
