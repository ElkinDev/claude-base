"""SessionStart hook (matcher: compact). Prints, as context, what a compaction summary
tends to drop: the newest checkpoint written by precompact-checkpoint.py (path plus its
disk-truth section), the last rows of the rulings register, the head of the worktree's
NOTES.md, the newest brief in CLAUDE_BRIEFS_DIR and the tail of CLAUDE_LANDINGS_FILE.
No model runs. Both modes open with the seat block: which chair the pane holds, the loud line
when nothing launched the session, and the newest resume brief of that seat. With --rulings it
prints the seat block and the rulings block and nothing else, which is what a SessionStart on
startup, resume, clear or fork wires; stdin is read there too, because the payload is what tells
a subagent from the session that launched it.

The block states facts and gives no orders. An instruction to re-read a file is paid for
on every compaction and is acted on whether or not the summary already carries the answer,
so the opening line names the checkpoint and the one condition that makes it worth opening.

stdin: the hook JSON (session_id, transcript_path, cwd, source). stdout becomes context,
capped here at 9,000 characters so the recovery itself never bloats the window. Claude
Code truncates hook stdout above about 10,000 characters (measured 2026-08-27 on 2.1.248
and not re-measured since), so the cap keeps a margin of 1,000 characters under it.
"""
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime

# Both caps are ceilings on what is printed, so the marker that says the text was cut is
# counted inside them and not added after: a cap the marker overruns is not a cap.
# Measured with the seeded register: headroom without a checkpoint 620 characters, none
# with a checkpoint of 1,800, the tail is what gets cut. The tail is the landings, the
# brief line, the notes line and the end of the gates listing; the rulings block prints
# before all of them, so a ruling is never what a cap takes. Claude Code truncated hook
# stdout above about 10,000 characters (measured 2026-08-27 on 2.1.248, not re-measured
# since); 9,000 keeps a margin of 1,000 under it.
CAP = 9000
CAP_MARKER = "\n[recovery output capped]"
DISK_TRUTH_CAP = 900
DISK_TRUTH_MARKER = "\n[see the file for the rest]"


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
        section = section[:DISK_TRUTH_CAP - len(DISK_TRUTH_MARKER)] + DISK_TRUTH_MARKER
    return section


RULINGS_FILE = os.path.join(os.path.expanduser("~"), ".claude", "rulings.md")
RULINGS_ROWS = 30
# The 26 rows the register opens with measure 7,275 characters with their heading, so a
# smaller cap cuts the seed itself. The block prints before the state sheet, so what CAP
# trims from the tail of a long run is the sheet and the notes line, never a ruling.
RULINGS_CAP = 7600
RULINGS_MARKER = "\n[rulings cut, open the file]"
# A register row: the time is xx:xx when it is not on record, so both halves take x.
RULING_ROW_RE = re.compile(r"^- \d{4}-\d{2}-\d{2} [0-9x]{2}:[0-9x]{2} \[")


def ruling_key(row):
    """The sort key of a register row: its date, then its hour as written.

    The hour is compared as text, which is what the x of an unrecorded minute needs:
    x sorts after every real digit of its position, so 12:1x lands after 12:19 and
    before 12:20, and xx:xx sits at the end of its day, which is all that is known
    about it. The row shape is fixed width, so the two slices are exact.
    """
    return (row[2:12], row[13:18])


