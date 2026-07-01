from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT_SECONDS = 180
TARGETED_TESTS = [
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


def build_check_plan(
    *,
    include_monitor: bool = True,
    include_tests: bool = True,
    project_root: Path = PROJECT_ROOT,
) -> list[CheckStep]:
    py = python_bin(project_root)
    baseline_code = (
        "from pathlib import Path; "
        "from scripts.phase2j_baseline_check import check_phase2_baseline; "
        "import json; "
        "print(json.dumps(check_phase2_baseline(Path('nbs_marketing_data.db')), "
        "ensure_ascii=False, indent=2))"
    )
    plan = [
        CheckStep("git-status", ["git", "status", "--short", "--branch"]),
        CheckStep("git-diff-stat", ["git", "diff", "--stat"], required=False),
        CheckStep("git-diff-name-only", ["git", "diff", "--name-only"], required=False),
        CheckStep("system-status", [py, "scripts/system_manager.py", "status"]),
        CheckStep("system-acceptance", [py, "scripts/system_manager.py", "acceptance"]),
    ]
    if include_monitor:
        plan.append(CheckStep("system-monitor", [py, "scripts/system_manager.py", "monitor"]))
    plan.append(CheckStep("phase2-baseline", [py, "-c", baseline_code]))
    if include_tests:
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


def run_checks(*, include_monitor: bool = True, include_tests: bool = True, project_root: Path = PROJECT_ROOT) -> dict:
    plan = build_check_plan(include_monitor=include_monitor, include_tests=include_tests, project_root=project_root)
    results = [run_step(step, project_root=project_root) for step in plan]
    return {
        "overallStatus": compute_overall_status(results),
        "projectRoot": str(project_root),
        "results": results,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Hermes post-change monitoring checks for nbs_analytics.")
    parser.add_argument("--skip-monitor", action="store_true", help="Skip system_manager.py monitor write.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip targeted pytest monitoring pack.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_checks(include_monitor=not args.skip_monitor, include_tests=not args.skip_tests)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
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
