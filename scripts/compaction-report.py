#!/usr/bin/env python3
"""Measure what a Claude Code session pays per turn and what each compaction cost and saved.

Reads transcripts (~/.claude/projects/<project>/<session>.jsonl), nothing else. For every
session it reports the compaction boundaries (context before, floor after, context twelve
turns later, tokens dropped), the cycles between them (turns, average and peak context,
tokens re-sent, weighted cost per turn), the cache breaks (turns that rewrote more than
50k tokens into the cache, with the gap since the previous turn), and a recommended
trigger band derived from the measured floor, overhead and growth rate.

Weighted cost uses the relative prices of the four token classes: uncached input 1,
cache write 1.25, cache read 0.1, output 5. Change them with --weights if your prices
differ; the conclusions do not depend on the exact values.

Usage:
  python compaction-report.py <transcript.jsonl> [more...]
  python compaction-report.py --day 2026-08-27            every transcript with turns that day
  python compaction-report.py --day 2026-08-27 --json     machine-readable
Options: --window 200000, --weights in=1,cw=1.25,cr=0.1,out=5, --break 50000
"""
import argparse
import glob
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone

HOME = os.path.expanduser("~")


def parse_weights(text):
    weights = {"in": 1.0, "cw": 1.25, "cr": 0.1, "out": 5.0}
    for part in (text or "").split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            weights[key.strip()] = float(value)
    return weights


def local_time(stamp):
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
    except Exception:
        return None


def load_rows(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def is_boundary(row):
    if row.get("isCompactSummary") or (row.get("type") == "system" and row.get("compactMetadata")):
        return True
    if row.get("type") == "user":
        content = (row.get("message") or {}).get("content")
        if isinstance(content, str) and content.startswith("This session is being continued from a previous conversation"):
            return True
    return False


def summary_chars(row):
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(block.get("text") or "") for block in content if isinstance(block, dict))
    return 0


def turns_of(rows, day):
    """Assistant turns deduplicated by message id (the last row of a streamed message carries
    the complete usage). Each turn: index, time, ctx, in, cw, cr, out."""
    by_id, order = {}, []
    for index, row in enumerate(rows):
        if row.get("type") != "assistant":
            continue
        message = row.get("message") or {}
        usage = message.get("usage") or {}
        if not usage:
            continue
        key = message.get("id") or f"row-{index}"
        if key not in by_id:
            order.append(key)
        by_id[key] = {
            "index": index,
            "time": local_time(row.get("timestamp")),
            "in": usage.get("input_tokens") or 0,
            "cw": usage.get("cache_creation_input_tokens") or 0,
            "cr": usage.get("cache_read_input_tokens") or 0,
            "out": usage.get("output_tokens") or 0,
        }
    turns = []
    for key in order:
        turn = by_id[key]
        turn["ctx"] = turn["in"] + turn["cw"] + turn["cr"]
        if day and (turn["time"] is None or turn["time"].strftime("%Y-%m-%d") != day):
            continue
        turns.append(turn)
    return turns


def weighted(turn, weights):
    return turn["in"] * weights["in"] + turn["cw"] * weights["cw"] + turn["cr"] * weights["cr"] + turn["out"] * weights["out"]


