import pickle
from io import BytesIO
from pathlib import Path

import pandas as pd
import pipeline
import pytest


def _seller_rows(rows):
    return pd.DataFrame(
        [
            {
                "來源單據號": order_id,
                "收款單號": receipt_id,
                "收款時間": receipt_time,
                "統一日期": receipt_time[:10],
                "銷售點": sales_point,
                "銷售員": salesperson,
                "收款原幣金額": amount,
                "交易時間": receipt_time,
                "行程天數": 3,
                "數量": 1,
                "團名稱": "測試團",
                "來源報表標籤": "旅行團",
            }
            for order_id, receipt_id, receipt_time, sales_point, salesperson, amount in rows
        ]
    )


def test_builder_captures_all_source_rows_then_selects_only_approved_beta_sellers(monkeypatch):
    from backend.services import beta_seller_scope_service

    calls = []
    tours = _seller_rows(
        [
            ("ZZ-TOUR-01", "R-01", "2026-05-01 10:00:00", "新銷售點", "002785 朱淑宜", 100),
            ("XX-TOUR-02", "R-02", "2026-05-02 10:00:00", "任意銷售點", "004014 杨慧如", 200),
            ("OTHER-TOUR", "R-03", "2026-05-02 11:00:00", "任意銷售點", "名單外", 300),
            ("EXTRA-TOUR", "R-06", "2026-05-02 12:00:00", "任意銷售點", "新合資格銷售員", 350),
        ]
    )
    others = _seller_rows(
        [
            ("OTHER-TICKET-01", "R-04", "2026-05-03 10:00:00", "任意銷售點", "NL9 麦浩贤", 400),
            ("OTHER-TICKET-02", "R-05", "2026-05-03 11:00:00", "任意銷售點", "", 500),
        ]
    )

    def fake_process(*args, **kwargs):
        calls.append(kwargs)
        return tours, others, pd.DataFrame(), {"summary": pd.DataFrame()}

    monkeypatch.setattr(beta_seller_scope_service, "process_raw_files", fake_process)
    ticket_source = BytesIO(b"tickets")
    ticket_source.name = "tickets.xlsx"
    ticket_source.beta_source_role = "ticket"
    transport_source = BytesIO(b"transport")
    transport_source.name = "transport.xlsx"
    transport_source.beta_source_role = "transport"
    package_source = BytesIO(b"package")
    package_source.name = "package.xlsx"
    package_source.beta_source_role = "package"
    hotel_source = BytesIO(b"hotel")
    hotel_source.name = "hotel.xlsx"
    hotel_source.beta_source_role = "hotel"

    beta_tour, beta_others, coverage = beta_seller_scope_service.build_beta_seller_scope_frames(
        "main.xlsx",
        "tour.xlsx",
        [transport_source, ticket_source, package_source, hotel_source],
        {"195": "舊銷售點"},
        ["1950506"],
        list(pipeline.BETA_COMPARISON_SALESPEOPLE_TO_CATEGORY),
    )

    assert calls[0]["beta_capture_mode"] is True
    assert calls[0]["return_entity_audit"] is True
    assert set(beta_tour["銷售員"]) == {"002785 朱淑宜", "004014 杨慧如"}
    assert set(beta_tour["銷售點"]) == {"新銷售點", "任意銷售點"}
    assert set(beta_others["銷售員"]) == {"NL9 麦浩贤"}
    assert len(coverage["sourceFingerprint"]) == 64
    assert coverage["audit"]["inputRows"] == 6
    assert coverage["audit"]["includedRows"] == 3
    assert coverage["audit"]["blankSalespersonRows"] == 1
    assert coverage["audit"]["unknownSalespersonRows"] == 2


@pytest.mark.parametrize(
    "other_names",
    [
        ["未識別副表.xlsx"],
        ["交通副表.xlsx", "交通補充.xlsx"],
        ["門券套票.xlsx"],
    ],
)
def test_unverified_or_ambiguous_source_role_mapping_excludes_batch_from_beta(
    monkeypatch, other_names
):
    from backend.services import beta_seller_scope_service

    rows = _seller_rows(
        [("ORDER-1", "R-1", "2026-05-01 10:00:00", "任意銷售點", "002785 朱淑宜", 100)]
    )
    monkeypatch.setattr(
        beta_seller_scope_service,
        "process_raw_files",
        lambda *args, **kwargs: (rows, rows, pd.DataFrame(), {"summary": pd.DataFrame()}),
    )

    tour, others, coverage = beta_seller_scope_service.build_beta_seller_scope_frames(
        ("main.xlsx", b"main"),
        ("旅行團.xlsx", b"tour"),
        [(name, name.encode("utf-8")) for name in other_names],
        {}, [], list(pipeline.BETA_COMPARISON_SALESPEOPLE_TO_CATEGORY),
    )

    assert tour.empty and others.empty
    assert coverage["sourceRoleMappingVerified"] is False
    assert coverage["sourceRoleMappingIssues"]


