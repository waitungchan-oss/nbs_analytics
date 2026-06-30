import sqlite3

from backend.services import restore_drill_service


def _sqlite(path, value):
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def test_restore_drill_validates_isolated_copy_without_changing_live_db(tmp_path):
    live = tmp_path / "nbs.db"
    backup = tmp_path / "nbs.db.backup_20260625_120000"
    report_path = tmp_path / "restore_drill_latest.json"
    _sqlite(live, "live")
    _sqlite(backup, "backup")

    seen_targets = []

    def baseline_check(target):
        seen_targets.append(target)
        conn = sqlite3.connect(target)
        try:
            value = conn.execute("SELECT value FROM marker").fetchone()[0]
        finally:
            conn.close()
        return {"status": "matched" if value == "backup" else "drift", "checks": []}

    report = restore_drill_service.run_restore_drill(
        live_db_path=live,
        backup_path=backup,
        report_path=report_path,
        baseline_check=baseline_check,
    )

    assert report["status"] == "passed"
    assert seen_targets and seen_targets[0] != live
    conn = sqlite3.connect(live)
    try:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "live"
    finally:
        conn.close()
    assert report_path.exists()


def test_restore_drill_fails_closed_when_baseline_drifts(tmp_path):
    live = tmp_path / "nbs.db"
    backup = tmp_path / "nbs.db.backup_20260625_120000"
    _sqlite(live, "live")
    _sqlite(backup, "backup")

    report = restore_drill_service.run_restore_drill(
        live_db_path=live,
        backup_path=backup,
        report_path=tmp_path / "report.json",
        baseline_check=lambda target: {"status": "drift", "checks": [{"key": "combinedRevenue"}]},
    )

    assert report["status"] == "failed"
    assert report["baseline"]["status"] == "drift"

