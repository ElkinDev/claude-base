"""SessionStart hook (matcher: compact). Prints, as context, what a compaction summary
tends to drop: the newest checkpoint written by precompact-checkpoint.py (path plus its
disk-truth section), the head of the worktree's NOTES.md, the newest brief in
CLAUDE_BRIEFS_DIR, the tail of CLAUDE_LANDINGS_FILE and a pointer to NOTES.autosave.md.
No model runs.

stdin: the hook JSON (session_id, transcript_path, cwd, source). stdout becomes context,
capped here at 4,000 characters so the recovery itself never bloats the window.
"""
import glob
import json
import os
import sys
from datetime import datetime

CAP = 4000
DISK_TRUTH_CAP = 1500


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


def checkpoint_dir(transcript_path, cwd):
    configured = os.environ.get("CLAUDE_CHECKPOINT_DIR")
    if configured:
        return configured
    if transcript_path:
        project = os.path.basename(os.path.dirname(transcript_path)) or "default"
    else:
        project = "".join(ch if ch.isalnum() else "-" for ch in cwd) or "default"
    return os.path.join(os.path.expanduser("~"), ".claude", "checkpoints", project)


def newest_checkpoint(folder, session_id):
    files = [
        path
        for path in sorted(glob.glob(os.path.join(folder, f"????????-??????-{session_id[:8]}-*.md")))
        if not path.endswith("-summary.md") and "-agent-" not in os.path.basename(path)
    ]
    return files[-1] if files else ""


def disk_truth_section(path):
    """The '## Disk truth' section of a checkpoint, capped."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except Exception:
        return ""
    start = text.find("## Disk truth")
    if start < 0:
        return ""
    end = text.find("\n## ", start + 1)
    section = text[start:end if end > 0 else len(text)].strip()
    if len(section) > DISK_TRUTH_CAP:
        section = section[:DISK_TRUTH_CAP] + "\n[see the file for the rest]"
    return section


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        data = {}
    cwd = data.get("cwd") or os.getcwd()
    session_id = str(data.get("session_id") or "")
    transcript_path = str(data.get("transcript_path") or "")
    out = [f"[compaction recovery {datetime.now().strftime('%Y-%m-%d %H:%M')}] Re-read before acting: the acceptance list, the last decision and the next step live in the files below, not in the summary."]
    checkpoint = newest_checkpoint(checkpoint_dir(transcript_path, cwd), session_id) if session_id else ""
    if checkpoint:
        section = disk_truth_section(checkpoint)
        out.append(f"Checkpoint written just before this compaction: {checkpoint} (git truth, subagents, last words). Trust it over the summary for paths, tips and uncommitted files." + (f"\n{section}" if section else ""))
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
