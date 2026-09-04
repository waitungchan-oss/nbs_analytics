# Release Gate Branch Protection Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 GitHub `main` 設為受保護分支，唯一 required status check 為由 GitHub Actions 提供的 `Release gate aggregate`，並以 deterministic local tooling、before snapshot、single PUT、post-GET verification 與 rollback contract完成可稽核 enforcement。

**Architecture:** Repository 保存 exact desired policy；純本地 Python CLI只負責把 policy渲染成 GitHub API payload及驗證 GET response，不持有 token或直接 mutate GitHub。主 Codex在 preflight通過後執行一次明確 `gh api PUT`，再用同一 deterministic validator驗證 live state；Context Agent、Governance Graph、Memory Hub與Memory Sidecar維持 read-only／non-authoritative。

**Tech Stack:** Python 3.10、pytest、JSON exact schema、GitHub CLI `gh`、GitHub REST Branch Protection API `2026-03-10`、GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-09-03-release-gate-branch-protection-design.md`

## Global Constraints

- 實作模型固定為 `gpt-5.6-luna`，reasoning effort固定為 `medium`；每個 Task使用 fresh bounded session，目標不超過15 turns。
- Repository固定為 `waitungchan-oss/nbs_analytics`，target branch固定為 `main`。
- 唯一 required check固定為 `Release gate aggregate`，provider固定為 `github-actions`、`app_id=15368`。
- `strict=true`、`enforce_admins=true`、required Pull Request啟用、required approvals固定為0、bypass actors固定為空。
- Force push與branch deletion固定 disabled。
- 所有 REST calls帶 `Accept: application/vnd.github+json`及`X-GitHub-Api-Version: 2026-03-10`。
- PUT前必須取得before snapshot；existing protection drift、403、404、422、provider drift或uncertain mutation一律停止，不自行放寬contract。
- 正式業務scope固定為「不含掛賬核銷與 TT 退款轉團款」；2026-05 frozen baseline固定為HKD 12,057,968。
- 不修改正式SQLite、baseline、revenue、GMV／退款規則、export schema或production business state。
- Strict Review、Full pytest、Hermes、UI acceptance、sandbox capability與Governance Graph是獨立gates，不互相取代。
- Governance Graph、Memory Hub、Memory Sidecar、Agent Operations與Hermes維持read-only／non-authoritative。
- 每個有tracked diff的Task先跑focused verification及findings-first Review；Review PASS後才由主Codex建立checkpoint commit。

---

### Task 1: 建立 canonical branch-protection policy與deterministic renderer

**Files:**
- Create: `agent_config/release_gate_branch_protection.json`
- Create: `scripts/release_gate_branch_protection.py`
- Create: `tests/test_release_gate_branch_protection.py`

**Interfaces:**
- Consumes: exact JSON schema `nbs-release-gate-branch-protection-v1`。
- Produces: `BranchProtectionPolicy`、`load_policy(path: Path) -> BranchProtectionPolicy`、`build_update_payload(policy: BranchProtectionPolicy) -> dict[str, object]`。
- Later tasks consume the rendered GitHub Branch Protection PUT payload; this Task不讀network、不呼叫`gh`。

- [ ] **Step 1: 建立fresh Task context**

Run:

```bash
.venv/bin/python scripts/context_agent.py --collect-only \
  --brief docs/superpowers/specs/2026-09-03-release-gate-branch-protection-design.md \
  --query "exact branch protection policy renderer Release gate aggregate app id 15368" \
  --output .nbs_agent_runtime/reports/release-gate-protection-task-01-context.json
```

Expected: exit `0`；`schemaVersion=context-evidence-v1`；Memory hints若存在只能是`non_authoritative_memory`。

- [ ] **Step 2: 寫renderer failing tests**

Create these tests in `tests/test_release_gate_branch_protection.py`:

```python
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
    import json

    payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    payload[field] = value
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_policy(path)
```

- [ ] **Step 3: 執行tests確認RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_release_gate_branch_protection.py -q
```

