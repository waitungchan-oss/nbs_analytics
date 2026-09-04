import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.release_gate_branch_protection import (
    build_update_payload,
    load_policy,
    validate_live_protection,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "agent_config" / "release_gate_branch_protection.json"


def test_policy_renders_only_github_actions_aggregate_as_required_check():
    policy = load_policy(POLICY_PATH)
    payload = build_update_payload(policy)

    assert payload["required_status_checks"] == {
        "strict": True,
        "checks": [{"context": "Release gate aggregate", "app_id": 15368}],
    }
    assert "contexts" not in payload["required_status_checks"]
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


def live_payload():
    return {
        "required_status_checks": {
            "strict": True,
            "contexts": [],
            "checks": [{"context": "Release gate aggregate", "app_id": 15368}],
        },
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 0,
            "require_last_push_approval": False,
            "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
        },
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_linear_history": {"enabled": False},
        "block_creations": {"enabled": False},
        "required_conversation_resolution": {"enabled": False},
        "lock_branch": {"enabled": False},
        "allow_fork_syncing": {"enabled": False},
        "restrictions": None,
    }


def test_live_validator_accepts_exact_protection():
    assert validate_live_protection(load_policy(POLICY_PATH), live_payload()) == ()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["required_status_checks"].update(strict=False),
        lambda p: p["required_status_checks"]["checks"].append(
            {"context": "Hermes release gate", "app_id": 15368}
        ),
        lambda p: p["required_status_checks"]["checks"][0].update(app_id=-1),
        lambda p: p["enforce_admins"].update(enabled=False),
        lambda p: p.pop("required_pull_request_reviews"),
        lambda p: p["required_pull_request_reviews"].update(
            bypass_pull_request_allowances={"users": ["waitungchan-oss"]}
        ),
        lambda p: p["allow_force_pushes"].update(enabled=True),
        lambda p: p["allow_deletions"].update(enabled=True),
    ],
)
def test_live_validator_rejects_drift(mutation):
    payload = live_payload()
    mutation(payload)
    assert validate_live_protection(load_policy(POLICY_PATH), payload)


@pytest.mark.parametrize("remove_key", ["required_status_checks", "checks"])
def test_live_validator_rejects_missing_required_status_checks(remove_key):
    payload = live_payload()
    if remove_key == "required_status_checks":
        payload.pop(remove_key)
    else:
        payload["required_status_checks"].pop(remove_key)
    assert validate_live_protection(load_policy(POLICY_PATH), payload)


def test_live_validator_rejects_context_aliases_and_malformed_bypass():
    payload = live_payload()
    payload["required_status_checks"]["contexts"] = ["Hermes release gate"]
    assert validate_live_protection(load_policy(POLICY_PATH), payload)

    payload = live_payload()
    payload["required_status_checks"]["contexts"] = ["Release gate aggregate"]
    assert validate_live_protection(load_policy(POLICY_PATH), payload) == ()

    payload = live_payload()
    payload["required_pull_request_reviews"]["bypass_pull_request_allowances"] = []
    assert validate_live_protection(load_policy(POLICY_PATH), payload)

    payload = live_payload()
    payload["required_pull_request_reviews"]["bypass_pull_request_allowances"] = {"users": []}
    assert validate_live_protection(load_policy(POLICY_PATH), payload)


@pytest.mark.parametrize(
    "field",
    [
        "required_linear_history",
        "block_creations",
        "required_conversation_resolution",
        "lock_branch",
        "allow_fork_syncing",
    ],
)
def test_live_validator_rejects_unsafe_branch_safety_flags(field):
    payload = live_payload()
    key = "enabled"
    payload[field][key] = True
    assert validate_live_protection(load_policy(POLICY_PATH), payload)


def test_live_validator_accepts_omitted_bypass_allowances():
    payload = live_payload()
    payload["required_pull_request_reviews"].pop("bypass_pull_request_allowances")
    assert validate_live_protection(load_policy(POLICY_PATH), payload) == ()


def test_live_validator_rejects_branch_restrictions():
    payload = live_payload()
    payload["restrictions"] = {"users": [], "teams": [], "apps": []}
    assert validate_live_protection(load_policy(POLICY_PATH), payload)

    payload = live_payload()
    payload.pop("restrictions")
    assert validate_live_protection(load_policy(POLICY_PATH), payload) == ()


def test_render_and_verify_cli(tmp_path):
    rendered = tmp_path / "put.json"
    verified = tmp_path / "verified.json"
    live = tmp_path / "live.json"
    live.write_text(json.dumps(live_payload()), encoding="utf-8")

    render = subprocess.run(
        [sys.executable, "scripts/release_gate_branch_protection.py", "render",
         "--policy", str(POLICY_PATH), "--output", str(rendered)],
        cwd=ROOT, capture_output=True, text=True,
    )
    verify = subprocess.run(
        [sys.executable, "scripts/release_gate_branch_protection.py", "verify",
         "--policy", str(POLICY_PATH), "--input", str(live),
         "--output", str(verified)],
        cwd=ROOT, capture_output=True, text=True,
    )

    assert render.returncode == 0
    assert verify.returncode == 0
    assert json.loads(verified.read_text(encoding="utf-8"))["status"] == "PASS"


def test_render_cli_returns_drift_exit_for_invalid_policy(tmp_path):
    invalid = tmp_path / "invalid-policy.json"
    invalid.write_text(json.dumps({"schemaVersion": "wrong"}), encoding="utf-8")
    rendered = tmp_path / "put.json"

    completed = subprocess.run(
        [sys.executable, "scripts/release_gate_branch_protection.py", "render",
         "--policy", str(invalid), "--output", str(rendered)],
        cwd=ROOT, capture_output=True, text=True,
    )

    assert completed.returncode == 2


def test_render_cli_returns_input_exit_for_missing_policy(tmp_path):
    completed = subprocess.run(
        [sys.executable, "scripts/release_gate_branch_protection.py", "render",
         "--policy", str(tmp_path / "missing.json"), "--output", str(tmp_path / "put.json")],
        cwd=ROOT, capture_output=True, text=True,
    )

    assert completed.returncode == 3
