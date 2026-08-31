"""Keep data-backed tests reproducible without copying production data into Git."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.sandbox_capability_preflight import (
    SandboxCapabilityError,
    SandboxCapabilityEvidence,
    SandboxProbeRequest,
    run_sandbox_probe,
)
from backend.agents.sandbox_capability_receipt import write_capability_evidence


_MIN_CANONICAL_DB_BYTES = 1_000_000


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _source_db(project_root: Path) -> Path | None:
    configured = os.environ.get("NBS_ANALYTICS_SOURCE_DB")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.append(project_root / "nbs_marketing_data.db")
    # An isolated worktree may not contain the ignored production snapshot;
    # the main checkout is a read-only source for a disposable test snapshot.
    candidates.append(project_root.parent.parent / "nbs_marketing_data.db")
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink() and candidate.stat().st_size >= _MIN_CANONICAL_DB_BYTES:
            return candidate
    return None


def _prepare_disposable_db(project_root: Path) -> None:
    if os.environ.get("NBS_ANALYTICS_DB_FILE"):
        return
    current = project_root / "nbs_marketing_data.db"
    if current.is_file() and current.stat().st_size >= _MIN_CANONICAL_DB_BYTES:
        return
    source = _source_db(project_root)
    if source is None:
        return
    target = Path(tempfile.gettempdir()) / f"nbs_analytics_pytest_{source.stat().st_mtime_ns}.db"
    if not target.exists():
        source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
            source_conn.close()
    os.environ["NBS_ANALYTICS_DB_FILE"] = str(target)


def pytest_configure(config) -> None:
    _prepare_disposable_db(_project_root())
    config.addinivalue_line("markers", "sandbox: tests requiring nested macOS sandbox capability")
    config.addinivalue_line("markers", "no_sandbox: explicitly excluded from the module sandbox marker")


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--sandbox-preflight",
        choices=("required", "auto", "off"),
        default=None,
        help="sandbox capability policy: required, auto, or off",
    )


def sandbox_gate_status(config, evidence: SandboxCapabilityEvidence) -> str:
    mode = config.getoption("--sandbox-preflight", default=None) if hasattr(config, "getoption") else config.option("--sandbox-preflight")
    mode = mode or ("required" if os.environ.get("CI") else "auto")
    if mode == "off" and os.environ.get("CI"):
        raise pytest.UsageError("--sandbox-preflight=off is not allowed in CI")
    return evidence.status


def sandbox_markexpr_excludes(config) -> bool:
    markexpr = getattr(getattr(config, "option", None), "markexpr", "") or ""
    normalized = " ".join(str(markexpr).split()).lower()
    return normalized in {"not sandbox", "not (sandbox)"}


def render_sandbox_blocker(evidence: SandboxCapabilityEvidence) -> str:
    return (
        "sandbox capability preflight blocked: "
        f"status={evidence.status} failureCode={evidence.failure_code} "
        f"evidenceFingerprint={evidence.evidence_fingerprint}; "
        "run sandbox tests on a qualified macOS runner with nested sandbox capability"
    )


class _SandboxBlockedItem(pytest.Item):
    def __init__(self, name, parent, message: str):
        super().__init__(name, parent)
        self.message = message

    def runtest(self) -> None:
        pytest.fail(self.message, pytrace=False)

    def repr_failure(self, excinfo):
        return str(excinfo.value)

    def reportinfo(self):
        return self.path, 0, "sandbox capability preflight"


def pytest_sessionstart(session) -> None:
    root = _project_root()
    probe_root = Path(tempfile.mkdtemp(prefix="nbs-sandbox-preflight-"))
    session.config._nbs_sandbox_probe_root = probe_root
    workspace = canonical_fingerprint({"projectRoot": str(root.resolve()), "testContract": "sandbox-integration-v1"})
    profile = canonical_fingerprint({"profile": "sandbox-capability-probe-v1", "network": "deny", "write": "single-probe-target"})
    try:
        request = SandboxProbeRequest("darwin", workspace, Path("/usr/bin/sandbox-exec"), probe_root, 5.0, 64 * 1024, profile)
        evidence = run_sandbox_probe(request)
    except (SandboxCapabilityError, OSError, ValueError) as exc:
        evidence = SandboxCapabilityEvidence._build("invalid_evidence", os.sys.platform, "0" * 64, profile, workspace, {key: False for key in ("applicationApplied", "filesystemPolicyEnforced", "processPolicyEnforced", "networkPolicyEnforced")}, "probe_evidence_invalid", (str(exc)[:512],), "1970-01-01T00:00:00Z", "1970-01-01T00:00:00Z")
    session.config._nbs_sandbox_capability = evidence
    try:
        write_capability_evidence(
            root / ".nbs_agent_runtime" / "sandbox-capability" / "evidence.json",
            evidence,
        )
    except (SandboxCapabilityError, OSError):
        # The in-memory gate remains authoritative for this pytest session;
        # Hermes will report a missing/invalid receipt rather than PASS.
        pass


def pytest_collection_modifyitems(config, items) -> None:
    evidence = getattr(config, "_nbs_sandbox_capability", None)
    for item in items:
        if item.get_closest_marker("no_sandbox") is not None:
            item.own_markers = [marker for marker in item.own_markers if marker.name != "sandbox"]
    sandbox_items = [item for item in items if item.get_closest_marker("sandbox") is not None and item.get_closest_marker("no_sandbox") is None]
    if evidence is None or not sandbox_items or evidence.status == "available":
        return
    retained = [item for item in items if item not in sandbox_items]
    if sandbox_markexpr_excludes(config):
        items[:] = retained
        return
    mode = config.getoption("--sandbox-preflight", default=None) or ("required" if os.environ.get("CI") else "auto")
    if mode == "auto":
        reason = render_sandbox_blocker(evidence)
        for item in sandbox_items:
            item.add_marker(pytest.mark.skip(reason=reason))
        return
    message = render_sandbox_blocker(evidence)
    blocked = _SandboxBlockedItem.from_parent(sandbox_items[0].parent, name="sandbox-capability-preflight", message=message)
    blocked.add_marker(pytest.mark.sandbox)
    items[:] = retained + [blocked]


def pytest_sessionfinish(session, exitstatus) -> None:
    probe_root = getattr(session.config, "_nbs_sandbox_probe_root", None)
    if isinstance(probe_root, Path) and probe_root.is_dir() and not probe_root.is_symlink():
        for child in probe_root.iterdir():
            if child.is_file() and not child.is_symlink():
                child.unlink()
        probe_root.rmdir()
