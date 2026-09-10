"""Bounded, path-safe and atomic storage for evaluation artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
from pathlib import Path
from typing import Any


def _relative(value: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("unsafe_path")
    path = Path(value)
    if ".." in path.parts or "\\" in value or path == Path("."):
        raise ValueError("unsafe_path")
    return path


def _safe_file(root: Path, relative: str, max_bytes: int) -> Path:
    rel = _relative(relative)
    if root.is_symlink():
        raise ValueError("unsafe_path")
    candidate = root / rel
    cursor = root
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("invalid_artifact")
    target = candidate.resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise ValueError("unsafe_path")
    if not target.is_file() or target.is_symlink():
        raise ValueError("invalid_artifact")
    size = target.stat().st_size
    if size > max_bytes:
        raise ValueError("size_exceeded")
    return target


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _open_relative_nofollow(root: Path, relative: str) -> int:
    """Open a relative file through directory descriptors to avoid path races."""
    rel = _relative(relative)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = flags | getattr(os, "O_DIRECTORY", 0)
    parent_fd = os.open(root, directory_flags)
    try:
        for part in rel.parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        return os.open(rel.parts[-1], flags, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def read_json(root: Path, relative_path: str, *, max_bytes: int) -> dict:
    target = _safe_file(Path(root), relative_path, max_bytes)
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = _open_relative_nofollow(Path(root), relative_path)
        try:
            stat_result = os.fstat(descriptor)
            if not stat.S_ISREG(stat_result.st_mode):
                raise ValueError("invalid_artifact")
            raw = os.read(descriptor, max_bytes + 1)
        finally:
            os.close(descriptor)
        if len(raw) > max_bytes:
            raise ValueError("size_exceeded")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non_finite")))
    except UnicodeDecodeError:
        raise ValueError("invalid_utf8")
    except json.JSONDecodeError:
        raise ValueError("invalid_json")
    if not isinstance(value, dict):
        raise ValueError("invalid_json_object")
    return value


def read_json_bytes(root: Path, relative_path: str, *, max_bytes: int) -> tuple[dict, bytes]:
    """Read and parse one file; return the exact bytes used for validation."""
    target = _safe_file(Path(root), relative_path, max_bytes)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = _open_relative_nofollow(Path(root), relative_path)
    try:
        stat_result = os.fstat(descriptor)
        if not stat.S_ISREG(stat_result.st_mode):
            raise ValueError("invalid_artifact")
        raw = os.read(descriptor, max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(raw) > max_bytes:
        raise ValueError("size_exceeded")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non_finite")))
    except UnicodeDecodeError:
        raise ValueError("invalid_utf8")
    except json.JSONDecodeError:
        raise ValueError("invalid_json")
    if not isinstance(value, dict):
        raise ValueError("invalid_json_object")
    return value, raw


def _inventory(root: Path) -> tuple[int, int]:
    total = files = 0
    if not root.exists():
        return 0, 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("unsafe_path")
        if path.is_file():
            files += 1
            total += path.stat().st_size
    return total, files


def publish_bundle(root: Path, experiment_id: str, files: dict[str, bytes], *, quota_bytes: int = 33_554_432) -> dict:
    root = Path(root)
    if (not isinstance(experiment_id, str) or not experiment_id or experiment_id in {".", ".."}
            or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in experiment_id)):
        raise ValueError("unsafe_experiment_id")
    if not isinstance(files, dict) or not files:
        raise ValueError("invalid_bundle")
    normalized: dict[Path, bytes] = {}
    for name, content in files.items():
        path = _relative(name)
        if path == Path("bundle-manifest.json"):
            raise ValueError("reserved_artifact_name")
        if not isinstance(content, bytes):
            raise ValueError("invalid_bundle")
        if len(content) > 2 * 1024 * 1024:
            raise ValueError("size_exceeded")
        normalized[path] = content
    if root.exists() and root.is_symlink():
        raise ValueError("unsafe_path")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("unsafe_path")
    try:
        root.resolve().relative_to(root.parent.resolve())
    except ValueError as exc:
        raise ValueError("unsafe_path") from exc
    lock_path = root / ".agent-eval.lock"
    with lock_path.open("a+b") as lock:
        try:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except ImportError:
            raise ValueError("blocked_platform")
        try:
            bundle = root / experiment_id
            hashes = {str(path): hashlib.sha256(content).hexdigest() for path, content in normalized.items()}
            metadata_bytes = json.dumps({"schemaVersion": "agent-eval-bundle-v1", "experimentId": experiment_id,
                                         "files": hashes}, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if bundle.exists() or bundle.is_symlink():
                if not bundle.is_dir() or bundle.is_symlink():
                    raise ValueError("unsafe_path")
                metadata = bundle / "bundle-manifest.json"
                existing_hashes = {}
                if metadata.is_file():
                    existing_hashes = json.loads(metadata.read_text(encoding="utf-8")).get("files", {})
                if existing_hashes != hashes:
                    raise ValueError("experiment_collision")
                for relative, expected_hash in hashes.items():
                    target = _safe_file(bundle, relative, 2 * 1024 * 1024)
                    if hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
                        raise ValueError("experiment_collision")
                return {"status": "existing", "experimentId": experiment_id, "files": hashes}
            current_bytes, current_files = _inventory(root)
            if current_bytes + sum(len(value) for value in normalized.values()) + len(metadata_bytes) > quota_bytes or current_files + len(normalized) + 1 > 4096:
                raise ValueError("quota_exceeded")
            staging = root / f".tmp-{experiment_id}-{secrets.token_hex(8)}"
            staging.mkdir(mode=0o700)
            try:
                for path, content in normalized.items():
                    target = staging / path
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with target.open("xb") as handle:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(target, 0o600)
                metadata = staging / "bundle-manifest.json"
                with metadata.open("xb") as handle:
                    handle.write(metadata_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(metadata, 0o600)
                os.rename(staging, bundle)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            return {"status": "published", "experimentId": experiment_id, "files": hashes}
        finally:
            try:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            except ImportError:
                pass
