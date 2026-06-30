from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_CAPACITY_WARNING_BYTES = 3 * 1024**3
BACKUP_PATTERN = re.compile(
    r"^(?P<db>.+)\.backup_(?P<stamp>\d{8}_\d{6})(?:_(?P<microseconds>\d{6}))?$"
)


def _backup_timestamp(path: Path) -> datetime | None:
    match = BACKUP_PATTERN.match(path.name)
    if not match:
        return None
    try:
        value = datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S")
    except ValueError:
        return None
    return value.replace(tzinfo=timezone.utc)


def _month_key(value: datetime) -> tuple[int, int]:
    return value.year, value.month


def plan_backup_retention(
    *,
    db_path: Path,
    now: datetime | None = None,
    protected_paths: set[str] | None = None,
    capacity_warning_bytes: int = DEFAULT_CAPACITY_WARNING_BYTES,
) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    protected = {str(Path(path).resolve()) for path in (protected_paths or set())}
    candidates = []
    for path in db_path.parent.glob(f"{db_path.name}.backup_*"):
        timestamp = _backup_timestamp(path)
        if path.is_file() and timestamp is not None:
            candidates.append((path, timestamp))
    candidates.sort(key=lambda item: item[1], reverse=True)

    keep: set[Path] = set()
    for path, timestamp in candidates:
        if str(path.resolve()) in protected:
            keep.add(path)
        if timestamp.date() >= (now - timedelta(days=6)).date():
            keep.add(path)

    week_cutoff = now - timedelta(weeks=4)
    weekly = {}
    for path, timestamp in candidates:
        if timestamp >= week_cutoff:
            weekly.setdefault(timestamp.isocalendar()[:2], path)
    keep.update(weekly.values())

    month_keys = []
    cursor_year, cursor_month = now.year, now.month
    for _ in range(6):
        month_keys.append((cursor_year, cursor_month))
        cursor_month -= 1
        if cursor_month == 0:
            cursor_year -= 1
            cursor_month = 12
    monthly = {}
    for path, timestamp in candidates:
        key = _month_key(timestamp)
        if key in month_keys:
            monthly.setdefault(key, path)
    keep.update(monthly.values())

    delete = [path for path, _ in candidates if path not in keep]
    kept = [path for path, _ in candidates if path in keep]
    retained_bytes = sum(path.stat().st_size for path in kept)
    return {
        "generatedAt": now.isoformat(),
        "capacityWarningBytes": int(capacity_warning_bytes),
        "capacityWarning": retained_bytes > int(capacity_warning_bytes),
        "retainedBytes": retained_bytes,
        "deletableBytes": sum(path.stat().st_size for path in delete),
        "keepPaths": [str(path) for path in kept],
        "deletePaths": [str(path) for path in delete],
        "candidateCount": len(candidates),
    }


def apply_backup_retention(plan: dict) -> dict:
    deleted = []
    skipped = []
    for raw_path in plan.get("deletePaths", []):
        path = Path(raw_path)
        if _backup_timestamp(path) is None or ".quarantine_" in path.name:
            skipped.append(str(path))
            continue
        if path.exists() and path.is_file():
            path.unlink()
            deleted.append(str(path))
    return {"deletedPaths": deleted, "skippedPaths": skipped}

