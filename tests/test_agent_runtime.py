import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from backend.agents.agent_runtime import (
    AgentRuntime,
    SandboxedSubprocessAgentRunner,
    SubprocessAgentRunner,
    agent_request_fingerprint,
    resolve_runtime_output_path,
)
from backend.agents.evidence_models import EvidenceBundle


PYTHON_ALLOWLIST = (sys.executable,)


def _init_sandbox_fixture(root: Path) -> None:
    (root / ".gitignore").write_text(
        "*.db\n.env\n.nbs_runtime/\n", encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Sandbox Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)


def _response(summary: str = "done") -> str:
    return json.dumps({
        "schemaVersion": "implementation-response-v1",
        "status": "completed",
        "summary": summary,
        "requestedValidationCommandIds": [],
    })


def _sandbox_runner(
    root: Path,
    script: Path,
    allowed_write_paths: tuple[str, ...],
) -> SandboxedSubprocessAgentRunner:
    return SandboxedSubprocessAgentRunner(
        [sys.executable, str(script)],
        allowed_executables=PYTHON_ALLOWLIST,
        project_root=root,
        allowed_write_paths=allowed_write_paths,
        timeout_seconds=5,
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec contract")
def test_sandboxed_runner_allows_only_exact_approved_source_write(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    target = worktree / "src/allowed.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    (worktree / ".env").write_text("MUST_NOT_BE_STAGED=1\n", encoding="utf-8")
    (worktree / "formal.db").write_bytes(b"FORMAL")
    script = worktree / "agent.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "payload = json.load(sys.stdin)\n"
        "root = pathlib.Path(payload['execution']['worktree'])\n"
        "assert payload['task']['approvedWorktree'] == str(root)\n"
        "assert not (root / '.env').exists()\n"
        "assert not (root / 'formal.db').exists()\n"
        "(root / 'src/allowed.py').write_text('value = 2\\n', encoding='utf-8')\n"
        f"print({ _response('staged allowed write')!r})\n",
        encoding="utf-8",
    )
    _init_sandbox_fixture(worktree)

    result = _sandbox_runner(worktree, script, ("src/allowed.py",)).run({
        "task": {"approvedWorktree": str(worktree)},
    })

    assert result["status"] == "completed"
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert (worktree / ".env").read_text(encoding="utf-8") == "MUST_NOT_BE_STAGED=1\n"
    assert (worktree / "formal.db").read_bytes() == b"FORMAL"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec contract")
def test_sandboxed_runner_excludes_secret_even_if_it_was_accidentally_tracked(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    target = worktree / "src/allowed.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    script = worktree / "agent.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "payload = json.load(sys.stdin)\n"
        "root = pathlib.Path(payload['execution']['worktree'])\n"
        "assert not (root / '.env').exists()\n"
        "pathlib.Path('src/allowed.py').write_text('value = 2\\n', encoding='utf-8')\n"
        f"print({_response('tracked secret excluded')!r})\n",
        encoding="utf-8",
    )
    _init_sandbox_fixture(worktree)
    (worktree / ".env").write_text("TRACKED_SECRET=do-not-stage\n", encoding="utf-8")
    subprocess.run(["git", "add", "-f", ".env"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-qm", "accidental secret"], cwd=worktree, check=True)

    result = _sandbox_runner(worktree, script, ("src/allowed.py",)).run({"task": {}})

    assert result["status"] == "completed"
    assert target.read_text(encoding="utf-8") == "value = 2\n"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec contract")
def test_sandboxed_runner_denies_external_secret_read(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    target = worktree / "src/allowed.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    external = tmp_path / "external.env"
    external.write_text("TOP_SECRET=do-not-leak\n", encoding="utf-8")
    script = worktree / "agent.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "json.load(sys.stdin)\n"
        f"pathlib.Path({str(external)!r}).read_text(encoding='utf-8')\n"
        f"print({_response('secret leaked')!r})\n",
        encoding="utf-8",
    )
    _init_sandbox_fixture(worktree)

    with pytest.raises(RuntimeError, match="Agent command failed"):
        _sandbox_runner(worktree, script, ("src/allowed.py",)).run({"task": "hostile"})

    assert target.read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec contract")
def test_sandboxed_runner_denies_localhost_network_and_indirect_formal_write(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    target = worktree / "src/allowed.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    formal = tmp_path / "external-formal.db"
    formal.write_bytes(b"FORMAL")

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(1)
    port = listener.getsockname()[1]
    connected = threading.Event()

    def serve() -> None:
        try:
            connection, _ = listener.accept()
        except TimeoutError:
            return
        with connection:
            connected.set()
            formal.write_bytes(b"CORRUPTED_BY_LOCAL_SERVICE")

    server = threading.Thread(target=serve)
    server.start()
    script = worktree / "agent.py"
    script.write_text(
        "import json, socket, sys\n"
        "json.load(sys.stdin)\n"
        f"socket.create_connection(('127.0.0.1', {port}), timeout=1).close()\n"
        f"print({_response('network reached')!r})\n",
        encoding="utf-8",
    )
    _init_sandbox_fixture(worktree)

    with pytest.raises(RuntimeError, match="Agent command failed"):
        _sandbox_runner(worktree, script, ("src/allowed.py",)).run({"task": "hostile"})

    server.join(timeout=2)
    listener.close()
    assert not connected.is_set()
    assert formal.read_bytes() == b"FORMAL"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec contract")
def test_sandboxed_runner_kills_background_child_before_staging_validation(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    target = worktree / "src/allowed.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    script = worktree / "agent.py"
    script.write_text(
        "import json, os, pathlib, sys, time\n"
        "json.load(sys.stdin)\n"
        "if os.fork() == 0:\n"
        "    os.close(1); os.close(2)\n"
        "    time.sleep(1)\n"
        "    pathlib.Path('src/allowed.py').write_text('value = 99\\n', encoding='utf-8')\n"
        "    os._exit(0)\n"
        f"print({_response('parent complete')!r})\n",
        encoding="utf-8",
    )
    _init_sandbox_fixture(worktree)

    result = _sandbox_runner(worktree, script, ("src/allowed.py",)).run({"task": "child"})

    time.sleep(1.2)
    assert result["status"] == "completed"
    assert target.read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec contract")
def test_sandboxed_runner_atomically_replaces_actual_hardlink_without_touching_external_inode(
    tmp_path, monkeypatch,
):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    target = worktree / "src/allowed.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    external = tmp_path / "external.py"
    external.write_text("FORMAL\n", encoding="utf-8")
    script = worktree / "agent.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "json.load(sys.stdin)\n"
        "pathlib.Path('src/allowed.py').write_text('value = 2\\n', encoding='utf-8')\n"
        f"print({_response('race-safe write')!r})\n",
        encoding="utf-8",
    )
    _init_sandbox_fixture(worktree)
    runner = _sandbox_runner(worktree, script, ("src/allowed.py",))
    original_apply = runner._apply_staged_changes

    def race_before_apply(staging, changes):
        target.unlink()
        os.link(external, target)
        original_apply(staging, changes)

    monkeypatch.setattr(runner, "_apply_staged_changes", race_before_apply)

    result = runner.run({"task": "race"})

    assert result["status"] == "completed"
    assert external.read_text(encoding="utf-8") == "FORMAL\n"
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert target.stat().st_ino != external.stat().st_ino


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec contract")
def test_sandboxed_runner_denies_first_transient_ignored_db_write(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    formal_db = worktree / "formal.db"
    formal_db.write_bytes(b"FORMAL")
    allowed = worktree / "src/allowed.py"
    allowed.parent.mkdir()
    allowed.write_text("value = 1\n", encoding="utf-8")
    sentinel = worktree / "src/touched.txt"
    script = worktree / "agent.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "json.load(sys.stdin)\n"
        f"db = pathlib.Path({str(formal_db)!r})\n"
        f"sentinel = pathlib.Path({str(sentinel)!r})\n"
        "try:\n"
        "    db.write_bytes(b'TRANSIENT')\n"
        "except PermissionError:\n"
        "    pass\n"
        "else:\n"
        "    sentinel.write_text('touched', encoding='utf-8')\n"
        "pathlib.Path('src/allowed.py').write_text('value = 2\\n', encoding='utf-8')\n"
        f"print({_response('transient denied')!r})\n",
        encoding="utf-8",
    )
    _init_sandbox_fixture(worktree)

    result = _sandbox_runner(
        worktree,
        script,
        ("src/allowed.py", "src/touched.txt"),
    ).run({"task": "transient"})

    assert result["status"] == "completed"
    assert formal_db.read_bytes() == b"FORMAL"
    assert not sentinel.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec contract")
def test_sandboxed_runner_rejects_symlink_write_target(tmp_path):
    external = tmp_path / "external.py"
    external.write_text("value = 1\n", encoding="utf-8")
    link = tmp_path / "allowed.py"
    link.symlink_to(external)
    script = tmp_path / "agent.py"
    script.write_text("print('{}')\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="symlink"):
        _sandbox_runner(tmp_path, script, ("allowed.py",))


def test_sandboxed_runner_accepts_existing_needs_repair_response_contract():
    response = {
        "schemaVersion": "implementation-response-v1",
        "status": "needs_repair",
        "summary": "targeted validation failed",
        "requestedValidationCommandIds": ["pytest_targeted"],
    }

    assert SandboxedSubprocessAgentRunner._validate_response(
        json.dumps(response)
    ) == response


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


def test_output_path_rejects_runtime_root_itself(tmp_path):
    with pytest.raises(PermissionError, match="file below"):
        resolve_runtime_output_path(tmp_path, ".nbs_agent_runtime")


def test_output_path_rejects_symlinked_runtime_parent(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / ".nbs_agent_runtime").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PermissionError, match="symlink"):
        resolve_runtime_output_path(project, ".nbs_agent_runtime/reports/review.json")


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


def test_runtime_output_validator_rejects_fresh_and_cached_invalid_results(tmp_path):
    class Runner:
        calls = 0

        def run(self, payload):
            self.calls += 1
            return {"schemaVersion": "context-summary-v1", "status": "bad" if self.calls < 3 else "ready"}

    def validate(result):
        if result.get("status") != "ready":
            raise ValueError("invalid full output")
        return result

    runtime = AgentRuntime(tmp_path / ".nbs_agent_runtime")
    runner = Runner()
    runtime.run("context", make_bundle(), runner, "context-summary-v1", "contract")
    with pytest.raises(ValueError, match="invalid full output"):
        runtime.run(
            "context", make_bundle(), runner, "context-summary-v1", "contract",
            output_validator=validate,
        )
    result = runtime.run(
        "context", make_bundle(), runner, "context-summary-v1", "contract",
        output_validator=validate,
    )
    assert result["status"] == "ready"
    assert runner.calls == 3


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
