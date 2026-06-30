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
