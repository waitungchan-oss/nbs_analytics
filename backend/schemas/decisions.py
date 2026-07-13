from pydantic import BaseModel, Field


class DecisionTarget(BaseModel):
    id: str
    label: str
    month: str
    scope: str
    targetRevenue: float
    actualRevenue: float | None
    attainmentPct: float | None
    gapAmount: float | None
    forecastedRevenue: float | None
    projectedGapAmount: float | None
    status: str


class DecisionAlert(BaseModel):
    id: str
    code: str
    severity: str
    title: str
    summary: str
    recommendation: str
    evidence: dict = Field(default_factory=dict)
    status: str


class DecisionCard(BaseModel):
    id: str
    priority: str
    title: str
    summary: str
    recommendation: str
    evidence: dict = Field(default_factory=dict)
    status: str


class DecisionOverviewResponse(BaseModel):
    status: str
    message: str
    targetConfig: dict = Field(default_factory=dict)
    targets: list[DecisionTarget] = Field(default_factory=list)
    alerts: list[DecisionAlert] = Field(default_factory=list)
    decisions: list[DecisionCard] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)
