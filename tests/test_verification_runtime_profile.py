from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.services.verification_runtime_profile import (
    VERIFICATION_PROFILE_SCHEMA,
    VerificationRuntimeProfile,
    VerificationRuntimeProfileError,
)


def _payload(tmp_path: Path) -> dict:
    unsigned = {
        "schemaVersion": VERIFICATION_PROFILE_SCHEMA,
        "profileId": "profile-20260817-001",
        "projectId": "nbs_analytics",
        "gitHead": "a" * 40,
        "worktreeFingerprint": canonical_fingerprint({"head": "a" * 40, "status": ""}),
        "database": {
            "snapshotRef": "verification/profile-20260817-001/database.sqlite",
            "sourceFingerprint": "b" * 64,
            "snapshotFingerprint": "c" * 64,
            "readOnly": True,
        },
        "baseline": {
            "registryFingerprint": "d" * 64,
            "requiredMay2026Total": "HKD 12,057,968",
        },
        "runtime": {
            "generationRef": "verification/profile-20260817-001/generation.json",
            "cacheInventory": {"fileCount": 3, "totalBytes": 10, "fingerprint": "e" * 64},
        },
        "services": {
            "profileNamespace": "profile-20260817-001",
            "ports": {"api": 18601, "streamlit": 18502, "vue": 15173},
        },
        "createdAt": "2026-08-17T10:00:00+08:00",
    }
    return {**unsigned, "profileFingerprint": canonical_fingerprint(unsigned)}


def test_load_accepts_exact_immutable_profile(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    profile = VerificationRuntimeProfile.from_dict(payload, expected_git_head="a" * 40)

    assert profile.to_dict() == payload
    assert profile.fingerprint() == payload["profileFingerprint"]
    assert profile.database.read_only is True
    with pytest.raises(TypeError):
        profile.services.ports["api"] = 19001  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        profile.project_id = "other"  # type: ignore[misc]


def test_loader_rejects_unknown_keys_and_head_mismatch(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    payload["unknown"] = True
    with pytest.raises(VerificationRuntimeProfileError, match="exact"):
        VerificationRuntimeProfile.from_dict(payload)

    payload = _payload(tmp_path)
    with pytest.raises(VerificationRuntimeProfileError, match="gitHead"):
        VerificationRuntimeProfile.from_dict(payload, expected_git_head="b" * 40)


def test_loader_rejects_tampered_fingerprint_and_unsafe_refs(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    payload["baseline"]["requiredMay2026Total"] = "HKD 0"
    with pytest.raises(VerificationRuntimeProfileError, match="frozen value"):
        VerificationRuntimeProfile.from_dict(payload)

    payload = _payload(tmp_path)
    payload["database"]["snapshotRef"] = "../nbs_marketing_data.db"
    payload["profileFingerprint"] = canonical_fingerprint({key: value for key, value in payload.items() if key != "profileFingerprint"})
    with pytest.raises(VerificationRuntimeProfileError, match="relative"):
        VerificationRuntimeProfile.from_dict(payload)


def test_load_rejects_symlinked_profile_file(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    source = tmp_path / "profile.json"
    source.write_text(__import__("json").dumps(payload), encoding="utf-8")
    link = tmp_path / "profile-link.json"
    link.symlink_to(source)
    with pytest.raises(VerificationRuntimeProfileError, match="symlink"):
        VerificationRuntimeProfile.load(link)


def test_loader_rejects_worktree_fingerprint_mismatch(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    with pytest.raises(VerificationRuntimeProfileError, match="worktreeFingerprint"):
        VerificationRuntimeProfile.from_dict(
            payload,
            expected_git_head="a" * 40,
            expected_worktree_fingerprint="f" * 64,
        )


def test_loader_rejects_refs_from_another_profile(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    payload["database"]["snapshotRef"] = "verification/other-profile/database.sqlite"
    payload["profileFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "profileFingerprint"}
    )
    with pytest.raises(VerificationRuntimeProfileError, match="profileId"):
        VerificationRuntimeProfile.from_dict(payload)
