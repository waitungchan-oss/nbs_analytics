import asyncio
import pytest


class CountingUpload:
    filename = "main.xlsx"

    def __init__(self):
        self.read_count = 0

    async def read(self):
        self.read_count += 1
        return b"main"


def test_busy_upload_does_not_read_file(monkeypatch):
    from backend.services import upload_action_service
    from backend.services.upload_lock_service import UploadBusyError

    upload = CountingUpload()
    monkeypatch.setattr(
        upload_action_service,
        "acquire_upload_lease",
        lambda **kwargs: (_ for _ in ()).throw(UploadBusyError({"entry_point": "streamlit"})),
    )

    with pytest.raises(UploadBusyError):
        asyncio.run(upload_action_service.run_vue_upload_action(main_file=upload))

    assert upload.read_count == 0


def test_confirmation_is_forwarded_to_canonical_orchestrator(monkeypatch):
    from backend.services import upload_action_service

    captured = {}
    monkeypatch.setattr(upload_action_service, "acquire_upload_lease", lambda **kwargs: _Lease())
    monkeypatch.setattr(
        upload_action_service, "execute_upload_operation",
        lambda *args, **kwargs: captured.update(kwargs) or _Execution(),
    )
    monkeypatch.setattr(upload_action_service, "build_system_health", lambda **kwargs: {})

    result = asyncio.run(upload_action_service.run_vue_upload_action(
        main_file=CountingUpload(),
        receipt_exclusion_confirmation={"proposalFingerprint": "p1", "selectedCandidateIds": ["c1"]},
    ))

    assert captured["receipt_exclusion_confirmation"]["proposalFingerprint"] == "p1"
    assert result["status"] == "blocked"


class _Lease:
    operation = object()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Execution:
    response = {"status": "blocked", "message": "blocked"}
    entity_audit = {}
    anomaly_frame = __import__("pandas").DataFrame()
