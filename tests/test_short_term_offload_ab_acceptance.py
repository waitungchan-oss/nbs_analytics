import json
from pathlib import Path
import pytest

from scripts.short_term_offload_ab_acceptance import evaluate_three_pairs
from tests.test_short_term_offload_ab_operator import _receipt
from tests.test_short_term_offload_ab_models import _run
from scripts.short_term_offload_ab_operator import record_ab_evidence


def _pair(root: Path, suffix: str) -> Path:
    control, treatment = root / f"c-{suffix}.json", root / f"t-{suffix}.json"
    control.write_text(json.dumps(_receipt(_run("off", 1, f"control-{suffix}"))), encoding="utf-8")
    treatment.write_text(json.dumps(_receipt(_run("on", 2, f"treatment-{suffix}", input_tokens=400, output_tokens=100))), encoding="utf-8")
    return record_ab_evidence(control, treatment, evidence_root=root, workload_fingerprint="8" * 64, provenance_refs=("brief.md",))


def test_acceptance_requires_three_distinct_pairs(tmp_path: Path):
    one = _pair(tmp_path, "one")
    result = evaluate_three_pairs((one, one, one))
    assert result["status"] == "blocked_runner_capability"


def test_acceptance_passes_only_three_distinct_complete_pairs(tmp_path: Path):
    paths = tuple(_pair(tmp_path, suffix) for suffix in ("one", "two", "three"))
    result = evaluate_three_pairs(paths)
    assert result["status"] == "pass"
    assert result["pairCount"] == 3
    assert result["meanTokenReductionRatio"] == 0.5
    assert result["pairs"][0]["controlTotalTokens"] == 1000
    assert result["pairs"][0]["treatmentTotalTokens"] == 500
    assert result["pairs"][0]["controlP95Ms"] == 100


def test_acceptance_rejects_rehashed_tampered_reduction(tmp_path: Path):
    paths = [_pair(tmp_path, suffix) for suffix in ("one", "two", "three")]
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["tokenReductionRatio"] = 0.99
        from backend.agents.evidence_models import canonical_fingerprint
        unsigned = dict(payload)
        unsigned.pop("evidenceFingerprint")
        payload["evidenceFingerprint"] = canonical_fingerprint(unsigned)
        path.write_text(json.dumps(payload), encoding="utf-8")
    assert evaluate_three_pairs(tuple(paths))["status"] == "blocked_runner_capability"


def test_acceptance_rejects_cross_pair_workload_mismatch(tmp_path: Path):
    paths = [_pair(tmp_path, suffix) for suffix in ("one", "two", "three")]
    payload = json.loads(paths[1].read_text(encoding="utf-8"))
    payload["workloadFingerprint"] = "9" * 64
    paths[1].write_text(json.dumps(payload), encoding="utf-8")
    assert evaluate_three_pairs(tuple(paths))["status"] == "blocked_runner_capability"


def test_acceptance_rejects_output_traversal(tmp_path: Path):
    paths = tuple(_pair(tmp_path, suffix) for suffix in ("one", "two", "three"))
    with pytest.raises(ValueError):
        evaluate_three_pairs(paths, output_path=tmp_path / ".." / "escape.json")
