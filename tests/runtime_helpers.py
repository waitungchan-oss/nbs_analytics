"""Test subprocess helpers that work in both checkouts and linked worktrees."""

from __future__ import annotations

import sys
from pathlib import Path


def repository_python(project_root: Path) -> Path:
    """Use a project venv when present, otherwise the interpreter running pytest."""
    local = project_root / ".venv" / "bin" / "python"
    if local.is_file() and local.stat().st_mode & 0o111:
        return local
    return Path(sys.executable)
