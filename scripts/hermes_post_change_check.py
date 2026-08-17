from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_TIMEOUT_SECONDS = 180
TARGETED_TESTS = [
    "tests/test_governance_graph_models.py",
    "tests/test_governance_graph_policy.py",
    "tests/test_governance_graph_service.py",
    "tests/test_governance_graph_cli.py",
    "tests/test_phase2_precheck_acceptance.py",
    "tests/test_dashboard_service.py",
    "tests/test_dashboard_api.py",
    "tests/test_database_rollback.py",
    "tests/test_stability_history_service.py",
    "tests/test_system_health_service.py",
    "tests/test_restore_drill_service.py",
    "tests/test_upload_preflight_service.py",
    "tests/test_upload_rollback_service.py",
    "tests/test_upload_api.py",
    "tests/test_monthly_baseline_service.py",
    "tests/test_monthly_baseline_check_cli.py",
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
    "tests/test_workflow_models.py",
    "tests/test_workflow_store.py",
    "tests/test_workflow_notifications.py",
    "tests/test_workflow_retention.py",
    "tests/test_workflow_orchestrator_start.py",
    "tests/test_workflow_orchestrator_approve.py",
    "tests/test_agent_workflow_cli.py",
    "tests/test_agent_workflow_integration.py",
    "tests/test_documentation_agent_docs.py",
    "tests/test_memory_sidecar_hermes_boundary.py",
    "tests/test_agent_operations_service.py",
    "tests/test_agent_operations_rendering.py",
    "tests/test_hermes_post_change_check.py",
]


@dataclass(frozen=True)
class CheckStep:
    label: str
    command: list[str]
    required: bool = True
    timeout: int = DEFAULT_TIMEOUT_SECONDS


def python_bin(project_root: Path = PROJECT_ROOT) -> str:
    venv_python = project_root / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def documentation_artifact_report(project_root: Path = PROJECT_ROOT) -> dict:
    """Inspect documentation sidecars without invoking or writing anything."""
    runs_root = Path(project_root) / ".nbs_agent_runtime" / "runs"
    artifact_names = (
        "documentation-evidence.json",
        "documentation-proposal.json",
        "documentation-preview.json",
        "documentation-application.json",
        "documentation-telemetry.json",
    )
    max_bytes = 5 * 1024 * 1024
    report = {
        "schemaVersion": "documentation-hermes-report-v1",
        "runCount": 0,
        "artifactCounts": {name: 0 for name in artifact_names},
        "invalidRuns": [],
        "capWarnings": [],
        "policy": "read-only",
        "invocations": 0,
        "writes": 0,
    }
    if not runs_root.is_dir() or runs_root.is_symlink():
        return report
    for run_dir in sorted(runs_root.iterdir(), key=lambda path: path.name):
        if run_dir.is_symlink() or not run_dir.is_dir():
            continue
        report["runCount"] += 1
        invalid = False
        for name in artifact_names:
            path = run_dir / name
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_file():
                invalid = True
                continue
            report["artifactCounts"][name] += 1
            if path.stat().st_size > max_bytes:
                report["capWarnings"].append({"runId": run_dir.name, "artifact": name})
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                invalid = True
                continue
            expected = {
                "documentation-evidence.json": "documentation-evidence-v1",
                "documentation-proposal.json": "documentation-proposal-v1",
                "documentation-application.json": "documentation-application-v1",
                "documentation-telemetry.json": "documentation-telemetry-v1",
            }.get(name)
            if expected and (not isinstance(payload, dict) or payload.get("schemaVersion") != expected):
                invalid = True
        if invalid and len(report["invalidRuns"]) < 100:
            report["invalidRuns"].append(run_dir.name)
    return report


