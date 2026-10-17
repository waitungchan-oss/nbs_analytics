from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


PRESERVED_UNTRACKED_ROOTS = frozenset(
    {
        "Summay/AGENT_MEMORY_MAP_2026-09-05",
        "Summay/agent-town-demo",
        "Summay/秋季旅遊展_2026-09-25至27_銷售數據整理.docx",
    }
)
MAX_DEPTH = 12
MAX_FILES_PER_ROOT = 128
MAX_TOTAL_FILES = 256
MAX_TOTAL_ENTRIES = 4096
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
HASH_CHUNK_BYTES = 128 * 1024
_INVENTORY_SCHEMA = "preserved-untracked-inventory-v1"
_OPEN_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_OPEN_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class PreservedUntrackedError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
    )


def _same_identity(before: os.stat_result, after: os.stat_result) -> None:
    if _identity(before) != _identity(after):
        raise PreservedUntrackedError("preserved_untracked_source_drift", "filesystem entry changed during inventory")


@dataclass(frozen=True)
class PreservedUntrackedRoot:
    path: str
    kind: str
    regular_file_count: int
    total_bytes: int
    max_depth: int
    tree_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "regularFileCount": self.regular_file_count,
            "totalBytes": self.total_bytes,
            "maxDepth": self.max_depth,
            "treeSha256": self.tree_sha256,
        }


@dataclass(frozen=True)
class PreservedUntrackedInventory:
    entries: tuple[PreservedUntrackedRoot, ...]
    inventory_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": _INVENTORY_SCHEMA,
            "entries": [entry.to_dict() for entry in self.entries],
            "inventoryFingerprint": self.inventory_fingerprint,
        }


@dataclass
class _Budget:
    total_files: int = 0
    total_bytes: int = 0
    total_entries: int = 0


def _read_regular_file(file_fd: int, before: os.stat_result, budget: _Budget) -> tuple[str, int]:
    if not stat.S_ISREG(before.st_mode):
        raise PreservedUntrackedError("preserved_untracked_unsafe_entry", "expected a regular file")
    if before.st_nlink != 1:
        raise PreservedUntrackedError("preserved_untracked_hardlink", "hard-linked files are not allowed")
    if before.st_size > MAX_FILE_BYTES:
        raise PreservedUntrackedError("preserved_untracked_limit_exceeded", "file size cap exceeded")
    if budget.total_bytes + before.st_size > MAX_TOTAL_BYTES:
        raise PreservedUntrackedError("preserved_untracked_limit_exceeded", "total byte cap exceeded")

    digest = hashlib.sha256()
    read_bytes = 0
    while True:
        chunk = os.read(file_fd, HASH_CHUNK_BYTES)
        if not chunk:
            break
        read_bytes += len(chunk)
        if read_bytes > MAX_FILE_BYTES or budget.total_bytes + read_bytes > MAX_TOTAL_BYTES:
            raise PreservedUntrackedError("preserved_untracked_limit_exceeded", "file or total byte cap exceeded")
        digest.update(chunk)
    after = os.fstat(file_fd)
    _same_identity(before, after)
    if read_bytes != before.st_size:
        raise PreservedUntrackedError("preserved_untracked_source_drift", "file length changed during inventory")
    budget.total_bytes += read_bytes
    return digest.hexdigest(), read_bytes


