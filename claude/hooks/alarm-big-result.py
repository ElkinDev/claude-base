"""PostToolUse hook for every tool: measure the result and shout when it is huge.

It cannot change anything, it counts. Every tool result adds a row to
$CLAUDE_LEDGER_DIR/tool-sizes.csv (time, session, tool, characters, agent), so the ledger can
later rank which tools fill the window, and whose window they filled: a session's own turn loop
and each of its subagents pay separately, and a total that mixes them hides which one to brief.
Above the alarm threshold it also fires a herdr notification, when herdr is on PATH, and returns
a one-line systemMessage naming the tool and the size, so the offender is visible while it
happens.

The agent column was added after the four-column file already existed on every machine, so a
file that has a header keeps it: rows written from here carry five fields whatever the header
says, and every reader of this file reads both shapes.

It never blocks: every failure exits 0.
"""
import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

ALARM_CHARS = 50000
CSV_NAME = "tool-sizes.csv"
CSV_HEADER = ("time", "session_id", "tool_name", "chars", "agent")
SUBAGENTS_SEGMENT = "/subagents/"
AGENT_PREFIX = "agent-"
TRANSCRIPT_SUFFIX = ".jsonl"


def ledger_path():
    directory = os.environ.get("CLAUDE_LEDGER_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "ledger"
    )
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, CSV_NAME)


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
        if chars > ALARM_CHARS:
            message = "%s returned %d chars" % (tool, chars)
            notify(message)
            out = {
                "systemMessage": "%s returned %d characters, above the %d alarm. Narrow the next "
                "call (a filter, a range, a count) instead of asking for the whole thing again."
                % (tool, chars, ALARM_CHARS)
            }
            sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
