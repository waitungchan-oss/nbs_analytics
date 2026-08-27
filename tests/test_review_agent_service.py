import pytest

from backend.agents.evidence_models import EvidenceBundle, EvidenceItem, canonical_fingerprint
from backend.agents.memory_hub_integration_models import build_memory_hub_integration_evidence
from backend.agents.review_agent_service import (
    build_review_evidence_payload,
    build_review_report,
    merge_review_batches,
    split_review_bundle_by_file,
)


class ReviewRunner:
    def __init__(self, verdict="pass", findings=None):
        self.verdict = verdict
        self.findings = findings or []
        self.last_payload = None
        self.calls = 0

    def run(self, payload):
        self.calls += 1
        self.last_payload = payload
        return {
            "schemaVersion": "review-report-v1",
            "verdict": self.verdict,
            "findings": self.findings,
            "requirementCoverage": ["objective"],
            "testCoverage": ["targeted: passed"],
            "baselineRisk": "none",
            "residualRisk": ["Hermes pending"],
            "hermesRequiredChecks": ["phase2-baseline"],
            "reviewFingerprint": payload["bundleFingerprint"],
        }


def review_bundle(dirty=None, content="+change"):
    return EvidenceBundle(
        schema_version="review-evidence-v1",
        task={"id": "x", "objective": "approved", "scope": ["backend"], "forbidden": []},
        repository={
            "branch": "feature", "head": "abc", "headRef": "WORKTREE", "base": "HEAD",
            "baseSha": "a" * 40, "dirtyFiles": dirty or [],
        },
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=(EvidenceItem(kind="diff", source="x.py", content=content),),
    )


def verification():
    return [{
        "label": "targeted", "argv": ["python", "-m", "pytest"], "exitCode": 0,
        "stdoutTail": "1 passed", "stderrTail": "",
    }]


def runtime_path(project_root):
    return project_root / ".nbs_agent_runtime"


def context_summary(status="ready"):
    return {
        "schemaVersion": "context-summary-v1",
        "status": status,
        "taskUnderstanding": ["objective"],
        "systemBoundaries": ["read-only"],
        "relevantFiles": [],
        "dependencies": [],
        "recommendedTests": ["targeted"],
        "risks": [],
        "unknowns": [],
        "contextFingerprint": "context-fingerprint",
    }


def memory_evidence(*, status="ready", consumer_id="context-agent", hint_count=1):
    return build_memory_hub_integration_evidence(
        project_id="nbs-analytics", consumer_id=consumer_id,
        integration_mode="direct_query", status=status, reason="ok",
        query_fingerprint="a" * 64 if status == "ready" else None,
        hints_fingerprint="b" * 64 if status == "ready" else None,
        policy_decision_fingerprints=("c" * 64,), source_refs=("docs/brief.md",),
        hint_count=hint_count, generated_at="2026-08-18T00:00:00+00:00",
    ).to_dict()


def test_review_optional_memory_observation_is_bounded_and_verdict_independent(tmp_path):
    without = build_review_report(
        review_bundle(), context_summary=context_summary(), verification=verification(),
        project_root=tmp_path, runner=ReviewRunner(), runtime_root=runtime_path(tmp_path),
        instructions="contract", strict=True,
    )
    with_memory = build_review_report(
        review_bundle(), context_summary=context_summary(), verification=verification(),
        project_root=tmp_path, runner=ReviewRunner(), runtime_root=runtime_path(tmp_path),
        instructions="contract", strict=True, memory_hub_evidence=memory_evidence(),
    )
    assert with_memory["verdict"] == without["verdict"] == "pass"
    assert with_memory["requirementCoverage"] == without["requirementCoverage"]
    observation = with_memory["memoryHubContext"]
    assert set(observation) == {"schemaVersion", "status", "consumerId", "integrationMode", "authority", "evidenceFingerprint", "hintCount", "diagnostics"}
    assert observation["status"] == "ready"
    assert observation["hintCount"] == 1


def test_review_invalid_memory_is_ignored_without_blocking_canonical_review(tmp_path):
    evidence = memory_evidence(consumer_id="other-agent")
    report = build_review_report(
        review_bundle(), context_summary=context_summary(), verification=verification(),
        project_root=tmp_path, runner=ReviewRunner(), runtime_root=runtime_path(tmp_path),
        instructions="contract", strict=True, memory_hub_evidence=evidence,
    )
    assert report["verdict"] == "pass"
    assert report["memoryHubContext"]["status"] == "ignored"
    assert report["memoryHubContext"]["diagnostics"] == ["consumer_mismatch"]