def test_declared_source_role_conflicting_with_filename_blocks_beta_batch(monkeypatch):
    from backend.services import beta_seller_scope_service

    rows = _seller_rows(
        [("ORDER-1", "R-1", "2026-05-01 10:00:00", "任意銷售點", "002785 朱淑宜", 100)]
    )
    monkeypatch.setattr(
        beta_seller_scope_service,
        "process_raw_files",
        lambda *args, **kwargs: (rows, rows, pd.DataFrame(), {"summary": pd.DataFrame()}),
    )

    def tagged_source(name, role):
        source = BytesIO(b"test-workbook")
        source.name = name
        source.beta_source_role = role
        return source

    other_sources = [
        tagged_source("交通副表.xlsx", "transport"),
        tagged_source("門券副表.xlsx", "ticket"),
        tagged_source("套票副表.xlsx", "package"),
        tagged_source("酒店副表.xlsx", "hotel"),
    ]
    other_sources[1].beta_source_role = "transport"

    beta_tour, beta_others, coverage = beta_seller_scope_service.build_beta_seller_scope_frames(
        "主表.xlsx",
        "旅行團副表.xlsx",
        other_sources,
        {},
        [],
        list(pipeline.BETA_COMPARISON_SALESPEOPLE_TO_CATEGORY),
    )

    assert beta_tour.empty and beta_others.empty
    assert coverage["sourceRoleMappingVerified"] is False
    assert coverage["sourceRoleMappingIssues"]


def test_missing_required_source_role_blocks_beta_before_processing(monkeypatch):
    from backend.services import beta_seller_scope_service

    calls = []
    rows = _seller_rows(
        [("ORDER-1", "R-1", "2026-05-01 10:00:00", "任意銷售點", "002785 朱淑宜", 100)]
    )

    def process_runner(*args, **kwargs):
        calls.append("processed")
        return rows, rows, pd.DataFrame(), {"summary": pd.DataFrame()}

    monkeypatch.setattr(beta_seller_scope_service, "process_raw_files", process_runner)

    def tagged_source(name, role):
        source = BytesIO(b"test-workbook")
        source.name = name
        source.beta_source_role = role
        return source

    sources = [
        tagged_source("交通副表.xlsx", "transport"),
        tagged_source("門券副表.xlsx", "ticket"),
        tagged_source("套票副表.xlsx", "package"),
    ]
    tour, others, coverage = beta_seller_scope_service.build_beta_seller_scope_frames(
        "主表.xlsx", "旅行團副表.xlsx", sources, {}, [],
        list(pipeline.BETA_COMPARISON_SALESPEOPLE_TO_CATEGORY),
    )

    assert calls == []
    assert tour.empty and others.empty
    assert coverage["sourceRoleMappingVerified"] is False
    assert coverage["missingSourceRoles"] == ["hotel"]
    assert "missing_required:hotel" in coverage["sourceRoleMappingIssues"]


def test_cache_merge_drops_stale_verification_for_incomplete_source_roles():
    from backend.services import beta_seller_scope_service

    old = {
        "sourceFingerprint": "a" * 64,
        "sourceRoles": list(beta_seller_scope_service.BETA_REQUIRED_SOURCE_ROLES),
        "sourceRoleMappingVerified": True,
        "requiredDateIntervals": [{"start": "2026-05-01", "end": "2026-05-01"}],
        "dateIntervals": [{"start": "2026-05-01", "end": "2026-05-01"}],
        "verification": {
            "preflightStatus": "matched",
            "driftStatus": "no_drift",
            "entityMatchStatus": "matched",
            "liveDbUnchanged": True,
        },
    }
    incoming = {
        "sourceFingerprint": "b" * 64,
        "sourceRoles": [role for role in beta_seller_scope_service.BETA_REQUIRED_SOURCE_ROLES if role != "hotel"],
        "sourceRoleMappingVerified": False,
        "requiredDateIntervals": [{"start": "2026-05-01", "end": "2026-05-01"}],
        "dateIntervals": [{"start": "2026-05-01", "end": "2026-05-01"}],
    }

    merged = beta_seller_scope_service._merge_coverage(old, incoming)

    assert merged["sourceRoleMappingVerified"] is False
    assert merged["missingSourceRoles"] == ["hotel"]
    assert merged["complete"] is False
    assert "preflightStatus" not in merged["verification"]
    assert merged["verification"]["sourceRoleMappingStatus"] == "unverified"
    assert merged["verification"]["sourceRolesStatus"] == "incomplete"


