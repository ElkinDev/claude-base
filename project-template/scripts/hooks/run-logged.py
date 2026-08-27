"""Runs a shell command with its whole output on disk and only a digest on stdout.

The gradle PreToolUse filter rewrites a build command into a call to this runner. The
runner executes the original command through Git Bash, writes every line to a log file
under <project>/build/tool-logs/, prints a short digest, and exits with the command's own
exit code so the caller still sees success or failure.

Usage:
    python run-logged.py --log <path> -- <command>

The command runs in a fresh bash, so nothing it does to its own shell survives it. A
leading `cd <dir> &&` must therefore stay outside this call, in the session shell, and only
the part after it is passed here; the filter hook splits the command that way.

The digest keeps the build result line, the failed tasks, kotlin and compiler errors, the
failing test lines, the test count summaries, the "What went wrong" block and the tail of
the output. It is capped by lines and by characters, always cutting from the middle so the
first line (the log path) and the last lines (where a build usually explains itself) stay.
"""
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

CHAR_CAP = 4000
LINE_CAP = 150
TAIL_LINES = 15
WRONG_BLOCK_LINES = 20
SLUG_CAP = 40

BASH_CANDIDATES = (
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files\Git\bin\bash.exe",
    "/bin/bash",
)

KEEP_PATTERNS = (
    re.compile(r"BUILD SUCCESSFUL|BUILD FAILED"),
    re.compile(r">\s*Task\b.*\bFAILED\b"),
    re.compile(r"^e: "),
    re.compile(r"error:", re.IGNORECASE),
    re.compile(r"tests? completed", re.IGNORECASE),
)


def slug(command):
    """A short file-name-safe tag for a command, used in the log file name."""
    text = re.sub(r"[^A-Za-z0-9]+", "-", command.lower()).strip("-")
    return text[:SLUG_CAP].strip("-") or "command"


def default_log_path(command, project_dir=None):
    project = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(project, "build", "tool-logs", "%s-%s.log" % (stamp, slug(command)))


def _keep(line):
    if line.rstrip().endswith("FAILED"):
        return True
    return any(pattern.search(line) for pattern in KEEP_PATTERNS)


def _wrong_block(lines):
    """Indices of the gradle "What went wrong" block, up to WRONG_BLOCK_LINES lines."""
    keep = set()
    for i, line in enumerate(lines):
        if "What went wrong" not in line:
            continue
        keep.add(i)
        for j in range(i + 1, min(i + WRONG_BLOCK_LINES, len(lines))):
            if lines[j].startswith("* "):
                break
            keep.add(j)
    return keep


def cap_lines(lines, cap=LINE_CAP):
    if len(lines) <= cap:
        return lines
    marker = "... [%d lines cut from the middle of the digest]" % (len(lines) - cap + 1)
    head = cap // 2
    tail = cap - head - 1
    return lines[:head] + [marker] + lines[len(lines) - tail:]


def cap_chars(text, cap=CHAR_CAP):
    if len(text) <= cap:
        return text
    marker = "\n... [digest cut in the middle, the whole output is in the log]\n"
    room = cap - len(marker)
    head = room // 2
    tail = room - head
    return text[:head] + marker + text[len(text) - tail:]


def digest(output, log_path, exit_code):
    lines = output.splitlines()
    keep = {i for i, line in enumerate(lines) if _keep(line)}
    keep |= _wrong_block(lines)
    keep |= set(range(max(0, len(lines) - TAIL_LINES), len(lines)))
    body = []
    previous = -1
    for i in sorted(keep):
        if previous >= 0 and i > previous + 1:
            body.append("... [%d lines only in the log]" % (i - previous - 1))
        body.append(lines[i])
        previous = i
    header = "%s (exit %d, %d lines of output)" % (log_path.replace("\\", "/"), exit_code, len(lines))
    return cap_chars("\n".join(cap_lines([header] + body)))


def _bash():
    for candidate in BASH_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("bash")


def run(command, log_path):
    directory = os.path.dirname(log_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    bash = _bash()
    if bash:
        argv, shell = [bash, "-c", command], False
    else:
        argv, shell = command, True
    captured = []
    with open(log_path, "w", encoding="utf-8", errors="replace", newline="\n") as log:
        process = subprocess.Popen(
            argv,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
        for line in process.stdout:
            log.write(line)
            captured.append(line.rstrip("\r\n"))
        process.stdout.close()
        process.wait()
    return process.returncode, "\n".join(captured)


def main():
    argv = sys.argv[1:]
    log_path = None
    if "--log" in argv:
        i = argv.index("--log")
        if i + 1 < len(argv):
            log_path = argv[i + 1]
            del argv[i:i + 2]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    command = " ".join(argv).strip()
    if not command:
        sys.stderr.write("run-logged.py: no command given\n")
        return 2
    if not log_path:
        log_path = default_log_path(command)
    exit_code, output = run(command, log_path)
    sys.stdout.buffer.write((digest(output, log_path, exit_code) + "\n").encode("utf-8"))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
