#!/usr/bin/env python3
"""Before and after on one page: the frozen baseline against the current window.

Reads two `ledger-day.py --json` payloads and renders `ledger/compare.md`: one
table per axis (cost, speed, quality) with the columns `KPI | baseline |
current | delta | verdict`, a recommendation line, and the ranked list of what
is left to optimize. The other inputs are the gate exit files the lanes write,
`landings.md`, the field reports the owner files after a handoff,
`ledger/meters-log.csv` and `ledger/tool-sizes.csv`.

Attribution: the current side counts only the sessions that started at or after
the cap start, plus the ones the config names; every other session of the same
window goes to a `pre-cap` row that is printed, never dropped, so the day's
total stays honest without polluting the pattern's numbers.

Sample rule: a per-feature KPI has no verdict until the current side has merged
at least `min_features` features; until then the cell says `insufficient sample`
and the raw value is still shown.

No model, no network, no writes outside the ledger directory. Python 3.12.

Usage:
    python ledger-compare.py [--config FILE] [--baseline FILE] [--current FILE]
                             [--cap-start "YYYY-MM-DD HH:MM"] [--out FILE]
                             [--ledger-dir DIR] [--gates-dir DIR]
                             [--landings FILE] [--field-reports DIR]
                             [--kit-dir DIR] [--repo DIR] [--meters-log FILE]
                             [--tool-sizes FILE] [--no-analyzer]
                             [--no-token-ledger] [--freeze-baseline FILE]
                             [--stdout]

With no `--current` it runs `ledger-day.py --dry-run` itself over the window
that starts at the cap start and ends now, so `ledger.md` keeps its own rows and
the compare still reads a window that no scheduled run produces.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LEDGER_DAY = os.path.join(HERE, "ledger-day.py")
DEFAULT_LEDGER_DIR = (os.environ.get("CLAUDE_LEDGER_DIR")
                      or os.path.join(ROOT, "ledger"))
DEFAULT_CONFIG = os.path.join(HERE, "ledger-config.json")

_spec = importlib.util.spec_from_file_location("ledger_day", LEDGER_DAY)
ledger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger)

# Everything that can be retuned lives here and under the `compare` key of
# `ledger-config.json`; `ledger-config.example.json` shows the shape. The two
# session lists exist because a launcher can export the context cap before the
# session writes its first line, so a start time is not always the last word on
# which side a session belongs to.
DEFAULTS = {
    "baseline": "baseline.json",
    "baseline_label": None,
    "cap_start": None,
    "capped_sessions": [],
    "pre_cap_sessions": [],
    "thresholds": {"cost_pct": 15.0, "speed_pct": 15.0, "quality_count": 1.0},
    "min_features": 3,
    "raw_tokens_per_point": 85000000,
    "feature_project": None,
    "gates_dir": os.path.join(tempfile.gettempdir(), "claude"),
    "landings": os.path.join(ROOT, "landings.md"),
    "field_reports": os.path.join(ROOT, "field-reports"),
    "kit_dir": os.path.join(ROOT, "kit"),
}

REOPEN_RE = re.compile(r"-r([2-9])'")
BRANCH_RE = re.compile(r"Merge branch '([^']+)'")
LEVER = {
    "forks": "F8, forks banned as readers",
    "status": "F9, status is a file, not an agent",
    "narration": "section 18, agents writing what nobody reads",
    "probes": "F11, no wake and quota-probe loops",
    "cache": "F10, no account or model switch inside a session",
    "lane_ctx": "F1 and F3, 200k cap and 80-turn lanes",
    "model_mix": "section 19, Sonnet and Opus per task",
    "tool_results": "F5, no whole reads of large files",
}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def parse_dt(value):
    """Parse the times `json.dump(default=str)` writes, or return None."""
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S %z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            when = dt.datetime.strptime(text[:len(fmt) + 6].strip(), fmt)
            return when.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def m(n):
    """Tokens as millions, the unit the owner reads."""
    return None if n is None else n / 1000000.0


def num(value, digits=1, suffix=""):
    if value is None:
        return "n/m"
    return ("%%.%df%%s" % digits) % (value, suffix)


def med(values, p):
    return ledger.percentile(values, p) if values else None


def ratio(top, bottom):
    if top is None or not bottom:
        return None
    return top / float(bottom)


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_config(path):
    """Merge `ledger-config.json`'s `compare` block over the defaults.

    `feature_project` falls back to the top-level key of the same name, so the
    repository whose merges count as features is named once and not twice.
    """
    cfg = json.loads(json.dumps(DEFAULTS))
    if path and os.path.isfile(path):
        try:
            whole = load_json(path) or {}
        except (OSError, ValueError):
            whole = {}
        for key, value in (whole.get("compare") or {}).items():
            if key == "thresholds" and isinstance(value, dict):
                cfg["thresholds"].update(value)
            else:
                cfg[key] = value
        if not cfg.get("feature_project"):
            cfg["feature_project"] = whole.get("feature_project")
    if not cfg.get("feature_project"):
        cfg["feature_project"] = "other"
    return cfg


# --------------------------------------------------------------------------
# One side of the comparison
# --------------------------------------------------------------------------
def row_of(rep, project):
    for row in rep.get("rows") or []:
        if row.get("project") == project:
            return row
    return None


def split_sessions(row, cap_start, capped_names, pre_cap_names):
    """Sessions that ran under the cap, and the ones that did not.

    A session named in `capped_sessions` counts as capped whatever its first
    entry says. The launcher usually exports the new setting when the pane
    opens and the cap start only records when someone confirmed it, so a
    session that began minutes before the reading still ran under the change.
    """
    capped, pre_cap = [], []
    for session in row.get("sessions") or []:
        sid = session.get("id") or ""
        if sid in pre_cap_names:
            pre_cap.append(session)
            continue
        if sid in capped_names:
            capped.append(session)
            continue
        started = parse_dt(session.get("started"))
        if started is None or started >= cap_start:
            capped.append(session)
        else:
            pre_cap.append(session)
    return capped, pre_cap


def gate_files(gates_dir, start, end, session_ids=None):
    """Gate exit files whose run finished inside the window.

    `lane-gate.sh` writes `<run>.exit` into the lane's scratchpad, one
    `PHASE_<name>_EXIT=<code> secs=<n>` line per phase where `secs` counts from
    the start of the gate, so the largest `secs` is the gate's wall clock.
    """
    gates = []
    if not gates_dir or not os.path.isdir(gates_dir):
        return gates
    for path in glob.glob(os.path.join(gates_dir, "**", "*.exit"),
                          recursive=True):
        try:
            when = dt.datetime.fromtimestamp(os.stat(path).st_mtime)
        except OSError:
            continue
        if not (start <= when < end):
            continue
        parts = os.path.normpath(path).split(os.sep)
        session = next((p[:8] for p in parts if len(p) == 36 and p.count("-") == 4), "")
        if session_ids is not None and session not in session_ids:
            continue
        secs, code = 0, None
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    hit = re.search(r"secs=(\d+)", line)
                    if hit:
                        secs = max(secs, int(hit.group(1)))
                    if line.startswith("GATE_EXIT="):
                        code = line.strip().split("=", 1)[1]
        except OSError:
            continue
        gates.append({"run": os.path.basename(path)[:-5], "session": session,
                      "when": when, "secs": secs, "exit": code})
    return gates


def landing_rows(path, start, end):
    """Rows of `landings.md` inside the window, by lane."""
    rows = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line.startswith("| 20"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 5:
                continue
            when = parse_dt(cells[0])
            if when is None or not (start <= when < end):
                continue
            rows.append({"when": when, "event": cells[1], "role": cells[2],
                         "where": cells[3], "id": cells[4]})
    return rows


def landings_start(path):
    """The first row any landing was ever written into, or None.

    A window that closed before this time had no landing hook at all, and a
    zero there would be a missing instrument dressed up as a measurement.
    """
    rows = landing_rows(path, dt.datetime.min, dt.datetime.max)
    return rows[0]["when"] if rows else None


def field_reports(folder, start, end):
    """Owner field reports filed inside the window.

    The file is named for the day the owner hit the defect; the modification
    time is what places it inside a window, because two windows can share a
    day and a day is not precise enough to tell them apart. The name's date is
    the fallback when the file cannot be stat'ed.
    """
    found = []
    if not folder or not os.path.isdir(folder):
        return found
    for name in sorted(os.listdir(folder)):
        hit = re.match(r"(\d{4}-\d{2}-\d{2})", name)
        if not hit:
            continue
        day = parse_dt(hit.group(1))
        if day is None:
            continue
        try:
            when = dt.datetime.fromtimestamp(
                os.stat(os.path.join(folder, name)).st_mtime)
        except OSError:
            when = day
        if start <= when < end:
            found.append(name)
    return found


def kit_runs(folder, start, end):
    """Device acceptance kit evidence folders inside the window."""
    runs = []
    if not folder or not os.path.isdir(folder):
        return runs
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isdir(path):
            continue
        try:
            when = dt.datetime.fromtimestamp(os.stat(path).st_mtime)
        except OSError:
            continue
        if start <= when < end:
            runs.append(name)
    return runs


def measure(rep, cfg, cap_start, sessions=None, label="window"):
    """Every number one side of the comparison contributes.

    `sessions` restricts the side to a set of session ids; None takes the whole
    project row, which is what the baseline uses.
    """
    project = cfg["feature_project"]
    row = row_of(rep, project) or {}
    start = parse_dt(rep.get("start"))
    end = parse_dt(rep.get("end"))
    days = max((end - start).total_seconds() / 86400.0, 1e-9) if start and end else None

    all_sessions = row.get("sessions") or []
    if sessions is None:
        chosen = all_sessions
        share = 1.0
    else:
        chosen = [s for s in all_sessions if s.get("id") in sessions]
        total = sum(s.get("tokens", 0) for s in all_sessions) or 1
        share = sum(s.get("tokens", 0) for s in chosen) / float(total)
    ids = {s.get("id") for s in chosen}
    lanes = [a for a in (row.get("lanes") or []) if a.get("session") in ids]

    tokens = sum(s.get("tokens", 0) for s in chosen)
    fresh = sum(s.get("fresh", 0) for s in chosen)
    turns = sum(s.get("turns", 0) for s in chosen)
    buckets = collections.Counter()
    for session in chosen:
        for name, value in (session.get("buckets") or {}).items():
            buckets[name] += value
    fork_tokens = sum(s.get("fork_tokens", 0) for s in chosen)
    # The analyzer reports cache breaks per project, never per session, so this
    # bucket is allocated by the side's share of the window's tokens.
    cache_tokens = int(round(row.get("cache_tokens", 0) * share))
    cache_breaks = row.get("cache_breaks", 0) * share
    waste = fork_tokens + buckets["status"] + buckets["narration"] + buckets["probes"] + cache_tokens

    orchestrator = max(chosen, key=lambda s: s.get("main_turns", 0), default=None)
    if orchestrator is not None and not orchestrator.get("main_turns"):
        orchestrator = None

    by_model = {}
    for family, stats in (row.get("by_model") or {}).items():
        by_model[family] = {key: int(round(value * share))
                            for key, value in stats.items()}

    # Waste turns are counted per project, never per session, so a side that is
    # a subset of the window takes its share of them, the same allocation the
    # cache bucket uses.
    bucket_turns = {name: value * share
                    for name, value in (row.get("bucket_turns") or {}).items()}

    features = row.get("features", 0)
    merges = rep.get("features") or []
    reopened = [f for f in merges if REOPEN_RE.search(f.get("subject", ""))]

    peaks = [a.get("ctx_peak", 0) for a in lanes]
    lane_turns = [a.get("turns", 0) for a in lanes]
    hours = []
    for lane in lanes:
        first = parse_dt(lane.get("first_human")) or parse_dt(lane.get("started"))
        last = parse_dt(lane.get("last_ts"))
        if first and last and last > first:
            hours.append((last - first).total_seconds() / 3600.0)

    return {
        "label": label, "start": start, "end": end, "days": days,
        "share": share, "bucket_turns": bucket_turns,
        "sessions": chosen, "session_ids": ids, "lanes": lanes,
        "features": features, "merges": merges, "reopened": len(reopened),
        "reopened_names": [(BRANCH_RE.search(f.get("subject", "")) or
                            re.match(r"(.*)", f.get("subject", ""))).group(1)
                           for f in reopened],
        "tokens": tokens, "fresh": fresh, "turns": turns,
        "buckets": dict(buckets), "fork_tokens": fork_tokens,
        "cache_tokens": cache_tokens, "cache_breaks": cache_breaks,
        "waste": waste,
        "waste_pct": (100.0 * waste / tokens) if tokens else None,
        # `points` is already the project's slice of the account meter; the
        # side takes its share of that slice, never of the account total.
        "points_measured": round(row.get("points", 0.0) * share, 3)
                           if rep.get("quota_samples", 0) >= 2 else None,
        "points_estimated": tokens / float(cfg["raw_tokens_per_point"]),
        "orchestrator": orchestrator,
        "main_sessions": sum(1 for s in chosen if s.get("main_turns")),
        "by_model": by_model,
        "lane_peak_median": med(peaks, 50), "lane_peak_p90": med(peaks, 90),
        "lane_turns_median": med(lane_turns, 50),
        "lane_turns_p90": med(lane_turns, 90),
        "lane_hours_median": med(hours, 50) if hours else None,
        "lanes_over_200k": sum(1 for p in peaks if p > 200000),
        "lanes_over_80_turns": sum(1 for t in lane_turns if t > 80),
        "compactions": sum(a.get("compactions", 0) for a in lanes),
        "cache_measurable": rep.get("cache_measurable", False),
    }


def attach_side_evidence(side, cfg, args, restrict):
    """Gates, landings, field reports and kit runs for one side's window."""
    ids = side["session_ids"] if restrict else None
    start, end = side["start"], side["end"]
    side["gates"] = gate_files(args.gates_dir, start, end, ids)
    side["landings"] = landing_rows(args.landings, start, end)
    first_landing = landings_start(args.landings)
    side["landings_measurable"] = bool(first_landing and end and
                                       first_landing < end)
    side["defects"] = field_reports(args.field_reports, start, end)
    side["kit"] = kit_runs(args.kit_dir, start, end)
    gates = side["gates"]
    side["gate_minutes_median"] = (med([g["secs"] for g in gates], 50) / 60.0
                                   if gates else None)
    side["gate_reds"] = sum(1 for g in gates if g["exit"] not in ("0", None))
    return side


