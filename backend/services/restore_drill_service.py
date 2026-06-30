from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from database import validate_sqlite_database


def _sqlite_copy(source: Path, destination: Path) -> None:
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()


def newest_backup(live_db_path: Path) -> Path | None:
    backups = [path for path in live_db_path.parent.glob(f"{live_db_path.name}.backup_*") if path.is_file()]
    return max(backups, key=lambda path: path.stat().st_mtime) if backups else None


def run_restore_drill(
    *,
    live_db_path: Path,
    report_path: Path,
    baseline_check: Callable[[Path], dict],
    backup_path: Path | None = None,
) -> dict:
    started = time.perf_counter()
    selected = Path(backup_path) if backup_path else newest_backup(live_db_path)
    report = {
        "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "failed",
        "liveDbPath": str(live_db_path),
        "backupPath": str(selected) if selected else None,
        "liveDatabaseModified": False,
        "integrity": None,
        "baseline": None,
        "error": None,
    }
    try:
        if selected is None:
            raise FileNotFoundError("no backup is available for restore drill")
        backup_check = validate_sqlite_database(selected)
        if not backup_check["ok"]:
            raise ValueError(f"backup integrity failed: {backup_check['integrity']}")
        with tempfile.TemporaryDirectory(prefix="nbs_restore_drill_") as temp_dir:
            isolated_target = Path(temp_dir) / live_db_path.name
            _sqlite_copy(selected, isolated_target)
            restored_check = validate_sqlite_database(isolated_target)
            report["integrity"] = restored_check
            if not restored_check["ok"]:
                raise ValueError(f"restored integrity failed: {restored_check['integrity']}")
            baseline = baseline_check(isolated_target)
            report["baseline"] = baseline
            report["status"] = "passed" if baseline.get("status") == "matched" else "failed"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["durationMs"] = round((time.perf_counter() - started) * 1000, 2)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report

