from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from backend.services.gmv_trusted_reference_service import (
    TRUSTED_ARTIFACT_CONTRACT_VERSION,
    TRUSTED_REFERENCE_ARTIFACT_KEYS,
    TRUSTED_REFERENCE_SCHEMA_VERSION,
    TrustedReferenceArtifact,
    TrustedReferenceManifest,
    TrustedReferenceSource,
    build_gmv_content_fingerprint,
    invalidate_trusted_reference,
    load_trusted_reference,
    validate_trusted_reference_manifest,
    write_trusted_reference,
)


SOURCE_VALUES = {
    "revenue_generation_token": "gmv-revenue-state-v1:revenue-a",
    "refund_state_sha256": "a" * 64,
    "rule_version": "不含掛賬核銷與TT退款轉團款",
    "export_schema_version": "gmv-formal-export-v2",
    "pipeline_fingerprint": "pipeline-gmv-fast-v1",
    "serializer_version": "gmv-export-serializer-v1",
}


def _source() -> TrustedReferenceSource:
    return TrustedReferenceSource(**SOURCE_VALUES)


def _manifest(
    *,
    content_fingerprint: str | None = None,
    seed_generation_path: str = "generations/generation-a",
    seed_manifest_sha256: str = "b" * 64,
) -> TrustedReferenceManifest:
    fingerprint = content_fingerprint or build_gmv_content_fingerprint(**SOURCE_VALUES)
    artifacts = {
        key: TrustedReferenceArtifact(
            kind="json" if key == "summaries" else "csv" if key.endswith("detail") else "xlsx",
            schema_fingerprint=hashlib.sha256(f"schema:{key}".encode()).hexdigest(),
            semantic_fingerprint=hashlib.sha256(f"semantic:{key}".encode()).hexdigest(),
            row_count=1,
            sheet_count=1 if key.endswith("xlsx") else 0,
        )
        for key in TRUSTED_REFERENCE_ARTIFACT_KEYS
    }
    return TrustedReferenceManifest(
        schema_version=TRUSTED_REFERENCE_SCHEMA_VERSION,
        reference_id=f"gmv-trusted-reference-v1:{fingerprint}",
        content_fingerprint=fingerprint,
        status="TRUSTED",
        created_at="2026-08-25T00:00:00+08:00",
        seed_mode="LEGACY_SEED",
        source=_source(),
        artifact_contract_version=TRUSTED_ARTIFACT_CONTRACT_VERSION,
        artifacts=artifacts,
        seed_provenance={
            "cacheKey": "gmv-formal-export-v1:cache-a",
            "generationPath": seed_generation_path,
            "manifestSha256": seed_manifest_sha256,
        },
    )


def _seed_generation(cache_dir: Path, *, relative_path: str = "generations/generation-a") -> str:
    path = cache_dir / relative_path
    path.mkdir(parents=True, exist_ok=True)
    content = b'{"schemaVersion":"gmv-formal-export-cache-v2"}\n'
    (path / "manifest.json").write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _write_reference_in_process(cache_dir: str, seed_path: str, result_path: str) -> None:
    root = Path(cache_dir)
    seed_sha = hashlib.sha256((root / seed_path / "manifest.json").read_bytes()).hexdigest()
    manifest = _manifest(seed_generation_path=seed_path, seed_manifest_sha256=seed_sha)
    result = write_trusted_reference(cache_dir=root, manifest=manifest)
    Path(result_path).write_text(result.seed_provenance["generationPath"], encoding="utf-8")


def test_content_fingerprint_is_deterministic_and_has_no_version_or_runtime_identity() -> None:
    first = build_gmv_content_fingerprint(**SOURCE_VALUES)
    second = build_gmv_content_fingerprint(**dict(reversed(list(SOURCE_VALUES.items()))))

    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)


@pytest.mark.parametrize("field", sorted(SOURCE_VALUES))
def test_content_fingerprint_changes_when_a_source_identity_changes(field: str) -> None:
    changed = dict(SOURCE_VALUES)
    changed[field] = "c" * 64 if field == "refund_state_sha256" else f"{changed[field]}-changed"

    assert build_gmv_content_fingerprint(**SOURCE_VALUES) != build_gmv_content_fingerprint(**changed)


def test_content_fingerprint_rejects_malformed_refund_state_hash() -> None:
    changed = dict(SOURCE_VALUES)
    changed["refund_state_sha256"] = "not-a-sha256"

    with pytest.raises(ValueError, match="refund_state_sha256"):
        build_gmv_content_fingerprint(**changed)


@pytest.mark.parametrize(
    "field",
    [
        "revenue_generation_token",
        "rule_version",
        "export_schema_version",
        "pipeline_fingerprint",
        "serializer_version",
    ],
)
def test_content_fingerprint_rejects_empty_source_identity(field: str) -> None:
    changed = dict(SOURCE_VALUES)
    changed[field] = ""

    with pytest.raises(ValueError, match=field):
        build_gmv_content_fingerprint(**changed)


