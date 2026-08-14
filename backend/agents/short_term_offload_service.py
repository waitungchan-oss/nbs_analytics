from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from .short_term_offload_models import ShortTermOffloadReference
from .short_term_offload_policy import ShortTermOffloadPolicy
from .short_term_offload_sanitizer import sanitize_tool_output
from .short_term_offload_store import ShortTermOffloadStore


@dataclass(frozen=True)
class OffloadPersistResult:
    status: str
    reference: ShortTermOffloadReference | None
    reason: str | None = None


@dataclass(frozen=True)
class DrillDownResult:
    status: str
    content: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CleanupResult:
    status: str
    removed_ref_ids: tuple[str, ...] = ()
    reason: str | None = None


def persist_tool_output(
    store: ShortTermOffloadStore,
    *,
    run_id: str,
    session_id: str,
    ref_id: str,
    content: str,
    summary: str,
    now: datetime | None = None,
    ttl_minutes: int | None = None,
    source_fingerprint: str | None = None,
) -> OffloadPersistResult:
    now = now or datetime.now(timezone.utc)
    policy = store.policy
    ttl = policy.default_ttl_minutes if ttl_minutes is None else ttl_minutes
    if not isinstance(ttl, int) or ttl < 1 or ttl > policy.max_ttl_hours * 60:
        return OffloadPersistResult("blocked", None, "invalid_ttl")
    try:
        policy.validate_ref_id(run_id)
        policy.validate_ref_id(session_id)
        policy.validate_ref_id(ref_id)
        sanitized = sanitize_tool_output(content, summary=summary, policy=policy, source_fingerprint=source_fingerprint)
    except ValueError as exc:
        return OffloadPersistResult("blocked", None, str(exc))
    from .short_term_offload_models import ShortTermOffloadArtifact
    blocked = sanitized.redaction_status == "blocked"
    stored_content = "" if blocked else sanitized.content
    stored_summary = "[blocked]" if blocked else sanitized.summary
    artifact = ShortTermOffloadArtifact(
        policy.schema_version, ref_id, run_id, session_id, "tool_output", stored_summary,
        stored_content, sha256(stored_content.encode()).hexdigest(), now,
        now + timedelta(minutes=ttl), sanitized.source_fingerprint, sanitized.redaction_status,
        "blocked" if blocked else "ready",
    )
    try:
        store.write(artifact)
    except ValueError as exc:
        return OffloadPersistResult("blocked", None, str(exc))
    return OffloadPersistResult("blocked" if blocked else "ready", ShortTermOffloadReference.from_artifact(artifact), "redaction_blocked" if blocked else None)


def drill_down(
    store: ShortTermOffloadStore,
    reference: ShortTermOffloadReference,
    *,
    run_id: str | None = None,
    session_id: str | None = None,
    now: datetime | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> str | None:
    now = now or datetime.now(timezone.utc)
    if run_id is not None and run_id != reference.run_id or session_id is not None and session_id != reference.session_id:
        raise ValueError("reference identity mismatch")
    limit = store.policy.max_drilldown_bytes if limit is None else limit
    if not isinstance(offset, int) or offset < 0 or not isinstance(limit, int) or limit < 1 or limit > store.policy.max_drilldown_bytes:
        raise ValueError("invalid drilldown bounds")
    artifact = store.read(reference.run_id, reference.session_id, reference.ref_id, now=now)
    if artifact is None or artifact.status != "ready" or artifact.expires_at <= now:
        return None
    if artifact.content_sha256 != reference.content_sha256 or sha256(artifact.content.encode()).hexdigest() != reference.content_sha256:
        raise ValueError("reference fingerprint mismatch")
    raw = artifact.content.encode()
    start = min(offset, len(raw))
    end = min(start + limit, len(raw))
    while start < end and start < len(raw) and (raw[start] & 0xC0) == 0x80:
        start += 1
    while end > start and end < len(raw) and (raw[end] & 0xC0) == 0x80:
        end -= 1
    return raw[start:end].decode("utf-8", errors="strict")


def mermaid_projection(references: tuple[ShortTermOffloadReference, ...]) -> str:
    """Return node-only projection; relationships are never inferred here."""
    lines = ["graph TD"]
    seen: set[str] = set()
    for reference in references:
        node = reference.node_id
        if node in seen:
            continue
        seen.add(node)
        label = reference.summary.replace("\"", "'").replace("\n", " ")
        lines.append(f'  {node}["{label}"]')
    return "\n".join(lines)


class ShortTermOffloadService:
    def __init__(self, store: ShortTermOffloadStore, *, policy: ShortTermOffloadPolicy) -> None:
        if store.policy != policy:
            raise ValueError("store policy mismatch")
        self.store = store
        self.policy = policy

    def persist_tool_output(self, *, run_id: str, session_id: str, ref_id: str, content: str,
                            summary: str, source_fingerprint: str, now: datetime | None = None,
                            ttl_minutes: int | None = None) -> OffloadPersistResult:
        return persist_tool_output(self.store, run_id=run_id, session_id=session_id, ref_id=ref_id,
                                   content=content, summary=summary, source_fingerprint=source_fingerprint,
                                   now=now, ttl_minutes=ttl_minutes)

    def drill_down(self, *, run_id: str, session_id: str, ref_id: str, expected_sha256: str,
                   offset: int = 0, limit: int | None = None, now: datetime | None = None) -> DrillDownResult:
        reference = self.store.read(run_id, session_id, ref_id, now=now)
        if reference is None:
            return DrillDownResult("missing_or_expired")
        if reference.content_sha256 != expected_sha256:
            return DrillDownResult("fingerprint_mismatch")
        artifact = self.store.read(run_id, session_id, ref_id, now=now)
        if artifact is None:
            return DrillDownResult("missing_or_expired")
        if artifact.run_id != run_id or artifact.session_id != session_id or artifact.ref_id != ref_id:
            return DrillDownResult("fingerprint_mismatch")
        if artifact.status != "ready":
            return DrillDownResult("blocked")
        try:
            content = drill_down(self.store, reference, now=now, offset=offset, limit=limit)
        except ValueError as exc:
            return DrillDownResult("blocked", reason=str(exc))
        return DrillDownResult("ready", content)

    def cleanup(self, *, now: datetime) -> CleanupResult:
        try:
            return CleanupResult("ready", self.store.cleanup_expired(now=now))
        except ValueError as exc:
            return CleanupResult("blocked", reason=str(exc))
