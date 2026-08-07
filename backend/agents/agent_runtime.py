from __future__ import annotations

import json
import fcntl
import hashlib
from contextlib import contextmanager
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Callable, Protocol
from uuid import uuid4

from backend.agents.evidence_models import (
    ALLOWED_CONTEXT_STATUSES,
    ALLOWED_REVIEW_STATUSES,
    EvidenceBundle,
    canonical_fingerprint,
    estimate_tokens,
)


DEFAULT_INPUT_TOKEN_LIMIT = 12_000
DEFAULT_OUTPUT_TOKEN_LIMIT = 1_500
_SAFE_AGENT_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_ALLOWED_TELEMETRY_RESULTS = ALLOWED_CONTEXT_STATUSES | ALLOWED_REVIEW_STATUSES
_TELEMETRY_MAX_BYTES = 1024 * 1024
_TELEMETRY_MAX_LINE_BYTES = 4096


class AgentRunner(Protocol):
    def run(self, payload: dict) -> dict: ...


def resolve_runtime_output_path(project_root: Path, raw_path: str) -> Path:
    project_lexical = Path(os.path.abspath(os.fspath(project_root)))
    root_lexical = project_lexical / ".nbs_agent_runtime"
    candidate = Path(raw_path)
    candidate_lexical = Path(os.path.abspath(
        os.fspath(project_lexical / candidate if not candidate.is_absolute() else candidate)
    ))
    try:
        relative = candidate_lexical.relative_to(root_lexical)
    except ValueError as exc:
        raise PermissionError(f"Agent output must stay under {root_lexical}") from exc
    if relative == Path("."):
        raise PermissionError(f"Agent output must be a file below {root_lexical}")
    current = root_lexical
    for part in relative.parts[:-1]:
        if current.is_symlink():
            raise PermissionError(f"Agent output parent cannot be a symlink: {current}")
        current = current / part
    if current.is_symlink():
        raise PermissionError(f"Agent output parent cannot be a symlink: {current}")
    root = root_lexical.resolve()
    resolved = candidate_lexical.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"Agent output must stay under {root_lexical}") from exc
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def resolve_implementation_runtime_path(project_root: Path, raw_path: str) -> Path:
    project_lexical = Path(os.path.abspath(os.fspath(project_root)))
    runtime_root = project_lexical / ".nbs_agent_runtime"
    implementation_root = runtime_root / "implementation"
    candidate = Path(raw_path)
    raw_candidate = implementation_root / candidate if not candidate.is_absolute() else candidate
    candidate_lexical = Path(os.path.abspath(os.fspath(raw_candidate)))
    try:
        relative = candidate_lexical.relative_to(implementation_root)
    except ValueError as exc:
        raise PermissionError(
            f"Implementation runtime output must stay under {implementation_root}"
        ) from exc
    if relative == Path("."):
        raise PermissionError("Implementation runtime output must be a file below implementation")
    current = runtime_root
    for part in ("implementation", *relative.parts[:-1]):
        if current.is_symlink():
            raise PermissionError(f"Implementation runtime parent cannot be a symlink: {current}")
        current = current / part
    if current.is_symlink():
        raise PermissionError(f"Implementation runtime parent cannot be a symlink: {current}")
    resolved = candidate_lexical.resolve()
    try:
        resolved.relative_to(implementation_root.resolve())
    except ValueError as exc:
        raise PermissionError(
            f"Implementation runtime output must stay under {implementation_root}"
        ) from exc
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def agent_request_fingerprint(
    bundle: EvidenceBundle,
    instructions: str,
    output_schema: str,
    evidence_payload: dict | None = None,
) -> str:
    public_evidence = bundle.to_dict() if evidence_payload is None else evidence_payload
    return canonical_fingerprint(
        {
            "sourceBundleFingerprint": bundle.fingerprint,
            "publicEvidence": public_evidence,
            "instructions": instructions,
            "outputSchema": output_schema,
        }
    )