def _open_directory(parent_fd: int, name: str) -> int:
    try:
        fd = os.open(name, os.O_RDONLY | _OPEN_DIRECTORY | _OPEN_NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        raise PreservedUntrackedError("preserved_untracked_unsafe_entry", "directory could not be opened without following links") from exc
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise PreservedUntrackedError("preserved_untracked_unsafe_entry", "expected a directory")
    return fd


def _open_parent(project_fd: int, path_parts: tuple[str, ...]) -> tuple[int, str]:
    current_fd = os.dup(project_fd)
    try:
        for part in path_parts[:-1]:
            next_fd = _open_directory(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, path_parts[-1]
    except Exception:
        os.close(current_fd)
        raise


def _validate_names(names: list[str]) -> list[str]:
    for name in names:
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise PreservedUntrackedError("preserved_untracked_unsafe_path", "tree contains an invalid path component")
        try:
            name.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise PreservedUntrackedError("preserved_untracked_unsafe_path", "tree contains a non-UTF-8 path") from exc
    return sorted(names)


def _list_directory_names(directory_fd: int, budget: _Budget) -> list[str]:
    names: list[str] = []
    try:
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                if budget.total_entries + len(names) >= MAX_TOTAL_ENTRIES:
                    raise PreservedUntrackedError(
                        "preserved_untracked_limit_exceeded", "total inventory entry cap exceeded"
                    )
                names.append(entry.name)
    except PreservedUntrackedError:
        raise
    except OSError as exc:
        raise PreservedUntrackedError("preserved_untracked_source_drift", "tree could not be read consistently") from exc
    names = _validate_names(names)
    budget.total_entries += len(names)
    return names


def _walk_directory(
    directory_fd: int,
    depth: int,
    relative: str,
    records: list[dict[str, object]],
    budget: _Budget,
    root_file_count: list[int],
    max_depth: list[int],
) -> None:
    before = os.fstat(directory_fd)
    if not stat.S_ISDIR(before.st_mode):
        raise PreservedUntrackedError("preserved_untracked_unsafe_entry", "expected a directory")
    records.append({"path": relative, "kind": "directory", "bytes": 0})
    try:
        names = _list_directory_names(directory_fd, budget)
        for name in names:
            child_depth = depth + 1
            if child_depth > MAX_DEPTH:
                raise PreservedUntrackedError("preserved_untracked_limit_exceeded", "tree depth cap exceeded")
            max_depth[0] = max(max_depth[0], child_depth)
            child_relative = name if relative == "." else f"{relative}/{name}"
            try:
                child_before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise PreservedUntrackedError("preserved_untracked_source_drift", "tree entry changed during inventory") from exc
            if stat.S_ISLNK(child_before.st_mode):
                raise PreservedUntrackedError("preserved_untracked_symlink", "symbolic links are not allowed")
            if stat.S_ISDIR(child_before.st_mode):
                child_fd = _open_directory(directory_fd, name)
                try:
                    _same_identity(child_before, os.fstat(child_fd))
                    _walk_directory(child_fd, child_depth, child_relative, records, budget, root_file_count, max_depth)
                    _same_identity(child_before, os.fstat(child_fd))
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(child_before.st_mode):
                root_file_count[0] += 1
                budget.total_files += 1
                if root_file_count[0] > MAX_FILES_PER_ROOT or budget.total_files > MAX_TOTAL_FILES:
                    raise PreservedUntrackedError("preserved_untracked_limit_exceeded", "regular file count cap exceeded")
                try:
                    child_fd = os.open(name, os.O_RDONLY | _OPEN_NOFOLLOW, dir_fd=directory_fd)
                except OSError as exc:
                    raise PreservedUntrackedError("preserved_untracked_source_drift", "file changed before it could be opened") from exc
                try:
                    opened = os.fstat(child_fd)
                    _same_identity(child_before, opened)
                    file_hash, size = _read_regular_file(child_fd, opened, budget)
                    _same_identity(opened, os.fstat(child_fd))
                finally:
                    os.close(child_fd)
                records.append({"path": child_relative, "kind": "file", "bytes": size, "sha256": file_hash})
            else:
                raise PreservedUntrackedError("preserved_untracked_unsafe_entry", "special filesystem entries are not allowed")
        _same_identity(before, os.fstat(directory_fd))
    except PreservedUntrackedError:
        raise
    except OSError as exc:
        raise PreservedUntrackedError("preserved_untracked_source_drift", "tree could not be read consistently") from exc


def _snapshot_root(project_fd: int, path: str, budget: _Budget) -> PreservedUntrackedRoot:
    parts = tuple(path.split("/"))
    parent_fd, name = _open_parent(project_fd, parts)
    records: list[dict[str, object]] = []
    file_count = [0]
    max_depth = [0]
    try:
        try:
            before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise PreservedUntrackedError("preserved_untracked_missing_root", "approved root is missing") from exc
        if stat.S_ISLNK(before.st_mode):
            raise PreservedUntrackedError("preserved_untracked_symlink", "approved root is a symbolic link")
        if stat.S_ISDIR(before.st_mode):
            budget.total_entries += 1
            if budget.total_entries > MAX_TOTAL_ENTRIES:
                raise PreservedUntrackedError("preserved_untracked_limit_exceeded", "total inventory entry cap exceeded")
            root_fd = _open_directory(parent_fd, name)
            try:
                opened = os.fstat(root_fd)
                _same_identity(before, opened)
                _walk_directory(root_fd, 0, ".", records, budget, file_count, max_depth)
                _same_identity(opened, os.fstat(root_fd))
                kind = "directory"
            finally:
                os.close(root_fd)
        elif stat.S_ISREG(before.st_mode):
            budget.total_entries += 1
            if budget.total_entries > MAX_TOTAL_ENTRIES:
                raise PreservedUntrackedError("preserved_untracked_limit_exceeded", "total inventory entry cap exceeded")
            budget.total_files += 1
            if budget.total_files > MAX_TOTAL_FILES:
                raise PreservedUntrackedError("preserved_untracked_limit_exceeded", "regular file count cap exceeded")
            if before.st_nlink != 1:
                raise PreservedUntrackedError("preserved_untracked_hardlink", "hard-linked files are not allowed")
            if before.st_size > MAX_FILE_BYTES or budget.total_bytes + before.st_size > MAX_TOTAL_BYTES:
                raise PreservedUntrackedError("preserved_untracked_limit_exceeded", "file or total byte cap exceeded")
            root_fd = os.open(name, os.O_RDONLY | _OPEN_NOFOLLOW, dir_fd=parent_fd)
            try:
                opened = os.fstat(root_fd)
                _same_identity(before, opened)
                file_hash, size = _read_regular_file(root_fd, opened, budget)
                _same_identity(opened, os.fstat(root_fd))
                records.append({"path": ".", "kind": "file", "bytes": size, "sha256": file_hash})
                file_count[0] = 1
                kind = "file"
            finally:
                os.close(root_fd)
        else:
            raise PreservedUntrackedError("preserved_untracked_unsafe_entry", "approved root must be a regular file or directory")
        _same_identity(before, os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
    finally:
        os.close(parent_fd)

    current_parent_fd, current_name = _open_parent(project_fd, parts)
    try:
        _same_identity(before, os.stat(current_name, dir_fd=current_parent_fd, follow_symlinks=False))
    finally:
        os.close(current_parent_fd)

    records.sort(key=lambda item: str(item["path"]).encode("utf-8"))
    return PreservedUntrackedRoot(
        path=path,
        kind=kind,
        regular_file_count=file_count[0],
        total_bytes=sum(int(item["bytes"]) for item in records if item["kind"] == "file"),
        max_depth=max_depth[0],
        tree_sha256=_canonical_sha256(records),
    )


def snapshot_preserved_untracked(project_root: Path, paths: Sequence[str]) -> PreservedUntrackedInventory:
    if isinstance(paths, (str, bytes)) or not isinstance(paths, Sequence):
        raise ValueError("preserved paths must be a sequence of exact roots")
    roots = list(paths)
    if not roots or len(roots) > len(PRESERVED_UNTRACKED_ROOTS):
        raise ValueError("preserved paths must contain one or more exact approved roots")
    if any(not isinstance(path, str) or path not in PRESERVED_UNTRACKED_ROOTS for path in roots):
        raise ValueError("preserved paths contain an unknown or non-normalized root")
    if len(set(roots)) != len(roots):
        raise ValueError("preserved paths must not contain duplicates")
    ordered_roots = sorted(roots, key=lambda value: value.encode("utf-8"))
    if not _OPEN_NOFOLLOW or not _OPEN_DIRECTORY:
        raise PreservedUntrackedError(
            "preserved_untracked_unsafe_entry", "secure no-follow directory traversal is unavailable"
        )
    project = Path(project_root).resolve(strict=True)
    if not project.is_dir():
        raise ValueError("project root must be a directory")
    project_fd = os.open(project, os.O_RDONLY | _OPEN_DIRECTORY | _OPEN_NOFOLLOW)
    budget = _Budget()
    try:
        entries = tuple(_snapshot_root(project_fd, path, budget) for path in ordered_roots)
    finally:
        os.close(project_fd)
    entry_payloads = [entry.to_dict() for entry in entries]
    fingerprint = _canonical_sha256({"schemaVersion": _INVENTORY_SCHEMA, "entries": entry_payloads})
    return PreservedUntrackedInventory(entries, fingerprint)
