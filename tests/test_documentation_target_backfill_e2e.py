import json
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path

from backend.agents.documentation_codex_runner import DocumentationRunnerResult
from backend.agents.documentation_evidence import _read_target_section
from backend.agents.documentation_policy import load_documentation_target
from backend.agents.documentation_workflow import DocumentationWorkflow
from backend.agents.verification_chain import VerificationChain, git_source_probe
from backend.agents.verification_session import VerificationSession
from backend.agents.workflow_models import (
    APPROVAL_SCHEMA,
    MANIFEST_SCHEMA,
    STATUS_SCHEMA,
    WorkflowApproval,
    WorkflowManifest,
    WorkflowStatus,
    canonical_sha256,
)
from backend.agents.workflow_store import WorkflowStore


SOURCE_ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "run-task-6-backfill"
TIMESTAMP = "2026-09-29T10:00:00+00:00"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _create_accepted_source(root: Path) -> str:
    files = {
        ".gitignore": ".nbs_agent_runtime/\n",
        "NBS_ANALYTICS_HANDOFF.md": (
            "# Handoff\n\n## 1. 本輪交接結論\n\nOld handoff conclusion.\n\n"
            "## 2. Next steps\n\nKeep this section unchanged.\n"
        ),
        "docs/agents/ACCEPTANCE_SHARD_ROLLOUT_RUNBOOK.md": (
            "# Shard rollout\n\n## Boundary\n\nOld shard boundary.\n\n"
            "## Execution\n\nKeep this section unchanged.\n"
        ),
        "docs/briefs/task-6.md": "Temporary accepted documentation source.\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for relative in (
        "docs/agents/REVIEW_AGENT_CONTRACT.md",
        "agent_config/token_budgets.json",
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE_ROOT / relative, destination)

    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Documentation E2E")
    _git(root, "config", "user.email", "docs-e2e@example.invalid")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "accepted temporary documentation source")
    return _git(root, "rev-parse", "HEAD")


def _seed_accepted_run(root: Path, commit_sha: str) -> str:
    brief_path = "docs/briefs/task-6.md"

    def source_probe():
        return git_source_probe(
            root,
            brief_path=brief_path,
            base_sha=commit_sha,
            head_ref="WORKTREE",
            contract_path="docs/agents/REVIEW_AGENT_CONTRACT.md",
            policy_path="agent_config/token_budgets.json",
        )

    current = source_probe()
    session = VerificationSession.create(
        project_id="nbs_analytics",
        base_sha=commit_sha,
        head_sha=current["head_sha"],
        brief_path=brief_path,
        brief_fingerprint=current["brief_fingerprint"],
        worktree_fingerprint=current["worktree_fingerprint"],
        diff_fingerprint=current["diff_fingerprint"],
        contract_fingerprint=current["contract_fingerprint"],
        policy_fingerprint=current["policy_fingerprint"],
    )
    VerificationChain.seal(
        session,
        runtime_root=root / ".nbs_agent_runtime/verification_sessions" / session.session_id,
        source_probe=source_probe,
    )

    store = WorkflowStore(root)
    store.create_run(
        WorkflowManifest(
            MANIFEST_SCHEMA, RUN_ID, brief_path,
            sha256((root / brief_path).read_bytes()).hexdigest(),
            "codex/documentation-e2e", commit_sha, (), TIMESTAMP, "a" * 64,
        ),
        WorkflowStatus(
            STATUS_SCHEMA, RUN_ID, "completed", "completed", TIMESTAMP,
            TIMESTAMP, TIMESTAMP, "temporary accepted source", None, 0,
        ),
    )
    store.write_approval(RUN_ID, WorkflowApproval(
        APPROVAL_SCHEMA, RUN_ID, "task-6-contract.json", "b" * 64,
        commit_sha, TIMESTAMP, "approved",
    ))
    for name, status in (
        ("review.json", {"status": "pass", "verdict": "pass"}),
        ("full-verification.json", {"status": "pass"}),
        ("hermes.json", {"overallStatus": "pass"}),
    ):
        store.write_artifact(RUN_ID, name, {
            **status, "commitSha": commit_sha,
            "sourceFingerprint": session.source_fingerprint,
        })
    return session.source_fingerprint


