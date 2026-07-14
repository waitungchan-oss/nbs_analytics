import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from backend.agents.agent_runtime import (
    AgentRuntime,
    SubprocessAgentRunner,
    agent_request_fingerprint,
    resolve_runtime_output_path,
)
from backend.agents.evidence_models import EvidenceBundle


PYTHON_ALLOWLIST = (sys.executable,)


def make_bundle(objective: str = "x") -> EvidenceBundle:
    return EvidenceBundle(
        schema_version="context-evidence-v1",
        task={"id": "x", "objective": objective},
        repository={"branch": "main", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
    )


def test_subprocess_runner_rejects_unapproved_executable():
    with pytest.raises(PermissionError, match="executable"):
        SubprocessAgentRunner(["bash", "-c", "cat"], allowed_executables=("codex",))


def test_subprocess_runner_uses_json_object_and_shell_false(tmp_path, monkeypatch):
    script = tmp_path / "agent.py"
    script.write_text(
        "import json,sys; p=json.load(sys.stdin); "
        "print(json.dumps({'schemaVersion':'context-summary-v1','echo':p['value']}))",
        encoding="utf-8",
    )
    calls = []
    original_run = __import__("subprocess").run

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return original_run(*args, **kwargs)

    monkeypatch.setattr("backend.agents.agent_runtime.subprocess.run", spy)
    runner = SubprocessAgentRunner(
        [sys.executable, str(script)],
        allowed_executables=PYTHON_ALLOWLIST,
        timeout_seconds=3,
    )
    assert runner.run({"value": "ok"})["echo"] == "ok"
    assert calls[0]["shell"] is False
    assert calls[0]["timeout"] == 3


def test_runtime_caches_same_fingerprint_and_writes_telemetry(tmp_path):
    script = tmp_path / "agent.py"
    script.write_text(
        "import json,sys; p=json.load(sys.stdin); "
        "print(json.dumps({'schemaVersion':'context-summary-v1','status':'ready',"
        "'taskUnderstanding':['ok'],'contextFingerprint':p['bundleFingerprint']}))",
        encoding="utf-8",
    )
    runner = SubprocessAgentRunner(
        [sys.executable, str(script)],
        allowed_executables=PYTHON_ALLOWLIST,
    )
    runtime = AgentRuntime(tmp_path / ".nbs_agent_runtime")

    first = runtime.run("context", make_bundle(), runner, output_schema="context-summary-v1", instructions="contract-v1")
    second = runtime.run("context", make_bundle(), runner, output_schema="context-summary-v1", instructions="contract-v1")
    runtime.run("context", make_bundle("changed"), runner, output_schema="context-summary-v1", instructions="contract-v1")
    runtime.run("context", make_bundle("changed"), runner, output_schema="context-summary-v1", instructions="contract-v2")

    assert first == second
    lines = (tmp_path / ".nbs_agent_runtime/telemetry/agent_runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[1])["cacheHit"] is True
    assert json.loads(lines[2])["cacheHit"] is False
    assert json.loads(lines[3])["cacheHit"] is False
    assert "contract-v1" not in lines[0]
    assert "HKD 12,057,968" not in lines[0]


def test_request_fingerprint_includes_public_evidence_and_contract_parts():
    source = make_bundle()
    baseline = agent_request_fingerprint(source, instructions="a", output_schema="s", evidence_payload={"public": 1})
    assert baseline != agent_request_fingerprint(source, instructions="b", output_schema="s", evidence_payload={"public": 1})
    assert baseline != agent_request_fingerprint(source, instructions="a", output_schema="t", evidence_payload={"public": 1})
    assert baseline != agent_request_fingerprint(source, instructions="a", output_schema="s", evidence_payload={"public": 2})
    assert baseline != agent_request_fingerprint(make_bundle("changed"), instructions="a", output_schema="s", evidence_payload={"public": 1})


def test_input_overflow_does_not_call_runner(tmp_path):
    class NeverRunner:
        def run(self, payload):
            raise AssertionError("runner must not be called")

    result = AgentRuntime(tmp_path / ".nbs_agent_runtime", input_token_limit=1).run(
        "context", make_bundle("large"), NeverRunner(), output_schema="context-summary-v1", instructions="contract"
    )
    assert result["status"] == "context_overflow"
    assert not list((tmp_path / ".nbs_agent_runtime/reports").glob("*"))


def test_output_budget_failure_is_not_cached(tmp_path):
    class VerboseRunner:
        calls = 0

        def run(self, payload):
            self.calls += 1
            return {"schemaVersion": "context-summary-v1", "text": "x" * 100}

    runner = VerboseRunner()
    runtime = AgentRuntime(tmp_path / ".nbs_agent_runtime", output_token_limit=1)
    with pytest.raises(ValueError, match="output token budget"):
        runtime.run("context", make_bundle(), runner, output_schema="context-summary-v1", instructions="contract")
    assert runner.calls == 1
    assert not list((tmp_path / ".nbs_agent_runtime/reports").glob("*"))


def test_runner_rejects_non_json_output(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text("print('not-json')", encoding="utf-8")
    runner = SubprocessAgentRunner([sys.executable, str(script)], allowed_executables=PYTHON_ALLOWLIST)
    with pytest.raises(ValueError, match="valid JSON"):
        runner.run({"bundleFingerprint": "x"})


def test_runner_enforces_timeout(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(1)", encoding="utf-8")
    runner = SubprocessAgentRunner(
        [sys.executable, str(script)],
        allowed_executables=PYTHON_ALLOWLIST,
        timeout_seconds=1,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run({"bundleFingerprint": "x"})


def test_output_path_must_stay_inside_agent_runtime(tmp_path):
    allowed = resolve_runtime_output_path(tmp_path, ".nbs_agent_runtime/reports/context.json")
    assert allowed == (tmp_path / ".nbs_agent_runtime/reports/context.json").resolve()
    with pytest.raises(PermissionError, match="Agent output"):
        resolve_runtime_output_path(tmp_path, "docs/context.json")


def test_allowlist_rejects_same_basename_from_different_path(tmp_path, monkeypatch):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    allowed = allowed_dir / "codex"
    allowed.write_text("#!/bin/sh\n", encoding="utf-8")
    allowed.chmod(0o755)
    impostor = tmp_path / "codex"
    impostor.write_text("#!/bin/sh\n", encoding="utf-8")
    impostor.chmod(0o755)
    monkeypatch.setenv("PATH", str(allowed_dir))
    with pytest.raises(PermissionError, match="allowlisted"):
        SubprocessAgentRunner([str(impostor)], allowed_executables=("codex",))


def test_runtime_root_must_be_named_nbs_agent_runtime(tmp_path):
    with pytest.raises(PermissionError, match="runtime root"):
        AgentRuntime(tmp_path / "outside")


def test_same_fingerprint_threaded_fill_calls_runner_once(tmp_path):
    class SlowRunner:
        calls = 0
        lock = threading.Lock()

        def run(self, payload):
            with self.lock:
                self.calls += 1
            time.sleep(0.1)
            return {"schemaVersion": "context-summary-v1", "status": "ready"}

    runtime = AgentRuntime(tmp_path / ".nbs_agent_runtime")
    runner = SlowRunner()
    results = []
    errors = []

    def invoke():
        try:
            results.append(runtime.run("context", make_bundle(), runner, "context-summary-v1", "contract"))
        except Exception as exc:  # pragma: no cover - makes thread failure visible
            errors.append(exc)

    threads = [threading.Thread(target=invoke) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(results) == 4
    assert runner.calls == 1


def test_telemetry_status_is_allowlisted_and_jsonl_rotates(tmp_path):
    class MaliciousRunner:
        def run(self, payload):
            return {"schemaVersion": "context-summary-v1", "status": "x" * 100_000}

    runtime = AgentRuntime(tmp_path / ".nbs_agent_runtime", output_token_limit=30_000)
    runtime.run("context", make_bundle(), MaliciousRunner(), "context-summary-v1", "contract")

    telemetry = tmp_path / ".nbs_agent_runtime/telemetry/agent_runs.jsonl"
    telemetry.parent.mkdir(parents=True, exist_ok=True)
    telemetry.write_text("x" * (1024 * 1024), encoding="utf-8")
    runtime._telemetry(
        telemetry,
        agent_name="a" * 500,
        bundle=make_bundle(),
        request_fingerprint="a" * 64,
        input_text="small",
        result={"schemaVersion": "context-summary-v1", "status": "z" * 500},
        cache_hit=False,
        started=time.perf_counter(),
    )
    assert telemetry.stat().st_size < 4096
    rotated = telemetry.with_name("agent_runs.jsonl.1")
    assert rotated.exists()
    record = json.loads(telemetry.read_text(encoding="utf-8"))
    assert record["agent"] == "a" * 64
    assert record["result"] == "unknown"


@pytest.mark.parametrize("malformed_status", [["ready"], {"status": "ready"}, None])
def test_non_string_telemetry_status_is_unknown_on_fresh_and_cache_hit(tmp_path, malformed_status):
    class Runner:
        calls = 0

        def run(self, payload):
            self.calls += 1
            return {"schemaVersion": "context-summary-v1", "status": malformed_status}

    runtime = AgentRuntime(tmp_path / ".nbs_agent_runtime")
    runner = Runner()
    runtime.run("context", make_bundle(), runner, "context-summary-v1", "contract")
    runtime.run("context", make_bundle(), runner, "context-summary-v1", "contract")

    assert runner.calls == 1
    lines = (tmp_path / ".nbs_agent_runtime/telemetry/agent_runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-2])["result"] == "unknown"
    assert json.loads(lines[-1])["result"] == "unknown"
