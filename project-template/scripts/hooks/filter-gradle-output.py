"""PreToolUse hook for Bash: send gradle output to a file and return only a digest.

A gradle run prints thousands of lines that no one reads and that stay in the context for
the rest of the session. This hook rewrites the command so run-logged.py executes it, keeps
the whole output in <project>/build/tool-logs/<stamp>-<slug>.log and prints a digest with
the log path, the build result, the failed tasks and errors, and the tail.

Nothing is lost, it moves to disk. Commands that are not gradle, and commands already
wrapped by run-logged.py, are left untouched (the hook exits 0 without output).

The rewrite travels in hookSpecificOutput.updatedInput, a documented PreToolUse field. The
hooks reference, section "PreToolUse decision control", describes it as: "Modifies the
tool's input parameters before execution. Replaces the entire input object, so include
unchanged fields alongside modified ones. Combine with "allow" to auto-approve, or "ask" to
show the modified input to the user."
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
POWERSHELL = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
SHELLS = {"bash", "bash.exe", "sh", "sh.exe"}
FILE_FLAGS = {"-file", "-f", "/file"}
INLINE_FLAGS = {"-c", "-command", "-encodedcommand"}
LEADING_CD = re.compile(r"^\s*(?:cd\s+[^&;|]+&&\s*)+", re.IGNORECASE)
RUNNER = "run-logged.py"
SLUG_CAP = 40


def basename(token):
    return token.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower()


def strip_prefixes(tokens):
    """Drop everything that runs before the command word: env assignments, time, timeout N.

    The loop repeats until nothing changes, because the prefixes come in any order:
    `timeout 600 JAVA_HOME=x ./gradlew test` needs the env pass to run again after the
    timeout pass.
    """
    changed = True
    while changed and tokens:
        changed = False
        while tokens and (ENV_ASSIGNMENT.match(tokens[0]) or tokens[0] in PREFIX_WORDS):
            tokens.pop(0)
            changed = True
        if len(tokens) > 1 and basename(tokens[0]) == "timeout":
            del tokens[:2]
            changed = True
    return tokens


def command_word(segment):
    """The basename of the program a segment actually runs, looking through one launcher.

    Only the command word counts. A script named as an argument is not a run:
    `git log -- scripts/lane-gate.sh` and `cat scripts/gradle-lockrun.ps1` must keep their
    own output. The two gradle wrappers are launched as `powershell -File <script>` and
    `bash <script>`, so those forms resolve to the script they execute; `-c` and
    `-Command` do not, because what follows is a command string, and the segment split
    already reaches the words inside it.
    """
    tokens = strip_prefixes(segment.split())
    if not tokens:
        return ""
    head = basename(tokens[0])
    rest = tokens[1:]
    if head in POWERSHELL:
        for i, token in enumerate(rest):
            if token.lower() in FILE_FLAGS and i + 1 < len(rest):
                return basename(rest[i + 1])
        return head
    if head in SHELLS:
        if any(token.lower() in INLINE_FLAGS for token in rest):
            return head
        for token in rest:
            if not token.startswith("-"):
                return basename(token)
    return head


def is_gradle(command):
    """True only when gradle, or one of its wrapper scripts, is the command word of a segment."""
    for segment in SEGMENT_SPLIT.split(command):
        word = command_word(segment)
        if word in GRADLE_COMMANDS or word in GRADLE_SCRIPTS:
            return True
    return False


def split_leading_cd(command):
    """Separate a leading `cd <dir> &&` chain from the command it introduces.

    run-logged.py runs its argument in a fresh bash, so a cd inside the wrapper would move
    that child and not the session shell the agent keeps using. The chain stays outside the
    wrapper, where it still applies to the session, and only the rest is wrapped.
    """
    match = LEADING_CD.match(command)
    if not match:
        return "", command
    return match.group(0).strip(), command[match.end():].strip()


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
    prefix, wrapped = split_leading_cd(command)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = posix(os.path.join(project_dir, "build", "tool-logs", "%s-%s.log" % (stamp, slug(wrapped))))
    new_command = 'python "%s" --log "%s" -- %s' % (runner, log, shell_quote(wrapped))
    if prefix:
        new_command = "%s %s" % (prefix, new_command)
    return new_command, log


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
