from pathlib import Path
import sys
import pytest

from scripts import system_manager


def _mock_profile_paths(monkeypatch, tmp_path):
    from types import SimpleNamespace

    paths = SimpleNamespace(
        db_path=tmp_path / "profile" / "snapshot.sqlite",
        cache_path=tmp_path / "profile" / "cache",
    )
    monkeypatch.setattr(system_manager, "_verification_profile_paths", lambda *_args: paths)
    return paths


def _managed_profile_record(pid, profile_id, paths):
    environment = {
        "NBS_ANALYTICS_DB_FILE": str(paths.db_path),
        "NBS_ANALYTICS_CACHE_DIR": str(paths.cache_path),
    }
    return {
        "pid": pid,
        "profileId": profile_id,
        "profileEnvironmentFingerprint": system_manager._environment_fingerprint(environment),
        "processStartIdentity": f"start-{pid}",
    }


def _specs(tmp_path: Path) -> dict:
    (tmp_path / "app.py").write_text("", encoding="utf-8")
    (tmp_path / "nbs_marketing_data.db").write_bytes(b"sqlite")
    return system_manager.build_service_specs(tmp_path, "python", "npm")


def test_preflight_reuses_existing_project_streamlit(monkeypatch, tmp_path):
    specs = _specs(tmp_path)
    specs = {"streamlit": specs["streamlit"]}
    command = (
        f"{tmp_path}/.venv/bin/python -m streamlit run app.py "
        "--server.port 8502 --server.address 127.0.0.1"
    )

    monkeypatch.setattr(system_manager, "read_state", lambda runtime_dir: {"services": {}})
    monkeypatch.setattr(system_manager, "_command_available", lambda command: True)
    monkeypatch.setattr(
        system_manager,
        "port_is_open",
        lambda host, port: port == specs["streamlit"]["port"],
    )
    monkeypatch.setattr(system_manager, "process_is_alive", lambda pid: False)
    monkeypatch.setattr(system_manager, "_listening_pids", lambda port: [1234])
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: command)
    monkeypatch.setattr(system_manager, "endpoint_is_ready", lambda url: url == specs["streamlit"]["ready_url"])

    result = system_manager.preflight(tmp_path, specs, tmp_path / ".nbs_runtime")

    assert result == {"ok": True, "issues": []}


def test_preflight_blocks_unrelated_process_on_service_port(monkeypatch, tmp_path):
    specs = _specs(tmp_path)
    specs = {"streamlit": specs["streamlit"]}

    monkeypatch.setattr(system_manager, "read_state", lambda runtime_dir: {"services": {}})
    monkeypatch.setattr(system_manager, "_command_available", lambda command: True)
    monkeypatch.setattr(
        system_manager,
        "port_is_open",
        lambda host, port: port == specs["streamlit"]["port"],
    )
    monkeypatch.setattr(system_manager, "process_is_alive", lambda pid: False)
    monkeypatch.setattr(system_manager, "_listening_pids", lambda port: [5678])
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: "python -m http.server 8502")
    monkeypatch.setattr(system_manager, "endpoint_is_ready", lambda url: False)

    result = system_manager.preflight(tmp_path, specs, tmp_path / ".nbs_runtime")

    assert result["ok"] is False
    assert "streamlit port 8502 is occupied by an unmanaged process" in result["issues"]


def test_windows_profile_preflight_fails_before_service_spawn(monkeypatch, tmp_path):
    specs = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-test",
    )
    monkeypatch.setattr(system_manager.sys, "platform", "win32")
    monkeypatch.setattr(
        system_manager, "read_state",
        lambda runtime_dir: pytest.fail("unsupported profile must fail before state or process access"),
    )

    result = system_manager.preflight(tmp_path, specs, tmp_path / "profile-runtime")

    assert result == {
        "ok": False,
        "issues": ["Verification-profile service ownership is unsupported on Windows"],
    }


@pytest.mark.parametrize("wrong_key", ["NBS_ANALYTICS_DB_FILE", "NBS_ANALYTICS_CACHE_DIR"])
def test_profile_reuse_rejects_database_or_cache_environment_drift(monkeypatch, tmp_path, wrong_key):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    expected_env = {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "profile-a" / "snapshot.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "profile-a" / "cache"),
    }
    spec["env"] = expected_env
    observed_env = dict(expected_env)
    observed_env[wrong_key] = str(tmp_path / "other-profile" / wrong_key)
    launch_record = {
        "pid": 1234,
        "profileId": "profile-a",
        "profileEnvironmentFingerprint": system_manager._environment_fingerprint(expected_env),
        "processStartIdentity": "process-start-1234",
    }
    observed_environment_calls = []
    monkeypatch.setattr(system_manager, "_listening_pids", lambda port: [1234])
    monkeypatch.setattr(
        system_manager,
        "_process_command",
        lambda pid: f"{tmp_path}/.venv/bin/python -m uvicorn backend.main:app --port {spec['port']}",
    )
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)
    monkeypatch.setattr(
        system_manager,
        "_process_environment",
        lambda pid, keys: observed_environment_calls.append(tuple(keys)) or observed_env,
        raising=False,
    )
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "process-start-1234")

    assert system_manager.find_reusable_service_pid(
        "api", spec, tmp_path, launch_record=launch_record
    ) is None
    assert observed_environment_calls == [tuple(expected_env)]


def test_profile_identity_uses_manager_launch_record_when_process_env_is_unavailable(monkeypatch, tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "profile-a" / "snapshot.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "profile-a" / "cache"),
    }
    launch_record = {
        "pid": 1234,
        "profileId": "profile-a",
        "profileEnvironmentFingerprint": system_manager._environment_fingerprint(spec["env"]),
        "processStartIdentity": "process-start-1234",
    }
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: f"python -m uvicorn backend.main:app --port {spec['port']}")
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: None)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "process-start-1234")
    monkeypatch.setattr(system_manager.sys, "platform", "darwin")

    assert system_manager._process_matches_service("api", 1234, spec, tmp_path, launch_record)


