from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.agent_eval_dataset import actor_case, validate_dataset


FIXTURE = Path(__file__).parent / "fixtures" / "agent_eval_cases_v1.json"


def test_actor_never_receives_scoring_material():
    case = {"id": "h04", "query": "找不存在的 SOP", "allowedSourcePaths": [],
            "goldChecks": ["must_abstain"], "goldRelevantIds": [], "split": "holdout"}
    assert actor_case(case) == {"id": "h04", "query": "找不存在的 SOP", "allowedSourcePaths": []}


def test_fixture_has_twelve_cases_and_split_counts():
    dataset = json.loads(FIXTURE.read_text(encoding="utf-8"))
    checked = validate_dataset(dataset)
    assert len(checked["cases"]) == 12
    assert sum(case["split"] == "dev" for case in checked["cases"]) == 4
    assert sum(case["split"] == "holdout" for case in checked["cases"]) == 8


def test_gold_extra_keys_are_not_accepted_as_case_schema():
    dataset = json.loads(FIXTURE.read_text(encoding="utf-8"))
    dataset["cases"][0]["answer"] = "secret"
    with pytest.raises(ValueError, match="invalid_case_fields"):
        validate_dataset(dataset)