def test_v2_cache_upserts_by_receipt_id_preserves_multiple_receipts_per_order_and_legacy_bytes(tmp_path):
    from backend.services import beta_seller_scope_service

    cache_path = tmp_path / "beta_seller_scope_v2.pkl"
    legacy_path = tmp_path / "beta_only_exception.pkl"
    legacy_path.write_bytes(b"legacy-beta-cache-must-not-change")
    legacy_before = legacy_path.read_bytes()
    first = _seller_rows(
        [
            ("ORDER-1", "RECEIPT-1", "2026-05-01 10:00:00", "店A", "002785 朱淑宜", 100),
            ("ORDER-1", "RECEIPT-2", "2026-05-01 11:00:00", "店A", "002785 朱淑宜", 50),
        ]
    )
    update = first.iloc[[0]].copy()
    update.loc[:, "收款原幣金額"] = 250
    first_coverage = {
        "sourceFingerprint": "a" * 64,
        "dateIntervals": [{"start": "2025-01-01", "end": "2025-01-03"}],
        "requiredDateIntervals": [
            {"start": "2025-01-01", "end": "2025-01-03"},
            {"start": "2025-02-01", "end": "2025-02-02"},
        ],
        "sourceRoles": list(beta_seller_scope_service.BETA_REQUIRED_SOURCE_ROLES),
        "sourceRoleMappingVerified": True,
        "complete": False,
        "status": "partial",
        "verification": {
            "preflightStatus": "matched", "driftStatus": "no_drift",
            "entityMatchStatus": "matched", "sourceRolesStatus": "complete",
            "liveDbUnchanged": True,
        },
    }
    second_coverage = {
        "sourceFingerprint": "b" * 64,
        "dateIntervals": [{"start": "2025-02-01", "end": "2025-02-02"}],
        "requiredDateIntervals": [
            {"start": "2025-01-01", "end": "2025-01-03"},
            {"start": "2025-02-01", "end": "2025-02-02"},
        ],
        "sourceRoles": list(beta_seller_scope_service.BETA_REQUIRED_SOURCE_ROLES),
        "sourceRoleMappingVerified": True,
        "complete": False,
        "status": "partial",
        "verification": {
            "preflightStatus": "matched", "driftStatus": "no_drift",
            "entityMatchStatus": "matched", "sourceRolesStatus": "complete",
            "liveDbUnchanged": True,
        },
    }

    beta_seller_scope_service.persist_beta_seller_scope_frames(
        first, pd.DataFrame(), first_coverage, cache_path=cache_path
    )
    beta_seller_scope_service.persist_beta_seller_scope_frames(
        update, pd.DataFrame(), second_coverage, cache_path=cache_path, data_generation=7
    )
    loaded_tour, loaded_others, coverage = beta_seller_scope_service.load_beta_seller_scope_frames(
        cache_path=cache_path
    )

    assert len(loaded_tour) == 2
    amounts_by_receipt = dict(zip(loaded_tour["收款單號"], loaded_tour["收款原幣金額"]))
    assert amounts_by_receipt == {"RECEIPT-1": 250, "RECEIPT-2": 50}
    assert loaded_others.empty
    assert coverage["version"] == beta_seller_scope_service.BETA_SELLER_SCOPE_CACHE_VERSION
    assert coverage["dateIntervals"] == [
        {"start": "2025-01-01", "end": "2025-01-03"},
        {"start": "2025-02-01", "end": "2025-02-02"},
    ]
    assert coverage["sourceFingerprints"] == ["a" * 64, "b" * 64]
    assert coverage["dataGeneration"] == 7
    assert coverage["rowsFingerprintMatched"] is True
    assert coverage["complete"] is True
    assert legacy_path.read_bytes() == legacy_before