def memory_sidecar_artifact_report(project_root: Path = PROJECT_ROOT) -> dict:
    """Inspect bounded memory sidecar evidence without provider access or writes."""
    from backend.agents.memory_sidecar_hint_models import MemoryHints, MemorySidecarSchemaError

    root = Path(project_root) / ".nbs_agent_runtime"
    runs_root = root / "runs"
    telemetry_path = root / "telemetry" / "memory_sidecar.jsonl"
    artifact_names = ("memory-hints.json", "memory_sidecar.jsonl")
    hints_max_bytes = 6000
    telemetry_max_bytes = 5 * 1024 * 1024
    report = {
        "schemaVersion": "memory-sidecar-hermes-report-v1",
        "policy": "read-only",
        "invocations": 0,
        "writes": 0,
        "status": "pass",
        "artifactCounts": {name: 0 for name in artifact_names},
        "fallbackChecks": {
            "timeout": "fallback",
            "degraded": "fallback",
            "stale": "blocked",
            "invalid": "blocked",
            "permission": "blocked",
        },
        "diagnostics": [],
    }

    def diagnostic(code: str, run_id: str) -> None:
        if len(report["diagnostics"]) < 100:
            report["diagnostics"].append({"code": code, "runId": run_id})

    telemetry_root = root / "telemetry"
    if root.is_symlink():
        diagnostic("permission_denied", "runtime")
    else:
        if runs_root.is_symlink():
            diagnostic("permission_denied", "runs")
        elif runs_root.is_dir():
            try:
                run_dirs = sorted(runs_root.iterdir(), key=lambda path: path.name)
            except (OSError, PermissionError):
                diagnostic("permission_denied", "runs")
                run_dirs = ()
            for run_dir in run_dirs:
                if run_dir.is_symlink():
                    diagnostic("permission_denied", run_dir.name)
                    continue
                if not run_dir.is_dir():
                    continue
                hints_path = run_dir / "memory-hints.json"
                if not hints_path.exists():
                    continue
                if hints_path.is_symlink() or not hints_path.is_file():
                    diagnostic("permission_denied", run_dir.name)
                    continue
                report["artifactCounts"]["memory-hints.json"] += 1
                try:
                    if hints_path.stat().st_size > hints_max_bytes:
                        raise MemorySidecarSchemaError("memory hints exceed the Hermes cap")
                    hints = MemoryHints.from_dict(json.loads(hints_path.read_text(encoding="utf-8")))
                except (OSError, UnicodeError, json.JSONDecodeError, MemorySidecarSchemaError, ValueError):
                    diagnostic("invalid_memory_hints", run_dir.name)
                    continue
                if hints.status == "stale" or any(item.freshness == "stale" for item in hints.hints):
                    diagnostic("stale_memory_hints", run_dir.name)
                elif hints.status in {"timeout", "degraded", "empty"}:
                    diagnostic(f"fallback_{hints.status}", run_dir.name)

        if telemetry_root.is_symlink() or telemetry_path.is_symlink():
            diagnostic("permission_denied", "telemetry")
        elif telemetry_path.exists() and telemetry_path.is_file():
            report["artifactCounts"]["memory_sidecar.jsonl"] = 1
            try:
                if telemetry_path.stat().st_size > telemetry_max_bytes:
                    raise ValueError("memory telemetry exceeds the Hermes cap")
                for line in telemetry_path.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    expected_keys = {
                        "schemaVersion", "runId", "mode", "queryFingerprint", "status", "latencyMs",
                        "hintCount", "inputBytes", "fallback", "redactionCount",
                    }
                    if not isinstance(event, dict) or set(event) != expected_keys:
                        raise ValueError("memory telemetry schema is invalid")
                    from backend.agents.memory_sidecar_telemetry import MemorySidecarTelemetryEvent
                    if MemorySidecarTelemetryEvent.from_parts(
                        run_id=event["runId"], mode=event["mode"], query_fingerprint=event["queryFingerprint"],
                        status=event["status"], latency_ms=event["latencyMs"], hint_count=event["hintCount"],
                        input_bytes=event["inputBytes"], fallback=event["fallback"], redaction_count=event["redactionCount"],
                    ).to_dict() != event:
                        raise ValueError("memory telemetry contents are invalid")
                    if event.get("status") == "stale":
                        diagnostic("stale_memory_hints", str(event.get("runId") or "telemetry"))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                diagnostic("invalid_memory_telemetry", "telemetry")
        elif telemetry_path.exists() or telemetry_path.is_symlink():
            diagnostic("permission_denied", "telemetry")

    codes = {item["code"] for item in report["diagnostics"]}
    if any(code.startswith("invalid_") for code in codes):
        report["status"] = "invalid"
    elif any(code not in {"fallback_timeout", "fallback_degraded", "fallback_empty"} for code in codes):
        report["status"] = "blocked"
    return report


