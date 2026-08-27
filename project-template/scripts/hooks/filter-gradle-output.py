"""PreToolUse hook for Bash: send gradle output to a file and return only a digest.

A gradle run prints thousands of lines that no one reads and that stay in the context for
the rest of the session. This hook rewrites the command so run-logged.py executes it, keeps
the whole output in <project>/build/tool-logs/<stamp>-<slug>.log and prints a digest with
the log path, the build result, the failed tasks and errors, and the tail.

Nothing is lost, it moves to disk. Commands that are not gradle, and commands already
wrapped by run-logged.py, are left untouched (the hook exits 0 without output).
"""
import json
import os
import re
import sys
from datetime import datetime

GRADLE_COMMANDS = {"gradlew", "gradlew.bat", "gradle", "gradle.bat"}
GRADLE_SCRIPTS = {"gradle-lockrun.ps1", "lane-gate.sh"}
SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|\n]")
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PREFIX_WORDS = {"time", "nice", "exec"}
RUNNER = "run-logged.py"
SLUG_CAP = 40


def basename(token):
    return token.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower()


def is_gradle(command):
    """True only when gradle, or one of its wrapper scripts, is the command word of a segment.

    A command that merely mentions gradle in its text, such as a grep, must not be wrapped:
    the digest would swallow its output.
    """
    for segment in SEGMENT_SPLIT.split(command):
        tokens = segment.split()
        while tokens and (ENV_ASSIGNMENT.match(tokens[0]) or tokens[0] in PREFIX_WORDS):
            tokens.pop(0)
        if len(tokens) > 1 and tokens[0] == "timeout":
            tokens = tokens[2:]
        if not tokens:
            continue
        if basename(tokens[0]) in GRADLE_COMMANDS:
            return True
        if any(basename(token) in GRADLE_SCRIPTS for token in tokens):
            return True
    return False


def slug(command):
    text = re.sub(r"[^A-Za-z0-9]+", "-", command.lower()).strip("-")
    return text[:SLUG_CAP].strip("-") or "command"


def shell_quote(command):
    """POSIX single-quote a command so bash passes it through as one argument."""
    return "'" + command.replace("'", "'\\''") + "'"


def posix(path):
    return path.replace("\\", "/")


def rewrite(command, project_dir):
    runner = posix(os.path.join(os.path.dirname(os.path.abspath(__file__)), RUNNER))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = posix(os.path.join(project_dir, "build", "tool-logs", "%s-%s.log" % (stamp, slug(command))))
    return 'python "%s" --log "%s" -- %s' % (runner, log, shell_quote(command)), log


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return 0
    try:
        if data.get("tool_name") != "Bash":
            return 0
        tool_input = data.get("tool_input") or {}
        command = tool_input.get("command") or ""
        if not command or RUNNER in command or not is_gradle(command):
            return 0
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
        new_command, log = rewrite(command, project_dir)
        updated = dict(tool_input)
        updated["command"] = new_command
        out = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "Gradle output goes to %s; only the digest returns." % log,
                "updatedInput": updated,
            }
        }
        sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
