import json
from pathlib import Path

import pytest

from scripts.release_gate_branch_protection import build_update_payload, load_policy


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "agent_config" / "release_gate_branch_protection.json"


def test_policy_renders_only_github_actions_aggregate_as_required_check():
    policy = load_policy(POLICY_PATH)
    payload = build_update_payload(policy)

    assert payload["required_status_checks"] == {
        "strict": True,
        "contexts": [],
        "checks": [{"context": "Release gate aggregate", "app_id": 15368}],
    }
    assert payload["enforce_admins"] is True
    assert payload["required_pull_request_reviews"] == {
        "dismiss_stale_reviews": False,
        "require_code_owner_reviews": False,
        "required_approving_review_count": 0,
        "require_last_push_approval": False,
    }
    assert payload["restrictions"] is None
    assert payload["allow_force_pushes"] is False
    assert payload["allow_deletions"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "other/repository"),
        ("branch", "develop"),
        ("strict", False),
        ("enforceAdmins", False),
        ("requirePullRequest", False),
        ("requiredApprovingReviewCount", 1),
        ("bypassActors", ["waitungchan-oss"]),
        ("allowForcePushes", True),
        ("allowDeletions", True),
    ],
)
def test_policy_rejects_contract_drift(tmp_path, field, value):
    payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    payload[field] = value
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_policy(path)