Expected: FAIL during import because `scripts.release_gate_branch_protection` does not exist。

- [ ] **Step 4: 建立exact policy artifact**

Create `agent_config/release_gate_branch_protection.json` with exactly:

```json
{
  "schemaVersion": "nbs-release-gate-branch-protection-v1",
  "repository": "waitungchan-oss/nbs_analytics",
  "branch": "main",
  "requiredCheck": {
    "context": "Release gate aggregate",
    "appId": 15368,
    "appSlug": "github-actions"
  },
  "strict": true,
  "enforceAdmins": true,
  "requirePullRequest": true,
  "requiredApprovingReviewCount": 0,
  "bypassActors": [],
  "allowForcePushes": false,
  "allowDeletions": false
}
```

- [ ] **Step 5: 實作immutable policy model與renderer**

Implement in `scripts/release_gate_branch_protection.py`:

```python
from __future__ import annotations

import argparse
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
        "schemaVersion", "repository", "branch", "requiredCheck", "strict",
        "enforceAdmins", "requirePullRequest", "requiredApprovingReviewCount",
        "bypassActors", "allowForcePushes", "allowDeletions",
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
```

Do not add network calls, token access or Git mutation。

- [ ] **Step 6: 執行GREEN verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_release_gate_branch_protection.py -q
.venv/bin/python -m py_compile scripts/release_gate_branch_protection.py
git diff --check
```

Expected: all commands exit `0`。

- [ ] **Step 7: 執行Task 1 findings-first Review與checkpoint**

Use `scripts/review_agent.py` with `--base HEAD --head WORKTREE`, the approved spec as both `--brief` and `--approved-brief`, and a Task contract whose allowlist is exactly the three Task 1 files. Runner profile must prove `gpt-5.6-luna` with `medium` effort. If Review verdict is not `pass`, do not commit。

After Review PASS:

```bash
git add agent_config/release_gate_branch_protection.json \
  scripts/release_gate_branch_protection.py \
  tests/test_release_gate_branch_protection.py
git commit -m "checkpoint(task-01): define release gate branch policy"
```

---

### Task 2: 建立live-response validator與fail-closed CLI

**Files:**
- Modify: `scripts/release_gate_branch_protection.py`
- Modify: `tests/test_release_gate_branch_protection.py`
- Modify: `tests/test_release_gate_workflow.py`

**Interfaces:**
- Consumes: `BranchProtectionPolicy`與GitHub GET branch-protection JSON response。
- Produces: `validate_live_protection(policy, payload) -> tuple[str, ...]`；CLI `render`及`verify`。
- CLI `render` exit `0`並寫PUT payload；CLI `verify` exit `0`只代表exact live match，drift exit `2`，CLI misuse exit `3`。
- GitHub API compatibility：PUT 的 `required_status_checks` 不同時送 `contexts: []` 與非空 `checks`；GET 的 `contexts` 可為空或 canonical aggregate single-item。

- [ ] **Step 1: 建立fresh Task context**

Run the same Context Agent command as Task 1, changing output to:

```text
.nbs_agent_runtime/reports/release-gate-protection-task-02-context.json
```

Expected: current commit與Task 1 checkpoint出現在`recentChanges`。

- [ ] **Step 2: 寫validator與CLI failing tests**

Append to `tests/test_release_gate_branch_protection.py`:

```python
import json
import subprocess
import sys

from scripts.release_gate_branch_protection import validate_live_protection


def live_payload():
    return {
        "required_status_checks": {
            "strict": True,
            "contexts": ["Release gate aggregate"],
            "checks": [{"context": "Release gate aggregate", "app_id": 15368}],
        },
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 0,
            "require_last_push_approval": False,
        },
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
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
```

Add to `tests/test_release_gate_workflow.py`:

```python
def test_release_aggregate_job_name_matches_branch_protection_contract():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "name: Release gate aggregate" in source
    assert source.count("name: Release gate aggregate") == 1
