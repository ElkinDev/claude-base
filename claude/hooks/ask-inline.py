"""ask-inline.py: PreToolUse hook on AskUserQuestion. It denies the question selector and tells the model to ask in
the reply text instead.

Why: the selector parks the session until the owner comes back to the terminal, shows one question at a time, and
carries no deadline, so a reversible default waits as long as a real decision. Asked in the reply, every question
is read whole with its data, its numbered options and the recommended one, and a reversible one names the time its
recommended option runs if no answer comes, so the work goes on. The rule itself lives in CLAUDE.md; this hook is
what keeps a session from slipping back to the selector.

Output: the PreToolUse deny decision with the reason the model reads. Log (ask-inline.log beside this hook, or
ASK_INLINE_LOG): one line per denial, `<local time> <session prefix> questions=<n> <cwd>`, never the question text.
The line count per day is the number the rule is read by: it falls to zero once every project's sessions ask inline.

ASK_INLINE_ALLOW=1 lets the selector through (a project that wants it sets the variable in its own settings). Any
error lets the call through with nothing printed: the hook fails open.
"""
import json
import os
import sys
from datetime import datetime

HOOKS = os.path.dirname(os.path.abspath(__file__))
LOG = os.environ.get("ASK_INLINE_LOG") or os.path.join(HOOKS, "ask-inline.log")

REASON = (
    "Ask in your reply text, not in the question selector (house rule in CLAUDE.md). Write each question whole, in "
    "the owner's language: its data, numbered options, the recommended one marked, and its class. A reversible "
    "default (an operational or copy choice, or one a ruling or precedent covers) says the time at which the "
    "recommended option runs if no answer comes, veto open, and runs it then; the owner's own words, money, legal "
    "exposure or design say the decision is the owner's and wait for the answer. For a reversible one, arm one "
    "bounded wait for its time before you end your turn, and run the recommended option then if no answer came."
)


def log(payload):
    """Called after the deny is printed, and never raises: an odd payload or a log that cannot be written costs the
    line, never the deny."""
    try:
        tool_input = payload.get("tool_input")
        questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
        line = "%s %s questions=%s %s\n" % (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            str(payload.get("session_id") or "-")[:8],
            len(questions) if isinstance(questions, list) else "-",
            payload.get("cwd") or "-",
        )
        with open(LOG, "a", encoding="utf-8", errors="replace") as h:
            h.write(line)
    except Exception:
        pass


def main():
    if os.environ.get("ASK_INLINE_ALLOW") == "1":
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "AskUserQuestion":
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": REASON}}))
    sys.stdout.flush()
    log(payload)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # fail open: a broken hook must never stop the session
        sys.exit(0)