def test_manifest_rejects_malformed_source_refund_state_hash() -> None:
    source = TrustedReferenceSource(**{**SOURCE_VALUES, "refund_state_sha256": "not-a-sha256"})
    manifest = _manifest()
    tampered = TrustedReferenceManifest(
        schema_version=manifest.schema_version,
        reference_id=manifest.reference_id,
        content_fingerprint=manifest.content_fingerprint,
        status=manifest.status,
        created_at=manifest.created_at,
        seed_mode=manifest.seed_mode,
        source=source,
        artifact_contract_version=manifest.artifact_contract_version,
        artifacts=manifest.artifacts,
        seed_provenance=manifest.seed_provenance,
    )

    with pytest.raises(ValueError, match="refundStateSha256"):
        validate_trusted_reference_manifest(tampered)


@pytest.mark.parametrize(
    "field",
    [
        "revenue_generation_token",
        "rule_version",
        "export_schema_version",
        "pipeline_fingerprint",
        "serializer_version",
    ],
)
def test_manifest_rejects_empty_direct_source_identity(field: str) -> None:
    manifest = _manifest()
    tampered_source = replace(manifest.source, **{field: ""})

    with pytest.raises(ValueError, match="bounded"):
        validate_trusted_reference_manifest(replace(manifest, source=tampered_source))


@pytest.mark.parametrize("field", ["created_at", "seed_mode"])
def test_manifest_rejects_empty_direct_metadata(field: str) -> None:
    with pytest.raises(ValueError, match="bounded"):
        validate_trusted_reference_manifest(replace(_manifest(), **{field: ""}))


def test_manifest_rejects_empty_direct_seed_cache_key() -> None:
    manifest = _manifest()
    tampered_provenance = {**manifest.seed_provenance, "cacheKey": ""}

    with pytest.raises(ValueError, match="bounded"):
        validate_trusted_reference_manifest(replace(manifest, seed_provenance=tampered_provenance))


def test_manifest_round_trip_has_exact_top_level_contract_and_sorted_artifact_keys() -> None:
    payload = _manifest().to_dict()

    assert set(payload) == {
        "schemaVersion",
        "referenceId",
        "contentFingerprint",
        "status",
        "createdAt",
        "seedMode",
        "source",
        "artifactContract",
        "artifacts",
        "seedProvenance",
    }
    assert payload["artifactContract"] == {
        "version": TRUSTED_ARTIFACT_CONTRACT_VERSION,
        "keys": sorted(TRUSTED_REFERENCE_ARTIFACT_KEYS),
    }

    restored = TrustedReferenceManifest.from_dict(payload)
    validate_trusted_reference_manifest(restored)
    assert restored.to_dict() == payload


def test_manifest_rejects_content_fingerprint_that_does_not_match_source_identity() -> None:
    manifest = _manifest(content_fingerprint="0" * 64)

    with pytest.raises(ValueError, match="content fingerprint"):
        validate_trusted_reference_manifest(manifest)


def test_manifest_rejects_unsorted_or_incomplete_artifact_contract() -> None:
    payload = _manifest().to_dict()
    payload["artifactContract"] = {
        "version": TRUSTED_ARTIFACT_CONTRACT_VERSION,
        "keys": list(reversed(sorted(TRUSTED_REFERENCE_ARTIFACT_KEYS)))[:-1],
    }

    with pytest.raises(ValueError, match="artifact contract"):
        validate_trusted_reference_manifest(TrustedReferenceManifest.from_dict(payload))


def test_manifest_rejects_malformed_artifact_fingerprint() -> None:
    payload = _manifest().to_dict()
    first_key = sorted(TRUSTED_REFERENCE_ARTIFACT_KEYS)[0]
    payload["artifacts"][first_key]["semanticFingerprint"] = "not-a-sha256"

    with pytest.raises(ValueError, match="fingerprint"):
        validate_trusted_reference_manifest(TrustedReferenceManifest.from_dict(payload))


def test_manifest_rejects_unknown_top_level_fields_fail_closed() -> None:
    payload = _manifest().to_dict()
    payload["unexpected"] = "must fail closed"

    with pytest.raises(ValueError, match="top-level"):
        TrustedReferenceManifest.from_dict(payload)