def governance_graph_artifact_report(project_root: Path = PROJECT_ROOT) -> dict:
    """Inspect persisted Graph projections without rebuilding or writing them."""
    runs_root = Path(project_root) / ".nbs_agent_runtime" / "runs"
    max_bytes = 5 * 1024 * 1024
    report = {
        "schemaVersion": "governance-graph-hermes-report-v1",
        "runCount": 0,
        "artifactCount": 0,
        "statusCounts": {},
        "invalidRuns": [],
        "capWarnings": [],
        "policy": "read-only",
        "invocations": 0,
        "writes": 0,
    }
    if not runs_root.is_dir() or runs_root.is_symlink():
        return report
    for run_dir in sorted(runs_root.iterdir(), key=lambda path: path.name):
        if run_dir.is_symlink() or not run_dir.is_dir():
            continue
        report["runCount"] += 1
        path = run_dir / "governance-graph.json"
        if path.is_symlink() or not path.exists():
            if path.is_symlink() and len(report["invalidRuns"]) < 100:
                report["invalidRuns"].append(run_dir.name)
            continue
        if not path.is_file():
            if len(report["invalidRuns"]) < 100:
                report["invalidRuns"].append(run_dir.name)
            continue
        report["artifactCount"] += 1
        if path.stat().st_size > max_bytes:
            report["capWarnings"].append({"runId": run_dir.name, "artifact": path.name})
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            if len(report["invalidRuns"]) < 100:
                report["invalidRuns"].append(run_dir.name)
            continue
        if (
            not isinstance(payload, dict)
            or payload.get("schemaVersion") != "nbs-governance-graph-v1"
            or payload.get("runId") != run_dir.name
        ):
            if len(report["invalidRuns"]) < 100:
                report["invalidRuns"].append(run_dir.name)
            continue
        status = payload.get("overallStatus")
        if isinstance(status, str):
            report["statusCounts"][status] = report["statusCounts"].get(status, 0) + 1
    return report


def short_term_offload_artifact_report(project_root: Path = PROJECT_ROOT) -> dict:
    """Read-only inspection of the isolated short-term offload root."""
    root = Path(project_root) / ".nbs_agent_runtime" / "short-term-offload"
    report = {
        "schemaVersion": "short-term-offload-hermes-report-v1",
        "status": "ready",
        "runCount": 0,
        "artifactCount": 0,
        "invalidPaths": [],
        "capWarnings": [],
        "policy": "read-only",
        "invocations": 0,
        "writes": 0,
    }
    if root.is_symlink():
        report["status"] = "blocked"
        report["invalidPaths"].append("root")
        return report
    if not root.is_dir():
        return report
    report["runCount"] = sum(1 for path in root.iterdir() if path.is_dir() and not path.is_symlink())
    for run_dir in sorted(root.iterdir(), key=lambda path: path.name):
        if run_dir.is_symlink() or not run_dir.is_dir():
            continue
        for session_dir in run_dir.iterdir():
            if session_dir.is_symlink() or not session_dir.is_dir():
                report["invalidPaths"].append(str(session_dir.relative_to(root)))
                continue
            for path in session_dir.glob("*.json"):
                if path.is_symlink() or not path.is_file():
                    report["invalidPaths"].append(str(path.relative_to(root)))
                    continue
                report["artifactCount"] += 1
                if path.stat().st_size > 50000:
                    report["capWarnings"].append(str(path.relative_to(root)))
    if report["invalidPaths"] or report["capWarnings"]:
        report["status"] = "blocked"
    return report


