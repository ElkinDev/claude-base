"""cold-rewrites.py: how many subagent turns re-sent their context cold, grouped by the event just before them.

    python scripts/cold-rewrites.py --since "2026-09-15 13:00" --until "2026-09-18 13:59"
    python scripts/cold-rewrites.py --since ... --until ... --row      one line for a ledger

The instrument of one token lever: a lane that ends when it launches its own tests, and is not resumed when they come
back green, should leave fewer cold rewrites after a gate-poll loop or a background notification. Read a window before
the change as the baseline and the same shape of window after it. Serves token reduction (it reads the number) and
automation (no agent reads transcripts by hand).

What counts. Every subagent transcript <root>/*/subagents/agent-*.jsonl with its .meta.json beside it (the agent kind),
where the root is one project folder of the projects directory; a root that is the projects directory itself reads
every project (<root>/*/*/subagents/agent-*.jsonl). Assistant turns are deduplicated by message id, since one
response is written as several transcript lines that repeat its usage. A cold rewrite is a turn after the
first whose cache_creation_input_tokens is over 50,000 and whose stamp falls in the window. It is filed under the last
event before it: a tool result of a command that loops on sleep and a gate, lock, .exit or .log file ("gate-poll loop"),
a tool result of a device command ("device script"), any other tool result ("other tool result"), a user message
holding <task-notification> ("background notification"), another user message after the first turn ("seat message"),
or a compaction summary ("compaction"). The gate-class line is the first two groups the lever reaches: gate-poll loop
and background notification. A second line counts fix-round launches (implementer or implementer-light whose
description says fix, round N, rN or delta) started in the window and the median of their whole cache write.

The window is local time (the machine's offset at each stamp), --since inclusive, --until inclusive to the minute; the
transcripts stamp UTC. On the machine it was written for, a baseline of four working days read 148 cold rewrites and
15.5M tokens of cache write, 83 of them and 9.4M in the gate class.

Environment: COLD_REWRITES_ROOT, a project folder or the projects directory (default the projects directory of
CLAUDE_CONFIG_DIR, else ~/.claude/projects). A device command is one that names adb, maestro, uiautomator or
screencap; COLD_REWRITES_DEVICE_RE replaces that pattern when a project drives its devices through its own
wrapper. Exit 0;
2 on a usage error or a root with no readable subagent transcript, one with its .meta.json (a wrong root would read as
a quiet window). A stamp that does not parse or carries no offset leaves its turn out of the window.
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import statistics
import sys

ROOT = os.environ.get("COLD_REWRITES_ROOT") or os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude"), "projects")
COLD = 50000
GATEPOLL = re.compile(r"(until|while)\b.*(sleep|grep)|sleep\s+\d+.*(gate|lock|exit)|(gate|lock|\.exit|\.log).*sleep", re.S)
DEVICE = re.compile(os.environ.get("COLD_REWRITES_DEVICE_RE") or r"adb|maestro|uiautomator|screencap", re.I)
FIX = re.compile(r"\bfix|round [2-9]|\br[2-9]\b|delta", re.I)
GATE_CLASS = ("gate-poll loop", "background notification")


def text_of(c):
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text", "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text")


def utc(stamp):
    """A transcript stamp (ISO, Z) as an aware UTC datetime, or None (unparseable, or no offset to compare with)."""
    try:
        t = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return t if t.tzinfo is not None else None


def local_minute(text):
    """YYYY-MM-DD HH:MM in the machine's local time, as an aware datetime."""
    return dt.datetime.strptime(text, "%Y-%m-%d %H:%M").astimezone()