def test_review_stale_memory_is_ignored(tmp_path):
    report = build_review_report(
        review_bundle(), context_summary=context_summary(), verification=verification(),
        project_root=tmp_path, runner=ReviewRunner(), runtime_root=runtime_path(tmp_path),
        instructions="contract", strict=True, memory_hub_evidence=memory_evidence(status="degraded", hint_count=0),
    )
    assert report["verdict"] == "pass"
    assert report["memoryHubContext"]["status"] == "ignored"
    assert report["memoryHubContext"]["diagnostics"] == ["evidence_not_ready"]


def test_review_payload_has_exact_public_contract():
    payload = build_review_evidence_payload(
        review_bundle(), context_summary=context_summary(), verification=verification(),
    )
    assert set(payload) == {"schemaVersion", "taskContract", "contextSummary", "gitDiff", "verification", "bundleFingerprint"}
    assert set(payload["gitDiff"]) == {
        "base", "head", "files", "patches", "truncated", "diffFingerprint",
    }


def test_review_payload_git_diff_fingerprint_is_deterministic_and_content_bound():
    payload = build_review_evidence_payload(
        review_bundle(), context_summary=context_summary(), verification=verification(),
    )
    git_diff = payload["gitDiff"]
    unsigned_git_diff = {key: value for key, value in git_diff.items() if key != "diffFingerprint"}
    assert git_diff["diffFingerprint"] == canonical_fingerprint(unsigned_git_diff)
    assert len(git_diff["diffFingerprint"]) == 64
    assert build_review_evidence_payload(
        review_bundle(), context_summary=context_summary(), verification=verification(),
    )["gitDiff"]["diffFingerprint"] == git_diff["diffFingerprint"]
    changed = build_review_evidence_payload(
        review_bundle(content="+different"), context_summary=context_summary(), verification=verification(),
    )
    assert changed["gitDiff"]["diffFingerprint"] != git_diff["diffFingerprint"]


def test_review_payload_uses_resolved_base_and_committed_head_identity():
    bundle = review_bundle()
    bundle = EvidenceBundle(
        schema_version=bundle.schema_version,
        task=bundle.task,
        repository={
            **bundle.repository,
            "base": "d42c310",
            "baseSha": "b" * 40,
            "headRef": "bd03127",
            "headSha": "c" * 40,
        },
        guardrails=bundle.guardrails,
        evidence=bundle.evidence,
    )

    git_diff = build_review_evidence_payload(
        bundle, context_summary=context_summary(), verification=verification(),
    )["gitDiff"]

    assert git_diff["base"] == "b" * 40
    assert git_diff["head"] == "c" * 40


def test_review_runtime_instructions_bind_output_fingerprint_to_input_bundle(tmp_path):
    runner = ReviewRunner()

    build_review_report(
        review_bundle(), context_summary=context_summary(), verification=verification(),
        project_root=tmp_path, runner=runner,
        runtime_root=tmp_path / ".nbs_agent_runtime",
        instructions="review-contract-v1", strict=True,
    )

    runtime_instructions = runner.last_payload["instructions"]
    assert "reviewFingerprint" in runtime_instructions
    assert "payload.bundleFingerprint" in runtime_instructions


def test_strict_review_blocks_pass_without_verification(tmp_path):
    report = build_review_report(
        review_bundle(), context_summary=context_summary(), verification=[],
        project_root=tmp_path, runner=ReviewRunner(), runtime_root=runtime_path(tmp_path),
        instructions="review-contract-v1", strict=True,
    )
    assert report["verdict"] == "blocked"


def test_strict_review_blocks_context_that_is_not_ready(tmp_path):
    runner = ReviewRunner()
    report = build_review_report(
        review_bundle(), context_summary=context_summary("context_overflow"), verification=verification(),
        project_root=tmp_path, runner=runner, runtime_root=runtime_path(tmp_path), instructions="review-contract-v1", strict=True,
    )
    assert report["verdict"] == "blocked"
    assert runner.calls == 0


def test_strict_review_rejects_nonzero_verification_before_runner(tmp_path):
    runner = ReviewRunner()
    report = build_review_report(
        review_bundle(), context_summary=context_summary(),
        verification=[{
            "label": "targeted", "argv": ["pytest"], "exitCode": 1,
            "stdoutTail": "", "stderrTail": "failed",
        }], runner=runner,
        project_root=tmp_path, runtime_root=runtime_path(tmp_path), instructions="review-contract-v1", strict=True,
    )
    assert report["verdict"] == "changes_required"
    assert runner.calls == 0


