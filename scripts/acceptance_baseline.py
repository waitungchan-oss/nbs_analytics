"""Build bounded diagnostic acceptance contract and performance artifacts."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.acceptance_contract import (
    build_acceptance_contract,
    contract_fingerprint,
    validate_acceptance_contract,
)
from backend.agents.acceptance_performance_v2 import (
    build_performance_baseline_v2,
)


MAX_ARTIFACT_BYTES = 256 * 1024


def _canonical_path(path: Path | str) -> Path:
    """Normalize paths so macOS ``/tmp`` aliases do not change identity."""
    return Path(os.path.abspath(os.fspath(path))).resolve(strict=False)


def _reject_symlinked_parents(lexical: Path) -> None:
    """Reject caller-created parent links while accepting macOS's /tmp alias."""
    current = Path(lexical.anchor)
    for part in lexical.parts[1:-1]:
        current /= part
        if current == Path("/tmp"):
            continue
        if current.is_symlink():
            raise ValueError(f"artifact parent is a symlink: {current}")


def _reject_symlink_target(path: Path) -> Path:
    lexical = Path(os.path.abspath(os.fspath(path)))
    _reject_symlinked_parents(lexical)
    if lexical.is_symlink():
        raise ValueError(f"artifact path is a symlink: {path}")
    return _canonical_path(lexical)


def _open_directory_no_follow(path: Path) -> int:
    """Open every parent component with O_NOFOLLOW and return its directory fd."""
    resolved = _canonical_path(path)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(resolved.anchor, flags)
    try:
        for part in resolved.parts[1:]:
            child_fd = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        return directory_fd
    except OSError as exc:
        os.close(directory_fd)
        raise ValueError(f"artifact parent is not a stable directory: {path}") from exc


def _open_temp_artifact(parent_fd: int, name: str) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(16):
        temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
        try:
            return os.open(temporary_name, flags, 0o600, dir_fd=parent_fd), temporary_name
        except FileExistsError:
            continue
    raise OSError("unable to allocate a unique artifact temporary file")


def _directory_target_is_symlink(parent_fd: int, name: str) -> bool:
    try:
        return stat.S_ISLNK(os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode)
    except FileNotFoundError:
        return False


def _json_bytes(payload: Mapping[str, Any], *, max_bytes: int) -> bytes:
    if not isinstance(payload, Mapping):
        raise ValueError("artifact payload must be an object")
    try:
        encoded = json.dumps(
            dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as exc:
        raise ValueError("artifact payload is not JSON serializable") from exc
    if len(encoded) > max_bytes:
        raise ValueError("artifact exceeds size cap")
    return encoded


def write_bounded_artifact(
    path: Path, payload: Mapping[str, Any], *, max_bytes: int = MAX_ARTIFACT_BYTES
) -> None:
    """Atomically write one bounded JSON object without following target symlinks."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("size cap is invalid")
    target = _reject_symlink_target(Path(path))
    encoded = _json_bytes(payload, max_bytes=max_bytes)
    parent_fd = _open_directory_no_follow(target.parent)
    descriptor = None
    temporary_name = None
    try:
        if _directory_target_is_symlink(parent_fd, target.name):
            raise ValueError(f"artifact path is a symlink: {path}")
        descriptor, temporary_name = _open_temp_artifact(parent_fd, target.name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = None
        os.replace(
            temporary_name,
            target.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary_name = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def read_bounded_artifact(
    path: Path, *, max_bytes: int = MAX_ARTIFACT_BYTES
) -> dict[str, Any]:
    """Read one bounded JSON object after normalizing the caller's path."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("size cap is invalid")
    candidate = _reject_symlink_target(Path(path))
    parent_fd = _open_directory_no_follow(candidate.parent)
    descriptor = None
    try:
        if _directory_target_is_symlink(parent_fd, candidate.name):
            raise ValueError("artifact path is a symlink")
        descriptor = os.open(
            candidate.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("artifact must be a regular file")
        if os.fstat(descriptor).st_size > max_bytes:
            raise ValueError("artifact exceeds size cap")
        with os.fdopen(descriptor, "rb") as handle:
            raw = handle.read(max_bytes + 1)
        descriptor = None
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("artifact JSON is invalid") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)
    if not isinstance(value, dict):
        raise ValueError("artifact JSON must be an object")
    return value


def _json_object(raw: str, field: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _add_contract_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("contract", help="write an acceptance contract artifact")
    parser.add_argument("--contract-id", default="nbs-acceptance")
    parser.add_argument("--contract-version", required=True)
    parser.add_argument("--scope-fingerprint", required=True)
    parser.add_argument("--semantic-rules-fingerprint", required=True)
    parser.add_argument("--dataset-snapshot-fingerprint", required=True)
    parser.add_argument("--supersedes-contract-fingerprint")
    parser.add_argument("--status", default="active")
    parser.add_argument("--output", required=True)


def _add_performance_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "performance", help="write an acceptance-performance-baseline-v2 artifact"
    )
    parser.add_argument("--contract", required=True)
    parser.add_argument("--baseline-family-id", required=True)
    parser.add_argument("--baseline-role", required=True)
    parser.add_argument("--lifecycle", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--manifest-fingerprint", required=True)
    parser.add_argument("--test-population-fingerprint", required=True)
    parser.add_argument("--runner-fingerprint", required=True)
    parser.add_argument("--environment-fingerprint", required=True)
    parser.add_argument("--dataset-snapshot-fingerprint", required=True)
    parser.add_argument("--selection-mode", required=True)
    parser.add_argument("--stages", required=True, help="JSON object with bounded stage durations")
    parser.add_argument("--test-count", required=True, help="JSON object with test counts")
    parser.add_argument("--comparison", help="optional JSON comparison result")
    parser.add_argument("--output", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_contract_parser(subparsers)
    _add_performance_parser(subparsers)
    return parser


def _run_contract(args: argparse.Namespace) -> None:
    payload = build_acceptance_contract(
        contract_id=args.contract_id,
        contract_version=args.contract_version,
        scope_fingerprint=args.scope_fingerprint,
        semantic_rules_fingerprint=args.semantic_rules_fingerprint,
        dataset_snapshot_fingerprint=args.dataset_snapshot_fingerprint,
        supersedes_contract_fingerprint=args.supersedes_contract_fingerprint,
        status=args.status,
    )
    write_bounded_artifact(Path(args.output), payload)


def _run_performance(args: argparse.Namespace) -> None:
    contract = read_bounded_artifact(Path(args.contract))
    validate_acceptance_contract(contract)
    stages = _json_object(args.stages, "stages")
    test_count = _json_object(args.test_count, "test-count")
    comparison = _json_object(args.comparison, "comparison") if args.comparison else None
    payload = build_performance_baseline_v2(
        baseline_family_id=args.baseline_family_id,
        baseline_role=args.baseline_role,
        lifecycle=args.lifecycle,
        contract_fingerprint=contract_fingerprint(contract),
        commit_sha=args.commit_sha,
        source_fingerprint=args.source_fingerprint,
        manifest_fingerprint=args.manifest_fingerprint,
        test_population_fingerprint=args.test_population_fingerprint,
        runner_fingerprint=args.runner_fingerprint,
        environment_fingerprint=args.environment_fingerprint,
        dataset_snapshot_fingerprint=args.dataset_snapshot_fingerprint,
        selection_mode=args.selection_mode,
        stages=stages,
        test_count=test_count,
        comparison=comparison,
    )
    write_bounded_artifact(Path(args.output), payload)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "contract":
            _run_contract(args)
        else:
            _run_performance(args)
        return 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"acceptance baseline blocked: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
