from __future__ import annotations

import json
from pathlib import Path

from backend.agents.documentation_codex_runner import (
    CODEX_DOCUMENTATION_INSTRUCTION,
    CodexDocumentationRunner,
)


class FakeProcess:
    def __init__(self, *, stdout=b"{}", stderr=b"", returncode=0, timeout=False):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.timeout = timeout
        self.stdin_payload = None
        self.killed = False

    def communicate(self, input=None, timeout=None):
        self.stdin_payload = input
        if self.timeout:
            raise TimeoutError
        return self.stdout, self.stderr

    def kill(self):
        self.killed = True


class FakeSubprocess:
    def __init__(self, process):
        self.process = process
        self.argv = None
        self.kwargs = None

    def Popen(self, argv, **kwargs):
        self.argv = tuple(argv)
        self.kwargs = kwargs
        return self.process


def _evidence(**updates):
    payload = {
        "schemaVersion": "documentation-evidence-v1",
        "taskId": "run-task-3",
        "generatedAt": "2026-07-18T12:00:00+08:00",
        "evidenceFingerprint": "a" * 64,
    }
    payload.update(updates)
    return json.dumps(payload)


def _draft(*, evidence_fingerprint="a" * 64):
    return json.dumps({
        "schemaVersion": "documentation-draft-v1",
        "evidenceFingerprint": evidence_fingerprint,
        "status": "ready",
        "proposals": [{"targetKind": "brief_backfill", "content": "Summary."}],
    })


def test_runner_passes_evidence_only_and_rejects_non_json(tmp_path):
    process = FakeProcess(stdout=b"not-json")
    fake_subprocess = FakeSubprocess(process)
    result = CodexDocumentationRunner(fake_subprocess, project_root=tmp_path).run(
        ("codex",), input_text=_evidence(), timeout_seconds=120, max_output_bytes=65536,
    )

    assert process.stdin_payload.decode() == _evidence()
    assert result.exit_code != 0
    assert fake_subprocess.argv[:5] == ("codex", "exec", "--json", "--sandbox", "read-only")
    assert "--json" in fake_subprocess.argv
    assert "--ephemeral" in fake_subprocess.argv
    assert "--ignore-user-config" in fake_subprocess.argv
    assert CODEX_DOCUMENTATION_INSTRUCTION in fake_subprocess.argv


def test_runner_accepts_exact_draft_with_matching_evidence_fingerprint():
    process = FakeProcess(stdout=_draft().encode())
    fake_subprocess = FakeSubprocess(process)
    result = CodexDocumentationRunner(fake_subprocess).run(
        ("codex",), input_text=_evidence(), timeout_seconds=120, max_output_bytes=65536,
    )

    assert result.exit_code == 0
    assert fake_subprocess.kwargs["env"]["CODEX_HOME"].endswith(".nbs_agent_runtime/codex_home")
    assert json.loads(result.stdout) == json.loads(_draft())


def test_runner_extracts_final_agent_message_from_codex_jsonl():
    stream = json.dumps({"type": "thread.started"}) + "\n" + json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": _draft()},
    })
    process = FakeProcess(stdout=stream.encode())
    result = CodexDocumentationRunner(FakeSubprocess(process)).run(
        ("codex",), input_text=_evidence(), timeout_seconds=120, max_output_bytes=65536,
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == json.loads(_draft())


def test_runner_accepts_valid_draft_when_cli_has_nonzero_exit():
    process = FakeProcess(stdout=_draft().encode(), returncode=1)
    fake_subprocess = FakeSubprocess(process)
    result = CodexDocumentationRunner(fake_subprocess).run(
        ("codex",), input_text=_evidence(), timeout_seconds=120, max_output_bytes=65536,
    )

    assert result.exit_code == 0


def test_runner_rejects_draft_with_mismatched_evidence_fingerprint():
    process = FakeProcess(stdout=_draft(evidence_fingerprint="b" * 64).encode())
    fake_subprocess = FakeSubprocess(process)
    result = CodexDocumentationRunner(fake_subprocess).run(
        ("codex",), input_text=_evidence(), timeout_seconds=120, max_output_bytes=65536,
    )

    assert result.exit_code == -2


def test_runner_rejects_final_proposal_payload():
    process = FakeProcess(stdout=json.dumps({
        "schemaVersion": "documentation-proposal-v1",
        "evidenceFingerprint": "a" * 64,
        "status": "ready",
        "proposals": [],
    }).encode())
    fake_subprocess = FakeSubprocess(process)
    result = CodexDocumentationRunner(fake_subprocess).run(
        ("codex",), input_text=_evidence(), timeout_seconds=120, max_output_bytes=65536,
    )

    assert result.exit_code == -2


def test_runner_rejects_draft_with_unknown_key():
    payload = json.loads(_draft())
    payload["targetIdentity"] = "docs/briefs/task-3.md"
    process = FakeProcess(stdout=json.dumps(payload).encode())
    fake_subprocess = FakeSubprocess(process)
    result = CodexDocumentationRunner(fake_subprocess).run(
        ("codex",), input_text=_evidence(), timeout_seconds=120, max_output_bytes=65536,
    )

    assert result.exit_code == -2


def test_runner_rejects_wrong_evidence_schema_before_spawn():
    process = FakeProcess()
    fake_subprocess = FakeSubprocess(process)
    result = CodexDocumentationRunner(fake_subprocess).run(
        ("codex",), input_text=_evidence(schemaVersion="wrong"),
        timeout_seconds=120, max_output_bytes=65536,
    )

    assert result.exit_code != 0
    assert fake_subprocess.argv is None


def test_runner_caps_stdout_and_stderr_without_persisting_command_or_paths(tmp_path):
    process = FakeProcess(stdout=b"x" * 100, stderr=b"/private/vault/secret" * 100)
    fake_subprocess = FakeSubprocess(process)
    result = CodexDocumentationRunner(fake_subprocess, project_root=tmp_path).run(
        ("codex", "exec", "--token", "secret", "/private/vault"),
        input_text=_evidence(), timeout_seconds=120, max_output_bytes=64,
    )

    assert len(result.stdout.encode()) == 65
    assert len(result.stderr_tail.encode()) <= 4096
    assert "/private/vault" not in " ".join(fake_subprocess.argv)
    assert "secret" not in " ".join(fake_subprocess.argv)


def test_runner_timeout_kills_process_and_returns_bounded_failure():
    process = FakeProcess(timeout=True)
    fake_subprocess = FakeSubprocess(process)
    result = CodexDocumentationRunner(fake_subprocess).run(
        ("codex",), input_text=_evidence(), timeout_seconds=1, max_output_bytes=65536,
    )

    assert result.exit_code == -1
    assert process.killed is True
