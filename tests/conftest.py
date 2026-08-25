"""Keep data-backed tests reproducible without copying production data into Git."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path


_MIN_CANONICAL_DB_BYTES = 1_000_000


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _source_db(project_root: Path) -> Path | None:
    configured = os.environ.get("NBS_ANALYTICS_SOURCE_DB")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.append(project_root / "nbs_marketing_data.db")
    # An isolated worktree may not contain the ignored production snapshot;
    # the main checkout is a read-only source for a disposable test snapshot.
    candidates.append(project_root.parent.parent / "nbs_marketing_data.db")
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink() and candidate.stat().st_size >= _MIN_CANONICAL_DB_BYTES:
            return candidate
    return None


def _prepare_disposable_db(project_root: Path) -> None:
    if os.environ.get("NBS_ANALYTICS_DB_FILE"):
        return
    current = project_root / "nbs_marketing_data.db"
    if current.is_file() and current.stat().st_size >= _MIN_CANONICAL_DB_BYTES:
        return
    source = _source_db(project_root)
    if source is None:
        return
    target = Path(tempfile.gettempdir()) / f"nbs_analytics_pytest_{source.stat().st_mtime_ns}.db"
    if not target.exists():
        source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
            source_conn.close()
    os.environ["NBS_ANALYTICS_DB_FILE"] = str(target)


def pytest_configure(config) -> None:
    _prepare_disposable_db(_project_root())
