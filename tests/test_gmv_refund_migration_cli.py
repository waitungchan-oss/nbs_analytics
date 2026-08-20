import sqlite3
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "scripts" / "migrate_gmv_refund_schema.py"


def _seed_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE tour_data (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO tour_data VALUES (1)")


def test_migration_cli_dry_run_does_not_write_schema_or_backup(tmp_path):
    db_path = tmp_path / "nbs.db"
    _seed_database(db_path)

    result = subprocess.run(
        [sys.executable, str(CLI), "--db-path", str(db_path), "--dry-run"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "DRY_RUN" in result.stdout
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'gmv_refund_batches'"
        ).fetchone()[0] == 0
    assert not list(tmp_path.glob("nbs.db.backup_*"))


def test_migration_cli_apply_creates_backup_and_validates_schema(tmp_path):
    db_path = tmp_path / "nbs.db"
    _seed_database(db_path)

    result = subprocess.run(
        [sys.executable, str(CLI), "--db-path", str(db_path), "--apply"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "APPLIED" in result.stdout
    assert list(tmp_path.glob("nbs.db.backup_*"))
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'v_gmv_current_scope'"
        ).fetchone()[0] == 1
