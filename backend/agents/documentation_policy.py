from __future__ import annotations

from typing import Any


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
