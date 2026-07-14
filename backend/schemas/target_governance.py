from typing import Literal

from pydantic import BaseModel, Field


class TargetConfigTarget(BaseModel):
    id: str
    label: str
    month: str
    scope: Literal["combined"]
    targetRevenue: float


class TargetConfigRequest(BaseModel):
    version: str
    scope: str
    population: str
    approvalStatus: Literal["draft", "approved"] = "draft"
    updatedBy: str
    changeReason: str
    approvedBy: str | None = None
    thresholds: dict[str, float] = Field(default_factory=dict)
    targets: list[TargetConfigTarget] = Field(default_factory=list)


class TargetConfigResponse(BaseModel):
    config: dict = Field(default_factory=dict)
    history: list[dict] = Field(default_factory=list)
