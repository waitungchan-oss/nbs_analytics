from __future__ import annotations

import argparse
import json
import os
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
RUNTIME_DIR = PROJECT_ROOT / ".nbs_runtime"
LOG_DIR = RUNTIME_DIR / "logs"
STATE_PATH = RUNTIME_DIR / "services.json"


def build_service_specs(
    project_root: Path,
    python_bin: str,
    npm_bin: str,
    *,
    ports: dict[str, int] | None = None,
    profile_id: str | None = None,
) -> dict:
    ports = ports or {"api": 8601, "streamlit": 8502, "vue": 5173}
    required_ports = {"api", "streamlit", "vue"}
    if set(ports) != required_ports or len(set(ports.values())) != len(ports):
        raise ValueError("service ports must contain unique api, streamlit, and vue ports")
    api_port, streamlit_port, vue_port = (int(ports[name]) for name in ("api", "streamlit", "vue"))
    return {
        "streamlit": {
            "port": streamlit_port,
            "ready_url": f"http://127.0.0.1:{streamlit_port}/_stcore/health",
            "browser_url": f"http://127.0.0.1:{streamlit_port}/",
            "profileId": profile_id,
            "cwd": project_root,
            "required_files": [project_root / "app.py"],
            "command": [
                python_bin,
                "-m",
                "streamlit",
                "run",
                "app.py",
                "--server.port",
                str(streamlit_port),
                "--server.address",
                "127.0.0.1",
                "--server.headless",
                "true",
            ],
        },
        "api": {
            "port": api_port,
            "ready_url": f"http://127.0.0.1:{api_port}/api/health",
            "browser_url": f"http://127.0.0.1:{api_port}/docs",
            "profileId": profile_id,
            "cwd": project_root,
            "required_files": [project_root / "backend" / "main.py"],
            "command": [
                python_bin,
                "-m",
                "uvicorn",
                "backend.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
        },
        "vue": {
            "port": vue_port,
            "ready_url": f"http://127.0.0.1:{vue_port}/",
            "browser_url": f"http://127.0.0.1:{vue_port}/",
            "profileId": profile_id,
            "cwd": project_root / "frontend",
            "required_files": [
                project_root / "frontend" / "package.json",
                project_root / "frontend" / "node_modules",
            ],
            "command": [
                npm_bin,
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                str(vue_port),
            ],
        },
    }


def read_state(runtime_dir: Path = RUNTIME_DIR) -> dict:
    path = runtime_dir / "services.json"
    if not path.exists():
        return {"services": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"services": {}}


def write_state(state: dict, runtime_dir: Path = RUNTIME_DIR) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / "services.json"
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def process_is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def _listening_pids(port: int) -> list[int]:
    if os.name == "nt":
        return []
    try:
        result = subprocess.run(
            ["lsof", "-tiTCP:%s" % int(port), "-sTCP:LISTEN"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            continue
    return pids


def _process_command(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    return result.stdout.strip()


def _process_cwd(pid: int) -> Path | None:
    if os.name == "nt":
        return None
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(int(pid)), "-d", "cwd", "-Fn"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("n") and len(line) > 1:
            return Path(line[1:]).resolve()
    return None


def _command_matches_service(
    name: str,
    command: str,
    spec: dict,
    project_root: Path,
    *,
    process_cwd: Path | None = None,
) -> bool:
    command = command.replace("\\", "/")
    project = str(project_root).replace("\\", "/")
    expected_port = str(spec["port"])
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    def has_port(*flags: str) -> bool:
        for index, token in enumerate(tokens):
            for flag in flags:
                if token == flag and index + 1 < len(tokens) and tokens[index + 1] == expected_port:
                    return True
                if token.startswith(flag + "=") and token.split("=", 1)[1] == expected_port:
                    return True
        return False
    expected_cwd = Path(spec.get("cwd") or project_root).resolve()
    cwd_match = process_cwd is not None and process_cwd == expected_cwd
    if project and project in command:
        project_match = True
    else:
        project_match = cwd_match or any(str(item).replace("\\", "/") in command for item in spec.get("required_files", []))
    if name == "streamlit":
        return project_match and "streamlit" in command and "app.py" in command and has_port("--server.port")
    if name == "api":
        return project_match and "uvicorn" in command and "backend.main:app" in command and has_port("--port")
    if name == "vue":
        return project_match and ("vite" in command or "npm" in command) and has_port("--port")
    return False


def find_reusable_service_pid(name: str, spec: dict, project_root: Path) -> int | None:
    for pid in _listening_pids(spec["port"]):
        if _command_matches_service(name, _process_command(pid), spec, project_root):
            return pid
    return None


def _adopt_service_record(pid: int, spec: dict, name: str, runtime_dir: Path) -> dict:
    return {
        "pid": int(pid),
        "port": spec["port"],
        "readyUrl": spec["ready_url"],
        "logPath": str(runtime_dir / "logs" / f"{name}.log"),
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "adopted": True,
        "profileId": spec.get("profileId"),
    }


def endpoint_is_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return 200 <= response.status < 500
    except (OSError, urllib.error.URLError):
        return False


def rotate_log(log_path: Path, keep: int = 5) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    for candidate in log_path.parent.glob(f"{log_path.name}.*"):
        suffix = candidate.name.removeprefix(f"{log_path.name}.")
        if suffix.isdigit() and int(suffix) >= keep:
            candidate.unlink()
    for index in range(keep - 1, 0, -1):
        source = log_path.with_name(f"{log_path.name}.{index}")
        if source.exists():
            source.replace(log_path.with_name(f"{log_path.name}.{index + 1}"))
    if log_path.exists():
        log_path.replace(log_path.with_name(f"{log_path.name}.1"))


def _command_available(command: str) -> bool:
    path = Path(command)
    return path.exists() if path.is_absolute() else shutil.which(command) is not None


def preflight(project_root: Path, specs: dict, runtime_dir: Path = RUNTIME_DIR) -> dict:
    issues: list[str] = []
    state = read_state(runtime_dir)
    managed = state.get("services", {})
    for name, spec in specs.items():
        for required in spec.get("required_files", []):
            if not Path(required).exists():
                issues.append(f"{name} required path is missing: {required}")
        command = spec.get("command") or []
        if command and not _command_available(str(command[0])):
            issues.append(f"{name} executable is unavailable: {command[0]}")
        if port_is_open("127.0.0.1", spec["port"]):
            pid = (managed.get(name) or {}).get("pid")
            if not process_is_alive(pid):
                reusable_pid = find_reusable_service_pid(name, spec, project_root)
                if not reusable_pid or not endpoint_is_ready(spec["ready_url"]):
                    issues.append(f"{name} port {spec['port']} is occupied by an unmanaged process")
    if not (project_root / "nbs_marketing_data.db").exists():
        issues.append("SQLite database is missing: nbs_marketing_data.db")
    return {"ok": not issues, "issues": issues}


def _resolve_runtime(project_root: Path) -> tuple[str, str]:
    venv_python = project_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python_bin = str(venv_python if venv_python.exists() else Path(sys.executable))
    npm_bin = shutil.which("npm") or shutil.which("npm.cmd") or "npm"
    return python_bin, npm_bin


def _spawn_service(name: str, spec: dict, runtime_dir: Path) -> dict:
    log_dir = runtime_dir / "logs"
    log_path = log_dir / f"{name}.log"
    rotate_log(log_path)
    log_handle = log_path.open("ab", buffering=0)
    kwargs = {
        "cwd": str(spec["cwd"]),
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(spec["command"], **kwargs)
    log_handle.close()
    return {
        "pid": process.pid,
        "port": spec["port"],
        "readyUrl": spec["ready_url"],
        "logPath": str(log_path),
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profileId": spec.get("profileId"),
    }


def wait_for_ready(spec: dict, pid: int, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_is_alive(pid):
            return False
        if endpoint_is_ready(spec["ready_url"]):
            return True
        time.sleep(0.4)
    return False


def start_services(project_root: Path = PROJECT_ROOT, open_browser: bool = True) -> int:
    python_bin, npm_bin = _resolve_runtime(project_root)
    specs = build_service_specs(project_root, python_bin, npm_bin)
    runtime_dir = project_root / ".nbs_runtime"
    check = preflight(project_root, specs, runtime_dir)
    if not check["ok"]:
        for issue in check["issues"]:
            print(f"[ERROR] {issue}")
        return 1

    state = read_state(runtime_dir)
    services = state.setdefault("services", {})
    started_names: list[str] = []
    for name, spec in specs.items():
        existing = services.get(name) or {}
        if process_is_alive(existing.get("pid")) and endpoint_is_ready(spec["ready_url"]):
            print(f"[READY] {name}: {spec['ready_url']}")
            continue
        reusable_pid = find_reusable_service_pid(name, spec, project_root)
        if reusable_pid and endpoint_is_ready(spec["ready_url"]):
            services[name] = _adopt_service_record(reusable_pid, spec, name, runtime_dir)
            write_state(state, runtime_dir)
            print(f"[READY] {name}: {spec['ready_url']} (adopted pid={reusable_pid})")
            continue
        services[name] = _spawn_service(name, spec, runtime_dir)
        started_names.append(name)
        write_state(state, runtime_dir)

    for name, spec in specs.items():
        record = services[name]
        if not wait_for_ready(spec, record["pid"]):
            print(f"[ERROR] {name} did not become ready. Log: {record['logPath']}")
            stop_services(project_root)
            return 1
        print(f"[READY] {name}: {spec['ready_url']}")

    state["status"] = "ready"
    write_state(state, runtime_dir)
    print("NBS services are ready:")
    print("  Streamlit: http://127.0.0.1:8502/")
    print("  Vue:       http://127.0.0.1:5173/")
    print("  API docs:  http://127.0.0.1:8601/docs")
    if open_browser:
        webbrowser.open("http://127.0.0.1:8502/")
    return 0


def _terminate_pid(pid: int) -> None:
    if not process_is_alive(pid):
        return
    try:
        if os.name == "nt":
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and process_is_alive(pid):
        time.sleep(0.2)
    if process_is_alive(pid):
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
            else:
                os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass


def stop_services(project_root: Path = PROJECT_ROOT) -> int:
    runtime_dir = project_root / ".nbs_runtime"
    state = read_state(runtime_dir)
    for name, record in (state.get("services") or {}).items():
        pid = record.get("pid")
        if process_is_alive(pid):
            print(f"[STOP] {name} pid={pid}")
            _terminate_pid(int(pid))
    write_state({"status": "stopped", "services": {}}, runtime_dir)
    return 0


def service_status(project_root: Path = PROJECT_ROOT, *, profile=None) -> dict:
    python_bin, npm_bin = _resolve_runtime(project_root)
    profile_ports = dict(profile.services.ports) if profile is not None else None
    profile_id = profile.profile_id if profile is not None else None
    specs = build_service_specs(project_root, python_bin, npm_bin, ports=profile_ports, profile_id=profile_id)
    runtime_dir = (
        project_root / ".nbs_agent_runtime" / "verification" / profile.profile_id
        if profile is not None
        else project_root / ".nbs_runtime"
    )
    state = read_state(runtime_dir)
    services = {}
    for name, spec in specs.items():
        record = (state.get("services") or {}).get(name) or {}
        alive = process_is_alive(record.get("pid"))
        ready = endpoint_is_ready(spec["ready_url"])
        owner_match = bool(
            alive
            and _command_matches_service(
                name,
                _process_command(record.get("pid")),
                spec,
                project_root,
                process_cwd=_process_cwd(record.get("pid")),
            )
        )
        identity_match = bool(owner_match and (profile is None or record.get("profileId") == profile.profile_id))
        services[name] = {
            "pid": record.get("pid"),
            "alive": alive,
            "ready": ready,
            "ownerMatch": owner_match,
            "identityMatch": identity_match,
            "failureReason": None if alive and ready and owner_match and identity_match else "service_identity_unavailable",
            "url": spec["ready_url"],
            "logPath": record.get("logPath"),
        }
    return {
        "status": "ready" if all(item["alive"] and item["ready"] and item["ownerMatch"] and item["identityMatch"] for item in services.values()) else "not_ready",
        "services": services,
    }


def run_http_acceptance(project_root: Path = PROJECT_ROOT, *, profile=None) -> dict:
    current = service_status(project_root, profile=profile)
    checks = current["services"]
    return {
        "status": "passed" if all(item.get("alive") and item.get("ready") and item.get("ownerMatch") and item.get("identityMatch") for item in checks.values()) else "failed",
        "checks": checks,
    }


def _build_health(project_root: Path) -> dict:
    from backend.services.system_health_service import build_system_health

    return build_system_health(
        db_path=project_root / "nbs_marketing_data.db",
        cache_path=project_root / ".nbs_runtime_cache",
        runtime_dir=project_root / ".nbs_runtime",
    )


def monitor_services(project_root: Path = PROJECT_ROOT) -> dict:
    from backend.services.operational_monitor_service import append_health_snapshot

    python_bin, npm_bin = _resolve_runtime(project_root)
    specs = build_service_specs(project_root, python_bin, npm_bin)
    endpoints = {
        name: {
            "ready": endpoint_is_ready(spec["ready_url"]),
            "url": spec["ready_url"],
        }
        for name, spec in specs.items()
    }
    return append_health_snapshot(
        _build_health(project_root),
        history_path=project_root / ".nbs_runtime" / "health_history.jsonl",
        endpoint_probes=endpoints,
    )


def run_retention(project_root: Path = PROJECT_ROOT, apply: bool = False) -> dict:
    from backend.services.backup_retention_service import (
        apply_backup_retention,
        plan_backup_retention,
    )
    from backend.services.stability_history_service import list_stability_history

    try:
        protected = {
            str(record["backupPath"])
            for record in list_stability_history(limit=100)
            if record.get("backupPath")
        }
    except Exception:
        protected = set()
    plan = plan_backup_retention(
        db_path=project_root / "nbs_marketing_data.db",
        protected_paths=protected,
    )
    plan["mode"] = "apply" if apply else "dry-run"
    if apply:
        plan["application"] = apply_backup_retention(plan)
    report_path = project_root / ".nbs_runtime" / "retention_latest.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def run_restore_drill_command(project_root: Path = PROJECT_ROOT, backup_path: str | None = None) -> dict:
    from backend.services.restore_drill_service import run_restore_drill
    from scripts.phase2j_baseline_check import check_phase2_baseline

    return run_restore_drill(
        live_db_path=project_root / "nbs_marketing_data.db",
        backup_path=Path(backup_path) if backup_path else None,
        report_path=project_root / ".nbs_runtime" / "restore_drill_latest.json",
        baseline_check=check_phase2_baseline,
    )


def create_diagnostics(project_root: Path = PROJECT_ROOT) -> Path:
    from backend.services.diagnostics_service import create_diagnostic_package

    return create_diagnostic_package(
        project_root=project_root,
        runtime_dir=project_root / ".nbs_runtime",
        status_payload=service_status(project_root),
        health_payload=_build_health(project_root),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage NBS Analytics local services.")
    parser.add_argument(
        "action",
        choices=[
            "start",
            "status",
            "stop",
            "monitor",
            "retention",
            "drill",
            "diagnose",
            "acceptance",
        ],
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Apply backup retention deletions.")
    parser.add_argument("--backup", help="Specific backup path for restore drill.")
    parser.add_argument("--verification-profile", help="Read-only verification profile path.")
    return parser


def _load_verification_profile(value: str | None):
    if not value:
        return None
    from backend.services.verification_runtime_paths import load_verification_runtime_profile
    profile_path = Path(value)
    if not profile_path.is_absolute():
        profile_path = PROJECT_ROOT / profile_path
    profile, _ = load_verification_runtime_profile(profile_path, project_root=PROJECT_ROOT)
    return profile


def main() -> int:
    args = build_parser().parse_args()
    profile = _load_verification_profile(args.verification_profile)
    if args.action == "start":
        return start_services(open_browser=not args.no_browser)
    if args.action == "stop":
        return stop_services()
    if args.action == "status":
        result = service_status(profile=profile)
    elif args.action == "monitor":
        result = monitor_services()
    elif args.action == "retention":
        result = run_retention(apply=args.apply)
    elif args.action == "drill":
        result = run_restore_drill_command(backup_path=args.backup)
    elif args.action == "diagnose":
        output = create_diagnostics()
        result = {"status": "created", "path": str(output)}
    else:
        result = run_http_acceptance(profile=profile)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") not in {"failed", "critical"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
