"""Run bounded, read-only HTTP acceptance checks for the GMV Streamlit UI.

The runner deliberately consumes only the bounded evidence contract produced by
an external browser/UI harness. It never uploads business files or writes to
the production database/cache.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
from urllib.error import URLError
from urllib.request import Request, urlopen

from backend.services.gmv_ui_acceptance_service import (
    UiAcceptanceEvidence,
    _from_mapping,
    validate_ui_acceptance_evidence,
)


_PRODUCTION_MARKERS = (
    ".nbs_runtime",
    "nbs_marketing_data.db",
    "production",
)


def _validate_target(url: str, fixture_root: str | Path) -> Path:
    """Validate an HTTP target and a temporary fixture root."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("HTTP URL is required; file:// is not supported")
    root = Path(fixture_root).expanduser().resolve()
    normalized = str(root).lower()
    if any(marker in normalized for marker in _PRODUCTION_MARKERS):
        raise ValueError("production database/cache paths are not allowed")
    temporary_roots = {Path(tempfile.gettempdir()).resolve()}
    runner_temp = os.environ.get("RUNNER_TEMP")
    if runner_temp:
        temporary_roots.add(Path(runner_temp).expanduser().resolve())
    if not any(root == candidate or candidate in root.parents for candidate in temporary_roots):
        raise ValueError("fixture root must be under the system temporary directory")
    return root


def load_bounded_evidence(path: str | Path) -> UiAcceptanceEvidence:
    """Load and validate only the bounded UI acceptance evidence contract."""
    evidence_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid UI acceptance evidence: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("UI acceptance evidence must be a JSON object")
    result = validate_ui_acceptance_evidence(payload)
    if result.status not in {"PASS", "FAIL"}:
        raise ValueError("invalid UI acceptance validation result")
    return _from_mapping(payload)


def _probe_http(url: str) -> tuple[int | None, str | None]:
    request = Request(url, headers={"User-Agent": "nbs-gmv-ui-acceptance/1"})
    try:
        with urlopen(request, timeout=5) as response:
            return int(response.status), None
    except (OSError, URLError) as exc:
        return None, str(exc)


def run_ui_acceptance(
    *, url: str, fixture_root: str | Path, evidence_path: str | Path,
) -> dict[str, Any]:
    root = _validate_target(url, fixture_root)
    evidence_file = Path(evidence_path).expanduser().resolve()
    try:
        evidence_file.relative_to(root)
    except ValueError as exc:
        raise ValueError("evidence file must be inside fixture root") from exc

    evidence = load_bounded_evidence(evidence_file)
    http_status, http_error = _probe_http(url)
    validation = validate_ui_acceptance_evidence(evidence)
    reasons = list(validation.failure_reasons)
    if http_error:
        reasons.append("HTTP_PROBE_FAILED")
    elif http_status is None or http_status >= 400:
        reasons.append("HTTP_STATUS_INVALID")
    if evidence.route.rstrip("/") != url.rstrip("/"):
        reasons.append("EVIDENCE_ROUTE_MISMATCH")
    reasons = sorted(set(reasons))
    return {
        "schemaVersion": "gmv-ui-acceptance-result-v1",
        "status": "PASS" if not reasons else "FAIL",
        "route": url,
        "httpStatus": http_status,
        "evidenceStatus": validation.status,
        "failureReasons": reasons,
        "activeVersionId": evidence.active_version_id,
        "downloadedArtifacts": dict(evidence.downloaded_artifacts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--fixture-root", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run_ui_acceptance(
        url=args.url, fixture_root=args.fixture_root, evidence_path=args.evidence,
    )
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        output = Path(args.output).expanduser().resolve()
        root = Path(args.fixture_root).expanduser().resolve()
        try:
            output.relative_to(root)
        except ValueError as exc:
            raise ValueError("output file must be inside fixture root") from exc
        output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