def build_check_plan(
    *,
    include_monitor: bool = True,
    include_tests: bool = True,
    project_root: Path = PROJECT_ROOT,
    verification_profile: str | None = None,
) -> list[CheckStep]:
    py = python_bin(project_root)
    baseline_code = (
        "from pathlib import Path; "
        "from scripts.phase2j_baseline_check import check_phase2_baseline; "
        "import json; "
        "print(json.dumps(check_phase2_baseline(Path('nbs_marketing_data.db')), "
        "ensure_ascii=False, indent=2))"
    )
    implementation_agent_file_code = (
        "from pathlib import Path; "
        "files=("
        "'backend/agents/implementation_models.py',"
        "'backend/agents/implementation_guard.py',"
        "'backend/agents/validation_runner.py',"
        "'backend/agents/implementation_agent_service.py',"
        "'scripts/implementation_agent.py',"
        "'tests/test_implementation_models.py',"
        "'tests/test_implementation_guard.py',"
        "'tests/test_validation_runner.py',"
        "'tests/test_implementation_agent_service.py',"
        "'tests/test_implementation_agent_cli.py',"
        "'tests/test_implementation_agent_integration.py'); "
        "missing=[path for path in files if not Path(path).is_file()]; "
        "print('missing=' + ','.join(missing)); "
        "raise SystemExit(bool(missing))"
    )
    workflow_artifact_report_code = (
        "from pathlib import Path; import json; "
        "root=Path('.nbs_agent_runtime/runs'); "
        "runs=[path for path in root.iterdir() if path.is_dir() and not path.is_symlink()] if root.is_dir() else []; "
        "artifacts=('manifest.json','status.json','approval.json','context.json','implementation.json','review.json','full-verification.json','hermes.json','archive-summary.json'); "
        "print(json.dumps({'schemaVersion':'agent-workflow-hermes-report-v1','runCount':len(runs),'artifactCounts':{name:sum((run_dir/name).is_file() and not (run_dir/name).is_symlink() for run_dir in runs) for name in artifacts},'retentionConfig':Path('agent_config/workflow_retention.json').is_file()}, sort_keys=True))"
    )
    documentation_artifact_report_code = (
        "from scripts.hermes_post_change_check import documentation_artifact_report; "
        "import json; "
        "print(json.dumps(documentation_artifact_report(), sort_keys=True))"
    )
    governance_graph_artifact_report_code = (
        "from scripts.hermes_post_change_check import governance_graph_artifact_report; "
        "import json; "
        "print(json.dumps(governance_graph_artifact_report(), sort_keys=True))"
    )
    short_term_offload_artifact_report_code = (
        "from scripts.hermes_post_change_check import short_term_offload_artifact_report; "
        "import json; "
        "print(json.dumps(short_term_offload_artifact_report(), sort_keys=True))"
    )
    memory_sidecar_artifact_report_code = (
        "from scripts.hermes_post_change_check import memory_sidecar_artifact_report; "
        "import json; "
        "print(json.dumps(memory_sidecar_artifact_report(), sort_keys=True))"
    )
    profile_args = ["--verification-profile", verification_profile] if verification_profile else []
    plan = [
        CheckStep("git-status", ["git", "status", "--short", "--branch"]),
        CheckStep("git-diff-stat", ["git", "diff", "--stat"], required=False),
        CheckStep("git-diff-name-only", ["git", "diff", "--name-only"], required=False),
        CheckStep("system-status", [py, "scripts/system_manager.py", "status", *profile_args]),
        CheckStep("system-acceptance", [py, "scripts/system_manager.py", "acceptance", *profile_args]),
    ]
    if include_monitor and not verification_profile:
        plan.append(CheckStep("system-monitor", [py, "scripts/system_manager.py", "monitor"]))
    plan.append(
        CheckStep(
            "phase2-baseline",
            [py, "scripts/phase2j_baseline_check.py", *profile_args]
            if verification_profile else [py, "-c", baseline_code],
        )
    )
    plan.append(
        CheckStep(
            "monthly-baseline-governance",
            [py, "scripts/monthly_baseline_check.py", *profile_args],
        )
    )
    plan.append(
        CheckStep(
            "workflow-artifact-retention-report",
            [py, "-c", workflow_artifact_report_code],
            required=False,
        )
    )
    plan.append(
        CheckStep(
            "documentation-artifact-report",
            [py, "-c", documentation_artifact_report_code],
            required=False,
        )
    )
    plan.append(
        CheckStep(
            "memory-sidecar-artifact-report",
            [py, "-c", memory_sidecar_artifact_report_code],
            required=False,
        )
    )
    plan.append(
        CheckStep(
            "governance-graph-artifact-report",
            [py, "-c", governance_graph_artifact_report_code],
            required=False,
        )
    )
    plan.append(
        CheckStep(
            "short-term-offload-artifact-report",
            [py, "-c", short_term_offload_artifact_report_code],
            required=False,
        )
    )
    plan.append(
        CheckStep(
            "implementation-agent-files",
            [py, "-c", implementation_agent_file_code],
        )
    )
    if include_tests:
        plan.extend(
            [
                CheckStep(
                    "implementation-agent-core-tests",
                    [
                        py,
                        "-m",
                        "pytest",
                        "tests/test_implementation_models.py",
                        "tests/test_implementation_guard.py",
                        "tests/test_validation_runner.py",
                        "-q",
                    ],
                    timeout=600,
                ),
                CheckStep(
                    "implementation-agent-integration-tests",
                    [
                        py,
                        "-m",
                        "pytest",
                        "tests/test_implementation_agent_service.py",
                        "tests/test_implementation_agent_cli.py",
                        "tests/test_implementation_agent_integration.py",
                        "-q",
                    ],
                    timeout=600,
                ),
            ]
        )
        plan.append(
            CheckStep(
                "targeted-tests",
                [
                    py,
                    "-m",
                    "pytest",
                    *TARGETED_TESTS,
                    "-q",
                    "-p",
                    "no:cacheprovider",
                ],
                timeout=600,
            )
        )
    return plan