# --------------------------------------------------------------------------
# The meters log
# --------------------------------------------------------------------------
def read_meters(path):
    """Rows of `meters-log.csv`, oldest first, unreadable rows dropped."""
    rows = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8", errors="ignore", newline="") as fh:
        for row in csv.DictReader(fh):
            when = parse_dt(row.get("time"))
            if when is None:
                continue
            def number(key):
                try:
                    return float(row.get(key) or "")
                except ValueError:
                    return None
            rows.append({"time": when, "account": (row.get("account") or "").strip(),
                         "weekly_all": number("weekly_all"),
                         "scoped_meter": (row.get("scoped_meter") or "").strip(),
                         "scoped_pct": number("scoped_pct")})
    rows.sort(key=lambda r: r["time"])
    return rows


def meter_climb(rows, key, start, end):
    """Points a meter climbed inside a window, resets counted whole."""
    series = [(r["time"], r[key]) for r in rows
              if start <= r["time"] <= end and r[key] is not None]
    if len(series) < 2:
        return None, len(series)
    climbed, previous = 0.0, series[0][1]
    for _when, value in series[1:]:
        if value >= previous:
            climbed += value - previous
        elif previous - value > ledger.RESET_DROP:
            climbed += value
        previous = value
    return climbed, len(series)


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------
def verdict_of(base, cur, rule, better, thresholds, scale=1):
    """`better`, `worse`, `same` or `n/m` for one pair of numbers.

    The quality rule is one defect, and the cells are rates, so the threshold
    is divided by whatever the rate is per: one defect across five features is
    a step of 0.2 on a per-feature row, not a step of 1.
    """
    if base is None or cur is None:
        return "n/m", "n/m"
    if rule == "mix":
        return "n/m", "-"
    if rule == "count":
        delta = cur - base
        text = "%+.3f" % delta
        limit = thresholds["quality_count"] / float(max(scale, 1))
        if abs(delta) < limit:
            return "same", text
    else:
        if base == 0 and cur == 0:
            return "same", "0"
        if base == 0:
            return ("worse" if better == "lower" else "better"), "+new"
        delta = 100.0 * (cur - base) / abs(base)
        text = "%+.1f%%" % delta
        limit = thresholds["cost_pct"] if rule == "cost" else thresholds["speed_pct"]
        if abs(delta) < limit:
            return "same", text
    improved = delta < 0 if better == "lower" else delta > 0
    return ("better" if improved else "worse"), text


