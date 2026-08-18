"""Provision the fixed deployment-owned Memory Hub catalogs.

This command is intentionally narrower than a general catalog builder: it reads
the tracked deployment manifest and source documents, writes only under the
fixed ``.nbs_agent_runtime/memory-hub`` root, and never overwrites divergent
runtime artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

# Keep direct ``python scripts/provision_memory_hub_catalog.py`` execution
# equivalent to module execution without accepting caller-controlled imports.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.memory_hub_agent_policy_catalog import AgentPolicyCatalog
from backend.agents.memory_hub_catalog import CatalogBuildPolicy, MemoryHubCatalogError, build_catalog
from backend.agents.memory_hub_deployment_provider import _read_manifest
from backend.agents.memory_hub_models import MemoryRecord, MemorySource
from backend.agents.memory_hub_team_catalog import TeamCatalog


RUNTIME_RELATIVE = Path(".nbs_agent_runtime/memory-hub")
PROJECT_ID = "nbs_analytics"

# Deployment-owned policy is deliberately fixed and deny-by-default. Changing
# it is a governance change that must update the tracked deployment contract.
TEAM_CATALOG = {
    "catalogFingerprint": "22b14c3af2ade7e9e908a8d52426f5549abf77424b66d78d2c0c06203a08abda",
    "projectId": PROJECT_ID,
    "schemaVersion": "memory-team-catalog-v1",
    "teams": [{
        "agentIds": ["context-agent"],
        "allowedScopes": ["project"],
        "recordFingerprint": "dc477ac9592121c87ae8188a55236b6dc3bf834787ec07c001a629176c30afcb",
        "role": "governance_reader",
        "schemaVersion": "memory-team-record-v1",
        "teamId": "team-context-governance",
    }],
}
AGENT_POLICY_CATALOG = {
    "agents": [{
        "agentClass": "context",
        "agentId": "context-agent",
        "allowedMemoryKinds": ["evidence", "governance", "skill"],
        "allowedScopes": ["project"],
        "recordFingerprint": "bc76b32816fc7a1069fe21c7be4ad316195ff15f87d9d8e0993dd91a007f81a4",
        "rules": [{
            "decision": "allow",
            "memoryKinds": ["evidence", "governance", "skill"],
            "ruleFingerprint": "8c409d773e577e8ea47d8668789f25c255588c51930d5f17cb28adca70332c86",
            "schemaVersion": "memory-agent-policy-rule-v1",
            "scopes": ["project"],
        }],
        "schemaVersion": "memory-agent-policy-record-v1",
        "teamIds": ["team-context-governance"],
    }],
    "catalogFingerprint": "12bdc6f9c9c1e8ad8d89a4da82e37b38cf9be01f7f2cb5ee195fe7821a305ee6",
    "defaultDecision": "deny",
    "projectId": PROJECT_ID,
    "schemaVersion": "memory-agent-policy-catalog-v1",
}


def _fixed_runtime_root(project_root: Path) -> Path:
    if not isinstance(project_root, Path) or project_root.is_symlink() or not project_root.is_dir():
        raise MemoryHubCatalogError("project root is invalid")
    root = project_root.resolve(strict=True)
    runtime = root / RUNTIME_RELATIVE
    if runtime.exists() and runtime.is_symlink():
        raise MemoryHubCatalogError("runtime root must not be a symlink")
    runtime.mkdir(parents=True, exist_ok=True)
    if not runtime.is_dir() or runtime.resolve(strict=True) != runtime:
        raise MemoryHubCatalogError("runtime root is invalid")
    return runtime


def _immutable_write(path: Path, payload: str, root: Path) -> None:
    raw = path.absolute()
    try:
        relative = raw.relative_to(root.absolute())
    except ValueError as exc:
        raise MemoryHubCatalogError("runtime output escapes fixed root") from exc
    if raw.is_symlink():
        raise MemoryHubCatalogError("runtime output must not be a symlink")
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise MemoryHubCatalogError("runtime output contains a symlink")
    if raw.exists():
        if not raw.is_file() or raw.read_text(encoding="utf-8") != payload:
            raise MemoryHubCatalogError(f"existing runtime artifact differs: {relative}")
        return
    raw.write_text(payload, encoding="utf-8")


def _manifest_policy(project_root: Path) -> tuple[Mapping[str, Any], Path, Path, tuple[MemorySource, ...], tuple[MemoryRecord, ...]]:
    manifest_path = project_root / "agent_config" / "memory_hub_catalog_deployment.json"
    payload = _read_manifest(manifest_path)
    if payload is None:
        raise MemoryHubCatalogError("deployment manifest is missing")
    source_root = project_root / payload["sourceRoot"]
    runtime_root = _fixed_runtime_root(project_root)
    sources = tuple(MemorySource.from_dict(item) for item in payload["sources"])
    source_index = {source.source_id: source for source in sources}
    records = tuple(MemoryRecord.from_dict(item, source_index) for item in payload["records"])
    return payload, source_root, runtime_root, sources, records


def provision(project_root: Path, *, check_only: bool = False) -> dict[str, Any]:
    manifest, source_root, runtime_root, sources, records = _manifest_policy(project_root)
    policy = CatalogBuildPolicy(
        source_root=source_root,
        output_root=runtime_root,
        sources=sources,
        records=records,
        built_from_head=manifest["builtFromHead"],
        policy_fingerprint=manifest["policyFingerprint"],
    )
    # Build in memory first. This verifies every source hash before any write.
    catalog_path = runtime_root / manifest["catalogFile"]
    if check_only and not catalog_path.is_file():
        raise MemoryHubCatalogError("catalog runtime artifact is missing")
    catalog = build_catalog(source_root, catalog_path, policy) if not check_only else _load_existing_catalog(project_root, policy, catalog_path)
    team_catalog = TeamCatalog.from_dict(TEAM_CATALOG, expected_project_id=PROJECT_ID)
    policy_catalog = AgentPolicyCatalog.from_dict(AGENT_POLICY_CATALOG, expected_project_id=PROJECT_ID, team_catalog=team_catalog)
    team_payload = json.dumps(team_catalog.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    policy_payload = json.dumps(policy_catalog.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    team_path = runtime_root / "team-catalog.json"
    agent_policy_path = runtime_root / "agent-policy-catalog.json"
    if check_only:
        for path, payload in ((team_path, team_payload), (agent_policy_path, policy_payload)):
            if not path.is_file() or path.read_text(encoding="utf-8") != payload:
                raise MemoryHubCatalogError(f"runtime artifact is missing or differs: {path.name}")
    else:
        _immutable_write(team_path, team_payload, runtime_root)
        _immutable_write(agent_policy_path, policy_payload, runtime_root)
    return {
        "schemaVersion": "memory-hub-provisioning-report-v1",
        "status": "ready",
        "projectId": PROJECT_ID,
        "catalogFingerprint": catalog.catalog_fingerprint,
        "teamCatalogFingerprint": team_catalog.catalog_fingerprint,
        "agentPolicyCatalogFingerprint": policy_catalog.catalog_fingerprint,
        "files": ["catalog.json", "team-catalog.json", "agent-policy-catalog.json"],
        "checkOnly": check_only,
    }


def _load_existing_catalog(project_root: Path, policy: CatalogBuildPolicy, path: Path):
    from backend.agents.memory_hub_catalog import load_catalog

    return load_catalog(path, policy.output_root, policy)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision fixed deployment-owned Memory Hub catalogs")
    parser.add_argument("--check-only", action="store_true", help="validate existing runtime artifacts without writing")
    args = parser.parse_args(argv)
    try:
        report = provision(PROJECT_ROOT, check_only=args.check_only)
    except Exception as exc:
        print(json.dumps({"schemaVersion": "memory-hub-provisioning-report-v1", "status": "blocked", "reason": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
