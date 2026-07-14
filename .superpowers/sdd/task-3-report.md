# Task 3 Report

## Status

DONE_WITH_CONCERNS

## Commit SHA

`6f7b30d` (`fix: bind validation interpreter roots`)

## Modified Files

- `backend/agents/validation_runner.py`
- `tests/test_validation_runner.py`
- `.superpowers/sdd/task-3-report.md`

Task 4+ service and CLI work was intentionally not implemented.

## RED

Initial brief command:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_validation_runner.py -q
```

Result before the test file existed: collection could not start because `tests/test_validation_runner.py` was not found.

After adding the failing tests and before implementation, the same command failed during collection with:

```text
ModuleNotFoundError: No module named 'backend.agents.validation_runner'
```

## GREEN And Verification

- `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_validation_runner.py -q`: PASS, `15 passed`.
- `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_validation_runner.py tests/test_implementation_models.py -q`: PASS, `23 passed`.
- `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_implementation_models.py tests/test_implementation_guard.py tests/test_agent_runtime.py -q`: PASS, `38 passed`.
- `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/validation_runner.py tests/test_validation_runner.py`: PASS, exit 0.
- `git diff --check`: PASS, no output.

## Second-Round Interpreter Containment Fix

The second review finding was fixed by making the interpreter roots explicit in `agent_config/implementation_commands.json`. The runner accepts only the lexical `.venv/bin/python` location from the command config, checks a worktree-local candidate after resolution remains under the approved repository root, and rejects a worktree-local symlink to an external executable. The approved repository-root `.venv/bin/python` is considered separately and may be a symlink only because that lexical path is explicitly configured as the approved repository interpreter. PATH lookup, `sys.executable` fallback, arbitrary absolute paths, and arbitrary symlinks remain disabled. Missing interpreters still raise the stable `CommandRejected` message.

## Second-Round RED

- Added `test_runner_rejects_worktree_interpreter_symlink_outside_approved_root` with a worktree-local `.venv/bin/python` symlink to `tmp_path/outside/python`.
- `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_validation_runner.py -q`: RED, `1 failed, 18 passed`; the new regression was accepted by the pre-fix resolver.

## Second-Round GREEN, Real Invocation And Regression Evidence

- `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_validation_runner.py -q`: PASS, `19 passed`.
- `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_validation_runner.py tests/test_implementation_models.py tests/test_implementation_guard.py -q`: PASS, `38 passed in 1.47s`.
- `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/validation_runner.py`: PASS, exit 0.
- Real invocation from the implementation worktree returned `ValidationResult(command_id='py_compile', exit_code=0, timed_out=False)` using the explicitly approved repository-root lexical interpreter; its uv-resolved target was `/Users/chanwaitung2025/.local/share/uv/python/cpython-3.10.20-macos-aarch64-none/bin/python3.10`.
- `git diff --check`: PASS, no output.

## Hermes Post-Change Check

- `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python scripts/hermes_post_change_check.py --json`: FAIL outside Task 3 scope. The implementation worktree has no formal SQLite/runtime data, so monitor reported database missing and baseline actual `HKD 0` versus frozen expected `HKD 12,057,968`; monthly baseline check also hit the pre-existing missing `_date` data-path error. The targeted runner/model/guard verification remained green (`38 passed`).

## Concerns

- Timeout results use exit code `124`, preserve capped partial output when available, and never retry automatically.
- Output is capped independently at 32,000 characters per stream.
- No Hermes post-change check or Task 4+ integration was run because this task is limited to the runner and its tests; the runner remains an isolated backend utility.

## Review Finding Fix

The Important finding was fixed by resolving the configured `.venv/bin/python` before execution. The resolver checks the project-local virtualenv first, then the approved repository root virtualenv for an implementation worktree, requires the allowlisted virtualenv directory to remain inside the approved repository root after realpath resolution, and rejects missing or non-executable interpreters with an actionable `CommandRejected`. It never performs PATH lookup, network access, or dependency installation. Process launch `OSError` is also converted to `CommandRejected` so executable failures do not escape as unstable raw exceptions.

## Review-Fix RED

After adding the resolver and real-invocation tests but before implementing the fix:

- `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_validation_runner.py -q`: FAIL, `5 failed, 13 passed`.
- The real invocation reproduced the review finding with `FileNotFoundError: [Errno 2] No such file or directory: '.venv/bin/python'`.

## Review-Fix GREEN And Real Invocation

- `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_validation_runner.py tests/test_implementation_models.py tests/test_implementation_guard.py -q`: PASS, `37 passed in 1.43s`.
- `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/validation_runner.py`: PASS, exit 0.
- Independent non-mocked invocation from the implementation worktree:
  `ValidationRunner(Path.cwd()).run("py_compile", ("backend/agents/validation_runner.py",))`: PASS, returned `ValidationResult` with `exitCode=0`, `timedOut=false`, and resolved interpreter `/Users/chanwaitung2025/.local/share/uv/python/cpython-3.10-macos-aarch64-none/bin/python3.10`.
- `git diff --check`: PASS, no output.
