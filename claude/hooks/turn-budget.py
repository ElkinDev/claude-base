"""PreToolUse hook for every tool: keep a subagent's turn budget in sight.

An agent with `maxTurns` in its definition stops dead at that count and returns only what it
already wrote. The definitions ask for a checkpoint report at 70 percent, but an agent sees no
counter, so on a busy project it read until the limit and handed back nothing.

Inside a subagent (the harness passes `agent_id` and `agent_type`) every tool call the agent
starts adds one empty file to `$CLAUDE_LEDGER_DIR/turn-budget/<agent_id>/`, so the file count is
the call count even when calls run in parallel (Windows has no atomic append). PreToolUse sees
every call that passes the harness's input validation: a call that then fails, is refused by
another hook or is denied a permission fires no PostToolUse, yet it spends a turn. A call
rejected before that (an unknown tool, a missing parameter) fires no hook at all and is not
counted. When the count first reaches 70
and then 90 percent of the definition's `maxTurns` (found as `<cwd>/.claude/agents/<type>.md`,
then `~/.claude/agents/<type>.md`), the hook adds one factual line to the agent's context through
`additionalContext`. A turn holds one or more calls, so unless calls are rejected for bad input
the count is never behind the turns and the note comes at or before the turn it names. In the main session, and for an agent whose
definition has no `maxTurns` or cannot be found, it does nothing and writes nothing.

Known limit: a finished agent resumed by a message keeps its agent_id, so its count carries on
from the first run. If the harness gives the resumed run a fresh `maxTurns`, its notes come early
or not at all.

It never blocks and never decides: every failure exits 0 with no output.
"""
import json
import math
import os
import re
import shutil
import sys
import time

BUDGET_DIR = "turn-budget"
MARKS = (70, 90)  # percent of maxTurns at which the agent gets one note each
KEEP_SECONDS = 2 * 86400  # call folders idle this long are pruned when a new agent starts
MAX_TURNS_LINE = re.compile(r"^maxTurns:\s*(\d+)\s*$")
SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]")  # no dots, so no name can be "." or ".."


def ledger_dir():
    return os.environ.get("CLAUDE_LEDGER_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "ledger")


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
                    and now - entry.stat().st_mtime > KEEP_SECONDS:
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
    # One empty file per call, named by the process and the clock: a shared counter loses calls
    # that run in parallel, while a new file per call never collides. The count read after this
    # call's file exists is at least this call's number.
    open(os.path.join(calls, "%d-%d" % (os.getpid(), time.time_ns())), "x").close()
    count = sum(1 for name in os.listdir(calls) if not name.startswith("mark-"))
    for mark in sorted(MARKS, reverse=True):
        if count < math.ceil(limit * mark / 100.0):
            continue
        try:
            # Created once per agent and mark, exclusively, so parallel calls give one note.
            open(os.path.join(calls, "mark-%d" % mark), "x").close()
        except OSError:
            return None
        return ("Turn budget, %d percent: this %s run has started %d tool calls; its limit is %d turns "
                "(maxTurns in %s), and a turn holds one or more calls. At the limit the run ends and "
                "returns only what it has already written or reported."
                % (mark, agent_type, count, limit, source))
    return None


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        note = budget_note(data)
        if note:
            out = {"hookSpecificOutput": {"hookEventName": data.get("hook_event_name") or "PreToolUse",
                                          "additionalContext": note}}
            sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