def _improved(base, cur, pct):
    """True when the current side dropped by more than `pct` percent."""
    if base is None or cur is None or not base:
        return False
    return 100.0 * (cur - base) / abs(base) <= -pct


def _worsened(base, cur, pct, absolute=None):
    """True when the current side rose past the threshold, percent or count."""
    if base is None or cur is None:
        return False
    if absolute is not None:
        return (cur - base) >= absolute
    if not base:
        return cur > 0
    return 100.0 * (cur - base) / abs(base) >= pct


def kpi(rows, label, base, cur, digits=1, suffix="", rule="cost",
        better="lower", per_feature=False, note=None, scale=1):
    rows.append({"label": label, "base": base, "cur": cur, "digits": digits,
                 "suffix": suffix, "rule": rule, "better": better,
                 "per_feature": per_feature, "note": note, "scale": scale})


def render_table(rows, thresholds, min_features, current_features, notes):
    """One axis table, with the sample rule applied to per-feature rows."""
    out = ["| KPI | baseline | current | delta | verdict |",
           "| --- | --- | --- | --- | --- |"]
    for row in rows:
        state, delta = verdict_of(row["base"], row["cur"], row["rule"],
                                  row["better"], thresholds, row.get("scale", 1))
        if row["per_feature"] and current_features < min_features:
            state = "insufficient sample (n=%d)" % current_features
            delta = "-"
        label = row["label"]
        if row["note"]:
            # One number per distinct note: the model-mix rows share a note and
            # printing it seven times would bury the ones that matter.
            existing = next((i for i, (_l, t) in enumerate(notes, 1)
                             if t == row["note"]), None)
            if existing is None:
                notes.append((label, row["note"]))
                existing = len(notes)
            label += " [%d]" % existing
        out.append("| %s | %s | %s | %s | %s |"
                   % (label, num(row["base"], row["digits"], row["suffix"]),
                      num(row["cur"], row["digits"], row["suffix"]),
                      delta, state))
    return out