def test_profile_identity_rejects_absolute_project_command_with_mismatched_cwd(monkeypatch, tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "profile-a" / "snapshot.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "profile-a" / "cache"),
    }
    launch_record = {
        "pid": 1234,
        "profileId": "profile-a",
        "profileEnvironmentFingerprint": system_manager._environment_fingerprint(spec["env"]),
        "processStartIdentity": "process-start-1234",
    }
    command = f"{tmp_path}/.venv/bin/python -m uvicorn backend.main:app --port {spec['port']}"
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: command)
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path / "another-project")
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: None)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "process-start-1234")

    assert not system_manager._process_matches_service("api", 1234, spec, tmp_path, launch_record)


def test_profile_service_adoption_fails_closed_without_process_start_identity(monkeypatch, tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "profile-a" / "snapshot.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "profile-a" / "cache"),
    }
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: None)

    with pytest.raises(RuntimeError, match="process-start identity unavailable"):
        system_manager._adopt_service_record(1234, spec, "api", tmp_path)


def test_profile_service_adoption_fails_closed_without_database_and_cache_environment(monkeypatch, tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {}
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "start-1234")

    with pytest.raises(RuntimeError, match="profile service environment is incomplete"):
        system_manager._adopt_service_record(1234, spec, "api", tmp_path)


def test_profile_process_match_rejects_missing_database_and_cache_environment(monkeypatch, tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {}
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: f"python -m uvicorn backend.main:app --port {spec['port']}")
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)

    assert not system_manager._process_matches_service("api", 1234, spec, tmp_path, {})


def test_profile_identity_fails_closed_when_environment_unavailable_off_macos(monkeypatch, tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "profile-a" / "snapshot.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "profile-a" / "cache"),
    }
    record = {
        "pid": 1234,
        "profileId": "profile-a",
        "profileEnvironmentFingerprint": system_manager._environment_fingerprint(spec["env"]),
        "processStartIdentity": "start-1234",
    }
    monkeypatch.setattr(system_manager.sys, "platform", "linux")
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: f"python -m uvicorn backend.main:app --port {spec['port']}")
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: None)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "start-1234")

    assert not system_manager._process_matches_service("api", 1234, spec, tmp_path, record)


def test_find_reusable_profile_service_uses_matching_managed_launch_record(monkeypatch, tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "profile-a" / "snapshot.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "profile-a" / "cache"),
    }
    record = {
        "pid": 1234,
        "profileId": "profile-a",
        "profileEnvironmentFingerprint": system_manager._environment_fingerprint(spec["env"]),
        "processStartIdentity": "process-start-1234",
    }
    monkeypatch.setattr(system_manager, "_listening_pids", lambda port: [1234])
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: f"python -m uvicorn backend.main:app --port {spec['port']}")
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: None)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "process-start-1234")

    assert system_manager.find_reusable_service_pid(
        "api", spec, tmp_path, launch_record=record,
    ) == 1234


def test_profile_identity_rejects_stale_or_wrong_manager_launch_record(monkeypatch, tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "profile-a" / "snapshot.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "profile-a" / "cache"),
    }
    launch_record = {
        "pid": 1234,
        "profileId": "profile-a",
        "profileEnvironmentFingerprint": system_manager._environment_fingerprint(spec["env"]),
        "processStartIdentity": "old-process-start",
    }
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: f"python -m uvicorn backend.main:app --port {spec['port']}")
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: None)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "recycled-process-start")

    assert not system_manager._process_matches_service("api", 1234, spec, tmp_path, launch_record)


def test_profile_identity_rejects_reused_pid_when_environment_matches(monkeypatch, tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "profile-a" / "snapshot.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "profile-a" / "cache"),
    }
    launch_record = {
        "pid": 1234,
        "profileId": "profile-a",
        "profileEnvironmentFingerprint": system_manager._environment_fingerprint(spec["env"]),
        "processStartIdentity": "original-process-start",
    }
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: f"python -m uvicorn backend.main:app --port {spec['port']}")
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: dict(spec["env"]))
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "recycled-process-start")

    assert not system_manager._process_matches_service("api", 1234, spec, tmp_path, launch_record)


@pytest.mark.parametrize("invalid_pid", [None, "not-a-pid", 0, -1])
def test_profile_process_matcher_fails_closed_for_invalid_pid(monkeypatch, tmp_path, invalid_pid):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "snapshot.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "cache"),
    }
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: (_ for _ in ()).throw(AssertionError("invalid pid inspected")))

    assert not system_manager._process_matches_service("api", invalid_pid, spec, tmp_path)


@pytest.mark.skipif(system_manager.sys.platform != "darwin", reason="macOS proc_pidinfo is platform-specific")
def test_process_start_identity_reads_macos_native_creation_time():
    import datetime
    import subprocess

    identity = system_manager._process_start_identity(system_manager.os.getpid())

    assert identity is not None
    assert identity.startswith("darwin:")
    assert len(identity.split(":")) == 3
    _, seconds, microseconds = identity.split(":")
    native_start = subprocess.check_output(
        ["ps", "-p", str(system_manager.os.getpid()), "-o", "lstart="],
        text=True, env={**system_manager.os.environ, "LC_ALL": "C"},
    ).strip()
    expected_seconds = int(datetime.datetime.strptime(
        native_start, "%a %b %d %H:%M:%S %Y",
    ).timestamp())
    assert int(seconds) == expected_seconds
    assert 0 <= int(microseconds) < 1_000_000


