from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_start_launchers_use_common_system_manager():
    mac = (ROOT / "啟動NBS系統_mac.command").read_text(encoding="utf-8")
    windows = (ROOT / "啟動NBS系統_windows.bat").read_text(encoding="utf-8")

    assert "scripts/system_manager.py" in mac
    assert " start" in mac
    assert "scripts\\system_manager.py" in windows
    assert " start" in windows


def test_stop_launchers_use_common_system_manager():
    mac = (ROOT / "停止NBS系統_mac.command").read_text(encoding="utf-8")
    windows = (ROOT / "停止NBS系統_windows.bat").read_text(encoding="utf-8")

    assert "scripts/system_manager.py" in mac
    assert " stop" in mac
    assert "scripts\\system_manager.py" in windows
    assert " stop" in windows
