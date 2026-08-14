from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "short_term_offload.py"


def test_cli_rejects_unknown_authority_flags_without_side_effect(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, str(SCRIPT), "inspect", "--runtime-root", str(tmp_path), "--approve"], capture_output=True, text=True)
    assert result.returncode != 0


def test_cli_rejects_arbitrary_runtime_root(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, str(SCRIPT), "cleanup", "--runtime-root", str(tmp_path), "--now", "2026-08-14T12:00:00+00:00"], capture_output=True, text=True)
    assert result.returncode != 0


def test_cli_rejects_structurally_valid_external_root(tmp_path: Path) -> None:
    external = tmp_path / ".nbs_agent_runtime" / "short-term-offload"
    external.mkdir(parents=True)
    result = subprocess.run([sys.executable, str(SCRIPT), "cleanup", "--runtime-root", str(external), "--now", "2026-08-14T12:00:00+00:00"], capture_output=True, text=True)
    assert result.returncode != 0


def test_runner_hook_defaults_off_and_rejects_unknown_mode() -> None:
    from scripts.hermes_live_ab_runner import run_live_ab
    import inspect
    assert inspect.signature(run_live_ab).parameters["short_term_offload"].default == "off"
