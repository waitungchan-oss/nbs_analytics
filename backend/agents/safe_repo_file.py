from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator


@contextmanager
def open_repo_file_nofollow(project_root: Path, relative_path: str) -> Iterator[int]:
    """Open a regular repository file by traversing each component without links."""
    relative = PurePosixPath(relative_path) if isinstance(relative_path, str) else None
    if (
        relative is None
        or not relative_path
        or "\\" in relative_path
        or "\x00" in relative_path
        or relative.is_absolute()
        or str(relative) != relative_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise PermissionError("Repository-relative file path is invalid")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory:
        raise PermissionError("No-follow directory traversal is unavailable")

    try:
        root = Path(project_root).resolve(strict=True)
        root_fd = os.open(root, os.O_RDONLY | directory | nofollow)
    except OSError as exc:
        raise PermissionError("Repository root cannot be opened safely") from exc

    parent_fd = os.dup(root_fd)
    try:
        if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
            raise PermissionError("Repository root is not a directory")
        for part in relative.parts[:-1]:
            try:
                child_fd = os.open(
                    part,
                    os.O_RDONLY | directory | nofollow,
                    dir_fd=parent_fd,
                )
            except OSError as exc:
                raise PermissionError("Repository path component cannot be opened safely") from exc
            try:
                child_is_directory = stat.S_ISDIR(os.fstat(child_fd).st_mode)
            except BaseException:
                os.close(child_fd)
                raise
            if not child_is_directory:
                os.close(child_fd)
                raise PermissionError("Repository path component is not a directory")
            os.close(parent_fd)
            parent_fd = child_fd

        try:
            file_fd = os.open(
                relative.parts[-1],
                os.O_RDONLY | nofollow,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise PermissionError("Repository file cannot be opened without following links") from exc
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise PermissionError("Repository file is not a regular file")
            yield file_fd
        finally:
            os.close(file_fd)
    finally:
        os.close(parent_fd)
        os.close(root_fd)
