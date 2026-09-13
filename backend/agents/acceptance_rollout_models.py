"""Bounded contracts for the opt-in acceptance shard rollout."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from backend.agents.evidence_models import canonical_fingerprint


ROLLOUT_PREFLIGHT_SCHEMA = "acceptance-shard-rollout-preflight-v1"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_ISOLATION_EXPECTED = {
    "fixtureRoots": "unique",
    "sqlite": "isolated",
    "cache": "isolated",
    "processes": "profile-bound",
}
_UI_REASONS = {
    "missing_ui_fixture",
    "missing_active_version",
    "missing_cache_manifest",
    "revenue_token_mismatch",
    "rule_version_mismatch",
    "cache_pipeline_mismatch",
    "artifact_integrity_failure",
    "ui_fixture_cache_mismatch",
    "ui_fixture_source_mismatch",
}
_BLOCKERS = {
    "ui_cache_mismatch",
    "runner_capability_unavailable",
    "runner_capability_source_mismatch",
    "isolation_source_mismatch",
    "isolation_fixtureRoots",
    "isolation_sqlite",
    "isolation_cache",
    "isolation_processes",
}


@dataclass(frozen=True)
class RolloutConfig:
    """The only rollout controls available before a separately approved L3."""

    shards_enabled: bool = False
    formal_release_enabled: bool = False
    rollout_level: str = "L0"
    shard_count: int = 4

    @classmethod
    def disabled(cls) -> "RolloutConfig":
        return cls()

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> "RolloutConfig":
        raw = environ.get("ACCEPTANCE_SHARDS_ENABLED", "false")
        if raw not in {"true", "false"}:
            raise ValueError("ACCEPTANCE_SHARDS_ENABLED must be exactly true or false")
        config = cls(
            shards_enabled=raw == "true",
            formal_release_enabled=False,
            rollout_level="L1" if raw == "true" else "L0",
        )
        validate_rollout_config(config)
        return config


def validate_rollout_config(config: RolloutConfig) -> None:
    if not isinstance(config, RolloutConfig):
        raise ValueError("rollout config is invalid")
    if not isinstance(config.shards_enabled, bool) or not isinstance(config.formal_release_enabled, bool):
        raise ValueError("rollout config flags are invalid")
    if config.formal_release_enabled or config.rollout_level == "L3":
        raise ValueError("formal release promotion requires a separately approved L3 artifact")
    if config.rollout_level not in {"L0", "L1", "L2"}:
        raise ValueError("rollout level is invalid")
    if not config.shards_enabled and config.rollout_level != "L0":
        raise ValueError("disabled shards must remain at L0")
    if isinstance(config.shard_count, bool) or not isinstance(config.shard_count, int) or not 1 <= config.shard_count <= 64:
        raise ValueError("shard count must be between 1 and 64")


def _require_sha(value: Any, pattern: re.Pattern[str], name: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{name} is invalid")
    return value


def _safe_source_binding(value: Mapping[str, Any]) -> dict[str, str | None]:
    raw = value.get("sourceBinding")
    if not isinstance(raw, Mapping):
        return {"commitSha": None, "sourceFingerprint": None}
    commit = raw.get("commitSha")
    source = raw.get("sourceFingerprint")
    return {
        "commitSha": commit if isinstance(commit, str) and _SHA40.fullmatch(commit) else None,
        "sourceFingerprint": source if isinstance(source, str) and _SHA64.fullmatch(source) else None,
    }


def _manifest_fingerprint(manifest: Mapping[str, Any], commit_sha: str, source_fingerprint: str) -> str:
    if manifest.get("schemaVersion") != "pytest-test-manifest-v1" or manifest.get("status") != "PASS":
        raise ValueError("manifest is not a passing pytest manifest")
    if manifest.get("commitSha") != commit_sha or manifest.get("sourceFingerprint") != source_fingerprint:
        raise ValueError("manifest identity does not match rollout source")
    nodeids = manifest.get("nodeids")
    if not isinstance(nodeids, list) or any(not isinstance(item, str) or not item for item in nodeids):
        raise ValueError("manifest nodeids are invalid")
    if nodeids != sorted(nodeids) or len(nodeids) != len(set(nodeids)):
        raise ValueError("manifest nodeids are not stable and unique")
    expected = canonical_fingerprint({
        "schemaVersion": "pytest-test-manifest-v1",
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "nodeids": nodeids,
    })
    if manifest.get("manifestFingerprint") != expected:
        raise ValueError("manifest fingerprint mismatch")
    return expected


def _safe_runner_capability(
    value: Mapping[str, Any], commit_sha: str, source_fingerprint: str,
) -> tuple[dict[str, Any], list[str]]:
    binding = _safe_source_binding(value)
    result = {
        "sourceBinding": binding,
        "sandbox": "available" if value.get("sandbox") == "available" else "unavailable",
        "interpreter": "qualified" if value.get("interpreter") == "qualified" else "unqualified",
    }
    blockers = []
    if binding != {"commitSha": commit_sha, "sourceFingerprint": source_fingerprint}:
        blockers.append("runner_capability_source_mismatch")
    if result["sandbox"] != "available" or result["interpreter"] != "qualified":
        blockers.append("runner_capability_unavailable")
    return result, blockers


def _safe_isolation(
    value: Mapping[str, Any], commit_sha: str, source_fingerprint: str,
) -> tuple[dict[str, Any], list[str]]:
    binding = _safe_source_binding(value)
    result = {"sourceBinding": binding}
    result.update({
        key: expected if value.get(key) == expected else "unknown"
        for key, expected in _ISOLATION_EXPECTED.items()
    })
    blockers = []
    if binding != {"commitSha": commit_sha, "sourceFingerprint": source_fingerprint}:
        blockers.append("isolation_source_mismatch")
    blockers.extend(f"isolation_{key}" for key, expected in _ISOLATION_EXPECTED.items() if result[key] != expected)
    return result, blockers


def _safe_ui_cache(
    value: Mapping[str, Any], commit_sha: str, source_fingerprint: str,
) -> tuple[dict[str, Any], list[str]]:
    binding = _safe_source_binding(value)
    status = value.get("status")
    matched = value.get("sourceMatched") is True
    raw_reason = value.get("reason")
    reason = raw_reason if isinstance(raw_reason, str) and raw_reason in _UI_REASONS else "ui_fixture_cache_mismatch"
    source_matched = binding == {"commitSha": commit_sha, "sourceFingerprint": source_fingerprint}
    if not source_matched:
        reason = "ui_fixture_source_mismatch"
    passed = status == "PASS" and matched and source_matched and raw_reason is None
    result = {
        "sourceBinding": binding,
        "status": "PASS" if passed else "BLOCKED",
        "sourceMatched": True if passed else False,
        "reason": None if passed else reason,
    }
    return result, [] if passed else ["ui_cache_mismatch"]


def _validate_source_binding(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"commitSha", "sourceFingerprint"}:
        raise ValueError("source binding is invalid")
    if value["commitSha"] is not None and (
        not isinstance(value["commitSha"], str) or not _SHA40.fullmatch(value["commitSha"])
    ):
        raise ValueError("source binding commit is invalid")
    if value["sourceFingerprint"] is not None and (
        not isinstance(value["sourceFingerprint"], str) or not _SHA64.fullmatch(value["sourceFingerprint"])
    ):
        raise ValueError("source binding fingerprint is invalid")


def build_rollout_preflight(
    *,
    config: RolloutConfig,
    commit_sha: str,
    source_fingerprint: str,
    manifest: Mapping[str, Any],
    runner_capability: Mapping[str, Any],
    isolation: Mapping[str, Any],
    ui_cache: Mapping[str, Any],
) -> dict[str, Any]:
    validate_rollout_config(config)
    commit = _require_sha(commit_sha, _SHA40, "commit")
    source = _require_sha(source_fingerprint, _SHA64, "source fingerprint")
    if not isinstance(manifest, Mapping):
        raise ValueError("manifest is invalid")
    manifest_fingerprint = _manifest_fingerprint(manifest, commit, source)
    if not isinstance(runner_capability, Mapping) or not isinstance(isolation, Mapping) or not isinstance(ui_cache, Mapping):
        raise ValueError("preflight inputs are invalid")
    safe_runner, runner_blockers = _safe_runner_capability(runner_capability, commit, source)
    safe_isolation, isolation_blockers = _safe_isolation(isolation, commit, source)
    safe_ui, ui_blockers = _safe_ui_cache(ui_cache, commit, source)
    blockers = sorted(set(runner_blockers + isolation_blockers + ui_blockers))
    unsigned = {
        "schemaVersion": ROLLOUT_PREFLIGHT_SCHEMA,
        "status": "PASS" if not blockers else "BLOCKED",
        "rolloutCandidate": False,
        "shardsEnabled": config.shards_enabled,
        "formalReleaseEnabled": False,
        "commitSha": commit,
        "sourceFingerprint": source,
        "manifestFingerprint": manifest_fingerprint,
        "runnerCapability": safe_runner,
        "isolation": safe_isolation,
        "uiCache": safe_ui,
        "blockers": blockers,
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def validate_rollout_preflight(payload: Mapping[str, Any]) -> None:
    required = {
        "schemaVersion", "status", "rolloutCandidate", "shardsEnabled", "formalReleaseEnabled",
        "commitSha", "sourceFingerprint", "manifestFingerprint", "runnerCapability", "isolation",
        "uiCache", "blockers", "evidenceFingerprint",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise ValueError("rollout preflight schema is invalid")
    if payload["schemaVersion"] != ROLLOUT_PREFLIGHT_SCHEMA or payload["status"] not in {"PASS", "BLOCKED"}:
        raise ValueError("rollout preflight status is invalid")
    if payload["rolloutCandidate"] is not False or payload["formalReleaseEnabled"] is not False:
        raise ValueError("rollout preflight cannot promote formal release")
    if not isinstance(payload["shardsEnabled"], bool):
        raise ValueError("rollout preflight flag is invalid")
    _require_sha(payload["commitSha"], _SHA40, "commit")
    _require_sha(payload["sourceFingerprint"], _SHA64, "source fingerprint")
    _require_sha(payload["manifestFingerprint"], _SHA64, "manifest fingerprint")
    runner = payload["runnerCapability"]
    if (
        not isinstance(runner, dict)
        or set(runner) != {"sourceBinding", "sandbox", "interpreter"}
        or runner["sandbox"] not in {"available", "unavailable"}
        or runner["interpreter"] not in {"qualified", "unqualified"}
    ):
        raise ValueError("runner capability is invalid")
    expected_binding = {"commitSha": payload["commitSha"], "sourceFingerprint": payload["sourceFingerprint"]}
    for component in (runner,):
        _validate_source_binding(component["sourceBinding"])
    isolation = payload["isolation"]
    if (
        not isinstance(isolation, dict)
        or set(isolation) != {"sourceBinding", *_ISOLATION_EXPECTED}
        or any(isolation[key] not in {expected, "unknown"} for key, expected in _ISOLATION_EXPECTED.items())
    ):
        raise ValueError("isolation contract is invalid")
    _validate_source_binding(isolation["sourceBinding"])
    derived_blockers = []
    if runner["sourceBinding"] != expected_binding:
        derived_blockers.append("runner_capability_source_mismatch")
    if runner["sandbox"] != "available" or runner["interpreter"] != "qualified":
        derived_blockers.append("runner_capability_unavailable")
    if isolation["sourceBinding"] != expected_binding:
        derived_blockers.append("isolation_source_mismatch")
    derived_blockers.extend(
        f"isolation_{key}" for key, expected in _ISOLATION_EXPECTED.items() if isolation[key] != expected
    )
    ui_cache = payload["uiCache"]
    if not isinstance(ui_cache, dict) or set(ui_cache) != {"sourceBinding", "status", "sourceMatched", "reason"}:
        raise ValueError("UI cache contract is invalid")
    _validate_source_binding(ui_cache["sourceBinding"])
    if ui_cache["status"] not in {"PASS", "BLOCKED"} or not isinstance(ui_cache["sourceMatched"], bool):
        raise ValueError("UI cache status is invalid")
    if ui_cache["status"] == "PASS" and (ui_cache["sourceMatched"] is not True or ui_cache["reason"] is not None):
        raise ValueError("UI cache is not source matched")
    if ui_cache["status"] == "BLOCKED" and (
        ui_cache["sourceMatched"] is not False
        or ui_cache["reason"] not in _UI_REASONS
    ):
        raise ValueError("UI cache blocker is invalid")
    if ui_cache["sourceBinding"] != expected_binding:
        derived_blockers.append("ui_cache_mismatch")
    if ui_cache["status"] == "BLOCKED":
        derived_blockers.append("ui_cache_mismatch")
    blockers = payload["blockers"]
    if not isinstance(blockers, list) or any(item not in _BLOCKERS for item in blockers) or blockers != sorted(set(blockers)):
        raise ValueError("rollout blockers are invalid")
    if blockers != sorted(set(derived_blockers)):
        raise ValueError("rollout blockers do not match bounded evidence")
    expected_status = "PASS" if not blockers else "BLOCKED"
    if payload["status"] != expected_status:
        raise ValueError("rollout preflight status does not match blockers")
    fingerprint = payload["evidenceFingerprint"]
    if not isinstance(fingerprint, str) or not _SHA64.fullmatch(fingerprint):
        raise ValueError("rollout preflight fingerprint is invalid")
    if canonical_fingerprint({key: payload[key] for key in payload if key != "evidenceFingerprint"}) != fingerprint:
        raise ValueError("rollout preflight fingerprint mismatch")


__all__ = [
    "ROLLOUT_PREFLIGHT_SCHEMA",
    "RolloutConfig",
    "build_rollout_preflight",
    "validate_rollout_config",
    "validate_rollout_preflight",
]