```

- [ ] **Step 3: 執行tests確認RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_release_gate_branch_protection.py \
  tests/test_release_gate_workflow.py -q
```

Expected: FAIL because `validate_live_protection`與CLI parser尚未實作。

- [ ] **Step 4: 實作exact live validator**

Implement:

```python
from collections.abc import Mapping


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
        if isinstance(bypass, Mapping) and any(bypass.get(k) for k in ("users", "teams", "apps")):
            errors.append("pull request bypass actors are not allowed")
    if _enabled(payload, "allow_force_pushes") is not False:
        errors.append("force pushes must be disabled")
    if _enabled(payload, "allow_deletions") is not False:
        errors.append("branch deletion must be disabled")
    return tuple(errors)
```

Then add this CLI implementation for exactly `render` and `verify`:

```python
class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
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
        policy = load_policy(args.policy)
        if args.command == "render":
            _write_json(args.output, build_update_payload(policy))
            return 0
        live = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(live, dict):
            raise ValueError("live branch protection response must be an object")
        errors = validate_live_protection(policy, live)
        report = {
            "schemaVersion": "nbs-release-gate-branch-protection-verification-v1",
            "status": "PASS" if not errors else "FAIL",
            "errors": list(errors),
        }
        _write_json(args.output, report)
        return 0 if not errors else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
```

Add `import sys` to the module imports. The `verify` report schema is:

```json
{
  "schemaVersion": "nbs-release-gate-branch-protection-verification-v1",
  "status": "PASS",
  "errors": []
}
```

Do not add `apply`、`rollback`或network commands。

- [ ] **Step 5: 執行GREEN verification**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_release_gate_branch_protection.py \
  tests/test_release_gate_workflow.py -q
.venv/bin/python -m py_compile scripts/release_gate_branch_protection.py
git diff --check
```

Expected: all commands exit `0`。

- [ ] **Step 6: 執行Task 2 findings-first Review與checkpoint**

Review allowlist must be exactly the three Task 2 files. Review base is Task 1 commit，head is`WORKTREE`。After Review PASS:

```bash
git add scripts/release_gate_branch_protection.py \
  tests/test_release_gate_branch_protection.py \
  tests/test_release_gate_workflow.py
git commit -m "checkpoint(task-02): validate live branch protection"
```

---

### Task 3: Preflight、snapshot、single PUT與live verification

**Files:**
- Runtime create: `.nbs_agent_runtime/reports/main-protection-before.json`
- Runtime create: `.nbs_agent_runtime/reports/main-protection-put.json`
- Runtime create: `.nbs_agent_runtime/reports/main-protection-after.json`
- Runtime create: `.nbs_agent_runtime/reports/main-protection-verification.json`
- Modify after successful verification: `NBS_ANALYTICS_HANDOFF.md`

**Interfaces:**
- Consumes: Task 2 renderer／validator、GitHub REST API `2026-03-10`、latest successful aggregate check identity。
- Produces: exact live branch protection plus bounded before／after／verification evidence。
- External mutation authority stays with main Codex; local Agent、Graph、Memory Hub、Memory Sidecar與LLM remain read-only。

- [ ] **Step 1: 建立fresh Task context與確認clean checkpoint**

Run:

```bash
git status --short --branch
git log -3 --oneline --decorate
.venv/bin/python scripts/context_agent.py --collect-only \
  --brief docs/superpowers/specs/2026-09-03-release-gate-branch-protection-design.md \
  --query "GitHub main protection preflight snapshot single PUT rollback" \
  --output .nbs_agent_runtime/reports/release-gate-protection-task-03-context.json
```

Expected: tracked worktree clean；branch is the approved feature branch；Task 2 checkpoint is HEAD。

- [ ] **Step 2: 執行read-only GitHub identity與permission preflight**

Run:

```bash
gh auth status
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/waitungchan-oss/nbs_analytics \
  --jq '{full_name,default_branch,visibility,permissions}'
