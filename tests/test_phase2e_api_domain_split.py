import inspect

from backend.services import dashboard_service


def test_revenue_scope_service_owns_official_scope_helpers():
    from backend.services import revenue_scope_service

    assert hasattr(revenue_scope_service, "build_revenue_scope_frames")
    assert hasattr(revenue_scope_service, "REVENUE_SCOPE_LABEL")
    assert not hasattr(dashboard_service, "EXCLUDED_RECEIPT_TYPES")
    assert not hasattr(dashboard_service, "EXCLUDED_PAYMENT_METHODS")


def test_stability_service_owns_baseline_and_upload_gate():
    from backend.services import stability_service

    assert hasattr(stability_service, "build_stability_baseline")
    assert hasattr(stability_service, "build_phase2c_stability_gate")
    assert hasattr(stability_service, "PHASE2B_BASELINE_FILTERS")


def test_dashboard_service_no_longer_defines_domain_helpers():
    source = inspect.getsource(dashboard_service)

    assert "def build_revenue_scope_frames(" not in source
    assert "def _collect_revenue_scope_excluded_ids(" not in source
    assert "def _drop_revenue_scope_excluded_ids(" not in source
    assert "def _stability_baseline(" not in source
    assert "def build_phase2c_stability_gate(" not in source
