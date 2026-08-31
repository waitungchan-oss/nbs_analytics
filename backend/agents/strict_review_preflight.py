from __future__ import annotations

from pathlib import PurePosixPath
import re


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _is_python(path: str) -> bool:
    return path.endswith(".py")


def _is_production_python(path: str) -> bool:
    return _is_python(path) and (path.startswith("backend/") or path.startswith("scripts/"))


def plan_required_checks(
    changed_files: tuple[str, ...], test_files: tuple[str, ...]
) -> tuple[str, ...]:
    """Return stable, de-duplicated deterministic checks for changed surfaces."""
    if not changed_files:
        raise ValueError("changed files are required")
    checks: set[str] = set()
    has_docs = False
    for path in changed_files:
        if path.startswith("docs/"):
            has_docs = True
        if _is_production_python(path) or (path.startswith("tests/") and _is_python(path)):
            checks.update(("python-compile", "targeted-tests", "git-diff-check"))
    if has_docs and not checks:
        checks.add("docs-validation")
    if not checks:
        checks.add("git-diff-check")
    order = ("python-compile", "targeted-tests", "git-diff-check", "docs-validation")
    return tuple(check for check in order if check in checks)


def _test_key(path: str) -> str:
    stem = PurePosixPath(path).stem.lower()
    return stem[5:] if stem.startswith("test_") else stem


def resolve_targeted_tests(
    changed_files: tuple[str, ...], available_tests: tuple[str, ...]
) -> tuple[str, ...]:
    """Resolve tests by stable filename relationship; fail closed if missing."""
    selected: list[str] = []
    for path in changed_files:
        if path.startswith("tests/") and _is_python(path):
            if path not in available_tests:
                raise ValueError(f"targeted test is unavailable for {path}")
            selected.append(path)
            continue
        if not _is_production_python(path):
            continue
        key = _test_key(path)
        matches = sorted(
            test for test in available_tests
            if _test_key(test) == key or _test_key(test).startswith(f"{key}_")
        )
        if not matches:
            raise ValueError(f"targeted test coverage is missing for {path}")
        selected.extend(matches)
    return tuple(dict.fromkeys(selected))


def _validate_sha(value: str, field: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} fingerprint is invalid")


def validate_source_binding(
    preflight_source: str,
    verification_source: str,
    graph_source: str | None,
    session_source: str,
) -> None:
    for value, field in (
        (preflight_source, "preflight source"),
        (verification_source, "verification source"),
        (session_source, "session source"),
    ):
        _validate_sha(value, field)
    if graph_source is not None:
        _validate_sha(graph_source, "graph source")
    expected = {preflight_source, verification_source, session_source}
    if graph_source is not None:
        expected.add(graph_source)
    if len(expected) != 1:
        raise ValueError("source fingerprint binding is invalid")


def validate_context_binding(
    context_fingerprint: str, context_source_fingerprint: str, session_source: str
) -> None:
    """Validate independent Context identity plus its session provenance."""
    _validate_sha(context_fingerprint, "context")
    _validate_sha(context_source_fingerprint, "context source")
    _validate_sha(session_source, "session source")
    if context_source_fingerprint != session_source:
        raise ValueError("context source fingerprint binding is invalid")


def is_reusable(
    command_source: str,
    current_source: str,
    command_fingerprint: str,
    current_command_fingerprint: str,
    policy_fingerprint: str,
    current_policy_fingerprint: str,
    runner_fingerprint: str,
    current_runner_fingerprint: str,
) -> bool:
    return (
        command_source == current_source
        and command_fingerprint == current_command_fingerprint
        and policy_fingerprint == current_policy_fingerprint
        and runner_fingerprint == current_runner_fingerprint
    )
