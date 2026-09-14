"""Shared, read-only path identity helpers for acceptance fixtures."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Mapping


def canonical_path(value: str | Path) -> Path:
    """Expand and resolve a path without requiring it to exist."""
    return Path(value).expanduser().resolve()


def temporary_roots(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    system_temp: str | Path | None = None,
) -> tuple[Path, ...]:
    """Return deduplicated canonical temporary roots in stable order."""
    env = environ if environ is not None else os.environ
    platform_value = platform_name or sys.platform
    candidates: list[str | Path | None] = [system_temp or tempfile.gettempdir(), env.get("TMPDIR")]
    if not platform_value.startswith("win"):
        candidates.extend(("/tmp", "/private/tmp"))
    candidates.append(env.get("RUNNER_TEMP"))

    roots: list[Path] = []
    for candidate in candidates:
        if not candidate:
            continue
        root = canonical_path(candidate)
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def is_temporary_path(path: str | Path, roots: Iterable[str | Path] | None = None) -> bool:
    """Return whether path is inside one of the allowed temporary roots."""
    resolved_path = canonical_path(path)
    candidates = tuple(canonical_path(item) for item in roots) if roots is not None else temporary_roots()
    return any(resolved_path == root or root in resolved_path.parents for root in candidates)
