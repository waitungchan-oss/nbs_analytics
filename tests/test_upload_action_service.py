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
