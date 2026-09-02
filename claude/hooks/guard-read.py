"""PreToolUse hook for Read and for the shell tools: keep whole large files and images out
of the context.

Two rules, and nothing else is ever denied:

1. Image files are denied when CLAUDE_ROLE is "orchestrator" and the call comes from the
   pane itself. A pane that coordinates does not need pixels; an agent it launches looks and
   reports. A subagent inherits the variable, so it is recognised by its payload instead:
   agent_id or agent_type present, or a transcript_path under a subagents directory. An
   unset CLAUDE_ROLE means "lane", which is allowed.
2. Any file above 150 KB is denied unless the call already carries a limit of 400 lines or
   fewer. The reason names the size and the line count and offers the slice commands.
   Images and PDFs are exempt from this rule: they are read as pixels and pages, a slice of
   them means nothing, and a device screenshot is over 150 KB every time.

The same two rules run on a shell command, because the Read tool is not the only way a file
reaches the window: `cat`, `sed`, `head`, `tail`, `type` and `Get-Content` put the same bytes
there. An orchestrator has a reason to prefer them, since a harness re-injects every file read
with the Read tool at each compaction and a shell read is paid for once, so the guard has to
follow it there or it stops guarding anything. Which files a command line prints, and whether it
prints them whole, is read by shell_read.py beside this file.

Any internal failure exits 0 without output, so the hook can never block a call.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from shell_read import ALLOWED_LIMIT_LINES, SIZE_LIMIT_BYTES, reads  # noqa: E402

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
SIZE_EXEMPT_EXTENSIONS = IMAGE_EXTENSIONS + (".pdf",)
SHELL_TOOLS = ("Bash", "PowerShell")


def deny(reason):
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


def count_lines(path):
    total = 0
    last = b""
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            total += chunk.count(b"\n")
            last = chunk[-1:]
    if last and last != b"\n":
        total += 1
    return total


def call_limit(tool_input):
    value = tool_input.get("limit")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def verdict(path, limit, role, is_subagent, check_size=True):
    """The denial reason for one file, or None when it may be read."""
    extension = os.path.splitext(path)[1].lower()
    if extension in IMAGE_EXTENSIONS and role == "orchestrator" and not is_subagent:
        return (
            "This pane runs as CLAUDE_ROLE=orchestrator and does not read images. "
            "%s stays on disk: delegate the look to a lane or a fork and ask for a "
            "written description, or open it yourself outside the session." % path
        )
    if extension in SIZE_EXEMPT_EXTENSIONS or not check_size:
        return None
    size = os.path.getsize(path)
    if size > SIZE_LIMIT_BYTES and not (limit is not None and limit <= ALLOWED_LIMIT_LINES):
        return (
            "%s is %d KB and %d lines. Reading it whole would fill the context with "
            "text you did not choose. Read a slice instead: Read with offset and limit "
            "(limit %d or fewer lines), or `sed -n 1,200p \"%s\"` for a known range, or "
            "`grep -n \"<pattern>\" \"%s\"` first to find the lines that matter."
            % (path, size // 1024, count_lines(path), ALLOWED_LIMIT_LINES, path, path)
        )
    return None


def guard_read(data, role, is_subagent):
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or ""
    if not path or not os.path.isfile(path):
        return None
    return verdict(path, call_limit(tool_input), role, is_subagent)


def guard_shell(data, role, is_subagent):
    command = str((data.get("tool_input") or {}).get("command") or "")
    cwd = str(data.get("cwd") or "")
    backticks = data.get("tool_name") != "PowerShell"  # there a backtick escapes, not substitutes
    for path, limit, elsewhere in reads(command, cwd, backticks):
        reason = verdict(path, limit, role, is_subagent, check_size=not elsewhere)
        if reason:
            return reason
    return None


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return 0
    try:
        tool = data.get("tool_name")
        if tool != "Read" and tool not in SHELL_TOOLS:
            return 0
        role = (os.environ.get("CLAUDE_ROLE") or "lane").strip().lower()
        transcript = str(data.get("transcript_path") or "").replace("\\", "/")
        is_subagent = bool(data.get("agent_id") or data.get("agent_type")) or "/subagents/" in transcript
        trace = os.environ.get("GUARD_READ_TRACE")
        if trace:
            with open(trace, "a", encoding="utf-8") as t:
                t.write("%s role=%s subagent=%s keys=%s\n" % (tool, role, is_subagent, sorted(data.keys())))
        reason = guard_read(data, role, is_subagent) if tool == "Read" else guard_shell(data, role, is_subagent)
        if reason:
            deny(reason)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