def _resolve_executable(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        if not path.is_file():
            raise FileNotFoundError(value)
        return path.resolve()
    found = shutil.which(value)
    if not found:
        raise FileNotFoundError(value)
    return Path(found).resolve()


class SubprocessAgentRunner:
    def __init__(
        self,
        argv: list[str],
        allowed_executables: tuple[str, ...],
        timeout_seconds: int = 120,
    ) -> None:
        if not argv:
            raise ValueError("Agent command cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("Agent timeout must be positive")

        executable = _resolve_executable(argv[0])
        allowed: set[Path] = set()
        for value in allowed_executables:
            try:
                allowed.add(_resolve_executable(value))
            except FileNotFoundError:
                continue
        if executable not in allowed:
            raise PermissionError(f"Agent executable is not allowlisted: {executable}")
        self.argv = (str(executable), *argv[1:])
        self.timeout_seconds = timeout_seconds

    def run(self, payload: dict) -> dict:
        completed = subprocess.run(
            list(self.argv),
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Agent command failed with exit {completed.returncode}: {completed.stderr[:1000]}"
            )
        try:
            result = _decode_json_or_codex_event_stream(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("Agent output is not valid JSON") from exc
        if not isinstance(result, dict):
            raise ValueError("Agent output must be a JSON object")
        return result


def _decode_json_or_codex_event_stream(output: str) -> dict:
    """Accept plain JSON and the final agent_message from `codex exec --json`."""
    try:
        value = json.loads(output)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    for line in reversed(output.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if event.get("type") == "item.completed" and isinstance(item, dict):
            text = item.get("text")
            if item.get("type") == "agent_message" and isinstance(text, str):
                value = json.loads(text)
                if isinstance(value, dict):
                    return value
    raise json.JSONDecodeError("Agent output is not valid JSON", output, 0)


class SandboxedSubprocessAgentRunner(SubprocessAgentRunner):
    """Run a production coding worker in a disposable tracked-files staging copy."""

    def __init__(
        self,
        argv: list[str],
        allowed_executables: tuple[str, ...],
        *,
        project_root: Path,
        allowed_write_paths: tuple[str, ...],
        timeout_seconds: int = 120,
        sandbox_backend: str = "/usr/bin/sandbox-exec",
    ) -> None:
        if sys.platform != "darwin":
            raise PermissionError("implementation sandbox backend is unsupported on this platform")
        try:
            backend = _resolve_executable(sandbox_backend)
        except FileNotFoundError as exc:
            raise PermissionError("implementation sandbox backend is unavailable") from exc
        if backend != Path("/usr/bin/sandbox-exec").resolve():
            raise PermissionError("implementation sandbox backend must be /usr/bin/sandbox-exec")
        super().__init__(
            argv,
            allowed_executables=allowed_executables,
            timeout_seconds=timeout_seconds,
        )
        self.project_root = project_root.resolve(strict=True)
        if not self.project_root.is_dir():
            raise PermissionError("implementation sandbox worktree must be a directory")
        root_info = self.project_root.stat()
        self.project_root_identity = (root_info.st_dev, root_info.st_ino)
        self.allowed_write_paths = self._validate_write_paths(allowed_write_paths)
        self.sandbox_backend = str(backend)

    def _validate_write_paths(self, raw_paths: tuple[str, ...]) -> tuple[Path, ...]:
        if not raw_paths:
            raise PermissionError("implementation sandbox requires approved write targets")
        paths: set[Path] = set()
        for raw in raw_paths:
            relative = Path(raw)
            if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
                raise PermissionError(f"implementation sandbox write path is invalid: {raw}")
            if self._is_sensitive_path(relative):
                raise PermissionError(f"implementation sandbox write path is protected: {raw}")
            current = self.project_root
            for part in relative.parts[:-1]:
                current = current / part
                if not current.exists():
                    break
                if current.is_symlink():
                    raise PermissionError(
                        f"implementation sandbox write parent cannot be a symlink: {raw}"
                    )
                if not current.is_dir():
                    raise PermissionError(
                        f"implementation sandbox write parent must already exist: {raw}"
                    )
            lexical_target = self.project_root / relative
            if lexical_target.is_symlink():
                raise PermissionError(
                    f"implementation sandbox write target cannot be a symlink: {raw}"
                )
            if lexical_target.exists() and not lexical_target.is_file():
                raise PermissionError(
                    f"implementation sandbox write target must be a file: {raw}"
                )
            paths.add(relative)
        return tuple(sorted(paths, key=lambda value: value.as_posix()))

    @staticmethod
    def _is_sensitive_path(relative: Path) -> bool:
        parts = {part.lower() for part in relative.parts}
        protected_parts = {
            ".git", ".nbs_runtime", ".nbs_agent_runtime", "secrets",
            ".ssh", ".aws", "credentials",
        }
        if protected_parts.intersection(parts):
            return True
        name = relative.name.lower()
        if name == ".env" or name.startswith(".env."):
            return True
        if name in {
            ".npmrc", ".netrc", ".pypirc", "credentials.json", "token.json",
            "auth.json", "id_rsa", "id_ed25519",
        }:
            return True
        if name.endswith(".json") and name.startswith(
            ("client_secret", "service_account", "oauth_credentials")
        ):
            return True
        return relative.suffix.lower() in {
            ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx",
        }

    def _tracked_files(self) -> tuple[Path, ...]:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--cached"],
            cwd=self.project_root,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise PermissionError("implementation staging requires a readable Git index")
        paths: list[Path] = []
        for raw in completed.stdout.split(b"\0"):
            if not raw:
                continue
            relative = Path(os.fsdecode(raw))
            if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
                raise PermissionError("Git index contains an unsafe staging path")
            if self._is_sensitive_path(relative):
                continue
            paths.append(relative)
        return tuple(paths)

    def _create_staging_copy(self, staging: Path) -> None:
        tracked = self._tracked_files()
        if tracked:
            completed = subprocess.run(
                [
                    "git", "checkout-index", "-z", "--stdin",
                    f"--prefix={os.fspath(staging)}{os.sep}",
                ],
                cwd=self.project_root,
                input=b"\0".join(os.fsencode(path) for path in tracked) + b"\0",
                capture_output=True,
                check=False,
            )
            if completed.returncode != 0:
                raise PermissionError("implementation staging could not materialize Git index files")
        for relative in tracked:
            target = staging / relative
            if target.is_symlink() or not target.is_file():
                raise PermissionError(f"tracked staging source must be a regular file: {relative}")
            if target.stat().st_nlink != 1:
                raise PermissionError(f"staging copy unexpectedly shares an inode: {relative}")
        for relative in self.allowed_write_paths:
            parent = staging / relative.parent
            parent.mkdir(parents=True, exist_ok=True)
            if parent.is_symlink():
                raise PermissionError(f"staging write parent cannot be a symlink: {relative}")
        self._overlay_actual_allowed_targets(staging)

    def _read_actual_allowed_target(self, relative: Path) -> tuple[bytes, int] | None:
        try:
            parent_fd = self._open_actual_parent(relative, create_missing=False)
        except FileNotFoundError:
            return None
        try:
            try:
                descriptor = os.open(
                    relative.name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise PermissionError(
                    f"actual implementation target cannot be a symlink: {relative}"
                ) from exc
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise PermissionError(
                        f"actual implementation target is not a regular file: {relative}"
                    )
                with os.fdopen(descriptor, "rb", closefd=False) as handle:
                    content = handle.read()
            finally:
                os.close(descriptor)
            return content, stat.S_IMODE(info.st_mode)
        finally:
            os.close(parent_fd)

    @staticmethod
    def _write_private_staging_target(path: Path, content: bytes, mode: int) -> None:
        if path.is_symlink():
            raise PermissionError(f"staging overlay target cannot be a symlink: {path}")
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError(f"staging overlay target must be a private file: {path}")
            path.unlink()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.fchmod(descriptor, mode)
        finally:
            os.close(descriptor)

    def _overlay_actual_allowed_targets(self, staging: Path) -> None:
        for relative in self.allowed_write_paths:
            staged = staging / relative
            actual = self._read_actual_allowed_target(relative)
            if actual is None:
                if staged.is_symlink() or (staged.exists() and not staged.is_file()):
                    raise PermissionError(
                        f"staging overlay target is not a regular file: {relative}"
                    )
                if staged.exists():
                    staged.unlink()
                continue
            content, mode = actual
            self._write_private_staging_target(staged, content, mode)

    @staticmethod
    def _read_private_file(path: Path, relative: str) -> tuple[bytes, int]:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError(f"staging file must be a private regular file: {relative}")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                content = handle.read()
        finally:
            os.close(descriptor)
        return content, stat.S_IMODE(info.st_mode)

    @staticmethod
    def _snapshot(root: Path) -> dict[str, tuple[str, int]]:
        snapshot: dict[str, tuple[str, int]] = {}
        for directory, names, files in os.walk(root, followlinks=False):
            base = Path(directory)
            for name in names:
                path = base / name
                if path.is_symlink():
                    raise PermissionError(f"staging directory cannot contain symlinks: {path}")
            for name in files:
                path = base / name
                relative = path.relative_to(root).as_posix()
                content, mode = SandboxedSubprocessAgentRunner._read_private_file(path, relative)
                snapshot[relative] = (
                    hashlib.sha256(content).hexdigest(),
                    mode,
                )
        return snapshot

    def _staged_changes(
        self,
        staging: Path,
        before: dict[str, tuple[str, int]],
    ) -> dict[Path, bytes | None]:
        after = self._snapshot(staging)
        changed = {
            Path(path) for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        }
        unexpected = changed - set(self.allowed_write_paths)
        if unexpected:
            rendered = ", ".join(sorted(path.as_posix() for path in unexpected))
            raise PermissionError(f"staging worker changed unapproved paths: {rendered}")
        changes: dict[Path, bytes | None] = {}
        for relative in sorted(changed, key=lambda value: value.as_posix()):
            staged = staging / relative
            changes[relative] = (
                self._read_private_file(staged, relative.as_posix())[0]
                if staged.exists() else None
            )
        return changes

    @staticmethod
    def _runtime_read_roots(executable: Path) -> tuple[Path, ...]:
        roots = {
            Path("/System/Library"),
            Path("/usr/lib"),
            Path("/usr/share"),
            Path("/private/var/db/dyld"),
        }
        if executable.name.startswith("python"):
            roots.add(executable.parent.parent)
        else:
            roots.add(executable.parent)
        return tuple(sorted((path.resolve() for path in roots if path.exists()), key=os.fspath))

    def _build_profile(self, staging: Path, targets: tuple[Path, ...]) -> str:
        rules = [
            "(version 1)",
            "(deny default)",
            "(deny file-link)",
            "(allow process*)",
            "(deny process-fork)",
            "(allow sysctl*)",
            "(allow mach*)",
            '(allow file-read* (literal "/"))',
            f"(allow file-read* (subpath {json.dumps(os.fspath(staging))}))",
            f"(allow file-read* (literal {json.dumps(self.argv[0])}))",
            '(allow file-read* (literal "/dev/null"))',
            '(allow file-read* (literal "/dev/urandom"))',
        ]
        rules.extend(
            f"(allow file-read* (subpath {json.dumps(os.fspath(root))}))"
            for root in self._runtime_read_roots(Path(self.argv[0]))
        )
        rules.extend(
            f"(allow file-write* (literal {json.dumps(os.fspath(target))}))"
            for target in targets
        )
        return "\n".join(rules)

    def _staged_argv(self, staging: Path) -> list[str]:
        argv = [self.argv[0]]
        for raw in self.argv[1:]:
            candidate = Path(raw)
            if candidate.is_absolute():
                try:
                    relative = candidate.resolve().relative_to(self.project_root)
                except ValueError:
                    argv.append(raw)
                else:
                    argv.append(os.fspath(staging / relative))
            else:
                argv.append(raw)
        return argv

    def _staged_payload(self, payload: dict, staging: Path) -> dict:
        actual = os.fspath(self.project_root)
        replacement = os.fspath(staging)

        def rewrite(value):
            if isinstance(value, dict):
                return {key: rewrite(item) for key, item in value.items()}
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            if isinstance(value, str) and (value == actual or value.startswith(actual + os.sep)):
                return replacement + value[len(actual):]
            return value

        staged = rewrite(deepcopy(payload))
        task = staged.get("task")
        if isinstance(task, dict):
            task["approvedWorktree"] = replacement
        staged["execution"] = {"mode": "disposable-staging", "worktree": replacement}
        return staged

    @staticmethod
    def _terminate_process_group(process_group: int) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process_group, sig)
            except ProcessLookupError:
                return
            time.sleep(0.05)

    @staticmethod
    def _validate_response(stdout: str) -> dict:
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("Agent output is not valid JSON") from exc
        required = {
            "schemaVersion", "status", "summary", "requestedValidationCommandIds",
        }
        if not isinstance(result, dict) or set(result) != required:
            raise ValueError("Agent output is not a valid implementation response")
        if result.get("schemaVersion") != "implementation-response-v1":
            raise ValueError("Agent output implementation schema is invalid")
        if result.get("status") not in {"completed", "needs_repair"}:
            raise ValueError("Agent output implementation status is invalid")
        if not isinstance(result.get("summary"), str) or not result["summary"].strip():
            raise ValueError("Agent output implementation summary is invalid")
        commands = result.get("requestedValidationCommandIds")
        if not isinstance(commands, list) or not all(isinstance(item, str) for item in commands):
            raise ValueError("Agent output validation commands are invalid")
        return result

    def _open_actual_parent(self, relative: Path, *, create_missing: bool = True) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.project_root, flags)
        try:
            root_info = os.fstat(descriptor)
            if (root_info.st_dev, root_info.st_ino) != self.project_root_identity:
                raise PermissionError("implementation worktree root changed during execution")
            for part in relative.parent.parts:
                try:
                    child = os.open(part, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create_missing:
                        raise
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                    child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _atomic_replace(parent_fd: int, name: str, content: bytes | None, mode: int) -> None:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info is not None and not stat.S_ISREG(info.st_mode):
            raise PermissionError(f"actual implementation target is not a regular file: {name}")
        if content is None:
            if info is not None:
                os.unlink(name, dir_fd=parent_fd)
            return
        temporary = f".nbs-agent-{uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.fchmod(descriptor, mode)
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except Exception:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            raise

    def _apply_staged_changes(self, staging: Path, changes: dict[Path, bytes | None]) -> None:
        prepared: list[tuple[Path, int, bytes | None, int]] = []
        try:
            for relative, content in changes.items():
                parent_fd = self._open_actual_parent(relative)
                mode = 0o644
                staged = staging / relative
                if staged.exists():
                    info = staged.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        os.close(parent_fd)
                        raise PermissionError(
                            f"staged implementation target is not a private regular file: {relative}"
                        )
                    mode = stat.S_IMODE(info.st_mode)
                prepared.append((relative, parent_fd, content, mode))
            for relative, parent_fd, content, mode in prepared:
                self._atomic_replace(parent_fd, relative.name, content, mode)
        finally:
            for _, parent_fd, _, _ in prepared:
                os.close(parent_fd)
        self._verify_actual_changes(changes)

    def _verify_actual_changes(self, changes: dict[Path, bytes | None]) -> None:
        for relative, expected in changes.items():
            parent_fd = self._open_actual_parent(relative)
            try:
                try:
                    descriptor = os.open(
                        relative.name,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd,
                    )
                except FileNotFoundError:
                    if expected is None:
                        continue
                    raise PermissionError(
                        f"actual implementation target disappeared after replace: {relative}"
                    )
                try:
                    info = os.fstat(descriptor)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise PermissionError(
                            f"actual implementation target is not a private regular file: {relative}"
                        )
                    with os.fdopen(descriptor, "rb", closefd=False) as handle:
                        actual = handle.read()
                finally:
                    os.close(descriptor)
                if expected is None or actual != expected:
                    raise PermissionError(
                        f"actual implementation target changed during atomic apply: {relative}"
                    )
            finally:
                os.close(parent_fd)

    def run(self, payload: dict) -> dict:
        with tempfile.TemporaryDirectory(prefix="nbs-implementation-staging-") as raw_staging:
            staging = Path(raw_staging).resolve()
            self._create_staging_copy(staging)
            before = self._snapshot(staging)
            targets = tuple(staging / relative for relative in self.allowed_write_paths)
            profile = self._build_profile(staging, targets)
            environment = {
                "HOME": os.fspath(staging),
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TMPDIR": os.fspath(staging),
            }
            process = subprocess.Popen(
                [self.sandbox_backend, "-p", profile, *self._staged_argv(staging)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=staging,
                env=environment,
                shell=False,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(
                    json.dumps(self._staged_payload(payload, staging), ensure_ascii=False),
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                self._terminate_process_group(process.pid)
                process.communicate()
                raise
            finally:
                self._terminate_process_group(process.pid)
            if process.returncode != 0:
                raise RuntimeError(
                    f"Agent command failed with exit {process.returncode}: {stderr[:1000]}"
                )
            result = self._validate_response(stdout)
            changes = self._staged_changes(staging, before)
            self._apply_staged_changes(staging, changes)
            return result


class AgentRuntime:
    def __init__(
        self,
        runtime_root: Path,
        input_token_limit: int | None = None,
        output_token_limit: int | None = None,
    ) -> None:
        self.runtime_root = runtime_root.resolve()
        if self.runtime_root.name != ".nbs_agent_runtime":
            raise PermissionError(
                f"Agent runtime root must be named .nbs_agent_runtime: {self.runtime_root}"
            )
        configured = self._load_configured_budgets()
        self.input_token_limit = input_token_limit or configured[0]
        self.output_token_limit = output_token_limit or configured[1]
        if self.input_token_limit <= 0 or self.output_token_limit <= 0:
            raise ValueError("Agent token budgets must be positive")

    def _load_configured_budgets(self) -> tuple[int, int]:
        config_path = self.runtime_root.parent / "agent_config" / "token_budgets.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            context = config["context"]
            return int(context["inputTokens"]), int(context["outputTokens"])
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return DEFAULT_INPUT_TOKEN_LIMIT, DEFAULT_OUTPUT_TOKEN_LIMIT

    def configured_budget(self, agent_name: str) -> tuple[int, int]:
        """Return a named agent budget without changing existing agent defaults."""
        if agent_name != "documentation":
            return self._load_configured_budgets()
        config_path = self.runtime_root.parent / "agent_config" / "token_budgets.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            return int(config["documentationInput"]), int(config["documentationOutput"])
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return 8_000, DEFAULT_OUTPUT_TOKEN_LIMIT

    def _paths(self, agent_name: str, fingerprint: str) -> tuple[Path, Path]:
        safe_name = _SAFE_AGENT_NAME.sub("-", agent_name).strip(".-") or "agent"
        report = self.runtime_root / "reports" / f"{safe_name}-{fingerprint}.json"
        telemetry = self.runtime_root / "telemetry" / "agent_runs.jsonl"
        report.parent.mkdir(parents=True, exist_ok=True)
        telemetry.parent.mkdir(parents=True, exist_ok=True)
        (self.runtime_root / "locks").mkdir(parents=True, exist_ok=True)
        return report, telemetry

    def _lock_path(self, fingerprint: str) -> Path:
        return self.runtime_root / "locks" / f"{fingerprint}.lock"

    @staticmethod
    @contextmanager
    def _locked(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield handle
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    @staticmethod
    def _write_json_atomic(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _schema_check(result: object, output_schema: str) -> dict:
        if not isinstance(result, dict):
            raise ValueError("Agent output must be a JSON object")
        if result.get("schemaVersion") != output_schema:
            raise ValueError(f"Unexpected agent schema: {result.get('schemaVersion')}")
        return result

    def _telemetry(
        self,
        path: Path,
        *,
        agent_name: str,
        bundle: EvidenceBundle,
        request_fingerprint: str,
        input_text: str,
        result: dict | None,
        cache_hit: bool,
        started: float,
    ) -> None:
        safe_agent_name = _SAFE_AGENT_NAME.sub("-", agent_name).strip(".-")[:64] or "agent"
        telemetry_result = "unknown"
        if result:
            for key in ("status", "verdict"):
                candidate_result = result.get(key)
                if isinstance(candidate_result, str) and candidate_result in _ALLOWED_TELEMETRY_RESULTS:
                    telemetry_result = candidate_result
                    break
        record = {
            "runId": uuid4().hex,
            "agent": safe_agent_name,
            "bundleFingerprint": bundle.fingerprint,
            "requestFingerprint": request_fingerprint,
            "inputCharacters": len(input_text),
            "estimatedInputTokens": estimate_tokens(input_text),
            "outputTokens": estimate_tokens(json.dumps(result, ensure_ascii=False)) if result else 0,
            "filesConsidered": len(bundle.evidence),
            "filesIncluded": len(bundle.evidence),
            "cacheHit": cache_hit,
            "durationMs": round((perf_counter() - started) * 1000, 3),
            "result": telemetry_result if result else "context_overflow",
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        self._append_bounded_telemetry(path, line)

    def append_memory_sidecar_telemetry(self, event: object) -> None:
        """Persist a bounded sidecar event beside existing ignored runtime telemetry."""
        from backend.agents.memory_sidecar_telemetry import MemorySidecarTelemetryEvent

        if not isinstance(event, MemorySidecarTelemetryEvent):
            raise ValueError("Memory sidecar telemetry event is invalid")
        record = event.to_dict()
        if set(record) != {
            "schemaVersion", "runId", "mode", "queryFingerprint", "status", "latencyMs",
            "hintCount", "inputBytes", "fallback", "redactionCount",
        }:
            raise ValueError("Memory sidecar telemetry schema is invalid")
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        self._append_bounded_telemetry(
            self.runtime_root / "telemetry" / "memory_sidecar.jsonl", line,
        )

    def _append_bounded_telemetry(self, path: Path, line: str) -> None:
        if len(line.encode("utf-8")) > _TELEMETRY_MAX_LINE_BYTES:
            raise ValueError("Agent telemetry record exceeds size limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.runtime_root / "locks" / "telemetry.lock"
        with self._locked(lock_path):
            if path.exists() and path.stat().st_size + len(line.encode("utf-8")) > _TELEMETRY_MAX_BYTES:
                rotated = path.with_name(f"{path.name}.1")
                if rotated.exists():
                    rotated.unlink()
                os.replace(path, rotated)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def run(
        self,
        agent_name: str,
        bundle: EvidenceBundle,
        runner: AgentRunner,
        output_schema: str,
        instructions: str,
        evidence_payload: dict | None = None,
        output_validator: Callable[[object], dict] | None = None,
    ) -> dict:
        public_evidence = bundle.to_dict() if evidence_payload is None else evidence_payload
        request_fingerprint = agent_request_fingerprint(
            bundle,
            instructions=instructions,
            output_schema=output_schema,
            evidence_payload=public_evidence,
        )
        payload = {
            "contractVersion": output_schema,
            "instructions": instructions,
            "evidence": public_evidence,
            "sourceBundleFingerprint": bundle.fingerprint,
            "bundleFingerprint": request_fingerprint,
        }
        input_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        report_path, telemetry_path = self._paths(agent_name, request_fingerprint)
        started = perf_counter()
        input_tokens = estimate_tokens(input_text)
        if input_tokens > self.input_token_limit:
            result = {
                "schemaVersion": output_schema,
                "status": "context_overflow",
                "requestFingerprint": request_fingerprint,
            }
            self._telemetry(
                telemetry_path,
                agent_name=agent_name,
                bundle=bundle,
                request_fingerprint=request_fingerprint,
                input_text=input_text,
                result=result,
                cache_hit=False,
                started=started,
            )
            return result

        result: dict
        cache_hit = False
        with self._locked(self._lock_path(request_fingerprint)):
            if report_path.exists():
                try:
                    result = self._schema_check(
                        json.loads(report_path.read_text(encoding="utf-8")), output_schema
                    )
                    if output_validator is not None:
                        result = self._schema_check(output_validator(result), output_schema)
                    if estimate_tokens(json.dumps(result, ensure_ascii=False)) > self.output_token_limit:
                        raise ValueError("Cached agent output exceeds output token budget")
                    cache_hit = True
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    cache_hit = False
                    report_path.unlink(missing_ok=True)

            if not cache_hit:
                result = self._schema_check(runner.run(payload), output_schema)
                if output_validator is not None:
                    result = self._schema_check(output_validator(result), output_schema)
                if estimate_tokens(json.dumps(result, ensure_ascii=False)) > self.output_token_limit:
                    raise ValueError("Agent output exceeds output token budget")
                self._write_json_atomic(report_path, result)

        self._telemetry(
            telemetry_path,
            agent_name=agent_name,
            bundle=bundle,
            request_fingerprint=request_fingerprint,
            input_text=input_text,
            result=result,
            cache_hit=cache_hit,
            started=started,
        )
        return result
