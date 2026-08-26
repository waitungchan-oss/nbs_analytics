from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import backend.services.verification_profile_acceptance as acceptance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed formal verification profile acceptance and bounded handoff (read-only)."
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="Verification profile JSON path; a relative path is resolved against --project-root.",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root that owns the verification runtime (default: current directory).",
    )
    parser.add_argument(
        "--expected-git-head",
        default=None,
        help="Expected Git HEAD the profile must be bound to (default: current repo HEAD).",
    )
    parser.add_argument(
        "--expected-project-id",
        default=None,
        help="Expected projectId binding (default: project root directory name).",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=24.0,
        help="Maximum profile age in hours before it is stale (default: 24).",
    )
    parser.add_argument(
        "--consumer",
        choices=["review", "hermes"],
        default="hermes",
        help="Handoff consumer tag for the bounded evidence (default: hermes).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path(args.project_root).resolve()
    profile_arg = Path(args.profile)
    profile_path = profile_arg if profile_arg.is_absolute() else project_root / profile_arg
    service_identity = acceptance.gather_service_identity(project_root, profile_path)
    result = acceptance.accept_profile_file(
        profile_path,
        project_root=project_root,
        expected_git_head=args.expected_git_head,
        expected_project_id=args.expected_project_id,
        max_profile_age=timedelta(hours=args.max_age_hours),
        service_identity=service_identity,
    )
    print(json.dumps(acceptance.handoff_evidence(result, consumer=args.consumer), ensure_ascii=False, indent=2))
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