def test_strict_review_accepts_pass_and_keeps_hermes_fields(tmp_path):
    runner = ReviewRunner()
    report = build_review_report(
        review_bundle(), context_summary=context_summary(), verification=verification(),
        project_root=tmp_path, runner=runner, runtime_root=runtime_path(tmp_path), instructions="review-contract-v1", strict=True,
    )
    assert report["verdict"] == "pass"
    assert report["residualRisk"] == ["Hermes pending"]
    assert report["hermesRequiredChecks"] == ["phase2-baseline"]
    assert set(runner.last_payload["evidence"]) == {
        "schemaVersion", "taskContract", "contextSummary", "gitDiff", "verification", "bundleFingerprint",
    }


def test_strict_review_allows_dirty_files_attributed_to_diff(tmp_path):
    report = build_review_report(
        review_bundle(dirty=["x.py"]), context_summary=context_summary(),
        verification=verification(), project_root=tmp_path, runner=ReviewRunner(), runtime_root=runtime_path(tmp_path),
        instructions="contract", strict=True,
    )
    assert report["verdict"] == "pass"


def test_strict_review_blocks_unattributed_dirty_file(tmp_path):
    report = build_review_report(
        review_bundle(dirty=["unrelated.py"]), context_summary=context_summary(),
        verification=verification(), project_root=tmp_path, runner=ReviewRunner(), runtime_root=runtime_path(tmp_path),
        instructions="contract", strict=True,
    )
    assert report["verdict"] == "blocked"


def test_strict_review_allows_explicitly_preserved_process_artifact(tmp_path):
    bundle = review_bundle(dirty=[".superpowers/sdd/task-report.md"])
    bundle = EvidenceBundle(
        schema_version=bundle.schema_version, task=bundle.task,
        repository={
            **bundle.repository,
            "preservedDirtyFiles": [".superpowers/sdd/task-report.md"],
        },
        guardrails=bundle.guardrails, evidence=bundle.evidence,
    )
    report = build_review_report(
        bundle, context_summary=context_summary(), verification=verification(),
        project_root=tmp_path, runner=ReviewRunner(), runtime_root=runtime_path(tmp_path),
        instructions="contract", strict=True,
    )
    assert report["verdict"] == "pass"


def test_strict_review_rejects_truncated_evidence(tmp_path):
    bundle = review_bundle()
    bundle = EvidenceBundle(
        schema_version=bundle.schema_version, task=bundle.task,
        repository=bundle.repository, guardrails=bundle.guardrails,
        evidence=(EvidenceItem(kind="diff", source="x.py", content="+change", metadata={"truncated": True}),),
    )
    report = build_review_report(
        bundle, context_summary=context_summary(), verification=verification(),
        project_root=tmp_path, runner=ReviewRunner(), runtime_root=runtime_path(tmp_path), instructions="contract", strict=True,
    )
    assert report["verdict"] == "context_overflow"


def test_batch_merge_preserves_high_findings_and_deduplicates():
    finding = {"severity": "high", "file": "x.py", "line": 3, "rule": "bug", "evidence": "x", "impact": "y", "recommendedAction": "z"}
    merged = merge_review_batches([
        {"verdict": "pass", "findings": [], "residualRisk": []},
        {"verdict": "changes_required", "findings": [finding, dict(finding)], "residualRisk": []},
    ], fingerprint="abc")
    assert merged["verdict"] == "changes_required"
    assert merged["findings"] == [finding]
    assert merged["reviewFingerprint"] == "abc"