def test_macos_start_identity_fails_closed_on_invalid_native_time(monkeypatch):
    import ctypes
    from types import SimpleNamespace

    class FakeProcPidInfo:
        def __call__(self, pid, _flavor, _arg, buffer, size):
            info = buffer._obj
            info.pbi_pid = pid
            info.pbi_start_tvsec = 1_700_000_000
            info.pbi_start_tvusec = 1_000_000
            return size

    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: SimpleNamespace(
        proc_pidinfo=FakeProcPidInfo(),
    ))
    assert system_manager._macos_process_start_identity(1234) is None


def test_process_start_identity_uses_linux_proc_start_ticks(monkeypatch):
    from io import BytesIO

    monkeypatch.setattr(system_manager.sys, "platform", "linux")
    monkeypatch.setattr(system_manager.Path, "open", lambda *args, **kwargs: BytesIO(b"123 (worker ) name) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 987654\n"))

    assert system_manager._process_start_identity(123) == "linux:123:987654"


def test_process_start_identity_uses_windows_creation_time(monkeypatch):
    monkeypatch.setattr(system_manager.sys, "platform", "win32")
    monkeypatch.setattr(system_manager, "_windows_process_start_identity", lambda pid: "windows:987654")

    assert system_manager._process_start_identity(1234) == "windows:987654"


def test_windows_process_start_identity_reads_and_closes_process_handle(monkeypatch):
    import ctypes
    import ctypes.wintypes

    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

    captured = {}

    def get_times(handle, creation, exit_time, kernel_time, user_time):
        creation_value = ctypes.cast(creation, ctypes.POINTER(type(creation._obj))).contents
        creation_value.low = 0x89ABCDEF
        creation_value.high = 0x01234567
        captured["timesHandle"] = handle
        return 1

    fake_kernel32 = type("Kernel32", (), {})()
    fake_kernel32.OpenProcess = FakeFunction(lambda access, inherit, pid: 77)
    fake_kernel32.GetProcessTimes = FakeFunction(get_times)
    fake_kernel32.CloseHandle = FakeFunction(lambda handle: captured.update(closed=handle) or 1)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: fake_kernel32, raising=False)

    assert system_manager._windows_process_start_identity(1234) == "windows:81985529216486895"
    assert captured == {"timesHandle": 77, "closed": 77}


def test_windows_process_start_identity_fails_closed_when_api_unavailable(monkeypatch):
    import ctypes

    def unavailable(*_args, **_kwargs):
        raise OSError("kernel32 unavailable")

    monkeypatch.setattr(ctypes, "WinDLL", unavailable, raising=False)

    assert system_manager._windows_process_start_identity(1234) is None


def test_profile_reuse_requires_launch_identity_and_exact_database_cache_environment(monkeypatch, tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "profile-a" / "snapshot.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "profile-a" / "cache"),
    }
    monkeypatch.setattr(system_manager, "_listening_pids", lambda port: [1234])
    monkeypatch.setattr(
        system_manager,
        "_process_command",
        lambda pid: f"{tmp_path}/.venv/bin/python -m uvicorn backend.main:app --port {spec['port']}",
    )
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: dict(spec["env"]), raising=False)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "start-1234")

    assert system_manager.find_reusable_service_pid("api", spec, tmp_path) is None
    record = {
        "pid": 1234,
        "profileId": "profile-a",
        "profileEnvironmentFingerprint": system_manager._environment_fingerprint(spec["env"]),
        "processStartIdentity": "start-1234",
    }
    assert system_manager.find_reusable_service_pid(
        "api", spec, tmp_path, launch_record=record,
    ) == 1234


def test_process_environment_returns_only_requested_values(monkeypatch):
    from io import BytesIO

    monkeypatch.setattr(system_manager.sys, "platform", "linux")
    monkeypatch.setattr(
        system_manager.Path,
        "open",
        lambda self, *args, **kwargs: BytesIO(
            b"SECRET_TOKEN=do-not-return\0NBS_ANALYTICS_DB_FILE=/tmp/db\0NBS_ANALYTICS_CACHE_DIR=/tmp/cache\0"
        ),
    )

    assert system_manager._process_environment(
        1234, {"NBS_ANALYTICS_DB_FILE", "NBS_ANALYTICS_CACHE_DIR"},
    ) == {
        "NBS_ANALYTICS_DB_FILE": "/tmp/db",
        "NBS_ANALYTICS_CACHE_DIR": "/tmp/cache",
    }


def test_process_environment_fails_closed_on_macos(monkeypatch):
    monkeypatch.setattr(system_manager.sys, "platform", "darwin")

    assert system_manager._process_environment(1234, {"NBS_ANALYTICS_DB_FILE"}) is None


def test_profile_preflight_rejects_managed_process_with_wrong_snapshot_identity(monkeypatch, tmp_path):
    db_path = tmp_path / "profile-a" / "snapshot.sqlite"
    db_path.parent.mkdir()
    db_path.write_bytes(b"snapshot")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "main.py").write_text("", encoding="utf-8")
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(db_path),
        "NBS_ANALYTICS_CACHE_DIR": str(db_path.parent / "cache"),
    }
    record = {
        "pid": 1234,
        "profileId": "profile-a",
        "profileEnvironmentFingerprint": system_manager._environment_fingerprint(spec["env"]),
        "processStartIdentity": "start-1234",
    }
    monkeypatch.setattr(system_manager, "read_state", lambda runtime_dir: {"services": {"api": record}})
    monkeypatch.setattr(system_manager, "port_is_open", lambda host, port: True)
    monkeypatch.setattr(system_manager, "process_is_alive", lambda pid: True)
    monkeypatch.setattr(
        system_manager,
        "_process_command",
        lambda pid: f"{tmp_path}/.venv/bin/python -m uvicorn backend.main:app --port 18601",
    )
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "start-1234")
    monkeypatch.setattr(
        system_manager,
        "_process_environment",
        lambda pid, keys: {
            "NBS_ANALYTICS_DB_FILE": str(tmp_path / "other-profile" / "snapshot.sqlite"),
            "NBS_ANALYTICS_CACHE_DIR": str(db_path.parent / "cache"),
        },
        raising=False,
    )
    monkeypatch.setattr(system_manager, "_command_available", lambda command: True)

    result = system_manager.preflight(
        tmp_path, {"api": spec}, tmp_path / "profile-a", database_path=db_path,
    )

    assert result["ok"] is False
    assert result["issues"] == ["api port 18601 is occupied by an unmanaged process"]