```

Expected exact values：`full_name=waitungchan-oss/nbs_analytics`、`default_branch=main`、`visibility=public`、`permissions.admin=true`。Any mismatch stops Task 3 before mutation。

- [ ] **Step 3: 重新驗證required check provider identity**

Run:

```bash
NBS_AGGREGATE_SHA=$(gh run list \
  --workflow "Release Gates" --status success --limit 1 \
  --json headSha --jq '.[0].headSha')
test -n "$NBS_AGGREGATE_SHA"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  "repos/waitungchan-oss/nbs_analytics/commits/$NBS_AGGREGATE_SHA/check-runs" \
  --jq '.check_runs[] | select(.name=="Release gate aggregate") | {name,conclusion,app:{id:.app.id,slug:.app.slug}}'
```

Expected exactly one result with `name=Release gate aggregate`、`conclusion=success`、`app.id=15368`、`app.slug=github-actions`。Zero、duplicate或drift stops mutation。

- [ ] **Step 4: 取得before snapshot並fail closed on drift**

Run the GET once:

```bash
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/waitungchan-oss/nbs_analytics/branches/main/protection
```

Current approved baseline is HTTP 404 `Branch not protected`。Use `apply_patch` to persist this bounded runtime snapshot:

```json
{
  "schemaVersion": "nbs-branch-protection-snapshot-v1",
  "repository": "waitungchan-oss/nbs_analytics",
  "branch": "main",
  "exists": false
}
```

If GET returns 200，persist the bounded response and stop for policy reconciliation；do not overwrite an existing rule。

- [ ] **Step 5: Render並inspect PUT payload**

Run:

```bash
.venv/bin/python scripts/release_gate_branch_protection.py render \
  --policy agent_config/release_gate_branch_protection.json \
  --output .nbs_agent_runtime/reports/main-protection-put.json
.venv/bin/python -m json.tool \
  .nbs_agent_runtime/reports/main-protection-put.json
```

Expected: only one check object, context `Release gate aggregate`、`app_id=15368`；strict/admin/PR requirements enabled；force push/deletion disabled。Do not continue if inspection differs from spec。

- [ ] **Step 6: 執行single PUT mutation**

Run exactly once:

```bash
gh api --method PUT \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/waitungchan-oss/nbs_analytics/branches/main/protection \
  --input .nbs_agent_runtime/reports/main-protection-put.json
```

Expected: HTTP 200 response。For 403、404、422 or transport uncertainty, do not issue a second PUT；run Step 7 GET to determine live state first。

- [ ] **Step 7: GET、persist與deterministically verify live state**

Run GET once，capture stdout，then use `apply_patch` to save its exact bounded JSON as `.nbs_agent_runtime/reports/main-protection-after.json`。Run:

```bash
.venv/bin/python scripts/release_gate_branch_protection.py verify \
  --policy agent_config/release_gate_branch_protection.json \
  --input .nbs_agent_runtime/reports/main-protection-after.json \
  --output .nbs_agent_runtime/reports/main-protection-verification.json
.venv/bin/python -m json.tool \
  .nbs_agent_runtime/reports/main-protection-verification.json
```

Expected: exit `0` and report `status=PASS`、`errors=[]`。Any error triggers rollback Step 8。

- [ ] **Step 8: 執行rollback only when post-verification fails**

Because approved before snapshot is `exists=false`, rollback command is:

```bash
gh api --method DELETE \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/waitungchan-oss/nbs_analytics/branches/main/protection
```

After DELETE，GET must return HTTP 404。Do not run this step when verification PASS。

- [ ] **Step 9: 更新handoff live snapshot**

Append a dated subsection to `NBS_ANALYTICS_HANDOFF.md` containing only verified values：repository、branch、required check、app identity、strict/admin/PR settings、before state、post-verification status與runtime evidence paths。Do not copy tokens、raw headers、absolute home path或claim that Graph／Memory approved the change。

- [ ] **Step 10: Review handoff diff與checkpoint**

Run:

```bash
git diff --check
.venv/bin/python -m pytest \
  tests/test_release_gate_branch_protection.py \
  tests/test_release_gate_workflow.py -q
