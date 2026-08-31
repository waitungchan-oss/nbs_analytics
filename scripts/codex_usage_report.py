"""Read-only weekly Codex usage report for the NBS Analytics project.

Reads Codex rollout session logs (default ``$CODEX_HOME/sessions`` or
``~/.codex/sessions``) and aggregates token usage by ISO week, plus optional
project-level agent pipeline telemetry (``.nbs_agent_runtime/telemetry/``).

This script is strictly read-only: it never writes to the project, SQLite,
baseline, runtime or Git. The only writable output is the ``--output`` path the
user explicitly requests.

Data source per session file (rollout-*.jsonl): the last ``event_msg`` record
with ``payload.type == "token_count"``; its ``payload.info.total_token_usage``
holds ``input_tokens``, ``cached_input_tokens``, ``output_tokens`` and
``reasoning_output_tokens`` (cumulative session totals).

Usage examples:

    # Human-readable report for the last 8 ISO weeks (default)
    .venv/bin/python scripts/codex_usage_report.py

    # Machine-readable JSON (runbook-style exchange format)
    .venv/bin/python scripts/codex_usage_report.py --format json --weeks 4

    # Full-range report from the first session
    .venv/bin/python scripts/codex_usage_report.py --since 2026-05-29
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _default_sessions_dir() -> Path:
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        return Path(env_home) / "sessions"
    return Path.home() / ".codex" / "sessions"


def _iter_session_files(sessions_dir: Path, start: date, end: date):
    """Yield rollout jsonl files whose date dir falls within [start, end]."""
    if not sessions_dir.is_dir():
        return
    year = start.year
    while year <= end.year:
        year_dir = sessions_dir / str(year)
        if year_dir.is_dir():
            for month_dir in sorted(year_dir.iterdir()):
                if not month_dir.is_dir():
                    continue
                for day_dir in sorted(month_dir.iterdir()):
                    if not day_dir.is_dir():
                        continue
                    try:
                        d = date(int(year_dir.name), int(month_dir.name), int(day_dir.name))
                    except ValueError:
                        continue
                    if start <= d <= end:
                        yield from sorted(day_dir.glob("rollout-*.jsonl"))
        year += 1


def _parse_session(path: Path) -> dict | None:
    """Return per-session usage summary, or None if the file is unusable."""
    result = {
        "path": str(path),
        "date": None,
        "cwd": None,
        "input": 0,
        "cached": 0,
        "output": 0,
        "reasoning": 0,
        "turns": 0,
        "orchestration": False,
        "usedPercent": None,
        "hasUsage": False,
    }
    try:
        stamp = path.name.split("rollout-")[1][:10]
        result["date"] = stamp
    except IndexError:
        pass
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "token_count" in line:
                    try:
                        record = json.loads(line)
                        payload = record.get("payload", {})
                        if payload.get("type") == "token_count":
                            info = payload.get("info", {})
                            usage = info.get("total_token_usage", {})
                            result["input"] = usage.get("input_tokens", result["input"])
                            result["cached"] = usage.get("cached_input_tokens", result["cached"])
                            result["output"] = usage.get("output_tokens", result["output"])
                            result["reasoning"] = usage.get("reasoning_output_tokens", result["reasoning"])
                            result["hasUsage"] = True
                            rate = payload.get("rate_limits", {}).get("primary", {})
                            if rate.get("used_percent") is not None:
                                result["usedPercent"] = rate.get("used_percent")
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        continue
                elif "session_meta" in line:
                    try:
                        record = json.loads(line)
                        meta = record.get("payload", {})
                        result["cwd"] = meta.get("cwd") or result["cwd"]
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        continue
                elif '"role":"user"' in line or '"role": "user"' in line:
                    result["turns"] += 1
                elif "spawn_agent" in line or "agent_workflow" in line:
                    result["orchestration"] = True
    except (OSError, UnicodeDecodeError):
        return None
    return result


def _week_key(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso.year, iso.week)


def _project_key(cwd: str | None) -> str:
    """Classify a session cwd into a coarse project bucket."""
    if not cwd:
        return "(未知)"
    if "nbs_analytics" in cwd:
        if ".worktrees/" in cwd:
            try:
                wt = cwd.split(".worktrees/")[1].split("/")[0]
                return f"nbs_analytics (worktree: {wt})"
            except IndexError:
                pass
        return "nbs_analytics (主目錄)"
    if "nbs-formal" in cwd or "/private/tmp/nbs" in cwd:
        return "nbs_analytics (sandbox)"
    if "dashboard-project" in cwd:
        return "dashboard-project"
    if "deepseek-harness" in cwd:
        return "deepseek-harness"
    if "財富增長" in cwd or "營銷研究" in cwd:
        return "財富/營銷研究"
    return "其他"


def _load_project_telemetry() -> dict | None:
    """Optional project agent pipeline stats (read-only)."""
    telemetry = PROJECT_ROOT / ".nbs_agent_runtime" / "telemetry" / "agent_runs.jsonl"
    if not telemetry.is_file():
        return None
    counts = {"context": 0, "review": 0}
    review_results = defaultdict(int)
    cache_hits = 0
    cache_total = 0
    est_input = 0
    try:
        with open(telemetry, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                agent = record.get("agent")
                if agent not in counts:
                    continue
                counts[agent] += 1
                if agent == "review":
                    review_results[record.get("result", "unknown")] += 1
                if record.get("cacheHit") is not None:
                    cache_total += 1
                    if record.get("cacheHit"):
                        cache_hits += 1
                est_input += record.get("estimatedInputTokens", 0) or 0
    except OSError:
        return None
    total_review = sum(review_results.values())
    rework_rate = (
        round(review_results.get("changes_required", 0) / total_review * 100, 1)
        if total_review
        else None
    )
    cache_rate = round(cache_hits / cache_total * 100, 1) if cache_total else None
    return {
        "counts": counts,
        "reviewResults": dict(review_results),
        "reviewReworkRatePct": rework_rate,
        "reviewCacheHitRatePct": cache_rate,
        "estimatedInputTokens": est_input,
    }


def _build_report(sessions_dir: Path, start: date, end: date, top_n: int) -> dict:
    weeks = defaultdict(lambda: {"sessions": 0, "input": 0, "cached": 0,
                                 "output": 0, "reasoning": 0, "orchestration": 0})
    project_rows_by_key: dict[str, dict] = {}
    sessions = []
    unreadable = 0
    for path in _iter_session_files(sessions_dir, start, end):
        parsed = _parse_session(path)
        if parsed is None:
            unreadable += 1
            continue
        sessions.append(parsed)
        d = date.fromisoformat(parsed["date"]) if parsed["date"] else start
        w = weeks[_week_key(d)]
        w["sessions"] += 1
        w["input"] += parsed["input"]
        w["cached"] += parsed["cached"]
        w["output"] += parsed["output"]
        w["reasoning"] += parsed["reasoning"]
        if parsed["orchestration"]:
            w["orchestration"] += 1

    totals = {
        "sessions": len(sessions),
        "input": sum(s["input"] for s in sessions),
        "cached": sum(s["cached"] for s in sessions),
        "output": sum(s["output"] for s in sessions),
        "reasoning": sum(s["reasoning"] for s in sessions),
        "orchestration": sum(1 for s in sessions if s["orchestration"]),
        "unreadable": unreadable,
    }
    totals["uncached"] = totals["input"] - totals["cached"]
    totals["cacheRatePct"] = round(totals["cached"] / totals["input"] * 100, 1) if totals["input"] else None

    top = sorted(
        (s for s in sessions if s["hasUsage"]),
        key=lambda s: s["input"] + s["output"],
        reverse=True,
    )[:top_n]

    last_used_percent = next(
        (s["usedPercent"] for s in reversed(sessions) if s["usedPercent"] is not None), None
    )

    week_rows = []
    for key in sorted(weeks):
        w = weeks[key]
        week_rows.append({
            "week": f"{key[0]}-W{key[1]:02d}",
            **w,
            "uncached": w["input"] - w["cached"],
            "cacheRatePct": round(w["cached"] / w["input"] * 100, 1) if w["input"] else None,
        })

    project_rows = []
    for s in sessions:
        key = _project_key(s.get("cwd"))
        p = project_rows_by_key.get(key)
        if p is None:
            p = {"project": key, "sessions": 0, "input": 0, "cached": 0,
                 "output": 0, "reasoning": 0}
            project_rows_by_key[key] = p
        p["sessions"] += 1
        p["input"] += s["input"]
        p["cached"] += s["cached"]
        p["output"] += s["output"]
        p["reasoning"] += s["reasoning"]
    for p in project_rows_by_key.values():
        p["uncached"] = p["input"] - p["cached"]
        p["cacheRatePct"] = round(p["cached"] / p["input"] * 100, 1) if p["input"] else None
    project_rows = sorted(project_rows_by_key.values(),
                          key=lambda r: r["input"], reverse=True)

    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "totals": totals,
        "weeks": week_rows,
        "projects": project_rows,
        "topSessions": [
            {
                "date": s["date"],
                "input": s["input"],
                "cached": s["cached"],
                "output": s["output"],
                "reasoning": s["reasoning"],
                "turns": s["turns"],
                "orchestration": s["orchestration"],
                "path": Path(s["path"]).name,
            }
            for s in top
        ],
        "lastUsedPercent": last_used_percent,
        "projectTelemetry": _load_project_telemetry(),
    }


def _fmt_tokens(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _render_markdown(report: dict) -> str:
    lines = []
    t = report["totals"]
    lines.append("# Codex 用量報表")
    lines.append("")
    lines.append(f"- 產生時間：{report['generatedAt']}")
    lines.append(f"- 期間：{report['range']['start']} ~ {report['range']['end']}")
    lines.append(
        f"- 總計：**{t['sessions']}** 個 session；輸入 **{_fmt_tokens(t['input'])}**"
        f"（快取 {t['cacheRatePct']}%）；輸出 **{_fmt_tokens(t['output'])}**；"
        f"reasoning **{_fmt_tokens(t['reasoning'])}**"
    )
    lines.append(f"- 含 agent 活動的 session（agent_workflow/spawn）：{t['orchestration']} 個")
    if report.get("lastUsedPercent") is not None:
        lines.append(f"- 最近一次 rate limit 用量：**{report['lastUsedPercent']}%**（7 日窗口）")
    lines.append("")
    lines.append("## 每週")
    lines.append("| 週 | sessions | 輸入 | 快取% | 未快取 | 輸出 | reasoning | agent活動 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for w in report["weeks"]:
        lines.append(
            f"| {w['week']} | {w['sessions']} | {_fmt_tokens(w['input'])} | "
            f"{w['cacheRatePct'] if w['cacheRatePct'] is not None else '-'} | "
            f"{_fmt_tokens(w['uncached'])} | {_fmt_tokens(w['output'])} | "
            f"{_fmt_tokens(w['reasoning'])} | {w['orchestration']} |"
        )
    lines.append("")
    lines.append("## 各專案用量")
    lines.append("| 專案 | sessions | 輸入 | 快取% | 未快取 | 輸出 | reasoning |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for p in report.get("projects", []):
        lines.append(
            f"| {p['project']} | {p['sessions']} | {_fmt_tokens(p['input'])} | "
            f"{p['cacheRatePct'] if p['cacheRatePct'] is not None else '-'} | "
            f"{_fmt_tokens(p['uncached'])} | {_fmt_tokens(p['output'])} | "
            f"{_fmt_tokens(p['reasoning'])} |"
        )
    lines.append("")
    lines.append(f"## Top {len(report['topSessions'])} session（按輸入+輸出）")
    lines.append("| 日期 | 輸入 | 輸出 | reasoning | turns | 編排 | 檔名 |")
    lines.append("|---|---:|---:|---:|---:|:---:|---|")
    for s in report["topSessions"]:
        lines.append(
            f"| {s['date']} | {_fmt_tokens(s['input'])} | {_fmt_tokens(s['output'])} | "
            f"{_fmt_tokens(s['reasoning'])} | {s['turns']} | "
            f"{'是' if s['orchestration'] else ''} | {s['path'][:44]} |"
        )
    telemetry = report.get("projectTelemetry")
    if telemetry:
        lines.append("")
        lines.append("## 專案 Agent Pipeline（.nbs_agent_runtime/telemetry）")
        lines.append(
            f"- Context runs：{telemetry['counts'].get('context', 0)}；"
            f"Review runs：{telemetry['counts'].get('review', 0)}"
        )
        if telemetry["reviewReworkRatePct"] is not None:
            lines.append(
                f"- Review 結果：{telemetry['reviewResults']}；"
                f"**返工率 {telemetry['reviewReworkRatePct']}%**；"
                f"cache hit {telemetry['reviewCacheHitRatePct']}%"
            )
        lines.append(f"- Agent 輸入 token（估計）：{_fmt_tokens(telemetry['estimatedInputTokens'])}")
    lines.append("")
    if t.get("unreadable"):
        lines.append(f"> 警告：{t['unreadable']} 個 session 檔案無法讀取，已略過。")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only weekly Codex usage report (token aggregation)."
    )
    parser.add_argument("--sessions-dir", default=str(_default_sessions_dir()),
                        help="Codex sessions directory (default: $CODEX_HOME/sessions or ~/.codex/sessions)")
    parser.add_argument("--weeks", type=int, default=8,
                        help="Number of ISO weeks to report (default: 8)")
    parser.add_argument("--since", help="Start date YYYY-MM-DD (default: 8 weeks ago)")
    parser.add_argument("--until", help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--top", type=int, default=10, help="Top sessions to list (default: 10)")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output", help="Optional output file path (JSON or text; only this path is written)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    end = date.fromisoformat(args.until) if args.until else date.today()
    if args.since:
        start = date.fromisoformat(args.since)
    else:
        start = end - timedelta(weeks=args.weeks)

    sessions_dir = Path(args.sessions_dir)
    if not sessions_dir.is_dir():
        print(f"codex_usage_report: sessions dir not found: {sessions_dir}", file=sys.stderr)
        return 2

    report = _build_report(sessions_dir, start, end, args.top)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" else _render_markdown(report)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        print(f"written: {out}", file=sys.stderr)
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
