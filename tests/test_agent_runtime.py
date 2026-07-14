import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.agents.agent_runtime import (
    AgentRuntime,
    SubprocessAgentRunner,
    agent_request_fingerprint,
    resolve_runtime_output_path,
)
from backend.agents.evidence_models import EvidenceBundle, canonical_fingerprint


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
        allowed_executables=(Path(sys.executable).name,),
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
        allowed_executables=(Path(sys.executable).name,),
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
    runner = SubprocessAgentRunner([sys.executable, str(script)], allowed_executables=(Path(sys.executable).name,))
    with pytest.raises(ValueError, match="valid JSON"):
        runner.run({"bundleFingerprint": "x"})


def test_runner_enforces_timeout(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(1)", encoding="utf-8")
    runner = SubprocessAgentRunner(
        [sys.executable, str(script)],
        allowed_executables=(Path(sys.executable).name,),
        timeout_seconds=1,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run({"bundleFingerprint": "x"})


def test_output_path_must_stay_inside_agent_runtime(tmp_path):
    allowed = resolve_runtime_output_path(tmp_path, ".nbs_agent_runtime/reports/context.json")
    assert allowed == (tmp_path / ".nbs_agent_runtime/reports/context.json").resolve()
    with pytest.raises(PermissionError, match="Agent output"):
        resolve_runtime_output_path(tmp_path, "docs/context.json")
