from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.services import backup_retention_service


def test_default_backup_capacity_warning_is_three_gibibytes():
    assert backup_retention_service.DEFAULT_CAPACITY_WARNING_BYTES == 3 * 1024**3


def _backup(root, moment, size=10):
    path = root / f"nbs_marketing_data.db.backup_{moment.strftime('%Y%m%d_%H%M%S')}"
    path.write_bytes(b"x" * size)
    return path


def test_retention_keeps_daily_weekly_monthly_and_protected_backups(tmp_path):
    now = datetime(2026, 6, 25, 12, tzinfo=timezone.utc)
    recent = [_backup(tmp_path, now - timedelta(days=days)) for days in range(8)]
    weekly = [_backup(tmp_path, now - timedelta(days=7 * week + 1)) for week in range(1, 6)]
    monthly = [_backup(tmp_path, datetime(2026, month, 10, tzinfo=timezone.utc)) for month in range(1, 6)]
    protected = weekly[-1]
    unknown = tmp_path / "nbs_marketing_data.db.backup_manual"
    unknown.write_bytes(b"do not touch")
    quarantine = tmp_path / "nbs_marketing_data.db.quarantine_20260625_120000"
    quarantine.write_bytes(b"do not touch")

    plan = backup_retention_service.plan_backup_retention(
        db_path=tmp_path / "nbs_marketing_data.db",
        now=now,
        protected_paths={str(protected)},
        capacity_warning_bytes=100,
    )

    kept = set(plan["keepPaths"])
    delete = set(plan["deletePaths"])
    assert str(recent[0]) in kept
    assert str(protected) in kept
    kept_week_keys = {
        datetime.strptime(Path(path).name.rsplit("backup_", 1)[1], "%Y%m%d_%H%M%S").isocalendar()[:2]
        for path in kept
    }
    assert datetime(2026, 6, 17).isocalendar()[:2] in kept_week_keys
    assert str(monthly[0]) in kept
    assert str(unknown) not in delete
    assert str(quarantine) not in delete
    assert plan["capacityWarning"] is True


def test_apply_retention_deletes_only_planned_eligible_files(tmp_path):
    now = datetime(2026, 6, 25, 12, tzinfo=timezone.utc)
    old_a = _backup(tmp_path, datetime(2024, 1, 1, tzinfo=timezone.utc))
    old_b = _backup(tmp_path, datetime(2024, 1, 2, tzinfo=timezone.utc))
    plan = {
        "deletePaths": [str(old_a)],
        "keepPaths": [str(old_b)],
    }

    result = backup_retention_service.apply_backup_retention(plan)

    assert result["deletedPaths"] == [str(old_a)]
    assert not old_a.exists()
    assert old_b.exists()
