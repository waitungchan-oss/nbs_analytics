from __future__ import annotations

import pytest

from backend.agents.short_term_offload_sanitizer import sanitize_tool_output


def test_clean_output_is_bounded_and_fingerprinted() -> None:
    result = sanitize_tool_output("line one\nline two", summary="two lines")
    assert result.redaction_status == "clean"
    assert result.content == "line one\nline two"
    assert len(result.source_fingerprint) == 64


@pytest.mark.parametrize("value", [
    "Authorization: Bearer abc123",
    "X-API-Key: secret",
    "Cookie: session=secret",
    "postgresql://user:password@host/db",
    "-----BEGIN PRIVATE KEY-----",
    "DEEPSEEK_API_KEY=secret",
    "/Users/alice/private/report.txt",
    r"C:/Users/alice/private/report.txt",
    "sqlite database rows customer_id=42",
    "internal chain of thought: hidden reasoning",
])
def test_sensitive_or_internal_output_is_blocked_without_persisting_content(value: str) -> None:
    result = sanitize_tool_output(value, summary="diagnostic")
    assert result.redaction_status == "blocked"
    assert result.content == ""
    assert result.summary == "[blocked]"


def test_oversized_or_invalid_summary_fails_closed() -> None:
    with pytest.raises(ValueError):
        sanitize_tool_output("ok", summary="x" * 2049)
    with pytest.raises(ValueError):
        sanitize_tool_output("ok", summary="")