# --------------------------------------------------------------------------
# What is left to optimize
# --------------------------------------------------------------------------
def tool_size_totals(path, start, end):
    """Tool-result characters inside the window, by tool name."""
    totals = collections.Counter()
    biggest = {}
    if not path or not os.path.isfile(path):
        return totals, biggest
    with open(path, encoding="utf-8", errors="ignore", newline="") as fh:
        for row in csv.DictReader(fh):
            when = parse_dt(row.get("time"))
            if when is None or not (start <= when < end):
                continue
            try:
                chars = int(row.get("chars") or 0)
            except ValueError:
                continue
            name = (row.get("tool_name") or "?").strip()
            totals[name] += chars
            if chars > biggest.get(name, 0):
                biggest[name] = chars
    return totals, biggest


def optimization_list(side, tool_totals, limit=5):
    """The window's largest spend sources, each with the lever that cuts it."""
    total = side["tokens"] or 1
    items = []

    def add(source, tokens, lever):
        if tokens and tokens > 0:
            items.append({"source": source, "tokens": int(tokens),
                          "share": 100.0 * tokens / total, "lever": lever})

    add("forks (subagents that inherit the parent context)",
        side["fork_tokens"], LEVER["forks"])
    add("status-reader turns", side["buckets"].get("status", 0), LEVER["status"])
    add("narration turns (no tool call, woken by a machine event)",
        side["buckets"].get("narration", 0), LEVER["narration"])
    add("probe and scheduled wakes that changed nothing",
        side["buckets"].get("probes", 0), LEVER["probes"])
    if side["cache_measurable"]:
        add("cache breaks above 100k", side["cache_tokens"], LEVER["cache"])
    heavy = [a for a in side["lanes"]
             if a.get("ctx_peak", 0) > 200000 or a.get("turns", 0) > 80]
    if heavy:
        add("%d lane(s) above 200k peak or 80 turns (largest: %s)"
            % (len(heavy), heavy[0].get("desc") or heavy[0].get("id")),
            sum(a["tokens"] for a in heavy), LEVER["lane_ctx"])
    for family, stats in sorted(side["by_model"].items(),
                                key=lambda kv: -kv[1].get("subagent", 0)):
        if stats.get("subagent", 0) > 0:
            add("subagent tokens on %s" % family, stats["subagent"],
                LEVER["model_mix"])
    for name, chars in tool_totals.most_common(3):
        add("tool results from %s (%.1f M characters, about %.1f M tokens read)"
            % (name, chars / 1e6, chars / 4e6), chars / 4.0, LEVER["tool_results"])
    items.sort(key=lambda i: -i["tokens"])
    return items[:limit]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def window_text(side):
    if not side["start"] or not side["end"]:
        return "unknown"
    return "%s to %s (%.2f days)" % (side["start"].strftime("%Y-%m-%d %H:%M"),
                                     side["end"].strftime("%Y-%m-%d %H:%M"),
                                     side["days"])


