"""Failed tool calls per day, grouped by error signature, read from a session transcript.

A failed call is a tool_result with is_error true, or any tool_result holding a line that matches one of the
known-failure rules in scripts/tool-errors.rules (producer TAB regex per line; a hit counts only when the command
that produced the result contains the producer, so a transcript or report read that quotes an old failure never
counts; a refused register row rides inside a compound command whose exit code is 0, so is_error alone misses
it). The signature of an is_error result is its failure line (the last line that reads like an error, else the
last non-empty line, never the "Exit code N" header), with digits, hex ids and paths collapsed, so repeats of
one defect group together. Per day it prints tool calls (the session's own, subagents have their own
transcripts), failed calls, the failure share, and the top signatures with a count, the tool name and one
example command prefix. A ledger that appends the --row line twice a day surfaces a defect class the same day,
without anyone reading a pane. Usage:
  python scripts/tool-errors.py --session <prefix> [--since 2026-09-11] [--top 8] [--row]
  python scripts/tool-errors.py --file <transcript.jsonl> --row

CLAUDE_PROJECTS_DIRS (a semicolon list) moves the transcript folders, CLAUDE_TOOL_ERRORS_RULES the rule file.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
DEFAULT_DIRS = [os.path.join(HOME, ".claude", "projects")]
DIGITS = re.compile(r"\d+")
HEXID = re.compile(r"\b[0-9a-f]{7,40}\b")
PATH = re.compile(r"[A-Za-z]:[\\/][^\s'\"]+|/[a-z]/[^\s'\"]+")
EXIT = re.compile(r"^Exit code \d+$")
ERRORISH = re.compile(r"error|Error|ERROR|failed|FAILED|not found|No such|TOO LONG|Traceback|denied|refused|REFUSED|"
                      r"cannot|Cannot|invalid|Invalid|timeout|Timeout|unexpected|Blocked|rejected|missing|Exception|_EXIT=[1-9]")
RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool-errors.rules")


def texts(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def project_dirs():
    raw = os.environ.get("CLAUDE_PROJECTS_DIRS")
    return [d.strip() for d in raw.split(";") if d.strip()] if raw else list(DEFAULT_DIRS)


def transcripts(prefix):
    found = []
    for folder in project_dirs():
        found += glob.glob(os.path.join(folder, "*", prefix + "*.jsonl"))
        found += glob.glob(os.path.join(folder, prefix + "*.jsonl"))
    return sorted(found, key=os.path.getmtime)


def load_rules(path=None):
    """Returns (producer, regex) pairs; a producer of '*' matches any command; 'a!b' means the command holds a and not b."""
    rules = []
    try:
        with open(path or os.environ.get("CLAUDE_TOOL_ERRORS_RULES") or RULES_PATH, encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\r\n")
                if not line.strip() or line.startswith("#"):
                    continue
                producer, _, rx = line.partition("\t")
                if not rx:
                    producer, rx = "*", producer
                rules.append((producer.strip(), re.compile(rx.strip())))
    except FileNotFoundError:
        pass
    return rules


def normalise(line):
    line = PATH.sub("<path>", line)
    line = HEXID.sub("<id>", line)
    line = DIGITS.sub("N", line)
    return line[:110]


def signature(text):
    """The failure line of an is_error result: the last error-looking line, else the last non-empty line."""
    lines = [l.strip() for l in text.splitlines() if l.strip() and not EXIT.match(l.strip())]
    if not lines:
        return "(empty)"
    errorish = [l for l in lines if ERRORISH.search(l)]
    return normalise(errorish[-1] if errorish else lines[-1])


def hits_for(text, command, rules, is_error):
    """The failure signatures of one tool result: every rule its producer allows, else the is_error line."""
    hits = []
    for producer, rule in rules:
        want, _, unwanted = producer.partition("!")
        if want != "*" and want not in command:
            continue
        if unwanted and unwanted in command:
            continue
        for m in rule.finditer(text):
            hits.append(normalise(m.group(0)))
    if not hits and is_error:
        hits.append(signature(text))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="", help="session id prefix")
    ap.add_argument("--since", default="", help='local day "YYYY-MM-DD"; earlier records are skipped')
    ap.add_argument("--top", type=int, default=8)
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
    use = {}  # tool_use id -> (tool name, command prefix)
    cmd_full = {}  # tool_use id -> the whole command text, for the producer filter
    calls = collections.Counter()
    for o in records:
        if o.get("type") != "assistant":
            continue
        day = (o.get("timestamp") or "")[:10]
        for c in (o.get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                inp = c.get("input") or {}
                cmd = str(inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("prompt") or "")
                use[c.get("id")] = (c.get("name"), cmd.replace("\n", " ")[:70])
                cmd_full[c.get("id")] = cmd
                if day >= a.since:
                    calls[day] += 1
    failed = collections.Counter()
    sigs = collections.defaultdict(collections.Counter)
    example = {}
    rules = load_rules()
    for o in records:
        if o.get("type") != "user":
            continue
        day = (o.get("timestamp") or "")[:10]
        if day < a.since:
            continue
        content = (o.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if not (isinstance(c, dict) and c.get("type") == "tool_result"):
                continue
            text = texts(c.get("content"))
            tid = c.get("tool_use_id")
            name, cmd = use.get(tid, ("?", ""))
            for sig in hits_for(text, cmd_full.get(tid, ""), rules, c.get("is_error")):
                failed[day] += 1
                key = (name, sig)
                sigs[day][key] += 1
                example.setdefault((day, key), cmd)
    days = sorted(set(calls) | set(failed))
    if a.row:
        days = days[-1:]
    for day in days:
        n, fcount = calls[day], failed[day]
        share = (100.0 * fcount / n) if n else 0.0
        top = sigs[day].most_common(a.top)
        if a.row:
            tops = "; ".join(f"{k[0]} '{k[1][:60]}' x{v}" for k, v in top[:4])
            print(f"tool-errors {day}: calls {n}, failed {fcount} ({share:.0f} pct); top: {tops}")
        else:
            print(f"{day} calls {n} failed {fcount} ({share:.0f} pct)")
            for k, v in top:
                print(f"    x{v:3d} {k[0]:<10} {k[1]}  e.g. {example[(day, k)]}")


if __name__ == "__main__":
    main()
