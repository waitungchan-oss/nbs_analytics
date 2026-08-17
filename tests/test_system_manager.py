from pathlib import Path

from scripts import system_manager


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


def test_build_service_specs_uses_profile_ports_and_identity(tmp_path):
    specs = system_manager.build_service_specs(
        tmp_path, "python", "npm", ports={"api": 18601, "streamlit": 18502, "vue": 15173}, profile_id="profile-test"
    )
    assert specs["api"]["port"] == 18601
    assert specs["streamlit"]["port"] == 18502
    assert specs["vue"]["port"] == 15173
    assert all(spec["profileId"] == "profile-test" for spec in specs.values())


def test_service_status_profile_rejects_missing_managed_pid(monkeypatch, tmp_path):
    from types import SimpleNamespace
    profile = SimpleNamespace(profile_id="profile-test", git_head="a" * 40, project_id="nbs_analytics", services=SimpleNamespace(profile_namespace="profile-test", ports={"api": 18601, "streamlit": 18502, "vue": 15173}))
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
    specs = system_manager.build_service_specs(tmp_path, "python", "npm", ports=dict(profile.services.ports), profile_id=profile.profile_id)
    monkeypatch.setattr(system_manager, "read_state", lambda runtime_dir: {"services": {name: {"pid": index + 1, "profileId": "profile-test"} for index, name in enumerate(specs)}})
    monkeypatch.setattr(system_manager, "process_is_alive", lambda pid: True)
    monkeypatch.setattr(system_manager, "endpoint_is_ready", lambda url: True)
    monkeypatch.setattr(system_manager, "_process_command", lambda pid: f"{tmp_path}/.venv/bin/python -m streamlit run {tmp_path}/app.py --server.port 18502" if pid == 1 else (f"{tmp_path}/.venv/bin/python -m uvicorn backend.main:app --port 18601" if pid == 2 else f"{tmp_path}/frontend/node_modules/.bin/vite --port 15173"))
    result = system_manager.run_http_acceptance(tmp_path, profile=profile)
    assert result["status"] == "passed"
    assert all(item["ownerMatch"] and item["identityMatch"] for item in result["checks"].values())


def test_http_acceptance_profile_rejects_wrong_identity(monkeypatch, tmp_path):
    from types import SimpleNamespace
    profile = SimpleNamespace(profile_id="profile-test", git_head="a" * 40, project_id="nbs_analytics", services=SimpleNamespace(profile_namespace="profile-test", ports={"api": 18601, "streamlit": 18502, "vue": 15173}))
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


def test_parser_accepts_verification_profile_option():
    args = system_manager.build_parser().parse_args(["acceptance", "--verification-profile", "profile.json"])
    assert args.verification_profile == "profile.json"
