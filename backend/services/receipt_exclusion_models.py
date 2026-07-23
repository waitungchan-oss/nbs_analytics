from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import pandas as pd


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ReceiptExclusionIdentity:
    receipt_no: str
    source_order_no: str
    exclusion_kind: str

    @property
    def candidate_id(self) -> str:
        return canonical_json_hash({
            "receiptNo": self.receipt_no,
            "sourceOrderNo": self.source_order_no,
            "exclusionKind": self.exclusion_kind,
        })


@dataclass(frozen=True)
class ReceiptExclusionRule:
    id: int
    identity: ReceiptExclusionIdentity
    status: str


@dataclass(frozen=True)
class ReceiptExclusionMatchResult:
    filtered_frame: pd.DataFrame
    matches: tuple[dict, ...]
    collisions: tuple[dict, ...]
