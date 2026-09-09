"""Offline report CLI; never starts a model, network client, or subprocess."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents.agent_eval_report import build_report, render_markdown
from backend.agents.agent_eval_manifest import verify_binding
from backend.agents.agent_eval_store import publish_bundle, read_json, read_json_bytes


def _load_input(root: Path, item: dict) -> dict:
    if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
        raise ValueError("invalid_input_index")
    path = item["path"]
    payload, raw = read_json_bytes(root, path, max_bytes=2 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError("input_hash_mismatch")
    return payload


def _error(code: str) -> int:
    print(json.dumps({"status": "blocked", "code": code}, ensure_ascii=False, sort_keys=True))
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an offline agent memory evaluation report")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--format", choices=("json", "markdown"), required=True)
    parser.add_argument("--save-id")
    args = parser.parse_args(argv)
    try:
        if not (args.root / args.manifest).is_file():
            return _error("missing_manifest")
        if not (args.root / args.inputs).is_file():
            return _error("missing_inputs")
        manifest = read_json(args.root, args.manifest, max_bytes=256 * 1024)
        index = read_json(args.root, args.inputs, max_bytes=256 * 1024)
        if index.get("schemaVersion") != "agent-eval-input-index-v1":
            raise ValueError("invalid_input_index")
        required_collections = {"observations", "ledgers", "quality", "diagnostics"}
        if not required_collections <= set(index) or any(not isinstance(index[name], list) for name in required_collections):
            raise ValueError("invalid_input_index")
        observation_artifact_refs = []
        def load_many(name: str) -> list[dict]:
            values = index.get(name, [])
            if not isinstance(values, list) or len(values) > 4096:
                raise ValueError("invalid_input_index")
            loaded = []
            for item in values:
                payload = _load_input(args.root, item)
                if name == "observations":
                    verify_binding(payload, manifest=manifest, artifact_ref=item,
                                   producer_registry=manifest.get("producerRegistry"))
                    observation_artifact_refs.append(item)
                loaded.append(payload)
            return loaded
        observations = load_many("observations")
        report = build_report(manifest, observations, load_many("ledgers"), load_many("quality"), load_many("diagnostics"), observation_artifact_refs=observation_artifact_refs)
        output = render_markdown(report) if args.format == "markdown" else json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if args.save_id:
            publish_bundle(args.root, args.save_id, {"report.md" if args.format == "markdown" else "report.json": output.encode("utf-8")})
        else:
            print(output)
        return 0 if report["status"] in {"available", "synthetic_only"} else 1
    except (OSError, ValueError, KeyError) as exc:
        return _error(str(exc) if str(exc).startswith(("missing_", "invalid_", "blocked_", "input_", "unsafe_", "quota_", "experiment_")) else "invalid_input")


if __name__ == "__main__":
    raise SystemExit(main())
