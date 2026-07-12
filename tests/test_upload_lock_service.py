import multiprocessing as mp
import os


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

    with acquire_upload_lease(
        coordination_db_path=lock_path,
        entry_point="recovery",
        source_files=["recovery.xlsx"],
        timeout_seconds=0.2,
    ) as lease:
        assert lease.operation.entry_point == "recovery"
