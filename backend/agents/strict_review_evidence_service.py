from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import re

from backend.agents.strict_review_preflight_models import build_preflight_fingerprint


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_TAIL_CHARS = 4000
_COMMAND_LABELS = {
    "py_compile": "python-compile",
    "pytest_targeted": "targeted-tests",
}


def run_preflight_checks(
    project_root: Path | None,
    plan: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    source_fingerprint: str,
    runner,
) -> tuple[dict, ...]:
    """Run only approved ValidationRunner commands and return bounded results."""
    del project_root
    if not isinstance(source_fingerprint, str) or not _SHA256.fullmatch(source_fingerprint):
        raise ValueError("source fingerprint is invalid")
    results: list[dict] = []
    for command_id, arguments in plan:
        label = _COMMAND_LABELS.get(command_id)
        if label is None:
            raise ValueError(f"unsupported validation command: {command_id}")
        try:
            result = runner.run(command_id, arguments)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"blocked validation runner: {exc}") from exc
        exit_code = result.exit_code
        stdout = str(result.stdout or "")[:_MAX_TAIL_CHARS]
        stderr = str(result.stderr or "")[:_MAX_TAIL_CHARS]
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise ValueError(f"validation result exit code is invalid: {command_id}")
        results.append({
            "label": label,
            "argv": list(result.argv),
            "exitCode": exit_code,
            "stdoutTail": stdout,
            "stderrTail": stderr,
            "timedOut": bool(getattr(result, "timed_out", False)),
            "sourceFingerprint": source_fingerprint,
        })
    return tuple(results)


def build_verification_v1(results: Iterable[dict]) -> dict:
    """Normalize service results to the existing exact verification-v1 shape."""
    commands = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("validation result is invalid")
        commands.append({
            "label": result["label"],
            "argv": list(result["argv"]),
            "exitCode": result["exitCode"],
            "stdoutTail": result["stdoutTail"][:_MAX_TAIL_CHARS],
            "stderrTail": result["stderrTail"][:_MAX_TAIL_CHARS],
        })
    return {"commands": commands}


def evaluate_check_results(results: Iterable[dict]) -> str:
    for result in results:
        if result.get("timedOut") or result.get("exitCode") != 0:
            return "verification_failed"
    return "ready"


def preflight_evidence_fingerprint(
    *, source_fingerprint: str, verification: dict
) -> str:
    return build_preflight_fingerprint({
        "sourceFingerprint": source_fingerprint,
        "verification": verification,
    })
