from __future__ import annotations

import json
from typing import Any
from pathlib import Path

from .documentation_models import (
    DOCUMENTATION_TARGETS_V2,
    DOCUMENTATION_TARGET_POLICY_V2_SCHEMA,
    DocumentationSchemaError,
    DocumentationTargetV2,
)


_PROTECTED = frozenset({"baseline", "revenue_scope", "permission", "security", "retention", "state_machine"})
_DOC_SUFFIXES = frozenset({".md", ".rst", ".txt", ".adoc"})
_FORMAT_SUFFIXES = frozenset({".json", ".yaml", ".yml", ".toml"})


class DocumentationImpactClassifier:
    def classify(self, changed_paths: tuple[str, ...], evidence: dict) -> dict:
        paths = tuple(sorted(set(changed_paths)))
        surfaces = tuple(sorted({item for item in evidence.get("riskSurfaces", []) if item in _PROTECTED}))
        if surfaces:
            targets = ("brief_backfill", "system_map", "adr")
            runner = True
        elif paths and all(self._is_skippable(path) for path in paths):
            targets = ()
            runner = False
        else:
            targets = ("brief_backfill", "system_map")
            runner = bool(paths)
            if any(self._requires_adr(path) for path in paths):
                targets = ("brief_backfill", "system_map", "adr")
        return {
            "runnerRequired": runner,
            "requiredTargets": list(targets),
            "riskSurfaces": list(surfaces),
        }

    @staticmethod
    def _is_skippable(path: str) -> bool:
        normalized = path.replace("\\", "/").lower()
        if normalized.startswith("tests/") or "/tests/" in normalized:
            return True
        suffix = "." + normalized.rsplit(".", 1)[-1] if "." in normalized.rsplit("/", 1)[-1] else ""
        return normalized.startswith("docs/") or suffix in _DOC_SUFFIXES | _FORMAT_SUFFIXES

    @staticmethod
    def _requires_adr(path: str) -> bool:
        normalized = path.replace("\\", "/").lower()
        parts = tuple(part for part in normalized.split("/") if part)
        return (
            normalized == "database.py"
            or parts[0:1] == ("database",)
            or "database" in parts
            or "migration" in parts
            or "migrations" in parts
        )


def _parse_documentation_target_catalog(payload: Any) -> dict[str, DocumentationTargetV2]:
    if not isinstance(payload, dict) or set(payload) != {"schemaVersion", "targets"}:
        raise DocumentationSchemaError("target catalog keys are invalid")
    if payload["schemaVersion"] != DOCUMENTATION_TARGET_POLICY_V2_SCHEMA:
        raise DocumentationSchemaError("target catalog schemaVersion is invalid")
    entries = payload["targets"]
    if not isinstance(entries, list):
        raise DocumentationSchemaError("target catalog targets must be a list")
    ids = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("targetId"), str):
            raise DocumentationSchemaError("target catalog entry must include a string targetId")
        ids.append(entry["targetId"])
    if len(ids) != len(set(ids)):
        raise DocumentationSchemaError("duplicate targetId in documentation target catalog")
    expected_ids = {entry["targetId"] for entry in DOCUMENTATION_TARGETS_V2}
    if set(ids) != expected_ids or len(ids) != len(expected_ids):
        raise DocumentationSchemaError("target catalog must contain exactly the five fixed target IDs")
    targets = {}
    for entry in entries:
        target = DocumentationTargetV2.from_dict(entry)
        targets[target.target_id] = target
    return targets


def load_documentation_target(target_id: str) -> DocumentationTargetV2:
    """Resolve one immutable v2 target ID from the repository policy catalog."""
    if not isinstance(target_id, str) or not target_id.strip():
        raise DocumentationSchemaError("targetId must be a non-empty string")
    config_path = Path(__file__).resolve().parents[2] / "agent_config" / "documentation_policies.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DocumentationSchemaError("documentation policy catalog cannot be loaded") from exc
    if not isinstance(config, dict) or "targetCatalogV2" not in config:
        raise DocumentationSchemaError("documentation policy is missing targetCatalogV2")
    catalog = _parse_documentation_target_catalog(config["targetCatalogV2"])
    try:
        return catalog[target_id]
    except KeyError as exc:
        raise DocumentationSchemaError("targetId is not in the fixed documentation target catalog") from exc
