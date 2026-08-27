"""PreToolUse hook for Read: keep whole large files and images out of the context.

Two rules, and nothing else is ever denied:

1. Image files are denied when CLAUDE_ROLE is "orchestrator". A pane that coordinates does
   not need pixels; a lane or a fork looks and reports. An unset CLAUDE_ROLE means "lane",
   which is allowed.
2. Any file above 150 KB is denied unless the call already carries a limit of 400 lines or
   fewer. The reason names the size and the line count and offers the slice commands.

Any internal failure exits 0 without output, so the hook can never block a Read.
"""
import json
import os
import sys

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
SIZE_LIMIT_BYTES = 150 * 1024
ALLOWED_LIMIT_LINES = 400


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


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return 0
    try:
        if data.get("tool_name") != "Read":
            return 0
        tool_input = data.get("tool_input") or {}
        path = tool_input.get("file_path") or ""
        if not path or not os.path.isfile(path):
            return 0

        extension = os.path.splitext(path)[1].lower()
        role = (os.environ.get("CLAUDE_ROLE") or "lane").strip().lower()
        if extension in IMAGE_EXTENSIONS and role == "orchestrator":
            deny(
                "This pane runs as CLAUDE_ROLE=orchestrator and does not read images. "
                "%s stays on disk: delegate the look to a lane or a fork and ask for a "
                "written description, or open it yourself outside the session." % path
            )
            return 0

        size = os.path.getsize(path)
        limit = call_limit(tool_input)
        if size > SIZE_LIMIT_BYTES and not (limit is not None and limit <= ALLOWED_LIMIT_LINES):
            lines = count_lines(path)
            deny(
                "%s is %d KB and %d lines. Reading it whole would fill the context with "
                "text you did not choose. Read a slice instead: Read with offset and limit "
                "(limit %d or fewer lines), or `sed -n 1,200p \"%s\"` for a known range, or "
                "`grep -n \"<pattern>\" \"%s\"` first to find the lines that matter."
                % (path, size // 1024, lines, ALLOWED_LIMIT_LINES, path, path)
            )
            return 0
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
