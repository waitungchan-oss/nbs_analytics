from __future__ import annotations

from dataclasses import dataclass


R2_SURFACES = frozenset({"upload", "sqlite", "baseline", "rollback", "revenue", "business_rules", "export_schema"})
R1_CODE_SUFFIXES = frozenset({".py", ".vue", ".js", ".mjs", ".sql", ".json"})
R0_DOCUMENT_SUFFIXES = frozenset({".md", ".txt"})

TERMINAL_BLOCKED_STATUSES = frozenset({"protected_incident", "blocked_user_decision", "blocked_missing_runner"})
PROTECTED_FAILURE_MODES = frozenset(
    {"baseline_drift", "revenue_scope_conflict", "unsafe_db_path", "protected_invariant_failure"}
)
NODE_TRANSITIONS = {
    "risk": ("spec_gate",),
    "spec_gate": ("plan_gate",),
    "plan_gate": ("task",),
    "task": ("targeted_verification",),
    "targeted_verification": ("review",),
    "review": ("full_verification",),
    "full_verification": ("hermes",),
    "hermes": ("documentation",),
    "documentation": ("git_integration",),
    "git_integration": (),
}
INVALIDATION = {
    "brief": (
        "risk",
        "spec_gate",
        "plan_gate",
        "task",
        "targeted_verification",
        "review",
        "full_verification",
        "hermes",
        "documentation",
        "git_integration",
    ),
    "risk": (
        "spec_gate",
        "plan_gate",
        "task",
        "targeted_verification",
        "review",
        "full_verification",
        "hermes",
        "documentation",
        "git_integration",
    ),
    "spec": (
        "plan_gate",
        "task",
        "targeted_verification",
        "review",
        "full_verification",
        "hermes",
        "documentation",
        "git_integration",
    ),
    "plan_or_contract": (
        "task",
        "targeted_verification",
        "review",
        "full_verification",
        "hermes",
        "documentation",
        "git_integration",
    ),
    "git_identity": (
        "targeted_verification",
        "review",
        "full_verification",
        "hermes",
        "documentation",
        "git_integration",
    ),
    "hermes": ("documentation", "git_integration"),
}


@dataclass(frozen=True)
class RiskClassification:
    level: str
    reason_code: str
    surfaces: tuple[str, ...]


@dataclass(frozen=True)
class GateValidation:
    status: str
    reason_code: str | None
    next_node: str | None
    overall_status: str


@dataclass(frozen=True)
class RetryResolution:
    next_status: str
    reason_code: str
    consume_repair_budget: bool
    next_node: str | None = None


def _is_safe_path(path: str) -> bool:
    if not path or path.startswith("/") or path.startswith("~") or "\\" in path:
        return False
    parts = path.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _suffix(path: str) -> str:
    head, dot, tail = path.rpartition(".")
    if not head or not dot:
        return ""
    return f".{tail}"


def classify_risk(
    changed_paths: tuple[str, ...],
    declared_surfaces: tuple[str, ...],
    behavior_change: bool,
    fingerprint_cache_hit: bool,
) -> RiskClassification:
    surfaces = tuple(dict.fromkeys(declared_surfaces))
    if any(surface in R2_SURFACES for surface in surfaces):
        protected = tuple(surface for surface in surfaces if surface in R2_SURFACES)
        return RiskClassification(level="R2", reason_code="protected_surface", surfaces=protected)

    if surfaces:
        return RiskClassification(level="R2", reason_code="unknown_or_ambiguous_surface", surfaces=surfaces)

    normalized_paths = tuple(changed_paths)
    suffixes = []
    for path in normalized_paths:
        if not _is_safe_path(path):
            return RiskClassification(level="R2", reason_code="unknown_or_ambiguous_surface", surfaces=())
        suffix = _suffix(path)
        suffixes.append(suffix)
        if suffix not in R1_CODE_SUFFIXES and suffix not in R0_DOCUMENT_SUFFIXES:
            return RiskClassification(level="R2", reason_code="unknown_or_ambiguous_surface", surfaces=())

    if behavior_change or any(suffix in R1_CODE_SUFFIXES for suffix in suffixes):
        return RiskClassification(level="R1", reason_code="behavioral_code_change", surfaces=())

    if normalized_paths and all(suffix in R0_DOCUMENT_SUFFIXES for suffix in suffixes):
        reason_code = "document_cache_reuse" if fingerprint_cache_hit else "documentation_only"
        return RiskClassification(level="R0", reason_code=reason_code, surfaces=())

    return RiskClassification(level="R0", reason_code="no_behavioral_change", surfaces=())


def validate_gate(
    gate_id: str,
    *,
    status: str,
    failure_mode: str | None = None,
    budget_remaining: int = 0,
) -> GateValidation:
    if status == "passed":
        next_nodes = NODE_TRANSITIONS.get(gate_id, ())
        return GateValidation(status="passed", reason_code=None, next_node=next_nodes[0] if next_nodes else None, overall_status="awaiting_authorization")

    if failure_mode in PROTECTED_FAILURE_MODES:
        return GateValidation(
            status="blocked",
            reason_code="protected_incident",
            next_node=None,
            overall_status="protected_incident",
        )

    if failure_mode == "design_conflict":
        return GateValidation(
            status="blocked",
            reason_code="design_conflict",
            next_node="plan_gate",
            overall_status="awaiting_authorization",
        )

    if budget_remaining > 0:
        return GateValidation(
            status="failed",
            reason_code="retry_available",
            next_node=gate_id,
            overall_status="awaiting_authorization",
        )

    return GateValidation(
        status="blocked",
        reason_code="correction_budget_exhausted",
        next_node=None,
        overall_status="blocked_user_decision",
    )


def allowed_next_nodes(overall_status: str, *, current_node: str) -> tuple[str, ...]:
    if overall_status in TERMINAL_BLOCKED_STATUSES:
        return ()
    return NODE_TRANSITIONS.get(current_node, ())


def resolve_retry(
    node_id: str,
    *,
    repair_loops_used: int,
    failure_mode: str | None = None,
) -> RetryResolution:
    del node_id
    if failure_mode == "environment":
        return RetryResolution(
            next_status="environment_recovery",
            reason_code="environment_recovery",
            consume_repair_budget=False,
        )
    if failure_mode in PROTECTED_FAILURE_MODES:
        return RetryResolution(
            next_status="protected_incident",
            reason_code="protected_incident",
            consume_repair_budget=False,
        )
    if failure_mode == "design_conflict":
        return RetryResolution(
            next_status="plan_gate",
            reason_code="design_conflict",
            consume_repair_budget=False,
            next_node="plan_gate",
        )
    if repair_loops_used < 1:
        return RetryResolution(
            next_status="luna_repair",
            reason_code="luna_repair",
            consume_repair_budget=True,
        )
    return RetryResolution(
        next_status="diagnosis_required",
        reason_code="repair_budget_exhausted",
        consume_repair_budget=False,
    )


def invalidate_downstream(trigger: str) -> tuple[str, ...]:
    if trigger not in INVALIDATION:
        raise ValueError(f"unknown invalidation trigger: {trigger}")
    return INVALIDATION[trigger]
