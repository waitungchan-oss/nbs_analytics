from io import BytesIO

from openpyxl import Workbook


def _workbook_bytes(*, amount=100, sheet_name="營收"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(["來源單據號", "收款原幣金額", "交易人數"])
    sheet.append(["A-001", amount, 2])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_equivalent_workbooks_pass_even_when_xlsx_metadata_differs():
    from backend.services.export_equivalence_service import compare_workbooks

    reference = _workbook_bytes()
    candidate = _workbook_bytes()
    report = compare_workbooks(
        reference,
        candidate,
        money_columns=("收款原幣金額",),
        stable_key_columns=("來源單據號",),
    )

    assert report.status == "PASS"
    assert report.mismatch_count == 0
    assert report.row_counts == {"營收": 1}


def test_equivalence_reports_value_row_and_schema_mismatches_with_a_bound():
    from backend.services.export_equivalence_service import compare_workbooks

    reference = _workbook_bytes()
    changed_value = _workbook_bytes(amount=101)
    changed_sheet = _workbook_bytes(sheet_name="不同")

    value_report = compare_workbooks(
        reference,
        changed_value,
        money_columns=("收款原幣金額",),
        stable_key_columns=("來源單據號",),
    )
    schema_report = compare_workbooks(reference, changed_sheet)

    assert value_report.status == "FAIL"
    assert value_report.mismatch_count == 1
    assert len(value_report.mismatch_examples) <= 20
    assert schema_report.status == "FAIL"
    assert schema_report.mismatch_count >= 1


def test_export_set_equivalence_requires_the_same_keys():
    from backend.services.export_equivalence_service import compare_export_sets

    reference = {"ex": _workbook_bytes()}
    candidate = {"ex_no_writeoff": _workbook_bytes()}

    report = compare_export_sets(reference, candidate)

    assert report.status == "FAIL"
    assert report.mismatch_count == 1