def run_step(step: CheckStep, *, project_root: Path = PROJECT_ROOT) -> dict:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(project_root)
    try:
        completed = subprocess.run(
            step.command,
            cwd=project_root,
            env=env,
            text=True,
            capture_output=True,
            timeout=step.timeout,
            check=False,
        )
        return {
            **asdict(step),
            "exitCode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            **asdict(step),
            "exitCode": 124,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + f"\nTimed out after {step.timeout}s",
        }
    except OSError as exc:
        return {
            **asdict(step),
            "exitCode": 127,
            "stdout": "",
            "stderr": str(exc),
        }


def compute_overall_status(results: list[dict]) -> str:
    required_failures = [item for item in results if item.get("required", True) and item.get("exitCode") != 0]
    if required_failures:
        return "fail"
    optional_failures = [item for item in results if not item.get("required", True) and item.get("exitCode") != 0]
    if optional_failures:
        return "warning"
    return "pass"


def run_checks(
    *,
    include_monitor: bool = True,
    include_tests: bool = True,
    project_root: Path = PROJECT_ROOT,
    verification_profile: str | None = None,
) -> dict:
    profile_identity = None
    if verification_profile:
        try:
            from backend.services.verification_runtime_paths import load_verification_runtime_profile
            profile_path = Path(verification_profile)
            if not profile_path.is_absolute():
                profile_path = project_root / profile_path
            profile, paths = load_verification_runtime_profile(profile_path, project_root=project_root)
            profile_identity = {
                "profileId": profile.profile_id,
                "projectId": profile.project_id,
                "gitHead": profile.git_head,
                "snapshotFingerprint": profile.database.snapshot_fingerprint,
                "sourceFingerprint": profile.database.source_fingerprint,
                "runtimeDir": str(paths.runtime_dir),
            }
        except Exception as exc:
            return {
                "overallStatus": "fail",
                "projectRoot": str(project_root),
                "verificationProfile": {"status": "blocked_runner_capability", "reason": type(exc).__name__},
                "results": [],
            }
    plan = build_check_plan(
        include_monitor=include_monitor,
        include_tests=include_tests,
        project_root=project_root,
        verification_profile=verification_profile,
    )
    results = [run_step(step, project_root=project_root) for step in plan]
    return {
        "overallStatus": compute_overall_status(results),
        "projectRoot": str(project_root),
        "results": results,
        **({"verificationProfile": profile_identity} if profile_identity else {}),
    }


