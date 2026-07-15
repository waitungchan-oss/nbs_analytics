from __future__ import annotations

import fnmatch
import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from backend.agents.implementation_models import (
    ImplementationTaskContract,
    load_implementation_policy,
)


READ_ONLY_GIT = {
    "branch": ("git", "branch", "--show-current"),
    "head": ("git", "rev-parse", "HEAD"),
    "status": ("git", "status", "--porcelain=v1", "-z"),
    "diff_numstat": ("git", "diff", "--numstat", "--"),
    "diff_numstat_cached": ("git", "diff", "--cached", "--numstat", "--"),
    "diff_numstat_head": ("git", "diff", "HEAD", "--numstat", "--"),
    "diff_head_binary": ("git", "diff", "HEAD", "--binary", "--"),
    "index_entries": ("git", "ls-files", "--stage", "-z"),
}


@dataclass(frozen=True)
class GuardDecision:
    status: str
    changed_files: tuple[str, ...] = ()
    diff_lines: int = 0
    reason: str = ""
    index_fingerprint_changed: bool = False
    tree_fingerprint_changed: bool = False
    formal_state_fingerprint_changed: bool = False


@dataclass(frozen=True)
class WorktreeState:
    head: str
    changes: Mapping[str, str]
    diff_lines: int
    index_fingerprint: str
    tree_fingerprint: str
    formal_state_fingerprint: str
    formal_state_entries: Mapping[str, str]


def _run_git(project_root: Path, command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
        shell=False,
    )


def _git_output(project_root: Path, name: str) -> str | None:
    completed = _run_git(project_root, READ_ONLY_GIT[name])
    return completed.stdout.strip() if completed.returncode == 0 else None


def _git_bytes(project_root: Path, name: str) -> bytes | None:
    completed = _run_git(project_root, READ_ONLY_GIT[name])
    return completed.stdout.encode() if completed.returncode == 0 else None


def _status_paths(project_root: Path) -> dict[str, str] | None:
    completed = _run_git(project_root, (*READ_ONLY_GIT["status"], "--untracked-files=all"))
    if completed.returncode != 0:
        return None

    entries = completed.stdout.split("\0")
    changes: dict[str, str] = {}
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        changes[path] = status
        if "R" in status or "C" in status:
            if index < len(entries) and entries[index]:
                changes[entries[index]] = status
            index += 1
    return changes


def _numstat(project_root: Path, command_name: str) -> dict[str, int] | None:
    completed = _run_git(project_root, READ_ONLY_GIT[command_name])
    if completed.returncode != 0:
        return None

    result: dict[str, int] = {}
    for line in completed.stdout.splitlines():
        added, deleted, path = line.split("\t", 2)
        result[path] = sum(
            int(value) for value in (added, deleted) if value.isdigit()
        )
    return result


def _diff_lines(project_root: Path, changes: Mapping[str, str]) -> int:
    # Observe both Git snapshots explicitly, then count the final tree once
    # against HEAD so staged and unstaged edits on one path are not doubled.
    if _numstat(project_root, "diff_numstat") is None:
        return 0
    if _numstat(project_root, "diff_numstat_cached") is None:
        return 0
    combined = _numstat(project_root, "diff_numstat_head")
    if combined is None:
        return 0
    total = sum(combined.values())

    for path, status in changes.items():
        if status == "??":
            candidate = project_root / path
            if candidate.is_file():
                total += len(candidate.read_text(encoding="utf-8", errors="replace").splitlines())
    return total


def _index_fingerprint(project_root: Path) -> str | None:
    entries = _git_bytes(project_root, "index_entries")
    return hashlib.sha256(entries).hexdigest() if entries is not None else None


def _tree_fingerprint(project_root: Path, changes: Mapping[str, str]) -> str | None:
    diff = _git_bytes(project_root, "diff_head_binary")
    if diff is None:
        return None
    digest = hashlib.sha256()
    digest.update(diff)
    for path, status in sorted(changes.items()):
        digest.update(status.encode())
        digest.update(b"\0")
        digest.update(path.encode())
        digest.update(b"\0")
        if status == "??":
            candidate = project_root / path
            if candidate.is_file():
                digest.update(candidate.read_bytes())
    return digest.hexdigest()


