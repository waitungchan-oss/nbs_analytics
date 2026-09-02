import pytest

from scripts.streamlit_ui_smoke import build_evidence


DOWNLOADS = {
    "total.detail": {"label": "下載總退款正式口徑", "url": "/mock/media/total.bin", "bytes": 10, "sha256": "c" * 64, "validated": True},
    "paid.detail": {"label": "下載已退款正式口徑", "url": "/mock/media/paid.bin", "bytes": 11, "sha256": "d" * 64, "validated": True},
}
MERGE_FLOW = {"upload": "release-gate-refund.csv", "mergeControl": "上傳並合併退款資料庫", "clicked": True, "initialVersionId": "v0", "finalVersionId": "v1", "initialDownloads": DOWNLOADS}


def test_streamlit_smoke_evidence_is_bound_and_records_real_runner():
    evidence = build_evidence("http://127.0.0.1:8765/", "a" * 40, "b" * 64, ["Agent Operations"], ["GMV 排除訂單看板"], ["GMV 排除訂單看板", "正式淨 GMV active version"], "CURRENT", "READY", "v1", "v1", DOWNLOADS, MERGE_FLOW, "streamlit.testing.v1.AppTest")
    assert evidence["commitSha"] == "a" * 40
    assert evidence["sourceFingerprint"] == "b" * 64
    assert evidence["uiSmoke"]["runner"] == "streamlit.testing.v1.AppTest"
    assert evidence["activeVersionId"] == "v1"
    assert evidence["downloadedArtifacts"] == {"total.detail": 10, "paid.detail": 11}
    assert all(item["validated"] for item in evidence["uiSmoke"]["downloads"].values())
    assert evidence["uiSmoke"]["mergeFlow"]["clicked"] is True


def test_streamlit_smoke_requires_rendered_gmv_flow():
    with pytest.raises(ValueError, match="required GMV"):
        build_evidence("http://127.0.0.1:8765/", "a" * 40, "b" * 64, [], [], [], "CURRENT", "READY", "v1", "v1", DOWNLOADS, MERGE_FLOW, "streamlit.testing.v1.AppTest")


def test_streamlit_smoke_requires_active_version_and_downloads():
    with pytest.raises(ValueError, match="active GMV version"):
        build_evidence("http://127.0.0.1:8765/", "a" * 40, "b" * 64, [], ["GMV 排除訂單看板"], ["GMV 排除訂單看板", "尚無正式淨 GMV active version"], "CURRENT", "READY", "v1", "v1", {}, MERGE_FLOW, "streamlit.testing.v1.AppTest")


def test_streamlit_smoke_requires_stable_version_and_validated_downloads():
    with pytest.raises(ValueError, match="changed across rerun"):
        build_evidence("http://127.0.0.1:8765/", "a" * 40, "b" * 64, [], ["GMV 排除訂單看板"], ["GMV 排除訂單看板", "正式淨 GMV active version"], "CURRENT", "READY", "v1", "v2", DOWNLOADS, MERGE_FLOW, "streamlit.testing.v1.AppTest")
