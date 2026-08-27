"""SessionEnd hook: run the closure audit and leave it on disk, at no model cost.

When the audit script named by CLAUDE_SESSION_AUDIT exists it is run on the
session transcript with
--hours 24 and its stdout is written to

    <CLAUDE_LEDGER_DIR>/session-audits/<yyyy-mm-dd>-<session first 8>.md

(default ledger directory ~/.claude/ledger). The audit reports what a
closing session would lose: the last human prompt, tool calls after it, files
written, crons left armed, queued task notifications and dirty worktrees. Without
the script the hook exits 0 in silence. Nothing here can block a session from
ending: every path returns 0, and the subprocess is bounded well under the 60
second timeout declared in settings.json.

SessionEnd fields, verified in the shipped CLI: the common base (session_id,
transcript_path, cwd) plus reason.
"""
import os
import subprocess
import sys
from datetime import datetime

DEFAULT_AUDIT = ""  # set CLAUDE_SESSION_AUDIT to the audit script
DEFAULT_LEDGER = os.path.join(
    os.path.expanduser("~"), ".claude", "ledger"
)  # override with CLAUDE_LEDGER_DIR
RUN_TIMEOUT_SECONDS = 45


def main():
    try:
        import json

        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return 0
    try:
        if not isinstance(data, dict):
            return 0
        transcript = data.get("transcript_path") or ""
        if not transcript or not os.path.isfile(transcript):
            return 0

        audit = os.environ.get("CLAUDE_SESSION_AUDIT") or DEFAULT_AUDIT
        if not os.path.isfile(audit):
            return 0

        done = subprocess.run(
            [sys.executable, audit, transcript, "--hours", "24"],
            capture_output=True,
            timeout=RUN_TIMEOUT_SECONDS,
        )
        report = done.stdout.decode("utf-8", "replace")
        if not report.strip():
            return 0

        ledger = os.environ.get("CLAUDE_LEDGER_DIR") or DEFAULT_LEDGER
        folder = os.path.join(ledger, "session-audits")
        os.makedirs(folder, exist_ok=True)
        session = (data.get("session_id") or "unknown")[:8]
        name = "%s-%s.md" % (datetime.now().strftime("%Y-%m-%d"), session)
        with open(os.path.join(folder, name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(report if report.endswith("\n") else report + "\n")
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
