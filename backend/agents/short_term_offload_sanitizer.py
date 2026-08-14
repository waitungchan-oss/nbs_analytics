from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re

from .short_term_offload_policy import ShortTermOffloadPolicy


_BLOCKED = re.compile(
    r"(?i)(authorization\s*:\s*bearer|(?:api[_ -]?key|secret|token)\s*[:=]|"
    r"cookie\s*:|(?:postgres|postgresql|mysql|mongodb|redis)://[^\s]+|"
    r"begin [^-]*private key|/Users/|/home/|\.env\b|sqlite|\.sqlite\b|"
    r"\b(?:csv|xlsx|xls)\b|customer[_ -]?id|chain of thought|internal reasoning|"
    r"[A-Za-z]:[\\/]+Users[\\/]+)"
)


@dataclass(frozen=True)
class SanitizedToolOutput:
    summary: str
    content: str
    redaction_status: str
    source_fingerprint: str


def sanitize_tool_output(
    content: str,
    *,
    summary: str,
    policy: ShortTermOffloadPolicy | None = None,
    source_fingerprint: str | None = None,
) -> SanitizedToolOutput:
    policy = policy or ShortTermOffloadPolicy()
    if not isinstance(content, str) or not isinstance(summary, str):
        raise ValueError("output must be text")
    if not summary or len(summary.encode()) > policy.max_summary_bytes:
        raise ValueError("summary cap")
    if len(content.encode()) > policy.max_content_bytes:
        raise ValueError("content cap")
    computed_fingerprint = sha256(content.encode()).hexdigest()
    if source_fingerprint is not None and source_fingerprint != computed_fingerprint:
        raise ValueError("source fingerprint mismatch")
    if _BLOCKED.search(content) or _BLOCKED.search(summary):
        return SanitizedToolOutput("[blocked]", "", "blocked", computed_fingerprint)
    return SanitizedToolOutput(summary, content, "clean", computed_fingerprint)
