from dataclasses import replace


def _artifacts():
    return {
        "ex": b"reference-all",
        "ex_no_writeoff": b"reference-no-writeoff",
        "ex_no_writeoff_refund_transfer": b"reference-official",
    }


def test_snapshot_round_trip_is_identity_bound_and_bounded(tmp_path):
    from backend.services.export_reference_cache_service import (
        TrustedReferenceIdentity,
        load_trusted_reference,
        materialize_trusted_reference,
        publish_trusted_reference,
    )

    identity = TrustedReferenceIdentity("source", "generation", "rules", "schema", "pipeline")
    snapshot = materialize_trusted_reference(tmp_path, identity, _artifacts())
    publish_trusted_reference(tmp_path, snapshot)

    assert load_trusted_reference(tmp_path, identity) == snapshot
    assert load_trusted_reference(
        tmp_path, replace(identity, rules_fingerprint="other")
    ) is None
    payload = (tmp_path / "trusted_reference" / "active.json").read_text(encoding="utf-8")
    assert "reference-all" not in payload
    assert "reference-no-writeoff" not in payload


def test_corrupt_or_escaped_pointer_fails_closed_and_preserves_previous_snapshot(tmp_path):
    from backend.services.export_reference_cache_service import (
        TrustedReferenceIdentity,
        load_trusted_reference,
        materialize_trusted_reference,
        publish_trusted_reference,
    )

    identity = TrustedReferenceIdentity("source", "generation", "rules", "schema", "pipeline")
    snapshot = materialize_trusted_reference(tmp_path, identity, _artifacts())
    publish_trusted_reference(tmp_path, snapshot)
    pointer = tmp_path / "trusted_reference" / "active.json"

    pointer.write_text('{"schema":"trusted-reference-v1","snapshot":"../escape.json"}', encoding="utf-8")
    assert load_trusted_reference(tmp_path, identity) is None

    publish_trusted_reference(tmp_path, snapshot)
    pointer.write_text('{"schema":"wrong","snapshot":"snapshots/x.json"}', encoding="utf-8")
    assert load_trusted_reference(tmp_path, identity) is None
