from __future__ import annotations

import pytest

from backend.agents.agent_eval_manifest import planned_slots
from backend.agents.agent_eval_report import build_report, render_markdown
from tests.test_agent_eval_manifest import _manifest


def test_invalid_manifest_rejected():
    try:
        build_report({}, [], [], [], [])
    except ValueError:
        return
    raise AssertionError("invalid manifest accepted")


def test_null_never_rendered_as_zero():
    report = {"schemaVersion": "agent-eval-report-v1", "status": "synthetic_only", "origin": "synthetic",
              "coverage": {"planned": 72, "observed": 0, "missing": 72, "failed": 0,
                           "successCount": 0, "failureCount": 0, "unknownCount": 72},
              "diagnostics": [{"code": "missing_usage"}]}
    text = render_markdown(report)
    assert "synthetic" in text and "missing_usage" in text


def test_manifest_is_left_joined_to_all_72_slots():
    report = build_report(_manifest(), [], [], [], [{"code": "missing_usage"}])
    assert len(report["slots"]) == len(planned_slots(_manifest()["caseIds"], 3)) == 72
    assert report["coverage"]["planned"] == 72
    assert report["coverage"]["missing"] == 72


def test_failed_terminal_state_cannot_be_counted_as_quality_success():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    ledgers = [{**slot, "terminalState": "timeout", "expectedCallIds": []} for slot in slots]
    quality = [{**slot, "checks": {"rubric": "pass"}} for slot in slots]
    report = build_report(manifest, [], ledgers, quality, [])
    assert report["coverage"]["successCount"] == 0
    assert report["coverage"]["failureCount"] == 72
    assert report["status"] == "partial"


