from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

import database
from backend.services.monthly_baseline_service import build_governed_stability_gate
from backend.services.receipt_exclusion_models import canonical_json_hash
from backend.services.receipt_exclusion_registry_service import (
    commit_receipt_exclusion_revocation,
    load_active_registry_snapshot,
    load_quarantine_evidence,
)

def verify_receipt_exclusion_confirmation(
    *,
    canonical_proposal: dict,
    private_evidence: dict,
    submitted_fingerprint: str,
    selected_candidate_ids: list[str],
) -> list[dict]:
    if canonical_proposal.get("proposalFingerprint") != submitted_fingerprint:
        raise ValueError("stale receipt exclusion proposal")
    allowed = {
        str(item["candidateId"]): item
        for item in canonical_proposal.get("candidates", [])
    }
    selected: list[dict] = []
    for candidate_id in selected_candidate_ids:
        if candidate_id not in allowed or candidate_id not in private_evidence:
            raise ValueError("unknown receipt exclusion candidate")
        selected.append({**allowed[candidate_id], **private_evidence[candidate_id]})
    if not selected:
        raise ValueError("at least one receipt exclusion candidate is required")
    return selected


def _database_snapshot_identity(path: Path) -> str:
    stat = path.stat() if path.exists() else None
    return canonical_json_hash({
        "path": str(path.resolve()),
        "size": stat.st_size if stat else 0,
        "modifiedNs": stat.st_mtime_ns if stat else 0,
    })


def preview_receipt_exclusion_revocation(
    rule_id: int,
    *,
    operation,
    live_db_path,
    registry_reader=load_active_registry_snapshot,
    evidence_loader=load_quarantine_evidence,
    snapshotter=database.snapshot_sqlite_database,
    upsert_runner=database.upsert_to_db,
    gate_builder=build_governed_stability_gate,
) -> dict:
    live_path = database.resolve_db_path(live_db_path)
    registry = registry_reader(db_path=live_path)
    evidence = evidence_loader(rule_id, db_path=live_path)
    prepared = pd.DataFrame([evidence["preparedPayload"]])
    table_name = str(evidence.get("tableName") or "others_data")
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "revocation-preview.db"
        snapshotter(live_path, temp_path)
        upsert_runner(
            prepared if table_name == "tour_data" else pd.DataFrame(),
            prepared if table_name == "others_data" else pd.DataFrame(),
            db_path=temp_path,
        )
        gate = gate_builder(db_path=temp_path)
    preview = {
        "schemaVersion": "receipt-exclusion-revocation-preview-v1",
        "ruleId": int(rule_id),
        "operationId": operation.operation_id,
        "registryRevision": str(registry.get("revision") or evidence.get("registryRevision") or ""),
        "preparedRowHash": str(evidence["preparedRowHash"]),
        "databaseIdentity": _database_snapshot_identity(live_path),
        "gate": gate,
    }
    return {
        **preview,
        "status": "revocation_ready" if gate.get("status") == "matched" else "revocation_blocked",
        "previewFingerprint": canonical_json_hash(preview),
        "deltaAmount": float(gate.get("deltaAmount") or 0),
    }


def confirm_receipt_exclusion_revocation(
    rule_id: int,
    *,
    operation,
    submitted_preview_fingerprint: str,
    revoked_by: str,
    live_db_path,
    registry_reader=load_active_registry_snapshot,
    evidence_loader=load_quarantine_evidence,
    preview_runner=preview_receipt_exclusion_revocation,
    revocation_committer=commit_receipt_exclusion_revocation,
) -> dict:
    live_path = database.resolve_db_path(live_db_path)
    before = registry_reader(db_path=live_path)
    preview = preview_runner(
        rule_id,
        operation=operation,
        live_db_path=live_path,
        registry_reader=registry_reader,
        evidence_loader=evidence_loader,
    )
    after = registry_reader(db_path=live_path)
    if (
        before.get("revision") != after.get("revision")
        or preview.get("registryRevision") != before.get("revision")
        or preview.get("previewFingerprint") != submitted_preview_fingerprint
        or preview.get("status") != "revocation_ready"
    ):
        raise ValueError("stale revocation preview")
    return revocation_committer(
        rule_id,
        operation_id=operation.operation_id,
        revoked_by=revoked_by,
        preview_fingerprint=submitted_preview_fingerprint,
        db_path=live_path,
    )
