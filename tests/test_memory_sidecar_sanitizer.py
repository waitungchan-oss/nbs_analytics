from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.agents.memory_sidecar_models import MemorySourceRef, MemorySidecarSchemaError
from backend.agents.memory_sidecar_sanitizer import MemorySanitizer
from backend.agents.memory_sidecar_gate import CompletedRunGate


RUN_ID = "run-task3"
COMMIT = "a" * 40
NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


def _write(path: Path, payload: object) -> str:
    if isinstance(payload, dict) and path.name != "manifest.json":
        payload = {**payload, "runId": RUN_ID, "gitHead": COMMIT}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _run(tmp_path: Path, *, status: str = "completed", review: str = "pass", verification: bool = True, hermes: str = "pass", documentation: object | None = None, include_documentation: bool = True, implementation: dict | None = None, manifest_run_id: str = RUN_ID) -> Path:
    root = tmp_path / "runtime" / "runs"
    run = root / RUN_ID
    run.mkdir(parents=True)
    _write(run / "manifest.json", {"runId": manifest_run_id, "gitHead": COMMIT})
    _write(run / "status.json", {"status": status})
    _write(run / "review.json", {"verdict": review})
    _write(run / "full-verification.json", {"fullPytest": {"exitCode": 0 if verification else 1}, "acceptance": {"status": "passed" if verification else "failed"}})
    _write(run / "hermes.json", {"overallStatus": hermes})
    if include_documentation:
        _write(run / "documentation-evidence.json", documentation if documentation is not None else {"status": "no_doc"})
    _write(run / "implementation.json", implementation or {"memoryCandidates": []})
    return root


def _candidate_payload(*, source_path: str = "review.json", summary: str = "review verdict: pass", kind: str = "verification_pattern") -> dict:
    return {
        "kind": kind,
        "summary": summary,
        "sourceRefs": [{"runId": RUN_ID, "artifactPath": source_path, "commit": COMMIT}],
        "freshness": {"generatedAt": NOW.isoformat(), "expiresAt": (NOW + timedelta(days=30)).isoformat(), "policyVersion": "memory-sidecar-policy-v1"},
        "confidence": "high",
    }



def test_sanitize_completed_run_preserves_only_verified_bounded_identity(tmp_path: Path) -> None:
    payload = {"memoryCandidates": [_candidate_payload()]}
    root = _run(tmp_path, implementation=payload)
    run = root / RUN_ID
    candidate = MemorySanitizer.sanitize_completed_run(
        gate=CompletedRunGate.from_run(root, RUN_ID), allowed_kinds=("verification_pattern",), now=NOW
    )[0]
    assert candidate.kind == "verification_pattern"
    assert candidate.source_refs == (MemorySourceRef(RUN_ID, "review.json", hashlib.sha256((run / "review.json").read_bytes()).hexdigest(), COMMIT),)
    assert candidate.summary == "review verdict: pass"
    assert not (root / "memory-candidates.json").exists()


def test_invalid_or_disallowed_candidate_is_dropped_fail_closed(tmp_path: Path) -> None:
    root = _run(tmp_path, implementation={"memoryCandidates": [_candidate_payload(kind="decision"), _candidate_payload(source_path="missing.json")]})
    assert MemorySanitizer.sanitize_completed_run(gate=CompletedRunGate.from_run(root, RUN_ID), allowed_kinds=("verification_pattern",), now=NOW) == ()


@pytest.mark.parametrize("path", ["/tmp/review.json", "../review.json", "Secrets/key.txt", "data.sqlite"])
def test_source_ref_rejects_absolute_traversal_symlink_and_denied_paths(tmp_path: Path, path: str) -> None:
    root = _run(tmp_path)
    with pytest.raises((ValueError, MemorySidecarSchemaError, PermissionError)):
        ref = MemorySourceRef(RUN_ID, path, "b" * 64, COMMIT)
        MemorySanitizer.validate_source_ref(source_ref=ref, run_root=root / RUN_ID)


def test_source_ref_rejects_symlink_artifact(tmp_path: Path) -> None:
    root = _run(tmp_path)
    run = root / RUN_ID
    link = run / "review-link.json"
    link.symlink_to(run / "review.json")
    ref = MemorySourceRef(RUN_ID, "review-link.json", hashlib.sha256((run / "review.json").read_bytes()).hexdigest(), COMMIT)
    with pytest.raises(PermissionError):
        MemorySanitizer.validate_source_ref(source_ref=ref, run_root=run)


def test_redact_summary_removes_sensitive_values_without_guessing(tmp_path: Path) -> None:
    assert MemorySanitizer.redact_summary("token=supersecret /Users/user/private.json") == ""
    root = _run(tmp_path, implementation={"memoryCandidates": [_candidate_payload(summary="baseline HKD 12,057,968") ]})
    assert MemorySanitizer.sanitize_completed_run(gate=CompletedRunGate.from_run(root, RUN_ID), allowed_kinds=("verification_pattern",), now=NOW) == ()


def test_unsupported_summary_is_dropped_even_when_source_identity_is_valid(tmp_path: Path) -> None:
    payload = _candidate_payload(summary="Keep strict review evidence bounded.")
    root = _run(tmp_path, implementation={"memoryCandidates": [payload]})
    assert MemorySanitizer.sanitize_completed_run(gate=CompletedRunGate.from_run(root, RUN_ID), allowed_kinds=("verification_pattern",), now=NOW) == ()


