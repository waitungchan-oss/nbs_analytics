import json
import zipfile

from backend.services import diagnostics_service


def test_diagnostic_zip_contains_operational_evidence_but_not_business_data(tmp_path):
    runtime = tmp_path / ".nbs_runtime"
    logs = runtime / "logs"
    logs.mkdir(parents=True)
    (logs / "api.log").write_text("line\n" * 500, encoding="utf-8")
    (runtime / "services.json").write_text('{"status":"ready"}', encoding="utf-8")
    (tmp_path / "nbs_marketing_data.db").write_bytes(b"secret business data")
    (tmp_path / "upload.xlsx").write_bytes(b"secret upload")

    output = diagnostics_service.create_diagnostic_package(
        project_root=tmp_path,
        runtime_dir=runtime,
        status_payload={"status": "ready"},
        health_payload={"status": "ok", "latestAcceptance": {"id": 1, "gate": {"large": True}}},
        environment_payload={"python": "3.10"},
    )

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "system-status.json" in names
        assert "health.json" in names
        assert "logs/api.log" in names
        assert all(not name.endswith(".db") for name in names)
        assert all(not name.endswith(".xlsx") for name in names)
        health = json.loads(archive.read("health.json"))
        assert "gate" not in health["latestAcceptance"]

