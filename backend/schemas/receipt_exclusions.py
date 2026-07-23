from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ReceiptExclusionCandidate(BaseModel):
    candidateId: str
    sourceOrderNo: str
    receiptNo: str
    exclusionKind: str
    observedAmount: float
    affectedRevenue: float
    rowHash: str


class ReceiptExclusionProposal(BaseModel):
    schemaVersion: Literal["receipt-exclusion-proposal-v1"]
    status: Literal["confirmation_required"]
    operationId: str
    proposalFingerprint: str
    sourceBatchFingerprint: str
    diagnosedCheckKey: str
    expectedTotal: float
    actualTotal: float
    deltaAmount: float
    candidates: list[ReceiptExclusionCandidate]


class ReceiptExclusionRevocationRequest(BaseModel):
    previewFingerprint: str
    confirmedBy: str = "vue-local"