def _matches_denied_path(relative_path: str, pattern: str) -> bool:
    normalized = pattern.rstrip("/")
    if normalized.endswith("/**"):
        prefix = normalized[:-3]
        if relative_path == prefix or relative_path.startswith(f"{prefix}/"):
            return True
    return fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(Path(relative_path).name, pattern)


def _formal_state_entries(project_root: Path, patterns: tuple[str, ...]) -> dict[str, str]:
    entries: dict[str, str] = {}
    content_patterns = tuple(pattern for pattern in patterns if not pattern.startswith(".git"))
    for current_root, directories, files in os.walk(project_root, topdown=True, followlinks=False):
        current = Path(current_root)
        relative_root = current.relative_to(project_root)
        if relative_root == Path(".git"):
            directories[:] = []
            continue
        directories[:] = sorted(name for name in directories if name != ".git")
        for name in sorted((*directories, *files)):
            candidate = current / name
            relative = candidate.relative_to(project_root).as_posix()
            if not any(_matches_denied_path(relative, pattern) for pattern in content_patterns):
                continue
            digest = hashlib.sha256()
            if candidate.is_symlink():
                digest.update(b"symlink\0")
                digest.update(os.readlink(candidate).encode("utf-8", errors="surrogateescape"))
            elif candidate.is_dir():
                digest.update(b"directory\0")
            elif candidate.is_file():
                digest.update(b"file\0")
                with candidate.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            else:
                digest.update(b"other\0")
                digest.update(str(candidate.lstat().st_mode).encode())
            entries[relative] = digest.hexdigest()
    return entries


