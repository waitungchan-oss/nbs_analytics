import json
import multiprocessing as mp
import os

import pytest


def _try_lease(lock_path, queue):
    from backend.services.upload_lock_service import UploadBusyError, acquire_upload_lease

    try:
        with acquire_upload_lease(
            coordination_db_path=lock_path,
            entry_point="worker",
            source_files=["worker.xlsx"],
            timeout_seconds=0.05,
        ):
            queue.put("acquired")
    except UploadBusyError as exc:
        queue.put(("busy", exc.owner))


def _acquire_then_crash(lock_path, queue):
    from backend.services.upload_lock_service import acquire_upload_lease

    lease = acquire_upload_lease(
        coordination_db_path=lock_path,
        entry_point="crash-worker",
        source_files=["crash.xlsx"],
        timeout_seconds=0.05,
    )
    queue.put("acquired")
    queue.close()
    queue.join_thread()
    assert lease.active is True
    os._exit(0)


def _acquire_until_released(lock_path, queue, release_event):
    from backend.services.upload_lock_service import UploadBusyError, acquire_upload_lease

    try:
        with acquire_upload_lease(
            coordination_db_path=lock_path,
            entry_point="handoff-worker",
            source_files=["handoff.xlsx"],
            timeout_seconds=0.05,
        ) as lease:
            queue.put(("acquired", lease.operation.operation_id))
            release_event.wait(5)
    except UploadBusyError as exc:
        queue.put(("busy", exc.owner))


def test_only_one_process_holds_upload_lease(tmp_path):
    from backend.services.upload_lock_service import acquire_upload_lease

    lock_path = tmp_path / "upload_coordination.db"
    queue = mp.Queue()
    with acquire_upload_lease(
        coordination_db_path=lock_path,
        entry_point="parent",
        source_files=["parent.xlsx"],
        timeout_seconds=0.05,
    ):
        child = mp.Process(target=_try_lease, args=(str(lock_path), queue))
        child.start()
        child.join(5)
        assert child.exitcode == 0
        outcome, owner = queue.get(timeout=2)
        assert outcome == "busy"
        assert owner["entry_point"] == "parent"
        assert owner["source_count"] == 1
        assert "source_files" not in owner


def test_process_crash_releases_sqlite_lease(tmp_path):
    from backend.services.upload_lock_service import acquire_upload_lease

    lock_path = tmp_path / "upload_coordination.db"
    queue = mp.Queue()
    child = mp.Process(target=_acquire_then_crash, args=(str(lock_path), queue))
    child.start()
    assert queue.get(timeout=2) == "acquired"
    child.join(5)
    assert child.exitcode == 0

    with acquire_upload_lease(
        coordination_db_path=lock_path,
        entry_point="recovery",
        source_files=["recovery.xlsx"],
        timeout_seconds=0.2,
    ) as lease:
        assert lease.operation.entry_point == "recovery"


def test_normal_release_removes_its_owner_sidecar(tmp_path):
    from backend.services.upload_lock_service import acquire_upload_lease

    lock_path = tmp_path / "upload_coordination.db"
    owner_path = tmp_path / "upload_coordination_owner.json"
    with acquire_upload_lease(
        coordination_db_path=lock_path,
        entry_point="normal-release",
        source_files=["normal.xlsx"],
    ):
        assert owner_path.exists()

    assert not owner_path.exists()


def test_release_keeps_owner_cleanup_ahead_of_handoff_acquire(tmp_path, monkeypatch):
    from backend.services.upload_lock_service import acquire_upload_lease

    lock_path = tmp_path / "upload_coordination.db"
    owner_path = tmp_path / "upload_coordination_owner.json"
    queue = mp.Queue()
    release_event = mp.Event()
    original_unlink = type(owner_path).unlink
    handoff = {}

    def attempt_handoff(path, *args, **kwargs):
        child = mp.Process(
            target=_acquire_until_released,
            args=(str(lock_path), queue, release_event),
        )
        child.start()
        handoff["outcome"] = queue.get(timeout=2)
        handoff["child"] = child
        result = original_unlink(path, *args, **kwargs)
        release_event.set()
        child.join(5)
        return result

    with acquire_upload_lease(
        coordination_db_path=lock_path,
        entry_point="old-owner",
        source_files=["old.xlsx"],
    ) as old_lease:
        monkeypatch.setattr(type(owner_path), "unlink", attempt_handoff)
        old_lease.release()

    assert handoff["outcome"][0] == "busy"
    assert handoff["child"].exitcode == 0
    with acquire_upload_lease(
        coordination_db_path=lock_path,
        entry_point="new-owner",
        source_files=["new.xlsx"],
    ) as new_lease:
        assert owner_path.exists()
        assert new_lease.operation.entry_point == "new-owner"
        assert json.loads(owner_path.read_text(encoding="utf-8"))["operation_id"] == (
            new_lease.operation.operation_id
        )


@pytest.mark.parametrize("cleanup_failure", ("read", "unlink"))
def test_release_suppresses_cleanup_oserror_and_preserves_upload_exception(
    tmp_path,
    monkeypatch,
    cleanup_failure,
):
    import backend.services.upload_lock_service as upload_lock_service

    lock_path = tmp_path / "upload_coordination.db"
    lease = upload_lock_service.acquire_upload_lease(
        coordination_db_path=lock_path,
        entry_point="cleanup-error",
        source_files=["cleanup.xlsx"],
    )

    def raise_oserror(*args, **kwargs):
        raise OSError("cleanup failed")

    if cleanup_failure == "read":
        monkeypatch.setattr(upload_lock_service, "_read_owner", raise_oserror)
    else:
        monkeypatch.setattr(type(lock_path), "unlink", raise_oserror)

    with pytest.raises(RuntimeError, match="upload failed"):
        with lease:
            raise RuntimeError("upload failed")

    assert lease.active is False
    with upload_lock_service.acquire_upload_lease(
        coordination_db_path=lock_path,
        entry_point="recovery",
        source_files=["recovery.xlsx"],
    ) as recovery:
        assert recovery.active is True
