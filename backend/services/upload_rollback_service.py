from __future__ import annotations

from collections.abc import Callable


def _core_status(gate: dict) -> str:
    core = gate.get("coreValidation") or {}
    return str(core.get("status") or gate.get("status") or "drift")


def handle_core_drift_rollback(
    gate: dict,
    backup_path: str | None,
    *,
    restore_database: Callable[[str], dict],
    rebuild_cache: Callable[[], None],
    build_gate: Callable[[], dict],
) -> dict:
    result = {
        "status": "accepted",
        "rollbackStatus": "not_required",
        "backupPath": backup_path,
        "quarantinePath": None,
        "postRollbackGate": None,
        "rollbackError": None,
    }
    if _core_status(gate) != "drift":
        return result
    if not backup_path:
        return {
            **result,
            "status": "rollback_failed",
            "rollbackStatus": "backup_missing",
            "rollbackError": "pre-upload backup path is missing",
        }

    try:
        restore_result = restore_database(backup_path)
        result["quarantinePath"] = restore_result.get("quarantine_path")
        rebuild_cache()
        post_gate = build_gate()
        result["postRollbackGate"] = post_gate
        if _core_status(post_gate) != "matched":
            return {
                **result,
                "status": "rollback_failed",
                "rollbackStatus": "verification_failed",
                "rollbackError": "post-rollback core validation drift",
            }
        return {
            **result,
            "status": "rejected_rolled_back",
            "rollbackStatus": "verified",
        }
    except Exception as exc:
        return {
            **result,
            "status": "rollback_failed",
            "rollbackStatus": "restore_failed",
            "rollbackError": f"{type(exc).__name__}: {exc}",
        }
