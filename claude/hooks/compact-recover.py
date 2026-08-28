"""SessionStart hook (matcher: compact). Prints, as context, what a compaction summary
tends to drop: the head of the worktree's NOTES.md, the newest brief in CLAUDE_BRIEFS_DIR,
the tail of CLAUDE_LANDINGS_FILE and a pointer to NOTES.autosave.md. No model runs.

stdin: the hook JSON (session_id, cwd, source). stdout becomes context, capped here at
4,000 characters so the recovery itself never bloats the window.
"""
import glob
import json
import os
import sys
from datetime import datetime

CAP = 4000


def head(path, lines):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join([next(f) for _ in range(lines)])
    except StopIteration:
        return open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


def tail(path, lines):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except Exception:
        return ""


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        data = {}
    cwd = data.get("cwd") or os.getcwd()
    out = [f"[compaction recovery {datetime.now().strftime('%Y-%m-%d %H:%M')}] Re-read before acting: the acceptance list, the last decision and the next step live in the files below, not in the summary."]
    notes = os.path.join(cwd, "NOTES.md")
    if os.path.isfile(notes):
        out.append(f"NOTES.md ({notes}), first 40 lines:\n{head(notes, 40).rstrip()}")
    else:
        out.append(f"No NOTES.md in {cwd}, which is correct: an agent keeps no notes file; its checkpoint is its report (law of 2026-08-27).")
    briefs = os.environ.get("CLAUDE_BRIEFS_DIR")
    if briefs and os.path.isdir(briefs):
        files = sorted(glob.glob(os.path.join(briefs, "*.md")), key=os.path.getmtime)
        if files:
            out.append(f"Train brief: {files[-1]} (read its status sections before continuing).")
    landings = os.environ.get("CLAUDE_LANDINGS_FILE")
    if landings and os.path.isfile(landings):
        out.append(f"Last landings ({landings}):\n{tail(landings, 5).rstrip()}")
    autosave = os.path.join(cwd, "NOTES.autosave.md")
    if os.path.isfile(autosave):
        out.append(f"The dialogue before this compaction was saved to {autosave}; read its last section if the summary lost a decision.")
    text = "\n\n".join(out)
    if len(text) > CAP:
        text = text[:CAP] + "\n[recovery output capped]"
    sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
