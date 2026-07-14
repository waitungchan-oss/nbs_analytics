# Agent Evidence Bundle Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 read-only Context Agent 與 Review Agent 的 Evidence Bundle Pipeline、CLI、fingerprint cache、Token telemetry 與 Codex 自動分派規則，在不改正式口徑、baseline、SQLite 或 Hermes 責任的前提下減少主 Codex 重複讀取上下文。

**Architecture:** 純 Python Collector 先從白名單文件、Git 與既有驗證結果生成受限 JSON bundle，再由可替換的 `AgentRunner` 將 bundle 交給 LLM。第一階段不綁定模型 SDK；Codex 可直接消費 `--collect-only` bundle，獨立 CLI 則可用受控 JSON stdin/stdout subprocess adapter。Context Agent 只壓縮修改前上下文，Review Agent 只審查 task/diff/test evidence，Hermes 保留正式 runtime、SQLite、baseline 與 post-change acceptance。

**Tech Stack:** Python 3.10、standard library `dataclasses/json/hashlib/pathlib/subprocess/uuid`、pytest、既有 Git CLI、既有 Hermes/System Manager；不新增 database、vector store、model SDK 或 Web endpoint。

## Global Constraints

- 正式營收口徑維持「不含掛賬核銷與TT退款轉團款」。
- 2026-05 frozen baseline 維持 `HKD 12,057,968`。
- Agent、Collector 與 CLI 不得讀取正式 SQLite rows、Excel 原始資料、generated exports、secrets 或完整 runtime logs。
- Agent 不得修改 tracked source、config、文件、Git index、正式 SQLite、runtime evidence、cache、backup 或 quarantine。
- Validation Runner 只可在 Git ignored 或 temporary path 產生 test/cache/build artifacts；執行前後 tracked worktree 必須一致。
- Context input 上限 12k estimated tokens、output 上限 1.5k；Review 單批 input 上限 16k、output 上限 2k。
- 第一階段不依賴外部模型 SDK；subprocess runner 必須使用 argv、`shell=False`、JSON stdin/stdout、timeout 與 executable allowlist。
- `--collect-only` 不調用 LLM，必須在無 API key、無網路環境可用。
- Review `pass` 不等於正式完成；full verification 與 Hermes acceptance 仍是必要 gate。
- 不把 Agent runtime 塞進 `app.py`、Streamlit pages、FastAPI routers 或 `scripts/hermes_post_change_check.py`。
- 所有新增 runtime artifacts 只保存在 `.nbs_agent_runtime/`，並加入 `.gitignore`。

## File Map

| Path | Responsibility |
|---|---|
| `agent_config/evidence_allowlist.json` | 可讀 roots/extensions、禁止 patterns、subprocess executable allowlist |
| `agent_config/token_budgets.json` | Context/Review input/output budget 與 excerpt limits |
| `agent_config/review_policies.json` | strict gate、risk keywords、required evidence policy |
| `backend/agents/evidence_models.py` | Evidence、report、fingerprint 與 schema validation types |
| `backend/agents/evidence_collector.py` | 安全 path resolve、Git/rg/文件證據收集與裁剪 |
| `backend/agents/agent_runtime.py` | Runner protocol、subprocess adapter、cache、telemetry |
| `backend/agents/context_agent_service.py` | Context bundle orchestration、prompt payload 與 output validation |
| `backend/agents/review_agent_service.py` | Review bundle orchestration、batching、strict PASS gate 與 output validation |
| `scripts/context_agent.py` | Context CLI、JSON/Markdown、collect-only、exit codes |
| `scripts/review_agent.py` | Review CLI、verification evidence、strict、exit codes |
| `docs/agents/CODEX_AGENT_DISPATCH.md` | 未來 Codex 可直接遵守的自動分派規則 |
| `AGENTS.md` | Repo-level 最小入口，連結 Agent contracts 與 dispatch rules |
| `tests/test_evidence_models.py` | Schema、canonical fingerprint、token estimate tests |
| `tests/test_evidence_collector.py` | allowlist、path escape、truncation、Git evidence tests |
| `tests/test_agent_runtime.py` | subprocess、cache、timeout、telemetry tests |
| `tests/test_context_agent_service.py` | Context status、budget、cache、invalid output tests |
| `tests/test_review_agent_service.py` | strict gate、findings、batch merge、dirty files tests |
| `tests/test_agent_cli.py` | 兩個 CLI 的 JSON、Markdown、exit code tests |
| `tests/test_agent_read_only_contract.py` | tracked worktree、DB/runtime unchanged integration tests |
| `scripts/hermes_post_change_check.py` | 只把 Agent tests 加入既有 targeted pack，不改 Hermes 職責 |
| `tests/test_hermes_post_change_check.py` | 固定 Hermes targeted pack 包含新增 Agent tests |

---

### Task 1: Evidence Models, Configuration And Runtime Isolation

狀態：verified

**Files:**
- Create: `backend/agents/__init__.py`
- Create: `backend/agents/evidence_models.py`
- Create: `agent_config/evidence_allowlist.json`
- Create: `agent_config/token_budgets.json`
- Create: `agent_config/review_policies.json`
- Modify: `.gitignore`
- Test: `tests/test_evidence_models.py`

**Interfaces:**
- Produces: `canonical_fingerprint(value: object) -> str`
- Produces: `estimate_tokens(text: str) -> int`
- Produces: `EvidenceItem`, `CommandEvidence`, `EvidenceBundle`, `AgentReportEnvelope`
- Produces: `load_json_config(project_root: Path, relative_path: str) -> dict`
- Consumed by: Tasks 2-5

- [ ] **Step 1: Write failing model and configuration tests**

```python
from pathlib import Path

import pytest

from backend.agents.evidence_models import (
    AgentReportEnvelope,
    EvidenceBundle,
    EvidenceItem,
    canonical_fingerprint,
    estimate_tokens,
    load_json_config,
)


def test_canonical_fingerprint_is_order_independent():
    assert canonical_fingerprint({"b": 2, "a": 1}) == canonical_fingerprint({"a": 1, "b": 2})


def test_estimate_tokens_uses_conservative_character_ratio():
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("中旅分析") == 4


def test_evidence_bundle_serializes_with_schema_and_fingerprint():
    bundle = EvidenceBundle(
        schema_version="context-evidence-v1",
        task={"id": "P3-2", "objective": "Build context"},
        repository={"branch": "main", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=(EvidenceItem(kind="document", source="docs/a.md", content="A"),),
    )
    payload = bundle.to_dict()
    assert payload["schemaVersion"] == "context-evidence-v1"
    assert payload["bundleFingerprint"] == bundle.fingerprint


def test_report_envelope_rejects_unknown_status():
    with pytest.raises(ValueError, match="Unsupported agent status"):
        AgentReportEnvelope(schema_version="context-summary-v1", status="invented", payload={})


def test_configs_are_valid_json_and_runtime_is_ignored():
    root = Path(__file__).resolve().parents[1]
    assert load_json_config(root, "agent_config/token_budgets.json")["context"]["inputTokens"] == 12000
    assert ".nbs_agent_runtime/" in (root / ".gitignore").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests and verify the red state**

Run:

```bash
.venv/bin/python -m pytest tests/test_evidence_models.py -q
```

Expected: collection FAIL because `backend.agents.evidence_models` does not exist.

- [ ] **Step 3: Add the configuration files**

`agent_config/evidence_allowlist.json`:

```json
{
  "schemaVersion": "evidence-allowlist-v1",
  "readRoots": ["docs", "Summay", "backend", "scripts", "tests"],
  "rootFiles": ["AGENTS.md", "NBS_ANALYTICS_HANDOFF.md", "NBS_ANALYTICS_SYSTEM_MAP.md", "NBS_HERMES_MONITORING.md", "requirements.txt"],
  "defaultContextFiles": ["AGENTS.md", "NBS_ANALYTICS_HANDOFF.md", "NBS_ANALYTICS_SYSTEM_MAP.md", "Summay/驗收基線.md"],
  "extensions": [".md", ".py", ".json", ".js", ".mjs", ".vue", ".txt"],
  "denyPatterns": [".env", "*.db", "*.sqlite", "*.xlsx", "*.xls", "*.csv", "*.pkl", "*.log", ".nbs_runtime/**", ".nbs_runtime_cache/**", "backups/**", "outputs/**", ".worktrees/**"],
  "agentExecutables": ["codex", "claude"]
}
```

`agent_config/token_budgets.json`:

```json
{
  "schemaVersion": "token-budgets-v1",
  "context": {"inputTokens": 12000, "outputTokens": 1500},
  "review": {"inputTokens": 16000, "outputTokens": 2000},
  "excerpt": {"maxFileLines": 120, "symbolContextLines": 20, "maxCommandCharacters": 12000}
}
```

`agent_config/review_policies.json`:

```json
{
  "schemaVersion": "review-policies-v1",
  "strictRequiresVerification": true,
  "riskKeywords": ["upload", "upsert", "rollback", "baseline", "revenue", "business_rules", "cache_generation", "export"],
  "requiredHermesChecks": ["system-acceptance", "system-monitor", "phase2-baseline", "monthly-baseline-governance"],
  "allowedVerdicts": ["pass", "changes_required", "blocked", "context_overflow", "invalid_bundle"],
  "allowedSeverities": ["critical", "high", "medium", "low"]
}
```

Append to `.gitignore`:

```gitignore

