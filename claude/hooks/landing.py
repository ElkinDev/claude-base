"""Landing hook: every finished unit of work writes one line, and no model reads it.

Wired on three events:

  SubagentStop   a background agent stopped. Fields verified in the shipped CLI:
                 stop_hook_active, agent_id, agent_transcript_path, agent_type,
                 last_assistant_message (optional), over the common base
                 session_id, transcript_path, cwd. Only agents with a type land;
                 see the note in main() for why, and CLAUDE_LANDINGS_ALL to undo it.
  TaskCompleted  a task finished. Fields: task_id, task_subject, task_description
                 (optional), teammate_name (optional). The published docs call the
                 subject task_name, so both spellings are read.
  Stop           a turn ended. Fields: stop_hook_active, last_assistant_message
                 (optional). This one only writes when CLAUDE_ROLE is "lane"
                 and the cwd holds a NOTES.md (the lane's first artifact), so
                 the orchestrator's own turns never land.

Behaviour: append one table row to the file named by CLAUDE_LANDINGS_FILE
(default ~/.claude/landings.md, created with a header when missing),
then raise one Herdr notification when herdr is on PATH and HERDR_ENV is 1.
Failures are swallowed: the hook exits 0 on every path and never blocks a turn.
When stop_hook_active is true it does nothing, so a blocked turn cannot loop.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

DEFAULT_LANDINGS = os.path.join(
    os.path.expanduser("~"), ".claude", "landings.md"
)  # override with CLAUDE_LANDINGS_FILE
HEADER = (
    "# Landings\n\n"
    "One row per finished unit of work, written by the landing hook. "
    "No model runs to produce it; read it when you are woken, never before.\n\n"
    "| time | event | role | where | id | summary |\n"
    "| --- | --- | --- | --- | --- | --- |\n"
)
TAIL_BYTES = 2 * 1024 * 1024
SUMMARY_LIMIT = 240
NOTIFICATION_LIMIT = 80
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def one_line(text, limit):
    """Flatten to a single cell: no reminders, no pipes, no newlines, bounded."""
    if not text:
        return ""
    text = SYSTEM_REMINDER.sub(" ", text)
    text = text.replace("|", "/")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def tail_lines(path):
    """Last TAIL_BYTES of a JSONL file as a list of lines, newest last."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        if size > TAIL_BYTES:
            handle.seek(size - TAIL_BYTES)
            handle.readline()  # drop the partial line the seek landed inside
        blob = handle.read()
    return blob.decode("utf-8", "replace").splitlines()


def block_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "\n".join(parts)


def last_assistant_text(path):
    """The last assistant text of a transcript, read from the end."""
    if not path or not os.path.isfile(path):
        return ""
    try:
        lines = tail_lines(path)
    except OSError:
        return ""
    for line in reversed(lines):
        line = line.strip()
        if not line or '"assistant"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        text = block_text(message.get("content"))
        text = SYSTEM_REMINDER.sub(" ", text).strip()
        if text:
            return text
    return ""


def git_branch(cwd):
    if not cwd or not os.path.isdir(cwd):
        return ""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if done.returncode != 0:
        return ""
    return done.stdout.decode("utf-8", "replace").strip()


def where(cwd):
    base = os.path.basename(os.path.normpath(cwd)) if cwd else ""
    branch = git_branch(cwd)
    if base and branch:
        return "%s @ %s" % (base, branch)
    return base or branch or "-"


def append_row(row):
    path = os.environ.get("CLAUDE_LANDINGS_FILE") or DEFAULT_LANDINGS
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    fresh = not os.path.isfile(path) or os.path.getsize(path) == 0
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        if fresh:
            handle.write(HEADER)
        handle.write(row + "\n")
    return path


def notify(title):
    if os.environ.get("HERDR_ENV") != "1":
        return False
    if not shutil.which("herdr"):
        return False
    try:
        subprocess.run(
            ["herdr", "notification", "show", title, "--sound", "done"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        data = json.loads(raw or "{}")
    except Exception:
        return 0
    try:
        if not isinstance(data, dict):
            return 0
        if data.get("stop_hook_active"):
            return 0

        event = data.get("hook_event_name") or "?"
        role = (os.environ.get("CLAUDE_ROLE") or "").strip().lower()
        cwd = data.get("cwd") or os.getcwd()
        # A lane writes NOTES.md in its worktree before anything else, so the file
        # is what tells a lane from an interactive session that merely inherited
        # the launcher's default role (one landed every turn on 2026-08-27).
        if event == "Stop" and (
            role != "lane" or not os.path.isfile(os.path.join(cwd, "NOTES.md"))
        ):
            return 0
        # Measured on 2026-08-27: SubagentStop also fires for the harness's own
        # short-lived internal agents, roughly one per tool call, about 300 rows an
        # hour across three open sessions. Those carry an empty agent_type; an agent
        # launched with the Agent tool always carries its type. A settings matcher of
        # ".+" does not filter them, so the rule lives here. Set CLAUDE_LANDINGS_ALL=1
        # to land every subagent stop instead.
        if (
            event == "SubagentStop"
            and not (data.get("agent_type") or "").strip()
            and os.environ.get("CLAUDE_LANDINGS_ALL") != "1"
        ):
            return 0

        if event == "SubagentStop":
            identifier = data.get("agent_id") or data.get("session_id") or ""
            summary = last_assistant_text(data.get("agent_transcript_path"))
            if not summary:
                summary = data.get("last_assistant_message") or ""
            agent_type = data.get("agent_type") or ""
            if agent_type:
                summary = "[%s] %s" % (agent_type, summary)
        elif event == "TaskCompleted":
            identifier = data.get("task_id") or data.get("session_id") or ""
            # The shipped CLI schema names it task_subject; the published docs say
            # task_name. Accept either, and fall back to the description.
            summary = (
                data.get("task_subject")
                or data.get("task_name")
                or data.get("subject")
                or data.get("task_description")
                or ""
            )
        else:
            identifier = data.get("session_id") or ""
            summary = last_assistant_text(data.get("transcript_path"))
            if not summary:
                summary = data.get("last_assistant_message") or ""

        summary = one_line(summary, SUMMARY_LIMIT) or "-"
        row = "| %s | %s | %s | %s | %s | %s |" % (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            event,
            role or "-",
            where(cwd),
            (identifier or "-")[:8],
            summary,
        )
        append_row(row)
        base = os.path.basename(os.path.normpath(cwd)) if cwd else "-"
        notify("%s %s: %s" % (event, base, one_line(summary, NOTIFICATION_LIMIT)))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
