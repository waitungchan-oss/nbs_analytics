import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


def test_production_benchmark_cli_emits_isolated_matrix_json(tmp_path):
    result = subprocess.run(
        [
            str(PYTHON), "scripts/benchmark_gmv_production_rebuild.py",
            "--fixture", "synthetic", "--samples", "3", "--warm-reads", "3",
            "--ratios", "0.001,0.01", "--output-root", str(tmp_path / "bench"),
        ], cwd=PROJECT_ROOT, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schemaVersion"] == "gmv-production-rebuild-benchmark-v1"
    assert report["databaseMutated"] is False
    assert report["formalScope"] == "不含掛賬核銷與TT退款轉團款"
    assert report["sampleCount"] == 3
    assert set(report["cases"]) == {"ratio-0.001", "ratio-0.010", "over-guardrail"}
    assert all(item["status"] in {"PASS", "INCONCLUSIVE", "FAIL"} for item in report["cases"].values())


def test_production_benchmark_cli_rejects_project_runtime_path():
    result = subprocess.run(
        [
            str(PYTHON), "scripts/benchmark_gmv_production_rebuild.py",
            "--output-root", str(PROJECT_ROOT / ".nbs_runtime_cache"),
        ], cwd=PROJECT_ROOT, text=True, capture_output=True, check=False,
    )

    assert result.returncode != 0
    assert "isolated" in result.stderr.lower()
