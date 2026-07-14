import json

from backend.services.business_rules_service import load_business_rules_snapshot
from rules import load_business_rules


def _write_rules(path, branch_mapping):
    path.write_text(
        json.dumps(
            {
                "BRANCH_MAPPING": branch_mapping,
                "TARGET_BRANCHES_S3": ["A", "B"],
                "CRUISE_DEPTS": ["Cruise"],
                "SALES_REP_LIST": ["Amy"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_load_rules_snapshot_uses_explicit_path_and_stable_fingerprint(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _write_rules(first, {"02": "B", "01": "A"})
    _write_rules(second, {"01": "A", "02": "B"})

    left = load_business_rules_snapshot(first)
    right = load_business_rules_snapshot(second)

    assert left.fingerprint == right.fingerprint
    assert left.branch_mapping == {"01": "A", "02": "B"}
    assert left.target_branches == ("A", "B")
    assert left.cruise_departments == ("Cruise",)
    assert left.sales_reps == ("Amy",)


def test_rules_snapshot_returns_defensive_facts_copies(tmp_path):
    config = tmp_path / "rules.json"
    _write_rules(config, {"01": "A"})
    snapshot = load_business_rules_snapshot(config)

    first = snapshot.facts_kwargs()
    first["branch_mapping"]["99"] = "Changed"
    first["target_branches_s3"].append("Changed")

    second = snapshot.facts_kwargs()
    assert second["branch_mapping"] == {"01": "A"}
    assert second["target_branches_s3"] == ["A", "B"]


def test_load_business_rules_accepts_explicit_path(tmp_path):
    config = tmp_path / "rules.json"
    _write_rules(config, {"88": "Explicit"})

    loaded = load_business_rules(config)

    assert loaded["BRANCH_MAPPING"] == {"88": "Explicit"}
