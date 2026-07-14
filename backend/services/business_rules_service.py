from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from rules import load_business_rules


@dataclass(frozen=True)
class BusinessRulesSnapshot:
    branch_mapping_items: tuple[tuple[str, str], ...]
    target_branches: tuple[str, ...]
    cruise_departments: tuple[str, ...]
    sales_reps: tuple[str, ...]
    fingerprint: str

    @property
    def branch_mapping(self) -> dict[str, str]:
        return dict(self.branch_mapping_items)

    def facts_kwargs(self) -> dict[str, object]:
        return {
            "branch_mapping": self.branch_mapping,
            "target_branches_s3": list(self.target_branches),
            "cruise_depts": list(self.cruise_departments),
            "sales_rep_list": list(self.sales_reps),
        }


def _string_tuple(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value) for value in values)


def load_business_rules_snapshot(
    config_path: str | Path | None = None,
) -> BusinessRulesSnapshot:
    rules = load_business_rules(config_path)
    raw_mapping = rules.get("BRANCH_MAPPING")
    mapping = raw_mapping if isinstance(raw_mapping, dict) else {}
    branch_mapping_items = tuple(
        sorted((str(key), str(value)) for key, value in mapping.items())
    )
    target_branches = _string_tuple(rules.get("TARGET_BRANCHES_S3"))
    cruise_departments = _string_tuple(rules.get("CRUISE_DEPTS"))
    sales_reps = _string_tuple(rules.get("SALES_REP_LIST"))
    contract = {
        "branchMapping": dict(branch_mapping_items),
        "targetBranches": list(target_branches),
        "cruiseDepartments": list(cruise_departments),
        "salesReps": list(sales_reps),
    }
    encoded = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return BusinessRulesSnapshot(
        branch_mapping_items=branch_mapping_items,
        target_branches=target_branches,
        cruise_departments=cruise_departments,
        sales_reps=sales_reps,
        fingerprint=hashlib.sha256(encoded).hexdigest(),
    )