def test_v2_cache_loader_rejects_wrong_version_without_mutating_file(tmp_path):
    from backend.services import beta_seller_scope_service

    cache_path = Path(tmp_path) / "beta_seller_scope_v2.pkl"
    with cache_path.open("wb") as handle:
        pickle.dump({"version": "unexpected", "tour": _seller_rows([]), "others": pd.DataFrame()}, handle)
    before = cache_path.read_bytes()

    tour, others, coverage = beta_seller_scope_service.load_beta_seller_scope_frames(cache_path=cache_path)

    assert tour.empty and others.empty and coverage == {}
    assert cache_path.read_bytes() == before


def test_v2_cache_loader_rejects_rows_when_fingerprint_does_not_match(tmp_path):
    from backend.services import beta_seller_scope_service

    cache_path = tmp_path / "beta_seller_scope_v2.pkl"
    tour = _seller_rows(
        [("ORDER-1", "RECEIPT-1", "2026-05-01 10:00:00", "店A", "002785 朱淑宜", 100)]
    )
    coverage = {
        "complete": True,
        "status": "complete",
        "verification": {"preflightStatus": "matched"},
    }
    with cache_path.open("wb") as handle:
        pickle.dump(
            {
                "version": beta_seller_scope_service.BETA_SELLER_SCOPE_CACHE_VERSION,
                "tour": tour,
                "others": pd.DataFrame(),
                "coverage": coverage,
                "dataGeneration": 3,
                "rowsFingerprint": "0" * 64,
            },
            handle,
        )

    loaded_tour, loaded_others, loaded_coverage = (
        beta_seller_scope_service.load_beta_seller_scope_frames(cache_path=cache_path)
    )

    assert loaded_tour.empty
    assert loaded_others.empty
    assert loaded_coverage["complete"] is False
    assert loaded_coverage["status"] == "invalid_cache"
    assert loaded_coverage["rowsFingerprintMatched"] is False


def test_cache_loader_rejects_generation_mismatch_and_missing_expected_generation(tmp_path):
    from backend.services import beta_seller_scope_service

    cache_path = tmp_path / "beta_seller_scope_v2.pkl"
    frame = _seller_rows(
        [("ORDER-1", "R-1", "2026-05-01 10:00:00", "店A", "002785 朱淑宜", 100)]
    )
    coverage = {
        "complete": True,
        "status": "complete",
        "verification": {},
    }
    with cache_path.open("wb") as handle:
        pickle.dump(
            {
                "version": beta_seller_scope_service.BETA_SELLER_SCOPE_CACHE_VERSION,
                "tour": frame,
                "others": pd.DataFrame(),
                "coverage": coverage,
                "dataGeneration": 3,
                "rowsFingerprint": beta_seller_scope_service._frames_fingerprint(frame, pd.DataFrame()),
            },
            handle,
        )

    stale_tour, _, stale = beta_seller_scope_service.load_beta_seller_scope_frames(
        cache_path=cache_path, expected_data_generation=4
    )
    unknown_tour, _, unknown = beta_seller_scope_service.load_beta_seller_scope_frames(
        cache_path=cache_path, expected_data_generation=None
    )

    assert stale_tour.empty and stale["status"] == "stale_generation"
    assert unknown_tour.empty and unknown["status"] == "stale_generation"


