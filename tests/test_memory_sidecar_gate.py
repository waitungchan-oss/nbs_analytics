from __future__ import annotations

import json
from pathlib import Path

from backend.agents.memory_sidecar_gate import CompletedRunGate

RUN_ID = "run-task3"
COMMIT = "a" * 40


def _write(path: Path, payload: object) -> None:
    if isinstance(payload, dict) and path.name != "manifest.json":
        payload = {**payload, "runId": RUN_ID, "gitHead": COMMIT}
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _run(tmp_path: Path, *, status="completed", review="pass", verification=True, hermes="pass", documentation=None, include_documentation=True, implementation=None, manifest_run_id=RUN_ID) -> Path:
    root = tmp_path / "runtime" / "runs"; run = root / RUN_ID; run.mkdir(parents=True)
    _write(run / "manifest.json", {"runId": manifest_run_id, "gitHead": COMMIT}); _write(run / "status.json", {"status": status}); _write(run / "review.json", {"verdict": review})
    _write(run / "full-verification.json", {"fullPytest": {"exitCode": 0 if verification else 1}, "acceptance": {"status": "passed" if verification else "failed"}}); _write(run / "hermes.json", {"overallStatus": hermes})
    if include_documentation: _write(run / "documentation-evidence.json", documentation if documentation is not None else {"status": "no_doc"})
    _write(run / "implementation.json", implementation or {"memoryCandidates": []})
    return root


def test_gate_requires_completed_passes_and_explicit_no_doc(tmp_path: Path) -> None:
    assert not CompletedRunGate.from_run(_run(tmp_path / "review", review="changes_required"), RUN_ID).is_memory_eligible()
    assert not CompletedRunGate.from_run(_run(tmp_path / "status", status="awaiting_authorization"), RUN_ID).is_memory_eligible()
    assert not CompletedRunGate.from_run(_run(tmp_path / "verify", verification=False), RUN_ID).is_memory_eligible()
    assert not CompletedRunGate.from_run(_run(tmp_path / "hermes", hermes="warning"), RUN_ID).is_memory_eligible()
    assert CompletedRunGate.from_run(_run(tmp_path / "ready"), RUN_ID).documentation_status == "no_doc"


def test_missing_or_generic_documentation_outcome_blocks(tmp_path: Path) -> None:
    assert not CompletedRunGate.from_run(_run(tmp_path / "missing", include_documentation=False), RUN_ID).is_memory_eligible()
    for value in ("not_requested", "skipped", "blocked", "invalid"):
        assert not CompletedRunGate.from_run(_run(tmp_path / value, documentation={"status": value}), RUN_ID).is_memory_eligible()


def test_stale_protected_blocked_and_mismatched_inputs_block(tmp_path: Path) -> None:
    stale = CompletedRunGate.from_run(_run(tmp_path / "stale", implementation={"status": "stale_target", "memoryCandidates": []}), RUN_ID)
    assert stale.stale_upstream and not stale.is_memory_eligible()
    blocked = CompletedRunGate.from_run(_run(tmp_path / "blocked", implementation={"status": "blocked", "memoryCandidates": []}), RUN_ID)
    assert blocked.blocked_upstream and not blocked.is_memory_eligible()
    protected_root = _run(tmp_path / "protected"); (protected_root / RUN_ID / "protected-incident.json").write_text("{}", encoding="utf-8")
    protected = CompletedRunGate.from_run(protected_root, RUN_ID)
    assert protected.protected_incident and not protected.is_memory_eligible()
    assert not CompletedRunGate.from_run(_run(tmp_path / "identity", manifest_run_id="run-other"), RUN_ID).is_memory_eligible()
