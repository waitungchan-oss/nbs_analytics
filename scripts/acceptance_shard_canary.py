"""Run bounded serial-parity canaries for the diagnostic acceptance shards."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.acceptance_rollout_models import RolloutConfig, validate_rollout_config
from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.release_gate_models import ReleaseGateValidationError
from scripts.full_pytest_gate import _parse_summary
from scripts.full_pytest_shard import run_pytest_shard
from scripts.full_pytest_shard_aggregate import aggregate_pytest_shards, compare_serial_and_shard
from scripts.pytest_manifest import collect_pytest_manifest


_CANARY_TIMEOUT_SECONDS = 1800
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_LINEAGE_KEYS = ("manifestFingerprint", "contractFingerprint", "testPopulationFingerprint")


def _counts(value: Mapping[str, Any]) -> dict[str, int]:
    result = value.get("result") if isinstance(value.get("result"), Mapping) else value
    return {key: int(result.get(key, 0)) for key in ("passed", "failed", "skipped")}


def _result(status: str, failure_code: str | None, **fields: Any) -> dict[str, Any]:
    unsigned = {
        "schemaVersion": "acceptance-shard-canary-v1",
        "status": status,
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "rolloutCandidate": "eligible" if status == "PASS" else "ineligible",
        "failureCode": failure_code,
        **fields,
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def _bind_source_identity(
    result: Mapping[str, Any], *, commit_sha: str, source_fingerprint: str,
    manifest_fingerprint: str | None,
    contract_fingerprint: str | None = None,
    test_population_fingerprint: str | None = None,
) -> dict[str, Any]:
    unsigned = dict(result)
    unsigned.pop("evidenceFingerprint", None)
    unsigned.update({"commitSha": commit_sha, "sourceFingerprint": source_fingerprint})
    if manifest_fingerprint is not None:
        if not _SHA64.fullmatch(manifest_fingerprint):
            raise ValueError("manifestFingerprint is invalid")
        unsigned["manifestFingerprint"] = manifest_fingerprint
    for field, value in (
        ("contractFingerprint", contract_fingerprint),
        ("testPopulationFingerprint", test_population_fingerprint),
    ):
        if value is not None:
            if not _SHA64.fullmatch(value):
                raise ValueError(f"{field} is invalid")
            unsigned[field] = value
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def summarize_canary_runs(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize exactly three independent runs using fresh timing evidence."""
    if len(runs) != 3:
        return _result("BLOCKED", "canary_repeat_count_invalid", runCount=len(runs), runs=[])
    compact_runs: list[dict[str, Any]] = []
    failure_code: str | None = None
    expected_lineage: dict[str, str] | None = None
    lineage_mode = any(
        isinstance(run, Mapping) and any(key in run for key in _LINEAGE_KEYS)
        for run in runs
    )
    for run in runs:
        if not isinstance(run, Mapping) or run.get("status") != "PASS":
            failure_code = "canary_run_failed"
            break
        lineage = {key: run.get(key) for key in _LINEAGE_KEYS if key in run}
        if lineage_mode:
            required_lineage_keys = {"manifestFingerprint", "testPopulationFingerprint"}
            if not required_lineage_keys <= set(lineage) or any(
                not isinstance(value, str) or not _SHA64.fullmatch(value)
                for value in lineage.values()
            ):
                failure_code = "canary_lineage_invalid"
                break
            if expected_lineage is None:
                expected_lineage = lineage
            elif set(lineage) != set(expected_lineage) or lineage != expected_lineage:
                failure_code = (
                    "canary_population_mismatch"
                    if lineage.get("testPopulationFingerprint")
                    != expected_lineage.get("testPopulationFingerprint")
                    else "canary_lineage_mismatch"
                )
                break
        parity = run.get("parity")
        if not isinstance(parity, Mapping) or parity.get("status") != "PASS":
            failure_code = "serial_parity_failed" if isinstance(parity, Mapping) else "serial_parity_missing"
            break
        try:
            serial_seconds = float(run["serialSeconds"])
            shard_seconds = float(run["shardSeconds"])
        except (KeyError, TypeError, ValueError):
            failure_code = "canary_timing_invalid"
            break
        if serial_seconds <= 0 or shard_seconds < 0:
            failure_code = "canary_timing_invalid"
            break
        ratio = shard_seconds / serial_seconds
        compact_runs.append({
            "status": "PASS",
            "serialSeconds": serial_seconds,
            "shardSeconds": shard_seconds,
            "speedRatio": ratio,
            "parity": {"status": "PASS"},
            **lineage,
        })
        if ratio > 0.8:
            failure_code = "speedup_threshold_not_met"
            break
    if failure_code is None and len(compact_runs) == 3:
        return _result(
            "PASS", None, runCount=3, runs=compact_runs,
            maxSpeedRatio=max(item["speedRatio"] for item in compact_runs),
        )
    return _result(
        "BLOCKED", failure_code or "canary_run_failed",
        runCount=len(runs), runs=compact_runs,
    )


