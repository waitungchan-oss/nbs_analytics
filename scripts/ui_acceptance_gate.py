"""Bind the existing bounded HTTP UI acceptance runner to release identity."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.agents.evidence_models import canonical_fingerprint
from scripts.run_gmv_ui_acceptance import _validate_target, load_bounded_evidence, run_ui_acceptance


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run_ui_acceptance_gate(
    project_root: Path,
    url: str,
    fixture_root: str | Path,
    evidence_path: str | Path,
    commit_sha: str,
    source_fingerprint: str,
) -> dict:
    if not _SHA40.fullmatch(commit_sha):
        raise ValueError("commit must be a 40-character SHA")
    if not _SHA64.fullmatch(source_fingerprint):
        raise ValueError("source fingerprint must be a 64-character SHA-256")
    root = _validate_target(url, fixture_root)
    evidence_file = Path(evidence_path).expanduser().resolve()
    evidence_file.relative_to(root)
    payload = json.loads(evidence_file.read_text(encoding="utf-8"))
    if payload.get("commitSha") != commit_sha:
        raise ValueError("UI evidence commit mismatch")
    if payload.get("sourceFingerprint") != source_fingerprint:
        raise ValueError("UI evidence source mismatch")
    load_bounded_evidence(evidence_file)
    result = run_ui_acceptance(url=url, fixture_root=root, evidence_path=evidence_file)
    unsigned = {
        "schemaVersion": "ui-acceptance-gate-v1", "gate": "ui_acceptance", "status": result["status"],
        "commitSha": commit_sha, "sourceFingerprint": source_fingerprint,
        "startedAt": _timestamp(), "finishedAt": _timestamp(),
        "result": {"route": result.get("route"), "httpStatus": result.get("httpStatus"), "evidenceStatus": result.get("evidenceStatus"), "failureReasons": result.get("failureReasons", [])},
        "metadata": {"commandId": "gmv-ui-acceptance", "httpOnly": True, "temporaryFixture": True},
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--url", required=True)
    parser.add_argument("--fixture-root", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = run_ui_acceptance_gate(args.project_root, args.url, args.fixture_root, args.evidence, args.commit_sha, args.source_fingerprint)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