def render(base, cur, pre, cfg, args, meters, tool_totals, run_now):
    thresholds = cfg["thresholds"]
    minf = cfg["min_features"]
    notes = []
    L = []
    add = L.append

    add("# Before and after")
    add("")
    add("Rendered %s by `scripts/ledger-compare.py`. Both sides are measured by "
        "the same script (`ledger-day.py`, sha256 `%s`), so the units match. "
        "Verdicts: `better` or `worse` when the delta passes %.0f percent on "
        "cost, %.0f percent on speed and %.0f count on quality; `same` inside "
        "that band; `n/m` when a side has no data, with the reason in a note. A "
        "per-feature KPI has no verdict until the current side has merged %d "
        "features."
        % (run_now.strftime("%Y-%m-%d %H:%M"),
           (base.get("sha") or "unknown")[:16], thresholds["cost_pct"],
           thresholds["speed_pct"], thresholds["quality_count"], minf))
    add("")
    add("Every KPI on the page is a rate or a share, never a raw count: the two "
        "windows are different lengths, so an absolute number would mostly be "
        "measuring the calendar. The absolute totals live in the header table "
        "and in the daily breakdown.")
    add("")
    add("| side | window | merged features | sessions | lanes | tokens (M) |")
    add("| --- | --- | --- | --- | --- | --- |")
    add("| baseline | %s | %d | %d | %d | %.0f |"
        % (window_text(base), base["features"], len(base["sessions"]),
           len(base["lanes"]), m(base["tokens"])))
    add("| current (under the cap) | %s | %d | %d | %d | %.0f |"
        % (window_text(cur), cur["features"], len(cur["sessions"]),
           len(cur["lanes"]), m(cur["tokens"])))
    if pre["sessions"]:
        add("| pre-cap, excluded from every KPI | %s | counted on the current "
            "side's merges | %d | %d | %.0f |"
            % (window_text(pre), len(pre["sessions"]), len(pre["lanes"]),
               m(pre["tokens"])))
    add("")
    if pre["sessions"]:
        add("Excluded from the current column and printed here so the day's "
            "total stays honest: %s. Those sessions started before the cap at "
            "%s and ran with the 1M window, so their context and waste belong to "
            "the old pattern, not to this one."
            % (", ".join("`%s` (%.0f M tokens, %d turns, peak %.0fk)"
                         % (s["id"], m(s["tokens"]), s["turns"],
                            s["ctx_peak"] / 1000.0)
                         for s in pre["sessions"]),
               cfg["cap_start"]))
        add("")

    # ---------------- cost ----------------
    add("## Cost")
    add("")
    rows = []
    kpi(rows, "raw tokens per merged feature (M)",
        ratio(m(base["tokens"]), base["features"]),
        ratio(m(cur["tokens"]), cur["features"]), digits=2, per_feature=True)
    kpi(rows, "fresh tokens per merged feature (M)",
        ratio(m(base["fresh"]), base["features"]),
        ratio(m(cur["fresh"]), cur["features"]), digits=3, per_feature=True,
        note="fresh is uncached input plus cache write plus output, the part "
             "that is not a cache read")
    kpi(rows, "quota points per merged feature",
        ratio(base["points_estimated"], base["features"]),
        ratio(cur["points_measured"] if cur["points_measured"] is not None
              else cur["points_estimated"], cur["features"]),
        digits=3, per_feature=True,
        note="baseline is estimated (raw tokens over %s per point, plan section "
             "42); the current side is %s. The two are never added together."
             % ("{:,}".format(cfg["raw_tokens_per_point"]),
                "measured from usage-log.csv"
                if cur["points_measured"] is not None else "estimated the same way"))
    orch_b, orch_c = base["orchestrator"], cur["orchestrator"]
    kpi(rows, "orchestrator context p50 (k)",
        (orch_b or {}).get("main_ctx_p50", 0) / 1000.0 if orch_b else None,
        (orch_c or {}).get("main_ctx_p50", 0) / 1000.0 if orch_c else None,
        note="the busiest main session of each side: %s against %s"
             % ((orch_b or {}).get("id", "none"), (orch_c or {}).get("id", "none")))
    kpi(rows, "orchestrator context max (k)",
        (orch_b or {}).get("main_ctx_max", 0) / 1000.0 if orch_b else None,
        (orch_c or {}).get("main_ctx_max", 0) / 1000.0 if orch_c else None)
    kpi(rows, "lane peak context, median (k)",
        ratio(base["lane_peak_median"], 1000),
        ratio(cur["lane_peak_median"], 1000))
    kpi(rows, "lane peak context, p90 (k)",
        ratio(base["lane_peak_p90"], 1000), ratio(cur["lane_peak_p90"], 1000))
    kpi(rows, "lane turns, median", base["lane_turns_median"],
        cur["lane_turns_median"], digits=0)
    kpi(rows, "lane turns, p90", base["lane_turns_p90"],
        cur["lane_turns_p90"], digits=0)
    kpi(rows, "waste share of the window (%)", base["waste_pct"],
        cur["waste_pct"], suffix="%")

    def bucket_share(side, key):
        tokens = side["fork_tokens"] if key == "forks" \
            else side["buckets"].get(key, 0)
        return (100.0 * tokens / side["tokens"]) if side["tokens"] else None

    for key, label in (("forks", "waste: forks (% of the window)"),
                       ("status", "waste: status readers (% of the window)"),
                       ("narration", "waste: narration turns (% of the window)"),
                       ("probes", "waste: probes (% of the window)")):
        kpi(rows, label, bucket_share(base, key), bucket_share(cur, key),
            digits=2, suffix="%",
            note="every bucket is a share of its own side's tokens, because "
                 "the two windows are not the same length and the absolute "
                 "millions would only be measuring the calendar")
    kpi(rows, "cache breaks above 100k per day",
        ratio(base["cache_breaks"], base["days"]) if base["cache_measurable"] else None,
        ratio(cur["cache_breaks"], cur["days"]) if cur["cache_measurable"] else None,
        digits=1,
        note="the official analyzer is the only source of the cache-break list; "
             "a side without it reads n/m instead of a zero")
    families = sorted(set(base["by_model"]) | set(cur["by_model"]))
    for family in families:
        for kind, name in (("main", "main contexts"), ("subagent", "subagents")):
            b = base["by_model"].get(family, {}).get(kind, 0)
            c = cur["by_model"].get(family, {}).get(kind, 0)
            if not b and not c:
                continue
            kpi(rows, "model mix: %s, %s (%% of the window)" % (family, name),
                (100.0 * b / base["tokens"]) if base["tokens"] else None,
                (100.0 * c / cur["tokens"]) if cur["tokens"] else None,
                digits=1, suffix="%", rule="mix",
                note="the model mix has no direction on its own; these rows are "
                     "the input to next week's decision on whether lane main "
                     "contexts stay on Fable, read next to the meters table")
    L += render_table(rows, thresholds, minf, cur["features"], notes)
    add("")

    # ---------------- speed ----------------
    add("## Speed")
    add("")
    rows = []
    kpi(rows, "lane wall clock, first prompt to last turn, median (hours)",
        base["lane_hours_median"], cur["lane_hours_median"], digits=2,
        rule="speed",
        note="measured inside the lane transcript; the brief-to-merge hour "
             "needs a lane-to-branch map, which no artifact carries yet")
    kpi(rows, "gates per merged feature",
        ratio(float(len(base["gates"])), base["features"]),
        ratio(float(len(cur["gates"])), cur["features"]), digits=2,
        rule="speed", per_feature=True)
    kpi(rows, "gradle minutes per gate, median",
        base["gate_minutes_median"], cur["gate_minutes_median"], digits=1,
        rule="speed",
        note="the largest `secs=` of each `<run>.exit` file, which counts from "
             "the gate's start, so it includes the mutex wait")
    kpi(rows, "red gate share (%)",
        (100.0 * base["gate_reds"] / len(base["gates"])) if base["gates"] else None,
        (100.0 * cur["gate_reds"] / len(cur["gates"])) if cur["gates"] else None,
        digits=1, suffix="%", rule="speed",
        note="a red gate is a rerun; the share is comparable across windows of "
             "different length where the count is not (baseline %d gates, "
             "current %d)" % (len(base["gates"]), len(cur["gates"])))
    kpi(rows, "narration turns per lane",
        ratio(base["bucket_turns"].get("narration", 0.0), len(base["lanes"])),
        ratio(cur["bucket_turns"].get("narration", 0.0), len(cur["lanes"])),
        digits=2, rule="speed",
        note="turns, not tokens; a lane that narrates is a lane that waits")
    kpi(rows, "waiting turns per lane (probes and scheduled wakes)",
        ratio(base["bucket_turns"].get("probes", 0.0), len(base["lanes"])),
        ratio(cur["bucket_turns"].get("probes", 0.0), len(cur["lanes"])),
        digits=2, rule="speed")
    kpi(rows, "landings recorded per lane",
        ratio(float(len(base["landings"])), len(base["lanes"]))
        if base["landings_measurable"] else None,
        ratio(float(len(cur["landings"])), len(cur["lanes"]))
        if cur["landings_measurable"] else None, digits=2, rule="speed",
        better="higher",
        note="a side whose window closed before the landing hook existed reads "
             "n/m, never a zero: the file has no row from it at all, and a zero "
             "there would be a missing instrument dressed up as a measurement")
    L += render_table(rows, thresholds, minf, cur["features"], notes)
    add("")

    # ---------------- quality ----------------
    add("## Quality")
    add("")
    add("The threshold is one defect. The cells are rates, so a row that is "
        "per feature trips at one defect spread over the current side's %d "
        "feature(s), and a row that is per lane at one over its %d lane(s)."
        % (max(cur["features"], 1), max(len(cur["lanes"]), 1)))
    add("")
    per_feature_scale = max(cur["features"], 1)
    per_lane_scale = max(len(cur["lanes"]), 1)
    rows = []
    kpi(rows, "defects the owner filed, per merged feature",
        ratio(float(len(base["defects"])), base["features"]),
        ratio(float(len(cur["defects"])), cur["features"]), digits=3,
        rule="count", per_feature=True, scale=per_feature_scale,
        note="one file per defect under `field-reports/`, the only manual input "
             "on the page; %d filed on the baseline side against %d on the "
             "current side"
             % (len(base["defects"]), len(cur["defects"])))
    kpi(rows, "device-kit runs before handoff", float(len(base["kit"])) or None,
        float(len(cur["kit"])) or None, digits=0, rule="count", better="higher",
        note="the kit evidence folder does not exist on either side yet; F13 "
             "creates it and this row starts reading")
    kpi(rows, "compactions per lane",
        ratio(float(base["compactions"]), len(base["lanes"])),
        ratio(float(cur["compactions"]), len(cur["lanes"])), digits=3,
        rule="count", scale=per_lane_scale)
    kpi(rows, "characters re-read in the turn after a compaction", None, None,
        rule="count",
        note="no artifact records the tool-result size of the turn that follows "
             "a compaction, so neither side has a number. A "
             "`post_compaction_chars` field on `ledger-day.py`'s lane entries "
             "would read it on both sides at once, since the transcripts are "
             "still on disk")
    kpi(rows, "review rounds per feature", None, None, rule="count",
        per_feature=True, scale=per_feature_scale,
        note="`ledger-day.py` counts a lane's turns, not the SendMessage fix "
             "cycles inside it. Counting the `SendMessage` tool uses per lane "
             "would read it on both sides, again from the transcripts already "
             "on disk")
    kpi(rows, "lanes reopened per merged feature (branch suffix r2 or higher)",
        ratio(float(base["reopened"]), base["features"]),
        ratio(float(cur["reopened"]), cur["features"]), digits=3, rule="count",
        per_feature=True, scale=per_feature_scale,
        note="%d reopened on the baseline side against %d on the current side"
             % (base["reopened"], cur["reopened"]))
    kpi(rows, "lanes above 200k peak context (% of lanes)",
        (100.0 * base["lanes_over_200k"] / len(base["lanes"]))
        if base["lanes"] else None,
        (100.0 * cur["lanes_over_200k"] / len(cur["lanes"]))
        if cur["lanes"] else None, digits=1, suffix="%", rule="cost")
    kpi(rows, "lanes above 80 turns (% of lanes)",
        (100.0 * base["lanes_over_80_turns"] / len(base["lanes"]))
        if base["lanes"] else None,
        (100.0 * cur["lanes_over_80_turns"] / len(cur["lanes"]))
        if cur["lanes"] else None, digits=1, suffix="%", rule="cost")
    L += render_table(rows, thresholds, minf, cur["features"], notes)
    add("")
    if base["reopened_names"]:
        add("Reopened on the baseline side: %s."
            % ", ".join("`%s`" % n for n in base["reopened_names"]))
        add("")

    # ---------------- meters ----------------
    add("## Meters")
    add("")
    if meters["rows"]:
        add("| meter | baseline window | current window | samples |")
        add("| --- | --- | --- | --- |")
        add("| all models, points climbed | %s | %s | %d |"
            % (num(meters["base_all"], 1), num(meters["cur_all"], 1),
               meters["cur_all_n"]))
        add("| %s, points climbed | %s | %s | %d |"
            % (meters["scoped_name"] or "scoped", num(meters["base_scoped"], 1),
               num(meters["cur_scoped"], 1), meters["cur_scoped_n"]))
        add("")
        add("`ledger-nightly.ps1` appends one line per run to "
            "`ledger/meters-log.csv` with two direct reads of the usage "
            "endpoint, no model and no session. A window needs two samples "
            "before a climb can be read, which is why a fresh log shows `n/m`.")
    else:
        add("`ledger/meters-log.csv` is empty or missing, so neither meter has "
            "a climb to report. `ledger-nightly.ps1` appends a line at 08:00 "
            "and 18:00.")
    add("")

    # ---------------- recommendation ----------------
    add("## Recommendation")
    add("")
    # The three axes are read on their primary unit, each one a rate so the
    # verdict does not move when the window gets longer.
    cost_ok = _improved(ratio(base["tokens"], base["features"]),
                        ratio(cur["tokens"], cur["features"]),
                        thresholds["cost_pct"])
    speed_ok = _improved(base["lane_hours_median"], cur["lane_hours_median"],
                         thresholds["speed_pct"])
    quality_ok = not _worsened(
        ratio(float(len(base["defects"])), base["features"]),
        ratio(float(len(cur["defects"])), cur["features"]), None,
        thresholds["quality_count"] / float(max(cur["features"], 1)))
    if cur["features"] < minf:
        add("Not decidable yet: the current side has merged %d feature(s) and "
            "the rule needs %d before a per-feature verdict counts. The cells "
            "that do not depend on a merge (context, waste, lane shape) are "
            "already comparable and are the ones to read today."
            % (cur["features"], minf))
    elif cost_ok and speed_ok and quality_ok:
        add("Keep the pattern: cost, speed and quality all improved.")
    elif cost_ok and speed_ok and not quality_ok:
        add("Raise the cap to 300k for build lanes: cost and speed improved and "
            "quality worsened, which is the case plan section 37 names.")
    else:
        add("Hold and re-read at the next run: the three axes do not agree yet.")
    add("")
    add("The owner decides; this page only prints what the numbers say.")
    add("")

    # ---------------- what is left ----------------
    add("## What is left to optimize")
    add("")
    items = optimization_list(cur, tool_totals)
    if items:
        add("The current window's largest spend sources, ranked. The top line "
            "is the next optimization. The list re-ranks itself as the levers "
            "land, and the sources overlap on purpose: a lane's tokens already "
            "contain its narration turns.")
        add("")
        add("| # | source | tokens (M) | share of the window | lever |")
        add("| --- | --- | --- | --- | --- |")
        for i, item in enumerate(items, 1):
            add("| %d | %s | %.1f | %.1f%% | %s |"
                % (i, item["source"], m(item["tokens"]), item["share"],
                   item["lever"]))
    else:
        add("No spend source above zero in this window yet.")
    add("")
    add("One source is missing from the ranking whenever the tool-size log has "
        "no target column: the files re-read most, by path. The log records the "
        "time, the session, the tool and the size of every large result, and "
        "the tool rows above are as close as this run gets without the path. "
        "Adding a target column to whichever hook writes that log makes the "
        "path row appear here on the next run with no other change.")
    add("")

    # ---------------- notes ----------------
    if notes:
        add("## Notes")
        add("")
        for i, (label, text) in enumerate(notes, 1):
            add("%d. %s: %s" % (i, label, text))
        add("")
    add("Sources this run read: `%s`, `%s`, the gate exit files under `%s`, "
        "`%s`, `%s`, `%s` and `%s`."
        % (args.baseline, args.current or "a live ledger-day.py run",
           args.gates_dir, args.landings, args.field_reports, args.meters_log,
           args.tool_sizes))
    add("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# Running ledger-day for the current side
# --------------------------------------------------------------------------
def run_current(args, cap_start):
    """Run ledger-day.py over the cap window and read back its JSON."""
    out = os.path.join(tempfile.gettempdir(), "ledger-compare-current.json")
    cmd = [sys.executable, LEDGER_DAY, "--since", cap_start.strftime("%Y-%m-%d %H:%M"),
           "--dry-run", "--json", out]
    if args.no_analyzer:
        cmd.append("--no-analyzer")
    if args.no_token_ledger:
        cmd.append("--no-token-ledger")
    if args.repo:
        cmd += ["--repo", args.repo]
    if args.ledger_dir:
        cmd += ["--ledger-dir", args.ledger_dir]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0 or not os.path.exists(out):
        raise SystemExit("ledger-day.py exited %d: %s"
                         % (result.returncode, (result.stderr or "")[-400:]))
    return load_json(out)


def freeze_baseline(path, cfg, label=None):
    """Annotate a ledger-day payload as a frozen baseline, in place and once.

    A window that predates the quota log has no measured points at all. That
    side falls back to the raw-token calibration, and the estimate is labelled
    so a reader never adds it to a measured number from the other side.
    """
    data = load_json(path)
    row = row_of(data, cfg["feature_project"]) or {}
    tokens = row.get("tokens", 0)
    features = row.get("features", 0) or 0
    measured = row.get("points") if data.get("quota_samples", 0) >= 2 else None
    points = {
        "measured": measured,
        "estimated": round(tokens / float(cfg["raw_tokens_per_point"]), 1),
        "estimated_basis": "raw tokens over %s per point"
                           % "{:,}".format(cfg["raw_tokens_per_point"]),
        "label": "measured" if measured is not None else "estimated",
    }
    if measured is None:
        points["measured_reason"] = (
            "the quota log has fewer than two samples inside this window, so "
            "no climb can be read and only the estimate is available")
    data["baseline"] = {
        "label": label or "the window this pattern is measured against",
        "frozen_at": dt.datetime.now().replace(microsecond=0).isoformat(sep=" "),
        "window": "%s to %s" % (data.get("start"), data.get("end")),
        "ledger_day_sha256": data.get("script_sha256"),
        "merged_features": features,
        "quota_points": points,
        "rule": "frozen; a recompute is a new file with its own date in the "
                "name, never an overwrite of this one",
    }
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=1, default=str)
    return data["baseline"]


