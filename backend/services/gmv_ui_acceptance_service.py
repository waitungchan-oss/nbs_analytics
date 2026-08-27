"""Read-only, bounded validation for GMV Streamlit acceptance evidence."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_ARTIFACTS = frozenset({"total.detail", "paid.detail"})


@dataclass(frozen=True, slots=True)
class UiAcceptanceEvidence:
    route: str
    initial_status: str
    merge_status: str
    active_version_id: str
    manifest_sha256: str
    downloaded_artifacts: Mapping[str, int]
    refreshed_version_id: str
    blocking_error: str | None = None


@dataclass(frozen=True, slots=True)
class UiAcceptanceResult:
    status: str
    failure_reasons: tuple[str, ...] = ()


def _reject_raw_payload(payload: Mapping[str, object]) -> None:
    for key in payload:
        normalized = str(key).replace("_", "").lower()
        if normalized in {"rows", "rawrows", "rawdata", "customer", "customername", "payment", "paymentdetails"}:
            raise ValueError("raw business data is not allowed in UI acceptance evidence")


def _from_mapping(payload: Mapping[str, object]) -> UiAcceptanceEvidence:
    _reject_raw_payload(payload)
    artifacts = payload.get("downloadedArtifacts")
    if not isinstance(artifacts, Mapping):
        artifacts = {}
    return UiAcceptanceEvidence(
        route=str(payload.get("route", "")),
        initial_status=str(payload.get("initialStatus", "")),
        merge_status=str(payload.get("mergeStatus", "")),
        active_version_id=str(payload.get("activeVersionId", "")),
        manifest_sha256=str(payload.get("manifestSha256", "")),
        downloaded_artifacts={str(key): int(value) for key, value in artifacts.items()},
        refreshed_version_id=str(payload.get("refreshedVersionId", "")),
        blocking_error=(str(payload["blockingError"]) if payload.get("blockingError") else None),
    )


def validate_ui_acceptance_evidence(
    evidence: UiAcceptanceEvidence | Mapping[str, object],
) -> UiAcceptanceResult:
    """Validate bounded UI evidence without persisting or exposing its payload."""
    item = _from_mapping(evidence) if isinstance(evidence, Mapping) else evidence
    reasons: list[str] = []
    if not item.route.startswith(("http://", "https://")):
        reasons.append("HTTP_ROUTE_REQUIRED")
    if item.initial_status not in {"CURRENT", "READY"}:
        reasons.append("INITIAL_ACTIVE_NOT_READY")
    if item.merge_status != "READY":
        reasons.append("MERGE_NOT_READY")
    if not item.active_version_id or item.refreshed_version_id != item.active_version_id:
        reasons.append("RESTART_VERSION_MISMATCH")
    if not _DIGEST.fullmatch(item.manifest_sha256):
        reasons.append("MANIFEST_DIGEST_INVALID")
    if not _REQUIRED_ARTIFACTS.issubset(item.downloaded_artifacts):
        reasons.append("REQUIRED_DIMENSION_ARTIFACT_MISSING")
    if any(value < 0 for value in item.downloaded_artifacts.values()):
        reasons.append("ARTIFACT_SIZE_INVALID")
    if item.blocking_error:
        reasons.append("BLOCKING_ERROR")
    return UiAcceptanceResult("FAIL" if reasons else "PASS", tuple(sorted(set(reasons))))
