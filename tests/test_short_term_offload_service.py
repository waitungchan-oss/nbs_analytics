from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.agents.short_term_offload_policy import ShortTermOffloadPolicy
from backend.agents.short_term_offload_service import (
    ShortTermOffloadService,
    drill_down,
    mermaid_projection,
    persist_tool_output,
)
from backend.agents.short_term_offload_store import ShortTermOffloadStore


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def test_persist_returns_reference_and_projection_is_read_only(tmp_path: Path) -> None:
    store = ShortTermOffloadStore(tmp_path, policy=ShortTermOffloadPolicy())
    result = persist_tool_output(store, run_id="run-1", session_id="session-1", ref_id="ref-1",
                                 content="line one\nline two", summary="two lines", now=NOW)
    assert result.reference is not None
    assert drill_down(store, result.reference, now=NOW) == "line one\nline two"
    graph = mermaid_projection((result.reference,))
    assert "tool-output-ref-1" in graph
    assert "--" not in graph


def test_blocked_or_expired_output_never_persists_and_drilldown_is_bounded(tmp_path: Path) -> None:
    store = ShortTermOffloadStore(tmp_path, policy=ShortTermOffloadPolicy(max_drilldown_bytes=4))
    blocked = persist_tool_output(store, run_id="run-1", session_id="session-1", ref_id="ref-1",
                                  content="DEEPSEEK_API_KEY=secret", summary="secret", now=NOW)
    assert blocked.reference is not None and blocked.status == "blocked"
    assert store.read("run-1", "session-1", "ref-1", now=NOW).status == "blocked"
    clean = persist_tool_output(store, run_id="run-1", session_id="session-1", ref_id="ref-2",
                                content="123456", summary="ok", now=NOW, ttl_minutes=1)
    assert clean.reference is not None
    with pytest.raises(ValueError):
        drill_down(store, clean.reference, now=NOW, limit=5)
    with pytest.raises(ValueError):
        drill_down(store, clean.reference, run_id="run-2", now=NOW)
    assert drill_down(store, clean.reference, now=NOW + __import__("datetime").timedelta(minutes=2)) is None


def test_service_contract_returns_structured_statuses_and_validates_source_fingerprint(tmp_path: Path) -> None:
    policy = ShortTermOffloadPolicy()
    store = ShortTermOffloadStore(tmp_path, policy=policy)
    service = ShortTermOffloadService(store, policy=policy)
    import hashlib
    fingerprint = hashlib.sha256("abc".encode()).hexdigest()
    result = service.persist_tool_output(run_id="run-1", session_id="session-1", ref_id="ref-1",
                                         content="abc", summary="abc", source_fingerprint=fingerprint, now=NOW)
    assert result.reference is not None
    assert service.drill_down(run_id="run-1", session_id="session-1", ref_id="ref-1",
                              expected_sha256="0" * 64, now=NOW).status == "fingerprint_mismatch"
    assert service.cleanup(now=NOW).status == "ready"


def test_service_rejects_blocked_status_and_preserves_utf8_boundaries(tmp_path: Path) -> None:
    policy = ShortTermOffloadPolicy()
    store = ShortTermOffloadStore(tmp_path, policy=policy)
    service = ShortTermOffloadService(store, policy=policy)
    import hashlib
    blocked = service.persist_tool_output(run_id="run-1", session_id="session-1", ref_id="blocked",
                                          content="X-API-Key: secret", summary="diagnostic",
                                          source_fingerprint=hashlib.sha256(b"X-API-Key: secret").hexdigest(), now=NOW)
    assert service.drill_down(run_id="run-1", session_id="session-1", ref_id="blocked",
                              expected_sha256=blocked.reference.content_sha256, now=NOW).status == "blocked"
    clean = service.persist_tool_output(run_id="run-1", session_id="session-1", ref_id="utf8",
                                        content="éabc", summary="unicode",
                                        source_fingerprint=hashlib.sha256("éabc".encode()).hexdigest(), now=NOW)
    result = service.drill_down(run_id="run-1", session_id="session-1", ref_id="utf8",
                                expected_sha256=clean.reference.content_sha256, offset=1, limit=1, now=NOW)
    assert result.status == "ready" and result.content in {"a", ""}


def test_runner_on_path_persists_bounded_child_output(tmp_path: Path) -> None:
    from scripts.hermes_live_ab_runner import _persist_child_output
    ref_id = _persist_child_output(tmp_path, run_id="run-1", session_id="session-1", arm="control", stdout="child result")
    assert ref_id == "control-child-output"
