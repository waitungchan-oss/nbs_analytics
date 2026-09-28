import pytest

from backend.agents.documentation_policy import DocumentationImpactClassifier


@pytest.fixture
def classifier():
    return DocumentationImpactClassifier()


@pytest.mark.parametrize(
    ("paths", "runner_required", "required_targets"),
    [
        (("tests/test_x.py",), False, ()),
        (("backend/routers/dashboard.py",), True, ("brief_backfill", "system_map")),
        (("backend/agents/workflow_models.py",), True, ("brief_backfill", "system_map")),
        (("database.py",), True, ("brief_backfill", "system_map", "adr")),
        (("docs/readme.md",), False, ()),
    ],
)
def test_classification(paths, runner_required, required_targets, classifier):
    result = classifier.classify(paths, evidence={"riskSurfaces": []})
    assert result["runnerRequired"] is runner_required
    assert tuple(result["requiredTargets"]) == required_targets


@pytest.mark.parametrize("surface", [
    "baseline", "revenue_scope", "permission", "security", "retention", "state_machine",
])
def test_protected_risk_surfaces_require_all_targets(classifier, surface):
    result = classifier.classify(("backend/unknown.py",), {"riskSurfaces": [surface]})
    assert result["runnerRequired"] is True
    assert tuple(result["requiredTargets"]) == ("brief_backfill", "system_map", "adr")


@pytest.mark.parametrize("path", [
    "backend/agents/schema_utils.py",
    "backend/services/schema_handler.py",
    "backend/ordinary_schema_code.py",
])
def test_schema_named_code_paths_do_not_require_adr(classifier, path):
    result = classifier.classify((path,), {"riskSurfaces": []})

    assert tuple(result["requiredTargets"]) == ("brief_backfill", "system_map")


@pytest.mark.parametrize("path", [
    "database.py",
    "backend/database/connection.py",
    "backend/migrations/0001_init.py",
])
def test_explicit_database_paths_require_adr(classifier, path):
    result = classifier.classify((path,), {"riskSurfaces": []})

    assert tuple(result["requiredTargets"]) == ("brief_backfill", "system_map", "adr")


def test_target_catalog_resolves_only_five_documented_ids():
    from backend.agents.documentation_models import DocumentationSchemaError
    from backend.agents.documentation_policy import load_documentation_target

    expected = {
        "handoff.current-conclusion": (
            "handoff", "NBS_ANALYTICS_HANDOFF.md", "## 1. 本輪交接結論",
        ),
        "handoff.verification-snapshot": (
            "handoff", "NBS_ANALYTICS_HANDOFF.md", "### 5.3 最近驗證快照",
        ),
        "runbook.pipeline-rollout-gate": (
            "runbook", "docs/agents/ACCEPTANCE_PIPELINE_HARDENING_RUNBOOK.md",
            "## Rollout handoff gate（Task 6）",
        ),
        "runbook.shard-boundary": (
            "runbook", "docs/agents/ACCEPTANCE_SHARD_ROLLOUT_RUNBOOK.md", "## Boundary",
        ),
        "runbook.parallel-rollout-boundary": (
            "runbook", "docs/agents/ACCEPTANCE_PARALLEL_ROLLOUT_RUNBOOK.md", "## Boundary",
        ),
    }

    for target_id, (kind, repo_path, heading) in expected.items():
        target = load_documentation_target(target_id)
        assert (target.target_kind, target.repo_path, target.section_heading) == (kind, repo_path, heading)
        assert target.operation == "replace_section"
        assert target.risk_tier == "high"
        assert target.required_approval_id == target_id
        assert target.must_exist is True

    for invalid_id in (
        "handoff.current-conclusion/../../database.py",
        "runbook.*",
        "../NBS_ANALYTICS_HANDOFF.md",
        "handoff.parallel-rollout-boundary",
        "runbook.current-conclusion",
    ):
        with pytest.raises(DocumentationSchemaError):
            load_documentation_target(invalid_id)

    from backend.agents.documentation_policy import _parse_documentation_target_catalog

    catalog = {"schemaVersion": "documentation-target-policy-v2", "targets": [
        {
            "schemaVersion": "documentation-target-v2",
            "targetId": target_id,
            "targetKind": kind,
            "repoPath": repo_path,
            "sectionHeading": heading,
            "operation": "replace_section",
            "riskTier": "high",
            "requiredApprovalId": target_id,
            "mustExist": True,
        }
        for target_id, (kind, repo_path, heading) in expected.items()
    ]}
    duplicated = {**catalog, "targets": [*catalog["targets"], catalog["targets"][0]]}
    with pytest.raises(DocumentationSchemaError, match="duplicate"):
        _parse_documentation_target_catalog(duplicated)

    wrong_kind = {**catalog, "targets": [dict(item) for item in catalog["targets"]]}
    wrong_kind["targets"][0]["targetKind"] = "runbook"
    with pytest.raises(DocumentationSchemaError, match="catalog"):
        _parse_documentation_target_catalog(wrong_kind)