def analyze(path, args, weights):
    rows = load_rows(path)
    turns = turns_of(rows, args.day)
    if not turns:
        return None
    # one compaction writes several boundary rows within the same minute (a system row with
    # the metadata, the summary row itself); group them and keep the largest text as the summary
    groups = {}
    for index, row in enumerate(rows):
        if not is_boundary(row):
            continue
        stamp = local_time(row.get("timestamp"))
        minute = stamp.strftime("%Y-%m-%d %H:%M") if stamp else f"row {index}"
        group = groups.setdefault(minute, {"index": index, "stamp": stamp, "summary_chars": 0, "pre": None, "post": None, "ms": None})
        group["summary_chars"] = max(group["summary_chars"], summary_chars(row))
        meta = row.get("compactMetadata") if row.get("type") == "system" else None
        if isinstance(meta, dict):  # exact figures from the harness, when the transcript has them
            group["pre"] = meta.get("preTokens") or group["pre"]
            group["post"] = meta.get("postTokens") or group["post"]
            group["ms"] = meta.get("durationMs") or group["ms"]
    compactions = []
    for minute, group in groups.items():
        stamp, index = group["stamp"], group["index"]
        if args.day and stamp and stamp.strftime("%Y-%m-%d") != args.day:
            continue
        before = [t for t in turns if t["index"] < index]
        after = [t for t in turns if t["index"] > index]
        if not before:
            continue
        floor = min(t["ctx"] for t in after[:3]) if after else None  # no turn after it yet
        compactions.append({
            "time": minute[-5:],
            "index": index,
            "before": before[-1]["ctx"],
            "floor": floor,
            "after_12": after[11]["ctx"] if len(after) > 11 else None,
            "dropped": before[-1]["ctx"] - floor if floor is not None else None,
            "summary_chars": group["summary_chars"],
            "pre_tokens": group["pre"],
            "post_tokens": group["post"],  # what the harness kept: the summary plus a preserved tail of recent messages
            "duration_ms": group["ms"],
        })
    # cycles between boundaries
    cuts = [c["index"] for c in compactions]
    cycles, start = [], 0
    for cut in cuts + [float("inf")]:
        segment = [t for t in turns if start <= t["index"] < cut]
        start = cut
        if not segment:
            continue
        cost = sum(weighted(t, weights) for t in segment)
        cycles.append({
            "turns": len(segment),
            "avg_ctx": round(sum(t["ctx"] for t in segment) / len(segment)),
            "peak_ctx": max(t["ctx"] for t in segment),
            "resent": sum(t["ctx"] for t in segment),
            "output": sum(t["out"] for t in segment),
            "weighted": round(cost),
            "per_turn": round(cost / len(segment)),
            "from": segment[0]["time"].strftime("%H:%M") if segment[0]["time"] else "?",
            "to": segment[-1]["time"].strftime("%H:%M") if segment[-1]["time"] else "?",
        })
    breaks = []
    for previous, turn in zip(turns, turns[1:]):
        if turn["cw"] >= args.break_tokens:
            gap = (turn["time"] - previous["time"]).total_seconds() / 60 if turn["time"] and previous["time"] else None
            breaks.append({
                "time": turn["time"].strftime("%H:%M") if turn["time"] else "?",
                "rewritten": turn["cw"],
                "gap_minutes": round(gap, 1) if gap is not None else None,
                "weighted": round(weighted(turn, weights)),
            })
    growth = [b["ctx"] - a["ctx"] for a, b in zip(turns, turns[1:]) if 0 < b["ctx"] - a["ctx"] < 0.5 * args.window]
    total = sum(weighted(t, weights) for t in turns)
    recommendation = recommend(compactions, growth, weights, args.window)
    return {
        "session": os.path.basename(path)[:8],
        "path": path,
        "turns": len(turns),
        "avg_ctx": round(sum(t["ctx"] for t in turns) / len(turns)),
        "peak_ctx": max(t["ctx"] for t in turns),
        "resent": sum(t["ctx"] for t in turns),
        "weighted": round(total),
        "per_turn": round(total / len(turns)),
        "compactions": compactions,
        "cycles": cycles,
        "cache_breaks": breaks,
        "growth_median": round(statistics.median(growth)) if growth else None,
        "recommendation": recommendation,
    }


def recommend(compactions, growth, weights, window):
    """Trigger band from the measured numbers: the working band that balances the per-turn
    carry of a larger context against the fixed cost of one more compaction.

    Per turn, every token of context costs its cache-read price; over a cycle of B/g turns
    the extra carry of a band B is about B^2/(2g) x cr. One compaction costs O (reading the
    old context once, writing the summary, re-caching the floor). Minimizing per-turn cost
    over the band gives B* = sqrt(2 x O x g / cr). With cr = 0.1 that is sqrt(20 x O x g)."""
    compactions = [c for c in compactions if c["floor"] is not None]
    if not compactions or not growth:
        return None
    floor = round(statistics.median(c["floor"] for c in compactions))
    overheads = []
    for c in compactions:
        summary_tokens = c["summary_chars"] / 4 if c["summary_chars"] else 5000  # the text the summarizer wrote
        overheads.append(c["before"] * weights["cr"] + summary_tokens * weights["out"] + c["floor"] * weights["cw"])
    overhead = statistics.median(overheads)
    g = statistics.median(growth)
    band = math.sqrt(2 * overhead * g / weights["cr"]) if weights["cr"] > 0 else None
    if band is None:
        return None
    trigger = floor + band
    return {
        "floor": floor,
        "overhead_weighted": round(overhead),
        "growth_per_turn": round(g),
        "band": round(band),
        "trigger_tokens": round(trigger),
        "trigger_fraction": round(trigger / window, 2),
        "note": "the cost curve is flat within a few percent for bands between half and twice this one; the trigger level is second order next to turns, floor, output and cache warmth",
    }


