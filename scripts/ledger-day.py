#!/usr/bin/env python3
"""Twice-daily token ledger: quota points per merged feature and waste share.

Run at 08:00 and 18:00 by `ledger-nightly.ps1` (or by cron on Mac and Linux).
It reads four sources for a time window and appends one row per project to
`<ledger>/ledger.md` plus a full breakdown to
`<ledger>/daily/<yyyy-mm-dd>-<HHMM>.md`, where `<ledger>` is `CLAUDE_LEDGER_DIR`
or the `ledger` folder next to this script's parent.

The project mapping and the repository whose merges count as features live in
`ledger-config.json` beside this script; `ledger-config.example.json` shows the
shape and is used when no local file exists.

Sources, in the order the breakdown reports them:

1. The official session analyzer (`analyze-sessions.mjs --json --since ...`),
   for `by_project` totals as a cross-check and for `cache_breaks`.
2. `token-ledger-v2.py --json` per main session touched inside the window, for
   the subagent fork flags and the compaction counts as a cross-check.
3. `ledger/usage-log.csv`, the only quota source (the OAuth token and the
   credentials file are never read).
4. The merge commits of the configured repository's main branch inside the
   window.

Everything that decides a number in the row is computed by this script's own
transcript scan, which is exact on both ends of the window; the two external
tools are cross-checks whose results are printed next to it. The analyzer has
no `--until`, so its totals cover "since the window start until now", which is
the same thing only when the script runs at the window end.

The markdown row format never changes. The `--json` payload carries more than
the row does, for `ledger-compare.py`: per project `fresh` (uncached input plus
cache write plus output), `by_model` (fable, opus, sonnet, haiku, synthetic,
other, each split into main contexts and subagents), `kind_tokens` and
`kind_fresh`, `main_ctx_p50/p90/max` and a `lanes` list with one entry per
subagent transcript (turns, tokens, fresh, peak and p50 context, compactions,
models, first entry, first human prompt, last entry). Each session also carries
`fresh`, `started`, `fork_tokens`, its own `buckets` and its main-context
percentiles (`main_turns`, `main_ctx_p50/p90/max`), so a comparison can split a
window by session without a second scan; the cache-break bucket stays
project-level, because the analyzer reports it that way. The payload carries
`schema` and `script_sha256`.

Usage:
    python ledger-day.py [--since 24h | --since "2026-08-27 08:00"]
                         [--until "2026-08-27 18:00"]
                         [--ledger-dir DIR] [--projects-dir DIR]
                         [--usage-log FILE] [--repo DIR] [--git-log-file FILE]
                         [--analyzer PATH] [--analyzer-json FILE] [--no-analyzer]
                         [--no-token-ledger] [--json OUT] [--dry-run]

No model, no network, no writes outside the ledger directory.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "ledger-config.json")
EXAMPLE_CONFIG = os.path.join(HERE, "ledger-config.example.json")
TOKEN_LEDGER = os.path.join(HERE, "token-ledger-v2.py")


def find_analyzer():
    """The official session-report analyzer, in whichever marketplace holds it."""
    pattern = os.path.join(os.path.expanduser("~"), ".claude", "plugins",
                           "marketplaces", "*", "plugins", "session-report",
                           "skills", "session-report", "analyze-sessions.mjs")
    found = sorted(glob.glob(pattern))
    return found[0] if found else None


def load_config(path=CONFIG_PATH):
    """Read ledger-config.json, falling back to the committed example.

    Every key is optional. `projects` maps a lowercase transcript folder prefix
    to a project name and the longest matching prefix wins, `project_order`
    fixes the order of the rows, `repo` is the repository whose merges count as
    features, `branch` its main branch, and `ledger_dir` overrides where the
    ledger is written when CLAUDE_LEDGER_DIR is not set.
    """
    config = {"projects": {}, "project_order": [], "repo": None,
              "branch": "main", "ledger_dir": None}
    source = path if os.path.exists(path) else EXAMPLE_CONFIG
    if source == EXAMPLE_CONFIG and os.path.exists(source):
        sys.stderr.write("no ledger-config.json beside the script, reading the "
                         "example instead; its project names are placeholders\n")
    if os.path.exists(source):
        try:
            with open(source, encoding="utf-8") as fh:
                config.update(json.load(fh))
        except (OSError, ValueError) as exc:
            sys.stderr.write("%s is unreadable, using defaults: %s\n" % (source, exc))
    else:
        sys.stderr.write("no ledger-config.json beside the script, every "
                         "transcript folder will be reported as `other`\n")
    config["projects"] = {str(k).lower(): v
                          for k, v in (config.get("projects") or {}).items()}
    order = list(config.get("project_order") or [])
    for name in config["projects"].values():
        if name not in order:
            order.append(name)
    if "other" not in order:
        order.append("other")
    config["project_order"] = order
    return config


CONFIG = load_config()
PROJECT_PREFIXES = CONFIG["projects"]
PROJECT_ORDER = CONFIG["project_order"]
# The merges of the configured repository belong to the first project listed.
CONFIG_FEATURE_PROJECT = (CONFIG.get("feature_project")
                          or (PROJECT_ORDER[0] if PROJECT_ORDER else "other"))
DEFAULT_LEDGER_DIR = (os.environ.get("CLAUDE_LEDGER_DIR")
                      or CONFIG.get("ledger_dir")
                      or os.path.join(os.path.dirname(HERE), "ledger"))
DEFAULT_PROJECTS_DIR = os.path.join(
    os.path.expanduser("~"), ".claude", "projects")
DEFAULT_REPO = CONFIG.get("repo")
DEFAULT_BRANCH = CONFIG.get("branch") or "main"
DEFAULT_ANALYZER = find_analyzer()

# A turn that does any of this is work, never waste.
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Artifact"}
LAUNCH_TOOLS = {"Agent", "Task"}
MESSAGE_TOOLS = {"SendMessage"}
PRODUCTIVE_TOOLS = WRITE_TOOLS | LAUNCH_TOOLS | MESSAGE_TOOLS
SHELL_TOOLS = {"Bash", "PowerShell", "BashOutput"}
PRODUCTIVE_CMD_RE = re.compile(
    r"\bgit\s+(commit|merge|push|tag|cherry-pick|revert|rebase)\b"
    r"|\bcc\s+[a-z]\b"
    r"|\bclaude\s+-p\b"
    r"|\bherdr\s+(agent\s+(launch|start|send|new)|pane\s+send)\b"
    r"|\bschtasks\s+/create\b"
    r"|(?<![0-9&])>{1,2}\s*[A-Za-z0-9_.$~/\\\"']", re.IGNORECASE)
# Reads of state named by the owner's definition of a status-reader turn.
STATUS_CMD_RE = re.compile(
    r"\bherdr\s+agent\s+(get|list|read)\b"
    r"|\bherdr\s+pane\s+read\b"
    r"|\bgit\s+status\b"
    r"|\bgit\s+log\b"
    r"|usage-probe\.py"
    r"|/usage\b"
    r"|\bschtasks\s+/query\b", re.IGNORECASE)

# A weekly meter that falls by more than this many points inside one window is
# a reset; anything smaller is the noise the status line writes every few
# seconds (values of 63, 66 and 65 within the same minute are normal).
RESET_DROP = 25

# Version of the `--json` payload. Bumped when a field changes meaning, never
# when one is added; `ledger-compare.py` reads it to say which side is stale.
JSON_SCHEMA = 2

LEDGER_HEADER = ("| Time | Window | Project | Tokens (in+out, k) | Quota points "
                 "| Merged features | Points per feature | Waste % "
                 "| forks / status / narration / probes / cache (k) |")
LEDGER_SEP = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
LEDGER_TITLE = "## Waste ledger, 08:00 and 18:00"


# --------------------------------------------------------------------------
# Window and time
# --------------------------------------------------------------------------
def parse_when(text, now):
    """Parse `24h`, `3d` or a local `YYYY-MM-DD HH:MM` into a naive local time."""
    if not text:
        return None
    m = re.fullmatch(r"(\d+)([dh])", text.strip())
    if m:
        n = int(m.group(1))
        delta = dt.timedelta(hours=n) if m.group(2) == "h" else dt.timedelta(days=n)
        return now - delta
    s = text.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise SystemExit("cannot parse a time from %r" % text)


def local_ts(iso):
    """Transcript timestamps are UTC with a Z; the window is local time."""
    if not iso:
        return None
    try:
        d = dt.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    if iso.endswith("Z") or "+00:00" in iso:
        d = d.replace(tzinfo=dt.timezone.utc).astimezone()
        return d.replace(tzinfo=None)
    return d


def project_of(folder):
    """Map a transcript folder name to a project, longest prefix wins."""
    low = folder.lower()
    best, best_len = "other", -1
    for prefix, name in PROJECT_PREFIXES.items():
        if low == prefix or low.startswith(prefix + "-"):
            if len(prefix) > best_len:
                best, best_len = name, len(prefix)
    return best


# --------------------------------------------------------------------------
# Transcript scan
# --------------------------------------------------------------------------
def iter_transcripts(projects_dir, start, max_mb, skipped):
    """Yield transcript files that can hold entries inside the window.

    Transcripts are append-only, so a file last modified before the window
    started cannot contain an in-window entry. Files above `max_mb` are skipped
    and named in the breakdown, never dropped silently.
    """
    if not os.path.isdir(projects_dir):
        return
    for folder in sorted(os.listdir(projects_dir)):
        root = os.path.join(projects_dir, folder)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in sorted(files):
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if dt.datetime.fromtimestamp(st.st_mtime) < start:
                    continue
                if st.st_size > max_mb * 1024 * 1024:
                    skipped.append((path, st.st_size))
                    continue
                parts = os.path.relpath(path, projects_dir).split(os.sep)
                if "subagents" in parts:
                    i = parts.index("subagents")
                    yield path, folder, parts[i - 1], "subagent"
                elif "workflows" in parts:
                    yield path, folder, parts[1], "subagent"
                else:
                    yield path, folder, name[:-6], "main"


def classify_wake(entry, tool_names):
    """Return what woke the turn that follows this user entry.

    Same classes as token-ledger-v2 (human, tool_result, task_notification,
    agent_message, system_meta, compaction, scheduled) plus `monitor`, for a
    turn woken only by the result of a Monitor watch.
    """
    if entry.get("isCompactSummary") or entry.get("type") == "summary":
        return "compaction"
    msg = entry.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        results = [b for b in content
                   if isinstance(b, dict) and b.get("type") == "tool_result"]
        if results:
            names = {tool_names.get(b.get("tool_use_id"), "?") for b in results}
            if names == {"Monitor"}:
                return "monitor"
            return "tool_result"
        content = " ".join(b.get("text", "") for b in content
                           if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        return "other"
    text = content.lstrip()
    head = text[:200]
    if text.startswith("<task-notification>"):
        return "task_notification"
    if text.startswith("This session is being continued"):
        return "compaction"
    if "<agent-message" in head or "<cross-session" in head or "<message from" in head:
        return "agent_message"
    if text.startswith("<scheduled") or text.startswith("<wakeup") or "ScheduleWakeup" in text[:120]:
        return "scheduled"
    if text.startswith("<system-reminder>") or entry.get("isMeta"):
        return "system_meta"
    return "human"


def scan_file(path, start, end):
    """Scan one transcript into deduplicated turns inside the window.

    One API response is written as several assistant lines sharing a requestId,
    one per content block; they are grouped so a turn carries all of its tool
    calls, and the usage is taken from the block that reports the most output.

    Three times come from the whole file and not from the window, because the
    compare places a session against the cap start and measures a lane from its
    own first prompt: `file_start` (first entry), `first_human` (first human
    prompt) and `last_ts` (last entry).
    """
    turns = collections.OrderedDict()
    tool_names = {}
    last_wake = None
    compactions = 0
    first_user_class = None
    file_start = None
    first_human = None
    last_ts = None
    empty = {"turns": [], "compactions": 0, "first_user_class": None,
             "file_start": None, "first_human": None, "last_ts": None}
    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        return empty
    with fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            kind = o.get("type")
            when = local_ts(o.get("timestamp") or "")
            if when is not None:
                if file_start is None or when < file_start:
                    file_start = when
                if last_ts is None or when > last_ts:
                    last_ts = when
            if kind == "user":
                cls = classify_wake(o, tool_names)
                if first_user_class is None:
                    first_user_class = cls
                if cls == "human" and first_human is None and when is not None:
                    first_human = when
                if cls == "compaction" and when and start <= when < end:
                    compactions += 1
                last_wake = cls
                continue
            if kind != "assistant":
                continue
            msg = o.get("message") or {}
            content = msg.get("content") or []
            key = msg.get("id") or o.get("requestId") or o.get("uuid")
            turn = turns.get(key)
            if turn is None:
                if when is None or not (start <= when < end):
                    # Still record tool ids so later tool_results resolve.
                    for b in content if isinstance(content, list) else []:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            tool_names[b.get("id")] = b.get("name")
                    continue
                turn = {"ts": when, "model": msg.get("model") or "?",
                        "tools": [], "cmds": [], "tokens": 0, "ctx": 0,
                        "out": 0, "in": 0, "cc": 0, "cr": 0,
                        "wake": last_wake or "unknown"}
                turns[key] = turn
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    name = b.get("name")
                    tool_names[b.get("id")] = name
                    turn["tools"].append(name)
                    if name in SHELL_TOOLS:
                        args = b.get("input") or {}
                        cmd = args.get("command") or args.get("cmd") or ""
                        if isinstance(cmd, str):
                            turn["cmds"].append(cmd)
            usage = msg.get("usage")
            if usage and usage.get("output_tokens", 0) >= turn["out"]:
                turn["in"] = usage.get("input_tokens", 0)
                turn["cc"] = usage.get("cache_creation_input_tokens", 0)
                turn["cr"] = usage.get("cache_read_input_tokens", 0)
                turn["ctx"] = turn["in"] + turn["cc"] + turn["cr"]
                turn["out"] = usage.get("output_tokens", 0)
                turn["tokens"] = turn["ctx"] + turn["out"]
    ordered = list(turns.values())
    if ordered:
        ordered[-1]["final"] = True
    return {"turns": ordered, "compactions": compactions,
            "first_user_class": first_user_class, "file_start": file_start,
            "first_human": first_human, "last_ts": last_ts}


# --------------------------------------------------------------------------
# Waste buckets
# --------------------------------------------------------------------------
def turn_is_productive(turn):
    """True when the turn writes, commits, launches an agent or sends a message."""
    if set(turn["tools"]) & PRODUCTIVE_TOOLS:
        return True
    return any(PRODUCTIVE_CMD_RE.search(c) for c in turn["cmds"])


def is_status_reader(turn):
    """A turn whose only tool calls read state and that changes nothing.

    The reads the owner named: `herdr agent get|list|read`, `herdr pane read`,
    `git status`, `git log` with no commit or merge in the same turn,
    `usage-probe.py` and `/usage`.
    """
    if not turn["tools"] or turn_is_productive(turn):
        return False
    if any(t not in SHELL_TOOLS for t in turn["tools"]):
        return False
    return bool(turn["cmds"]) and all(STATUS_CMD_RE.search(c) for c in turn["cmds"])


def is_narration(turn):
    """An assistant turn with no tool call at all, woken by a machine event.

    A turn with no tool call answering a human prompt is an answer, not
    narration; only turns woken by a tool result or a task notification count.
    The last turn of a transcript is the report the agent hands back, which is
    a message, so it is not narration either.
    """
    return (not turn["tools"] and not turn.get("final")
            and turn["wake"] in ("tool_result", "task_notification"))


def is_probe(turn):
    """A turn woken by a Monitor event or a scheduled wake that changed nothing."""
    return turn["wake"] in ("scheduled", "monitor") and not turn_is_productive(turn)


def bucket_of(turn):
    """One bucket per turn, in priority order, so nothing is counted twice."""
    if is_status_reader(turn):
        return "status"
    if is_probe(turn):
        return "probes"
    if is_narration(turn):
        return "narration"
    return None


# --------------------------------------------------------------------------
# Quota, merges, external tools
# --------------------------------------------------------------------------
def read_usage_log(path):
    """Read the status-line quota log. Both header formats are accepted."""
    rows = []
    if not os.path.exists(path):
        return rows, None
    with open(path, encoding="utf-8", errors="ignore", newline="") as fh:
        reader = csv.DictReader(fh)
        names = reader.fieldnames or []
        weekly = next((c for c in ("seven_day_used", "weekly_all", "scoped_meter")
                       if c in names), None)
        if not weekly:
            return rows, None
        for row in reader:
            when = None
            raw = (row.get("time") or "").strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
                try:
                    when = dt.datetime.strptime(raw[:len(fmt) + 2].strip(), fmt)
                    break
                except ValueError:
                    continue
            if when is None:
                continue
            try:
                value = float(row.get(weekly) or "")
            except ValueError:
                continue
            rows.append((when, value, (row.get("account") or "").strip()))
    rows.sort(key=lambda r: r[0])
    return rows, weekly


def points_climbed(rows, start, end):
    """Points the weekly meter climbed inside the window.

    End minus start, and when the meter falls by more than RESET_DROP the week
    reset, so the value after the drop is added whole. Smaller falls are the
    noise of a meter sampled every few seconds and add nothing.
    """
    window = [r for r in rows if start <= r[0] <= end]
    if len(window) < 2:
        return 0.0, len(window), []
    climbed = 0.0
    resets = []
    prev = window[0][1]
    for when, value, _acct in window[1:]:
        if value >= prev:
            climbed += value - prev
        elif prev - value > RESET_DROP:
            climbed += value
            resets.append((when, prev, value))
        prev = value
    return climbed, len(window), resets


def read_merges(repo, start, end, git_log_file, branch=None):
    """Merge commits on the main branch inside the window, features and others."""
    branch = branch or DEFAULT_BRANCH
    lines = []
    source = None
    if git_log_file:
        source = "file %s" % git_log_file
        with open(git_log_file, encoding="utf-8", errors="ignore") as fh:
            lines = [l.strip() for l in fh if l.strip()]
    elif repo and os.path.isdir(os.path.join(repo, ".git")):
        source = "git log %s %s" % (repo, branch)
        cmd = ["git", "-C", repo, "log", "--merges",
               "--since", start.strftime("%Y-%m-%d %H:%M:%S"),
               "--until", end.strftime("%Y-%m-%d %H:%M:%S"),
               "--format=%h|%ad|%s", "--date=iso", branch]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                 encoding="utf-8", errors="replace")
            lines = [l.strip() for l in (out.stdout or "").splitlines() if l.strip()]
        except (OSError, subprocess.SubprocessError) as exc:
            return [], [], "git failed: %s" % exc
    elif repo:
        return [], [], "no repository at %s" % repo
    else:
        return [], [], "no repository configured, set `repo` in ledger-config.json"
    features, others = [], []
    for line in lines:
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        subject = parts[2]
        entry = {"sha": parts[0], "date": parts[1], "subject": subject}
        if subject.startswith("Merge branch 'vc") or subject.startswith("Merge branch 'spec-"):
            features.append(entry)
        else:
            others.append(entry)
    return features, others, source


def run_analyzer(analyzer, start, notes):
    """Run the official session analyzer and return its parsed JSON, or None."""
    if not analyzer:
        notes.append("the session-report analyzer was not found under "
                     "~/.claude/plugins/marketplaces, so cache breaks are not "
                     "measurable")
        return None
    if not os.path.exists(analyzer):
        notes.append("analyzer not found at %s" % analyzer)
        return None
    if not shutil.which("node"):
        notes.append("node is not on PATH, the analyzer did not run")
        return None
    cmd = ["node", analyzer, "--json", "--since", start.strftime("%Y-%m-%dT%H:%M:%S")]
    try:
        # The transcripts carry every language, so the pipe is read as UTF-8;
        # the console locale (cp1252 here) would raise on the first prompt that
        # is not Latin-1 and hand back an empty stdout.
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                             encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        notes.append("analyzer failed: %s" % exc)
        return None
    if out.returncode != 0:
        notes.append("analyzer exited %d: %s" % (out.returncode, out.stderr[-200:]))
        return None
    try:
        return json.loads(out.stdout or "")
    except ValueError as exc:
        notes.append("analyzer output is not JSON: %s" % exc)
        return None


def run_token_ledger(session_paths, start, notes):
    """Run token-ledger-v2 per main session for the fork and compaction cross-check."""
    if not os.path.exists(TOKEN_LEDGER):
        notes.append("token-ledger-v2.py not found at %s" % TOKEN_LEDGER)
        return {}
    result = {}
    day = start.strftime("%Y-%m-%d")
    tmp = os.path.join(tempfile.gettempdir(), "ledger-tlv2.json")
    for path in session_paths:
        cmd = [sys.executable, TOKEN_LEDGER, path, "--since", day, "--json", tmp]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                                 encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError) as exc:
            notes.append("token-ledger-v2 failed on %s: %s" % (os.path.basename(path), exc))
            continue
        if out.returncode != 0 or not os.path.exists(tmp):
            notes.append("token-ledger-v2 exited %d on %s"
                         % (out.returncode, os.path.basename(path)))
            continue
        try:
            with open(tmp, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            notes.append("token-ledger-v2 json unreadable: %s" % exc)
            continue
        agents = data.get("agents") or []
        result[os.path.basename(path)[:8]] = {
            "agents": len(agents),
            "forks": sum(1 for a in agents if a.get("fork")),
            "compactions": (data.get("orchestrator") or {}).get("compactions", 0),
        }
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return result


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def k(n):
    return round(n / 1000.0, 1)


def fresh_of(turn):
    """Tokens that were not read from the cache: uncached input, cache write, output."""
    return turn["in"] + turn["cc"] + turn["out"]


MODEL_FAMILIES = (("fable", "fable"), ("opus", "opus"), ("sonnet", "sonnet"),
                  ("haiku", "haiku"), ("synthetic", "synthetic"))


def model_family(model):
    """Group a model id into the family the compare reports by."""
    low = (model or "").lower()
    for needle, name in MODEL_FAMILIES:
        if needle in low:
            return name
    return "other"


def percentile(values, p):
    """Nearest-rank percentile of a list, 0 when it is empty."""
    if not values:
        return 0
    s = sorted(values)
    i = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[i]


def sha256_of(path):
    """Hex digest of a file, or None when it cannot be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def session_out(entry):
    """One session for the payload: the context list becomes its percentiles."""
    ctx = entry.pop("main_ctx", [])
    out = dict(entry)
    out["main_turns"] = len(ctx)
    out["main_ctx_p50"] = percentile(ctx, 50)
    out["main_ctx_p90"] = percentile(ctx, 90)
    out["main_ctx_max"] = max(ctx) if ctx else 0
    return out


