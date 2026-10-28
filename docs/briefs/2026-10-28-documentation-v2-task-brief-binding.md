# Documentation v2 approved task brief binding

## Objective

Repair the governed documentation v2 preview flow so the approved task objective is available to the documentation runner as bounded, tamper-evident evidence. Bind the UTF-8 task brief content to the workflow manifest path and SHA-256, reject unsafe, missing, oversized, or changed briefs, and require the runner to produce a valid canonical draft fingerprint.

## Scope

- Include the approved brief content and its path/hash in documentation v2 evidence.
- Preserve legacy v2 evidence parsing, but fail closed before preview when the approved brief is absent.
- Clarify runner instructions so claims are limited to evidence and a blocked response still follows the canonical output contract.
- Add focused regression coverage for brief binding, path safety, size/hash validation, and runner/service behavior.
- Update only directly relevant documentation-agent governance descriptions.

## Out of scope

No changes to SQLite, revenue/business rules, baseline, export schema, release gate policy, documentation target catalog, or direct handoff/runbook Markdown backfill.

## Acceptance

Focused documentation workflow tests pass, followed by findings-first Review, full pytest, Hermes and applicable UI/release aggregate gates on the exact source. Only after those gates pass may the official documentation workflow preview and explicitly approved fixed-target apply run.
