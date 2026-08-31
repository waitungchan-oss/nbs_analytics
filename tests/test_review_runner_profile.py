import json

import pytest


def _profile(tmp_path, model="gpt-5.4"):
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nprintf 'codex-cli 0.142.5\\n'\n", encoding="utf-8")
    executable.chmod(0o755)
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [{"slug": model, "base_instructions": "base"}]}), encoding="utf-8")
    return executable, cache


def test_preflight_accepts_supported_runner_and_cache(tmp_path):
    from backend.agents.review_runner_profile import RunnerProfile, preflight_runner

    executable, cache = _profile(tmp_path)
    result = preflight_runner(RunnerProfile(str(executable), "gpt-5.4", cache))

    assert result.status == "ready"
    assert result.cache_schema_status == "compatible"
    assert result.cli_version == "codex-cli 0.142.5"


def test_profile_adapter_returns_explicit_local_cli_identity(tmp_path):
    from backend.agents.review_runner_profile import RunnerProfile

    executable, cache = _profile(tmp_path)
    identity = RunnerProfile(str(executable), "gpt-5.4", cache).to_runner_identity(
        profile_name="strict-review", execution_environment="local-macos", provider="codex"
    )

    assert identity.transport == "local_cli"
    assert identity.runner_id == "strict-review"
    assert identity.model == "gpt-5.4"


def test_preflight_blocks_missing_base_instructions(tmp_path):
    from backend.agents.review_runner_profile import RunnerProfile, preflight_runner

    executable, cache = _profile(tmp_path)
    cache.write_text(json.dumps({"models": [{"slug": "gpt-5.4"}]}), encoding="utf-8")

    result = preflight_runner(RunnerProfile(str(executable), "gpt-5.4", cache))

    assert result.status == "blocked_runtime"
    assert result.cache_schema_status == "incompatible"
    assert "incompatible" in result.diagnostics[0]


def test_preflight_blocks_model_not_in_cache(tmp_path):
    from backend.agents.review_runner_profile import RunnerProfile, preflight_runner

    executable, cache = _profile(tmp_path, model="gpt-5.4")

    result = preflight_runner(RunnerProfile(str(executable), "gpt-5.5", cache))

    assert result.status == "blocked_runtime"
    assert "requested model" in result.diagnostics[0]


def test_preflight_cli_returns_json_and_ready_exit_code(tmp_path, capsys):
    from scripts.codex_runner_preflight import main

    executable, cache = _profile(tmp_path)
    assert main(["--model", "gpt-5.4", "--executable", str(executable), "--cache", str(cache)]) == 0
    assert '"status": "ready"' in capsys.readouterr().out


def test_preflight_accepts_modern_model_messages_for_matching_cli(tmp_path):
    from backend.agents.review_runner_profile import RunnerProfile, preflight_runner

    executable, cache = _profile(tmp_path)
    cache.write_text(json.dumps({
        "models": [{"slug": "gpt-5.4", "model_messages": {"instructions_template": "base"}}],
    }), encoding="utf-8")
    executable.write_text("#!/bin/sh\nprintf 'codex-cli 0.150.1\\n'\n", encoding="utf-8")

    result = preflight_runner(RunnerProfile(str(executable), "gpt-5.4", cache))

    assert result.status == "ready"


def test_preflight_rejects_legacy_cache_for_modern_cli(tmp_path):
    from backend.agents.review_runner_profile import RunnerProfile, preflight_runner

    executable, cache = _profile(tmp_path)
    executable.write_text("#!/bin/sh\nprintf 'codex-cli 0.150.1\\n'\n", encoding="utf-8")

    result = preflight_runner(RunnerProfile(str(executable), "gpt-5.4", cache))

    assert result.status == "blocked_runtime"
    assert result.cache_schema_status == "incompatible"


def test_preflight_blocks_cli_below_configured_version_floor(tmp_path):
    from backend.agents.review_runner_profile import RunnerProfile, preflight_runner

    executable, cache = _profile(tmp_path)
    result = preflight_runner(RunnerProfile(str(executable), "gpt-5.4", cache, cli_version_floor="0.150.0"))

    assert result.status == "blocked_runtime"
    assert "version floor" in result.diagnostics[0]


# ---------------------------------------------------------------------------
# Task 3: live runner capability probe / RunnerCapabilityReceipt
# ---------------------------------------------------------------------------