def _formal_state_fingerprint(entries: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for path, entry_fingerprint in sorted(entries.items()):
        digest.update(path.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(entry_fingerprint.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def capture_worktree_state(project_root: Path) -> WorktreeState:
    root = Path(project_root).resolve()
    head = _git_output(root, "head")
    changes = _status_paths(root)
    if head is None or changes is None:
        raise ValueError("project_root must be a readable Git worktree")
    index_fingerprint = _index_fingerprint(root)
    tree_fingerprint = _tree_fingerprint(root, changes)
    if index_fingerprint is None or tree_fingerprint is None:
        raise ValueError("Git index and tree fingerprints are unavailable")
    policy = load_implementation_policy(root)
    formal_state_entries = _formal_state_entries(root, tuple(policy["deniedWritePatterns"]))
    return WorktreeState(
        head=head,
        changes=changes,
        diff_lines=_diff_lines(root, changes),
        index_fingerprint=index_fingerprint,
        tree_fingerprint=tree_fingerprint,
        formal_state_fingerprint=_formal_state_fingerprint(formal_state_entries),
        formal_state_entries=formal_state_entries,
    )


def _resolve_guard_path(project_root: Path, raw_path: str, denied_patterns: tuple[str, ...]) -> str:
    root = project_root.resolve()
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise PermissionError("Allowed write path must be relative to project root")

    lexical = Path(os.path.abspath(os.fspath(root / candidate)))
    current = root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise PermissionError(f"Write path cannot contain symlinks: {current}")

    resolved = lexical.resolve(strict=False)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError("Write path must stay under project root") from exc
    relative_text = relative.as_posix()
    if any(
        fnmatch.fnmatch(relative_text, pattern) or fnmatch.fnmatch(resolved.name, pattern)
        for pattern in denied_patterns
    ):
        raise PermissionError(f"Write path is denied by policy: {relative_text}")
    return relative_text


def _scope_paths(project_root: Path, contract: ImplementationTaskContract) -> tuple[tuple[str, ...], tuple[str, ...]]:
    policy = load_implementation_policy(project_root)
    patterns = tuple(policy["deniedWritePatterns"])
    allowed = tuple(_resolve_guard_path(project_root, path, patterns) for path in contract.allowed_write_paths)
    return allowed, patterns


def validate_preconditions(project_root: Path, contract: ImplementationTaskContract) -> GuardDecision:
    root = Path(project_root).resolve()
    approved_worktree = Path(contract.approved_worktree).resolve(strict=False)
    if root != approved_worktree:
        return GuardDecision("blocked_wrong_worktree", reason="approved worktree does not match project root")

    branch = _git_output(root, "branch")
    head = _git_output(root, "head")
    changes = _status_paths(root)
    if branch is None or head is None or changes is None:
        return GuardDecision("blocked_project", reason="project root is not a readable Git worktree")

    policy = load_implementation_policy(root)
    if not branch.startswith(policy["requiredBranchPrefix"]):
        return GuardDecision("blocked_wrong_branch", reason="branch does not match implementation policy")
    if head != contract.approved_base_sha:
        return GuardDecision("blocked_head_mismatch", reason="HEAD differs from approved base SHA")
    try:
        _scope_paths(root, contract)
    except (PermissionError, KeyError, TypeError):
        return GuardDecision("blocked_scope", reason="allowed write paths violate implementation policy")
    if changes:
        return GuardDecision("blocked_dirty_worktree", changed_files=tuple(sorted(changes)))
    return GuardDecision("allowed")


def validate_changes(
    project_root: Path,
    contract: ImplementationTaskContract,
    before: WorktreeState,
) -> GuardDecision:
    root = Path(project_root).resolve()
    try:
        after = capture_worktree_state(root)
        allowed, denied_patterns = _scope_paths(root, contract)
    except (ValueError, PermissionError, KeyError, TypeError):
        return GuardDecision("blocked_scope", reason="worktree or policy scope is invalid")

    changed = tuple(sorted(
        path for path in set(before.changes) | set(after.changes)
        if before.changes.get(path) != after.changes.get(path)
    ))
    index_fingerprint_changed = before.index_fingerprint != after.index_fingerprint
    tree_fingerprint_changed = before.tree_fingerprint != after.tree_fingerprint
    formal_changed = tuple(sorted(
        path for path in set(before.formal_state_entries) | set(after.formal_state_entries)
        if before.formal_state_entries.get(path) != after.formal_state_entries.get(path)
    ))
    if before.formal_state_fingerprint != after.formal_state_fingerprint:
        all_changed = tuple(sorted(set(changed) | set(formal_changed)))
        return GuardDecision(
            "blocked_scope",
            changed_files=all_changed,
            diff_lines=after.diff_lines,
            reason="runner changed formal state denied by policy; worktree is quarantined",
            index_fingerprint_changed=index_fingerprint_changed,
            tree_fingerprint_changed=tree_fingerprint_changed,
            formal_state_fingerprint_changed=True,
        )
    if index_fingerprint_changed:
        return GuardDecision(
            "blocked_scope",
            changed_files=changed,
            diff_lines=after.diff_lines,
            reason="runner changed the Git index",
            index_fingerprint_changed=True,
            tree_fingerprint_changed=tree_fingerprint_changed,
        )
    for path in changed:
        try:
            normalized = _resolve_guard_path(root, path, denied_patterns)
        except PermissionError:
            return GuardDecision("blocked_scope", changed_files=changed, reason="changed path violates policy")
        if normalized not in allowed:
            return GuardDecision("blocked_scope", changed_files=changed, reason="changed path is outside approved scope")

    policy_limits = load_implementation_policy(root)["limits"]
    max_changed_files = min(contract.max_changed_files, policy_limits["maxChangedFiles"])
    max_diff_lines = min(contract.max_diff_lines, policy_limits["maxDiffLines"])
    if len(changed) > max_changed_files or after.diff_lines > max_diff_lines:
        return GuardDecision(
            "blocked_diff_limit", changed_files=changed, diff_lines=after.diff_lines,
            reason="change count or diff lines exceed policy limit",
        )
    return GuardDecision("allowed", changed_files=changed, diff_lines=after.diff_lines)
