from __future__ import annotations

import subprocess

import pytest

from backend.agents import workflow_notifications
from backend.agents.workflow_notifications import (
    MacOSWorkflowNotifier,
    NoOpWorkflowNotifier,
    NotificationResult,
    build_notifier,
)


def completed() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, "", "")


def test_macos_notifier_never_uses_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(workflow_notifications.sys, "platform", "darwin")
    monkeypatch.setattr(
        workflow_notifications.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or completed(),
    )

    result = MacOSWorkflowNotifier().send("Awaiting authorization", "run abc123")

    assert calls[0][0][0][0] == "/usr/bin/osascript"
    assert calls[0][1]["shell"] is False
    assert result == NotificationResult(delivered=True)


def test_macos_notifier_redacts_sensitive_values_and_caps_script_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(workflow_notifications.sys, "platform", "darwin")
    monkeypatch.setattr(workflow_notifications.os, "environ", {"TOKEN": "secret-token"})
    monkeypatch.setattr(
        workflow_notifications.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args[0], kwargs)) or completed(),
    )

    title = "x" * 200
    message = "y" * 400
    result = MacOSWorkflowNotifier().send(title, message)

    script = calls[0][0][2]
    assert result.delivered is True
    message_payload, title_payload = script.split('"')[1], script.rsplit('"', 2)[1]
    assert len(title_payload) == 80
    assert len(message_payload) == 240


def test_macos_notifier_removes_controls_paths_and_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(workflow_notifications.sys, "platform", "darwin")
    monkeypatch.setattr(workflow_notifications.os, "environ", {"TOKEN": "secret-token"})
    monkeypatch.setattr(
        workflow_notifications.subprocess,
        "run",
        lambda *args, **kwargs: calls.append(args[0]) or completed(),
    )

    result = MacOSWorkflowNotifier().send(
        "title\n\x00",
        "secret-token /Users/chanwaitung2025/private.txt\tmessage",
    )

    script = calls[0][2]
    assert result.delivered is True
    assert "secret-token" not in script
    assert "/Users/chanwaitung2025/private.txt" not in script
    assert "\n" not in script
    assert "\x00" not in script


def test_macos_notifier_returns_warning_when_command_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workflow_notifications.sys, "platform", "darwin")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("osascript unavailable")

    monkeypatch.setattr(workflow_notifications.subprocess, "run", fail)

    result = MacOSWorkflowNotifier().send("title", "message")

    assert result.delivered is False
    assert result.warning is not None
    assert "notification" in result.warning.lower()


def test_non_macos_builds_noop_notifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow_notifications.sys, "platform", "linux")

    notifier = build_notifier()
    result = notifier.send("title", "message")

    assert isinstance(notifier, NoOpWorkflowNotifier)
    assert result.delivered is False
    assert result.warning is not None


def test_disabled_builds_silent_noop_notifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow_notifications.sys, "platform", "darwin")

    notifier = build_notifier(enabled=False)

    assert isinstance(notifier, NoOpWorkflowNotifier)
    assert notifier.send("title", "message") == NotificationResult(delivered=False)
