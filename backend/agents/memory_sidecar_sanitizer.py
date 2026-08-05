from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .memory_sidecar_models import MEMORY_TTL, MemoryCandidate, MemorySidecarSchemaError, MemorySourceRef
from .memory_sidecar_gate import CompletedRunGate


_MAX_ARTIFACT_BYTES = 512 * 1024
_CANONICAL_ARTIFACTS = frozenset("manifest.json status.json approval.json context.json implementation.json targeted-verification.json review.json full-verification.json hermes.json documentation-evidence.json documentation-proposal.json documentation-preview.json documentation-application.json documentation-telemetry.json verified-backfill.json risk-classification.json design-spec-gate.json plan-gate.json git-integration.json".split())
_SENSITIVE_SUMMARY = re.compile(r"(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+|(?:/Users/|/home/|/tmp/|[A-Za-z]:[\\/])[^\s]+", re.IGNORECASE)
_SECRET_SUMMARY = re.compile(r"(?:api[_-]?key|token|password|secret)\s*[:=]|authorization\s*:\s*bearer\s+\S+|\bbearer\s+\S+", re.IGNORECASE)
class MemorySanitizer:
    @staticmethod
    def _canonical_summary(*, artifact_path: str, payload: Mapping[str, Any]) -> str | None:
        fields = {
            "review.json": ("review verdict", "verdict"),
            "hermes.json": ("hermes status", "overallStatus"),
            "status.json": ("workflow status", "status"),
            "implementation.json": ("implementation status", "status"),
            "documentation-evidence.json": ("documentation status", "status"),
        }
        label_field = fields.get(artifact_path)
        if label_field is None or not isinstance(payload.get(label_field[1]), str):
            return None
        return f"{label_field[0]}: {payload[label_field[1]]}"

    @staticmethod
    def validate_source_ref(*, source_ref: MemorySourceRef, run_root: Path) -> None:
        root = Path(run_root).resolve(strict=True)
        if source_ref.run_id != root.name:
            raise PermissionError("sourceRef run ID does not match run root")
        candidate = root / source_ref.artifact_path
        if candidate.is_symlink() or not candidate.is_file():
            raise PermissionError("sourceRef artifact must be a regular file")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise PermissionError("sourceRef escapes run root") from exc
        if resolved.stat().st_size > _MAX_ARTIFACT_BYTES:
            raise ValueError("sourceRef artifact exceeds cap")
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if digest != source_ref.artifact_sha256:
            raise ValueError("sourceRef artifact fingerprint mismatch")

    @staticmethod
    def redact_summary(summary: str) -> str:
        if not isinstance(summary, str) or not summary:
            return ""
        lowered = summary.lower()
        if any(marker in lowered for marker in ("baseline", "business rule", "revenue scope", "正式口徑", "12,057,968")) or _SECRET_SUMMARY.search(summary):
            return ""
        redacted = _SENSITIVE_SUMMARY.sub("", summary).strip()
        redacted = re.sub(r"\s{2,}", " ", redacted)
        return redacted if redacted else ""

    @classmethod
    def sanitize_completed_run(cls, *, gate: CompletedRunGate, allowed_kinds: Sequence[str], now: datetime | None = None) -> tuple[MemoryCandidate, ...]:
        if not gate.is_memory_eligible() or not gate.snapshot_matches():
            return ()
        implementation = gate.artifact_payload("implementation.json")
        raw_candidates = implementation.get("memoryCandidates") if implementation else None
        if not isinstance(raw_candidates, list):
            return ()
        manifest = gate.artifact_payload("manifest.json") or {}
        commit = manifest.get("gitHead")
        if not isinstance(commit, str):
            return ()
        reference_time = now or datetime.now(timezone.utc)
        if reference_time.tzinfo is None or reference_time.utcoffset() is None:
            return ()
        candidates: list[MemoryCandidate] = []
        for raw in raw_candidates:
            if not isinstance(raw, Mapping) or set(raw) != {"kind", "summary", "sourceRefs", "freshness", "confidence"}:
                continue
            kind = raw.get("kind")
            if not isinstance(kind, str) or kind not in set(allowed_kinds):
                continue
            summary = cls.redact_summary(raw.get("summary"))
            freshness = raw.get("freshness")
            refs = raw.get("sourceRefs")
            if not summary or not isinstance(freshness, Mapping) or not isinstance(refs, list) or not refs:
                continue
            if set(freshness) != {"generatedAt", "expiresAt", "policyVersion"} or not isinstance(raw.get("confidence"), str):
                continue
            parsed_refs: list[MemorySourceRef] = []
            source_summaries: list[str] = []
            valid = True
            for ref in refs:
                if not isinstance(ref, Mapping) or set(ref) not in ({"runId", "artifactPath", "commit"}, {"runId", "artifactPath", "artifactSha256", "commit"}):
                    valid = False
                    break
                path = ref.get("artifactPath")
                if ref.get("runId") != gate.run_id or ref.get("commit") != commit or not isinstance(path, str):
                    valid = False
                    break
                if path not in _CANONICAL_ARTIFACTS:
                    valid = False
                    break
                source_payload = gate.artifact_payload(path)
                digest = dict(gate.artifact_fingerprints).get(path)
                if source_payload is None or digest is None:
                    valid = False
                    break
                if "artifactSha256" in ref and ref.get("artifactSha256") != digest:
                    valid = False
                    break
                source_summary = cls._canonical_summary(artifact_path=path, payload=source_payload)
                if source_summary is None:
                    valid = False
                    break
                try:
                    source_ref = MemorySourceRef(gate.run_id, path, digest, commit)
                    parsed_refs.append(source_ref)
                    source_summaries.append(source_summary)
                except (ValueError, OSError, PermissionError, MemorySidecarSchemaError):
                    valid = False
                    break
            if not valid:
                continue
            if not valid or summary != "; ".join(source_summaries):
                continue
            try:
                generated_at = datetime.fromisoformat(freshness["generatedAt"])
                expires_at = datetime.fromisoformat(freshness["expiresAt"])
                if generated_at.tzinfo is None or expires_at.tzinfo is None or generated_at > reference_time or expires_at <= reference_time or generated_at < reference_time - MEMORY_TTL or expires_at - generated_at > MEMORY_TTL:
                    continue
                candidates.append(MemoryCandidate.from_parts(
                    kind=kind, summary=summary, source_refs=parsed_refs, source_status="completed",
                    generated_at=generated_at.isoformat(), expires_at=expires_at.isoformat(),
                    confidence=raw["confidence"], policy_version=freshness["policyVersion"],
                ))
            except (ValueError, TypeError, KeyError, MemorySidecarSchemaError):
                continue
        if not gate.snapshot_matches():
            return ()
        return tuple(sorted(candidates, key=lambda item: item.memory_id))
