"""UserPromptSubmit hook, plus a --recover mode for SessionStart (matcher: compact). Appends
every prompt a person types to <checkpoints>/<session8>-prompts.md the moment it is submitted,
and reads the last ones back, verbatim, right after a compaction. A compaction summary
paraphrases what was asked; this file does not, so an order given at the start of a session is
still readable at the end of it.

stdin: the hook JSON (session_id, transcript_path, cwd, prompt; agent_id and agent_type when a
subagent is the one submitting). Append mode prints nothing at all for most of the day: on
UserPromptSubmit whatever the hook writes to stdout is added to the model's context, so a hook
whose job is to write a file has no business paying for a line in the window. The exception is
the closing round of a seated session, one line saying the day ends and what it owes before it
does. --recover prints the block, capped, since there its stdout is the point.

Never blocks a prompt: every failure exits 0 in silence. Slash commands, the harness tags
(task notifications, system reminders, local command output) and a subagent's prompt are not
what the owner typed, so they are skipped.
"""
import json
import os
import sys
from datetime import datetime

ENTRY_CAP = 1500
ENTRY_MARKER = " [cut]"
FILE_CAP = 200_000
BLOCK_CAP = 6000
KEEP = 10
SKIP_PREFIXES = ("/", "<task-notification>", "<system-reminder>", "<local-command")
HEADER = "Last typed prompts of this session (owner or the analyst relay), verbatim, file "


def checkpoint_dir(transcript_path, cwd):
    """The folder compact-recover.py and the checkpoint hook use, resolved the same way."""
    configured = os.environ.get("CLAUDE_CHECKPOINT_DIR")
    if configured:
        return configured
    if transcript_path:
        project = os.path.basename(os.path.dirname(transcript_path)) or "default"
    else:
        project = "".join(ch if ch.isalnum() else "-" for ch in cwd) or "default"
    return os.path.join(os.path.expanduser("~"), ".claude", "checkpoints", project)


def log_path(data):
    """The prompt file for this session, or "" when the payload does not name a session."""
    session_id = str(data.get("session_id") or "")
    if not session_id:
        return ""
    cwd = str(data.get("cwd") or os.getcwd())
    folder = checkpoint_dir(str(data.get("transcript_path") or ""), cwd)
    return os.path.join(folder, f"{session_id[:8]}-prompts.md")


def entry_text(prompt):
    """One line of text for the prompt, or "" when the prompt is not one a person typed."""
    text = " ".join(str(prompt or "").split())
    if not text:
        return ""
    if text.startswith(SKIP_PREFIXES):
        return ""
    if len(text) > ENTRY_CAP:
        text = text[:ENTRY_CAP - len(ENTRY_MARKER)] + ENTRY_MARKER
    return text


def ends_with_newline(path):
    """True when the file is absent, empty or already ends with a line break."""
    try:
        size = os.path.getsize(path)
        if size == 0:
            return True
        with open(path, "rb") as handle:
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) == b"\n"
    except Exception:
        return True


def trim(path):
    """Keep the last FILE_CAP bytes of the file, whole lines only.

    The tail starts mid line, so everything up to the first newline goes and what is kept is
    whole lines only, the freshly appended one always among them: append never glues an entry to
    a file that does not end with a newline, so the last line of the tail is always a whole entry
    and cutting at that first newline can never leave a ragged fragment behind. The one shape
    that cannot be cut, a tail whose only newline is its last byte or that has none at all, is
    kept as it stands rather than emptied, which is a file that no longer comes from this hook.
    The rewrite goes through a temporary file and os.replace, so a process killed mid-trim leaves
    the old file, not a truncated one.
    """
    try:
        if os.path.getsize(path) <= FILE_CAP:
            return
        with open(path, "rb") as handle:
            handle.seek(-FILE_CAP, os.SEEK_END)
            data = handle.read()
        cut = data.find(b"\n")
        if 0 <= cut < len(data) - 1:
            data = data[cut + 1:]
        temp = path + ".trim"
        with open(temp, "wb") as handle:
            handle.write(data)
        os.replace(temp, path)
    except Exception:
        pass