def test_write_and_load_reference_uses_atomic_pointer_and_seed_checksum(tmp_path: Path) -> None:
    seed_path = "seed/generation-a"
    seed_sha = _seed_generation(tmp_path, relative_path=seed_path)
    manifest = _manifest(seed_generation_path=seed_path, seed_manifest_sha256=seed_sha)

    written = write_trusted_reference(cache_dir=tmp_path, manifest=manifest)
    loaded = load_trusted_reference(
        cache_dir=tmp_path,
        content_fingerprint=manifest.content_fingerprint,
        expected_source=manifest.source.to_dict(),
    )

    assert written == manifest
    assert loaded == manifest
    pointer = json.loads((tmp_path / "references" / manifest.content_fingerprint / "trusted.json").read_text())
    assert set(pointer) == {
        "schemaVersion", "contentFingerprint", "referenceId",
        "generationPath", "manifestPath", "manifestSha256",
    }
    manifest_path = tmp_path / pointer["manifestPath"]
    assert pointer["manifestSha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def test_reference_publication_writes_pointer_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_path = "seed/generation-a"
    seed_sha = _seed_generation(tmp_path, relative_path=seed_path)
    manifest = _manifest(seed_generation_path=seed_path, seed_manifest_sha256=seed_sha)
    from backend.services import gmv_trusted_reference_service as service

    original_atomic_write = service._atomic_write

    def fail_pointer(path: Path, data: bytes) -> None:
        if path.name == "trusted.json":
            raise OSError("simulated pointer failure")
        original_atomic_write(path, data)

    monkeypatch.setattr(service, "_atomic_write", fail_pointer)
    with pytest.raises(OSError, match="pointer failure"):
        write_trusted_reference(cache_dir=tmp_path, manifest=manifest)

    assert not (tmp_path / "references" / manifest.content_fingerprint / "trusted.json").exists()


def test_source_mismatch_is_read_only_without_returning_reference(tmp_path: Path) -> None:
    seed_path = "seed/generation-a"
    seed_sha = _seed_generation(tmp_path, relative_path=seed_path)
    manifest = _manifest(seed_generation_path=seed_path, seed_manifest_sha256=seed_sha)
    write_trusted_reference(cache_dir=tmp_path, manifest=manifest)
    expected_source = {**manifest.source.to_dict(), "ruleVersion": "changed-rule"}

    assert load_trusted_reference(
        cache_dir=tmp_path,
        content_fingerprint=manifest.content_fingerprint,
        expected_source=expected_source,
    ) is None
    assert (tmp_path / "references" / manifest.content_fingerprint / "trusted.json").exists()
    assert not (tmp_path / "references" / manifest.content_fingerprint / "invalid.json").exists()


def test_malformed_expected_source_is_read_only(tmp_path: Path) -> None:
    seed_path = "seed/generation-a"
    seed_sha = _seed_generation(tmp_path, relative_path=seed_path)
    manifest = _manifest(seed_generation_path=seed_path, seed_manifest_sha256=seed_sha)
    write_trusted_reference(cache_dir=tmp_path, manifest=manifest)

    malformed_source = {**manifest.source.to_dict(), "refundStateSha256": "not-a-sha256"}
    assert load_trusted_reference(
        cache_dir=tmp_path,
        content_fingerprint=manifest.content_fingerprint,
        expected_source=malformed_source,
    ) is None
    assert (tmp_path / "references" / manifest.content_fingerprint / "trusted.json").exists()
    assert not (tmp_path / "references" / manifest.content_fingerprint / "invalid.json").exists()


def test_retention_missing_seed_generation_invalidates_reference(tmp_path: Path) -> None:
    seed_path = "seed/generation-a"
    seed_sha = _seed_generation(tmp_path, relative_path=seed_path)
    manifest = _manifest(seed_generation_path=seed_path, seed_manifest_sha256=seed_sha)
    write_trusted_reference(cache_dir=tmp_path, manifest=manifest)
    (tmp_path / seed_path / "manifest.json").unlink()

    assert load_trusted_reference(
        cache_dir=tmp_path,
        content_fingerprint=manifest.content_fingerprint,
        expected_source=manifest.source.to_dict(),
    ) is None
    assert (tmp_path / "references" / manifest.content_fingerprint / "invalid.json").exists()


def test_pointer_checksum_or_path_tampering_fails_closed(tmp_path: Path) -> None:
    seed_path = "seed/generation-a"
    seed_sha = _seed_generation(tmp_path, relative_path=seed_path)
    manifest = _manifest(seed_generation_path=seed_path, seed_manifest_sha256=seed_sha)
    write_trusted_reference(cache_dir=tmp_path, manifest=manifest)
    pointer_path = tmp_path / "references" / manifest.content_fingerprint / "trusted.json"
    pointer = json.loads(pointer_path.read_text())
    pointer["manifestSha256"] = "c" * 64
    pointer["generationPath"] = "../outside"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    assert load_trusted_reference(
        cache_dir=tmp_path,
        content_fingerprint=manifest.content_fingerprint,
        expected_source=manifest.source.to_dict(),
    ) is None
    assert not pointer_path.exists()


def test_invalid_existing_reference_can_be_reseeded(tmp_path: Path) -> None:
    first_path = "seed/first"
    second_path = "seed/second"
    first_sha = _seed_generation(tmp_path, relative_path=first_path)
    second_sha = _seed_generation(tmp_path, relative_path=second_path)
    first = _manifest(seed_generation_path=first_path, seed_manifest_sha256=first_sha)
    write_trusted_reference(cache_dir=tmp_path, manifest=first)
    pointer_path = tmp_path / "references" / first.content_fingerprint / "trusted.json"
    pointer = json.loads(pointer_path.read_text())
    pointer["manifestSha256"] = "c" * 64
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    second = replace(first, seed_provenance={
        **first.seed_provenance,
        "generationPath": second_path,
        "manifestSha256": second_sha,
    })

    assert write_trusted_reference(cache_dir=tmp_path, manifest=second) == second
    assert load_trusted_reference(
        cache_dir=tmp_path,
        content_fingerprint=second.content_fingerprint,
        expected_source=second.source.to_dict(),
    ) == second


def test_same_fingerprint_first_valid_reference_wins(tmp_path: Path) -> None:
    first_path = "seed/first"
    second_path = "seed/second"
    first_sha = _seed_generation(tmp_path, relative_path=first_path)
    second_sha = _seed_generation(tmp_path, relative_path=second_path)
    first = _manifest(seed_generation_path=first_path, seed_manifest_sha256=first_sha)
    second = replace(first, seed_provenance={
        **first.seed_provenance,
        "generationPath": second_path,
        "manifestSha256": second_sha,
    })

    assert write_trusted_reference(cache_dir=tmp_path, manifest=first) == first
    assert write_trusted_reference(cache_dir=tmp_path, manifest=second) == first
    loaded = load_trusted_reference(
        cache_dir=tmp_path,
        content_fingerprint=first.content_fingerprint,
        expected_source=first.source.to_dict(),
    )
    assert loaded == first


def test_concurrent_same_fingerprint_seed_keeps_one_valid_pointer(tmp_path: Path) -> None:
    seed_paths = [f"seed/concurrent-{index}" for index in range(4)]
    manifests = []
    for seed_path in seed_paths:
        seed_sha = _seed_generation(tmp_path, relative_path=seed_path)
        manifests.append(_manifest(seed_generation_path=seed_path, seed_manifest_sha256=seed_sha))

    with ThreadPoolExecutor(max_workers=len(manifests)) as pool:
        results = list(pool.map(
            lambda item: write_trusted_reference(cache_dir=tmp_path, manifest=item),
            manifests,
        ))

    assert len({result.seed_provenance["generationPath"] for result in results}) == 1
    loaded = load_trusted_reference(
        cache_dir=tmp_path,
        content_fingerprint=manifests[0].content_fingerprint,
        expected_source=manifests[0].source.to_dict(),
    )
    assert loaded is not None
    assert loaded.seed_provenance["generationPath"] in seed_paths


def test_independent_process_same_fingerprint_seed_keeps_one_valid_pointer(tmp_path: Path) -> None:
    seed_paths = [f"seed/process-{index}" for index in range(3)]
    result_paths = []
    for index, seed_path in enumerate(seed_paths):
        _seed_generation(tmp_path, relative_path=seed_path)
        result_paths.append(tmp_path / f"process-result-{index}.txt")

    context = mp.get_context("spawn")
    processes = [
        context.Process(
            target=_write_reference_in_process,
            args=(str(tmp_path), seed_path, str(result_path)),
        )
        for seed_path, result_path in zip(seed_paths, result_paths)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)

    assert all(process.exitcode == 0 for process in processes)
    assert all(path.is_file() for path in result_paths)
    loaded = load_trusted_reference(
        cache_dir=tmp_path,
        content_fingerprint=_manifest().content_fingerprint,
        expected_source=_source().to_dict(),
    )
    assert loaded is not None
    assert loaded.seed_provenance["generationPath"] in seed_paths


def test_invalidate_reference_records_bounded_reason(tmp_path: Path) -> None:
    seed_path = "seed/generation-a"
    seed_sha = _seed_generation(tmp_path, relative_path=seed_path)
    manifest = _manifest(seed_generation_path=seed_path, seed_manifest_sha256=seed_sha)
    write_trusted_reference(cache_dir=tmp_path, manifest=manifest)

    invalidate_trusted_reference(
        cache_dir=tmp_path,
        content_fingerprint=manifest.content_fingerprint,
        reason="manual test invalidation",
    )
    invalid = json.loads((tmp_path / "references" / manifest.content_fingerprint / "invalid.json").read_text())
    assert invalid["contentFingerprint"] == manifest.content_fingerprint
    assert invalid["reason"] == "manual test invalidation"