def test_profile_preflight_blocks_managed_process_that_is_not_ready(monkeypatch, tmp_path):
    snapshot = tmp_path / "snapshot.sqlite"
    snapshot.write_bytes(b"snapshot")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "main.py").write_text("", encoding="utf-8")
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(snapshot),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "cache"),
    }
    record = {
        "pid": 1234,
        "profileId": "profile-a",
        "profileEnvironmentFingerprint": system_manager._environment_fingerprint(spec["env"]),
        "processStartIdentity": "start-1234",
    }
    monkeypatch.setattr(system_manager, "read_state", lambda runtime_dir: {"services": {"api": record}})
    monkeypatch.setattr(system_manager, "port_is_open", lambda host, port: True)
    monkeypatch.setattr(system_manager, "process_is_alive", lambda pid: True)
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: f"python -m uvicorn backend.main:app --port {spec['port']}")
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: dict(spec["env"]))
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "start-1234")
    monkeypatch.setattr(system_manager, "endpoint_is_ready", lambda url: False)
    monkeypatch.setattr(system_manager, "_command_available", lambda command: True)

    result = system_manager.preflight(
        tmp_path, {"api": spec}, tmp_path / "runtime", database_path=snapshot,
    )

    assert result["ok"] is False
    assert result["issues"] == ["api managed process on port 18601 is not ready"]


def test_preflight_checks_profile_snapshot_instead_of_missing_root_database(monkeypatch, tmp_path):
    snapshot = tmp_path / ".nbs_agent_runtime" / "verification" / "profile-test" / "snapshot.sqlite"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(b"SQLite snapshot")
    specs = {
        "api": {
            "port": 18601,
            "ready_url": "http://127.0.0.1:18601/api/ready",
            "required_files": [],
            "command": [],
        },
    }
    monkeypatch.setattr(system_manager, "read_state", lambda runtime_dir: {"services": {}})
    monkeypatch.setattr(system_manager, "port_is_open", lambda host, port: False)

    result = system_manager.preflight(
        tmp_path,
        specs,
        tmp_path / ".nbs_agent_runtime" / "verification" / "profile-test",
        database_path=snapshot,
    )

    assert result == {"ok": True, "issues": []}
    assert not (tmp_path / "nbs_marketing_data.db").exists()


def test_build_service_specs_uses_profile_ports_and_identity(tmp_path):
    specs = system_manager.build_service_specs(
        tmp_path, "python", "npm", ports={"api": 18601, "streamlit": 18502, "vue": 15173}, profile_id="profile-test"
    )
    assert specs["api"]["port"] == 18601
    assert specs["streamlit"]["port"] == 18502
    assert specs["vue"]["port"] == 15173
    assert all(spec["profileId"] == "profile-test" for spec in specs.values())


def test_api_service_uses_lightweight_readiness_endpoint(tmp_path):
    specs = system_manager.build_service_specs(tmp_path, "python", "npm")

    assert specs["api"]["ready_url"] == "http://127.0.0.1:8601/api/ready"


def test_start_services_accepts_verification_profile_without_using_default_ports(monkeypatch, tmp_path):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        profile_id="profile-test",
        services=SimpleNamespace(ports={"api": 18601, "streamlit": 18502, "vue": 15173}),
    )
    paths = SimpleNamespace(
        db_path=tmp_path / "snapshot.sqlite",
        cache_path=tmp_path / "profile-cache",
    )
    paths.db_path.write_bytes(b"SQLite snapshot")
    paths.cache_path.mkdir()
    captured = {}
    monkeypatch.setattr(system_manager, "_resolve_runtime", lambda root: ("python", "npm"))
    monkeypatch.setattr(system_manager, "_verification_profile_paths", lambda *_args: paths)

    def preflight(root, specs, runtime, *, database_path=None):
        captured.update(specs=specs, runtime=runtime, database_path=database_path)
        return {"ok": True, "issues": []}

    monkeypatch.setattr(system_manager, "preflight", preflight)
    monkeypatch.setattr(system_manager, "read_state", lambda runtime: {"services": {}})
    monkeypatch.setattr(system_manager, "write_state", lambda *args: None)
    monkeypatch.setattr(system_manager, "find_reusable_service_pid", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        system_manager,
        "_spawn_service",
        lambda name, spec, runtime: {"pid": 1, "port": spec["port"], "profileId": "profile-test"},
    )
    monkeypatch.setattr(system_manager, "wait_for_ready", lambda *args: True)
    assert system_manager.start_services(tmp_path, open_browser=False, profile=profile) == 0
    assert {value["port"] for value in captured["specs"].values()} == {18601, 18502, 15173}
    assert captured["runtime"] == tmp_path / ".nbs_agent_runtime/verification/profile-test"
    assert captured["database_path"] == paths.db_path
    assert all(
        spec["env"] == {
            "NBS_ANALYTICS_DB_FILE": str(paths.db_path),
            "NBS_ANALYTICS_CACHE_DIR": str(paths.cache_path),
        }
        for spec in captured["specs"].values()
    )


