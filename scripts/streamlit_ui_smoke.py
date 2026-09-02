"""Run a bounded Streamlit AppTest smoke flow and emit UI gate evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.evidence_models import canonical_fingerprint


_ACTIVE_VERSION_RE = re.compile(r"正式淨 GMV active version[：:]\s*([A-Za-z0-9._-]+)")
_REFUND_UPLOAD_INPUT_SELECTOR = 'section[data-testid="stFileUploaderDropzone"][aria-label^="上傳退款明細數據"] input[type="file"]'


def _download_record(item, media_manager) -> dict[str, object]:
    url = str(getattr(getattr(item, "proto", None), "url", ""))
    filename = url.rsplit("/", 1)[-1]
    if not filename:
        raise ValueError(f"download control has no media URL: {item.label}")
    try:
        media_file = media_manager._storage.get_file(filename)
    except Exception as exc:
        raise ValueError(f"download payload is not available: {item.label}") from exc
    payload = bytes(media_file.content)
    if not payload:
        raise ValueError(f"download payload is empty: {item.label}")
    return {
        "label": str(item.label),
        "url": url,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "validated": True,
    }


def _active_version_id(dataframes) -> str:
    candidates: set[str] = set()
    for item in dataframes:
        value = getattr(item, "value", None)
        if not isinstance(value, pd.DataFrame) or "version_id" not in value.columns:
            continue
        candidates.update(
            str(raw).strip()
            for raw in value["version_id"].tolist()
            if str(raw).strip()
        )
    if len(candidates) != 1:
        raise ValueError("Streamlit UI did not expose exactly one active GMV version")
    return next(iter(candidates))


def _required_downloads(download_items, media_manager) -> dict[str, dict[str, object]]:
    records = [_download_record(item, media_manager) for item in download_items]
    required: dict[str, dict[str, object]] = {}
    for record in records:
        label = str(record["label"])
        if "正式口徑" not in label:
            continue
        dimension = "total.detail" if label.startswith("下載總退款") else "paid.detail"
        if dimension not in required:
            required[dimension] = record
    if set(required) != {"total.detail", "paid.detail"}:
        raise ValueError("Streamlit UI did not expose validated total/paid GMV downloads")
    return required


def _required_browser_downloads(page, timeout_ms: int) -> dict[str, dict[str, object]]:
    required: dict[str, dict[str, object]] = {}
    for dimension, label in (("total.detail", "下載總退款正式口徑"), ("paid.detail", "下載已退款正式口徑")):
        button = page.get_by_role("button", name=label, exact=True)
        if button.count() != 1:
            raise ValueError(f"served Streamlit UI did not expose exactly one download control: {label}")
        with page.expect_download(timeout=timeout_ms) as download_info:
            button.click()
        download = download_info.value
        path = download.path()
        if path is None:
            raise ValueError(f"served Streamlit download has no payload path: {label}")
        payload = Path(path).read_bytes()
        if not payload:
            raise ValueError(f"served Streamlit download payload is empty: {label}")
        required[dimension] = {
            "label": label,
            "url": str(getattr(download, "url", "")),
            "filename": str(download.suggested_filename),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "validated": True,
            "clicked": True,
        }
    return required


def _served_active_version_id(page) -> str:
    matches = _ACTIVE_VERSION_RE.findall(page.locator("body").inner_text())
    candidates = {str(value).strip() for value in matches if str(value).strip()}
    if len(candidates) != 1:
        raise ValueError("served Streamlit UI did not expose exactly one active GMV version")
    return next(iter(candidates))


def _served_refund_upload_input(page):
    upload_input = page.locator(_REFUND_UPLOAD_INPUT_SELECTOR)
    if upload_input.count() != 1:
        raise ValueError("served Streamlit UI did not expose exactly one GMV refund uploader")
    return upload_input


def run_served_smoke(url: str, route: str, commit_sha: str, source_fingerprint: str, timeout: float) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright is required for served Streamlit UI acceptance") from exc

    timeout_ms = max(1000, int(timeout * 1000))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.get_by_text("GMV 排除訂單看板", exact=True).first.wait_for(timeout=timeout_ms)
            page.locator('[data-test-connection-state="CONNECTED"]').wait_for(timeout=timeout_ms)
            page.get_by_role("tab", name="GMV 排除訂單看板", exact=True).click()
            page.get_by_role("button", name="下載總退款正式口徑", exact=True).wait_for(timeout=timeout_ms)
            initial_version_id = _served_active_version_id(page)
            initial_body = page.locator("body").inner_text()
            initial_status = "CURRENT" if initial_version_id and "cache 尚未 ready" not in initial_body else "READY"
            _served_refund_upload_input(page).set_input_files({
                "name": "release-gate-refund.csv",
                "mimeType": "text/csv",
                "buffer": "退款單號,來源單據號,退款原幣金額,退款狀態\nrelease-gate-refund,330000000,0,已退款\n".encode("utf-8-sig"),
            })
            merge_button = page.get_by_role("button", name="上傳並合併退款資料庫", exact=True)
            merge_button.wait_for(timeout=timeout_ms)
            merge_button.click()
            deadline = time.monotonic() + timeout
            refreshed_version_id = ""
            while time.monotonic() < deadline:
                try:
                    refreshed_version_id = _served_active_version_id(page)
                except ValueError:
                    refreshed_version_id = ""
                if refreshed_version_id and refreshed_version_id != initial_version_id:
                    break
                page.wait_for_timeout(250)
            if not refreshed_version_id or refreshed_version_id == initial_version_id:
                raise RuntimeError("served Streamlit UI merge did not publish a new active GMV version")
            page.get_by_role("tab", name="GMV 排除訂單看板", exact=True).click()
            page.get_by_role("button", name="下載總退款正式口徑", exact=True).wait_for(timeout=timeout_ms)
            downloads = _required_browser_downloads(page, timeout_ms)
            tabs = page.get_by_role("tab").all_inner_texts()
            titles = page.locator("h1").all_inner_texts()
            rendered_text = ["GMV 排除訂單看板", f"正式淨 GMV active version：{refreshed_version_id}"]
            merge_flow = {"upload": "release-gate-refund.csv", "mergeControl": "上傳並合併退款資料庫", "clicked": True, "servedUrl": url, "initialVersionId": initial_version_id, "finalVersionId": refreshed_version_id}
            merge_status = "READY" if refreshed_version_id != initial_version_id and downloads else "BLOCKED"
            return build_evidence(route, commit_sha, source_fingerprint, titles, tabs, rendered_text, initial_status, merge_status, refreshed_version_id, refreshed_version_id, downloads, merge_flow, "playwright.sync_api")
        finally:
            browser.close()


def build_evidence(route: str, commit_sha: str, source_fingerprint: str, titles: list[str], tabs: list[str], rendered_text: list[str], initial_status: str, merge_status: str, active_version_id: str, refreshed_version_id: str, downloads: dict[str, dict[str, object]], merge_flow: dict[str, object], runner: str) -> dict:
    required_tab = "GMV 排除訂單看板"
    required_marker = "GMV 排除訂單看板"
    active_text = next((text for text in rendered_text if "正式淨 GMV active version" in text and "尚無" not in text), "")
    if required_tab not in tabs or not any(required_marker in text for text in rendered_text):
        raise ValueError("Streamlit UI did not render the required GMV flow")
    if not active_text:
        raise ValueError("Streamlit UI did not render an active GMV version")
    if not active_version_id or refreshed_version_id != active_version_id:
        raise ValueError("Streamlit UI active GMV version changed across rerun")
    if initial_status not in {"CURRENT", "READY"} or merge_status != "READY":
        raise ValueError("Streamlit UI merge flow did not reach a ready state")
    if set(downloads) != {"total.detail", "paid.detail"}:
        raise ValueError("Streamlit UI did not expose validated total/paid GMV downloads")
    rendered_fingerprint = canonical_fingerprint({"titles": titles, "tabs": tabs, "text": rendered_text, "activeVersionId": active_version_id, "downloads": downloads})
    return {
        "route": route,
        "initialStatus": initial_status,
        "mergeStatus": merge_status,
        "activeVersionId": active_version_id,
        "manifestSha256": rendered_fingerprint,
        "downloadedArtifacts": {key: int(value["bytes"]) for key, value in downloads.items()},
        "refreshedVersionId": refreshed_version_id,
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "uiSmoke": {"runner": runner, "titles": titles, "tabs": tabs, "renderedMarkers": [required_marker], "downloads": downloads, "interaction": "upload_click_rerun_and_payload_validation", "mergeFlow": merge_flow},
    }


def run_smoke(project_root: Path, route: str, commit_sha: str, source_fingerprint: str, timeout: float, served_url: str | None = None) -> dict:
    if served_url:
        return run_served_smoke(served_url, route, commit_sha, source_fingerprint, timeout)
    try:
        from streamlit.testing.v1 import AppTest
    except ImportError as exc:
        raise RuntimeError("streamlit.testing.v1.AppTest is required for UI acceptance") from exc
    from streamlit.testing.v1 import app_test

    captured_managers = []
    original_media_manager = app_test.MediaFileManager

    class CapturingMediaFileManager(original_media_manager):
        def __init__(self, storage):
            super().__init__(storage)
            captured_managers.append(self)

    app_test.MediaFileManager = CapturingMediaFileManager
    try:
        app = AppTest.from_file(str(project_root / "app.py")).run(timeout=timeout)
        first_version_id = _active_version_id(app.dataframe)
        initial_text = [str(item.value) for collection in (app.markdown, app.caption, app.info, app.warning, app.success) for item in collection]
        initial_downloads = _required_downloads(app.get("download_button"), captured_managers[-1])
        initial_status = "CURRENT" if not any("cache 尚未 ready" in text for text in initial_text) else "READY"
        uploader = app.file_uploader(key="GMV_EXCLUSION_UPLOAD")
        refund_upload = "退款單號,來源單據號,退款原幣金額,退款狀態\nrelease-gate-refund,330000000,0,已退款\n".encode("utf-8-sig")
        uploader.set_value(("release-gate-refund.csv", refund_upload, "text/csv"))
        app.run(timeout=timeout)
        merge_buttons = [button for button in app.button if button.label == "上傳並合併退款資料庫"]
        if len(merge_buttons) != 1:
            raise RuntimeError("Streamlit UI did not render exactly one merge control after upload")
        merge_buttons[0].click()
        app.run(timeout=timeout)
        refreshed_version_id = _active_version_id(app.dataframe)
    finally:
        app_test.MediaFileManager = original_media_manager
    if app.exception:
        raise RuntimeError(f"Streamlit AppTest exception: {app.exception}")
    if len(captured_managers) < 3:
        raise RuntimeError("Streamlit AppTest did not provide upload/merge/rerun media managers")
    if refreshed_version_id == first_version_id:
        raise RuntimeError("Streamlit UI merge control did not create a new active GMV version")
    titles = [item.value for item in app.title if isinstance(item.value, str)]
    tabs = [item.label for item in app.tabs if isinstance(item.label, str)]
    rendered_text = [str(item.value) for collection in (app.markdown, app.caption, app.info, app.warning, app.success) for item in collection]
    downloads = _required_downloads(app.get("download_button"), captured_managers[-1])
    merge_flow = {"upload": "release-gate-refund.csv", "mergeControl": "上傳並合併退款資料庫", "clicked": True, "initialVersionId": first_version_id, "finalVersionId": refreshed_version_id, "initialDownloads": initial_downloads}
    merge_status = "READY" if refreshed_version_id != first_version_id and downloads else "BLOCKED"
    return build_evidence(route, commit_sha, source_fingerprint, titles, tabs, rendered_text, initial_status, merge_status, refreshed_version_id, refreshed_version_id, downloads, merge_flow, "streamlit.testing.v1.AppTest")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--route", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--served-url")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)
    evidence = run_smoke(args.project_root.resolve(), args.route, args.commit_sha, args.source_fingerprint, args.timeout, args.served_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "evidenceFingerprint": canonical_fingerprint(evidence)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