def print_report(report, window):
    print(f"\n== {report['session']}  {report['path']}")
    print(f"turns {report['turns']}  avg ctx {report['avg_ctx']:,}  peak {report['peak_ctx']:,}  re-sent {report['resent']:,}  weighted {report['weighted']:,}  per turn {report['per_turn']:,}")
    if report["compactions"]:
        print("compactions:")
        for c in report["compactions"]:
            after = f"{c['after_12']:,} ({c['after_12'] / window:.0%})" if c["after_12"] else "?"
            floor = f"{c['floor']:,} ({c['floor'] / window:.0%})" if c["floor"] is not None else "? (no turn after it yet)"
            dropped = f"{c['dropped']:,}" if c["dropped"] is not None else "?"
            kept = f", kept {c['post_tokens']:,} tokens" if c.get("post_tokens") else ""
            took = f"  took {c['duration_ms'] / 1000:.0f} s" if c.get("duration_ms") else ""
            print(f"  {c['time']}  before {c['before']:,} ({c['before'] / window:.0%})  floor {floor}  after 12 turns {after}  dropped {dropped}  summary {c['summary_chars']:,} chars{kept}{took}")
        timed = [c["duration_ms"] for c in report["compactions"] if c.get("duration_ms")]
        if timed:
            print(f"  time spent compacting: {sum(timed) / 1000:.0f} s across {len(timed)} timed compactions, median {statistics.median(timed) / 1000:.0f} s")
    else:
        print("compactions: none")
    print("cycles:")
    for i, c in enumerate(report["cycles"], 1):
        print(f"  {i}: {c['from']}-{c['to']}  {c['turns']} turns  avg {c['avg_ctx']:,}  peak {c['peak_ctx']:,}  re-sent {c['resent']:,}  out {c['output']:,}  weighted {c['weighted']:,}  per turn {c['per_turn']:,}")
    if report["cache_breaks"]:
        print("cache breaks (cache rewrite above the --break size):")
        for b in report["cache_breaks"]:
            gap = f"{b['gap_minutes']} min since the previous turn" if b["gap_minutes"] is not None else "gap unknown"
            print(f"  {b['time']}  rewrote {b['rewritten']:,} tokens ({gap}), weighted {b['weighted']:,}")
    rec = report["recommendation"]
    if rec:
        print(f"recommendation: floor {rec['floor']:,}, overhead per compaction {rec['overhead_weighted']:,} weighted, growth {rec['growth_per_turn']:,} per turn, band {rec['band']:,}, trigger about {rec['trigger_tokens']:,} tokens ({rec['trigger_fraction']:.0%} of {window:,})")
        print(f"  {rec['note']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("transcripts", nargs="*", help="transcript files; default every transcript touched on --day")
    parser.add_argument("--day", default="", help="YYYY-MM-DD, keep only turns of that local day")
    parser.add_argument("--window", type=int, default=200000)
    parser.add_argument("--weights", default="in=1,cw=1.25,cr=0.1,out=5")
    parser.add_argument("--break", dest="break_tokens", type=int, default=50000, help="cache write size that counts as a cache break")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    weights = parse_weights(args.weights)
    paths = list(args.transcripts)
    if not paths:
        if not args.day:
            parser.error("give transcript paths or --day")
        day = datetime.strptime(args.day, "%Y-%m-%d").date()
        for path in glob.glob(os.path.join(HOME, ".claude", "projects", "*", "*.jsonl")):
            modified = datetime.fromtimestamp(os.path.getmtime(path)).date()
            if modified >= day:
                paths.append(path)
    reports = []
    for path in paths:
        try:
            report = analyze(path, args, weights)
        except Exception as error:
            print(f"skipped {path}: {error}", file=sys.stderr)
            continue
        if report:
            reports.append(report)
    reports.sort(key=lambda r: -r["weighted"])
    if args.json:
        print(json.dumps(reports, indent=2, default=str))
        return 0
    if not reports:
        print("no turns found")
        return 0
    total = sum(r["weighted"] for r in reports)
    turns = sum(r["turns"] for r in reports)
    print(f"{len(reports)} sessions, {turns} turns, weighted total {total:,}, per turn {round(total / turns):,}")
    for report in reports:
        print_report(report, args.window)
        share = report["weighted"] / total if total else 0
        print(f"share of the total: {share:.0%} of the weighted cost in {report['turns'] / turns:.0%} of the turns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