def test_spawn_service_merges_profile_environment_without_dropping_existing_env(monkeypatch, tmp_path):
    import subprocess
    from types import SimpleNamespace

    observed = {}

    def fake_popen(command, **kwargs):
        observed.update(command=command, **kwargs)
        return SimpleNamespace(pid=123)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "start-token")
    monkeypatch.setenv("NBS_TEST_PRESERVED_ENV", "present")
    spec = {
        "command": ["runner", "service"],
        "cwd": tmp_path,
        "port": 18601,
        "ready_url": "http://127.0.0.1:18601/api/ready",
        "env": {
            "NBS_ANALYTICS_DB_FILE": str(tmp_path / "snapshot.sqlite"),
            "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "cache"),
        },
    }

    system_manager._spawn_service("api", spec, tmp_path / "runtime")

    assert observed["env"]["NBS_TEST_PRESERVED_ENV"] == "present"
    assert observed["env"]["NBS_ANALYTICS_DB_FILE"] == spec["env"]["NBS_ANALYTICS_DB_FILE"]
    assert observed["env"]["NBS_ANALYTICS_CACHE_DIR"] == spec["env"]["NBS_ANALYTICS_CACHE_DIR"]


def test_profile_spawn_terminates_child_when_process_start_identity_is_unavailable(monkeypatch, tmp_path):
    import subprocess
    from types import SimpleNamespace

    process = SimpleNamespace(pid=123, terminated=False, waited=False)
    process.terminate = lambda: setattr(process, "terminated", True)
    process.wait = lambda **_kwargs: setattr(process, "waited", True)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: None)
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm",
        ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-a",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "snapshot.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "cache"),
    }

    with pytest.raises(RuntimeError, match="process-start identity unavailable"):
        system_manager._spawn_service("api", spec, tmp_path / "runtime")

    assert process.terminated is True
    assert process.waited is True


def test_start_services_profile_failure_stops_same_profile(monkeypatch, tmp_path):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        profile_id="profile-test",
        services=SimpleNamespace(ports={"api": 18601, "streamlit": 18502, "vue": 15173}),
    )
    paths = SimpleNamespace(db_path=tmp_path / "snapshot.sqlite", cache_path=tmp_path / "profile-cache")
    paths.db_path.write_bytes(b"SQLite snapshot")
    paths.cache_path.mkdir()
    captured = {}
    monkeypatch.setattr(system_manager, "_resolve_runtime", lambda root: ("python", "npm"))
    monkeypatch.setattr(system_manager, "_verification_profile_paths", lambda *_args: paths)
    monkeypatch.setattr(system_manager, "preflight", lambda *args, **kwargs: {"ok": True, "issues": []})
    monkeypatch.setattr(system_manager, "read_state", lambda runtime: {"services": {}})
    monkeypatch.setattr(system_manager, "write_state", lambda *args: None)
    monkeypatch.setattr(system_manager, "find_reusable_service_pid", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        system_manager,
        "_spawn_service",
        lambda *args: {"pid": 1, "logPath": str(tmp_path / "service.log")},
    )
    monkeypatch.setattr(system_manager, "wait_for_ready", lambda *args: False)
    monkeypatch.setattr(
        system_manager,
        "stop_services",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs) or 0,
    )

    assert system_manager.start_services(tmp_path, open_browser=False, profile=profile) == 1
    assert captured == {"args": (tmp_path,), "kwargs": {"profile": profile}}


def test_start_services_profile_spawn_identity_failure_stops_partial_profile_stack(monkeypatch, tmp_path):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        profile_id="profile-test",
        services=SimpleNamespace(ports={"api": 18601, "streamlit": 18502, "vue": 15173}),
    )
    paths = SimpleNamespace(db_path=tmp_path / "snapshot.sqlite", cache_path=tmp_path / "profile-cache")
    paths.db_path.write_bytes(b"SQLite snapshot")
    paths.cache_path.mkdir()
    stopped = {}
    monkeypatch.setattr(system_manager, "_resolve_runtime", lambda root: ("python", "npm"))
    monkeypatch.setattr(system_manager, "_verification_profile_paths", lambda *_args: paths)
    monkeypatch.setattr(system_manager, "preflight", lambda *args, **kwargs: {"ok": True, "issues": []})
    monkeypatch.setattr(system_manager, "read_state", lambda runtime: {"services": {}})
    monkeypatch.setattr(system_manager, "write_state", lambda *args: None)
    monkeypatch.setattr(system_manager, "find_reusable_service_pid", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        system_manager, "_spawn_service",
        lambda *args: (_ for _ in ()).throw(RuntimeError("process-start identity unavailable")),
    )
    monkeypatch.setattr(
        system_manager, "stop_services",
        lambda *args, **kwargs: stopped.update(args=args, kwargs=kwargs) or 0,
    )

    assert system_manager.start_services(tmp_path, open_browser=False, profile=profile) == 1
    assert stopped == {"args": (tmp_path,), "kwargs": {"profile": profile}}


def test_service_status_profile_rejects_missing_managed_pid(monkeypatch, tmp_path):
    from types import SimpleNamespace
    profile = SimpleNamespace(profile_id="profile-test", git_head="a" * 40, project_id="nbs_analytics", services=SimpleNamespace(profile_namespace="profile-test", ports={"api": 18601, "streamlit": 18502, "vue": 15173}))
    _mock_profile_paths(monkeypatch, tmp_path)
    captured = {}
    monkeypatch.setattr(system_manager, "read_state", lambda runtime_dir: captured.update({"runtime_dir": runtime_dir}) or {"services": {}})
    monkeypatch.setattr(system_manager, "process_is_alive", lambda pid: False)
    monkeypatch.setattr(system_manager, "endpoint_is_ready", lambda url: True)
    result = system_manager.service_status(tmp_path, profile=profile)
    assert result["status"] == "not_ready"
    assert all(item["alive"] is False and item["identityMatch"] is False for item in result["services"].values())
    assert captured["runtime_dir"] == tmp_path / ".nbs_agent_runtime" / "verification" / "profile-test"


