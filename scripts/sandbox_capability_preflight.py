"""Run the required macOS sandbox capability preflight."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.sandbox_capability_preflight import (
    SandboxCapabilityEvidence,
    SandboxProbeRequest,
    run_sandbox_probe,
)
from backend.agents.sandbox_capability_receipt import write_capability_evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    project_root = Path.cwd().resolve()
    probe_root = Path(tempfile.mkdtemp(prefix="nbs-sandbox-preflight-"))
    try:
        workspace = canonical_fingerprint({
            "projectRoot": str(project_root),
            "testContract": "sandbox-integration-v1",
        })
        profile = canonical_fingerprint({
            "profile": "sandbox-capability-probe-v1",
            "network": "deny",
            "write": "single-probe-target",
        })
        request = SandboxProbeRequest(
            "darwin", workspace, Path("/usr/bin/sandbox-exec"), probe_root,
            5.0, 64 * 1024, profile,
        )
        evidence = run_sandbox_probe(request)
        write_capability_evidence(args.output.resolve(), evidence)
        print(evidence.to_dict()["status"])
        return 0 if evidence.status == "available" else 1
    finally:
        for child in probe_root.iterdir():
            if child.is_file() and not child.is_symlink():
                child.unlink()
        probe_root.rmdir()


if __name__ == "__main__":
    raise SystemExit(main())
