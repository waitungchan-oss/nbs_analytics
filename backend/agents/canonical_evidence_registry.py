from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


CANONICAL_EVIDENCE_SCHEMA = "governance-canonical-evidence-v1"


@dataclass(frozen=True)
class CanonicalEvidenceRegistryEntry:
    artifact_kind: str
    filename: str
    writer: str
    entrypoint: str
    schema_version: str
    writer_versions: frozenset[str]
    status_reasons: Mapping[str, frozenset[str] | None]
    payload_caps: Mapping[str, int]


def _entry(
    artifact_kind: str,
    filename: str,
    writer: str,
    entrypoint: str,
    status_reasons: dict[str, frozenset[str] | None],
    payload_caps: dict[str, int],
) -> CanonicalEvidenceRegistryEntry:
    writer_versions = frozenset({"1.0.0"})
    return CanonicalEvidenceRegistryEntry(
        artifact_kind=artifact_kind,
        filename=filename,
        writer=writer,
        entrypoint=entrypoint,
        schema_version=CANONICAL_EVIDENCE_SCHEMA,
        writer_versions=writer_versions,
        status_reasons=MappingProxyType(status_reasons),
        payload_caps=MappingProxyType(payload_caps),
    )


_ENTRIES = (
    _entry(
        "task_gate", "task-gate.json", "task_gate_writer",
        "backend.agents.canonical_evidence_writer:write_task_gate",
        {
            "passed": None,
            "failed": frozenset({"gate_failed", "missing_evidence", "schema_violation"}),
            "blocked": frozenset({"blocked_dependency", "missing_evidence"}),
        },
        {"taskId": 128, "evidenceKinds": 64, "evidenceKindsList": 16},
    ),
    _entry(
        "terra_diagnosis", "terra-diagnosis.json", "terra_diagnosis_runner",
        "backend.agents.canonical_evidence_writer:write_terra_diagnosis",
        {
            "completed": frozenset({"protected_incident", "diagnosis_failed"}),
            "blocked": frozenset({"blocked_missing_evidence", "runner_error"}),
            "not_required": None,
        },
        {"strings": 128, "referenceBasename": 128},
    ),
    _entry(
        "protected_incident", "protected-incident.json", "protected_incident_recorder",
        "backend.agents.canonical_evidence_writer:write_protected_incident",
        {
            "detected": frozenset({"policy_violation", "data_integrity", "security_boundary", "protected_incident"}),
            "contained": frozenset({"policy_violation", "data_integrity", "security_boundary", "protected_incident"}),
            "closed": frozenset({"policy_violation", "data_integrity", "security_boundary", "protected_incident"}),
            "blocked": frozenset({"blocked_missing_evidence", "security_boundary"}),
        },
        {"strings": 128, "referenceBasename": 128},
    ),
)
_BY_KIND = MappingProxyType({entry.artifact_kind: entry for entry in _ENTRIES})


class CanonicalEvidenceRegistry:
    """Code-owned, fixed registry for canonical evidence writers."""

    def entries(self) -> tuple[CanonicalEvidenceRegistryEntry, ...]:
        return _ENTRIES

    def for_kind(self, artifact_kind: str) -> CanonicalEvidenceRegistryEntry:
        try:
            return _BY_KIND[artifact_kind]
        except KeyError as exc:
            raise ValueError("unknown canonical evidence artifact kind") from exc
