import json
from pathlib import Path


def test_manifest_publication_is_versioned_atomic_and_checksum_validated(tmp_path):
    from backend.services.export_manifest_service import (
        EXPORT_MANIFEST_SCHEMA,
        ExportArtifact,
        build_export_package,
        load_ready_export_manifest,
        publish_export_manifest,
    )

    artifacts = {
        "ex": ExportArtifact("ex", "all.xlsx", b"all-workbook"),
        "ex_no_writeoff": ExportArtifact("ex_no_writeoff", "no-writeoff.xlsx", b"no-writeoff"),
        "ex_no_writeoff_refund_transfer": ExportArtifact(
            "ex_no_writeoff_refund_transfer", "official.xlsx", b"official"
        ),
    }
    manifest_path = publish_export_manifest(
        tmp_path,
        generation_token="generation-1",
        rules_fingerprint="rules-1",
        export_schema_version="schema-1",
        artifacts=artifacts,
        equivalence_status="PASS",
    )

    manifest = load_ready_export_manifest(manifest_path)
    package = build_export_package(manifest, tmp_path)

    assert manifest.schema == EXPORT_MANIFEST_SCHEMA
    assert manifest.status == "READY"
    assert manifest_path.name == "manifest.json"
    assert package.exists()
    assert package.read_bytes().startswith(b"PK")
    assert all((tmp_path / artifact.path).exists() for artifact in manifest.artifacts.values())


def test_invalid_manifest_or_checksum_fails_closed(tmp_path):
    from backend.services.export_manifest_service import (
        ExportArtifact,
        load_ready_export_manifest,
        publish_export_manifest,
    )

    manifest_path = publish_export_manifest(
        tmp_path,
        generation_token="generation-1",
        rules_fingerprint="rules-1",
        export_schema_version="schema-1",
        artifacts={"ex": ExportArtifact("ex", "all.xlsx", b"payload")},
        equivalence_status="PASS",
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["artifacts"]["ex"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_ready_export_manifest(manifest_path) is None