# Local Agent evidence, reports and token telemetry
.nbs_agent_runtime/
```

- [ ] **Step 4: Implement immutable evidence models and helpers**

`backend/agents/evidence_models.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any


ALLOWED_CONTEXT_STATUSES = {
    "ready", "blocked_missing_brief", "blocked_missing_evidence",
    "dirty_worktree", "context_overflow", "invalid_bundle",
}
ALLOWED_REVIEW_STATUSES = {
    "pass", "changes_required", "blocked", "context_overflow", "invalid_bundle",
}


def canonical_fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def estimate_tokens(text: str) -> int:
    value = str(text or "")
    non_ascii = sum(1 for character in value if ord(character) > 127)
    ascii_count = len(value) - non_ascii
    return non_ascii + ((ascii_count + 3) // 4)


def load_json_config(project_root: Path, relative_path: str) -> dict:
    return json.loads((project_root / relative_path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class EvidenceItem:
    kind: str
    source: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "source": self.source, "content": self.content, "metadata": self.metadata}


@dataclass(frozen=True)
class CommandEvidence:
    label: str
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "label": self.label, "argv": list(self.argv), "exitCode": self.exit_code,
            "stdout": self.stdout, "stderr": self.stderr, "truncated": self.truncated,
        }


@dataclass(frozen=True)
class EvidenceBundle:
    schema_version: str
    task: dict
    repository: dict
    guardrails: dict
    evidence: tuple[EvidenceItem, ...] = ()
    commands: tuple[CommandEvidence, ...] = ()

    def unsigned_dict(self) -> dict:
        return {
            "schemaVersion": self.schema_version,
            "task": self.task,
            "repository": self.repository,
            "guardrails": self.guardrails,
            "evidence": [item.to_dict() for item in self.evidence],
            "commands": [item.to_dict() for item in self.commands],
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.unsigned_dict())

    def to_dict(self) -> dict:
        return {**self.unsigned_dict(), "bundleFingerprint": self.fingerprint}

@dataclass(frozen=True)
class AgentReportEnvelope:
    schema_version: str
    status: str
    payload: dict

    def __post_init__(self) -> None:
        allowed = ALLOWED_CONTEXT_STATUSES | ALLOWED_REVIEW_STATUSES
        if self.status not in allowed:
            raise ValueError(f"Unsupported agent status: {self.status}")

    def to_dict(self) -> dict:
        return {"schemaVersion": self.schema_version, "status": self.status, **self.payload}
```

Create an empty `backend/agents/__init__.py`.

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_evidence_models.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add .gitignore agent_config backend/agents tests/test_evidence_models.py
git commit -m "feat: add agent evidence models and policies"
```

---

### Task 2: Safe Evidence Collector

狀態：verified

**Files:**
- Create: `backend/agents/evidence_collector.py`
- Test: `tests/test_evidence_collector.py`

**Interfaces:**
- Consumes: Task 1 `EvidenceBundle`, `EvidenceItem`, `CommandEvidence`, configs
- Produces: `EvidencePolicy.from_project(project_root: Path) -> EvidencePolicy`
- Produces: `EvidenceCollector.collect_context(brief_path: Path, base_ref: str) -> EvidenceBundle`
- Produces: `EvidenceCollector.collect_review(brief_path: Path, base_ref: str, head_ref: str) -> EvidenceBundle`
- Produces: `EvidencePolicy.resolve_read_path(path: Path) -> Path`
- Consumed by: Tasks 4-5

- [ ] **Step 1: Write failing collector security and truncation tests**

```python
import subprocess
from pathlib import Path

import pytest

from backend.agents.evidence_collector import EvidenceCollector, EvidencePolicy


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def write_configs(root: Path) -> None:
    (root / "agent_config").mkdir()
    (root / "agent_config/evidence_allowlist.json").write_text(
        '{"readRoots":["docs","backend","tests"],"rootFiles":["AGENTS.md"],'
        '"extensions":[".md",".py"],"denyPatterns":["*.db",".env","outputs/**"],'
        '"agentExecutables":["codex"]}', encoding="utf-8"
    )
    (root / "agent_config/token_budgets.json").write_text(
        '{"context":{"inputTokens":12000,"outputTokens":1500},'
        '"review":{"inputTokens":16000,"outputTokens":2000},'
        '"excerpt":{"maxFileLines":5,"symbolContextLines":2,"maxCommandCharacters":200}}',
        encoding="utf-8",
    )


def test_policy_rejects_path_escape_and_denied_data(tmp_path):
    write_configs(tmp_path)
    policy = EvidencePolicy.from_project(tmp_path)
    with pytest.raises(PermissionError):
        policy.resolve_read_path(tmp_path.parent / "outside.md")
    denied = tmp_path / "secret.db"
    denied.write_text("x", encoding="utf-8")
    with pytest.raises(PermissionError):
        policy.resolve_read_path(denied)


def test_context_collection_truncates_documents_and_never_reads_db(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("\n".join(f"line-{i}" for i in range(20)), encoding="utf-8")
    (tmp_path / "secret.db").write_text("formal rows", encoding="utf-8")
    subprocess.run(["git", "add", "docs/brief.md", "agent_config"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    bundle = EvidenceCollector(tmp_path).collect_context(brief, base_ref="HEAD")

    document = next(item for item in bundle.evidence if item.source == "docs/brief.md")
    assert len(document.content.splitlines()) == 5
    assert "formal rows" not in str(bundle.to_dict())
    assert bundle.repository["head"]


def test_context_collection_includes_only_allowlisted_explicit_and_query_matches(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "backend").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("snapshot objective", encoding="utf-8")
    related = tmp_path / "backend/snapshot.py"
    related.write_text("def build_snapshot():\n    return 'snapshot'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    bundle = EvidenceCollector(tmp_path).collect_context(
        brief, base_ref="HEAD", include_paths=(related,), queries=("build_snapshot",),
    )

    sources = [item.source for item in bundle.evidence]
    assert "backend/snapshot.py" in sources
    assert any(item.label.startswith("rg-query-") for item in bundle.commands)


def test_review_collection_uses_argv_and_captures_changed_files(tmp_path):
    init_repo(tmp_path)
    write_configs(tmp_path)
    (tmp_path / "docs").mkdir()
    brief = tmp_path / "docs/brief.md"
    brief.write_text("objective", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    brief.write_text("objective changed", encoding="utf-8")

    bundle = EvidenceCollector(tmp_path).collect_review(brief, base_ref="HEAD", head_ref="WORKTREE")

    assert bundle.repository["dirtyFiles"] == ["docs/brief.md"]
    assert any(item.label.startswith("git-diff-file-") for item in bundle.commands)
    changed = next(item for item in bundle.commands if item.label == "git-diff-name-only")
    assert changed.argv[:3] == ("git", "diff", "--name-only")
```

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_evidence_collector.py -q
```

Expected: collection FAIL because `evidence_collector.py` does not exist.

- [ ] **Step 3: Implement policy-enforced collection**

Implement `backend/agents/evidence_collector.py` with these exact public shapes:

```python
from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.agents.evidence_models import CommandEvidence, EvidenceBundle, EvidenceItem, load_json_config


@dataclass(frozen=True)
class EvidencePolicy:
    project_root: Path
    read_roots: tuple[str, ...]
    root_files: tuple[str, ...]
    default_context_files: tuple[str, ...]
    extensions: tuple[str, ...]
    deny_patterns: tuple[str, ...]
    max_file_lines: int
    max_command_characters: int
    agent_executables: tuple[str, ...]

    @classmethod
    def from_project(cls, project_root: Path) -> "EvidencePolicy":
        root = project_root.resolve()
        allow = load_json_config(root, "agent_config/evidence_allowlist.json")
        budgets = load_json_config(root, "agent_config/token_budgets.json")
        excerpt = budgets["excerpt"]
        return cls(
            project_root=root,
            read_roots=tuple(allow["readRoots"]),
            root_files=tuple(allow["rootFiles"]),
            default_context_files=tuple(allow.get("defaultContextFiles") or ()),
            extensions=tuple(allow["extensions"]),
            deny_patterns=tuple(allow["denyPatterns"]),
            max_file_lines=int(excerpt["maxFileLines"]),
            max_command_characters=int(excerpt["maxCommandCharacters"]),
            agent_executables=tuple(allow["agentExecutables"]),
        )

    def resolve_read_path(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise PermissionError(f"Path is outside project root: {path}") from exc
        relative_text = relative.as_posix()
        if any(fnmatch.fnmatch(relative_text, pattern) or fnmatch.fnmatch(resolved.name, pattern) for pattern in self.deny_patterns):
            raise PermissionError(f"Denied evidence path: {relative_text}")
        top = relative.parts[0] if relative.parts else ""
        allowed = relative_text in self.root_files or top in self.read_roots
        if not allowed or resolved.suffix not in self.extensions:
            raise PermissionError(f"Path is not allowlisted: {relative_text}")
        return resolved


class EvidenceCollector:
    def __init__(self, project_root: Path, *, policy: EvidencePolicy | None = None) -> None:
        self.project_root = project_root.resolve()
        self.policy = policy or EvidencePolicy.from_project(self.project_root)

    def _run(self, label: str, argv: list[str]) -> CommandEvidence:
        completed = subprocess.run(
            argv, cwd=self.project_root, text=True, capture_output=True,
            timeout=60, check=False, shell=False,
        )
        limit = self.policy.max_command_characters
        return CommandEvidence(
            label=label, argv=tuple(argv), exit_code=completed.returncode,
            stdout=completed.stdout[:limit], stderr=completed.stderr[:limit],
            truncated=len(completed.stdout) > limit or len(completed.stderr) > limit,
        )

    def _document(self, path: Path) -> EvidenceItem:
        resolved = self.policy.resolve_read_path(path)
        relative = resolved.relative_to(self.project_root).as_posix()
        lines = resolved.read_text(encoding="utf-8").splitlines()
        selected = lines[: self.policy.max_file_lines]
        return EvidenceItem(
            kind="document", source=relative, content="\n".join(selected),
            metadata={"lineCount": len(lines), "truncated": len(lines) > len(selected)},
        )

    def _query_paths(self, queries: tuple[str, ...]) -> tuple[tuple[Path, ...], tuple[CommandEvidence, ...]]:
        found: list[Path] = []
        commands: list[CommandEvidence] = []
        roots = [root for root in self.policy.read_roots if (self.project_root / root).exists()]
        for index, query in enumerate(queries[:8]):
            result = self._run(
                f"rg-query-{index}",
                ["rg", "--files-with-matches", "--fixed-strings", "--", query, *roots],
            )
            commands.append(result)
            for line in result.stdout.splitlines():
                candidate = self.project_root / line
                try:
                    resolved = self.policy.resolve_read_path(candidate)
                except PermissionError:
                    continue
                if resolved not in found:
                    found.append(resolved)
                if len(found) >= 12:
                    break
        return tuple(found), tuple(commands)

    def _repository(self) -> tuple[dict, tuple[CommandEvidence, ...]]:
        head = self._run("git-head", ["git", "rev-parse", "HEAD"])
        branch = self._run("git-branch", ["git", "branch", "--show-current"])
        status = self._run("git-status", ["git", "status", "--porcelain"])
        recent = self._run("git-log", ["git", "log", "-5", "--oneline"])
        dirty = sorted(line[3:] for line in status.stdout.splitlines() if len(line) > 3)
        return {
            "branch": branch.stdout.strip(), "head": head.stdout.strip(), "dirtyFiles": dirty,
        }, (head, branch, status, recent)

    def collect_context(
        self,
        brief_path: Path,
        base_ref: str = "main",
        *,
        include_paths: tuple[Path, ...] = (),
        queries: tuple[str, ...] = (),
    ) -> EvidenceBundle:
        repository, commands = self._repository()
        base = self._run("git-base", ["git", "rev-parse", base_ref])
        query_paths, query_commands = self._query_paths(queries)
        default_paths = [
            self.project_root / relative
            for relative in self.policy.default_context_files
            if (self.project_root / relative).is_file()
        ]
        selected_paths = [brief_path, *default_paths, *include_paths, *query_paths]
        unique_paths = tuple(dict.fromkeys(path.resolve() for path in selected_paths))
        evidence = tuple(self._document(path) for path in unique_paths)
        task = {"id": brief_path.stem, "objective": evidence[0].content, "scope": [], "forbidden": []}
        return EvidenceBundle(
            schema_version="context-evidence-v1", task=task, repository=repository,
            guardrails={"revenueScope": "不含掛賬核銷與TT退款轉團款", "mayBaseline": "HKD 12,057,968"},
            evidence=evidence, commands=commands + (base,) + query_commands,
        )

    def collect_review(self, brief_path: Path, base_ref: str = "main", head_ref: str = "WORKTREE") -> EvidenceBundle:
        repository, commands = self._repository()
        diff_range = base_ref if head_ref == "WORKTREE" else f"{base_ref}...{head_ref}"
        changed = self._run("git-diff-name-only", ["git", "diff", "--name-only", diff_range])
        patches: list[EvidenceItem] = []
        patch_commands: list[CommandEvidence] = []
        for index, relative in enumerate(changed.stdout.splitlines()[:50]):
            if relative.startswith("/") or ".." in Path(relative).parts:
                raise PermissionError(f"Unsafe changed path: {relative}")
            patch = self._run(
                f"git-diff-file-{index}",
                ["git", "diff", diff_range, "--", relative],
            )
            patch_commands.append(patch)
            patches.append(EvidenceItem(
                kind="diff", source=relative, content=patch.stdout,
                metadata={"truncated": patch.truncated},
            ))
        return EvidenceBundle(
            schema_version="review-evidence-v1",
            task={"id": brief_path.stem, "objective": self._document(brief_path).content, "scope": [], "forbidden": []},
            repository={
                **repository, "base": base_ref, "headRef": head_ref,
                "diffFileLimitExceeded": changed.truncated or len(changed.stdout.splitlines()) > 50,
            },
            guardrails={"revenueScope": "不含掛賬核銷與TT退款轉團款", "mayBaseline": "HKD 12,057,968"},
            evidence=tuple(patches),
            commands=commands + (changed, *patch_commands),
        )
```

Do not accept arbitrary caller-supplied argv anywhere.

- [ ] **Step 4: Run focused tests and security spot checks**

Run:

```bash
.venv/bin/python -m pytest tests/test_evidence_collector.py -q
.venv/bin/python -c "from pathlib import Path; from backend.agents.evidence_collector import EvidencePolicy; p=EvidencePolicy.from_project(Path('.')); print(p.resolve_read_path(Path('docs/agents/NBS_AGENT_ARCHITECTURE.md')))"
```

Expected: tests PASS; spot check prints the resolved design document path. A manual attempt to resolve `nbs_marketing_data.db` must raise `PermissionError`.

- [ ] **Step 5: Commit Task 2**

```bash
git add backend/agents/evidence_collector.py tests/test_evidence_collector.py
git commit -m "feat: collect allowlisted agent evidence"
```

---

### Task 3: Agent Runner, Fingerprint Cache And Telemetry

狀態：verified

**Files:**
- Create: `backend/agents/agent_runtime.py`
- Test: `tests/test_agent_runtime.py`

**Interfaces:**
- Consumes: Task 1 fingerprints/configs and Task 2 policy executable allowlist
- Produces: `AgentRunner` protocol
- Produces: `SubprocessAgentRunner(argv, allowed_executables, timeout_seconds).run(payload) -> dict`
- Produces: `agent_request_fingerprint(bundle, instructions, output_schema, evidence_payload=None) -> str`
- Produces: `resolve_runtime_output_path(project_root, raw_path) -> Path`
- Produces: `AgentRuntime.run(agent_name, bundle, runner, output_schema, instructions, evidence_payload=None) -> dict`
- Produces: local cache/report/telemetry under `.nbs_agent_runtime/`
- Consumed by: Tasks 4-5

- [ ] **Step 1: Write failing runtime tests**

```python
import json
import sys
from pathlib import Path

import pytest

from backend.agents.agent_runtime import AgentRuntime, SubprocessAgentRunner, resolve_runtime_output_path
from backend.agents.evidence_models import EvidenceBundle


def bundle() -> EvidenceBundle:
    return EvidenceBundle(
        schema_version="context-evidence-v1",
        task={"id": "x", "objective": "x"},
        repository={"branch": "main", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
    )


def test_subprocess_runner_rejects_unapproved_executable():
    with pytest.raises(PermissionError, match="executable"):
        SubprocessAgentRunner(["bash", "-c", "cat"], allowed_executables=("codex",))


def test_runtime_caches_same_fingerprint_and_writes_telemetry(tmp_path):
    script = tmp_path / "agent.py"
    script.write_text(
        "import json,sys; p=json.load(sys.stdin); "
        "print(json.dumps({'schemaVersion':'context-summary-v1','status':'ready',"
        "'taskUnderstanding':['ok'],'contextFingerprint':p['bundleFingerprint']}))",
        encoding="utf-8",
    )
    runner = SubprocessAgentRunner([sys.executable, str(script)], allowed_executables=(Path(sys.executable).name,))
    runtime = AgentRuntime(tmp_path / ".nbs_agent_runtime")

    first = runtime.run("context", bundle(), runner, output_schema="context-summary-v1", instructions="contract-v1")
    second = runtime.run("context", bundle(), runner, output_schema="context-summary-v1", instructions="contract-v1")

    changed = EvidenceBundle(
        schema_version="context-evidence-v1",
        task={"id": "x", "objective": "changed"},
        repository={"branch": "main", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
    )
    runtime.run("context", changed, runner, output_schema="context-summary-v1", instructions="contract-v1")
    runtime.run("context", changed, runner, output_schema="context-summary-v1", instructions="contract-v2")

    assert first == second
    lines = (tmp_path / ".nbs_agent_runtime/telemetry/agent_runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[1])["cacheHit"] is True
    assert json.loads(lines[2])["cacheHit"] is False
    assert json.loads(lines[3])["cacheHit"] is False


def test_runner_rejects_non_json_output(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text("print('not-json')", encoding="utf-8")
    runner = SubprocessAgentRunner([sys.executable, str(script)], allowed_executables=(Path(sys.executable).name,))
    with pytest.raises(ValueError, match="valid JSON"):
        runner.run({"bundleFingerprint": "x"})


def test_output_path_must_stay_inside_agent_runtime(tmp_path):
    allowed = resolve_runtime_output_path(tmp_path, ".nbs_agent_runtime/reports/context.json")
    assert allowed == (tmp_path / ".nbs_agent_runtime/reports/context.json").resolve()
    with pytest.raises(PermissionError, match="Agent output"):
        resolve_runtime_output_path(tmp_path, "docs/context.json")
```

- [ ] **Step 2: Run tests and verify failure**

```bash
.venv/bin/python -m pytest tests/test_agent_runtime.py -q
```

Expected: collection FAIL because `agent_runtime.py` does not exist.

- [ ] **Step 3: Implement the runner and runtime**

Implement:

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from backend.agents.evidence_models import EvidenceBundle, canonical_fingerprint, estimate_tokens


class AgentRunner(Protocol):
    def run(self, payload: dict) -> dict: ...


def resolve_runtime_output_path(project_root: Path, raw_path: str) -> Path:
    root = (project_root / ".nbs_agent_runtime").resolve()
    candidate = Path(raw_path)
    resolved = (project_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"Agent output must stay under {root}") from exc
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def agent_request_fingerprint(
    bundle: EvidenceBundle,
    *,
    instructions: str,
    output_schema: str,
    evidence_payload: dict | None = None,
) -> str:
    public_evidence = evidence_payload or bundle.to_dict()
    return canonical_fingerprint({
        "sourceBundleFingerprint": bundle.fingerprint,
        "publicEvidenceFingerprint": canonical_fingerprint(public_evidence),
        "instructions": instructions,
        "outputSchema": output_schema,
    })


class SubprocessAgentRunner:
    def __init__(self, argv: list[str], *, allowed_executables: tuple[str, ...], timeout_seconds: int = 120) -> None:
        if not argv:
            raise ValueError("Agent command cannot be empty")

        def resolve_executable(value: str) -> Path:
            path = Path(value)
            if path.is_absolute():
                if not path.is_file():
                    raise FileNotFoundError(value)
                return path.resolve()
            found = shutil.which(value)
            if not found:
                raise FileNotFoundError(value)
            return Path(found).resolve()

        executable = resolve_executable(argv[0])
        allowed: set[Path] = set()
        for value in allowed_executables:
            try:
                allowed.add(resolve_executable(value))
            except FileNotFoundError:
                continue
        if executable not in allowed:
            raise PermissionError(f"Agent executable is not allowlisted: {executable}")
        self.argv = (str(executable), *argv[1:])
        self.timeout_seconds = timeout_seconds

    def run(self, payload: dict) -> dict:
        completed = subprocess.run(
            list(self.argv), input=json.dumps(payload, ensure_ascii=False),
            text=True, capture_output=True, timeout=self.timeout_seconds,
            check=False, shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Agent command failed with exit {completed.returncode}: {completed.stderr[:1000]}")
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("Agent output is not valid JSON") from exc
        if not isinstance(result, dict):
            raise ValueError("Agent output must be a JSON object")
        return result


class AgentRuntime:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root

    def _paths(self, agent_name: str, fingerprint: str) -> tuple[Path, Path]:
        report = self.runtime_root / "reports" / f"{agent_name}-{fingerprint}.json"
        telemetry = self.runtime_root / "telemetry" / "agent_runs.jsonl"
        report.parent.mkdir(parents=True, exist_ok=True)
        telemetry.parent.mkdir(parents=True, exist_ok=True)
        return report, telemetry

    def run(
        self,
        agent_name: str,
        bundle: EvidenceBundle,
        runner: AgentRunner,
        *,
        output_schema: str,
        instructions: str,
        evidence_payload: dict | None = None,
    ) -> dict:
        public_evidence = evidence_payload or bundle.to_dict()
        request_fingerprint = agent_request_fingerprint(
            bundle, instructions=instructions, output_schema=output_schema,
            evidence_payload=public_evidence,
        )
        payload = {
            "contractVersion": output_schema,
            "instructions": instructions,
            "evidence": public_evidence,
            "sourceBundleFingerprint": bundle.fingerprint,
            "bundleFingerprint": request_fingerprint,
        }
        report_path, telemetry_path = self._paths(agent_name, request_fingerprint)
        started = perf_counter()
        cache_hit = report_path.exists()
        if cache_hit:
            result = json.loads(report_path.read_text(encoding="utf-8"))
        else:
            result = runner.run(payload)
            if result.get("schemaVersion") != output_schema:
                raise ValueError(f"Unexpected agent schema: {result.get('schemaVersion')}")
            report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        telemetry = {
            "runId": uuid4().hex, "agent": agent_name,
            "bundleFingerprint": bundle.fingerprint, "requestFingerprint": request_fingerprint,
            "inputCharacters": len(json.dumps(payload, ensure_ascii=False)),
            "estimatedInputTokens": estimate_tokens(json.dumps(payload, ensure_ascii=False)),
            "outputTokens": estimate_tokens(json.dumps(result, ensure_ascii=False)),
            "filesConsidered": len(bundle.evidence), "filesIncluded": len(bundle.evidence),
            "cacheHit": cache_hit, "durationMs": round((perf_counter() - started) * 1000, 3),
            "result": result.get("status") or result.get("verdict"),
        }
        with telemetry_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(telemetry, ensure_ascii=False) + "\n")
        return result
```

When tests require Python as a fake runner, inject its basename only in the test-specific allowlist; production config remains `codex/claude`. Hermes is intentionally excluded so the general Agent adapter cannot blur Hermes acceptance responsibilities.

- [ ] **Step 4: Run runtime tests**

```bash
.venv/bin/python -m pytest tests/test_agent_runtime.py -q
```

Expected: PASS, including timeout/non-JSON/executable rejection cases.

- [ ] **Step 5: Commit Task 3**

```bash
git add backend/agents/agent_runtime.py tests/test_agent_runtime.py
git commit -m "feat: add cached agent runtime telemetry"
```

---

### Task 4: Context Agent Service And CLI

狀態：verified

**Files:**
- Create: `backend/agents/context_agent_service.py`
- Create: `scripts/context_agent.py`
- Test: `tests/test_context_agent_service.py`
- Test: `tests/test_agent_cli.py`

**Interfaces:**
- Consumes: Tasks 1-3 Collector, runtime and budgets
- Produces: `build_context_evidence_payload(bundle: EvidenceBundle) -> dict`
- Produces: `context_bundle_from_payload(payload: dict) -> EvidenceBundle`
- Produces: `build_context_report(bundle, runner, runtime_root, instructions, collect_only=False) -> dict`
- Produces: `format_context_markdown(report: dict) -> str`
- Produces CLI flags: `--brief`, `--base`, `--bundle`, `--include`, `--query`, `--collect-only`, `--agent-command`, `--format`, `--output`
- Exit codes: ready 0; blocked/missing 2; policy 3; overflow 4; runtime/schema 5

- [ ] **Step 1: Write failing Context service tests**

```python
from pathlib import Path

import pytest

from backend.agents.context_agent_service import build_context_report
from backend.agents.evidence_models import EvidenceBundle, EvidenceItem


class FakeRunner:
    last_payload = None

    def run(self, payload):
        self.last_payload = payload
        return {
            "schemaVersion": "context-summary-v1",
            "status": "ready",
            "taskUnderstanding": ["approved objective"],
            "systemBoundaries": ["baseline unchanged"],
            "relevantFiles": [], "dependencies": [], "recommendedTests": [],
            "risks": [], "unknowns": [],
            "contextFingerprint": payload["bundleFingerprint"],
        }


def make_bundle(content="short"):
    return EvidenceBundle(
        schema_version="context-evidence-v1",
        task={"id": "x", "objective": "approved objective", "scope": [], "forbidden": []},
        repository={"branch": "main", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=(EvidenceItem(kind="document", source="docs/x.md", content=content),),
    )


def test_context_report_accepts_valid_runner_output(tmp_path):
    runner = FakeRunner()
    report = build_context_report(
        make_bundle(), runner=runner, runtime_root=tmp_path,
        instructions="context-contract-v1",
    )
    assert report["status"] == "ready"
    assert report["contextFingerprint"]
    assert set(runner.last_payload["evidence"]) >= {
        "schemaVersion", "task", "repository", "guardrails", "documents",
        "symbols", "relatedTests", "recentChanges", "bundleFingerprint",
    }


def test_context_report_returns_overflow_before_runner(tmp_path):
    report = build_context_report(
        make_bundle("x" * 60000), runner=FakeRunner(), runtime_root=tmp_path,
        instructions="context-contract-v1", input_token_limit=10,
    )
    assert report["status"] == "context_overflow"


def test_collect_only_returns_bundle_without_runner(tmp_path):
    report = build_context_report(
        make_bundle(), runner=None, runtime_root=tmp_path,
        instructions="context-contract-v1", collect_only=True,
    )
    assert report["schemaVersion"] == "context-evidence-v1"
    assert report["bundleFingerprint"]


def test_context_report_rejects_output_over_budget(tmp_path):
    class VerboseRunner(FakeRunner):
        def run(self, payload):
            report = super().run(payload)
            report["taskUnderstanding"] = ["x" * 1000]
            return report

    with pytest.raises(ValueError, match="output token budget"):
        build_context_report(
            make_bundle(), runner=VerboseRunner(), runtime_root=tmp_path,
            instructions="context-contract-v1", output_token_limit=10,
        )
```

- [ ] **Step 2: Add failing CLI tests to `tests/test_agent_cli.py`**

```python
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"


def test_context_cli_collect_only_outputs_json():
    result = subprocess.run(
        [str(PYTHON), "scripts/context_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--collect-only"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["schemaVersion"] == "context-evidence-v1"


def test_context_cli_missing_brief_exits_two():
    result = subprocess.run(
        [str(PYTHON), "scripts/context_agent.py", "--brief", "docs/missing.md", "--collect-only"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2


def test_context_cli_rejects_output_outside_agent_runtime():
    forbidden = ROOT / "docs/context-output.json"
    result = subprocess.run(
        [str(PYTHON), "scripts/context_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--collect-only", "--output", "docs/context-output.json"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 3
    assert not forbidden.exists()
```

- [ ] **Step 3: Run focused tests and verify failure**

```bash
.venv/bin/python -m pytest tests/test_context_agent_service.py tests/test_agent_cli.py -q
```

Expected: collection/import FAIL.

- [ ] **Step 4: Implement Context service validation and Markdown rendering**

`context_agent_service.py` must:

```python
def build_context_evidence_payload(bundle: EvidenceBundle) -> dict:
    unsigned = {
        "schemaVersion": "context-evidence-v1",
        "task": bundle.task,
        "repository": bundle.repository,
        "guardrails": bundle.guardrails,
        "documents": [
            item.to_dict() for item in bundle.evidence
            if item.kind == "document" and not item.source.startswith("tests/")
        ],
        "symbols": [
            {"queryId": item.label, "paths": item.stdout.splitlines()}
            for item in bundle.commands if item.label.startswith("rg-query-")
        ],
        "relatedTests": [
            item.to_dict() for item in bundle.evidence if item.source.startswith("tests/")
        ],
        "recentChanges": [
            {"summary": line}
            for item in bundle.commands if item.label == "git-log"
            for line in item.stdout.splitlines()
        ],
    }
    return {**unsigned, "bundleFingerprint": canonical_fingerprint(unsigned)}


def context_bundle_from_payload(payload: dict) -> EvidenceBundle:
    unsigned = {key: value for key, value in payload.items() if key != "bundleFingerprint"}
    if canonical_fingerprint(unsigned) != payload.get("bundleFingerprint"):
        raise ValueError("Context evidence fingerprint does not match payload")
    documents = [
        EvidenceItem(
            kind=str(item.get("kind") or "document"), source=str(item["source"]),
            content=str(item["content"]), metadata=dict(item.get("metadata") or {}),
        )
        for item in [*payload.get("documents", []), *payload.get("relatedTests", [])]
    ]
    semantic = [
        EvidenceItem(kind="symbol", source=str(item["queryId"]), content=json.dumps(item, ensure_ascii=False))
        for item in payload.get("symbols", [])
    ] + [
        EvidenceItem(kind="recent_change", source="git-log", content=str(item["summary"]))
        for item in payload.get("recentChanges", [])
    ]
    return EvidenceBundle(
        schema_version="context-evidence-v1", task=dict(payload["task"]),
        repository=dict(payload["repository"]), guardrails=dict(payload["guardrails"]),
        evidence=tuple([*documents, *semantic]),
    )


def build_context_report(
    bundle: EvidenceBundle,
    *,
    runner: AgentRunner | None,
    runtime_root: Path,
    instructions: str,
    collect_only: bool = False,
    input_token_limit: int = 12000,
    output_token_limit: int = 1500,
) -> dict:
    payload = build_context_evidence_payload(bundle)
    if collect_only:
        return payload
    request_payload = {"instructions": instructions, "evidence": payload}
    expected_fingerprint = agent_request_fingerprint(
        bundle, instructions=instructions, output_schema="context-summary-v1",
        evidence_payload=payload,
    )
    if estimate_tokens(json.dumps(request_payload, ensure_ascii=False)) > input_token_limit:
        return {
            "schemaVersion": "context-summary-v1",
            "status": "context_overflow",
            "unknowns": ["Collector must reduce evidence before LLM dispatch."],
            "contextFingerprint": expected_fingerprint,
        }
    if runner is None:
        return {
            "schemaVersion": "context-summary-v1",
            "status": "blocked_missing_evidence",
            "unknowns": ["No AgentRunner was configured; use --collect-only or --agent-command."],
            "contextFingerprint": expected_fingerprint,
        }
    result = AgentRuntime(runtime_root).run(
        "context", bundle, runner,
        output_schema="context-summary-v1", instructions=instructions,
        evidence_payload=payload,
    )
    if estimate_tokens(json.dumps(result, ensure_ascii=False)) > output_token_limit:
        raise ValueError("Context Agent output token budget exceeded")
    required = {"status", "taskUnderstanding", "systemBoundaries", "relevantFiles", "recommendedTests", "risks", "unknowns", "contextFingerprint"}
    missing = sorted(required - result.keys())
    if missing or result.get("contextFingerprint") != expected_fingerprint:
        raise ValueError(f"Invalid Context Agent report; missing={missing}")
    return result
```

`format_context_markdown()` must render status, task understanding, boundaries, relevant files, tests, risks and unknowns without embedding the original bundle.

- [ ] **Step 5: Implement the Context CLI**

The CLI must insert `PROJECT_ROOT` into `sys.path`, load `docs/agents/CONTEXT_AGENT_CONTRACT.md` as the `instructions` argument, parse `--agent-command` with `shlex.split`, verify its executable through `EvidencePolicy.agent_executables`, and never use `shell=True`.

```python
def exit_code_for_status(status: str) -> int:
    if status == "ready" or status is None:
        return 0
    if status in {"blocked_missing_brief", "blocked_missing_evidence", "dirty_worktree"}:
        return 2
    if status == "context_overflow":
        return 4
    return 5
```

The CLI catches `PermissionError` from policy or `resolve_runtime_output_path()` and exits 3 without writing output. `--bundle` uses `context_bundle_from_payload()` and is mutually exclusive with `--brief`. Repeatable `--include` paths and `--query` fixed strings are forwarded only to the Collector methods above. `--output` is accepted only when its resolved path is below `<project>/.nbs_agent_runtime/`; absence of `--output` prints to stdout. `--collect-only` must not require `--agent-command`.

- [ ] **Step 6: Run Context tests and CLI smoke checks**

```bash
.venv/bin/python -m pytest tests/test_context_agent_service.py tests/test_agent_cli.py -q
.venv/bin/python scripts/context_agent.py --brief docs/agents/NBS_AGENT_ARCHITECTURE.md --collect-only > /tmp/nbs-context-bundle.json
.venv/bin/python -m json.tool /tmp/nbs-context-bundle.json >/dev/null
```

Expected: tests PASS; JSON tool exits 0; no `.db`, `.xlsx`, `.env` or runtime log content appears in the bundle.

- [ ] **Step 7: Commit Task 4**

```bash
git add backend/agents/context_agent_service.py scripts/context_agent.py tests/test_context_agent_service.py tests/test_agent_cli.py
git commit -m "feat: add context evidence agent cli"
```

---

### Task 5: Review Agent, Verification Evidence And Strict PASS Gate

狀態：verified

**Files:**
- Create: `backend/agents/review_agent_service.py`
- Create: `scripts/review_agent.py`
- Modify: `tests/test_agent_cli.py`
- Test: `tests/test_review_agent_service.py`

**Interfaces:**
- Consumes: Tasks 1-4 models, Collector and runtime
- Produces: `build_review_evidence_payload(bundle, context_summary, verification) -> dict`
- Produces: `build_review_report(bundle, context_summary, verification, runner, runtime_root, instructions, strict=True) -> dict`
- Produces: `split_review_bundle_by_file(bundle, patch_token_budget) -> tuple[EvidenceBundle, ...]`
- Produces: `run_review_batches(bundle, context_summary, verification, runner, runtime_root, instructions, strict=True) -> dict`
- Produces: `merge_review_batches(reports: list[dict], fingerprint: str) -> dict`
- Produces: `format_review_markdown(report: dict) -> str`
- CLI flags: `--brief`, `--base`, `--head`, `--context`, `--verification`, `--collect-only`, `--agent-command`, `--strict`, `--format`, `--output`
- Exit codes: pass/collect 0; changes required 1; blocked 2; policy 3; overflow 4; runtime/schema 5

- [ ] **Step 1: Write failing Review service tests**

```python
from pathlib import Path

import pytest

from backend.agents.evidence_models import EvidenceBundle, EvidenceItem
from backend.agents.review_agent_service import (
    build_review_report,
    merge_review_batches,
    split_review_bundle_by_file,
)


class ReviewRunner:
    def __init__(self, verdict="pass", findings=None):
        self.verdict = verdict
        self.findings = findings or []
        self.last_payload = None

    def run(self, payload):
        self.last_payload = payload
        return {
            "schemaVersion": "review-report-v1",
            "verdict": self.verdict,
            "findings": self.findings,
            "requirementCoverage": ["objective"],
            "testCoverage": ["targeted: passed"],
            "baselineRisk": "none",
            "residualRisk": ["Hermes pending"],
            "hermesRequiredChecks": ["phase2-baseline"],
            "reviewFingerprint": payload["bundleFingerprint"],
        }


def review_bundle(dirty=None):
    return EvidenceBundle(
        schema_version="review-evidence-v1",
        task={"id": "x", "objective": "approved", "scope": ["backend"], "forbidden": []},
        repository={"branch": "feature", "head": "abc", "dirtyFiles": dirty or []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=(EvidenceItem(kind="diff", source="git", content="+change"),),
    )


def test_strict_review_blocks_pass_without_verification(tmp_path):
    report = build_review_report(
        review_bundle(), context_summary={"status": "ready"}, verification=[],
        runner=ReviewRunner(), runtime_root=tmp_path,
        instructions="review-contract-v1", strict=True,
    )
    assert report["verdict"] == "blocked"


def test_strict_review_accepts_pass_with_successful_verification(tmp_path):
    runner = ReviewRunner()
    report = build_review_report(
        review_bundle(), context_summary={"status": "ready"},
        verification=[{"label": "targeted", "exitCode": 0}],
        runner=runner, runtime_root=tmp_path,
        instructions="review-contract-v1", strict=True,
    )
    assert report["verdict"] == "pass"
    assert report["residualRisk"] == ["Hermes pending"]
    assert set(runner.last_payload["evidence"]) >= {
        "schemaVersion", "taskContract", "contextSummary", "gitDiff",
        "verification", "bundleFingerprint",
    }


def test_batch_merge_preserves_high_findings():
    finding = {"severity": "high", "file": "x.py", "line": 3, "rule": "bug", "evidence": "x", "impact": "y", "recommendedAction": "z"}
    merged = merge_review_batches([
        {"verdict": "pass", "findings": [], "residualRisk": []},
        {"verdict": "changes_required", "findings": [finding], "residualRisk": []},
    ], fingerprint="abc")
    assert merged["verdict"] == "changes_required"
    assert merged["findings"] == [finding]


def test_large_review_bundle_splits_only_between_files():
    bundle = EvidenceBundle(
        schema_version="review-evidence-v1",
        task={"id": "x", "objective": "approved", "scope": [], "forbidden": []},
        repository={"branch": "feature", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=tuple(
            EvidenceItem(kind="diff", source=f"file-{index}.py", content="x" * 40)
            for index in range(3)
        ),
    )
    batches = split_review_bundle_by_file(bundle, patch_token_budget=10)
    assert len(batches) == 3
    assert [batch.evidence[0].source for batch in batches] == ["file-0.py", "file-1.py", "file-2.py"]


def test_review_returns_overflow_before_runner(tmp_path):
    large = EvidenceBundle(
        schema_version="review-evidence-v1",
        task={"id": "x", "objective": "approved", "scope": [], "forbidden": []},
        repository={"branch": "feature", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=(EvidenceItem(kind="diff", source="git", content="x" * 10000),),
    )
    report = build_review_report(
        large, context_summary={"status": "ready"},
        verification=[{"label": "targeted", "exitCode": 0}],
        runner=ReviewRunner(), runtime_root=tmp_path,
        instructions="review-contract-v1", input_token_limit=10, strict=True,
    )
    assert report["verdict"] == "context_overflow"


def test_review_rejects_output_over_budget(tmp_path):
    class VerboseReviewRunner(ReviewRunner):
        def run(self, payload):
            report = super().run(payload)
            report["residualRisk"] = ["x" * 1000]
            return report

    with pytest.raises(ValueError, match="output token budget"):
        build_review_report(
            review_bundle(), context_summary={"status": "ready"},
            verification=[{"label": "targeted", "exitCode": 0}],
            runner=VerboseReviewRunner(), runtime_root=tmp_path,
            instructions="review-contract-v1", output_token_limit=10, strict=True,
        )
```

- [ ] **Step 2: Add failing Review CLI tests**

Append to `tests/test_agent_cli.py`:

```python
def test_review_cli_collect_only_outputs_review_bundle():
    result = subprocess.run(
        [str(PYTHON), "scripts/review_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--base", "HEAD", "--head", "WORKTREE", "--collect-only"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["schemaVersion"] == "review-evidence-v1"


def test_review_cli_strict_without_verification_exits_two(tmp_path):
    context = tmp_path / "context.json"
    context.write_text('{"status":"ready"}', encoding="utf-8")
    result = subprocess.run(
        [str(PYTHON), "scripts/review_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--base", "HEAD", "--head", "WORKTREE", "--context", str(context), "--strict"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
```

- [ ] **Step 3: Run focused tests and verify failure**

```bash
.venv/bin/python -m pytest tests/test_review_agent_service.py tests/test_agent_cli.py -q
```

Expected: collection/import FAIL.

- [ ] **Step 4: Implement strict Review orchestration**

`build_review_evidence_payload()` must expose the exact approved Review contract and keep the immutable Collector bundle unchanged:

`build_review_report()` accepts `input_token_limit: int = 16000` and `output_token_limit: int = 2000` in addition to the interface above.

Implement file-boundary batching before the public payload builder:

```python
def split_review_bundle_by_file(
    bundle: EvidenceBundle,
    *,
    patch_token_budget: int,
) -> tuple[EvidenceBundle, ...]:
    patches = [item for item in bundle.evidence if item.kind == "diff"]
    if not patches:
        return (bundle,)
    groups: list[list[EvidenceItem]] = []
    current: list[EvidenceItem] = []
    current_tokens = 0
    for patch in patches:
        patch_tokens = estimate_tokens(patch.content)
        if current and current_tokens + patch_tokens > patch_token_budget:
            groups.append(current)
            current = []
            current_tokens = 0
        current.append(patch)
        current_tokens += patch_tokens
    if current:
        groups.append(current)
    return tuple(
        EvidenceBundle(
            schema_version=bundle.schema_version,
            task=bundle.task,
            repository=bundle.repository,
            guardrails=bundle.guardrails,
            evidence=tuple(group),
            commands=bundle.commands,
        )
        for group in groups
    )
```

```python
def build_review_evidence_payload(
    bundle: EvidenceBundle,
    *,
    context_summary: dict,
    verification: list[dict],
) -> dict:
    unsigned = {
        "schemaVersion": "review-evidence-v1",
        "taskContract": bundle.task,
        "contextSummary": context_summary,
        "gitDiff": {
            "base": bundle.repository.get("base"),
            "head": bundle.repository.get("headRef"),
            "files": [item.source for item in bundle.evidence if item.kind == "diff"],
            "patches": [item.to_dict() for item in bundle.evidence if item.kind == "diff"],
            "truncated": bool(bundle.repository.get("diffFileLimitExceeded")) or any(
                bool(item.metadata.get("truncated"))
                for item in bundle.evidence if item.kind == "diff"
            ),
        },
        "verification": {"commands": verification},
    }
    return {**unsigned, "bundleFingerprint": canonical_fingerprint(unsigned)}


evidence_payload = build_review_evidence_payload(
    bundle, context_summary=context_summary, verification=verification,
)
review_fingerprint = agent_request_fingerprint(
    bundle,
    instructions=instructions,
    output_schema="review-report-v1",
    evidence_payload=evidence_payload,
)
if evidence_payload["gitDiff"]["truncated"]:
    return {
        "schemaVersion": "review-report-v1", "verdict": "context_overflow",
        "findings": [], "requirementCoverage": [], "testCoverage": [],
        "baselineRisk": "none", "residualRisk": ["Review diff was truncated; split the task or lower the diff scope."],
        "hermesRequiredChecks": [], "reviewFingerprint": review_fingerprint,
    }
if estimate_tokens(json.dumps({"instructions": instructions, "evidence": evidence_payload}, ensure_ascii=False)) > input_token_limit:
    return {
        "schemaVersion": "review-report-v1", "verdict": "context_overflow",
        "findings": [], "requirementCoverage": [], "testCoverage": [],
        "baselineRisk": "none", "residualRisk": ["Collector must split or reduce Review evidence."],
        "hermesRequiredChecks": [], "reviewFingerprint": review_fingerprint,
    }
```

Before runner dispatch:

```python
if strict and not verification:
    return {
        "schemaVersion": "review-report-v1",
        "verdict": "blocked",
        "findings": [],
        "requirementCoverage": [],
        "testCoverage": [],
        "baselineRisk": "none",
        "residualRisk": ["Strict review requires verification evidence."],
        "hermesRequiredChecks": [],
        "reviewFingerprint": review_fingerprint,
    }
if strict and any(int(item.get("exitCode", 1)) != 0 for item in verification):
    return {
        "schemaVersion": "review-report-v1", "verdict": "changes_required",
        "findings": [], "requirementCoverage": [], "testCoverage": verification,
        "baselineRisk": "none", "residualRisk": ["At least one verification command failed."],
        "hermesRequiredChecks": [], "reviewFingerprint": review_fingerprint,
    }

result = AgentRuntime(runtime_root).run(
    "review",
    bundle,
    runner,
    output_schema="review-report-v1",
    instructions=instructions,
    evidence_payload=evidence_payload,
)
if estimate_tokens(json.dumps(result, ensure_ascii=False)) > output_token_limit:
    raise ValueError("Review Agent output token budget exceeded")
```

Dispatch `bundle` through `AgentRuntime` with `evidence_payload` and `instructions` loaded from `docs/agents/REVIEW_AGENT_CONTRACT.md`. Then validate required fields, allowed verdict/severity, exact `review_fingerprint`, file/line for each finding, and non-empty residual risk when verdict is `pass`. `merge_review_batches()` sorts findings by `critical/high/medium/low`, deduplicates exact finding fingerprints, and chooses the strictest verdict using `invalid_bundle > context_overflow > blocked > changes_required > pass`.

`run_review_batches()` calls `split_review_bundle_by_file()` with `patch_token_budget=max(1000, input_token_limit // 2)`, runs `build_review_report()` for each batch, and passes all reports to `merge_review_batches()`. If a single-file batch still returns `context_overflow`, the merged verdict remains `context_overflow`; it must never be changed to `pass`. The Review CLI calls `run_review_batches()`, not a single unbounded report.

- [ ] **Step 5: Implement Review CLI and verification-file contract**

`--verification` accepts a JSON file only in this shape:

```json
{
  "commands": [
    {"label": "targeted-tests", "argv": ["python", "-m", "pytest"], "exitCode": 0, "stdoutTail": "12 passed", "stderrTail": ""}
  ]
}
```

The CLI never executes argv from that file. It only treats the file as evidence. Actual commands are run separately by Codex or the future Validation Runner. `--collect-only` ignores Context/verification requirements, calls `build_review_evidence_payload()` with empty Context/verification objects, and emits the exact `review-evidence-v1` public payload. As with the Context CLI, `--output` must resolve below `<project>/.nbs_agent_runtime/`.

```python
def exit_code_for_verdict(verdict: str | None) -> int:
    if verdict in {None, "pass"}:
        return 0
    if verdict == "changes_required":
        return 1
    if verdict == "blocked":
        return 2
    if verdict == "context_overflow":
        return 4
    return 5
```

- [ ] **Step 6: Run Review tests and smoke checks**

```bash
.venv/bin/python -m pytest tests/test_review_agent_service.py tests/test_agent_cli.py -q
.venv/bin/python scripts/review_agent.py --brief docs/agents/NBS_AGENT_ARCHITECTURE.md --base HEAD --head WORKTREE --collect-only > /tmp/nbs-review-bundle.json
.venv/bin/python -m json.tool /tmp/nbs-review-bundle.json >/dev/null
```

Expected: PASS; collect-only returns valid JSON without executing tests or Hermes.

- [ ] **Step 7: Commit Task 5**

```bash
git add backend/agents/review_agent_service.py scripts/review_agent.py tests/test_review_agent_service.py tests/test_agent_cli.py
git commit -m "feat: add strict review evidence agent"
```

---

### Task 6: Codex Auto-Dispatch Contract And Repo Instructions

狀態：verified

**Files:**
- Create: `docs/agents/CODEX_AGENT_DISPATCH.md`
- Create: `AGENTS.md`
- Modify: `docs/agents/NBS_AGENT_ARCHITECTURE.md`
- Test: `tests/test_agent_dispatch_contract.py`

**Interfaces:**
- Produces: machine-readable dispatch table embedded in Markdown fenced JSON
- Produces: repo-level instructions pointing to Context/Review contracts
- Does not execute Agent automatically inside application runtime

- [ ] **Step 1: Write failing dispatch contract test**

```python
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dispatch_document_contains_machine_readable_rules():
    text = (ROOT / "docs/agents/CODEX_AGENT_DISPATCH.md").read_text(encoding="utf-8")
    match = re.search(r"```json\n(.*?)\n```", text, re.S)
    assert match
    rules = json.loads(match.group(1))
    assert rules["context"]["anyOf"]["changedCodeFilesGte"] == 2
    assert "upload" in rules["context"]["riskSurfaces"]
    assert rules["review"]["before"] == ["commit", "merge", "hermes"]


def test_root_agents_links_all_governance_contracts():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for path in [
        "docs/agents/NBS_AGENT_ARCHITECTURE.md",
        "docs/agents/CONTEXT_AGENT_CONTRACT.md",
        "docs/agents/REVIEW_AGENT_CONTRACT.md",
        "docs/agents/CODEX_AGENT_DISPATCH.md",
        "NBS_HERMES_MONITORING.md",
    ]:
        assert path in text
```

- [ ] **Step 2: Run test and verify failure**

```bash
.venv/bin/python -m pytest tests/test_agent_dispatch_contract.py -q
```

Expected: FAIL because dispatch and root instruction files do not exist.

- [ ] **Step 3: Write the dispatch contract**

`CODEX_AGENT_DISPATCH.md` must explain the human-readable flow and contain this exact JSON block:

```json
{
  "schemaVersion": "codex-agent-dispatch-v1",
  "context": {
    "anyOf": {"changedCodeFilesGte": 2, "requiresImplementationPlan": true, "hasApprovedBrief": true},
    "riskSurfaces": ["upload", "sqlite", "baseline", "rollback", "cache", "api_contract", "export"],
    "skipFor": ["single_line_typo", "markdown_spelling", "read_only_explanation", "valid_fingerprint_cache_hit"]
  },
  "review": {
    "onFileTypes": [".py", ".vue", ".js", ".mjs", ".sql", ".json"],
    "onCrossModuleDiff": true,
    "riskSurfaces": ["revenue", "baseline", "business_rules", "upload", "export"],
    "before": ["commit", "merge", "hermes"],
    "skipFor": ["verified_document_backfill", "git_metadata", "format_only_without_behavior_change"]
  }
}
```

Document that Codex invokes `context_agent.py --collect-only`, consumes the compact bundle in the current task, and only uses `--agent-command` when the user/environment has explicitly configured an approved runner.

- [ ] **Step 4: Add minimal repo-level `AGENTS.md`**

The file must use Traditional Chinese and state:

```markdown
# NBS Analytics Agent Instructions

正式修改前先讀 `docs/agents/NBS_AGENT_ARCHITECTURE.md` 與 `docs/agents/CODEX_AGENT_DISPATCH.md`。

- 需要 Context Agent 時，先執行 `scripts/context_agent.py --collect-only`，只把 compact bundle 帶入主規劃。
- 每個 implementation Task 完成後，依 `docs/agents/REVIEW_AGENT_CONTRACT.md` 做 findings-first review。
- Review PASS 後仍要跑完整驗證與 `scripts/hermes_post_change_check.py`。
- Context/Review Agent 永遠 read-only，不得修改 SQLite、baseline、runtime、Git 或程式碼。
- Hermes 邊界以 `NBS_HERMES_MONITORING.md` 為準，不與 Review Agent 重複。

正式口徑固定為「不含掛賬核銷與TT退款轉團款」；2026-05 baseline 固定為 `HKD 12,057,968`。
```

- [ ] **Step 5: Update architecture implementation status and links**

Add links to the dispatch contract and root instructions under architecture governance, but keep status as `implementation_in_progress` until Task 8 passes.

- [ ] **Step 6: Run dispatch contract tests**

```bash
.venv/bin/python -m pytest tests/test_agent_dispatch_contract.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 6**

```bash
git add AGENTS.md docs/agents tests/test_agent_dispatch_contract.py
git commit -m "docs: define codex agent dispatch contract"
```

---

### Task 7: Read-Only Integration Gate And Hermes Coverage

狀態：verified

**Files:**
- Create: `tests/test_agent_read_only_contract.py`
- Modify: `scripts/hermes_post_change_check.py`
- Modify: `tests/test_hermes_post_change_check.py`

**Interfaces:**
- Consumes: Context/Review collect-only CLI
- Produces: proof that Agent collection does not mutate tracked worktree, formal DB or `.nbs_runtime`
- Extends: Hermes `TARGETED_TESTS` only; no new Hermes role or report section

- [ ] **Step 1: Write failing read-only integration test**

```python
import hashlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def tracked_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, text=True, capture_output=True, check=True,
    ).stdout


def test_collect_only_does_not_modify_tracked_db_or_runtime(tmp_path):
    db = ROOT / "nbs_marketing_data.db"
    generation = ROOT / ".nbs_runtime/data_generation.json"
    before = {"git": tracked_status(), "db": digest(db), "generation": digest(generation)}

    context = subprocess.run(
        [str(PYTHON), "scripts/context_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--collect-only"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    review = subprocess.run(
        [str(PYTHON), "scripts/review_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--base", "HEAD", "--head", "WORKTREE", "--collect-only"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )

    after = {"git": tracked_status(), "db": digest(db), "generation": digest(generation)}
    assert context.returncode == 0
    assert review.returncode == 0
    assert after == before
```

- [ ] **Step 2: Add failing Hermes targeted-pack assertions**

Append these names to the existing loop in `tests/test_hermes_post_change_check.py`:

```python
for test_name in [
    "tests/test_evidence_models.py",
    "tests/test_evidence_collector.py",
    "tests/test_agent_runtime.py",
    "tests/test_context_agent_service.py",
    "tests/test_review_agent_service.py",
    "tests/test_agent_cli.py",
    "tests/test_agent_dispatch_contract.py",
    "tests/test_agent_read_only_contract.py",
]:
    assert test_name in targeted.command
```

- [ ] **Step 3: Run tests and verify Hermes assertion fails**

```bash
.venv/bin/python -m pytest tests/test_agent_read_only_contract.py tests/test_hermes_post_change_check.py -q
```

Expected: read-only test may PASS; Hermes assertion FAIL because new tests are not in `TARGETED_TESTS`.

- [ ] **Step 4: Add Agent tests to Hermes targeted pack**

Append the eight test paths to `TARGETED_TESTS` in `scripts/hermes_post_change_check.py`. Do not add Agent CLI execution to `build_check_plan`; Hermes should run tests that prove the contracts, not duplicate Context/Review work.

- [ ] **Step 5: Run Agent integration and Hermes unit tests**

```bash
.venv/bin/python -m pytest \
  tests/test_evidence_models.py \
  tests/test_evidence_collector.py \
  tests/test_agent_runtime.py \
  tests/test_context_agent_service.py \
  tests/test_review_agent_service.py \
  tests/test_agent_cli.py \
  tests/test_agent_dispatch_contract.py \
  tests/test_agent_read_only_contract.py \
  tests/test_hermes_post_change_check.py -q
```

Expected: PASS; Git tracked status remains unchanged by the test run.

- [ ] **Step 6: Commit Task 7**

```bash
git add scripts/hermes_post_change_check.py tests/test_hermes_post_change_check.py tests/test_agent_read_only_contract.py
git commit -m "test: govern agent evidence pipeline in hermes"
```

---

### Task 8: Full Verification, Telemetry Trial And Documentation Evidence

狀態：verified（implementation 與 verification evidence 已提交；merge 前仍需 final review）

**Files:**
- Modify: `docs/agents/NBS_AGENT_ARCHITECTURE.md`
- Modify: `docs/agents/CONTEXT_AGENT_CONTRACT.md` only if implementation names differ from the approved schema
- Modify: `docs/agents/REVIEW_AGENT_CONTRACT.md` only if implementation names differ from the approved schema
- Modify: `NBS_ANALYTICS_SYSTEM_MAP.md`
- Modify: `docs/superpowers/plans/2026-07-14-agent-evidence-pipeline.md`

**Interfaces:**
- Produces: verified implementation evidence and current system map
- Requires: all prior Tasks committed and worktree changes attributable
- Does not: merge to main or mutate formal data

- [x] **Step 1: Run compile for all new Python modules and CLIs**

```bash
.venv/bin/python -m py_compile \
  backend/agents/evidence_models.py \
  backend/agents/evidence_collector.py \
  backend/agents/agent_runtime.py \
  backend/agents/context_agent_service.py \
  backend/agents/review_agent_service.py \
  scripts/context_agent.py \
  scripts/review_agent.py
```

Expected: exit 0 and no output.

- [x] **Step 2: Run the complete Agent test pack**

```bash
.venv/bin/python -m pytest \
  tests/test_evidence_models.py \
  tests/test_evidence_collector.py \
  tests/test_agent_runtime.py \
  tests/test_context_agent_service.py \
  tests/test_review_agent_service.py \
  tests/test_agent_cli.py \
  tests/test_agent_dispatch_contract.py \
  tests/test_agent_read_only_contract.py \
  tests/test_hermes_post_change_check.py -q
```

Expected: PASS.

- [x] **Step 3: Run collect-only telemetry trial**

```bash
.venv/bin/python scripts/context_agent.py \
  --brief docs/agents/NBS_AGENT_ARCHITECTURE.md \
  --collect-only \
  --output .nbs_agent_runtime/bundles/context-trial.json
.venv/bin/python scripts/review_agent.py \
  --brief docs/agents/NBS_AGENT_ARCHITECTURE.md \
  --base main \
  --head WORKTREE \
  --collect-only \
  --output .nbs_agent_runtime/bundles/review-trial.json
.venv/bin/python -m json.tool .nbs_agent_runtime/bundles/context-trial.json >/dev/null
.venv/bin/python -m json.tool .nbs_agent_runtime/bundles/review-trial.json >/dev/null
```

Expected: both bundles are valid JSON, remain below configured input budgets, contain no denied path content, and `.nbs_agent_runtime/` remains ignored.

- [x] **Step 4: Run full Python regression**

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests PASS.

- [x] **Step 5: Run Vue verification and build**

```bash
npm --prefix frontend run verify
npm --prefix frontend run build
```

Expected: contract verification and Vite build PASS; tracked worktree remains unchanged.

- [x] **Step 6: Start services and run acceptance**

```bash
.venv/bin/python scripts/system_manager.py start --no-browser
.venv/bin/python scripts/system_manager.py acceptance
```

Expected: Streamlit, API and Vue are ready; acceptance status is `passed`.

- [x] **Step 7: Run Hermes read-only acceptance**

```bash
.venv/bin/python scripts/hermes_post_change_check.py --json > /tmp/agent-evidence-hermes.json
.venv/bin/python -c "import json; d=json.load(open('/tmp/agent-evidence-hermes.json')); assert d['overallStatus']=='pass'; print(d['overallStatus'])"
```

Expected: `pass`; SQLite integrity `ok`; monthly baselines matched; 2026-05 actual remains `HKD 12,057,968`; Agent targeted tests included.

- [x] **Step 8: Update architecture and system-map evidence**

Update `NBS_AGENT_ARCHITECTURE.md` status to `verified` and record:

- actual Agent test count and full pytest count;
- collect-only bundle estimated tokens and denied-data spot check;
- read-only integration result;
- service acceptance and Hermes result;
- exact implementation commit IDs.

Add a concise Agent Architecture section to `NBS_ANALYTICS_SYSTEM_MAP.md` linking the three contracts, two CLIs, `.nbs_agent_runtime/` and the boundary that Hermes remains final acceptance.

- [x] **Step 9: Review changed files and run final format checks**

```bash
git diff --check
git status --short --branch
git diff --stat main...HEAD
```

Expected: no whitespace errors; only planned files changed; `.nbs_agent_runtime/` absent from Git status.

- [x] **Step 10: Commit verification evidence**

```bash
git add \
  docs/agents \
  NBS_ANALYTICS_SYSTEM_MAP.md \
  docs/superpowers/plans/2026-07-14-agent-evidence-pipeline.md
git commit -m "docs: verify agent evidence pipeline"
```

- [ ] **Step 11: Request final code review before merge**（保留為 merge 前 gate，待 merge 前執行）

#### Task 8 Verification Record

- Implementation commits：`2b7243b`、`5c07e60`、`9e2670a`、`9b3f300`、`4ba657c`。
- Agent pack（implementation plan 指定 9 檔）：`110 passed`；full pytest：`329 passed`。
- Context `--collect-only`：`8,284` estimated tokens，無 overflow；Review `--collect-only`：`29` 個 diff files。
- denied source paths：`0`。此數字只計資料內容，文件中描述 deny patterns 的文字不計入資料內容。
- Read-only integration、Vue verify/build、system acceptance 均 passed。
- Hermes `overallStatus=pass`；2026-05 baseline matched `HKD 12,057,968`。
- Task 8 文件收尾與 verification evidence 已提交，並保留 merge 前 review 與使用者授權 gate。

Review requirements:

- no forbidden source or formal data access;
- no arbitrary shell execution;
- bundle and review fingerprint invalidation is correct;
- strict Review cannot pass without verification evidence;
- Hermes responsibilities are unchanged;
- all actual verification evidence is reflected in documentation.

Do not merge until review findings are resolved and the full Task 8 validation is rerun.

---

## Completion Gate

The implementation is complete only when all of the following are true:

1. Context and Review collect-only CLIs work without LLM credentials.
2. A configured allowlisted subprocess Agent can consume JSON stdin and return validated JSON stdout.
3. Denied data paths, path escape, arbitrary executables and non-JSON output are rejected.
4. Fingerprint cache invalidates on task, HEAD, dirty diff or verification changes.
5. Strict Review cannot report PASS without successful verification evidence and residual risk.
6. Agent runtime writes only to `.nbs_agent_runtime/`; tracked worktree, formal DB and runtime evidence remain unchanged.
7. Full pytest, Vue verify/build, service acceptance and Hermes all pass.
8. 2026-01 to 2026-06 monthly governance remains matched and 2026-05 remains `HKD 12,057,968`.
9. Architecture, contracts, dispatch rules and system map match the implemented interfaces.
10. User explicitly authorizes merge to `main` after final evidence is reported.