def _serial_control(project_root: Path, timeout_seconds: int = 1800) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--sandbox-preflight", "required"],
            cwd=project_root, capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "BLOCKED", "failureCode": "serial_control_timeout",
            "result": {"passed": 0, "failed": 0, "skipped": 0},
            "serialSeconds": float(time.perf_counter() - started),
        }
    try:
        parsed = _parse_summary(f"{completed.stdout}\n{completed.stderr}")
    except ValueError:
        parsed = {"passed": 0, "failed": 1, "skipped": 0}
    return {
        "status": "PASS" if completed.returncode == 0 and parsed["failed"] == 0 else "FAIL",
        "failureCode": None if completed.returncode == 0 and parsed["failed"] == 0 else "serial_control_failed",
        "result": {key: parsed.get(key, 0) for key in ("passed", "failed", "skipped")},
        "serialSeconds": max(float(parsed.get("durationSeconds", 0.0)), time.perf_counter() - started),
    }


def _port_readiness(ports: Mapping[str, int]) -> bool:
    child_code = (
        "import socket,sys,time; "
        "s=[]; "
        "[s.append(socket.create_server(('127.0.0.1', int(p)), reuse_port=False)) for p in sys.argv[1:]]; "
        "print('READY', flush=True); "
        "time.sleep(300)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, *(str(port) for port in ports.values())],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if child.poll() is not None:
                return False
            if all(
                _tcp_ready(int(port)) for port in ports.values()
            ):
                return True
            time.sleep(0.02)
        return False
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=1)


def _tcp_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except OSError:
        return False


def _run_shard_canary(root: Path, manifest: Mapping[str, Any], *, repeat: int, index: int, shard_count: int) -> dict[str, Any]:
    """Run one shard with an explicit per-shard process and fixture namespace."""
    run_id = f"canary-{repeat}-{index}-{uuid.uuid4().hex}"
    fixture_root = Path(tempfile.gettempdir()) / f"nbs-canary-{uuid.uuid4().hex}"

    def readiness_probe(ports: dict[str, int]) -> bool:
        # The runtime allocates this mapping from the unique run_id above; the
        # child binder proves that this shard's namespace is actually usable.
        if len(ports) != len(set(ports.values())):
            return False
        return _port_readiness(ports)

    try:
        return run_pytest_shard(
            root, manifest, shard_index=index, shard_count=shard_count,
            run_id=run_id, fixture_root=fixture_root,
            timeout_seconds=_CANARY_TIMEOUT_SECONDS,
            port_readiness_probe=readiness_probe,
        )
    finally:
        if fixture_root.exists() and not fixture_root.is_symlink():
            shutil.rmtree(fixture_root)


