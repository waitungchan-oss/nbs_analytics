# Task 1 Report: Make SQLite Targets Explicit

## Status

DONE

## Scope

- Added `resolve_db_path()` and explicit-path `get_db_connection()`.
- Added `snapshot_sqlite_database()` with post-copy SQLite integrity validation.
- Added optional explicit targets to `hot_backup_database()`, `upsert_to_db()`, `load_all_data_from_db()`, and `restore_database_from_backup()`.
- Preserved omitted-argument call sites, existing transaction boundaries, and restore ordering: validate backup, quarantine live DB, replace, validate restored DB.
- Added `tests/test_database_explicit_path.py` from the task brief.
- No upload adapters, baseline registry, formal revenue logic, or unrelated files were modified. `tests/test_database_rollback.py` required no change.

## TDD Evidence

The new test file was run before production changes:

```text
2 failed
TypeError: load_all_data_from_db() got an unexpected keyword argument 'db_path'
AttributeError: module 'database' has no attribute 'snapshot_sqlite_database'
```

After the minimal implementation:

```text
tests/test_database_explicit_path.py tests/test_database_rollback.py
8 passed in 0.75s
```

## Verification

```text
.venv/bin/python -m pytest -q
141 passed in 15.69s
```

Additional checks passed:

- `git diff --check`
- May 2026 frozen baseline remains `12057968` / `HKD 12,057,968` in the existing baseline and acceptance artifacts.
- Worktree scope review found only `database.py` and the new explicit-path test as code changes; no rollback test edit was needed.

## Concerns

None identified for Task 1. Existing callers continue to omit `db_path` and resolve through `DB_FILE`; explicit callers can now bind their target without mutating the default.
