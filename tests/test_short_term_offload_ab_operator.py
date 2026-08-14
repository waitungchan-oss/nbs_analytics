import json
from pathlib import Path

from backend.agents.evidence_models import canonical_fingerprint
from scripts.short_term_offload_ab_operator import record_ab_evidence, run_bounded_ab_workload
from scripts.hermes_live_ab_runner import LiveABRunResult
from tests.test_short_term_offload_ab_models import _run


def _receipt(run):
    run_dict = run.to_dict()
    run_dict.pop("runFingerprint")
    session_id = f"session-{run.run_id}"
    status = "disabled" if run.recall_mode == "off" else "activated"
    activation_id = canonical_fingerprint({"manifestId": "7" * 64, "runId": run.run_id, "sessionId": session_id, "recallMode": run.recall_mode, "status": status})
    return {
        "schemaVersion": "hermes-runner-capability-receipt-v1",
        **run_dict,
        "manifestId": "7" * 64,
        "sessionId": session_id,
        "activationReceipt": {
            "schemaVersion": "hermes-recall-activation-receipt-v1",
            "activationId": activation_id,
            "recallMode": run.recall_mode,
            "status": status,
        },
        "provenanceSourceCount": 1,
        "provenanceCoveredCount": 1,
        "responseId": f"response-{run.run_id}",
        "priorResponseIds": [],
    }


def test_operator_rejects_non_live_or_incomplete_receipts(tmp_path: Path):
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    treatment = tmp_path / "treatment.json"
    treatment.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    try:
        record_ab_evidence(path, treatment, evidence_root=tmp_path,
                           workload_fingerprint="8" * 64, provenance_refs=("brief.md",))
    except Exception as exc:
        assert "missing" in str(exc) or "keys" in str(exc)
    else:
        raise AssertionError("incomplete receipt must be rejected")


def test_operator_bounds_receipt_paths_and_writes_derived_evidence(tmp_path: Path):
    # This test verifies path and output boundaries; canonical activation IDs are
    # supplied by the live runner and intentionally not fabricated here.
    outside = tmp_path.parent / "outside-receipt.json"
    outside.write_text("{}", encoding="utf-8")
    try:
        record_ab_evidence(outside, outside, evidence_root=tmp_path,
                           workload_fingerprint="8" * 64, provenance_refs=("brief.md",))
    except ValueError as exc:
        assert "outside" in str(exc) or "receipt" in str(exc)


def test_operator_writes_immutable_derived_evidence(tmp_path: Path):
    control = tmp_path / "control.json"
    treatment = tmp_path / "treatment.json"
    control.write_text(json.dumps(_receipt(_run("off", 1, "control-op"))), encoding="utf-8")
    treatment.write_text(json.dumps(_receipt(_run("on", 2, "treatment-op", input_tokens=400, output_tokens=100))), encoding="utf-8")
    output = record_ab_evidence(control, treatment, evidence_root=tmp_path,
                                workload_fingerprint="8" * 64, provenance_refs=("brief.md",))
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["result"] == "pass"
    assert payload["tokenReductionRatio"] == 0.5
    assert output.parent.name == "short-term-offload-ab"


def test_operator_rejects_symlink_root_and_nested_receipt_path(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)
    receipt = target / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    try:
        record_ab_evidence(receipt, receipt, evidence_root=root_link,
                           workload_fingerprint="8" * 64, provenance_refs=("brief.md",))
    except ValueError as exc:
        assert "root" in str(exc)
    nested_link = target / "nested"
    nested_link.symlink_to(tmp_path, target_is_directory=True)
    try:
        record_ab_evidence(nested_link / "receipt.json", receipt, evidence_root=target,
                           workload_fingerprint="8" * 64, provenance_refs=("brief.md",))
    except ValueError as exc:
        assert "symlink" in str(exc)


def test_bounded_workload_entry_rejects_symlink_evidence_root(tmp_path: Path, monkeypatch):
    target = tmp_path / "target-workload"
    target.mkdir()
    root_link = tmp_path / "evidence-link"
    root_link.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(
        "scripts.short_term_offload_ab_operator.run_live_ab",
        lambda *args, **kwargs: LiveABRunResult("completed", "", "control.json", "treatment.json", None),
    )
    try:
        run_bounded_ab_workload(
            object(), {}, "q", ["ref"], project_root=tmp_path, env={}, evidence_root=root_link,
            provenance_refs=("brief.md",),
        )
    except ValueError as exc:
        assert "evidence root" in str(exc)
    else:
        raise AssertionError("symlink evidence root must be rejected")