def _ready_profile(tmp_path, model="gpt-5.4"):
    """Executable that answers --version and echoes a valid probe JSON."""
    executable, cache = _profile(tmp_path, model=model)
    executable.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  --version) printf 'codex-cli 0.142.5\\n';;\n"
        "  *) printf '{\"status\":\"ok\",\"model\":\"%s\"}\\n' \"$5\";;\n"
        "esac\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, cache


def _ready_profile_instance(tmp_path, model="gpt-5.4"):
    from backend.agents.review_runner_profile import RunnerProfile

    executable, cache = _ready_profile(tmp_path, model=model)
    return RunnerProfile(str(executable), model, cache)


def test_probe_runner_turn_ready_requires_live_probe(tmp_path):
    from backend.agents.review_runner_profile import preflight_runner, probe_runner

    profile = _ready_profile_instance(tmp_path)

    static = preflight_runner(profile)
    assert static.status == "ready"

    receipt = probe_runner(profile)
    assert receipt.status == "turn_ready"
    assert receipt.cli_version == "codex-cli 0.142.5"
    assert receipt.cache_fingerprint
    assert receipt.environment_fingerprint
    assert receipt.expires_by_fingerprint
    assert receipt.diagnostics == ()


def test_live_probe_invokes_codex_exec_subcommand(tmp_path, monkeypatch):
    import subprocess

    from backend.agents.review_runner_profile import probe_runner

    profile = _ready_profile_instance(tmp_path)
    observed = []

    def fake_run(argv, **kwargs):
        observed.append(argv)
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="codex-cli 0.142.5\n", stderr="")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"status": "ok", "model": "gpt-5.4"}), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    receipt = probe_runner(profile)

    assert receipt.status == "turn_ready"
    probe_argv = next(argv for argv in observed if "--version" not in argv)
    assert probe_argv[1:3] == ["exec", "--ephemeral"]


def test_live_probe_decodes_codex_jsonl_agent_message(tmp_path, monkeypatch):
    import subprocess

    from backend.agents.review_runner_profile import probe_runner

    profile = _ready_profile_instance(tmp_path)
    profile.cache_path.write_text(
        json.dumps({"models": [{"slug": "gpt-5.4", "model_messages": {"instructions_template": "base"}}]}),
        encoding="utf-8",
    )
    event_stream = json.dumps({"type": "thread.started"}) + "\n" + json.dumps({
        "type": "item.completed",
        "item": {
            "type": "agent_message",
            "text": json.dumps({"status": "ok", "model": "gpt-5.4"}),
        },
    })

    def fake_run(argv, **kwargs):
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="codex-cli 0.150.1\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout=event_stream + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    receipt = probe_runner(profile)

    assert receipt.status == "turn_ready"


def test_probe_runner_static_failure_blocks_capability_without_probe(tmp_path):
    from backend.agents.review_runner_profile import probe_runner

    executable, cache = _ready_profile(tmp_path)
    profile = _ready_profile_instance(tmp_path)
    # model not present in the cache -> static preflight fails
    from backend.agents.review_runner_profile import RunnerProfile

    profile = RunnerProfile(str(executable), "gpt-5.5", cache)

    receipt = probe_runner(profile)
    assert receipt.status == "blocked_runner_capability"
    assert any("model" in diagnostic for diagnostic in receipt.diagnostics)


def test_live_nonzero_exit_is_transport_blocked(tmp_path, monkeypatch):
    import subprocess

    from backend.agents.review_runner_profile import probe_runner

    profile = _ready_profile_instance(tmp_path)

    def fake_run(argv, **kwargs):
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="codex-cli 0.142.5\n", stderr="")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    receipt = probe_runner(profile)
    assert receipt.status == "blocked_runner_transport"
    assert any("nonzero" in diagnostic for diagnostic in receipt.diagnostics)


def test_live_probe_timeout_is_transport_blocked(tmp_path, monkeypatch):
    import subprocess

    from backend.agents.review_runner_profile import probe_runner

    profile = _ready_profile_instance(tmp_path)

    def fake_run(argv, **kwargs):
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="codex-cli 0.142.5\n", stderr="")
        raise subprocess.TimeoutExpired(argv[0], 15)

    monkeypatch.setattr(subprocess, "run", fake_run)

    receipt = probe_runner(profile)
    assert receipt.status == "blocked_runner_transport"
    assert any("time" in diagnostic for diagnostic in receipt.diagnostics)