def test_mutation_after_gate_snapshot_is_dropped(tmp_path: Path) -> None:
    payload = {"memoryCandidates": [_candidate_payload()]}
    root = _run(tmp_path, implementation=payload)
    gate = CompletedRunGate.from_run(root, RUN_ID)
    _write(root / RUN_ID / "review.json", {"verdict": "pass", "changedAfterGate": True})
    assert MemorySanitizer.sanitize_completed_run(gate=gate, allowed_kinds=("verification_pattern",), now=NOW) == ()


def test_deterministic_fingerprint_and_freshness_are_required(tmp_path: Path) -> None:
    root = _run(tmp_path, implementation={"memoryCandidates": [_candidate_payload()]})
    first = MemorySanitizer.sanitize_completed_run(gate=CompletedRunGate.from_run(root, RUN_ID), allowed_kinds=("verification_pattern",), now=NOW)
    second = MemorySanitizer.sanitize_completed_run(gate=CompletedRunGate.from_run(root, RUN_ID), allowed_kinds=("verification_pattern",), now=NOW)
    assert first[0].memory_fingerprint == second[0].memory_fingerprint
    assert first[0].expires_at > first[0].generated_at


@pytest.mark.parametrize("freshness", [
    {"generatedAt": (NOW - timedelta(days=31)).isoformat(), "expiresAt": (NOW - timedelta(days=1)).isoformat(), "policyVersion": "memory-sidecar-policy-v1"},
    {"generatedAt": (NOW + timedelta(minutes=1)).isoformat(), "expiresAt": (NOW + timedelta(days=1)).isoformat(), "policyVersion": "memory-sidecar-policy-v1"},
])
def test_expired_or_future_candidate_is_dropped(tmp_path: Path, freshness: dict) -> None:
    payload = _candidate_payload()
    payload["freshness"] = freshness
    root = _run(tmp_path, implementation={"memoryCandidates": [payload]})
    assert MemorySanitizer.sanitize_completed_run(gate=CompletedRunGate.from_run(root, RUN_ID), allowed_kinds=("verification_pattern",), now=NOW) == ()


def test_over_age_candidate_is_dropped(tmp_path: Path) -> None:
    payload = _candidate_payload()
    payload["freshness"] = {"generatedAt": (NOW - timedelta(days=91)).isoformat(), "expiresAt": (NOW + timedelta(days=1)).isoformat(), "policyVersion": "memory-sidecar-policy-v1"}
    root = _run(tmp_path, implementation={"memoryCandidates": [payload]})
    assert MemorySanitizer.sanitize_completed_run(gate=CompletedRunGate.from_run(root, RUN_ID), allowed_kinds=("verification_pattern",), now=NOW) == ()


def test_mismatched_source_identity_is_dropped_without_rewriting(tmp_path: Path) -> None:
    payload = _candidate_payload()
    payload["sourceRefs"][0]["runId"] = "run-other"
    root = _run(tmp_path, implementation={"memoryCandidates": [payload]})
    assert MemorySanitizer.sanitize_completed_run(gate=CompletedRunGate.from_run(root, RUN_ID), allowed_kinds=("verification_pattern",), now=NOW) == ()


def test_mismatched_manifest_identity_blocks_gate(tmp_path: Path) -> None:
    root = _run(tmp_path, manifest_run_id="run-other")
    assert CompletedRunGate.from_run(root, RUN_ID).is_memory_eligible() is False


def test_non_canonical_run_local_source_is_dropped(tmp_path: Path) -> None:
    payload = _candidate_payload(source_path="evidence.json")
    root = _run(tmp_path, implementation={"memoryCandidates": [payload]})
    (root / RUN_ID / "evidence.json").write_text("safe but non-canonical", encoding="utf-8")
    assert MemorySanitizer.sanitize_completed_run(gate=CompletedRunGate.from_run(root, RUN_ID), allowed_kinds=("verification_pattern",), now=NOW) == ()


def test_empty_source_refs_are_dropped(tmp_path: Path) -> None:
    payload = _candidate_payload()
    payload["sourceRefs"] = []
    root = _run(tmp_path, implementation={"memoryCandidates": [payload]})
    assert MemorySanitizer.sanitize_completed_run(gate=CompletedRunGate.from_run(root, RUN_ID), allowed_kinds=("verification_pattern",), now=NOW) == ()


def test_oversized_source_artifact_is_rejected_before_digest_read(tmp_path: Path) -> None:
    payload = _candidate_payload(source_path="evidence.json")
    root = _run(tmp_path, implementation={"memoryCandidates": [payload]})
    (root / RUN_ID / "evidence.json").write_bytes(b"x" * (512 * 1024 + 1))
    assert MemorySanitizer.sanitize_completed_run(gate=CompletedRunGate.from_run(root, RUN_ID), allowed_kinds=("verification_pattern",), now=NOW) == ()


@pytest.mark.parametrize("summary", ["token=alpha beta", "Authorization: Bearer abc.def.ghi"])
def test_redact_summary_drops_complete_secret_bearing_summary(summary: str) -> None:
    assert MemorySanitizer.redact_summary(summary) == ""