def test_http_acceptance_profile_requires_matching_owner_and_identity(monkeypatch, tmp_path):
    from types import SimpleNamespace
    profile = SimpleNamespace(profile_id="profile-test", git_head="a" * 40, project_id="nbs_analytics", services=SimpleNamespace(profile_namespace="profile-test", ports={"api": 18601, "streamlit": 18502, "vue": 15173}))
    paths = _mock_profile_paths(monkeypatch, tmp_path)
    specs = system_manager.build_service_specs(tmp_path, "python", "npm", ports=dict(profile.services.ports), profile_id=profile.profile_id)
    monkeypatch.setattr(system_manager, "read_state", lambda runtime_dir: {"services": {name: _managed_profile_record(index + 1, "profile-test", paths) for index, name in enumerate(specs)}})
    monkeypatch.setattr(system_manager, "process_is_alive", lambda pid: True)
    monkeypatch.setattr(system_manager, "endpoint_is_ready", lambda url: True)
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: f"{tmp_path}/.venv/bin/python -m streamlit run {tmp_path}/app.py --server.port 18502" if pid == 1 else (f"{tmp_path}/.venv/bin/python -m uvicorn backend.main:app --port 18601" if pid == 2 else f"{tmp_path}/frontend/node_modules/.bin/vite --port 15173"))
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path / "frontend" if pid == 3 else tmp_path)
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: {
        "NBS_ANALYTICS_DB_FILE": str(paths.db_path),
        "NBS_ANALYTICS_CACHE_DIR": str(paths.cache_path),
    }, raising=False)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: f"start-{pid}")
    result = system_manager.run_http_acceptance(tmp_path, profile=profile)
    assert result["status"] == "passed"
    assert all(item["ownerMatch"] and item["identityMatch"] for item in result["checks"].values())


def test_http_acceptance_profile_rejects_snapshot_environment_drift(monkeypatch, tmp_path):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        profile_id="profile-test",
        git_head="a" * 40,
        project_id="nbs_analytics",
        services=SimpleNamespace(profile_namespace="profile-test", ports={"api": 18601, "streamlit": 18502, "vue": 15173}),
    )
    paths = _mock_profile_paths(monkeypatch, tmp_path)
    specs = system_manager.build_service_specs(tmp_path, "python", "npm", ports=dict(profile.services.ports), profile_id=profile.profile_id)
    monkeypatch.setattr(system_manager, "read_state", lambda runtime_dir: {"services": {name: _managed_profile_record(index + 1, profile.profile_id, paths) for index, name in enumerate(specs)}})
    monkeypatch.setattr(system_manager, "process_is_alive", lambda pid: True)
    monkeypatch.setattr(system_manager, "endpoint_is_ready", lambda url: True)
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: f"{tmp_path}/.venv/bin/python -m streamlit run {tmp_path}/app.py --server.port 18502" if pid == 1 else (f"{tmp_path}/.venv/bin/python -m uvicorn backend.main:app --port 18601" if pid == 2 else f"{tmp_path}/frontend/node_modules/.bin/vite --port 15173"))
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path / "frontend" if pid == 3 else tmp_path)
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "wrong-snapshot.sqlite") if pid == 2 else str(paths.db_path),
        "NBS_ANALYTICS_CACHE_DIR": str(paths.cache_path),
    }, raising=False)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: f"start-{pid}")

    result = system_manager.run_http_acceptance(tmp_path, profile=profile)

    assert result["status"] == "failed"
    assert result["checks"]["api"]["identityMatch"] is False
    assert result["checks"]["streamlit"]["identityMatch"] is True


def test_http_acceptance_profile_rejects_wrong_identity(monkeypatch, tmp_path):
    from types import SimpleNamespace
    profile = SimpleNamespace(profile_id="profile-test", git_head="a" * 40, project_id="nbs_analytics", services=SimpleNamespace(profile_namespace="profile-test", ports={"api": 18601, "streamlit": 18502, "vue": 15173}))
    _mock_profile_paths(monkeypatch, tmp_path)
    specs = system_manager.build_service_specs(tmp_path, "python", "npm", ports=dict(profile.services.ports), profile_id=profile.profile_id)
    monkeypatch.setattr(system_manager, "read_state", lambda runtime_dir: {"services": {name: {"pid": index + 1, "profileId": "other-profile"} for index, name in enumerate(specs)}})
    monkeypatch.setattr(system_manager, "process_is_alive", lambda pid: True)
    monkeypatch.setattr(system_manager, "endpoint_is_ready", lambda url: True)
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: f"{tmp_path}/app.py streamlit --server.port 18502" if pid == 1 else (f"{tmp_path}/backend.main:app uvicorn --port 18601" if pid == 2 else f"{tmp_path}/frontend vite --port 15173"))
    result = system_manager.run_http_acceptance(tmp_path, profile=profile)
    assert result["status"] == "failed"
    assert all(item["identityMatch"] is False for item in result["checks"].values())


def test_command_matching_rejects_port_substring_collision(tmp_path):
    spec = system_manager.build_service_specs(tmp_path, "python", "npm", ports={"api": 18601, "streamlit": 18502, "vue": 15173})["api"]
    command = f"{tmp_path}/.venv/bin/python -m uvicorn backend.main:app --port 118601"
    assert system_manager._command_matches_service("api", command, spec, tmp_path) is False


def test_command_matching_accepts_relative_entrypoint_when_process_cwd_matches(tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm", ports={"api": 18601, "streamlit": 18502, "vue": 15173}
    )["streamlit"]
    command = "python -m streamlit run app.py --server.port 18502"
    assert system_manager._command_matches_service(
        "streamlit", command, spec, tmp_path, process_cwd=tmp_path
    ) is True