def test_live_probe_invalid_json_is_transport_blocked(tmp_path, monkeypatch):
    import subprocess

    from backend.agents.review_runner_profile import probe_runner

    profile = _ready_profile_instance(tmp_path)

    def fake_run(argv, **kwargs):
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="codex-cli 0.142.5\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="not json", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    receipt = probe_runner(profile)
    assert receipt.status == "blocked_runner_transport"
    assert any("JSON" in diagnostic for diagnostic in receipt.diagnostics)


def test_live_probe_model_mismatch_is_transport_blocked(tmp_path, monkeypatch):
    import subprocess

    from backend.agents.review_runner_profile import probe_runner

    profile = _ready_profile_instance(tmp_path)

    def fake_run(argv, **kwargs):
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="codex-cli 0.142.5\n", stderr="")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"status": "ok", "model": "other-model"}), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    receipt = probe_runner(profile)
    assert receipt.status == "blocked_runner_transport"
    assert any("model" in diagnostic for diagnostic in receipt.diagnostics)


def test_live_probe_accepts_known_codex_display_name_alias(tmp_path, monkeypatch):
    import subprocess

    from backend.agents.review_runner_profile import probe_runner

    profile = _ready_profile_instance(tmp_path)

    def fake_run(argv, **kwargs):
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="codex-cli 0.150.1\n", stderr="")
        return subprocess.CompletedProcess(
            argv, 0,
            stdout=json.dumps({"status": "ok", "model": "GPT-5 Codex"}),
            stderr="",
        )

    profile.cache_path.write_text(
        json.dumps({"models": [{"slug": "gpt-5.4", "model_messages": {"instructions_template": "base"}}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    receipt = probe_runner(profile)

    assert receipt.status == "turn_ready"


def test_live_probe_output_over_8kib_is_transport_blocked(tmp_path, monkeypatch):
    import subprocess

    from backend.agents.review_runner_profile import probe_runner

    profile = _ready_profile_instance(tmp_path)

    def fake_run(argv, **kwargs):
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="codex-cli 0.142.5\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="x" * 9000, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    receipt = probe_runner(profile)
    assert receipt.status == "blocked_runner_transport"
    assert any("bound" in diagnostic or "8 KiB" in diagnostic for diagnostic in receipt.diagnostics)


def test_failed_probe_is_not_retried(tmp_path, monkeypatch):
    import subprocess

    from backend.agents.review_runner_profile import probe_runner

    profile = _ready_profile_instance(tmp_path)
    probe_calls = []

    def fake_run(argv, **kwargs):
        probe_calls.append(argv)
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="codex-cli 0.142.5\n", stderr="")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    receipt = probe_runner(profile)
    assert receipt.status == "blocked_runner_transport"
    assert sum(1 for argv in probe_calls if "--version" not in argv) == 1


def test_receipt_round_trips_exact_schema(tmp_path):
    from backend.agents.review_runner_profile import RunnerCapabilityReceipt, probe_runner

    profile = _ready_profile_instance(tmp_path)
    receipt = probe_runner(profile)

    restored = RunnerCapabilityReceipt.from_dict(receipt.to_dict())
    assert restored == receipt
    assert set(receipt.to_dict()) == {
        "schemaVersion", "status", "executable", "cliVersion", "model",
        "cacheFingerprint", "environmentFingerprint", "diagnostics",
        "expiresByFingerprint",
    }


def test_receipt_rejects_invalid_status():
    from backend.agents.review_runner_profile import RunnerCapabilityReceipt, RunnerProfileError

    payload = RunnerCapabilityReceipt(
        schema_version="runner-capability-v1",
        status="turn_ready",
        executable="/bin/true",
        cli_version="codex-cli 0.142.5",
        model="gpt-5.4",
        cache_fingerprint="a" * 64,
        environment_fingerprint="b" * 64,
        diagnostics=(),
        expires_by_fingerprint="c" * 64,
    ).to_dict()
    payload["status"] = "unknown"

    with pytest.raises(RunnerProfileError, match="status"):
        RunnerCapabilityReceipt.from_dict(payload)


def test_capability_fingerprint_changes_with_identity(tmp_path):
    from backend.agents.review_runner_profile import RunnerProfile, capability_fingerprint

    executable, cache = _ready_profile(tmp_path)
    profile = RunnerProfile(str(executable), "gpt-5.4", cache)

    base = capability_fingerprint(profile, cli_version="codex-cli 0.142.5", cache_fingerprint="a" * 64)
    other_model = capability_fingerprint(
        RunnerProfile(str(executable), "gpt-5.5", cache),
        cli_version="codex-cli 0.142.5", cache_fingerprint="a" * 64,
    )
    other_cache = capability_fingerprint(profile, cli_version="codex-cli 0.142.5", cache_fingerprint="b" * 64)

    assert base == capability_fingerprint(profile, cli_version="codex-cli 0.142.5", cache_fingerprint="a" * 64)
    assert base != other_model
    assert base != other_cache


def test_receipt_reuse_skips_second_live_probe(tmp_path, monkeypatch):
    import subprocess

    from backend.agents.review_runner_profile import probe_runner

    profile = _ready_profile_instance(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    probe_calls = []

    def fake_run(argv, **kwargs):
        probe_calls.append(argv)
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="codex-cli 0.142.5\n", stderr="")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"status": "ok", "model": "gpt-5.4"}), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    first = probe_runner(profile, receipt_path=receipt_path)
    second = probe_runner(profile, receipt_path=receipt_path)

    assert first.status == "turn_ready"
    assert second.status == "turn_ready"
    assert second == first
    assert second.expires_by_fingerprint == first.expires_by_fingerprint
    assert sum(1 for argv in probe_calls if "--version" not in argv) == 1


def test_receipt_is_stale_when_capability_identity_changes(tmp_path, monkeypatch):
    import subprocess

    from backend.agents.review_runner_profile import probe_runner

    profile = _ready_profile_instance(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    probe_calls = []

    def fake_run(argv, **kwargs):
        probe_calls.append(argv)
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="codex-cli 0.142.5\n", stderr="")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"status": "ok", "model": "gpt-5.4"}), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    first = probe_runner(profile, receipt_path=receipt_path)
    profile.cache_path.write_text(
        json.dumps({"models": [{"slug": "gpt-5.4", "base_instructions": "changed"}]}),
        encoding="utf-8",
    )
    second = probe_runner(profile, receipt_path=receipt_path)

    assert first.status == "turn_ready"
    assert second.status == "turn_ready"
    assert second.expires_by_fingerprint != first.expires_by_fingerprint
    assert sum(1 for argv in probe_calls if "--version" not in argv) == 2


