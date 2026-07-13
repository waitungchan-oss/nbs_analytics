from __future__ import annotations

from pydantic import BaseModel, Field


class DashboardFilters(BaseModel):
    years: list[int] = Field(default_factory=list)
    months: list[str] = Field(default_factory=list)
    dateRange: list[str] = Field(default_factory=list)
    branch: str = "全部分社"
    salesGroup: str = "全部銷售組"


class DashboardContextResponse(BaseModel):
    hasData: bool
    tourRows: int
    othersRows: int
    maxDate: str | None
    minDate: str | None
    years: list[int]
    months: list[str]
    branches: list[str]
    salesGroups: list[str]
    revenueScope: str


class KpiCard(BaseModel):
    label: str
    value: str
    delta: str
    note: str
    accent: str


class BranchRankingRow(BaseModel):
    rank: int
    branch: str
    tourRevenue: float
    cruiseRevenue: float
    ticketRevenue: float
    totalRevenue: float
    sharePct: float


class SpecialistRankingRow(BaseModel):
    rank: int
    specialist: str
    tourRevenue: float
    cruiseRevenue: float
    ticketRevenue: float
    totalRevenue: float
    sharePct: float


class RevenueTotals(BaseModel):
    branchRevenue: float
    specialistRevenue: float
    combinedRevenue: float
    formattedCombinedRevenue: str
    scope: str


class DataFreshness(BaseModel):
    minDate: str | None
    maxDate: str | None
    rawRows: int
    analysisRows: int
    excludedRows: int
    scope: str


class StabilityCheck(BaseModel):
    key: str
    label: str
    expected: str | int | float | None
    actual: str | int | float | None
    delta: str | int | float | None = None
    unit: str = ""
    status: str


class StabilitySummary(BaseModel):
    totalChecks: int
    matchedChecks: int
    driftChecks: int


class FreshnessSummary(BaseModel):
    totalChecks: int
    stableChecks: int
    updatedChecks: int


class CoreValidationGroup(BaseModel):
    status: str
    summary: StabilitySummary
    checks: list[StabilityCheck]


class FreshnessUpdateGroup(BaseModel):
    status: str
    summary: FreshnessSummary
    checks: list[StabilityCheck]


class StabilityBaseline(BaseModel):
    name: str
    baselineMonth: str
    status: str
    formattedExpectedTotal: str
    formattedActualTotal: str
    expectedTotal: float
    actualTotal: float
    deltaAmount: float
    deltaPct: float
    summary: StabilitySummary
    coreValidation: CoreValidationGroup
    freshnessUpdate: FreshnessUpdateGroup
    checks: list[StabilityCheck]


class DashboardSummaryResponse(BaseModel):
    appliedFilters: DashboardFilters
    revenueScope: str
    scopeAudit: dict
    kpis: list[KpiCard]
    revenueTotals: RevenueTotals
    dataFreshness: DataFreshness
    stabilityBaseline: StabilityBaseline
    branchRanking: list[BranchRankingRow]
    specialistRanking: list[SpecialistRankingRow]
    productMix: list[dict]
    exportReadiness: dict


class AnnualSummaryRow(BaseModel):
    year: int
    branchRevenue: float
    specialistRevenue: float
    combinedRevenue: float
    branchSharePct: float
    specialistSharePct: float


class MonthlyTrendRow(BaseModel):
    month: str
    branchRevenue: float
    specialistRevenue: float
    combinedRevenue: float


class ProductDrilldownRow(BaseModel):
    product: str
    revenue: float
    sharePct: float


class ReconciliationCheck(BaseModel):
    key: str
    expected: float
    actual: float
    delta: float
    status: str


class Reconciliation(BaseModel):
    status: str
    combinedRevenue: float
    checks: list[ReconciliationCheck]


class DashboardFactsResponse(BaseModel):
    status: str
    serviceVersion: str
    generationToken: str
    cacheKey: str
    factsCacheStatus: str
    revenueScope: str
    scopeAudit: dict
    kpiTotals: dict[str, float]
    monthlyTotals: list[MonthlyTrendRow]
    branchRanking: list[BranchRankingRow]
    specialistRanking: list[SpecialistRankingRow]
    productTotals: list[ProductDrilldownRow]
    reconciliation: Reconciliation


class DashboardAnalyticsResponse(BaseModel):
    appliedFilters: DashboardFilters
    revenueScope: str
    annualSummary: list[AnnualSummaryRow]
    monthlyTrend: list[MonthlyTrendRow]
    branchRanking: list[BranchRankingRow]
    specialistRanking: list[SpecialistRankingRow]
    productDrilldown: dict[str, list[ProductDrilldownRow]]
    reconciliation: Reconciliation


class StabilityHistoryItem(BaseModel):
    id: int
    createdAt: str
    uploadStatus: str
    uploadMessage: str
    sourceFiles: list[str]
    coreStatus: str
    baselineMonth: str | None
    formattedExpectedTotal: str | None
    formattedActualTotal: str | None
    deltaAmount: float
    matchedChecks: int
    totalChecks: int
    driftCheckCount: int
    freshnessStatus: str
    freshnessUpdateCount: int
    latestDataDate: str | None
    batchSummary: list[dict]
    upsertSummary: list[dict]
    driftDiagnosis: dict = Field(default_factory=dict)
    gate: dict
    rollbackStatus: str | None = None
    backupPath: str | None = None
    quarantinePath: str | None = None
    postRollbackGate: dict = Field(default_factory=dict)
    rollbackError: str | None = None
    operationId: str | None = None
    entryPoint: str | None = None
    stageTimings: list[dict] = Field(default_factory=list)
    cacheState: str | None = None
    cacheError: str | None = None
    dataGeneration: dict = Field(default_factory=dict)
    monthlyBaseline: dict = Field(default_factory=dict)


class StabilityHistoryResponse(BaseModel):
    items: list[StabilityHistoryItem]
    count: int


class UploadActionResponse(BaseModel):
    status: str
    message: str
    operationId: str
    entryPoint: str
    sourceFiles: list[str]
    preflightReport: dict
    upsertSummary: dict | None = None
    stabilityGate: dict | None = None
    rollbackResult: dict | None = None
    historyRecordId: int | None = None
    historyError: str | None = None
    writeCommitted: bool
    monthlyBaseline: dict = Field(default_factory=dict)
    cacheState: str
    cacheError: str | None = None
    dataGeneration: dict = Field(default_factory=dict)
    stageTimings: list[dict] = Field(default_factory=list)
    latestHealth: dict
    entityAudit: dict | None = None
    anmRowCount: int | None = None
    environment: dict | None = None
