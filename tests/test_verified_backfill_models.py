import pytest

from backend.agents.verified_backfill_models import VerifiedBackfillManifest


def payload() -> dict:
    return {
        "sourceCommit": "a" * 40,
        "sourceBranch": "main",
        "dirtyFiles": [],
        "gateHashes": {
            "pytest": "b" * 64,
            "systemAcceptance": "c" * 64,
            "hermes": "d" * 64,
        },
        "reviewHash": "e" * 64,
    }


def test_verified_backfill_manifest_round_trips_and_is_immutable():
    manifest = VerifiedBackfillManifest.from_dict(payload())

    assert manifest.to_dict() == payload()
    with pytest.raises((AttributeError, TypeError)):
        manifest.source_branch = "codex/test"


def test_verified_backfill_manifest_gate_hashes_are_deeply_immutable():
    manifest = VerifiedBackfillManifest.from_dict(payload())

    with pytest.raises(TypeError):
        manifest.gate_hashes["pytest"] = "f" * 64
    with pytest.raises((AttributeError, TypeError)):
        manifest.gate_hashes.clear()
    assert manifest.to_dict() == payload()


def test_verified_backfill_manifest_rejects_non_main_or_dirty_state():
    with pytest.raises(ValueError, match="main"):
        VerifiedBackfillManifest.from_dict({**payload(), "sourceBranch": "codex/test"})
    with pytest.raises(ValueError, match="dirtyFiles"):
        VerifiedBackfillManifest.from_dict({**payload(), "dirtyFiles": [{"path": "x.py"}]})


def test_verified_backfill_manifest_rejects_invalid_commit_and_gate_hashes():
    with pytest.raises(ValueError, match="sourceCommit"):
        VerifiedBackfillManifest.from_dict({**payload(), "sourceCommit": "A" * 40})
    with pytest.raises(ValueError, match="systemAcceptance"):
        invalid = {**payload(), "gateHashes": {**payload()["gateHashes"], "systemAcceptance": "x"}}
        VerifiedBackfillManifest.from_dict(invalid)