```

Run findings-first Review with allowlist only `NBS_ANALYTICS_HANDOFF.md` and fresh Task 3 verification summary。After Review PASS:

```bash
git add NBS_ANALYTICS_HANDOFF.md
git commit -m "checkpoint(task-03): record enforced release gate protection"
```

---

### Task 4: Final independent gates、PR enforcement與local-main closeout

**Files:**
- No new business files。
- Runtime create: fresh Review／Full pytest／Hermes／UI／aggregate evidence。

**Interfaces:**
- Consumes: Tasks 1–3 commits and live protection verification PASS。
- Produces: source-bound final gate evidence、a PR governed by required `Release gate aggregate`、merged remote and local `main`。

- [ ] **Step 1: 執行fresh focused verification**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_release_gate_branch_protection.py \
  tests/test_release_gate_workflow.py \
  tests/test_release_gate.py \
  tests/test_release_gate_models.py -q
.venv/bin/python -m py_compile scripts/release_gate_branch_protection.py
git diff --check
```

Expected: all commands exit `0`；no deselected/skipped release-protection tests。

- [ ] **Step 2: 執行fresh Strict Review**

Collect context with the approved spec；Review the complete feature branch against its approved base。Review input must include exact Task contracts、fresh focused command results、current HEAD、worktree fingerprint and complete diff。Verdict must be `pass` with no critical/high findings before integration。

- [ ] **Step 3: 執行Full pytest、Hermes與UI acceptance as separate gates**

Run the repository-approved local Full pytest adapter with required sandbox capability，the read-only Hermes adapter with isolated baseline fixture，and the HTTP/Streamlit UI acceptance adapter with temporary fixture。Do not reuse PR #51 evidence。Expected outcomes：

```text
Full pytest: PASS
Hermes: PASS
UI acceptance: PASS
```

The exact counts and evidence fingerprints are recorded from the fresh run；the plan does not prescribe historical counts。

- [ ] **Step 4: Push feature branch and create PR**

Before push：

```bash
git status --short --branch
git log -5 --oneline --decorate
```

Push only the approved feature branch，create PR targeting `main`，then inspect:

```bash
gh pr checks --watch
```

Expected required check: `Release gate aggregate`。The PR must remain unmergeable while aggregate is pending/failing and become mergeable only after aggregate succeeds。Do not merge on child checks alone。

- [ ] **Step 5: Merge only after all fresh gates pass**

Required live results：

```text
Full pytest release gate: pass
Hermes release gate: pass
UI acceptance release gate: pass
Release gate aggregate: pass
Required macOS sandbox capability: reported independently
Hermes Governance Graph: reported independently
```

Merge through GitHub PR，then update local `main` non-destructively。Never use `git reset --hard`、force push or history rewrite。

- [ ] **Step 6: Post-merge live verification**

Run:

```bash
git switch main
git pull --ff-only origin main
git status --short --branch
git log -3 --oneline --decorate
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/waitungchan-oss/nbs_analytics/branches/main/protection
```

Save the bounded GET response with `apply_patch` to runtime and re-run `verify`。Expected: local `main==origin/main`、worktree clean、live verification PASS。

## Execution checkpoint protocol for `gpt-5.6-luna medium`

For every Task：

1. Start from the Task's exact Context command；do not reopen unrelated repository files。
2. Keep Task scope to listed files and external endpoint only。
3. Use RED → minimal GREEN → focused verification → Review → checkpoint。
4. Stop after each checkpoint and report commit SHA、changed files、test result、Review verdict and remaining Task number。
5. If a blocker is caused by a bounded bug in listed files，repair within the same Task and repeat fresh verification；do not broaden into Graph、Memory、Agent orchestration、database or business-rule changes。
6. External PUT has no autonomous repair loop；any uncertain state requires GET reconciliation before another mutation。
