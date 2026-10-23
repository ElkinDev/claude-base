"""PreToolUse hook (Bash, PowerShell): deny a subagent any run_in_background call except a local server or an
Android emulator.

Why: a subagent that starts a command with run_in_background is re-invoked each time a child exits, and each time
it stops with a child still alive it wakes the session that launched it for a full turn ("waiting on its own
background work"). Each of those turns re-reads the whole context of the agent or of its parent, so a few
background builds or polls can cost more context than the work itself. Work that fits inside the tool's own
timeout runs in the foreground; work that must run longer is started detached by a foreground call that returns
at once (a log and an exit file), after which the agent ends its turn and its parent resumes it once the exit file
exists.

The exception: a call that starts a local server (python -m http.server, npx serve, npm run dev, serve or preview,
vite but not vite build, uvicorn, flask run) or `emulator ... -avd` (SERVER_RE) may still run in the background,
since the agent needs it alive for the rest of its own turn. That call is still denied when it also holds a wait
loop: a shell, PowerShell or Python loop around a sleep (has_loop), with heredoc bodies dropped first so a script
written to a file is not a loop.

A subagent is a payload with agent_id or agent_type, or a transcript_path inside a `subagents` folder. A main
session, a call without run_in_background and any tool but Bash and PowerShell pass untouched.

One log line per deny in guard-background.log next to this file; GUARD_BACKGROUND_LOG moves it (tests).
GUARD_BACKGROUND_OFF=1 turns the hook off. Any error exits 0 with no output, so the hook never blocks by accident.
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASH_LOOP_RE = re.compile(
    r"\b(?:for|while|until)\b[\s\S]{0,400}?\bdo\b[\s\S]{0,2000}?(?:\bsleep\b|\btimeout\s+/t\b)", re.IGNORECASE)
PS_LOOP_RE = re.compile(
    r"\b(?:while|for|foreach|until)\s*\([\s\S]{0,400}?\)\s*\{[\s\S]{0,2000}?\bStart-Sleep\b"
    r"|\bForEach-Object\s*\{[\s\S]{0,2000}?\bStart-Sleep\b"
    r"|\bdo\s*\{[\s\S]{0,2000}?\bStart-Sleep\b", re.IGNORECASE)
PY_LOOP_RE = re.compile(r"\b(?:while|for)\b[^\n]{0,400}?:[\s\S]{0,2000}?\b(?:time|asyncio)\.sleep\s*\(")
SERVER_RE = re.compile(
    r"(?:python3?(?:\.exe)?\s+-m\s+http\.server|\bnpx\s+serve\b|\bnpm\s+run\s+(?:dev|serve|preview)\b"
    r"|\bvite(?:\.js)?(?=\s|$)(?!\s+build)|\buvicorn\b|\bflask\s+run\b"
    r"|\bemulator(?:\.exe)?\s+(?:-[a-z0-9-]+\s+(?:[^-\s]\S*\s+)?)*-avd\b)", re.IGNORECASE)
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n[\s\S]*?^\s*\2\s*$", re.MULTILINE)
REASON = (
    "A subagent may not use run_in_background: every child exit re-invokes you, and every stop with a child "
    "still alive wakes the session that launched you for a full turn, each re-reading a whole context. Run work "
    "that fits inside the tool timeout in the foreground, with an explicit timeout. Start longer work detached by "
    "a FOREGROUND call that returns at once, with its output to a log and its exit code to an exit file (for "
    "example the kit clone's scripts/gate-detach.ps1 -Command '<bash command>; echo $? > <exit file>' -Log <log>), "
    "then end your turn naming the log and the exit file; the session that launched you resumes you once the "
    "exit file exists. A local server (python -m http.server, npx serve, npm run dev, vite, uvicorn, flask run) "
    "or emulator -avd that this task needs may still run in the background, with no wait loop in the same command."
)


def has_loop(command):
    """True when the command holds a shell, PowerShell or Python wait loop outside a heredoc body."""
    command = HEREDOC_RE.sub("<<HEREDOC", str(command or ""))
    return bool(BASH_LOOP_RE.search(command) or PS_LOOP_RE.search(command) or PY_LOOP_RE.search(command))


def is_subagent(data):
    transcript = str(data.get("transcript_path") or "").replace("\\", "/")
    return bool(data.get("agent_id") or data.get("agent_type")) or "/subagents/" in transcript


def log_deny(data):
    path = os.environ.get("GUARD_BACKGROUND_LOG") or os.path.join(HERE, "guard-background.log")
    line = "%s\tsession=%s\tagent_type=%s\ttool=%s\n" % (
        time.strftime("%Y-%m-%dT%H:%M:%S"), str(data.get("session_id") or "?")[:8],
        data.get("agent_type") or "?", data.get("tool_name"))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)


def decide(data):
    """The deny output for a payload, or None to let the call pass."""
    if not isinstance(data, dict) or data.get("tool_name") not in ("Bash", "PowerShell"):
        return None
    if not is_subagent(data):
        return None
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict) or not tool_input.get("run_in_background"):
        return None
    command = str(tool_input.get("command") or "")
    if SERVER_RE.search(command) and not has_loop(command):
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": REASON}}


def main():
    try:
        if os.environ.get("GUARD_BACKGROUND_OFF") == "1":
            return 0
        raw = sys.stdin.buffer.read()
        if b"run_in_background" not in raw:
            return 0            # most calls: no parse
        out = decide(json.loads(raw.decode("utf-8", "replace") or "{}"))
        if out is None:
            return 0
        try:
            log_deny(json.loads(raw.decode("utf-8", "replace")))
        except Exception:
            pass
        sys.stdout.write(json.dumps(out))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