def build_report(args, start, end, now):
    """Collect every source and compute the per-project rows."""
    notes = []
    sources = []
    skipped = []

    # 1. transcripts, this script's own window-exact scan
    projects = collections.defaultdict(lambda: {
        "tokens": 0, "turns": 0, "sessions": {}, "folders": set(),
        "buckets": collections.Counter(), "bucket_turns": collections.Counter(),
        "forks": 0, "fork_tokens": 0, "cache_tokens": 0, "cache_breaks": 0,
        "compactions": 0, "fresh": 0, "by_model": {}, "main_ctx": [],
        "lanes": [], "kind_tokens": collections.Counter(),
        "kind_fresh": collections.Counter()})
    main_sessions = []
    for path, folder, session, kind in iter_transcripts(
            args.projects_dir, start, args.max_file_mb, skipped):
        scan = scan_file(path, start, end)
        turns = scan["turns"]
        if not turns:
            continue
        name = project_of(folder)
        proj = projects[name]
        proj["folders"].add(folder)
        tokens = sum(t["tokens"] for t in turns)
        fresh = sum(fresh_of(t) for t in turns)
        proj["tokens"] += tokens
        proj["fresh"] += fresh
        proj["turns"] += len(turns)
        proj["compactions"] += scan["compactions"]
        proj["kind_tokens"][kind] += tokens
        proj["kind_fresh"][kind] += fresh
        for turn in turns:
            fam = proj["by_model"].setdefault(model_family(turn["model"]), {
                "tokens": 0, "fresh": 0, "turns": 0, "main": 0, "subagent": 0})
            fam["tokens"] += turn["tokens"]
            fam["fresh"] += fresh_of(turn)
            fam["turns"] += 1
            fam[kind] += turn["tokens"]
        if kind == "main":
            proj["main_ctx"].extend(t["ctx"] for t in turns)
        entry = proj["sessions"].setdefault(session, {
            "id": session[:8], "project": name, "turns": 0, "tokens": 0,
            "ctx_peak": 0, "subagents": 0, "forks": 0, "fresh": 0,
            "started": None, "fork_tokens": 0, "main_ctx": [],
            "buckets": collections.Counter()})
        entry["turns"] += len(turns)
        entry["tokens"] += tokens
        entry["fresh"] += fresh
        entry["ctx_peak"] = max(entry["ctx_peak"], max(t["ctx"] for t in turns))
        if kind == "main":
            entry["main_ctx"].extend(t["ctx"] for t in turns)
        started = scan["file_start"]
        if started and (entry["started"] is None or started < entry["started"]):
            entry["started"] = started
        is_fork = False
        if kind == "subagent":
            entry["subagents"] += 1
            meta = path[:-6] + ".meta.json"
            meta_data = {}
            if os.path.exists(meta):
                try:
                    with open(meta, encoding="utf-8") as fh:
                        meta_data = json.load(fh) or {}
                    is_fork = bool(meta_data.get("isFork"))
                except (OSError, ValueError):
                    meta_data, is_fork = {}, False
            if not is_fork and scan["first_user_class"] == "compaction":
                is_fork = True
            if is_fork:
                entry["forks"] += 1
                entry["fork_tokens"] += tokens
                proj["forks"] += 1
                proj["fork_tokens"] += tokens
            ctxs = [t["ctx"] for t in turns]
            proj["lanes"].append({
                "id": os.path.basename(path)[:-6].replace("agent-", "")[:16],
                "session": session[:8], "type": meta_data.get("agentType") or "?",
                "desc": (meta_data.get("description") or "")[:80],
                "fork": is_fork, "turns": len(turns), "tokens": tokens,
                "fresh": fresh, "ctx_peak": max(ctxs),
                "ctx_p50": percentile(ctxs, 50),
                "compactions": scan["compactions"],
                "models": sorted({model_family(t["model"]) for t in turns}),
                "started": scan["file_start"], "first_human": scan["first_human"],
                "last_ts": scan["last_ts"],
            })
        else:
            main_sessions.append(path)
        if is_fork:
            continue  # fork tokens are already one bucket, never counted twice
        for turn in turns:
            bucket = bucket_of(turn)
            if bucket:
                proj["buckets"][bucket] += turn["tokens"]
                proj["bucket_turns"][bucket] += 1
                entry["buckets"][bucket] += turn["tokens"]
    sources.append("own transcript scan of %s (window-exact on both ends)"
                   % args.projects_dir)

    # 2. official analyzer, for by_project and cache_breaks
    analyzer = None
    if args.analyzer_json:
        try:
            with open(args.analyzer_json, encoding="utf-8") as fh:
                analyzer = json.load(fh)
            sources.append("analyzer JSON injected from %s" % args.analyzer_json)
        except (OSError, ValueError) as exc:
            notes.append("analyzer JSON unreadable: %s" % exc)
    elif args.no_analyzer:
        notes.append("analyzer skipped by --no-analyzer")
    else:
        analyzer = run_analyzer(args.analyzer, start, notes)
        if analyzer:
            sources.append("official analyzer %s --since %s"
                           % (os.path.basename(args.analyzer), start.isoformat()))
    analyzer_totals = {}
    cache_measurable = analyzer is not None
    if analyzer:
        for folder, stats in (analyzer.get("by_project") or {}).items():
            name = project_of(folder)
            tot = analyzer_totals.setdefault(name, {"tokens": 0, "calls": 0})
            tot["tokens"] += (stats.get("input_tokens", {}).get("total", 0)
                              + stats.get("output_tokens", 0))
            tot["calls"] += stats.get("api_calls", 0)
        for cb in analyzer.get("cache_breaks") or []:
            when = local_ts(cb.get("ts") or "")
            if when is None or not (start <= when < end):
                continue
            proj = projects[project_of(cb.get("project") or "")]
            proj["cache_tokens"] += cb.get("uncached", 0)
            proj["cache_breaks"] += 1

    # 3. token-ledger-v2 per main session, cross-check only
    cross = {}
    if args.no_token_ledger:
        notes.append("token-ledger-v2 skipped by --no-token-ledger")
    elif main_sessions:
        cross = run_token_ledger(main_sessions, start, notes)
        if cross:
            sources.append("token-ledger-v2.py on %d session(s)" % len(cross))

    # 4. quota
    rows, weekly_col = read_usage_log(args.usage_log)
    if weekly_col:
        sources.append("usage-log.csv column %s" % weekly_col)
    else:
        notes.append("usage-log.csv has no weekly column, quota points are 0")
    account_points, samples, resets = points_climbed(rows, start, end)
    accounts = sorted({r[2] for r in rows if start <= r[0] <= end and r[2]})

    # 5. merges
    features, other_merges, merge_source = read_merges(
        args.repo, start, end, args.git_log_file,
        getattr(args, "branch", DEFAULT_BRANCH))
    if merge_source:
        sources.append("merges from %s" % merge_source)

    # rows
    total_tokens = sum(p["tokens"] for p in projects.values()) or 1
    out_rows = []
    for name in PROJECT_ORDER + sorted(set(projects) - set(PROJECT_ORDER)):
        if name not in projects:
            continue
        proj = projects[name]
        if proj["turns"] == 0:
            continue
        share = proj["tokens"] / float(total_tokens)
        points = round(account_points * share, 1)
        waste = (proj["fork_tokens"] + proj["buckets"]["status"]
                 + proj["buckets"]["narration"] + proj["buckets"]["probes"]
                 + proj["cache_tokens"])
        waste_turns = (proj["fork_tokens"] + proj["buckets"]["status"]
                       + proj["buckets"]["narration"] + proj["buckets"]["probes"])
        n_features = len(features) if name == CONFIG_FEATURE_PROJECT else 0
        out_rows.append({
            "project": name,
            "folders": sorted(proj["folders"]),
            "tokens": proj["tokens"],
            "turns": proj["turns"],
            "points": points,
            "features": n_features,
            "points_per_feature": round(points / n_features, 2) if n_features else None,
            "waste_pct": round(100.0 * waste / proj["tokens"], 1) if proj["tokens"] else 0.0,
            "waste_pct_no_overlap": (round(100.0 * waste_turns / proj["tokens"], 1)
                                     if proj["tokens"] else 0.0),
            "forks": proj["forks"],
            "fork_tokens": proj["fork_tokens"],
            "buckets": dict(proj["buckets"]),
            "bucket_turns": dict(proj["bucket_turns"]),
            "cache_tokens": proj["cache_tokens"],
            "cache_breaks": proj["cache_breaks"],
            "compactions": proj["compactions"],
            "fresh": proj["fresh"],
            "by_model": proj["by_model"],
            "kind_tokens": dict(proj["kind_tokens"]),
            "kind_fresh": dict(proj["kind_fresh"]),
            "main_ctx_p50": percentile(proj["main_ctx"], 50),
            "main_ctx_p90": percentile(proj["main_ctx"], 90),
            "main_ctx_max": max(proj["main_ctx"]) if proj["main_ctx"] else 0,
            "lanes": sorted(proj["lanes"], key=lambda a: -a["tokens"]),
            "sessions": [session_out(s) for s in
                         sorted(proj["sessions"].values(),
                                key=lambda s: -s["tokens"])],
            "analyzer_tokens": analyzer_totals.get(name, {}).get("tokens"),
        })
    return {
        "schema": JSON_SCHEMA,
        "script_sha256": sha256_of(os.path.abspath(__file__)),
        "now": now, "start": start, "end": end, "rows": out_rows,
        "account_points": round(account_points, 1), "accounts": accounts,
        "quota_samples": samples, "resets": resets, "weekly_col": weekly_col,
        "features": features, "other_merges": other_merges,
        "sources": sources, "notes": notes, "skipped": skipped,
        "cross": cross, "cache_measurable": cache_measurable,
        "total_tokens": sum(p["tokens"] for p in projects.values()),
    }