def test_cached_coverage_keeps_date_holes_until_the_required_union_is_complete(tmp_path):
    from backend.services import beta_seller_scope_service

    cache_path = tmp_path / "beta_seller_scope_v2.pkl"
    required = [{"start": "2026-01-01", "end": "2026-01-04"}]
    batches = [
        ("R-1", "2026-01-01", [{"start": "2026-01-01", "end": "2026-01-02"}]),
        ("R-2", "2026-01-04", [{"start": "2026-01-04", "end": "2026-01-04"}]),
        ("R-3", "2026-01-03", [{"start": "2026-01-03", "end": "2026-01-03"}]),
    ]

    for index, (receipt_id, day, intervals) in enumerate(batches):
        tour = _seller_rows(
            [(f"ORDER-{index}", receipt_id, f"{day} 10:00:00", "任意銷售點", "002785 朱淑宜", 100)]
        )
        beta_seller_scope_service.persist_beta_seller_scope_frames(
            tour,
            pd.DataFrame(),
            {
                "sourceFingerprint": str(index) * 64,
                "sourceRoles": ["main", "tour", "transport", "ticket", "package", "hotel"],
                "sourceRoleMappingVerified": True,
                "requiredSourceRoles": ["main", "tour", "transport", "ticket", "package", "hotel"],
                "missingSourceRoles": [],
                "dateIntervals": intervals,
                "requiredDateIntervals": required,
                "complete": index == 2,
                "status": "complete" if index == 2 else "partial",
                "verification": {
                    "preflightStatus": "matched",
                    "driftStatus": "no_drift",
                    "entityMatchStatus": "matched",
                    "liveDbUnchanged": True,
                },
            },
            cache_path=cache_path,
        )
        _, _, coverage = beta_seller_scope_service.load_beta_seller_scope_frames(cache_path=cache_path)
        if index < 2:
            assert coverage["complete"] is False
            expected_missing_end = "2026-01-04" if index == 0 else "2026-01-03"
            assert coverage["missingDateIntervals"] == [
                {"start": "2026-01-03", "end": expected_missing_end}
            ]
        else:
            assert coverage["complete"] is True
            assert coverage["missingDateIntervals"] == []


def test_cached_coverage_does_not_accumulate_roles_from_incomplete_batches(tmp_path):
    from backend.services import beta_seller_scope_service

    cache_path = tmp_path / "beta_seller_scope_v2.pkl"
    required_roles = ["main", "tour", "transport", "ticket", "package", "hotel"]
    batches = [
        (["main", "tour", "transport"], ["ticket", "package", "hotel"]),
        (["ticket", "package", "hotel"], []),
    ]
    for index, (source_roles, missing_roles) in enumerate(batches):
        day = f"2026-05-0{index + 1}"
        frame = _seller_rows(
            [(f"ORDER-{index}", f"R-{index}", f"{day} 10:00:00", "任意銷售點", "002785 朱淑宜", 100)]
        )
        beta_seller_scope_service.persist_beta_seller_scope_frames(
            frame,
            pd.DataFrame(),
            {
                "sourceFingerprint": str(index + 1) * 64,
                "sourceRoles": source_roles,
                "sourceRoleMappingVerified": True,
                "requiredSourceRoles": required_roles,
                "missingSourceRoles": missing_roles,
                "dateIntervals": [{"start": day, "end": day}],
                "requiredDateIntervals": [{"start": "2026-05-01", "end": "2026-05-02"}],
                "verification": {
                    "preflightStatus": "matched",
                    "driftStatus": "no_drift",
                    "entityMatchStatus": "matched",
                    "sourceRolesStatus": "incomplete" if missing_roles else "complete",
                    "sourceRoleMappingStatus": "verified",
                    "liveDbUnchanged": True,
                },
            },
            cache_path=cache_path,
        )
        loaded_tour, loaded_others, coverage = beta_seller_scope_service.load_beta_seller_scope_frames(
            cache_path=cache_path
        )
        if index == 0:
            assert loaded_tour.empty
            assert loaded_others.empty

    assert set(coverage["sourceRoles"]) == {"ticket", "package", "hotel"}
    assert coverage["missingSourceRoles"] == ["main", "tour", "transport"]
    assert coverage["verification"]["sourceRolesStatus"] == "incomplete"
    assert coverage["complete"] is False
    assert loaded_tour.empty
    assert loaded_others.empty


def test_entity_mismatch_keeps_cached_scope_incomplete(tmp_path):
    from backend.services import beta_seller_scope_service

    cache_path = tmp_path / "beta_seller_scope_v2.pkl"
    complete_intervals = [{"start": "2026-05-01", "end": "2026-05-01"}]
    frame = _seller_rows(
        [("ORDER-1", "RECEIPT-1", "2026-05-01 10:00:00", "任意銷售點", "002785 朱淑宜", 100)]
    )
    beta_seller_scope_service.persist_beta_seller_scope_frames(
        frame,
        pd.DataFrame(),
        {
            "sourceFingerprint": "c" * 64,
            "sourceRoles": list(beta_seller_scope_service.BETA_REQUIRED_SOURCE_ROLES),
            "sourceRoleMappingVerified": True,
            "missingSourceRoles": [],
            "dateIntervals": complete_intervals,
            "requiredDateIntervals": complete_intervals,
            "verification": {
                "preflightStatus": "matched",
                "driftStatus": "no_drift",
                "entityMatchStatus": "unmatched_or_unverified",
                "liveDbUnchanged": True,
            },
        },
        cache_path=cache_path,
        data_generation=9,
    )

    _, _, coverage = beta_seller_scope_service.load_beta_seller_scope_frames(
        cache_path=cache_path, expected_data_generation=9
    )

    assert coverage["verification"]["entityMatchStatus"] == "unmatched_or_unverified"
    assert coverage["complete"] is False
    assert coverage["status"] == "partial"


