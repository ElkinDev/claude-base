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
follow it there or it stops guarding anything. The command is split on the shell separators,
each piece is parsed for read targets and for the slice it already asks for, and a piece whose
output is piped or redirected is left alone: those bytes go to another program, not to the
window. Anything that is not one of those commands passes untouched.

Any internal failure exits 0 without output, so the hook can never block a call.
"""
import json
import os
import re
import sys

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
SIZE_EXEMPT_EXTENSIONS = IMAGE_EXTENSIONS + (".pdf",)
SIZE_LIMIT_BYTES = 150 * 1024
ALLOWED_LIMIT_LINES = 400

SHELL_TOOLS = ("Bash", "PowerShell")
COUNT_FLAGS = ("-totalcount", "-head", "-first", "-tail", "-last")
VALUE_FLAGS = COUNT_FLAGS + ("-path", "-literalpath", "-encoding", "-readcount", "-delimiter",
                             "-filter", "-include", "-exclude", "-stream")
PATH_FLAGS = ("-path", "-literalpath")
SED_RANGE = re.compile(r"^(\d+)(?:,(\d+))?p$")
DASH_NUMBER = re.compile(r"^-(\d+)$")
DRIVE_PATH = re.compile(r"^/([a-zA-Z])/")


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


def split_segments(command):
    """[(text, elsewhere)] for every command in a shell line. `elsewhere` is True when the
    output of that piece goes to a pipe or a file instead of to the window."""
    pieces, buf, quote, elsewhere, index = [], [], None, False, 0
    while index < len(command):
        char = command[index]
        if quote:
            buf.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            buf.append(char)
            index += 1
            continue
        if command[index:index + 2] in ("&&", "||"):
            pieces.append(("".join(buf), elsewhere))
            buf, elsewhere, index = [], False, index + 2
            continue
        if char in ";\n&":
            pieces.append(("".join(buf), elsewhere))
            buf, elsewhere, index = [], False, index + 1
            continue
        if char == "|":
            pieces.append(("".join(buf), True))
            buf, elsewhere, index = [], False, index + 1
            continue
        if char in "<>":
            # a redirect or a heredoc: what follows names a stream, not a file being read
            elsewhere = True
            while index < len(command) and command[index] not in ";\n|&":
                index += 1
            continue
        buf.append(char)
        index += 1
    pieces.append(("".join(buf), elsewhere))
    return [(text.strip(), flag) for text, flag in pieces if text.strip()]


def tokenize(segment):
    """Words of one command, quotes honoured and removed, backslashes left alone so a
    Windows path survives."""
    tokens, current, quote, quoted = [], [], None, False
    for char in segment:
        if quote:
            if char == quote:
                quote = None
            else:
                current.append(char)
            continue
        if char in "'\"":
            quote, quoted = char, True
            continue
        if char.isspace():
            if current or quoted:
                tokens.append("".join(current))
                current, quoted = [], False
            continue
        current.append(char)
    if current or quoted:
        tokens.append("".join(current))
    return tokens


def as_count(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sed_lines(script):
    """How many lines `sed -n <script>` prints, or None when that cannot be told."""
    total = 0
    for part in script.split(";"):
        match = SED_RANGE.match(part.strip())
        if not match:
            return None
        first, last = int(match.group(1)), int(match.group(2) or match.group(1))
        total += max(0, last - first + 1)
    return total


def read_plan(tokens):
    """(paths, max_lines) for a command that prints a file, else None. max_lines is None
    when the whole file would be printed and 0 when the slice is bounded by bytes."""
    if not tokens:
        return None
    name = os.path.basename(tokens[0]).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    args, paths, count = tokens[1:], [], None
    if name in ("cat", "type"):
        return [a for a in args if not a.startswith("-")], None
    if name in ("head", "tail"):
        index = 0
        while index < len(args):
            arg = args[index]
            if arg in ("-n", "-c", "--lines", "--bytes"):
                count = as_count(args[index + 1]) if index + 1 < len(args) else None
                if arg in ("-c", "--bytes") and count is not None:
                    count = 0 if count <= SIZE_LIMIT_BYTES else None
                index += 2
                continue
            if DASH_NUMBER.match(arg):
                count = int(arg[1:])
            elif not arg.startswith("-"):
                paths.append(arg)
            index += 1
        return paths, 10 if count is None and not any(a in ("-n", "-c", "--lines", "--bytes") for a in args) else count
    if name == "sed":
        if any(a == "-i" or a.startswith("--in-place") or (a.startswith("-i") and len(a) > 2) for a in args):
            return None
        quiet = any(a in ("-n", "--quiet", "--silent") for a in args)
        script, index, scripted = None, 0, False
        while index < len(args):
            arg = args[index]
            if arg in ("-e", "--expression", "-f", "--file"):
                scripted, index = True, index + 2
                continue
            if arg.startswith("-"):
                index += 1
                continue
            if script is None and not scripted:
                script = arg
            else:
                paths.append(arg)
            index += 1
        return paths, (sed_lines(script) if quiet and script else None)
    if name in ("get-content", "gc"):
        index = 0
        while index < len(args):
            arg = args[index]
            lowered = arg.lower()
            if lowered in VALUE_FLAGS:
                value = args[index + 1] if index + 1 < len(args) else ""
                if lowered in COUNT_FLAGS:
                    count = as_count(value)
                elif lowered in PATH_FLAGS:
                    paths.append(value)
                index += 2
                continue
            if arg.startswith("-"):
                index += 1
                continue
            paths.append(arg)
            index += 1
        return paths, count
    return None


def resolve(raw, cwd):
    path = os.path.expanduser(raw)
    if not os.path.isabs(path) and cwd:
        path = os.path.join(cwd, path)
    if os.path.isfile(path):
        return path
    match = DRIVE_PATH.match(raw)
    if match:  # a Git Bash path, /c/Repo/... for C:/Repo/...
        translated = "%s:/%s" % (match.group(1).upper(), raw[3:])
        if os.path.isfile(translated):
            return translated
    return path


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
    for segment, elsewhere in split_segments(command):
        plan = read_plan(tokenize(segment))
        if not plan:
            continue
        targets, limit = plan
        for target in targets:
            path = resolve(target, cwd)
            if not os.path.isfile(path):
                continue
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