def render_row(rep, row):
    """One markdown row of the ledger table."""
    ppf = "-" if row["points_per_feature"] is None else "%.2f" % row["points_per_feature"]
    cache = "n/m" if not rep["cache_measurable"] else "%.1f" % k(row["cache_tokens"])
    buckets = "%.1f / %.1f / %.1f / %.1f / %s" % (
        k(row["fork_tokens"]), k(row["buckets"].get("status", 0)),
        k(row["buckets"].get("narration", 0)), k(row["buckets"].get("probes", 0)),
        cache)
    return "| %s | %s to %s | %s | %.1f | %.1f | %d | %s | %.1f%% | %s |" % (
        rep["now"].strftime("%Y-%m-%d %H:%M"),
        rep["start"].strftime("%m-%d %H:%M"), rep["end"].strftime("%m-%d %H:%M"),
        row["project"], k(row["tokens"]), row["points"], row["features"],
        ppf, row["waste_pct"], buckets)


def append_ledger(path, rep):
    """Append the rows to ledger.md, adding this table below the existing one."""
    content = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
    parts = []
    if LEDGER_HEADER not in content:
        if content and not content.endswith("\n"):
            parts.append("\n")
        parts.append("\n" + LEDGER_TITLE + "\n\n")
        parts.append("Quota points are account-wide (the weekly meter of "
                     "`usage-log.csv`) split between projects by their share of "
                     "the window's tokens, because several projects can share "
                     "one account. "
                     "`n/m` means the bucket was not measurable, and the daily "
                     "file says why.\n\n")
        parts.append(LEDGER_HEADER + "\n" + LEDGER_SEP + "\n")
    for row in rep["rows"]:
        parts.append(render_row(rep, row) + "\n")
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(parts))


