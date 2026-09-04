"""Render the approved GitHub main-branch protection contract."""

from __future__ import annotations

import json
import argparse
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


POLICY_SCHEMA = "nbs-release-gate-branch-protection-v1"
EXPECTED_REPOSITORY = "waitungchan-oss/nbs_analytics"
EXPECTED_BRANCH = "main"
EXPECTED_CONTEXT = "Release gate aggregate"
EXPECTED_APP_ID = 15368
EXPECTED_APP_SLUG = "github-actions"


@dataclass(frozen=True)
class BranchProtectionPolicy:
    repository: str
    branch: str
    required_context: str
    app_id: int
    app_slug: str
    strict: bool
    enforce_admins: bool
    require_pull_request: bool
    required_approving_review_count: int
    bypass_actors: tuple[str, ...]
    allow_force_pushes: bool
    allow_deletions: bool


def load_policy(path: Path) -> BranchProtectionPolicy:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_keys = {
        "schemaVersion",
        "repository",
        "branch",
        "requiredCheck",
        "strict",
        "enforceAdmins",
        "requirePullRequest",
        "requiredApprovingReviewCount",
        "bypassActors",
        "allowForcePushes",
        "allowDeletions",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError("branch protection policy schema is invalid")
    check = payload["requiredCheck"]
    if not isinstance(check, dict) or set(check) != {"context", "appId", "appSlug"}:
        raise ValueError("requiredCheck schema is invalid")
    exact = (
        payload["schemaVersion"] == POLICY_SCHEMA
        and payload["repository"] == EXPECTED_REPOSITORY
        and payload["branch"] == EXPECTED_BRANCH
        and check == {
            "context": EXPECTED_CONTEXT,
            "appId": EXPECTED_APP_ID,
            "appSlug": EXPECTED_APP_SLUG,
        }
        and payload["strict"] is True
        and payload["enforceAdmins"] is True
        and payload["requirePullRequest"] is True
        and isinstance(payload["requiredApprovingReviewCount"], int)
        and not isinstance(payload["requiredApprovingReviewCount"], bool)
        and payload["requiredApprovingReviewCount"] == 0
        and payload["bypassActors"] == []
        and payload["allowForcePushes"] is False
        and payload["allowDeletions"] is False
    )
    if not exact:
        raise ValueError("branch protection policy violates the approved contract")
    return BranchProtectionPolicy(
        repository=payload["repository"],
        branch=payload["branch"],
        required_context=check["context"],
        app_id=check["appId"],
        app_slug=check["appSlug"],
        strict=True,
        enforce_admins=True,
        require_pull_request=True,
        required_approving_review_count=0,
        bypass_actors=(),
        allow_force_pushes=False,
        allow_deletions=False,
    )


def build_update_payload(policy: BranchProtectionPolicy) -> dict[str, object]:
    return {
        "required_status_checks": {
            "strict": policy.strict,
            "checks": [{"context": policy.required_context, "app_id": policy.app_id}],
        },
        "enforce_admins": policy.enforce_admins,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
            "required_approving_review_count": policy.required_approving_review_count,
            "require_last_push_approval": False,
        },
        "restrictions": None,
        "required_linear_history": False,
        "allow_force_pushes": policy.allow_force_pushes,
        "allow_deletions": policy.allow_deletions,
        "block_creations": False,
        "required_conversation_resolution": False,
        "lock_branch": False,
        "allow_fork_syncing": False,
    }


def _enabled(payload: Mapping[str, object], key: str) -> bool | None:
    value = payload.get(key)
    return value.get("enabled") if isinstance(value, Mapping) else None


def validate_live_protection(
    policy: BranchProtectionPolicy,
    payload: Mapping[str, object],
) -> tuple[str, ...]:
    errors: list[str] = []
    status = payload.get("required_status_checks")
    if not isinstance(status, Mapping) or status.get("strict") is not True:
        errors.append("required_status_checks.strict must be true")
    if not isinstance(status, Mapping) or status.get("contexts") not in (
        [], [policy.required_context]
    ):
        errors.append("required_status_checks.contexts must be empty or canonical aggregate")
    checks = status.get("checks") if isinstance(status, Mapping) else None
    expected_checks = [{"context": policy.required_context, "app_id": policy.app_id}]
    if checks != expected_checks:
        errors.append("required status checks must contain only the approved aggregate")
    if _enabled(payload, "enforce_admins") is not True:
        errors.append("admin enforcement must be enabled")
    reviews = payload.get("required_pull_request_reviews")
    if not isinstance(reviews, Mapping):
        errors.append("pull request requirement is missing")
    else:
        count = reviews.get("required_approving_review_count")
        if isinstance(count, bool) or count != 0:
            errors.append("required approving review count must be zero")
        for field in (
            "dismiss_stale_reviews",
            "require_code_owner_reviews",
            "require_last_push_approval",
        ):
            if reviews.get(field) is not False:
                errors.append(f"{field} must be false")
        bypass = reviews.get("bypass_pull_request_allowances")
        if bypass is not None and (
            not isinstance(bypass, Mapping)
            or set(bypass) != {"users", "teams", "apps"}
            or any(bypass.get(key) != [] for key in ("users", "teams", "apps"))
        ):
            errors.append("pull request bypass actors are not allowed")
    if _enabled(payload, "allow_force_pushes") is not False:
        errors.append("force pushes must be disabled")
    if _enabled(payload, "allow_deletions") is not False:
        errors.append("branch deletion must be disabled")
    for field in ("required_linear_history", "block_creations", "required_conversation_resolution", "allow_fork_syncing"):
        if _enabled(payload, field) is not False:
            errors.append(f"{field} must be disabled")
    lock_branch = payload.get("lock_branch")
    if not isinstance(lock_branch, Mapping) or lock_branch.get("enabled") is not False:
        errors.append("branch lock must be disabled")
    if payload.get("restrictions") is not None:
        errors.append("branch restrictions must be null")
    return tuple(errors)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parser() -> _ArgumentParser:
    parser = _ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=_ArgumentParser)
    render = subparsers.add_parser("render")
    render.add_argument("--policy", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--policy", type=Path, required=True)
    verify.add_argument("--input", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except (ValueError, SystemExit) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    try:
        policy = load_policy(args.policy)
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        if args.command == "render":
            _write_json(args.output, build_update_payload(policy))
            return 0
        live = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(live, dict):
            raise ValueError("live branch protection response must be an object")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    errors = validate_live_protection(policy, live)
    report = {
        "schemaVersion": "nbs-release-gate-branch-protection-verification-v1",
        "status": "PASS" if not errors else "FAIL",
        "errors": list(errors),
    }
    try:
        _write_json(args.output, report)
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