SEATS = ("orchestrator", "analyst")
MINUTES_IN_DAY = 24 * 60
CLOSING_WINDOW = 45
# The round does not shut at the hour, because a session still typing past it is what the line
# exists for, but it does end: without a tail, a window that wraps midnight would cover the day.
CLOSING_TAIL = 120
NOTICE = ("Closing round: the day ends at %s. Write the resume brief, land or stop every agent, "
          "nothing running at the hour.")


def clock(value):
    """`HH:MM` as minutes since midnight, or None when the text is not a time of day."""
    parts = str(value or "").strip().split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    hours, minutes = int(parts[0]), int(parts[1])
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def closing_notice(data):
    """The one line a seated session is told once the closing round has opened.

    The round opens CLOSING_WINDOW minutes before CLAUDE_CLOSING_HOUR and stays open CLOSING_TAIL
    minutes past it, because a session still typing after the hour is the case the line exists
    for. The arithmetic wraps the day, so a closing hour after midnight gets the same window as
    any other instead of a round that opens at 23:45 and never closes.

    A lane has no day to close and a subagent holds no chair, so neither is told anything, and a
    session without the variable is a session on a machine that never set a closing hour. A
    prompt the log itself refuses, a slash command or a harness tag, is not a person asking for
    something either, and the notice is an answer to a person.

    CLAUDE_TEST_NOW replaces the clock, so the cases around the edge of the window are exact
    rather than a suite that has to be run at nine in the evening.
    """
    if data.get("agent_id") or data.get("agent_type"):
        return ""
    if not entry_text(data.get("prompt")):
        return ""
    if (os.environ.get("CLAUDE_ROLE") or "").strip().lower() not in SEATS:
        return ""
    hour = (os.environ.get("CLAUDE_CLOSING_HOUR") or "").strip()
    end = clock(hour)
    if end is None:
        return ""
    now = clock(os.environ.get("CLAUDE_TEST_NOW"))
    if now is None:
        now = clock(datetime.now().strftime("%H:%M"))
    since = (now - end) % MINUTES_IN_DAY
    if CLOSING_TAIL < since < MINUTES_IN_DAY - CLOSING_WINDOW:
        return ""
    return NOTICE % hour


def append(data):
    text = entry_text(data.get("prompt"))
    if not text or data.get("agent_id"):
        return 0
    path = log_path(data)
    if not path:
        return 0
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        # A log left without its final newline, by a kill mid-write or by a hand that edited it,
        # would swallow this entry into its last line. The separator keeps every entry a line.
        lead = "" if ends_with_newline(path) else "\n"
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{lead}- {stamp} {text}\n")
        trim(path)
    except Exception:
        pass
    return 0


def recover(data):
    path = log_path(data)
    if not path:
        return 0
    if not os.path.isfile(path):
        write(f"No prompt log for this session at {path}.")
        return 0
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            entries = [line for line in handle.read().split("\n") if line.strip()]
    except Exception:
        return 0
    if not entries:
        write(f"No prompt log for this session at {path}.")
        return 0
    entries = entries[-KEEP:]
    head = HEADER + path + ":"
    # Whole entries only: the oldest goes first, and nothing is printed cut in half.
    while entries and len("\n".join([head] + entries)) > BLOCK_CAP:
        entries.pop(0)
    write("\n".join([head] + entries) if entries else head)
    return 0


def write(text):
    sys.stdout.buffer.write(text.encode("utf-8"))


def main(argv):
    try:
        # utf-8-sig, not utf-8: PowerShell 5.1 puts a BOM in front of anything it pipes to a
        # native command, and a BOM left in the text makes json.loads raise on a good payload.
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig", "replace") or "{}")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if "--recover" in argv:
        return recover(data)
    code = append(data)
    notice = closing_notice(data)
    if notice:
        write(notice)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        sys.exit(0)
