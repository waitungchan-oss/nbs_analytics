import json
from hashlib import sha256

from scripts import hermes_post_change_check as post_check


def _write_v2_documentation_artifacts(root, *, run_id="run-1", target_id="handoff.current-conclusion"):
    from backend.agents.documentation_models import DocumentationProposalV2
    from backend.agents.documentation_policy import load_documentation_target
    from backend.agents.documentation_validator import DocumentationProposalValidator
    from backend.agents.workflow_models import canonical_sha256

    target = load_documentation_target(target_id)
    target_path = root / target.repo_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    before_text = (
        f"# Handoff\n\n{target.section_heading}\nOld handoff text.\n\n"
        "## 2. Following section\nLeave this section alone.\n"
    )
    target_path.write_text(before_text, encoding="utf-8")
    section = before_text[before_text.index(target.section_heading):before_text.index("\n## 2. Following section") + 1]
    commit_sha = "a" * 40
    source_fingerprint = "b" * 64
    run_root = root / ".nbs_agent_runtime" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    source_payloads = {
        "manifest.json": {"runId": run_id, "gitHead": commit_sha},
        "status.json": {"runId": run_id, "status": "completed"},
        "approval.json": {
            "runId": run_id, "authorizationStatus": "approved", "approvedBaseSha": commit_sha,
        },
        "review.json": {
            "overallStatus": "PASS", "commitSha": commit_sha, "sourceFingerprint": source_fingerprint,
        },
        "full-verification.json": {
            "overallStatus": "PASS", "commitSha": commit_sha, "sourceFingerprint": source_fingerprint,
        },
        "hermes.json": {
            "overallStatus": "PASS", "commitSha": commit_sha, "sourceFingerprint": source_fingerprint,
        },
    }
    for name, payload in source_payloads.items():
        (run_root / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def payload_sha256(payload):
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()

    evidence_unsigned = {
        "schemaVersion": "documentation-evidence-v2",
        "taskId": "task-hermes-test",
        "generatedAt": "2026-09-29T09:00:00+08:00",
        "runId": run_id,
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "selectedTargetId": target_id,
        "sources": [
            {
                "path": f".nbs_agent_runtime/runs/{run_id}/{name}",
                "sha256": payload_sha256(payload),
            }
            for name, payload in source_payloads.items()
        ] + [{"path": target.repo_path, "sha256": sha256(target_path.read_bytes()).hexdigest()}],
        "gateResults": [
            {
                "gate": gate, "status": "pass",
                "sourceFingerprint": source_fingerprint,
                "evidenceFingerprint": payload_sha256(source_payloads[f"{gate}.json"]),
            }
            for gate in ("review", "full-verification", "hermes")
        ],
        "guardrails": {
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "mayBaseline": "HKD 12,057,968",
        },
        "expectedSectionSha256": sha256(section.encode("utf-8")).hexdigest(),
    }
    evidence = {
        **evidence_unsigned,
        "evidenceFingerprint": canonical_sha256(evidence_unsigned),
    }
    content = f"{target.section_heading}\nUpdated handoff text.\n"
    proposal_unsigned = {
        "schemaVersion": "documentation-proposal-v2",
        "taskId": evidence["taskId"],
        "generatedAt": "2026-09-29T09:01:00+08:00",
        "evidence": evidence,
        "evidenceFingerprint": evidence["evidenceFingerprint"],
        "status": "ready",
        "targetId": target_id,
        "targetKind": target.target_kind,
        "repoPath": target.repo_path,
        "sectionHeading": target.section_heading,
        "operation": target.operation,
        "expectedSectionSha256": evidence["expectedSectionSha256"],
        "content": content,
        "contentSha256": sha256(content.encode("utf-8")).hexdigest(),
    }
    proposal = {
        **proposal_unsigned,
        "proposalFingerprint": canonical_sha256(proposal_unsigned),
    }
    preview = DocumentationProposalValidator(root).build_target_preview(
        DocumentationProposalV2.from_dict(proposal)
    ).to_dict()
    application_unsigned = {
        "schemaVersion": "documentation-application-v2",
        "taskId": proposal["taskId"],
        "generatedAt": "2026-09-29T09:02:00+08:00",
        "runId": run_id,
        "sourceFingerprint": source_fingerprint,
        "proposalFingerprint": proposal["proposalFingerprint"],
        "status": "awaiting_target_approval",
        "targetId": target_id,
        "repoPath": target.repo_path,
        "approvalId": target.required_approval_id,
        "beforeSectionSha256": preview["beforeSectionSha256"],
        "afterSectionSha256": preview["afterSectionSha256"],
        "result": "preview",
    }
    application = {
        **application_unsigned,
        "applicationFingerprint": canonical_sha256(application_unsigned),
    }
    artifact_dir = (
        root / ".nbs_agent_runtime" / "runs" / run_id / "documentation"
        / "targets" / target_id
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "documentation-evidence-v2.json": evidence,
        "documentation-proposal-v2.json": proposal,
        "documentation-preview-v2.json": preview,
        "documentation-application-v2.json": application,
    }
    for name, payload in artifacts.items():
        (artifact_dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return artifact_dir, artifacts


def test_default_plan_includes_git_runtime_baseline_and_targeted_tests():
    plan = post_check.build_check_plan(include_monitor=True, include_tests=True)
    labels = [step.label for step in plan]

    assert labels[:3] == ["git-status", "git-diff-stat", "git-diff-name-only"]
    assert "system-status" in labels
    assert "system-acceptance" in labels
    assert "system-monitor" in labels
    assert "phase2-baseline" in labels
    assert "monthly-baseline-governance" in labels
    assert "implementation-agent-files" in labels
    assert "targeted-tests" in labels
    assert "documentation-artifact-report" in labels
    assert "memory-sidecar-artifact-report" in labels
    targeted = next(step for step in plan if step.label == "targeted-tests")
    assert "tests/test_memory_sidecar_hermes_boundary.py" in targeted.command
    assert "tests/test_monthly_baseline_service.py" in targeted.command
    assert "tests/test_monthly_baseline_check_cli.py" in targeted.command
    for test_name in [
        "tests/test_database_explicit_path.py",
        "tests/test_upload_lock_service.py",
        "tests/test_cache_generation_service.py",
        "tests/test_upload_orchestrator_service.py",
        "tests/test_upload_action_service.py",
        "tests/test_upload_single_writer_integration.py",
        "tests/test_business_rules_service.py",
        "tests/test_application_snapshot_service.py",
        "tests/test_decision_service.py",
        "tests/test_decision_api.py",
        "tests/test_evidence_models.py",
        "tests/test_evidence_collector.py",
        "tests/test_agent_runtime.py",
        "tests/test_context_agent_service.py",
        "tests/test_review_agent_service.py",
        "tests/test_agent_cli.py",
        "tests/test_agent_dispatch_contract.py",
        "tests/test_agent_read_only_contract.py",
        "tests/test_implementation_models.py",
        "tests/test_implementation_guard.py",
        "tests/test_validation_runner.py",
        "tests/test_implementation_agent_service.py",
        "tests/test_implementation_agent_cli.py",
        "tests/test_implementation_agent_integration.py",
    ]:
        assert test_name in targeted.command


def test_implementation_agent_file_gate_is_read_only():
    plan = post_check.build_check_plan(include_monitor=False, include_tests=False)
    file_gate = next(step for step in plan if step.label == "implementation-agent-files")

    assert file_gate.command[1:3] == ["-c", file_gate.command[2]]
    assert "backend/agents/implementation_models.py" in file_gate.command[2]
    assert "backend/agents/implementation_guard.py" in file_gate.command[2]
    assert "backend/agents/implementation_agent_service.py" in file_gate.command[2]
    assert "scripts/implementation_agent.py" in file_gate.command[2]
    assert "read_text" not in file_gate.command[2]
    assert "write_text" not in file_gate.command[2]
    assert "worktree" not in file_gate.command[2]


def test_plan_has_exact_implementation_agent_test_pack_commands():
    plan = post_check.build_check_plan(include_monitor=False, include_tests=True)
    commands = {step.label: step.command for step in plan}
    py = post_check.python_bin()

    assert commands["implementation-agent-core-tests"] == [
        py,
        "-m",
        "pytest",
        "tests/test_implementation_models.py",
        "tests/test_implementation_guard.py",
        "tests/test_validation_runner.py",
        "-q",
    ]
    assert commands["implementation-agent-integration-tests"] == [
        py,
        "-m",
        "pytest",
        "tests/test_implementation_agent_service.py",
        "tests/test_implementation_agent_cli.py",
        "tests/test_implementation_agent_integration.py",
        "-q",
    ]


def test_plan_can_skip_monitor_and_tests_for_fast_dry_run():
    plan = post_check.build_check_plan(include_monitor=False, include_tests=False)
    labels = [step.label for step in plan]

    assert "system-monitor" not in labels
    assert "targeted-tests" not in labels
    assert "phase2-baseline" in labels
    assert "monthly-baseline-governance" in labels


def test_plan_can_skip_external_service_acceptance_without_skipping_status_or_baseline():
    plan = post_check.build_check_plan(
        include_monitor=False, include_tests=False, include_system_acceptance=False,
    )
    labels = [step.label for step in plan]

    assert "system-status" in labels
    assert "system-acceptance" not in labels
    assert "phase2-baseline" in labels
    assert "monthly-baseline-governance" in labels


def test_documentation_hermes_check_is_read_only_and_does_not_dispatch():
    plan = post_check.build_check_plan(include_monitor=False, include_tests=False)
    step = next(item for item in plan if item.label == "documentation-artifact-report")
    command = " ".join(step.command)
    source = post_check.documentation_artifact_report.__code__.co_consts

    assert any("documentation-application.json" in str(value) for value in source)
    assert any("documentation-proposal.json" in str(value) for value in source)
    assert any("documentation-evidence.json" in str(value) for value in source)
    assert any("documentation-telemetry.json" in str(value) for value in source)
    assert "agent_workflow.py" not in command
    assert "documentation_agent.py" not in command
    assert "write_text" not in command
    assert "write_artifact" not in command
    assert "document" not in command.lower().replace("documentation", "")


def test_documentation_artifact_report_validates_schema_caps_and_writes_nothing(tmp_path):
    run = tmp_path / ".nbs_agent_runtime/runs/run-1"
    run.mkdir(parents=True)
    (run / "documentation-telemetry.json").write_text(
        '{"schemaVersion":"documentation-telemetry-v1","proposalCount":1,"result":"applied"}',
        encoding="utf-8",
    )

    report = post_check.documentation_artifact_report(tmp_path)

    assert report["schemaVersion"] == "documentation-hermes-report-v1"
    assert report["artifactCounts"]["documentation-telemetry.json"] == 1
    assert report["invalidRuns"] == []
    assert report["policy"] == "read-only"
    assert report["invocations"] == 0
    assert report["writes"] == 0

    unsafe_project = tmp_path / "unsafe-project"
    unsafe_project.mkdir()
    external_runtime = tmp_path / "external-runtime"
    (external_runtime / "runs").mkdir(parents=True)
    (unsafe_project / ".nbs_agent_runtime").symlink_to(external_runtime, target_is_directory=True)
    unsafe_report = post_check.documentation_artifact_report(unsafe_project)
    assert unsafe_report["invalidTargets"] == [{
        "runId": "*", "targetId": "*", "reason": "unsafe_runtime_directory",
    }]


def test_hermes_validates_documentation_v2_artifacts_read_only(tmp_path):
    artifact_dir, artifacts = _write_v2_documentation_artifacts(tmp_path)
    before = {name: (artifact_dir / name).read_bytes() for name in artifacts}

    report = post_check.documentation_artifact_report(tmp_path)

    assert report["schemaVersion"] == "documentation-hermes-report-v1"
    assert report["v2TargetCount"] == 1
    assert report["v2ArtifactCounts"]["documentation-evidence-v2.json"] == 1
    assert report["v2ArtifactCounts"]["documentation-preview-v2.json"] == 1
    assert report["invalidTargets"] == []
    assert report["invalidRuns"] == []
    assert report["policy"] == "read-only"
    assert report["invocations"] == 0
    assert report["writes"] == 0
    assert {name: (artifact_dir / name).read_bytes() for name in artifacts} == before


def test_hermes_blocks_malformed_stale_and_over_cap_v2_artifacts(tmp_path):
    from backend.agents.workflow_models import canonical_sha256

    malformed_dir, _ = _write_v2_documentation_artifacts(tmp_path, run_id="run-malformed")
    malformed_path = malformed_dir / "documentation-proposal-v2.json"
    malformed = json.loads(malformed_path.read_text(encoding="utf-8"))
    malformed["proposalFingerprint"] = "0" * 64
    malformed_path.write_text(json.dumps(malformed), encoding="utf-8")

    stale_dir, _ = _write_v2_documentation_artifacts(tmp_path, run_id="run-stale")
    stale_path = stale_dir / "documentation-application-v2.json"
    stale = json.loads(stale_path.read_text(encoding="utf-8"))
    stale["taskId"] = "different-task"
    stale_unsigned = {key: value for key, value in stale.items() if key != "applicationFingerprint"}
    stale["applicationFingerprint"] = canonical_sha256(stale_unsigned)
    stale_path.write_text(json.dumps(stale), encoding="utf-8")

    bad_status_dir, _ = _write_v2_documentation_artifacts(tmp_path, run_id="run-bad-status")
    for name in (
        "documentation-proposal-v2.json",
        "documentation-preview-v2.json",
        "documentation-application-v2.json",
    ):
        (bad_status_dir / name).unlink()
    bad_status_path = bad_status_dir / "documentation-evidence-v2.json"
    bad_status = json.loads(bad_status_path.read_text(encoding="utf-8"))
    bad_status["gateResults"][0]["status"] = "failed"
    bad_status_unsigned = {key: value for key, value in bad_status.items() if key != "evidenceFingerprint"}
    bad_status["evidenceFingerprint"] = canonical_sha256(bad_status_unsigned)
    bad_status_path.write_text(json.dumps(bad_status), encoding="utf-8")

    unlisted_dir, _ = _write_v2_documentation_artifacts(tmp_path, run_id="run-unlisted-file")
    (unlisted_dir / "unlisted.json").write_text("{}", encoding="utf-8")

    unsafe_mode_dir, _ = _write_v2_documentation_artifacts(tmp_path, run_id="run-unsafe-mode")
    (unsafe_mode_dir / "documentation-application-v2.json").chmod(0o666)

    over_cap_dir, _ = _write_v2_documentation_artifacts(tmp_path, run_id="run-over-cap")
    over_cap_path = over_cap_dir / "documentation-application-v2.json"
    over_cap_path.write_text(" " * (5 * 1024 * 1024 + 1), encoding="utf-8")

    symlink_run = tmp_path / ".nbs_agent_runtime/runs/run-symlink/documentation/targets"
    symlink_run.mkdir(parents=True)
    outside = tmp_path / "outside-target"
    outside.mkdir()
    (symlink_run / "handoff.current-conclusion").symlink_to(outside, target_is_directory=True)
    linked_run = tmp_path / ".nbs_agent_runtime/runs/run-linked"
    linked_run.symlink_to(malformed_dir.parents[2], target_is_directory=True)

    report = post_check.documentation_artifact_report(tmp_path)

    invalid_targets = {
        (item["runId"], item["targetId"])
        for item in report["invalidTargets"]
    }
    assert {("run-malformed", "handoff.current-conclusion"),
            ("run-stale", "handoff.current-conclusion"),
            ("run-bad-status", "handoff.current-conclusion"),
            ("run-unlisted-file", "handoff.current-conclusion"),
            ("run-unsafe-mode", "handoff.current-conclusion"),
            ("run-over-cap", "handoff.current-conclusion"),
            ("run-symlink", "handoff.current-conclusion")} <= invalid_targets
    assert {
        "run-malformed", "run-stale", "run-bad-status",
        "run-unlisted-file", "run-unsafe-mode", "run-over-cap", "run-symlink", "run-linked",
    } <= set(report["invalidRuns"])
    assert any(item["runId"] == "run-over-cap" for item in report["capWarnings"])
    assert report["policy"] == "read-only"
    assert report["invocations"] == 0
    assert report["writes"] == 0


def test_hermes_blocks_v2_artifacts_when_current_source_or_target_drifts(tmp_path):
    target_root = tmp_path / "target-drift"
    _write_v2_documentation_artifacts(target_root)
    target_path = target_root / "NBS_ANALYTICS_HANDOFF.md"
    target_path.write_text(
        target_path.read_text(encoding="utf-8").replace("Old handoff text.", "Changed after preview."),
        encoding="utf-8",
    )

    gate_root = tmp_path / "gate-drift"
    _write_v2_documentation_artifacts(gate_root, run_id="run-gate-drift")
    gate_path = gate_root / ".nbs_agent_runtime/runs/run-gate-drift/review.json"
    gate_path.write_text('{"overallStatus":"FAIL"}', encoding="utf-8")

    target_report = post_check.documentation_artifact_report(target_root)
    gate_report = post_check.documentation_artifact_report(gate_root)

    assert [(item["runId"], item["targetId"]) for item in target_report["invalidTargets"]] == [
        ("run-1", "handoff.current-conclusion"),
    ]
    assert [(item["runId"], item["targetId"]) for item in gate_report["invalidTargets"]] == [
        ("run-gate-drift", "handoff.current-conclusion"),
    ]


def test_documentation_v2_hermes_never_changes_release_authority(tmp_path, monkeypatch):
    from backend.agents.documentation_controller import DocumentationController

    _write_v2_documentation_artifacts(tmp_path)
    release_result = tmp_path / ".nbs_agent_runtime/release-gates/release-gate-result.json"
    release_result.parent.mkdir(parents=True)
    release_result.write_text('{"Final-Acceptance":"pending"}\n', encoding="utf-8")
    before = release_result.read_bytes()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Hermes documentation inspection must never apply or dispatch")

    monkeypatch.setattr(DocumentationController, "apply_target", forbidden)
    monkeypatch.setattr(post_check.subprocess, "run", forbidden)

    report = post_check.documentation_artifact_report(tmp_path)

    assert report["policy"] == "read-only"
    assert report["invocations"] == 0
    assert report["writes"] == 0
    assert release_result.read_bytes() == before


def test_memory_sidecar_hermes_check_is_read_only_and_does_not_start_gateway():
    plan = post_check.build_check_plan(include_monitor=False, include_tests=False)
    step = next(item for item in plan if item.label == "memory-sidecar-artifact-report")
    command = " ".join(step.command)

    assert "memory_sidecar_artifact_report" in command
    assert "gateway" not in command.lower()
    assert "write_text" not in command
    assert "append_memory_sidecar_telemetry" not in command


def test_sandbox_capability_hermes_report_is_read_only_and_validates_receipt(tmp_path):
    from backend.agents.sandbox_capability_preflight import SandboxCapabilityEvidence
    from backend.agents.sandbox_capability_receipt import write_capability_evidence

    evidence = SandboxCapabilityEvidence._build(
        "blocked_environment", "darwin", "a" * 64, "b" * 64, "c" * 64,
        {"applicationApplied": False, "filesystemPolicyEnforced": False,
         "processPolicyEnforced": False, "networkPolicyEnforced": False},
        "sandbox_apply_denied", ("bounded diagnostic",),
        "2026-08-31T00:00:00Z", "2026-08-31T00:00:01Z",
    )
    path = tmp_path / ".nbs_agent_runtime/sandbox-capability/evidence.json"
    write_capability_evidence(path, evidence)

    report = post_check.sandbox_capability_artifact_report(tmp_path)

    assert report["schemaVersion"] == "sandbox-capability-hermes-report-v1"
    assert report["status"] == "blocked_environment"
    assert report["failureCode"] == "sandbox_apply_denied"
    assert report["artifactCount"] == 1
    assert report["policy"] == "read-only"
    assert report["invocations"] == report["writes"] == 0


def test_sandbox_capability_hermes_report_marks_missing_receipt_blocked(tmp_path):
    report = post_check.sandbox_capability_artifact_report(tmp_path)

    assert report["status"] == "blocked"
    assert report["artifactCount"] == 0
    assert report["policy"] == "read-only"


def test_memory_hub_integration_report_is_read_only_and_validates_evidence(tmp_path):
    from backend.agents.memory_hub_integration_models import build_memory_hub_integration_evidence
    run = tmp_path / ".nbs_agent_runtime" / "runs" / "run-1"
    run.mkdir(parents=True)
    evidence = build_memory_hub_integration_evidence(
        project_id="nbs-analytics", consumer_id="context-agent", integration_mode="direct_query",
        status="ready", reason="ok", query_fingerprint="a" * 64, hints_fingerprint="b" * 64,
        policy_decision_fingerprints=("c" * 64,), source_refs=(), hint_count=1,
        generated_at="2026-08-18T00:00:00+00:00",
    ).to_dict()
    (run / "memory-hub-integration.json").write_text(__import__("json").dumps(evidence), encoding="utf-8")
    report = post_check.memory_hub_integration_artifact_report(tmp_path)
    assert report["status"] == "pass"
    assert report["readyCount"] == 1
    assert report["invocations"] == report["writes"] == 0
    labels = [step.label for step in post_check.build_check_plan(include_monitor=False, include_tests=False)]
    assert "memory-hub-integration-artifact-report" in labels


def test_governance_graph_report_is_read_only_and_bounded(tmp_path):
    report = post_check.governance_graph_artifact_report(tmp_path)

    assert report["schemaVersion"] == "governance-graph-hermes-report-v1"
    assert report["policy"] == "read-only"
    assert report["invocations"] == 0
    assert report["writes"] == 0
    assert report["runCount"] == 0


def test_hermes_targeted_tests_include_graph_pack():
    targeted = next(
        step for step in post_check.build_check_plan(include_monitor=False)
        if step.label == "targeted-tests"
    )
    for name in (
        "tests/test_governance_graph_models.py",
        "tests/test_governance_graph_policy.py",
        "tests/test_governance_graph_service.py",
        "tests/test_governance_graph_cli.py",
    ):
        assert name in targeted.command


def test_governance_graph_report_validates_schema_caps_and_symlinks(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    valid = runs / "run-valid"
    valid.mkdir(parents=True)
    (valid / "governance-graph.json").write_text(
        '{"schemaVersion":"nbs-governance-graph-v1","runId":"run-valid","overallStatus":"blocked"}',
        encoding="utf-8",
    )
    malformed = runs / "run-malformed"
    malformed.mkdir()
    (malformed / "governance-graph.json").write_text("not-json", encoding="utf-8")
    dangling = runs / "run-dangling"
    dangling.mkdir()
    (dangling / "governance-graph.json").symlink_to(tmp_path / "missing.json")

    report = post_check.governance_graph_artifact_report(tmp_path)

    assert report["runCount"] == 3
    assert report["artifactCount"] == 2
    assert set(report["invalidRuns"]) == {"run-malformed", "run-dangling"}
    assert report["statusCounts"] == {"blocked": 1}


def test_overall_status_fails_on_failed_required_step():
    results = [
        {"label": "git-status", "required": True, "exitCode": 0},
        {"label": "phase2-baseline", "required": True, "exitCode": 1},
    ]

    assert post_check.compute_overall_status(results) == "fail"


def test_overall_status_warns_on_failed_optional_step():
    results = [
        {"label": "git-status", "required": True, "exitCode": 0},
        {"label": "git-diff-stat", "required": False, "exitCode": 1},
    ]

    assert post_check.compute_overall_status(results) == "warning"


def test_overall_status_passes_when_all_steps_pass():
    results = [
        {"label": "git-status", "required": True, "exitCode": 0},
        {"label": "phase2-baseline", "required": True, "exitCode": 0},
    ]

    assert post_check.compute_overall_status(results) == "pass"


def test_markdown_report_summarizes_baseline_tests_and_commit_advice():
    report = {
        "overallStatus": "pass",
        "projectRoot": "/tmp/nbs",
        "results": [
            {
                "label": "git-status",
                "required": True,
                "exitCode": 0,
                "stdout": "## main\n M pipeline.py\n?? tests/test_official_export_workbook_contract.py\n",
                "stderr": "",
            },
            {
                "label": "phase2-baseline",
                "required": True,
                "exitCode": 0,
                "stdout": '{"status":"matched","formattedActualTotal":"HKD 12,057,968"}',
                "stderr": "",
            },
            {
                "label": "targeted-tests",
                "required": True,
                "exitCode": 0,
                "stdout": "39 passed in 9.40s",
                "stderr": "",
            },
            {
                "label": "monthly-baseline-governance",
                "required": True,
                "exitCode": 0,
                "stdout": '{"status":"monitoring","blockingStatus":"matched","promotionReady":false}',
                "stderr": "",
            },
        ],
    }

    markdown = post_check.format_markdown_report(report)

    assert "# Hermes Post-Change Report" in markdown
    assert "Overall status: PASS" in markdown
    assert "phase2-baseline: PASS" in markdown
    assert "HKD 12,057,968" in markdown
    assert "targeted-tests: PASS" in markdown
    assert "monthly-baseline-governance: PASS" in markdown
    assert "promotionReady" in markdown
    assert "39 passed" in markdown
    assert "Commit recommendation: ready after reviewing grouped diff" in markdown
