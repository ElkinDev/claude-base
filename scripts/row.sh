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
# A text naming an HH:Mx time 10 minutes to 6 hours ahead of the clock is written as typed and answered with a
# ROW WARN line, which the detector counts too; the check is row_warn.py beside this script, whose replay over
# the register is the reading of how often it happens, and a time right after another day's date (09-08 11:3x)
# is left alone. The warning never changes the exit status, and a missing row_warn.py only skips it.
# ROW_CLOCK="YYYY-MM-DD HH:MM" stands in for the clock only when ROW_REGISTER is set too (a dry run), so a stray
# export can never stamp the real register; a clock out of that shape or range is refused before anything is
# written. One date call gives both the date and the stamp.
K=${1:?kind}; TXT=${2-}; PANE=${3:-orchestrator pane}
REG=${ROW_REGISTER:-${CLAUDE_RULINGS_FILE:-$HOME/.claude/rulings.md}}
if [ -n "$ROW_REGISTER" ] && [ -n "$ROW_CLOCK" ]; then NOW=$ROW_CLOCK; else NOW=$(date "+%Y-%m-%d %H:%M"); fi
if ! printf '%s' "$NOW" | grep -Eq '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01]) ([01][0-9]|2[0-3]):[0-5][0-9]$'; then
  echo "ROW REFUSED: bad clock '$NOW' (YYYY-MM-DD HH:MM)"; exit 1
fi
D=${NOW% *}; T=$(printf '%s' "${NOW#* }" | cut -c1-4)x
ROW_DIR=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && { pwd -W 2>/dev/null || pwd; })  # this file's folder, sourced or run
export ROW_K="$K" ROW_TXT="$TXT" ROW_PANE="$PANE" ROW_REG="$REG" ROW_D="$D" ROW_T="$T" ROW_NOW="${NOW#* }" ROW_DIR PYTHONUTF8=1 PYTHONIOENCODING=utf-8
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
# A time the text names in the stamp's own HH:Mx form, more than 10 minutes and at most 6 hours ahead of the clock,
# is a time guessed rather than read (row_warn.py). The row is written as typed; the warning goes back to the seat
# and the failed-call detector counts it (tool-errors.rules).
try:
    import importlib.util
    if not os.path.isabs(os.environ.get("ROW_DIR", "")):
        raise RuntimeError("no folder for row_warn.py")  # an empty or relative ROW_DIR would load from the caller's folder
    spec = importlib.util.spec_from_file_location("row_warn", os.path.join(os.environ["ROW_DIR"], "row_warn.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # by its path beside row.sh, never from the caller's folder
    ahead = mod.ahead(body, d, os.environ["ROW_NOW"])
    if ahead:
        print(f"ROW WARN the text names {', '.join(ahead)}, ahead of the clock {os.environ['ROW_NOW']}: run date before "
              f"writing a time (a deadline in that form may stand)")
except Exception:
    pass  # the row is already written; a warning never changes the exit status
PY
