"""PostToolUse hook for every tool: measure the result and shout when it is huge.

It cannot change anything, it counts. Every tool result adds a row to
$CLAUDE_LEDGER_DIR/tool-sizes.csv (time, session, tool, characters), so the ledger can
later rank which tools fill the window. Above the alarm threshold it also fires a herdr
notification, when herdr is on PATH, and returns a one-line systemMessage naming the tool
and the size, so the offender is visible while it happens.

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
CSV_HEADER = ("time", "session_id", "tool_name", "chars")


def ledger_path():
    directory = os.environ.get("CLAUDE_LEDGER_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "ledger"
    )
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, CSV_NAME)


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
        try:
            append_row(ledger_path(), [datetime.now().isoformat(timespec="seconds"), session, tool, chars])
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