def main(argv=None):
    now = dt.datetime.now().replace(microsecond=0)
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--ledger-dir", default=DEFAULT_LEDGER_DIR)
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--current", default=None)
    parser.add_argument("--cap-start", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--gates-dir", default=None)
    parser.add_argument("--landings", default=None)
    parser.add_argument("--field-reports", default=None)
    parser.add_argument("--kit-dir", default=None)
    parser.add_argument("--meters-log", default=None)
    parser.add_argument("--tool-sizes", default=None)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--no-analyzer", action="store_true")
    parser.add_argument("--no-token-ledger", action="store_true")
    parser.add_argument("--freeze-baseline", default=None)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.cap_start:
        cfg["cap_start"] = args.cap_start
    for key in ("gates_dir", "landings", "field_reports", "kit_dir"):
        value = getattr(args, key)
        if value:
            cfg[key] = value
        else:
            setattr(args, key, cfg[key])
    if args.baseline is None:
        args.baseline = cfg["baseline"]
        if not os.path.isabs(args.baseline):
            args.baseline = os.path.join(args.ledger_dir, args.baseline)
    if args.meters_log is None:
        args.meters_log = os.path.join(args.ledger_dir, "meters-log.csv")
    if args.tool_sizes is None:
        args.tool_sizes = os.path.join(args.ledger_dir, "tool-sizes.csv")
    if args.out is None:
        args.out = os.path.join(args.ledger_dir, "compare.md")

    if args.freeze_baseline:
        block = freeze_baseline(args.freeze_baseline, cfg,
                                cfg.get("baseline_label"))
        print("frozen: %s" % args.freeze_baseline)
        print(json.dumps(block, indent=1))
        return 0

    cap_start = parse_dt(cfg["cap_start"])
    if cap_start is None:
        raise SystemExit("cannot parse the cap start %r" % cfg["cap_start"])

    base_rep = load_json(args.baseline)
    cur_rep = load_json(args.current) if args.current else run_current(args, cap_start)

    if base_rep.get("schema") != cur_rep.get("schema"):
        print("warning: the two sides carry different payload schemas (%s and "
              "%s); recompute the baseline before trusting the deltas"
              % (base_rep.get("schema"), cur_rep.get("schema")), file=sys.stderr)

    base = measure(base_rep, cfg, cap_start, None, "baseline")
    base["sha"] = base_rep.get("script_sha256") or ""
    attach_side_evidence(base, cfg, args, restrict=False)

    cur_row = row_of(cur_rep, cfg["feature_project"]) or {"sessions": []}
    capped, pre_cap = split_sessions(cur_row, cap_start,
                                     set(cfg["capped_sessions"]),
                                     set(cfg["pre_cap_sessions"]))
    cur = measure(cur_rep, cfg, cap_start, {s["id"] for s in capped}, "current")
    attach_side_evidence(cur, cfg, args, restrict=True)
    pre = measure(cur_rep, cfg, cap_start, {s["id"] for s in pre_cap}, "pre-cap")
    attach_side_evidence(pre, cfg, args, restrict=True)

    meter_rows = read_meters(args.meters_log)
    cur_all, cur_all_n = meter_climb(meter_rows, "weekly_all",
                                     cur["start"] or now, cur["end"] or now)
    cur_scoped, cur_scoped_n = meter_climb(meter_rows, "scoped_pct",
                                           cur["start"] or now, cur["end"] or now)
    base_all, _ = meter_climb(meter_rows, "weekly_all",
                              base["start"] or now, base["end"] or now)
    base_scoped, _ = meter_climb(meter_rows, "scoped_pct",
                                 base["start"] or now, base["end"] or now)
    meters = {"rows": meter_rows, "cur_all": cur_all, "cur_all_n": cur_all_n,
              "cur_scoped": cur_scoped, "cur_scoped_n": cur_scoped_n,
              "base_all": base_all, "base_scoped": base_scoped,
              "scoped_name": meter_rows[-1]["scoped_meter"] if meter_rows else ""}

    tool_totals, _biggest = tool_size_totals(args.tool_sizes, cur["start"] or now,
                                             cur["end"] or now)

    page = render(base, cur, pre, cfg, args, meters, tool_totals, now)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    if args.stdout:
        sys.stdout.write(page)
    else:
        print("compare page: %s" % args.out)
        print("baseline %d feature(s), current %d feature(s), pre-cap sessions %d"
              % (base["features"], cur["features"], len(pre["sessions"])))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    sys.exit(main())
