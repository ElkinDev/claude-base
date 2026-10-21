"""PostToolUse hook for every tool: measure the result and shout when it is huge.

It cannot change anything, it counts. Every tool result adds a row to
$CLAUDE_LEDGER_DIR/tool-sizes.csv (time, session, tool, characters, agent), so the ledger can
later rank which tools fill the window, and whose window they filled: a session's own turn loop
and each of its subagents pay separately, and a total that mixes them hides which one to brief.
Above the alarm threshold it returns a one-line systemMessage naming the tool and the size, so
the offender is visible in the session while it happens. A herdr desktop notification is fired as
well only when BIG_RESULT_NOTIFY=1 and herdr is on PATH. By default it is off: on a busy machine
it fired over a thousand desktop toasts a day, mostly for Read, and nobody acted on them.

The agent column was added after the four-column file already existed on every machine, so a
file that has a header keeps it: rows written from here carry five fields whatever the header
says, and every reader of this file reads both shapes.

It also keeps each subagent's turn budget in sight. An agent with `maxTurns` in its definition
stops dead at that count and returns only what it already wrote; the definitions ask for a
checkpoint report at 70 percent, but an agent sees no counter, so in practice it read until the
limit and handed back nothing. Inside a subagent (the harness passes `agent_id` and `agent_type`)
every tool call adds one empty file to `$CLAUDE_LEDGER_DIR/turn-budget/<agent_id>/`, so the file
count is the call count even when calls run in parallel (Windows has no atomic append). When the count first reaches 70 and then 90
percent of the definition's `maxTurns` (found as `<cwd>/.claude/agents/<type>.md`, then
`~/.claude/agents/<type>.md`), the hook adds one factual line to the agent's context through
`additionalContext`. A turn can hold several calls, so the count runs ahead of the turns and the
note comes early, never late. An agent with no `maxTurns` in a file it can find gets no note.
This runs in the same process as the size row, so it costs no extra start per tool call.

It never blocks: every failure exits 0.
"""
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

ALARM_CHARS = 50000
CSV_NAME = "tool-sizes.csv"
CSV_HEADER = ("time", "session_id", "tool_name", "chars", "agent")
NOTIFY_ENV = "BIG_RESULT_NOTIFY"
SUBAGENTS_SEGMENT = "/subagents/"
AGENT_PREFIX = "agent-"
TRANSCRIPT_SUFFIX = ".jsonl"
BUDGET_DIR = "turn-budget"
BUDGET_MARKS = (70, 90)  # percent of maxTurns at which the agent gets one note each
BUDGET_KEEP_SECONDS = 2 * 86400  # counter files older than this are pruned when a new agent starts
MAX_TURNS_LINE = re.compile(r"^maxTurns:\s*(\d+)\s*$")
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def ledger_dir():
    directory = os.environ.get("CLAUDE_LEDGER_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "ledger"
    )
    os.makedirs(directory, exist_ok=True)
    return directory


def ledger_path():
    return os.path.join(ledger_dir(), CSV_NAME)


def max_turns(agent_type, cwd):
    """maxTurns from the agent's definition, project folder first, then the user's; None when absent.

    Only the frontmatter is read (between the first two `---` lines), so a body line that happens
    to start with `maxTurns:` never counts.
    """
    name = SAFE_NAME.sub("", str(agent_type or ""))
    if not name:
        return None, ""
    places = []
    if cwd:
        places.append(os.path.join(cwd, ".claude", "agents", name + ".md"))
    places.append(os.path.join(os.path.expanduser("~"), ".claude", "agents", name + ".md"))
    for path in places:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            continue
        if not lines or lines[0].strip() != "---":
            continue
        for line in lines[1:]:
            if line.strip() == "---":
                break
            match = MAX_TURNS_LINE.match(line.strip())
            if match:
                value = int(match.group(1))
                return (value if value > 0 else None), path
        return None, path
    return None, ""


def is_junction(path):
    check = getattr(os.path, "isjunction", None)  # Python 3.12 and later
    return bool(check and check(path))


def prune(folder, keep):
    """Remove the call folders of agents idle for two days; never a link, never the current one."""
    now = time.time()
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for entry in entries:
        try:
            if os.path.normcase(entry.path) == os.path.normcase(keep) or entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False) and not is_junction(entry.path) \
                    and now - entry.stat().st_mtime > BUDGET_KEEP_SECONDS:
                shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            continue