def test_legacy_zero_orphan_gate_cache_is_revalidated_read_only_for_matching_generation(tmp_path):
    from backend.services import beta_seller_scope_service

    cache_path = tmp_path / "beta_seller_scope_v2.pkl"
    frame = _seller_rows(
        [("ORDER-1", "RECEIPT-1", "2026-05-01 10:00:00", "任意銷售點", "002785 朱淑宜", 100)]
    )
    interval = [{"start": "2026-05-01", "end": "2026-05-01"}]
    beta_seller_scope_service.persist_beta_seller_scope_frames(
        frame, pd.DataFrame(),
        {
            "sourceFingerprint": "d" * 64,
            "sourceRoles": list(beta_seller_scope_service.BETA_REQUIRED_SOURCE_ROLES),
            "sourceRoleMappingVerified": True,
            "missingSourceRoles": [],
            "dateIntervals": interval,
            "requiredDateIntervals": interval,
            "audit": {"inputRows": 2},
            "entityAuditSummary": [
                {"指標": name, "數值": count} for name, count in {
                    "主表有效來源單據號數": 2,
                    "副表有效交易號碼數": 2,
                    "成功匹配唯一單號數": 1,
                    "成功匹配主表行數": 1,
                    "主表未匹配行數": 1,
                    "副表未落主表單號數": 1,
                }.items()
            ],
            "verification": {
                "preflightStatus": "matched", "driftStatus": "no_drift",
                "entityMatchStatus": "unmatched_or_unverified", "liveDbUnchanged": True,
            },
        },
        cache_path=cache_path, data_generation=9,
    )
    before = cache_path.read_bytes()
    loaded, _, coverage = beta_seller_scope_service.load_beta_seller_scope_frames(
        cache_path=cache_path, expected_data_generation=9
    )
    assert len(loaded) == 1
    assert coverage["complete"] is True
    assert coverage["verification"]["entityMatchStatus"] == "matched"
    assert cache_path.read_bytes() == before
    stale, _, stale_coverage = beta_seller_scope_service.load_beta_seller_scope_frames(
        cache_path=cache_path, expected_data_generation=10
    )
    assert stale.empty and stale_coverage["status"] == "stale_generation"


def test_clear_beta_seller_scope_cache_removes_rows_and_coverage(tmp_path):
    from backend.services import beta_seller_scope_service

    cache_path = tmp_path / "beta_seller_scope_v2.pkl"
    cache_path.write_bytes(b"previous seller rows and coverage")

    beta_seller_scope_service.clear_beta_seller_scope_cache(cache_path=cache_path)

    assert not cache_path.exists()
    tour, others, coverage = beta_seller_scope_service.load_beta_seller_scope_frames(cache_path=cache_path)
    assert tour.empty and others.empty and coverage == {}


def test_tuple_wrapped_secondary_sources_preserve_declared_roles(monkeypatch):
    from backend.services import beta_seller_scope_service

    monkeypatch.setattr(
        beta_seller_scope_service,
        "process_raw_files",
        lambda *args, **kwargs: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}),
    )
    sources = []
    for role, name in (
        ("transport", "交通副表.xlsx"),
        ("ticket", "門券副表.xlsx"),
        ("package", "套票副表.xlsx"),
        ("hotel", "酒店副表.xlsx"),
    ):
        content = BytesIO(name.encode("utf-8"))
        content.name = name
        content.beta_source_role = role
        sources.append((name, content))

    _, _, coverage = beta_seller_scope_service.build_beta_seller_scope_frames(
        "main.xlsx", "旅行團副表.xlsx", sources, {}, [], []
    )

    assert coverage["sourceRoleMappingVerified"] is True
    assert coverage["missingSourceRoles"] == []
