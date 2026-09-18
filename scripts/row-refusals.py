"""Register-row refusals and cuts per day, read from a session transcript (tool_result blocks only).

What the wrapper prints is what this reads. scripts/row.sh answers one of three ways: `ROW ok text N/400`
for a row written whole, the same line plus `CUT M chars` for a text cut at a sentence boundary, and
`ROW REFUSED: ...` for a blank text, a bad kind or a bad pane, which writes nothing. A cut is not a
failure, it is a row that lost its tail, so the two are counted apart: a seat whose cuts keep rising is
writing paragraphs into a register that holds decisions.

Per day it prints: rows written, of them cut, refusals, how many refusals came straight after another one
(the retry bounced too), the output tokens of the assistant message that followed each refusal (an upper
bound of the retry cost, the message may hold other calls) and the median text lengths.

  python scripts/row-refusals.py --session <prefix> [--since 2026-09-11] [--row]
  python scripts/row-refusals.py --file <transcript.jsonl>

CLAUDE_PROJECTS_DIRS (a semicolon list) moves the transcript folders; without it they are read from
~/.claude/projects.
"""
import argparse
import collections
import glob
import json
import os
import statistics
import re
import sys

HOME = os.path.expanduser("~")
DEFAULT_DIRS = [os.path.join(HOME, ".claude", "projects")]
# The wrapper's three answers. The legacy shapes (a refusal on length, an accepted row printed as
# "ROW N ok") are read too, so a transcript written before the cut existed still counts.
OK_RX = re.compile(r"ROW ok text (\d+)/(\d+)")
CUT_RX = re.compile(r"CUT (\d+) chars")
REFUSED_RX = re.compile(r"ROW REFUSED: (.+)")
LEGACY_TOO_LONG = re.compile(r"TOO LONG (\d+) \(cut (\d+)\)")
LEGACY_OK = re.compile(r"ROW (\d+) ok")


def project_dirs():
    raw = os.environ.get("CLAUDE_PROJECTS_DIRS")
    return [d.strip() for d in raw.split(";") if d.strip()] if raw else list(DEFAULT_DIRS)


def transcripts(prefix):
    found = []
    for folder in project_dirs():
        found += glob.glob(os.path.join(folder, "*", prefix + "*.jsonl"))
        found += glob.glob(os.path.join(folder, prefix + "*.jsonl"))
    return sorted(found, key=os.path.getmtime)


def texts(content):
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "tool_result":
                    yield from texts(c.get("content"))
                elif c.get("type") == "text":
                    yield c.get("text", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="", help="session id prefix")
    ap.add_argument("--since", default="", help='local day "YYYY-MM-DD"; earlier records are skipped')
    ap.add_argument("--row", action="store_true", help="one ledger line for the last day found")
    ap.add_argument("--file", help="a transcript path, instead of --session")
    a = ap.parse_args()
    paths = [a.file] if a.file else transcripts(a.session)
    if not paths:
        sys.exit("no transcript for %r under %s" % (a.session, ", ".join(project_dirs())))
    records = []
    with open(paths[-1], encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except Exception:
                pass
    events = []
    for i, o in enumerate(records):
        day = (o.get("timestamp") or "")[:10]
        if (a.since and day < a.since) or o.get("type") != "user":
            continue
        for t in texts((o.get("message") or {}).get("content")):
            for m in OK_RX.finditer(t):
                cut = CUT_RX.search(t)
                events.append((day, "cut" if cut else "ok", int(m.group(1)), i))
            for m in LEGACY_OK.finditer(t):
                events.append((day, "ok", int(m.group(1)), i))
            for m in REFUSED_RX.finditer(t):
                events.append((day, "ref", 0, i))
            for m in LEGACY_TOO_LONG.finditer(t):
                events.append((day, "ref", int(m.group(1)), i))

    def next_out(i):
        """The usage of the assistant message that answered the refusal, an upper bound of its cost."""
        for j in range(i + 1, min(i + 6, len(records))):
            o = records[j]
            if o.get("type") == "assistant":
                u = (o.get("message") or {}).get("usage") or {}
                return u.get("output_tokens") or 0, u.get("cache_creation_input_tokens") or 0
        return 0, 0

    per = collections.defaultdict(lambda: {"ref": 0, "ok": 0, "cut": 0, "again": 0, "out": [], "cc": [],
                                           "oklen": [], "cutlen": []})
    prev = None
    for day, kind, val, i in events:
        d = per[day]
        if kind == "ref":
            d["ref"] += 1
            if prev == "ref":
                d["again"] += 1
            out, cc = next_out(i)
            d["out"].append(out)
            d["cc"].append(cc)
        else:
            d["ok"] += 1
            d["oklen"].append(val)
            if kind == "cut":
                d["cut"] += 1
                d["cutlen"].append(val)
        prev = kind
    days = sorted(per)
    if a.row:
        days = days[-1:]
    if not days:
        print("no register rows in %s%s" % (os.path.basename(paths[-1]), " since " + a.since if a.since else ""))
        return
    total = collections.Counter()
    for day in days:
        d = per[day]
        med = lambda xs: statistics.median(xs) if xs else 0
        print("%s rows written %d (cut %d) refused %d (retry refused again: %d) retry output tokens %d "
              "(median %.0f) cache-write %d; text length median written %.0f cut %.0f" % (
                  day, d["ok"], d["cut"], d["ref"], d["again"], sum(d["out"]), med(d["out"]),
                  sum(d["cc"]), med(d["oklen"]), med(d["cutlen"])))
        for k in ("ref", "ok", "cut", "again"):
            total[k] += d[k]
        total["out"] += sum(d["out"])
        total["cc"] += sum(d["cc"])
    if not a.row:
        print("TOTAL %s..%s: written %d cut %d refused %d again %d retry-output %d cache-write %d" % (
            a.since or days[0], days[-1], total["ok"], total["cut"], total["ref"], total["again"],
            total["out"], total["cc"]))


if __name__ == "__main__":
    main()
