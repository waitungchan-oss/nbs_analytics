from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Protocol


_OSASCRIPT = "/usr/bin/osascript"
_TITLE_LIMIT = 80
_MESSAGE_LIMIT = 240
_ABSOLUTE_PATH = re.compile(r"(?<![\w])/(?:[^/\s]+/)*[^/\s]+")


@dataclass(frozen=True)
class NotificationResult:
    delivered: bool
    warning: str | None = None


class WorkflowNotifier(Protocol):
    def send(self, title: str, message: str) -> NotificationResult:
        ...


def _sanitize(value: str, limit: int) -> str:
    sanitized = str(value)
    for environment_value in sorted(os.environ.values(), key=len, reverse=True):
        if environment_value:
            sanitized = sanitized.replace(environment_value, "[redacted]")
    sanitized = _ABSOLUTE_PATH.sub("[path]", sanitized)
    sanitized = "".join(character if character >= " " else " " for character in sanitized)
    return sanitized[:limit]


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class MacOSWorkflowNotifier:
    def send(self, title: str, message: str) -> NotificationResult:
        if sys.platform != "darwin":
            return NotificationResult(False, "workflow notification unavailable on non-macOS")

        safe_title = _escape_applescript(_sanitize(title, _TITLE_LIMIT))
        safe_message = _escape_applescript(_sanitize(message, _MESSAGE_LIMIT))
        script = f'display notification "{safe_message}" with title "{safe_title}"'
        try:
            subprocess.run(
                [_OSASCRIPT, "-e", script],
                check=True,
                shell=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return NotificationResult(False, "workflow notification failed")
        return NotificationResult(True)


@dataclass(frozen=True)
class NoOpWorkflowNotifier:
    warning: str | None = None

    def send(self, title: str, message: str) -> NotificationResult:
        return NotificationResult(False, self.warning)


def build_notifier(enabled: bool = True) -> WorkflowNotifier:
    if not enabled:
        return NoOpWorkflowNotifier()
    if sys.platform != "darwin":
        return NoOpWorkflowNotifier("workflow notification unavailable on non-macOS")
    return MacOSWorkflowNotifier()