def test_command_matching_rejects_relative_entrypoint_from_other_cwd(tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm", ports={"api": 18601, "streamlit": 18502, "vue": 15173}
    )["streamlit"]
    command = "python -m streamlit run app.py --server.port 18502"
    assert system_manager._command_matches_service(
        "streamlit", command, spec, tmp_path, process_cwd=tmp_path / "other"
    ) is False


def test_vue_command_matching_uses_frontend_working_directory(tmp_path):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm", ports={"api": 18601, "streamlit": 18502, "vue": 15173}
    )["vue"]
    assert system_manager._command_matches_service(
        "vue", "npm run dev --host 127.0.0.1 --port 15173", spec, tmp_path,
        process_cwd=tmp_path / "frontend",
    ) is True


def test_parser_accepts_verification_profile_option():
    args = system_manager.build_parser().parse_args(["acceptance", "--verification-profile", "profile.json"])
    assert args.verification_profile == "profile.json"


def test_stop_services_profile_stops_only_matching_profile_processes(monkeypatch, tmp_path):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        profile_id="profile-test",
        services=SimpleNamespace(ports={"api": 18601, "streamlit": 18502, "vue": 15173}),
    )
    paths = _mock_profile_paths(monkeypatch, tmp_path)
    specs = system_manager.build_service_specs(
        tmp_path, "python", "npm", ports=dict(profile.services.ports), profile_id=profile.profile_id
    )
    state = {
        "status": "ready",
        "services": {
            "streamlit": _managed_profile_record(101, "profile-test", paths),
            "api": _managed_profile_record(102, "other-profile", paths),
            "vue": _managed_profile_record(103, "profile-test", paths),
        },
    }
    captured = {}
    commands = {
        101: "python -m streamlit run app.py --server.port 18502",
        102: "python -m uvicorn backend.main:app --port 18601",
        103: "npm run dev --host 127.0.0.1 --port 15173",
    }
    monkeypatch.setattr(system_manager, "read_state", lambda runtime: state)
    monkeypatch.setattr(system_manager, "process_is_alive", lambda pid: True)
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: commands[pid])
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: {
        "NBS_ANALYTICS_DB_FILE": str(paths.db_path),
        "NBS_ANALYTICS_CACHE_DIR": str(paths.cache_path),
    }, raising=False)
    monkeypatch.setattr(
        system_manager,
        "_process_cwd",
        lambda pid: tmp_path / "frontend" if pid == 103 else tmp_path,
    )
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: f"start-{pid}")
    monkeypatch.setattr(system_manager, "_terminate_pid", lambda pid: captured.setdefault("terminated", []).append(pid))
    monkeypatch.setattr(system_manager, "write_state", lambda value, runtime: captured.update(state=value, runtime=runtime))

    assert system_manager.stop_services(tmp_path, profile=profile) == 0
    assert captured["terminated"] == [101, 103]
    assert captured["runtime"] == tmp_path / ".nbs_agent_runtime" / "verification" / "profile-test"
    assert captured["state"] == {
        "status": "partial",
        "services": {"api": state["services"]["api"]},
    }


def test_stop_services_report_separates_stopped_and_identity_mismatch(monkeypatch, tmp_path):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        profile_id="profile-test",
        services=SimpleNamespace(ports={"api": 18601, "streamlit": 18502, "vue": 15173}),
    )
    paths = _mock_profile_paths(monkeypatch, tmp_path)
    state = {
        "status": "ready",
        "services": {
            "streamlit": _managed_profile_record(101, "profile-test", paths),
            "api": _managed_profile_record(102, "other-profile", paths),
        },
    }
    commands = {
        101: "python -m streamlit run app.py --server.port 18502",
        102: "python -m uvicorn backend.main:app --port 18601",
    }
    captured = {}
    monkeypatch.setattr(system_manager, "read_state", lambda runtime: state)
    monkeypatch.setattr(system_manager, "process_is_alive", lambda pid: True)
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: commands[pid])
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: {
        "NBS_ANALYTICS_DB_FILE": str(paths.db_path),
        "NBS_ANALYTICS_CACHE_DIR": str(paths.cache_path),
    }, raising=False)
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: f"start-{pid}")
    monkeypatch.setattr(system_manager, "_terminate_pid", lambda pid: captured.setdefault("terminated", []).append(pid))
    monkeypatch.setattr(system_manager, "write_state", lambda value, runtime: captured.update(state=value, runtime=runtime))

    report = system_manager.stop_services_report(tmp_path, profile=profile)

    assert report["schemaVersion"] == "service-stop-result-v1"
    assert report["status"] == "partial"
    assert report["stopped"] == ["streamlit"]
    assert report["identityMismatches"] == ["api"]
    assert captured["terminated"] == [101]
    assert captured["state"] == {
        "status": "partial",
        "services": {"api": state["services"]["api"]},
    }
    assert report["durationSeconds"] >= 0


def test_stop_services_does_not_terminate_profile_process_with_wrong_snapshot(monkeypatch, tmp_path):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        profile_id="profile-test",
        services=SimpleNamespace(ports={"api": 18601, "streamlit": 18502, "vue": 15173}),
    )
    paths = _mock_profile_paths(monkeypatch, tmp_path)
    state = {"status": "ready", "services": {"api": _managed_profile_record(102, profile.profile_id, paths)}}
    captured = {}
    monkeypatch.setattr(system_manager, "read_state", lambda runtime: state)
    monkeypatch.setattr(system_manager, "process_is_alive", lambda pid: True)
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: f"{tmp_path}/.venv/bin/python -m uvicorn backend.main:app --port 18601")
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "start-102")
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "other-profile.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(paths.cache_path),
    }, raising=False)
    monkeypatch.setattr(system_manager, "_terminate_pid", lambda pid: captured.setdefault("terminated", []).append(pid))
    monkeypatch.setattr(system_manager, "write_state", lambda value, runtime: captured.update(state=value, runtime=runtime))

    report = system_manager.stop_services_report(tmp_path, profile=profile)

    assert report["stopped"] == []
    assert report["identityMismatches"] == ["api"]
    assert captured.get("terminated", []) == []


