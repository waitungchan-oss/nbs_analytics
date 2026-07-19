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