def budget_note(data):
    """Count this subagent's tool call and return the note due at this count, or None."""
    agent_id = SAFE_NAME.sub("", str(data.get("agent_id") or ""))
    agent_type = data.get("agent_type")
    if not agent_id or not agent_type:
        return None
    limit, source = max_turns(agent_type, data.get("cwd"))
    if not limit:
        return None
    folder = os.path.join(ledger_dir(), BUDGET_DIR)
    calls = os.path.join(folder, agent_id)
    fresh = not os.path.isdir(calls)
    os.makedirs(calls, exist_ok=True)
    if fresh:
        prune(folder, keep=calls)
    # One empty file per call, named by the process and the clock: Windows has no atomic append, so
    # a shared counter loses calls that run in parallel, while a new file per call never collides.
    # The count read after this call's file exists is at least this call's number.
    open(os.path.join(calls, "%d-%d" % (os.getpid(), time.time_ns())), "x").close()
    count = sum(1 for name in os.listdir(calls) if not name.startswith("mark-"))
    for mark in sorted(BUDGET_MARKS, reverse=True):
        threshold = math.ceil(limit * mark / 100.0)
        if count < threshold:
            continue
        try:
            # Created once per agent and mark, exclusively, so parallel calls give one note.
            open(os.path.join(calls, "mark-%d" % mark), "x").close()
        except OSError:
            return None
        return ("Turn budget, %d percent: this %s run has made %d tool calls; its limit is %d turns "
                "(maxTurns in %s), and a turn holds one or more calls. At the limit the run ends and "
                "returns only what it has already written or reported."
                % (mark, agent_type, count, limit, source))
    return None


def agent_column(transcript_path):
    """Whose window this result landed in: `main`, a subagent id, or empty when unknown.

    The harness writes a subagent's transcript under a `subagents` directory as
    `agent-<id>.jsonl`; a path without that segment belongs to the session's own turn loop.
    An absent field is left empty rather than guessed, so a reader can tell "not a subagent"
    from "the harness did not say".
    """
    path = str(transcript_path or "").replace("\\", "/")
    if not path:
        return ""
    if SUBAGENTS_SEGMENT not in path:
        return "main"
    name = path.rsplit("/", 1)[-1]
    if name.lower().endswith(TRANSCRIPT_SUFFIX):
        name = name[:-len(TRANSCRIPT_SUFFIX)]
    if name.startswith(AGENT_PREFIX):
        name = name[len(AGENT_PREFIX):]
    return name


def response_size(response):
    if response is None:
        return 0
    if isinstance(response, str):
        return len(response)
    try:
        return len(json.dumps(response, ensure_ascii=False))
    except Exception:
        return len(str(response))


def append_row(path, row):
    """Append one row, writing the header only into a file that does not have one yet.

    A file already carrying the four-column header keeps it: rewriting a header in place would
    mean rewriting the whole file under a hook that must never be slow and never fail loudly.
    """
    exists = os.path.isfile(path) and os.path.getsize(path) > 0
    with open(path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        if not exists:
            writer.writerow(CSV_HEADER)
        writer.writerow(row)


def notify(message):
    herdr = shutil.which("herdr")
    if not herdr:
        return
    try:
        subprocess.run(
            [herdr, "notification", "show", message],
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return 0
    try:
        tool = data.get("tool_name") or "unknown"
        session = data.get("session_id") or ""
        chars = response_size(data.get("tool_response"))
        agent = agent_column(data.get("transcript_path"))
        try:
            append_row(ledger_path(),
                       [datetime.now().isoformat(timespec="seconds"), session, tool, chars, agent])
        except Exception:
            pass
        out = {}
        if chars > ALARM_CHARS:
            if os.environ.get(NOTIFY_ENV) == "1":
                notify("%s returned %d chars" % (tool, chars))
            out["systemMessage"] = (
                "%s returned %d characters, above the %d alarm. Narrow the next "
                "call (a filter, a range, a count) instead of asking for the whole thing again."
                % (tool, chars, ALARM_CHARS))
        try:
            note = budget_note(data)
        except Exception:
            note = None
        if note:
            out["hookSpecificOutput"] = {"hookEventName": "PostToolUse", "additionalContext": note}
        if out:
            sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