def run_canary(*, project_root: Path, config: RolloutConfig, repeats: int = 3) -> dict[str, Any]:
    """Run three source-bound serial controls and diagnostic shard aggregates."""
    validate_rollout_config(config)
    if repeats != 3:
        raise ValueError("canary repeats must be exactly 3")
    if not config.shards_enabled:
        return _result("BLOCKED", "shards_disabled", runCount=0, runs=[])
    source_fingerprint = os.environ.get("NBS_ACCEPTANCE_SOURCE_FINGERPRINT")
    expected_commit_sha = os.environ.get("NBS_ACCEPTANCE_COMMIT_SHA")
    contract_fingerprint = os.environ.get("NBS_ACCEPTANCE_CONTRACT_FINGERPRINT")
    if not source_fingerprint or not _SHA64.fullmatch(source_fingerprint):
        return _result("BLOCKED", "source_fingerprint_required", runCount=0, runs=[])
    if not expected_commit_sha or not _SHA40.fullmatch(expected_commit_sha):
        return _result("BLOCKED", "commit_identity_required", runCount=0, runs=[])
    if contract_fingerprint is not None and not _SHA64.fullmatch(contract_fingerprint):
        return _result("BLOCKED", "contract_fingerprint_invalid", runCount=0, runs=[])
    root = Path(project_root).resolve()
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if commit_sha != expected_commit_sha:
        return _result("BLOCKED", "commit_identity_mismatch", runCount=0, runs=[])
    runs: list[dict[str, Any]] = []
    manifest_fingerprint: str | None = None
    test_population_fingerprint: str | None = None
    for repeat in range(3):
        manifest = collect_pytest_manifest(root, commit_sha=commit_sha, source_fingerprint=source_fingerprint)
        if manifest.get("status") != "PASS":
            runs.append({"status": "BLOCKED", "failureCode": "manifest_invalid"})
            break
        manifest_fingerprint = manifest.get("manifestFingerprint")
        nodeids = manifest.get("nodeids")
        if (
            not isinstance(nodeids, list)
            or any(not isinstance(nodeid, str) or not nodeid.strip() for nodeid in nodeids)
            or len(nodeids) != len(set(nodeids))
        ):
            runs.append({"status": "BLOCKED", "failureCode": "manifest_population_invalid"})
            break
        test_population_fingerprint = canonical_fingerprint({"nodeids": sorted(nodeids)})
        if not isinstance(manifest_fingerprint, str) or not _SHA64.fullmatch(manifest_fingerprint):
            runs.append({
                "status": "BLOCKED",
                "failureCode": "manifest_identity_invalid",
                "manifestFingerprint": manifest_fingerprint,
                "contractFingerprint": contract_fingerprint,
                "testPopulationFingerprint": test_population_fingerprint,
            })
            break
        lineage = {
            "manifestFingerprint": manifest_fingerprint,
            "testPopulationFingerprint": test_population_fingerprint,
        }
        if contract_fingerprint is not None:
            lineage["contractFingerprint"] = contract_fingerprint
        serial = _serial_control(root)
        if serial["status"] != "PASS":
            runs.append({"status": serial["status"], "failureCode": serial.get("failureCode", "serial_control_failed"), "serialSeconds": serial["serialSeconds"], "shardSeconds": 0.0, **lineage})
            break
        shard_started = time.perf_counter()
        def run_one(index: int) -> dict[str, Any]:
            return _run_shard_canary(root, manifest, repeat=repeat, index=index, shard_count=config.shard_count)

        try:
            with ThreadPoolExecutor(max_workers=config.shard_count) as executor:
                shards = list(executor.map(run_one, range(config.shard_count)))
        except (OSError, RuntimeError, ReleaseGateValidationError, subprocess.TimeoutExpired) as exc:
            runs.append({"status": "BLOCKED", "failureCode": "shard_runner_error", "detail": str(exc), "serialSeconds": serial["serialSeconds"], "shardSeconds": time.perf_counter() - shard_started, **lineage})
            break
        shard_seconds = time.perf_counter() - shard_started
        if any(shard.get("status") != "PASS" for shard in shards):
            runs.append({"status": "BLOCKED", "failureCode": "shard_run_failed", "serialSeconds": serial["serialSeconds"], "shardSeconds": shard_seconds, **lineage})
            break
        aggregate = aggregate_pytest_shards(manifest, shards, expected_commit_sha=commit_sha, expected_source_fingerprint=source_fingerprint)
        parity = compare_serial_and_shard(serial, aggregate)
        runs.append({"status": "PASS" if parity["status"] == "PASS" else "BLOCKED", "failureCode": parity.get("failureCode"), "serialSeconds": serial["serialSeconds"], "shardSeconds": shard_seconds, "parity": parity, **lineage})
        if parity["status"] != "PASS":
            break
    summary = summarize_canary_runs(runs) if len(runs) == 3 else _result("BLOCKED", (runs[-1].get("failureCode") if runs else "canary_run_failed"), runCount=len(runs), runs=list(runs))
    return _bind_source_identity(
        summary, commit_sha=commit_sha, source_fingerprint=source_fingerprint,
        manifest_fingerprint=manifest_fingerprint,
        contract_fingerprint=contract_fingerprint,
        test_population_fingerprint=test_population_fingerprint,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_canary(project_root=args.project_root, config=RolloutConfig.from_environment(os.environ), repeats=args.repeats)
    except (OSError, ValueError, RuntimeError, ReleaseGateValidationError, subprocess.SubprocessError) as exc:
        result = _result("BLOCKED", "canary_runner_error", runCount=0, runs=[], detail=str(exc))
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.expanduser().resolve().write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
