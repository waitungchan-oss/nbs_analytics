"""Run a bounded, advisory acceptance precheck for local development."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.acceptance_telemetry import build_gate_telemetry
from backend.agents.evidence_models import canonical_fingerprint
from scripts.full_pytest_gate import _parse_summary


_TAIL = 4000
_CONTRACT_PACK = (
    "tests/test_full_pytest_gate.py",
    "tests/test_hermes_gate.py",
    "tests/test_release_gate.py",
    "tests/test_gmv_ui_acceptance_runner.py",
    "tests/test_system_manager.py",
    "tests/test_release_gate_workflow.py",
)
_DIRECT_MAPPINGS = {
    "scripts/full_pytest_gate.py": ("tests/test_full_pytest_gate.py",),
    "scripts/hermes_gate.py": ("tests/test_hermes_gate.py",),
    "scripts/release_gate.py": ("tests/test_release_gate.py",),
    "backend/agents/release_gate_models.py": ("tests/test_release_gate_models.py", "tests/test_release_gate.py"),
    "scripts/ui_acceptance_gate.py": ("tests/test_ui_acceptance_gate.py",),
    "scripts/run_gmv_ui_acceptance.py": ("tests/test_gmv_ui_acceptance_runner.py",),
    "backend/agents/acceptance_paths.py": ("tests/test_acceptance_paths.py", "tests/test_gmv_ui_acceptance_runner.py"),
    "backend/agents/acceptance_telemetry.py": ("tests/test_acceptance_telemetry.py", "tests/test_release_gate_models.py"),
    "scripts/system_manager.py": ("tests/test_system_manager.py",),
}
_SHA40 = set("0123456789abcdef")
_SHA64 = set("0123456789abcdef")


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _validate_identity(commit_sha: str, source_fingerprint: str) -> None:
    if len(commit_sha) != 40 or any(char not in _SHA40 for char in commit_sha):
        raise ValueError("commit must be a 40-character SHA")
    if len(source_fingerprint) != 64 or any(char not in _SHA64 for char in source_fingerprint):
        raise ValueError("source fingerprint must be a 64-character SHA-256")


def _git(project_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=project_root, capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"git command failed: {' '.join(args)}")
    return completed.stdout


def collect_changed_paths(project_root: Path, base_ref: str | None = None) -> list[str]:
    paths: set[str] = set()
    if base_ref:
        paths.update(line.strip() for line in _git(project_root, "diff", "--name-only", f"{base_ref}...HEAD").splitlines() if line.strip())
    paths.update(line.strip() for line in _git(project_root, "diff", "--name-only", "HEAD", "--").splitlines() if line.strip())
    paths.update(line.strip() for line in _git(project_root, "ls-files", "--others", "--exclude-standard").splitlines() if line.strip())
    return sorted(paths)


def select_targeted_tests(changed_paths: Sequence[str]) -> tuple[list[str], str]:
    selected: set[str] = set()
    unknown = False
    for raw_path in changed_paths:
        path = Path(raw_path).as_posix()
        mapped = _DIRECT_MAPPINGS.get(path)
        if mapped is not None:
            selected.update(mapped)
            continue
        if path.startswith("tests/") and path.endswith(".py"):
            selected.add(path)
            continue
        unknown = True
    if unknown or not selected:
        return sorted(set(_CONTRACT_PACK) | selected), "contract_pack"
    return sorted(selected), "changed_file_mapping"


def _safe_argv(argv: Sequence[str]) -> list[str]:
    return [Path(value).name if Path(value).is_absolute() else value for value in argv]


def run_fast_precheck(
    project_root: Path,
    *,
    commit_sha: str,
    source_fingerprint: str,
    changed_paths: Sequence[str],
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    _validate_identity(commit_sha, source_fingerprint)
    if timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    root = Path(project_root).resolve()
    selected_tests, selection_mode = select_targeted_tests(changed_paths)
    argv = [sys.executable, "-m", "pytest", "-q", *selected_tests]
    started = time.perf_counter()
    status = "FAIL"
    result = {"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0}
    metadata: dict[str, Any] = {
        "commandId": "fast-acceptance-precheck",
        "argv": _safe_argv(argv),
        "exitCode": None,
    }
    stdout = stderr = ""
    try:
        completed = subprocess.run(
            argv, cwd=root, capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
        stdout, stderr = _as_text(completed.stdout), _as_text(completed.stderr)
        metadata["exitCode"] = completed.returncode
        try:
            result = _parse_summary(f"{stdout}\n{stderr}")
        except ValueError:
            metadata["failureCode"] = "malformed_summary"
        status = "PASS" if completed.returncode == 0 and "failureCode" not in metadata and result["failed"] == 0 else "FAIL"
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = _as_text(exc.output), _as_text(exc.stderr)
        metadata.update({"failureCode": "timeout", "timeoutSeconds": timeout_seconds})
        status = "BLOCKED"
    except OSError as exc:
        stderr = str(exc)
        metadata.update({"failureCode": "runner_os_error", "errorType": type(exc).__name__})
        status = "BLOCKED"

    failure_code = metadata.get("failureCode")
    metadata["stdoutTail"] = stdout[-_TAIL:]
    metadata["stderrTail"] = stderr[-_TAIL:]
    metadata["telemetry"] = build_gate_telemetry(
        duration_seconds=time.perf_counter() - started,
        failure_code=failure_code,
        blocked_reason=failure_code if status == "BLOCKED" else None,
    )
    unsigned = {
        "schemaVersion": "acceptance-fast-precheck-v1",
        "authority": "advisory",
        "status": status,
        "fullGateRequired": True,
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "changedPaths": list(changed_paths)[:128],
        "selectedTests": selected_tests,
        "selectionMode": selection_mode,
        "result": result,
        "metadata": metadata,
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def _working_tree_identity(project_root: Path, commit_sha: str, changed_paths: Sequence[str]) -> str:
    status = _git(project_root, "status", "--short", "--untracked-files=all")
    diff = _git(project_root, "diff", "--binary", "HEAD", "--")
    untracked_digests = {}
    for relative in changed_paths:
        candidate = project_root / relative
        try:
            resolved = candidate.resolve()
            resolved.relative_to(project_root.resolve())
        except (OSError, ValueError):
            untracked_digests[relative] = "unavailable"
            continue
        if candidate.is_symlink():
            untracked_digests[relative] = "symlink"
            continue
        if not candidate.is_file():
            untracked_digests[relative] = "missing-or-non-file"
            continue
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        untracked_digests[relative] = digest.hexdigest()
    payload = {
        "commitSha": commit_sha,
        "status": status,
        "diff": diff,
        "changedPaths": list(changed_paths),
        "fileDigests": untracked_digests,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--base-ref")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    try:
        commit_sha = _git(root, "rev-parse", "HEAD").strip()
        changed_paths = collect_changed_paths(root, args.base_ref)
        source_fingerprint = _working_tree_identity(root, commit_sha, changed_paths)
        result = run_fast_precheck(
            root,
            commit_sha=commit_sha,
            source_fingerprint=source_fingerprint,
            changed_paths=changed_paths,
            timeout_seconds=args.timeout,
        )
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            runtime_root = (root / ".nbs_agent_runtime").resolve()
            output = args.output if args.output.is_absolute() else root / args.output
            output = output.resolve()
            output.relative_to(runtime_root)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0 if result["status"] == "PASS" else 2
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"fast acceptance precheck blocked: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