def rulings_block():
    """The last rows of the owner's rulings register, whole, capped as one block.

    The rows are what a compaction summary paraphrases away, so they are printed
    before anything else on disk. A register that is missing says so in one line
    rather than dropping the paragraph, because its absence is itself news. A
    register that is there and still empty says that instead: a session told its
    register is missing writes a second one beside it, and the rulings split.

    A block over the cap gives up whole rows from its oldest end, never its newest:
    the newest rulings are the ones a compaction loses, which is why the register
    exists. The marker sits where the rows were dropped and is counted in the cap.

    The register is append-only by rule, so a ruling made at 11:47 and written at
    12:30 sits below a 12:2x row while carrying the older stamp: file order is not
    time order. The rows are sorted by their stamp before either cut, so the oldest
    end the cuts give up is the oldest by time and never the newest ruling. The sort
    is stable, so rows that carry the same stamp keep the order the file wrote them in.
    """
    path = os.environ.get("CLAUDE_RULINGS_FILE") or RULINGS_FILE
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            rows = [line.rstrip() for line in handle if RULING_ROW_RE.match(line)]
    except OSError:
        return f"No rulings register at {path}."
    if not rows:
        return f"No rulings yet in {path}."
    heading = (f"Rulings (last {RULINGS_ROWS}, register {path}; "
               "a delivery that contradicts one is blocked):")
    kept = sorted(rows, key=ruling_key)[-RULINGS_ROWS:]
    dropped = False
    while kept:
        block = heading + (RULINGS_MARKER if dropped else "") + "\n" + "\n".join(kept)
        if len(block) <= RULINGS_CAP:
            return block
        kept = kept[1:]
        dropped = True
    return heading + RULINGS_MARKER


LANE_STATE_SCRIPT = os.path.join(os.path.expanduser("~"), ".claude", "tools", "lane-state.py")
LANE_STATE_SHEET = os.path.join(os.path.expanduser("~"), ".claude", "law.md")
SHEET_TIMEOUT = 5
GATES_CAP = 700
GATES_MARKER = "\n[gates cut]"


def state_sheet():
    """The path of the state sheet, freshly rendered, plus its Gates section.

    lane-state.py reads only files already on disk, so the render costs no tokens. A
    render that fails leaves the previous sheet in place; a sheet that cannot be read
    at all drops this paragraph instead of the whole recovery block.

    CLAUDE_LANE_STATE_SCRIPT and CLAUDE_LANE_STATE_SHEET move the renderer and the file
    it writes. They travel together: redirecting one and not the other reads a sheet
    nobody wrote. A test run of this hook sets both, so that running it never rewrites
    the sheet the live sessions read.
    """
    script = os.environ.get("CLAUDE_LANE_STATE_SCRIPT") or LANE_STATE_SCRIPT
    sheet_path = os.environ.get("CLAUDE_LANE_STATE_SHEET") or LANE_STATE_SHEET
    if not os.path.isfile(script):
        return ""
    try:
        subprocess.run(
            [sys.executable, script, "law"],
            capture_output=True, timeout=SHEET_TIMEOUT,
        )
    except Exception:
        pass
    try:
        with open(sheet_path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except Exception:
        return ""
    rendered = datetime.fromtimestamp(os.path.getmtime(sheet_path)).strftime("%H:%M")
    gates = ""
    for index, line in enumerate(lines):
        if line.startswith("## Gates"):
            end = index + 1
            while end < len(lines) and not lines[end].startswith("## "):
                end += 1
            gates = "\n".join(lines[index:end]).rstrip()
            break
    if len(gates) > GATES_CAP:
        gates = gates[:GATES_CAP - len(GATES_MARKER)] + GATES_MARKER
    paragraph = (
        f"State sheet: {sheet_path} (rendered {rendered}, {len(lines)} lines); "
        "read it before any document over 20 KB."
    )
    return paragraph + ("\n" + gates if gates else "")


SEATS = ("orchestrator", "analyst")
BRIEFS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "briefs")
# The three variables the account launcher sets on every launch. None of them in the environment
# means nothing launched this session: a bare restore after a logon, or a session started by hand.
LAUNCHER_VARS = ("CLAUDE_ROLE", "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_DISABLE_1M_CONTEXT")
UNLAUNCHED = ("Not launched through the account launcher: no seat, no window, no --no-chrome. "
              "Relaunch through it before working.")


def seat():
    """The seat of this session, or "" when the pane holds none.

    A seat is a markdown file appended to the system prompt, but the file is not readable from
    here and a resumed session carries its seat in the conversation rather than in the launch
    line. CLAUDE_ROLE is what both launches set, so the variable is the seam, and any value that
    is not a seat, the default lane included, is no seat at all.
    """
    role = (os.environ.get("CLAUDE_ROLE") or "").strip().lower()
    return role if role in SEATS else ""