def test_nested_identity_is_used_for_slot_join():
    manifest = _manifest()
    slot = planned_slots(manifest["caseIds"], 3)[0]
    observation = {"identity": {"taskId": slot["taskId"], "repeatIndex": slot["repeatIndex"], "cohort": slot["cohort"]}, "callId": "call-1", "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 2}}
    ledger = {**slot, "terminalState": "completed", "expectedCallIds": ["call-1"]}
    report = build_report(manifest, [observation], [ledger], [], [])
    assert report["slots"][0]["usage"]["taskTotalTokens"] == 3


def test_report_builds_paired_off_on_comparisons():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    ledgers = [{**slot, "terminalState": "completed", "expectedCallIds": []} for slot in slots]
    quality = [{**slot, "checks": {"rubric": "pass"}} for slot in slots]
    report = build_report(manifest, [], ledgers, quality, [])
    assert len(report["comparisons"]) == 36
    assert report["comparisons"][0]["eligible"] is True
    assert report["comparisons"][0]["tokenDelta"] == 0


def test_duplicate_ledger_slot_invalidates_report():
    manifest = _manifest()
    slot = planned_slots(manifest["caseIds"], 3)[0]
    ledger = {**slot, "terminalState": "completed", "expectedCallIds": []}
    report = build_report(manifest, [], [ledger, ledger], [], [])
    assert report["status"] == "invalid"


def test_unplanned_observation_slot_invalidates_report():
    manifest = _manifest()
    observation = {"identity": {"taskId": "not-planned", "repeatIndex": 0, "cohort": "recall_off"},
                   "callId": "call-1", "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 1}}
    report = build_report(manifest, [observation], [], [], [])
    assert report["status"] == "invalid"


def test_canonical_nested_ledger_slots_are_retained():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    ledgers = [{"slot": slot, "terminalState": "completed", "expectedCallIds": []} for slot in slots]
    report = build_report(manifest, [], ledgers, [], [])
    assert report["coverage"]["observed"] == 72


def test_duplicate_slot_has_no_eligible_comparisons():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    ledgers = [{**slot, "terminalState": "completed", "expectedCallIds": []} for slot in slots]
    quality = [{**slot, "checks": {"rubric": "pass"}} for slot in slots]
    report = build_report(manifest, [], ledgers + [ledgers[0]], quality, [])
    assert report["status"] == "invalid"
    assert not any(item["eligible"] for item in report["comparisons"])
    assert all(item["tokenDelta"] is None for item in report["comparisons"])


def test_reused_session_is_invalid_and_never_compared():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    ledgers = [{**slot, "sessionId": "same-session", "terminalState": "completed", "expectedCallIds": []} for slot in slots]
    quality = [{**slot, "checks": {"rubric": "pass"}} for slot in slots]
    report = build_report(manifest, [], ledgers, quality, [])
    assert report["status"] == "invalid"
    assert not any(item["eligible"] for item in report["comparisons"])


def test_duplicate_synthetic_call_invalidates_all_comparisons():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    ledgers = [{"slot": slot, "terminalState": "completed", "expectedCallIds": []} for slot in slots]
    ledgers[0]["expectedCallIds"] = ["duplicate"]
    call = {"identity": slots[0], "callId": "duplicate", "origin": "synthetic",
            "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 1}}
    quality = [{"slot": slot, "checks": {"rubric": "pass"}} for slot in slots]
    report = build_report(manifest, [call, call], ledgers, quality, [])
    assert report["status"] == "invalid"
    assert not any(pair["eligible"] for pair in report["comparisons"])
    assert all(pair["tokenOff"] is None and pair["tokenOn"] is None for pair in report["comparisons"])
    assert all(pair["tokenDelta"] is None for pair in report["comparisons"])


def test_duplicate_observation_slot_invalidates_all_comparisons():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    ledgers = [{"slot": slot, "terminalState": "completed", "expectedCallIds": ["a"] if i == 0 else []}
               for i, slot in enumerate(slots)]
    quality = [{"slot": slot, "checks": {"rubric": "pass"}} for slot in slots]
    observations = [{"identity": slots[0], "callId": call_id, "origin": "real",
                     "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 1}}
                    for call_id in ("a", "a")]
    report = build_report(manifest, observations, ledgers, quality, [])
    assert report["status"] == "invalid"
    assert not any(pair["eligible"] for pair in report["comparisons"])


def test_distinct_expected_calls_in_one_slot_are_valid():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    ledgers = [{"slot": slot, "terminalState": "completed", "expectedCallIds": ["a", "b"] if i == 0 else []}
               for i, slot in enumerate(slots)]
    quality = [{"slot": slot, "checks": {"rubric": "pass"}} for slot in slots]
    observations = [{"schemaVersion": "agent-eval-observation-v1",
                     "identity": {**manifest["identity"], **slots[0], "sessionId": f"session-{call_id}"},
                     "callId": call_id, "origin": "real", "sessionId": f"session-{call_id}",
                     "producerId": "fixture-v1", "sourceSchema": "fixture-v1",
                     "producerFingerprint": "4" * 64,
                     "artifactRef": {"path": f"obs-{call_id}.json", "sha256": "5" * 64},
                     "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 1}}
                    for call_id in ("a", "b")]
    report = build_report(manifest, observations, ledgers, quality, [],
                          observation_artifact_refs=[observation["artifactRef"] for observation in observations])
    assert report["status"] == "available"
    assert report["slots"][0]["usage"]["status"] == "available"


def test_observation_session_reuse_invalidates_comparisons():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    ledgers = [{"slot": slot, "terminalState": "completed", "expectedCallIds": []} for slot in slots]
    quality = [{"slot": slot, "checks": {"rubric": "pass"}} for slot in slots]
    observations = [{"identity": slot, "callId": f"call-{i}", "origin": "real", "sessionId": "reused",
                     "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 1}}
                    for i, slot in enumerate(slots[:2])]
    report = build_report(manifest, observations, ledgers, quality, [])
    assert report["status"] == "invalid"
    assert not any(pair["eligible"] for pair in report["comparisons"])


def test_mixed_observation_origins_invalidates_comparisons():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    observations = [{"identity": slots[0], "callId": "real", "origin": "real",
                     "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 1}},
                    {"identity": slots[1], "callId": "synthetic", "origin": "synthetic",
                     "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 1}}]
    report = build_report(manifest, observations, [], [], [])
    assert report["status"] == "invalid"


def test_unknown_observation_origin_invalidates_comparisons():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    observations = [{"identity": slots[0], "callId": "tampered", "origin": "tampered",
                     "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 1}}]
    report = build_report(manifest, observations, [], [], [])
    assert report["status"] == "invalid"


def test_direct_report_rejects_mismatched_observation_binding():
    manifest = _manifest()
    slot = planned_slots(manifest["caseIds"], 3)[0]
    observation = {"identity": {**slot, "projectId": "other-project"}, "callId": "call-1",
                   "origin": "real", "producerId": "fixture-v1", "sourceSchema": "fixture-v1",
                   "producerFingerprint": "4" * 64, "artifactRef": {"path": "obs.json", "sha256": "5" * 64},
                   "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 1}}
    report = build_report(manifest, [observation], [], [], [], observation_artifact_refs=[observation["artifactRef"]])
    assert report["status"] == "invalid"


def test_report_rejects_canonical_observation_missing_provenance():
    manifest = _manifest()
    slot = planned_slots(manifest["caseIds"], 3)[0]
    observation = {"schemaVersion": "agent-eval-observation-v1", "identity": slot,
                   "callId": "call-1", "origin": "real", "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 1}}
    report = build_report(manifest, [observation], [], [], [])
    assert report["status"] == "invalid"


def test_report_rejects_observation_session_mismatch():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    ledgers = [{"slot": slots[0], "sessionId": "ledger-session", "terminalState": "completed", "expectedCallIds": ["call-1"]}]
    observation = {"identity": slots[0], "callId": "call-1", "origin": "real", "sessionId": "other-session",
                   "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 1}}
    report = build_report(manifest, [observation], ledgers, [], [])
    assert report["status"] == "invalid"


def test_strata_excludes_noncompleted_slots():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    ledgers = [{"slot": slot, "terminalState": "completed", "expectedCallIds": []} for slot in slots]
    ledgers[0]["terminalState"] = "timeout"
    quality = [{"slot": slot, "checks": {"rubric": "pass"}} for slot in slots]
    report = build_report(manifest, [], ledgers, quality, [])
    assert report["strata"][0]["eligible"] == 35


def test_report_rejects_ledger_or_quality_artifact_mismatch():
    manifest = _manifest()
    slot = planned_slots(manifest["caseIds"], 3)[0]
    ledger = {"slot": slot, "terminalState": "completed", "expectedCallIds": [], "artifactRef": {"path": "ledger.json", "sha256": "0" * 64}}
    quality = {"slot": slot, "checks": {"rubric": "pass"}, "artifactRef": {"path": "quality.json", "sha256": "1" * 64}}
    report = build_report(manifest, [], [ledger], [quality], [], ledger_artifact_refs=[{"path": "other.json", "sha256": "2" * 64}], quality_artifact_refs=[quality["artifactRef"]])
    assert report["status"] == "invalid"


def test_report_rejects_boolean_repeat_index():
    manifest = _manifest()
    slot = planned_slots(manifest["caseIds"], 3)[0]
    observation = {"identity": {**slot, "repeatIndex": True}, "callId": "call-1",
                   "origin": "real", "usage": {"measuredInputTokens": 1, "measuredOutputTokens": 1}}
    with pytest.raises(ValueError, match="invalid_slot_record"):
        build_report(manifest, [observation], [], [], [])


def test_failed_or_unknown_quality_prevents_available_status():
    manifest = _manifest()
    slots = planned_slots(manifest["caseIds"], 3)
    ledgers = [{"slot": slot, "terminalState": "completed", "expectedCallIds": []} for slot in slots]
    quality = [{"slot": slot, "checks": {"rubric": "pass"}} for slot in slots]
    quality[0]["checks"] = {"rubric": "unknown"}
    report = build_report(manifest, [], ledgers, quality, [])
    assert report["status"] == "partial"