def test_large_review_bundle_splits_only_between_files():
    bundle = EvidenceBundle(
        schema_version="review-evidence-v1",
        task={"id": "x", "objective": "approved", "scope": [], "forbidden": []},
        repository={"branch": "feature", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=tuple(EvidenceItem(kind="diff", source=f"file-{index}.py", content="x" * 40) for index in range(3)),
    )
    batches = split_review_bundle_by_file(bundle, patch_token_budget=10)
    assert len(batches) == 3
    assert [batch.evidence[0].source for batch in batches] == ["file-0.py", "file-1.py", "file-2.py"]


def test_strict_review_batches_scope_known_dirty_files_and_keep_unattributed_dirty_blocked(tmp_path):
    bundle = EvidenceBundle(
        schema_version="review-evidence-v1",
        task={"id": "x", "objective": "approved", "scope": [], "forbidden": []},
        repository={
            "branch": "feature", "head": "abc", "headRef": "WORKTREE", "base": "HEAD",
            "dirtyFiles": ["one.py", "two.py"],
        },
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=(
            EvidenceItem(kind="diff", source="one.py", content="x" * 40),
            EvidenceItem(kind="diff", source="two.py", content="x" * 40),
        ),
    )

    batches = split_review_bundle_by_file(bundle, patch_token_budget=10)
    reports = [
        build_review_report(
            batch, context_summary=context_summary(), verification=verification(),
            project_root=tmp_path, runner=ReviewRunner(), runtime_root=runtime_path(tmp_path),
            instructions="contract", strict=True,
        )
        for batch in batches
    ]

    assert [report["verdict"] for report in reports] == ["pass", "pass"]

    unsafe_bundle = EvidenceBundle(
        schema_version=bundle.schema_version,
        task=bundle.task,
        repository={**bundle.repository, "dirtyFiles": ["one.py", "two.py", "unrelated.py"]},
        guardrails=bundle.guardrails,
        evidence=bundle.evidence,
    )
    unsafe_batch = split_review_bundle_by_file(unsafe_bundle, patch_token_budget=10)[0]
    unsafe_report = build_review_report(
        unsafe_batch, context_summary=context_summary(), verification=verification(),
        project_root=tmp_path, runner=ReviewRunner(), runtime_root=runtime_path(tmp_path),
        instructions="contract", strict=True,
    )

    assert unsafe_report["verdict"] == "blocked"
    assert "unrelated.py" in unsafe_report["residualRisk"][0]


def test_single_file_over_budget_returns_overflow_before_runner(tmp_path):
    runner = ReviewRunner()
    report = build_review_report(
        review_bundle(content="x" * 10000), context_summary=context_summary(),
        verification=verification(), project_root=tmp_path, runner=runner, runtime_root=runtime_path(tmp_path),
        instructions="review-contract-v1", input_token_limit=10, strict=True,
    )
    assert report["verdict"] == "context_overflow"
    assert runner.calls == 0


def test_review_rejects_output_over_budget(tmp_path):
    class VerboseReviewRunner(ReviewRunner):
        def run(self, payload):
            report = super().run(payload)
            report["residualRisk"] = ["x" * 1000]
            return report

    with pytest.raises(ValueError, match="output token budget"):
        build_review_report(
            review_bundle(), context_summary=context_summary(), verification=verification(),
            project_root=tmp_path, runner=VerboseReviewRunner(), runtime_root=runtime_path(tmp_path),
            instructions="review-contract-v1", output_token_limit=10, strict=True,
        )


def test_review_rejects_malformed_runner_fingerprint(tmp_path):
    class BadRunner(ReviewRunner):
        def run(self, payload):
            result = super().run(payload)
            result["reviewFingerprint"] = "wrong"
            return result

    with pytest.raises(ValueError, match="fingerprint"):
        build_review_report(
            review_bundle(), context_summary=context_summary(), verification=verification(),
            project_root=tmp_path, runner=BadRunner(), runtime_root=runtime_path(tmp_path), instructions="contract", strict=True,
        )


@pytest.mark.parametrize(
    "task",
    [
        {"id": "x", "objective": "", "scope": [], "forbidden": []},
        {"id": "x", "objective": "ok", "scope": "backend", "forbidden": []},
        {"id": "x", "objective": "ok", "scope": [1], "forbidden": []},
        {"id": "x", "objective": "ok", "scope": [], "forbidden": [1]},
    ],
)
def test_review_rejects_malformed_task_contract(task):
    bundle = review_bundle()
    malformed = EvidenceBundle(
        schema_version=bundle.schema_version, task=task, repository=bundle.repository,
        guardrails=bundle.guardrails, evidence=bundle.evidence,
    )
    with pytest.raises(ValueError, match="task"):
        build_review_evidence_payload(
            malformed, context_summary=context_summary(), verification=verification(),
        )


@pytest.mark.parametrize(
    "summary",
    [
        {"schemaVersion": "context-summary-v1", "status": "ready"},
        {**context_summary(), "schemaVersion": "wrong"},
        {**context_summary(), "contextFingerprint": ""},
        {**context_summary(), "risks": "none"},
    ],
)
def test_strict_review_rejects_malformed_context_summary(tmp_path, summary):
    with pytest.raises(ValueError, match="Context summary"):
        build_review_report(
            review_bundle(), context_summary=summary, verification=verification(),
            project_root=tmp_path, runner=ReviewRunner(), runtime_root=runtime_path(tmp_path), instructions="contract", strict=True,
        )


@pytest.mark.parametrize(
    "command",
    [
        {"label": "targeted", "argv": [], "exitCode": 0},
        {
            "label": "targeted", "argv": ["pytest"], "exitCode": True,
            "stdoutTail": "", "stderrTail": "",
        },
        {
            "label": "targeted", "argv": ["pytest"], "exitCode": "0",
            "stdoutTail": "", "stderrTail": "",
        },
    ],
)
def test_review_rejects_malformed_verification_command(command):
    with pytest.raises(ValueError, match="verification"):
        build_review_evidence_payload(
            review_bundle(), context_summary=context_summary(), verification=[command],
        )


@pytest.mark.parametrize(
    "field",
    ["requirementCoverage", "testCoverage", "residualRisk", "hermesRequiredChecks"],
)
def test_strict_pass_requires_nonempty_gate_fields(tmp_path, field):
    class IncompletePassRunner(ReviewRunner):
        def run(self, payload):
            report = super().run(payload)
            report[field] = []
            return report

    with pytest.raises(ValueError, match=field):
        build_review_report(
            review_bundle(), context_summary=context_summary(), verification=verification(),
            project_root=tmp_path, runner=IncompletePassRunner(), runtime_root=runtime_path(tmp_path),
            instructions="contract", strict=True,
        )


def test_review_runtime_retries_after_invalid_fresh_output(tmp_path):
    class FlakyRunner(ReviewRunner):
        def run(self, payload):
            report = super().run(payload)
            if self.calls == 1:
                report["reviewFingerprint"] = "wrong"
            return report

    runner = FlakyRunner()
    with pytest.raises(ValueError, match="fingerprint"):
        build_review_report(
            review_bundle(), context_summary=context_summary(), verification=verification(),
            project_root=tmp_path, runner=runner, runtime_root=runtime_path(tmp_path), instructions="contract", strict=True,
        )
    report = build_review_report(
        review_bundle(), context_summary=context_summary(), verification=verification(),
        project_root=tmp_path, runner=runner, runtime_root=runtime_path(tmp_path), instructions="contract", strict=True,
    )
    assert report["verdict"] == "pass"
    assert runner.calls == 2


def test_strict_mode_changes_review_fingerprint_and_cache_identity(tmp_path):
    runner = ReviewRunner()
    strict_report = build_review_report(
        review_bundle(), context_summary=context_summary(), verification=verification(),
        project_root=tmp_path, runner=runner, runtime_root=runtime_path(tmp_path), instructions="contract", strict=True,
    )
    non_strict_report = build_review_report(
        review_bundle(), context_summary=context_summary(), verification=verification(),
        project_root=tmp_path, runner=runner, runtime_root=runtime_path(tmp_path), instructions="contract", strict=False,
    )
    assert strict_report["reviewFingerprint"] != non_strict_report["reviewFingerprint"]
    assert runner.calls == 2


def test_batch_merge_output_overflow_keeps_all_high_findings():
    findings = [
        {
            "severity": "high", "file": f"file-{index}.py", "line": 1,
            "rule": "bug", "evidence": "x" * 100, "impact": "impact",
            "recommendedAction": "fix",
        }
        for index in range(2)
    ]
    merged = merge_review_batches(
        [
            {"verdict": "changes_required", "findings": [finding], "residualRisk": []}
            for finding in findings
        ],
        fingerprint="abc", output_token_limit=50,
    )
    assert merged["verdict"] == "context_overflow"
    assert merged["findings"] == findings


def test_review_service_rejects_runtime_outside_project(tmp_path):
    project_root = tmp_path / "project"
    external_runtime = tmp_path / "external" / ".nbs_agent_runtime"
    with pytest.raises(PermissionError, match="project runtime"):
        build_review_report(
            review_bundle(), project_root=project_root,
            context_summary=context_summary(), verification=verification(),
            runner=ReviewRunner(), runtime_root=external_runtime,
            instructions="contract", strict=True,
        )


def test_review_service_rejects_symlinked_runtime(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    external_runtime = tmp_path / "external-runtime"
    external_runtime.mkdir()
    symlink_runtime = project_root / ".nbs_agent_runtime"
    symlink_runtime.symlink_to(external_runtime, target_is_directory=True)
    with pytest.raises(PermissionError, match="symlink"):
        build_review_report(
            review_bundle(), project_root=project_root,
            context_summary=context_summary(), verification=verification(),
            runner=ReviewRunner(), runtime_root=symlink_runtime,
            instructions="contract", strict=True,
        )
