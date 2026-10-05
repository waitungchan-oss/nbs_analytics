from __future__ import annotations

import json
from pathlib import Path

from backend.agents.documentation_codex_runner import (
    CODEX_DOCUMENTATION_INSTRUCTION,
    CODEX_DOCUMENTATION_V2_INSTRUCTION,
    CodexDocumentationRunner,
)
from backend.agents.workflow_models import canonical_sha256


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


def _v2_evidence(**updates):
    payload = {
        "schemaVersion": "documentation-evidence-v2",
        "taskId": "run-task-v2",
        "generatedAt": "2026-09-28T10:00:00+00:00",
        "runId": "run-task-v2",
        "commitSha": "b" * 40,
        "sourceFingerprint": "c" * 64,
        "selectedTargetId": "handoff.current-conclusion",
        "sources": [{"path": "NBS_ANALYTICS_HANDOFF.md", "sha256": "d" * 64}],
        "gateResults": [
            {"gate": name, "status": "pass", "sourceFingerprint": "c" * 64,
             "evidenceFingerprint": chr(54 + index) * 64}
            for index, name in enumerate(("review", "full-verification", "hermes"))
        ],
        "guardrails": {
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "mayBaseline": "HKD 12,057,968",
        },
        "expectedSectionSha256": "a" * 64,
    }
    payload.update(updates)
    payload["evidenceFingerprint"] = canonical_sha256(payload)
    return payload


def _v2_draft(evidence, *, target_id=None, schema="documentation-draft-v2", content="Bounded summary."):
    payload = {
        "schemaVersion": schema,
        "evidenceFingerprint": evidence["evidenceFingerprint"],
        "status": "ready",
        "proposals": [{
            "targetId": target_id or evidence["selectedTargetId"],
            "content": content,
        }],
    }
    payload["draftFingerprint"] = canonical_sha256(payload)
    return json.dumps(payload, ensure_ascii=False)


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
    assert "final agent message" in CODEX_DOCUMENTATION_INSTRUCTION


def test_runner_accepts_exact_draft_with_matching_evidence_fingerprint():
    process = FakeProcess(stdout=_draft().encode())
    fake_subprocess = FakeSubprocess(process)
    result = CodexDocumentationRunner(fake_subprocess).run(
        ("codex",), input_text=_evidence(), timeout_seconds=120, max_output_bytes=65536,
    )

    assert result.exit_code == 0
    assert fake_subprocess.kwargs["env"]["CODEX_HOME"].endswith(".nbs_agent_runtime/codex_home")
    assert json.loads(result.stdout) == json.loads(_draft())


def test_runner_v2_accepts_only_matching_target_id_and_schema():
    evidence = _v2_evidence()
    fake = FakeSubprocess(FakeProcess(stdout=_v2_draft(evidence).encode()))
    result = CodexDocumentationRunner(fake).run(
        ("codex",), input_text=json.dumps(evidence), timeout_seconds=120,
        max_output_bytes=65536,
    )

    assert result.exit_code == 0
    assert fake.argv[-1] == CODEX_DOCUMENTATION_V2_INSTRUCTION

    mismatch = CodexDocumentationRunner(FakeSubprocess(
        FakeProcess(stdout=_v2_draft(evidence, target_id="runbook.shard-boundary").encode()),
    )).run(("codex",), input_text=json.dumps(evidence), timeout_seconds=120,
            max_output_bytes=65536)
    assert mismatch.exit_code == -2

    wrong_schema = CodexDocumentationRunner(FakeSubprocess(
        FakeProcess(stdout=_v2_draft(evidence, schema="documentation-draft-v1").encode()),
    )).run(("codex",), input_text=json.dumps(evidence), timeout_seconds=120,
            max_output_bytes=65536)
    assert wrong_schema.exit_code == -2


def test_runner_uses_explicit_local_auth_home_without_serializing_it(tmp_path, monkeypatch):
    auth_home = tmp_path / "codex-auth"
    monkeypatch.setenv("NBS_DOCUMENTATION_CODEX_HOME", str(auth_home))
    process = FakeProcess(stdout=_draft().encode())
    fake_subprocess = FakeSubprocess(process)
    result = CodexDocumentationRunner(fake_subprocess, project_root=tmp_path).run(
        ("codex",), input_text=_evidence(), timeout_seconds=120, max_output_bytes=65536,
    )

    assert result.exit_code == 0
    assert fake_subprocess.kwargs["env"]["CODEX_HOME"] == str(auth_home.resolve())
    assert str(auth_home) not in " ".join(fake_subprocess.argv)


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


def test_runner_extracts_final_message_before_applying_output_cap():
    stream = (json.dumps({"type": "turn.started", "padding": "x" * 200}) + "\n") * 10
    stream += json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": _draft()},
    })
    result = CodexDocumentationRunner(FakeSubprocess(FakeProcess(stdout=stream.encode()))).run(
        ("codex",), input_text=_evidence(), timeout_seconds=120, max_output_bytes=256,
    )

    assert result.exit_code == 0


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

    assert len(result.stdout.encode()) == 64
    assert len(result.stderr_tail.encode()) <= 4096
    assert "/private/vault" not in " ".join(fake_subprocess.argv)
    assert "secret" not in " ".join(fake_subprocess.argv)


def test_runner_caps_invalid_multibyte_output_by_utf8_bytes():
    process = FakeProcess(stdout=("摘要" * 100).encode("utf-8"))
    result = CodexDocumentationRunner(FakeSubprocess(process)).run(
        ("codex",), input_text=_evidence(), timeout_seconds=120, max_output_bytes=16,
    )

    assert result.exit_code == -2
    assert len(result.stdout.encode("utf-8")) <= 16


def test_runner_timeout_kills_process_and_returns_bounded_failure():
    process = FakeProcess(timeout=True)
    fake_subprocess = FakeSubprocess(process)
    result = CodexDocumentationRunner(fake_subprocess).run(
        ("codex",), input_text=_evidence(), timeout_seconds=1, max_output_bytes=65536,
    )

    assert result.exit_code == -1
    assert process.killed is True