def test_stop_services_does_not_terminate_default_process_without_matching_launch_identity(monkeypatch, tmp_path):
    state = {"status": "ready", "services": {"api": {"pid": 4321, "profileId": None}}}
    captured = {}
    monkeypatch.setattr(system_manager, "_resolve_runtime", lambda _root: ("python", "npm"))
    monkeypatch.setattr(system_manager, "read_state", lambda _runtime: state)
    monkeypatch.setattr(system_manager, "process_is_alive", lambda _pid: True)
    monkeypatch.setattr(system_manager, "_process_matches_service", lambda *args, **kwargs: False)
    monkeypatch.setattr(system_manager, "_terminate_pid", lambda pid: captured.setdefault("terminated", []).append(pid))
    monkeypatch.setattr(system_manager, "write_state", lambda value, runtime: captured.update(state=value, runtime=runtime))

    report = system_manager.stop_services_report(tmp_path)

    assert report["status"] == "partial"
    assert report["stopped"] == []
    assert report["identityMismatches"] == ["api"]
    assert captured.get("terminated", []) == []
    assert captured["state"]["services"] == state["services"]


def test_stop_services_terminates_default_service_with_matching_launch_identity(monkeypatch, tmp_path):
    spec = system_manager.build_service_specs(tmp_path, "python", "npm")["api"]
    pid = 4322
    state = {
        "status": "ready",
        "services": {"api": {"pid": pid, "profileId": None, "processStartIdentity": "start-4322"}},
    }
    captured = {}
    monkeypatch.setattr(system_manager, "_resolve_runtime", lambda _root: ("python", "npm"))
    monkeypatch.setattr(system_manager, "read_state", lambda _runtime: state)
    monkeypatch.setattr(system_manager, "process_is_alive", lambda _pid: True)
    monkeypatch.setattr(
        system_manager, "_process_command",
        lambda _pid: f"{tmp_path}/.venv/bin/python -m uvicorn backend.main:app --port {spec['port']}",
    )
    monkeypatch.setattr(system_manager, "_process_cwd", lambda _pid: tmp_path)
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda _pid: "start-4322")
    monkeypatch.setattr(system_manager, "_terminate_pid", lambda value: captured.setdefault("terminated", []).append(value))
    monkeypatch.setattr(system_manager, "write_state", lambda value, runtime: captured.update(state=value, runtime=runtime))

    report = system_manager.stop_services_report(tmp_path)

    assert report["status"] == "clean"
    assert report["stopped"] == ["api"]
    assert captured["terminated"] == [pid]


def test_stop_services_int_wrapper_remains_zero(monkeypatch, tmp_path):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        profile_id="profile-test",
        services=SimpleNamespace(ports={"api": 18601, "streamlit": 18502, "vue": 15173}),
    )
    monkeypatch.setattr(
        system_manager,
        "stop_services_report",
        lambda *args, **kwargs: {
            "schemaVersion": "service-stop-result-v1",
            "status": "clean",
            "errors": [],
        },
    )

    assert system_manager.stop_services(tmp_path, profile=profile) == 0


def test_cli_stop_prints_structured_report_and_passes_verification_profile(monkeypatch, capsys):
    from types import SimpleNamespace

    profile = SimpleNamespace(profile_id="profile-test")
    monkeypatch.setattr(sys, "argv", ["system_manager.py", "stop", "--verification-profile", "profile.json"])
    monkeypatch.setattr(system_manager, "_load_verification_profile", lambda value: profile)
    monkeypatch.setattr(
        system_manager,
        "stop_services_report",
        lambda **kwargs: {
            "schemaVersion": "service-stop-result-v1",
            "profileId": kwargs["profile"].profile_id,
            "status": "clean",
            "stopped": [],
            "alreadyAbsent": [],
            "skipped": [],
            "identityMismatches": [],
            "errors": [],
            "durationSeconds": 0,
        },
    )

    assert system_manager.main() == 0
    assert '"schemaVersion": "service-stop-result-v1"' in capsys.readouterr().out


@pytest.mark.parametrize(("platform_name", "expected"), [("darwin", True), ("linux", False)])
def test_profile_identity_environment_unavailable_is_macos_only(
    monkeypatch, tmp_path, platform_name, expected
):
    spec = system_manager.build_service_specs(
        tmp_path, "python", "npm", ports={"api": 18601, "streamlit": 18502, "vue": 15173},
        profile_id="profile-test",
    )["api"]
    spec["env"] = {
        "NBS_ANALYTICS_DB_FILE": str(tmp_path / "snapshot.sqlite"),
        "NBS_ANALYTICS_CACHE_DIR": str(tmp_path / "cache"),
    }
    launch_record = {
        "pid": 1234,
        "profileId": "profile-test",
        "profileEnvironmentFingerprint": system_manager._environment_fingerprint(spec["env"]),
        "processStartIdentity": "start-1234",
    }
    monkeypatch.setattr(system_manager.sys, "platform", platform_name)
    monkeypatch.setattr(system_manager, "_process_cwd", lambda pid: tmp_path)
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: " ".join(spec["command"]))
    monkeypatch.setattr(system_manager, "_process_start_identity", lambda pid: "start-1234")
    monkeypatch.setattr(system_manager, "_process_environment", lambda pid, keys: None)

    assert system_manager._process_matches_service(
        "api", 1234, spec, tmp_path, launch_record
    ) is expected