def resume_brief(name):
    """The line naming the newest resume brief of this seat, or "" when there is no directory.

    Newest by name, not by modification time: the name carries the day the brief is for, and a
    brief corrected after it was written is still the brief of its own day. A directory that is
    not there is not a fault, it is a project without a board, so nothing is said about it.

    The stem is sorted, never the whole filename. A second brief of the same day is
    `<seat>-resume-<YYYY-MM-DD>-<HHMM>.md`, and over whole filenames the dash of its hour sorts
    before the dot of `.md`, which hands a seat that wrote a brief at midday the morning one.
    Over stems the hour is a longer string with the same prefix, so it sorts last, where it
    belongs.
    """
    folder = os.environ.get("CLAUDE_BRIEFS_DIR") or BRIEFS_DIR
    if not os.path.isdir(folder):
        return ""
    files = sorted(glob.glob(os.path.join(folder, f"{name}-resume-*.md")),
                   key=lambda f: os.path.splitext(os.path.basename(f))[0])
    if not files:
        return f"Resume brief: none found in {folder}"
    return f"Resume brief: {files[-1]} (read it first)"


def seat_block(data):
    """Which chair this pane holds, printed before anything else.

    A subagent inherits the whole environment of the session that launched it, so the variable
    alone cannot tell them apart; the payload can, and an agent holds no chair, so a payload
    that names one prints nothing.
    """
    if data.get("agent_id") or data.get("agent_type"):
        return ""
    name = seat()
    lines = [f"Seat: {name}" if name else "Seat: none (lane)"]
    if not any(var in os.environ for var in LAUNCHER_VARS):
        lines.append(UNLAUNCHED)
    if name:
        line = resume_brief(name)
        if line:
            lines.append(line)
    return "\n".join(lines)


def payload():
    """The hook JSON on stdin, as a dict, whatever arrives.

    Read in both modes, because the subagent exclusion of the seat block lives in the payload.
    A terminal on stdin is nobody piping anything, so it is never read: a hook run by hand must
    not hang on a read that will not return. utf-8-sig, not utf-8, because PowerShell puts a BOM
    in front of what it pipes to a native command.
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig", "replace") or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def main():
    data = payload()
    block = seat_block(data)
    if "--rulings" in sys.argv[1:]:
        text = rulings_block()
        if block:
            text = block + "\n\n" + text
        sys.stdout.buffer.write(text.encode("utf-8"))
        return 0
    cwd = data.get("cwd") or os.getcwd()
    session_id = str(data.get("session_id") or "")
    transcript_path = str(data.get("transcript_path") or "")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    checkpoint = newest_checkpoint(checkpoint_dir(transcript_path, cwd), session_id) if session_id else ""
    if checkpoint:
        out = [f"[compaction recovery {stamp}] The checkpoint at {checkpoint} is the git truth for this session, written just before the compaction; open it only if the summary lacks a path, a tip or a decision."]
        section = disk_truth_section(checkpoint)
        if section:
            out.append(section)
    else:
        out = [f"[compaction recovery {stamp}] No checkpoint for this session, so the summary plus the facts below are what there is."]
    if block:
        out.insert(0, block)
    out.append(rulings_block())
    sheet = state_sheet()
    if sheet:
        out.append(sheet)
    notes = os.path.join(cwd, "NOTES.md")
    if os.path.isfile(notes):
        out.append(f"NOTES.md ({notes}), first 40 lines:\n{head(notes, 40).rstrip()}")
    else:
        out.append(f"No NOTES.md in {cwd}, which is correct: an agent keeps no notes file; its checkpoint is its report (law of 2026-08-27).")
    briefs = os.environ.get("CLAUDE_BRIEFS_DIR")
    if briefs and os.path.isdir(briefs):
        files = sorted(glob.glob(os.path.join(briefs, "*.md")), key=os.path.getmtime)
        if files:
            out.append(f"Train brief: {files[-1]}")
    landings = os.environ.get("CLAUDE_LANDINGS_FILE")
    if landings and os.path.isfile(landings):
        out.append(f"Last landings ({landings}):\n{tail(landings, 5).rstrip()}")
    text = "\n\n".join(out)
    if len(text) > CAP:
        text = text[:CAP - len(CAP_MARKER)] + CAP_MARKER
    sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