def _result_by_label(report: dict, label: str) -> dict:
    for result in report.get("results", []):
        if result.get("label") == label:
            return result
    return {}


def _status_text(result: dict) -> str:
    if not result:
        return "MISSING"
    return "PASS" if int(result.get("exitCode", 1) or 0) == 0 else "FAIL"


def _first_matching_line(text: str, needles: tuple[str, ...]) -> str:
    for line in str(text or "").splitlines():
        if any(needle in line for needle in needles):
            return line.strip()
    return ""


def format_markdown_report(report: dict) -> str:
    status = str(report.get("overallStatus") or "unknown").upper()
    baseline = _result_by_label(report, "phase2-baseline")
    monthly = _result_by_label(report, "monthly-baseline-governance")
    tests = _result_by_label(report, "targeted-tests")
    workflow = _result_by_label(report, "workflow-artifact-retention-report")
    git_status = _result_by_label(report, "git-status")
    baseline_line = _first_matching_line(
        baseline.get("stdout", ""),
        ("formattedActualTotal", "HKD 12,057,968", "status"),
    )
    tests_line = _first_matching_line(tests.get("stdout", ""), ("passed", "failed", "error"))
    monthly_line = _first_matching_line(
        monthly.get("stdout", ""),
        ("promotionReady", "blockingStatus", "status"),
    )
    workflow_line = _first_matching_line(workflow.get("stdout", ""), ("runCount", "retentionConfig"))
    changed_lines = [
        line.strip()
        for line in str(git_status.get("stdout", "") or "").splitlines()
        if line.startswith(" M ") or line.startswith("?? ") or line.startswith(" A ")
    ]
    commit_advice = (
        "ready after reviewing grouped diff"
        if status == "PASS"
        else "not ready; fix failing required checks first"
    )
    changed_block = "\n".join(f"- `{line}`" for line in changed_lines[:20]) or "- No changed files reported."
    return "\n".join(
        [
            "# Hermes Post-Change Report",
            "",
            f"Overall status: {status}",
            f"Project root: `{report.get('projectRoot', '')}`",
            "",
            "## Required Checks",
            "",
            f"- phase2-baseline: {_status_text(baseline)}",
            f"- monthly-baseline-governance: {_status_text(monthly)}",
            f"- targeted-tests: {_status_text(tests)}",
            f"- workflow-artifact-retention-report: {_status_text(workflow)}",
            "",
            "## Evidence",
            "",
            f"- Baseline: `{baseline_line or 'not found in stdout'}`",
            f"- Monthly governance: `{monthly_line or 'not found in stdout'}`",
            f"- Tests: `{tests_line or 'not found in stdout'}`",
            f"- Workflow artifacts / retention: `{workflow_line or 'not found in stdout'}`",
            "",
            "## Changed Files",
            "",
            changed_block,
            "",
            "## Recommendation",
            "",
            f"Commit recommendation: {commit_advice}",
        ]
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Hermes post-change monitoring checks for nbs_analytics.")
    parser.add_argument("--skip-monitor", action="store_true", help="Skip system_manager.py monitor write.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip targeted pytest monitoring pack.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    parser.add_argument("--markdown", action="store_true", help="Print a concise Markdown report.")
    parser.add_argument("--verification-profile", help="Read-only verification profile path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_checks(
        include_monitor=not args.skip_monitor,
        include_tests=not args.skip_tests,
        verification_profile=args.verification_profile,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.markdown:
        print(format_markdown_report(report))
    else:
        print(f"Overall status: {report['overallStatus'].upper()}")
        print(f"Project root: {report['projectRoot']}")
        for result in report["results"]:
            print(f"\n===== {result['label']} ({result['exitCode']}) =====")
            if result.get("stdout"):
                print(result["stdout"].rstrip())
            if result.get("stderr"):
                print(result["stderr"].rstrip(), file=sys.stderr)
    return 0 if report["overallStatus"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