def test_write_and_load_capability_receipt_round_trip(tmp_path):
    from backend.agents.review_runner_profile import (
        load_capability_receipt,
        probe_runner,
        write_capability_receipt,
    )

    profile = _ready_profile_instance(tmp_path)
    receipt = probe_runner(profile)

    path = write_capability_receipt(tmp_path / "receipt.json", receipt)
    assert load_capability_receipt(path) == receipt
    assert load_capability_receipt(tmp_path / "missing.json") is None


def test_preflight_cli_without_probe_never_claims_turn_ready(tmp_path, capsys):
    from scripts.codex_runner_preflight import main

    executable, cache = _ready_profile(tmp_path)
    assert main(["--model", "gpt-5.4", "--executable", str(executable), "--cache", str(cache)]) == 0
    out = capsys.readouterr().out
    assert '"status": "ready"' in out
    assert "turn_ready" not in out


def test_preflight_cli_probe_mode_reports_turn_ready(tmp_path, capsys):
    from scripts.codex_runner_preflight import main

    executable, cache = _ready_profile(tmp_path)
    assert main(["--model", "gpt-5.4", "--executable", str(executable), "--cache", str(cache), "--probe"]) == 0
    assert '"status": "turn_ready"' in capsys.readouterr().out


def test_preflight_cli_probe_mode_blocks_transport_on_bad_response(tmp_path, capsys):
    from scripts.codex_runner_preflight import main

    executable, cache = _profile(tmp_path)
    assert main(["--model", "gpt-5.4", "--executable", str(executable), "--cache", str(cache), "--probe"]) == 2
    assert '"status": "blocked_runner_transport"' in capsys.readouterr().out


def test_preflight_cli_probe_mode_blocks_capability_on_static_failure(tmp_path, capsys):
    from scripts.codex_runner_preflight import main

    executable, cache = _ready_profile(tmp_path)
    assert main(["--model", "gpt-5.5", "--executable", str(executable), "--cache", str(cache), "--probe"]) == 2
    assert '"status": "blocked_runner_capability"' in capsys.readouterr().out