class _DraftRunner:
    def __init__(self, fragment: str):
        self.fragment = fragment

    def run(self, _argv, *, input_text, timeout_seconds, max_output_bytes):
        evidence = json.loads(input_text)
        unsigned = {
            "schemaVersion": "documentation-draft-v2",
            "evidenceFingerprint": evidence["evidenceFingerprint"],
            "status": "ready",
            "proposals": [{
                "targetId": evidence["selectedTargetId"],
                "content": self.fragment,
            }],
        }
        payload = {**unsigned, "draftFingerprint": canonical_sha256(unsigned)}
        return DocumentationRunnerResult(
            0, json.dumps(payload, ensure_ascii=False), "", 1,
        )


def test_verified_v2_backfill_applies_only_explicitly_approved_targets(tmp_path):
    base = tmp_path / "accepted-source"
    base.mkdir()
    accepted_commit = _create_accepted_source(base)
    cases = (
        (
            "handoff.current-conclusion",
            "runbook.shard-boundary",
            "Verified handoff conclusion.",
        ),
        (
            "runbook.shard-boundary",
            "handoff.current-conclusion",
            "Verified shard boundary.",
        ),
    )
    source_fingerprints = []

    for clone_name, (target_id, other_target_id, fragment) in zip(
        ("handoff-checkout", "runbook-checkout"), cases,
    ):
        project = tmp_path / clone_name
        subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(base), str(project)],
            check=True, capture_output=True, text=True,
        )
        assert _git(project, "rev-parse", "HEAD") == accepted_commit
        source_fingerprints.append(_seed_accepted_run(project, accepted_commit))

        target = load_documentation_target(target_id)
        other_target = load_documentation_target(other_target_id)
        target_path = project / target.repo_path
        other_path = project / other_target.repo_path
        before_files = {
            path: path.read_bytes()
            for path in {target_path, other_path}
        }
        _, before_text, _, start, end = _read_target_section(project, target)

        workflow = DocumentationWorkflow(
            project, runner=_DraftRunner(fragment), store=WorkflowStore(project),
        )
        preview = workflow.run(RUN_ID, agent_command="codex", target_id=target_id)
        assert preview["status"] == "preview_ready"
        assert preview["targetId"] == target_id
        assert preview["proposal"]["targetId"] == target_id
        assert {path: path.read_bytes() for path in before_files} == before_files

        mismatched = workflow.run(
            RUN_ID, agent_command=None, target_id=target_id,
            approve_target_id=other_target_id,
        )
        assert mismatched["status"] == "blocked"
        assert {path: path.read_bytes() for path in before_files} == before_files

        application = workflow.run(
            RUN_ID, agent_command=None, target_id=target_id,
            approve_target_id=target_id,
        )
        assert application["result"] == "applied"
        after_text = target_path.read_text(encoding="utf-8")
        replacement = preview["proposal"]["content"]
        assert after_text == before_text[:start] + replacement + before_text[end:]
        assert other_path.read_bytes() == before_files[other_path]

        artifact_dir = (
            project / ".nbs_agent_runtime/runs" / RUN_ID / "documentation"
            / "targets" / target_id
        )
        assert {path.name for path in artifact_dir.iterdir()} == {
            "documentation-evidence-v2.json",
            "documentation-proposal-v2.json",
            "documentation-preview-v2.json",
            "documentation-application-v2.json",
        }
        assert not (project / "nbs_marketing_data.db").exists()
        assert not (project / "Obsidian").exists()

    assert len(set(source_fingerprints)) == 1
