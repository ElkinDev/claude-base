#!/bin/bash
# Register row helper: row.sh "<kind>" "<text>" ["<pane>"] appends one row to the rulings register with the
# date, the HH:Mx stamp and the pane suffix. The 400-character cap is applied to the TEXT the seat types; the
# stamp the script adds (date, time, kind, pane) stays outside it. A text over the cap is never refused and
# never written whole: it is cut at the last sentence boundary inside the cap, the row carries a " [cut N]"
# marker, and the dropped tail is printed back so the seat can add a continuation row when the tail carried
# the decision. Nothing over the cap can reach the register, whatever the seat believes the cap is. Both seats
# use it: the third argument names the pane ("analyst pane"), default "orchestrator pane".
# The kind is one word (letters, digits, - and _, at most 24 characters) and the pane one short line (letters
# and spaces, at most 30); a blank text, a bad kind or a bad pane is refused with exit 1 and nothing written.
# The register is ROW_REGISTER, else CLAUDE_RULINGS_FILE (the file the recovery hook reads), else
# ~/.claude/rulings.md; the first is what a dry run on a temp file sets. The row is written by python in UTF-8
# with LF, so accents survive and no CR enters the register. Every cut prints a line the failed-call detector
# counts (scripts/tool-errors.rules), so a seat that keeps overshooting the cap shows up in the ledger.
K=${1:?kind}; TXT=${2-}; PANE=${3:-orchestrator pane}
REG=${ROW_REGISTER:-${CLAUDE_RULINGS_FILE:-$HOME/.claude/rulings.md}}
D=$(date "+%Y-%m-%d"); T=$(date "+%H:%M" | cut -c1-4)x
export ROW_K="$K" ROW_TXT="$TXT" ROW_PANE="$PANE" ROW_REG="$REG" ROW_D="$D" ROW_T="$T" PYTHONUTF8=1 PYTHONIOENCODING=utf-8
python - <<'PY'
import os, re, sys
CAP = 400
k, pane, reg, d, tm = (os.environ[x] for x in ("ROW_K", "ROW_PANE", "ROW_REG", "ROW_D", "ROW_T"))
t = re.sub(r"\s+", " ", os.environ["ROW_TXT"]).strip()
if not t:
    print("ROW REFUSED: empty text"); sys.exit(1)
if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,23}", k):
    print(f"ROW REFUSED: bad kind {k[:40]!r} (one word, letters, digits, - or _, at most 24)"); sys.exit(1)
if not re.fullmatch(r"[A-Za-z][A-Za-z ]{0,29}", pane):
    print(f"ROW REFUSED: bad pane {pane[:40]!r} (letters and spaces, at most 30)"); sys.exit(1)
n = len(t)
cut, tail, body = 0, "", t
if n > CAP:
    head = t[:CAP]
    # the last sentence boundary (". " or "; ") inside the cap, never in the first half of it
    ends = [m.end() for m in re.finditer(r"[.;] ", head) if m.end() >= CAP // 2]
    end = ends[-1] if ends else CAP
    body = t[:end].rstrip()
    tail = t[end:].strip()
    cut = n - len(body)
# The leading "- " is the row shape the recovery hook reads (RULING_ROW_RE in claude/hooks/compact-recover.py):
# a row written without it lands in the file and is never printed back to any session.
row = f"- {d} {tm} [{k}] {body}" + (f" [cut {cut}]" if cut else "") + f" ({pane} {tm})"
try:
    with open(reg, "a", encoding="utf-8", newline="\n") as f:
        f.write(row + "\n")
except OSError as e:
    print(f"ROW WRITE FAILED {reg}: {e}"); sys.exit(1)
if cut:
    print(f"ROW ok text {n}/{CAP} CUT {cut} chars, dropped: '{tail}'")
else:
    print(f"ROW ok text {n}/{CAP}")
PY
