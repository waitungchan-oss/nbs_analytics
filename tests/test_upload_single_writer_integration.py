import multiprocessing as mp
import sqlite3
import time


def _writer(lock_path, db_path, value, barrier, queue):
    from backend.services.upload_lock_service import UploadBusyError, acquire_upload_lease

    barrier.wait()
    try:
        with acquire_upload_lease(
            coordination_db_path=lock_path,
            entry_point=value,
            source_files=[f"{value}.xlsx"],
            timeout_seconds=0.05,
        ):
            connection = sqlite3.connect(db_path)
            connection.execute("INSERT INTO writes VALUES (?)", (value,))
            connection.commit()
            connection.close()
            time.sleep(0.2)
            queue.put("accepted")
    except UploadBusyError:
        queue.put("busy")


def test_two_processes_produce_exactly_one_formal_write(tmp_path):
    live_db = tmp_path / "live.db"
    lock_db = tmp_path / "upload_coordination.db"
    connection = sqlite3.connect(live_db)
    connection.execute("CREATE TABLE writes (value TEXT)")
    connection.close()
    queue = mp.Queue()
    barrier = mp.Barrier(2)
    processes = [
        mp.Process(target=_writer, args=(str(lock_db), str(live_db), value, barrier, queue))
        for value in ("streamlit", "fastapi")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(5)
        assert process.exitcode == 0

    assert sorted([queue.get(timeout=2), queue.get(timeout=2)]) == ["accepted", "busy"]
    connection = sqlite3.connect(live_db)
    try:
        assert connection.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 1
    finally:
        connection.close()
