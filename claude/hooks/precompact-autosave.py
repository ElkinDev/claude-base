"""PreCompact hook. Copies the human and assistant text of the transcript tail into
<cwd>/NOTES.autosave.md before the compaction rewrites the window, so what was said in
the last stretch is never unrecoverable. Tool results, tool calls and thinking are skipped.

stdin: the hook JSON (session_id, transcript_path, trigger, cwd, custom_instructions).
Keeps the last 40 text messages, each cut at 1,500 characters, and trims the file to its
last 200 KB. Never blocks the compaction: every failure exits 0 silently.

The file lands in a repository more often than not, and it is a dump of the conversation, so
before writing it the hook makes sure git ignores it: if `git check-ignore` does not already
cover it, the name is appended to the repository's local exclude list (`.git/info/exclude`,
shared by all worktrees of that repository) and never to the team's `.gitignore`. Idempotent,
silent outside a repository or without git, and enough for any machine or project where the
hook is installed: nobody has to remember the rule before the first compaction.
"""
import json
import os
import subprocess
import sys
from datetime import datetime

KEEP = 40
PER_MSG = 1500
FILE_CAP = 200_000
AUTOSAVE_NAME = "NOTES.autosave.md"


def git(cwd, *args):
    try:
        proc = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=10)
    except Exception:
        return None, ""
    return proc.returncode, (proc.stdout or "").strip()


def ensure_excluded(cwd, name=AUTOSAVE_NAME):
    """Make git ignore <name> under cwd through the local exclude list. Returns True when the
    name is ignored after the call, False when cwd is not a repository or git is unavailable."""
    code, _ = git(cwd, "check-ignore", "-q", name)
    if code == 0:
        return True  # .gitignore or the exclude list already covers it
    if code is None or code != 1:
        return False  # no git, or not inside a repository (128)
    code, exclude = git(cwd, "rev-parse", "--git-path", "info/exclude")
    if code != 0 or not exclude:
        return False
    if not os.path.isabs(exclude):
        exclude = os.path.join(cwd, exclude)
    try:
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        current = open(exclude, encoding="utf-8", errors="replace").read() if os.path.isfile(exclude) else ""
        if name not in current.splitlines():
            with open(exclude, "a", encoding="utf-8", newline="\n") as handle:
                if current and not current.endswith("\n"):
                    handle.write("\n")
                handle.write(name + "\n")
    except Exception:
        return False
    code, _ = git(cwd, "check-ignore", "-q", name)
    return code == 0


def texts_of(entry):
    msg = entry.get("message") or {}
    role = msg.get("role") or entry.get("type")
    content = msg.get("content")
    parts = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                parts.append(block["text"])
    text = "\n".join(p.strip() for p in parts if p and p.strip())
    if not text or text.startswith("<system-reminder>") or text.startswith("<local-command"):
        return None
    return role, text


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        path = data.get("transcript_path")
        cwd = data.get("cwd") or os.getcwd()
        if not path or not os.path.isfile(path):
            return 0
        rows = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("type") not in ("user", "assistant"):
                    continue
                got = texts_of(entry)
                if got:
                    rows.append(got)
        rows = rows[-KEEP:]
        if not rows:
            return 0
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        out = [f"\n\n## Autosave before compaction, {stamp} (trigger: {data.get('trigger', '?')}, session {str(data.get('session_id', ''))[:8]})\n"]
        for role, text in rows:
            if len(text) > PER_MSG:
                text = text[:PER_MSG] + " [cut]"
            out.append(f"\n**{role}:** {text}\n")
        target = os.path.join(cwd, AUTOSAVE_NAME)
        ensure_excluded(cwd)
        prev = ""
        if os.path.isfile(target):
            prev = open(target, encoding="utf-8", errors="replace").read()
        else:
            prev = "# NOTES.autosave.md\n\nWritten by the PreCompact hook: the last text messages before each compaction. Not for commit.\n"
        content = prev + "".join(out)
        if len(content) > FILE_CAP:
            content = content[-FILE_CAP:]
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
