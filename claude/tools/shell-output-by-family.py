#!/usr/bin/env python3
"""Shell output of the Claude Code transcripts by command family.

Reads the session transcripts under a projects directory, pairs every Bash or PowerShell
tool call with its result, and reports the characters those results put into the context,
by command family and by session class: main sessions (the orchestrator and any other pane
of the project), lanes (the subagent transcripts) and other files. Characters are what the
transcript holds; the token column is chars / 4, an estimate printed only so families can be
compared with each other, never a bill.

    python shell-output-by-family.py [--projects DIR] [--project SLUG] [--since DATE]
                                     [--until DATE] [--top N] [--largest N]

DATE is YYYY-MM-DD and is compared against the UTC timestamp of the result row, so a window
means the same on every machine. The default window is the last seven days, today included.
Nothing is written anywhere.

A family is the first word of the command that does work: leading `cd ... &&`, environment
assignments and the `echo "== label";` or `date ...;` headers of a batched call are skipped.
`git` keeps its subcommand (`git diff`), the gradle wrappers collapse into `gradle`, the
python launchers into `python`, and shell control words into `shell-ctl`.

Use it before adopting an output filter or a wrapper: a tool that promises a percentage is
judged against the families that dominate here, not against its own benchmark.
"""
import argparse
import datetime as dt
import heapq
import json
import os
import re
import sys

TOOLS = ("Bash", "PowerShell")
QUOTED_OR_WORD = r"(?:\"[^\"]*\"|'[^']*'|\S+)"
CD_PREFIX = re.compile(r"^cd\s+" + QUOTED_OR_WORD + r"\s*(?:&&|;)\s*")
ENV_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S*)\s*(?:&&|;)?\s*")
HEADER_PREFIX = re.compile(r"^(?:echo|date|printf)\b(?:\"[^\"]*\"|'[^']*'|[^;&\n])*?\s*(?:;|&&)\s*")
CONTROL = {"if", "for", "while", "until", "case", "set", "export", "test", "[", "[[", "{", "(", "then", "do"}
PYTHON = {"python", "python3", "py"}
GRADLE = {"gradlew", "gradlew.bat", "gradle", "gradle.bat"}


def strip_prefixes(text):
    while True:
        for pattern in (CD_PREFIX, ENV_PREFIX, HEADER_PREFIX):
            stripped = pattern.sub("", text, count=1)
            if stripped != text:
                text = stripped
                break
        else:
            return text


def family(command):
    """The first meaningful word of a shell command, lower case."""
    text = strip_prefixes((command or "").strip())
    words = text.split()
    if not words:
        return "(empty)"
    first = words[0].strip("\"'").replace("\\", "/")
    first = first.rsplit("/", 1)[-1].lower()
    if first in PYTHON:
        return "python"
    if first in GRADLE:
        return "gradle"
    if first in CONTROL:
        return "shell-ctl"
    if first == "git" and len(words) > 1:
        if words[1] == "-C" and len(words) > 3:
            return "git " + words[3].lower()
        if not words[1].startswith("-"):
            return "git " + words[1].lower()
    return first or "(empty)"


def result_chars(content):
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                total += len(block.get("text") or "")
        return total
    return 0


def classify(path, project_dir):
    parts = os.path.normpath(path).split(os.sep)
    if os.path.dirname(path) == project_dir:
        return "main"
    if "subagents" in parts:
        return "lanes"
    return "other"


def snippet(command, width=70):
    flat = " ".join((command or "").split())
    return flat if len(flat) <= width else flat[: width - 3] + "..."


def scan_file(path, klass, since, until, agg, largest, keep):
    uses = {}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"tool_use"' not in line and '"tool_result"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            message = row.get("message") or {}
            content = message.get("content")
            if not isinstance(content, list):
                continue
            stamp = (row.get("timestamp") or "")[:10]
            for block in content:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "tool_use" and block.get("name") in TOOLS:
                    uses[block.get("id")] = (block.get("input") or {}).get("command", "")
                elif kind == "tool_result" and block.get("tool_use_id") in uses:
                    if not (since <= stamp <= until):
                        continue
                    command = uses[block["tool_use_id"]]
                    chars = result_chars(block.get("content"))
                    fam = family(command)
                    bucket = agg.setdefault(klass, {}).setdefault(fam, [0, 0])
                    bucket[0] += 1
                    bucket[1] += chars
                    entry = (chars, fam, snippet(command), os.path.basename(path))
                    heap = largest.setdefault(klass, [])
                    if len(heap) < keep:
                        heapq.heappush(heap, entry)
                    elif entry > heap[0]:
                        heapq.heapreplace(heap, entry)


def default_projects():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def parse_args(argv):
    today = dt.datetime.now(dt.timezone.utc).date()
    parser = argparse.ArgumentParser(description="Shell output of the transcripts by command family.")
    parser.add_argument("--projects", default=default_projects(), help="projects directory (default: the kit home's projects)")
    parser.add_argument("--project", default=None, help="one project slug under the projects directory")
    parser.add_argument("--since", default=(today - dt.timedelta(days=6)).isoformat(), help="first day, YYYY-MM-DD, UTC")
    parser.add_argument("--until", default=today.isoformat(), help="last day, YYYY-MM-DD, UTC")
    parser.add_argument("--top", type=int, default=12, help="families per class")
    parser.add_argument("--largest", type=int, default=5, help="largest single results per class")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    for name in ("since", "until"):
        try:
            dt.date.fromisoformat(getattr(args, name))
        except ValueError:
            print("bad --%s, expected YYYY-MM-DD" % name, file=sys.stderr)
            return 2
    root = os.path.join(args.projects, args.project) if args.project else args.projects
    if not os.path.isdir(root):
        print("no such directory: %s" % root, file=sys.stderr)
        return 2
    agg, largest, files = {}, {}, 0
    project_dirs = [root] if args.project else [os.path.join(root, d) for d in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, d))]
    for project_dir in project_dirs:
        for folder, _dirs, names in os.walk(project_dir):
            for name in names:
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(folder, name)
                files += 1
                scan_file(path, classify(path, project_dir), args.since, args.until, agg, largest, args.largest)
    print("window %s to %s (UTC), %d transcript files under %s" % (args.since, args.until, files, root))
    for klass in ("main", "lanes", "other"):
        rows = [(fam, v[0], v[1]) for fam, v in agg.get(klass, {}).items()]
        results = sum(r[1] for r in rows)
        chars = sum(r[2] for r in rows)
        print("\n== %s: %d results, %d chars, about %dk tokens" % (klass, results, chars, chars // 4000))
        if not rows:
            continue
        print("  %9s %5s %8s %5s  %s" % ("chars", "n", "mean", "share", "family"))
        for fam, count, total in sorted(rows, key=lambda r: -r[2])[: args.top]:
            print("  %9d %5d %8d %4d%%  %s" % (total, count, total // max(count, 1), 100 * total // max(chars, 1), fam))
        print("  largest results:")
        for chars_one, fam, text, base in sorted(largest.get(klass, []), reverse=True):
            print("  %9d  %-12s %s  [%s]" % (chars_one, fam, text, base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
