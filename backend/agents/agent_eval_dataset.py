"""Versioned, de-identified evaluation cases with actor/scorer isolation."""

from __future__ import annotations

import copy
from typing import Any

from .agent_eval_models import canonical_bytes
from .evidence_models import canonical_fingerprint


SCHEMA = "agent-eval-dataset-v1"
_DATASET_FIELDS = {"schemaVersion", "datasetId", "cases", "datasetFingerprint"}
_CASE_FIELDS = {"id", "split", "category", "query", "allowedSourcePaths", "sourceHashes", "goldChecks", "goldRelevantIds", "origin"}
_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:@-")
_PATH_PREFIXES = ("backend/", "scripts/", "docs/agents/")


def _fail(code: str) -> None:
    raise ValueError(code)


def _id(value: Any, code: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 160 or any(c not in _ID_CHARS for c in value):
        _fail(code)


def _hash(value: Any) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        _fail("invalid_source_hash")


def actor_case(case: dict) -> dict:
    if not isinstance(case, dict):
        _fail("invalid_case")
    if set((key for key in case if key in {"id", "query", "allowedSourcePaths"})) != {"id", "query", "allowedSourcePaths"}:
        _fail("invalid_case")
    return {key: copy.deepcopy(case[key]) for key in ("id", "query", "allowedSourcePaths")}


def _validate_case(case: Any) -> dict:
    if not isinstance(case, dict) or set(case) != _CASE_FIELDS:
        _fail("invalid_case_fields")
    _id(case["id"], "invalid_case_id")
    if case["split"] not in {"dev", "holdout"}:
        _fail("invalid_split")
    if not isinstance(case["category"], str) or not case["category"]:
        _fail("invalid_category")
    if not isinstance(case["query"], str) or not case["query"] or len(canonical_bytes({"q": case["query"]})) > 4096:
        _fail("invalid_query")
    paths = case["allowedSourcePaths"]
    hashes = case["sourceHashes"]
    if not isinstance(paths, list) or any(not isinstance(path, str) or not path.startswith(_PATH_PREFIXES) or ".." in path.split("/") for path in paths):
        _fail("invalid_source_paths")
    if not isinstance(hashes, dict) or set(hashes) != set(paths):
        _fail("invalid_source_hashes")
    for digest in hashes.values():
        _hash(digest)
    if not isinstance(case["goldChecks"], list) or any(not isinstance(item, str) or not item for item in case["goldChecks"]):
        _fail("invalid_gold_checks")
    if not isinstance(case["goldRelevantIds"], list) or any(not isinstance(item, str) or not item for item in case["goldRelevantIds"]):
        _fail("invalid_gold_ids")
    if case["origin"] not in {"real_source_snapshot", "synthetic_fixture"}:
        _fail("invalid_origin")
    return copy.deepcopy(case)


def validate_dataset(dataset: dict) -> dict:
    if not isinstance(dataset, dict) or set(dataset) != _DATASET_FIELDS:
        _fail("invalid_dataset_fields")
    if dataset["schemaVersion"] != SCHEMA:
        _fail("invalid_dataset_schema")
    _id(dataset["datasetId"], "invalid_dataset_id")
    cases = dataset["cases"]
    if not isinstance(cases, list) or len(cases) != 12:
        _fail("invalid_case_count")
    validated = [_validate_case(case) for case in cases]
    if len({case["id"] for case in validated}) != 12:
        _fail("invalid_case_count")
    if sum(case["split"] == "dev" for case in validated) != 4 or sum(case["split"] == "holdout" for case in validated) != 8:
        _fail("invalid_split_counts")
    _hash(dataset["datasetFingerprint"])
    unsigned = {key: dataset[key] for key in _DATASET_FIELDS if key != "datasetFingerprint"}
    if dataset["datasetFingerprint"] != canonical_fingerprint(unsigned):
        _fail("dataset_fingerprint_mismatch")
    return {**copy.deepcopy(dataset), "cases": validated}