def render_daily(rep):
    """The full breakdown: rows, buckets, sessions, merges, quota, sources."""
    L = []
    add = L.append
    add("# Ledger %s" % rep["now"].strftime("%Y-%m-%d %H:%M"))
    add("")
    add("Window %s to %s (local time). Total measured %.1fk tokens over %d project(s)."
        % (rep["start"].isoformat(sep=" "), rep["end"].isoformat(sep=" "),
           k(rep["total_tokens"]), len(rep["rows"])))
    add("")
    add("## Sources that ran")
    add("")
    for source in rep["sources"]:
        add("- %s" % source)
    for note in rep["notes"]:
        add("- not run or partial: %s" % note)
    if rep["skipped"]:
        add("- transcripts skipped for size (counted nowhere, named here):")
        for path, size in rep["skipped"]:
            add("  - %s (%.0f MB)" % (path, size / 1048576.0))
    add("")
    add("## Rows")
    add("")
    add(LEDGER_HEADER)
    add(LEDGER_SEP)
    for row in rep["rows"]:
        add(render_row(rep, row))
    add("")
    add("## Per project")
    add("")
    for row in rep["rows"]:
        add("### %s" % row["project"])
        add("")
        add("Transcript folders: %s." % ", ".join(row["folders"]))
        add("Turns %d, tokens %.1fk, compaction summaries %d."
            % (row["turns"], k(row["tokens"]), row["compactions"]))
        if row["analyzer_tokens"] is not None:
            delta = row["analyzer_tokens"] - row["tokens"]
            add("Official analyzer for the same folders: %.1fk tokens "
                "(delta %+.1fk; the analyzer has no window end, so it also "
                "counts what ran between the window end and this run)."
                % (k(row["analyzer_tokens"]), k(delta)))
        add("")
        add("Waste buckets, each with its tokens and its turns:")
        add("")
        add("- forks: %.1fk over %d fork transcript(s)"
            % (k(row["fork_tokens"]), row["forks"]))
        for key, label in (("status", "status readers"),
                           ("narration", "narration turns"),
                           ("probes", "probes")):
            add("- %s: %.1fk over %d turn(s)"
                % (label, k(row["buckets"].get(key, 0)),
                   row["bucket_turns"].get(key, 0)))
        if rep["cache_measurable"]:
            add("- cache breaks: %.1fk over %d break(s) above 100k, from the "
                "analyzer's top 100 by size, so this is a floor"
                % (k(row["cache_tokens"]), row["cache_breaks"]))
        else:
            add("- cache breaks: not measurable: the official analyzer did not "
                "run, and it is the only source of the cache-break list")
        add("")
        add("Waste share %.1f%% as defined (the five buckets over the project's "
            "tokens). Without the cache-break overlap, since those tokens are "
            "part of the turns they belong to, it is %.1f%%."
            % (row["waste_pct"], row["waste_pct_no_overlap"]))
        add("")
        add("| Session | Project | Turns | Tokens (k) | Peak context (k) | Subagents | Forks |")
        add("| --- | --- | --- | --- | --- | --- | --- |")
        for s in row["sessions"]:
            add("| %s | %s | %d | %.1f | %.1f | %d | %d |"
                % (s["id"], s["project"], s["turns"], k(s["tokens"]),
                   k(s["ctx_peak"]), s["subagents"], s["forks"]))
        add("")
    add("## Quota")
    add("")
    add("Weekly meter column `%s`, %d sample(s) inside the window, account(s) %s. "
        "The account climbed %.1f point(s)."
        % (rep["weekly_col"] or "none", rep["quota_samples"],
           ", ".join(rep["accounts"]) or "none", rep["account_points"]))
    for when, before, after in rep["resets"]:
        add("- reset detected at %s: the meter fell from %.0f to %.0f, so %.0f "
            "point(s) were counted after it."
            % (when.isoformat(sep=" "), before, after, after))
    add("")
    add("## Merges")
    add("")
    if rep["features"]:
        add("Counted as features (%d):" % len(rep["features"]))
        for m in rep["features"]:
            add("- %s %s %s" % (m["sha"], m["date"], m["subject"]))
    else:
        add("No feature merge inside the window.")
    if rep["other_merges"]:
        add("")
        add("Other merges, listed and not counted (%d):" % len(rep["other_merges"]))
        for m in rep["other_merges"]:
            add("- %s %s %s" % (m["sha"], m["date"], m["subject"]))
    if rep["cross"]:
        add("")
        add("## token-ledger-v2 cross-check")
        add("")
        for sid, data in sorted(rep["cross"].items()):
            add("- %s: %d subagent(s), %d fork(s), %d compaction summary(ies)"
                % (sid, data["agents"], data["forks"], data["compactions"]))
    add("")
    return "\n".join(L) + "\n"


