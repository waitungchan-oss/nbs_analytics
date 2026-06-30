from pydantic import BaseModel, Field


class QualityDimension(BaseModel):
    dimension: str
    score: float
    health: str
    metric: str


class FieldCompletenessRow(BaseModel):
    field: str
    status: str
    completeRows: int | None
    totalRows: int
    completeRate: float | None


class DataQualityResponse(BaseModel):
    status: str
    scope: str
    overallScore: float
    overallHealth: str
    latestDate: str | None
    missingDays: int
    unmatchedRows: int
    excludedAmountRate: float
    rawRows: int = 0
    officialRows: int = 0
    dimensions: list[QualityDimension]
    fieldCompleteness: list[FieldCompletenessRow]


class ForecastCache(BaseModel):
    path: str | None = None
    modifiedAt: str | None = None
    version: str | None = None


class ForecastDailyRow(BaseModel):
    date: str
    weightVersion: str
    strategy: str
    arima: float
    prophet: float
    lightgbm: float
    consensus: float
    lower: float
    upper: float


class SevenDayForecast(BaseModel):
    windowStart: str
    windowEnd: str
    consensus: float
    lower: float
    upper: float


class MonthEndForecast(BaseModel):
    month: str
    mtdActual: float
    remainingDays: int
    remainingPrediction: float
    consensus: float
    lower: float
    upper: float


class ForecastResponse(BaseModel):
    status: str
    message: str
    scope: str
    cache: ForecastCache = Field(default_factory=ForecastCache)
    weights: list[dict] = Field(default_factory=list)
    daily: list[ForecastDailyRow] = Field(default_factory=list)
    sevenDay: SevenDayForecast | None = None
    monthEnd: MonthEndForecast | None = None
    health: dict = Field(default_factory=dict)

