from __future__ import annotations

import re
import subprocess
from pathlib import Path
from time import perf_counter

from backend.agents.implementation_models import ValidationResult
from backend.agents.evidence_models import load_json_config


_OUTPUT_CAP = 32_000
_UNSAFE_ARGUMENT = re.compile(r"[;&|$`()<>\n\r\t ]")
_MAXFAIL = re.compile(r"--maxfail=([0-9]+)$")
_PYTEST_OPTIONS = {"-q", "-v", "-x"}


class CommandRejected(ValueError):
    """Raised when a validation command is outside the approved contract."""


class ValidationRunner:
    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        config = load_json_config(self.project_root, "agent_config/implementation_commands.json")
        self.commands = config.get("commands", {})

    def run(self, command_id: str, arguments: tuple[str, ...]) -> ValidationResult:
        config = self.commands.get(command_id)
        if not isinstance(config, dict):
            raise CommandRejected(f"command {command_id!r} is not allowlisted")
        if not isinstance(arguments, tuple) or not all(isinstance(item, str) for item in arguments):
            raise CommandRejected("arguments must be a tuple of strings")

        argv = self._build_argv(command_id, config, arguments)
        timeout = config.get("timeoutSeconds")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise CommandRejected(f"command {command_id!r} has an invalid timeout")
        cwd = self.project_root / config.get("cwd", ".")
        if not self._is_under(cwd, self.project_root):
            raise CommandRejected("command working directory must stay under project root")

        started = perf_counter()
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                shell=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return ValidationResult(
                command_id=command_id,
                argv=tuple(argv),
                exit_code=124,
                stdout=self._cap_output(exc.stdout),
                stderr=self._cap_output(exc.stderr),
                duration_ms=self._duration_ms(started),
                timed_out=True,
            )

        return ValidationResult(
            command_id=command_id,
            argv=tuple(argv),
            exit_code=completed.returncode,
            stdout=self._cap_output(completed.stdout),
            stderr=self._cap_output(completed.stderr),
            duration_ms=self._duration_ms(started),
        )

    def _build_argv(
        self, command_id: str, config: dict, arguments: tuple[str, ...]
    ) -> list[str]:
        if "exact" in config:
            if arguments:
                raise CommandRejected(f"command {command_id!r} does not accept arguments")
            exact = config["exact"]
            if not isinstance(exact, list) or not all(isinstance(item, str) for item in exact):
                raise CommandRejected(f"command {command_id!r} has invalid argv")
            return list(exact)

        prefix = config.get("prefix")
        if not isinstance(prefix, list) or not all(isinstance(item, str) for item in prefix):
            raise CommandRejected(f"command {command_id!r} has invalid argv")
        if command_id == "pytest_targeted":
            self._validate_pytest_arguments(arguments)
        elif command_id == "py_compile":
            self._validate_py_compile_arguments(arguments)
        else:
            raise CommandRejected(f"command {command_id!r} is not supported")
        return [*prefix, *arguments]

    def _validate_pytest_arguments(self, arguments: tuple[str, ...]) -> None:
        targets = []
        for argument in arguments:
            self._reject_unsafe(argument)
            if argument in _PYTEST_OPTIONS or _MAXFAIL.fullmatch(argument):
                continue
            if argument.startswith("-"):
                raise CommandRejected(f"pytest option is not allowlisted: {argument}")
            targets.append(argument)
        if not targets:
            raise CommandRejected("pytest_targeted requires a target under tests/")
        for target in targets:
            self._validate_relative_path(target, self.project_root / "tests", suffix=None)

    def _validate_py_compile_arguments(self, arguments: tuple[str, ...]) -> None:
        if not arguments:
            raise CommandRejected("py_compile requires a Python file")
        for argument in arguments:
            self._reject_unsafe(argument)
            if argument.startswith("-"):
                raise CommandRejected(f"py_compile option is not allowed: {argument}")
            self._validate_relative_path(argument, self.project_root, suffix=".py")

    @staticmethod
    def _reject_unsafe(argument: str) -> None:
        if _UNSAFE_ARGUMENT.search(argument):
            raise CommandRejected(f"argument contains unsafe shell characters: {argument!r}")

    def _validate_relative_path(
        self, raw_path: str, allowed_root: Path, *, suffix: str | None
    ) -> None:
        candidate = Path(raw_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise CommandRejected(f"path is outside the approved root: {raw_path}")
        resolved = (self.project_root / candidate).resolve()
        if not self._is_under(resolved, allowed_root.resolve()):
            raise CommandRejected(f"path is outside the approved root: {raw_path}")
        if suffix is not None and resolved.suffix != suffix:
            raise CommandRejected(f"path must end with {suffix}: {raw_path}")

    @staticmethod
    def _is_under(candidate: Path, root: Path) -> bool:
        try:
            candidate.resolve().relative_to(root.resolve())
        except ValueError:
            return False
        return True

    @staticmethod
    def _cap_output(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        return value[:_OUTPUT_CAP]

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, int((perf_counter() - started) * 1000))