def read(root, lo, hi):
    cause, cause_cc, cause_kind = collections.Counter(), collections.Counter(), collections.Counter()
    fix, readable, unreadable = [], 0, 0
    found = (glob.glob(os.path.join(root, "*", "subagents", "agent-*.jsonl"))
             + glob.glob(os.path.join(root, "*", "*", "subagents", "agent-*.jsonl")))
    for sp in sorted(set(found)):
        try:
            with open(sp[:-6] + ".meta.json", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        kind, desc = meta.get("agentType"), str(meta.get("description") or "")
        tools, seen, last, first, nturn, total = {}, set(), "start", None, 0, 0
        try:
            handle = open(sp, encoding="utf-8", errors="replace")
        except OSError:
            unreadable += 1  # held by its writer or not a file: said on stderr, the counts are floors
            continue
        readable += 1
        with handle:
            for line in handle:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                t, m = d.get("type"), d.get("message") or {}
                if t == "user":
                    c = m.get("content")
                    if d.get("isCompactSummary"):
                        last = "compaction"
                        continue
                    results = [b for b in c if isinstance(b, dict) and b.get("type") == "tool_result"] if isinstance(c, list) else []
                    for b in results:
                        cmd = tools.get(b.get("tool_use_id"), "")
                        last = ("gate-poll loop" if GATEPOLL.search(cmd) else "device script" if DEVICE.search(cmd)
                                else "other tool result")
                    if not results:
                        if "<task-notification>" in text_of(c):
                            last = "background notification"
                        elif last != "start" or nturn > 0:
                            last = "seat message"
                elif t == "assistant":
                    for b in m.get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            inp = b.get("input") or {}
                            tools[b.get("id")] = str(inp.get("command", "")) if isinstance(inp, dict) else ""
                    u, mid = m.get("usage") or {}, m.get("id")
                    if not u or mid in seen:
                        continue
                    seen.add(mid)
                    when = utc(d.get("timestamp", ""))
                    if first is None:
                        first = when
                    nturn += 1
                    cc = u.get("cache_creation_input_tokens", 0) or 0
                    total += cc
                    if nturn > 1 and cc > COLD and when and lo <= when < hi:
                        cause[last] += 1
                        cause_cc[last] += cc
                        cause_kind[(last, kind)] += 1
        if first and lo <= first < hi and kind in ("implementer", "implementer-light") and FIX.search(desc):
            fix.append(total)
    return readable, unreadable, cause, cause_cc, cause_kind, fix


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", required=True, help="local YYYY-MM-DD HH:MM, inclusive")
    ap.add_argument("--until", required=True, help="local YYYY-MM-DD HH:MM, inclusive to the minute")
    ap.add_argument("--row", action="store_true", help="one line for a ledger")
    a = ap.parse_args(argv)
    try:
        lo = local_minute(a.since.strip())
        hi = local_minute(a.until.strip()) + dt.timedelta(minutes=1)
    except ValueError:
        ap.error("--since and --until take YYYY-MM-DD HH:MM (local time)")
    if hi <= lo:
        ap.error("--until is before --since")
    n, unreadable, cause, cause_cc, cause_kind, fix = read(ROOT, lo, hi)
    if unreadable:
        sys.stderr.write("cold-rewrites: %d subagent transcript(s) could not be opened; the counts are floors\n" % unreadable)
    if n == 0:
        ap.error("no readable subagent transcript (agent-*.jsonl with its .meta.json) under %s; set COLD_REWRITES_ROOT" % ROOT)
    gate_n = sum(cause[c] for c in GATE_CLASS)
    gate_cc = sum(cause_cc[c] for c in GATE_CLASS)
    head = "cold-rewrites %s to %s: %d events %.1fM write; gate-class %d, %.1fM" % (
        a.since.strip(), a.until.strip(), sum(cause.values()), sum(cause_cc.values()) / 1e6, gate_n, gate_cc / 1e6)
    fixes = "fix-round launches %d, median write %s" % (len(fix), "%.1fk" % (statistics.median(fix) / 1e3) if fix else "n/a")
    if a.row:
        print("%s; %s" % (head, fixes))
        return 0
    print(head)
    for c, k in cause.most_common():
        kinds = ", ".join("%s %d" % (kk, v) for (cc, kk), v in sorted(cause_kind.items(), key=lambda x: -x[1]) if cc == c)
        print("  %-24s n %4d  write %5.1fM  (%s)" % (c, k, cause_cc[c] / 1e6, kinds))
    print(fixes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
