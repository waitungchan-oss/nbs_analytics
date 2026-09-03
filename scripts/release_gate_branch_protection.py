"""Render the approved GitHub main-branch protection contract."""

from __future__ import annotations

import json
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
            "contexts": [],
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