def main(argv=None):
    now = dt.datetime.now().replace(microsecond=0)
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--since", default="24h")
    parser.add_argument("--until", default=None)
    parser.add_argument("--ledger-dir", default=DEFAULT_LEDGER_DIR)
    parser.add_argument("--projects-dir", default=DEFAULT_PROJECTS_DIR)
    parser.add_argument("--usage-log", default=None)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--git-log-file", default=None)
    parser.add_argument("--analyzer", default=DEFAULT_ANALYZER)
    parser.add_argument("--analyzer-json", default=None)
    parser.add_argument("--no-analyzer", action="store_true")
    parser.add_argument("--no-token-ledger", action="store_true")
    parser.add_argument("--max-file-mb", type=int, default=512)
    parser.add_argument("--json", dest="json_out", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.usage_log is None:
        args.usage_log = os.path.join(args.ledger_dir, "usage-log.csv")

    start = parse_when(args.since, now)
    end = parse_when(args.until, now) if args.until else now
    if start >= end:
        raise SystemExit("the window starts after it ends: %s to %s" % (start, end))

    rep = build_report(args, start, end, now)
    daily = render_daily(rep)
    lines = [render_row(rep, row) for row in rep["rows"]]

    if not args.dry_run:
        os.makedirs(os.path.join(args.ledger_dir, "daily"), exist_ok=True)
        daily_path = os.path.join(args.ledger_dir, "daily",
                                  now.strftime("%Y-%m-%d-%H%M") + ".md")
        with open(daily_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(daily)
        append_ledger(os.path.join(args.ledger_dir, "ledger.md"), rep)
        print("daily breakdown: %s" % daily_path)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rep, fh, indent=1, default=str)
    for line in lines:
        print(line)
    if not lines:
        print("no project had activity between %s and %s" % (start, end))
    for note in rep["notes"]:
        print("note: %s" % note, file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    sys.exit(main())
