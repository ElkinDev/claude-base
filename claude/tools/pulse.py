"""pulse.py: which adopted mechanisms are used, silent or dark, and which ledger warnings stand, with no agent.

    python pulse.py                 the block: one summary line, then only what is not fine
    python pulse.py --max 10        the block cut to 10 lines, for a session start
    python pulse.py --hours 24      the window a mechanism's trigger and use are counted in (default 24)

A mechanism that is adopted and never read goes dark without anyone noticing: on the machine this was built on, a
route of UI flows ran 0 times in 12 ledger runs before a person saw it. A ledger warning printed run after run reads
like new each time. The pulse names both at the next run, from files on disk. Serves quality (a silence is seen at
the next run) and automation (no agent reads it). Not served: tokens. Numbers: flagged warnings, target 0; ledger runs
from a key's first appearance to the decision row that cites it, target 1 (pulse-escalate.py writes the ask).

Mechanisms come from pulse.md in the evidence root, one line per mechanism, fields split on " | ":
    id | principle | ruling | trigger | use | number command | keep line | review date (YYYY-MM-DD)
A number command that holds " | " is written in backticks; no other field may hold one, and a line with any other
extra field is MALFORMED. A
trigger or a use is a glob under the root (an absolute path also works; ** matches any depth), optionally followed by
" !<glob>" to leave matches out; a glob ending in "/" counts folders only. Both are counted by mtime inside the
window. The use must be what the mechanism's reading or action leaves on disk, never a log its hook writes on its
own. A use of "none" means the mechanism leaves nothing to read yet. Status, one per mechanism:
    ALIVE   the use happened in the window           (fine, not printed in the block)
    IDLE    no trigger and no use in the window      (fine, not printed)
    SILENT  the trigger happened and the use did not
    DARK    the use is "none": nothing on disk says the mechanism ran
    NOMATCH the trigger or the use glob matches no path at any time: a typo, or a use that never happened
    DUE     the review date is reached; the number command and the keep line are printed for the seat to run
            (this version never runs a command from the register)

Warnings come from ledger/nightly.log: a `reports |` line, or a `meters |` line saying `no line appended`. Runs are
cut at each `] start,` line. For each warning text: its streak (consecutive runs ending at the last run) and its
recurrence (runs of the last 14 that carry it). STANDING is a streak of 3 or more; RECURRING is 3 or more of the last
14 with a streak under 3 (the section 1B reading of the draft).

Every item carries a key, so a decision row can cite it and a grep can join the two files: pulse:<id> for a
mechanism, pulse:w-<8 hex> for a warning (sha1 of its text), pulse:register-<8 hex> for a malformed register line
(sha1 of the line), pulse:<id> for a line whose fields parse and whose review date does not, pulse:register-missing
and pulse:log-missing. The "... N more" line of --max carries none. Exit 0 when every input was read; 1 when an input is
missing or a register line is malformed (the block still prints and names it); 2 on a usage error.

Environment: PULSE_ROOT, the evidence root the globs are read under (default ~/.claude); PULSE_REGISTER (default
<root>/pulse.md); PULSE_LOG (default nightly.log in CLAUDE_LEDGER_DIR, the folder the kit's ledger-nightly.ps1 writes
to when set, else <root>/ledger/nightly.log). The ledger task passes its own log as PULSE_LOG.
"""
import argparse
import datetime as dt
import glob
import hashlib
import os
import re
import sys
import time

ROOT = os.environ.get("PULSE_ROOT") or os.path.join(os.path.expanduser("~"), ".claude")
REGISTER = os.environ.get("PULSE_REGISTER") or os.path.join(ROOT, "pulse.md")
LOG = os.environ.get("PULSE_LOG") or (os.path.join(os.environ["CLAUDE_LEDGER_DIR"], "nightly.log")
                                      if os.environ.get("CLAUDE_LEDGER_DIR") else os.path.join(ROOT, "ledger", "nightly.log"))
SELF = os.path.abspath(__file__).replace("\\", "/")
FIELDS = 8
RECENT = 14
START = re.compile(r"\] start,")
STAMP = re.compile(r"^\[[^]]*\] ")


def count(spec, since):
    """(paths in the window, paths at any time) the spec names; a spec is '<glob>[ !<glob>]'."""
    want, _, drop = spec.partition(" !")
    folders = want.rstrip().endswith("/")

    def expand(g):
        g = g.strip()
        base = g if os.path.isabs(g) else os.path.join(ROOT, g)
        return set(os.path.normcase(os.path.normpath(p)) for p in glob.glob(base, recursive=True))
    hits = expand(want) - (expand(drop) if drop.strip() else set())
    n = total = 0
    for p in hits:
        try:
            if folders and not os.path.isdir(p):
                continue
            total += 1
            if os.path.getmtime(p) >= since:
                n += 1
        except OSError:
            continue  # gone between the glob and the stat
    return n, total


def mechanisms(since, today):
    """(items, counts, problems): items are (status, line) for what is not fine."""
    try:
        with open(REGISTER, encoding="utf-8-sig") as fh:
            lines = fh.read().splitlines()
    except OSError as e:
        return [], {}, ["pulse: cannot read the register %s (%s) [pulse:register-missing]" % (REGISTER, e.strerror or e)]
    items, counts, problems, seen = [], {}, [], set()
    for no, ln in enumerate(lines, 1):
        if not ln.strip() or ln.lstrip().startswith("#") or ln.startswith("id | "):
            continue
        f = [x.strip() for x in ln.split(" | ")]
        if len(f) > FIELDS:
            # a number command holding " | " sits in backticks; any other extra field is ambiguous, so it is loud
            # (review plse r2 note 6: a pipe in the keep line used to shift silently into the command)
            rest = " | ".join(f[5:])
            close = rest.find("`", 1) if rest.startswith("`") else -1
            tail = rest[close + 1:].split(" | ") if close > 0 else []
            f = f[:5] + [rest[:close + 1]] + [x.strip() for x in tail[1:]] if len(tail) == 3 and not tail[0] else f
        if len(f) != FIELDS or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", f[0]) or f[0] in seen:
            # keyed by the line's text, so the key holds when a line is inserted above it (review plse r1 finding 5)
            problems.append("MALFORMED register line %d: %d fields, id %r [pulse:register-%s]" % (
                no, len(f), f[0], hashlib.sha1(ln.strip().encode("utf-8")).hexdigest()[:8]))
            continue
        seen.add(f[0])
        mid, trigger, use, keep, review = f[0], f[3], f[4], f[6], f[7]
        number = f[5][1:-1] if len(f[5]) > 1 and f[5].startswith("`") and f[5].endswith("`") else f[5]
        try:
            due = dt.date.fromisoformat(review) <= today
        except ValueError:
            problems.append("MALFORMED register line %d: review date %r [pulse:%s]" % (no, review, mid))
            continue
        if use.lower() == "none":
            status, note = "DARK", "no use evidence named, nothing on disk says it ran"
        else:
            (t, t_all), (u, u_all) = count(trigger, since), count(use, since)
            if not t_all or not u_all:  # a glob no path ever matched: a typo, or a use that never happened (finding 2)
                status = "NOMATCH"
                note = "no path matches the %s glob at any time" % " or the ".join(
                    x for x, n in (("trigger", t_all), ("use", u_all)) if not n)
            else:
                status = "ALIVE" if u else "SILENT" if t else "IDLE"
                note = "trigger %d, use %d" % (t, u)
        counts[status] = counts.get(status, 0) + 1
        if status in ("SILENT", "DARK", "NOMATCH"):
            items.append((status, "%s %s: %s [pulse:%s]" % (status, mid, note, mid)))
        if due:
            counts["DUE"] = counts.get("DUE", 0) + 1
            items.append(("DUE", "DUE %s since %s: run `%s`, keep if %s [pulse:%s]" % (mid, review, number, keep, mid)))
    return items, counts, problems


def warnings():
    """(items, standing, recurring, texts, runs) or None when the log cannot be read."""
    try:
        with open(LOG, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    runs, seen = 0, {}
    for ln in lines:
        if START.search(ln):
            runs += 1
        if not runs:
            continue
        t = STAMP.sub("", ln, count=1)
        if t.startswith("reports | ") or (t.startswith("meters | ") and "no line appended" in t):
            seen.setdefault(t, set()).add(runs)
    items, standing, recurring = [], 0, 0
    for t, at in seen.items():
        streak = 0
        while runs - streak in at:
            streak += 1
        recent = sum(1 for r in range(runs - RECENT + 1, runs + 1) if r in at)
        key = "pulse:w-" + hashlib.sha1(t.encode("utf-8")).hexdigest()[:8]
        shown = t if len(t) <= 120 else t[:117] + "..."  # the key stays on the line; it hashes the whole text
        if streak >= 3:
            standing += 1
            items.append((-streak, -recent, "STANDING %d runs (%d of the last %d): %s [%s]" % (
                streak, recent, RECENT, shown, key)))
        elif recent >= 3:
            recurring += 1
            items.append((-streak, -recent, "RECURRING %d of the last %d (streak %d): %s [%s]" % (
                recent, RECENT, streak, shown, key)))
    items.sort(key=lambda x: (x[0], x[1], x[2]))
    return [x[2] for x in items], standing, recurring, len(seen), runs


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max", type=int, default=0, help="cut the block to this many lines (0: no cut)")
    ap.add_argument("--hours", type=float, default=24.0, help="the window for triggers and uses")
    ap.add_argument("--if-set", action="store_true",
                    help="print nothing and exit 0 when the register does not exist (a session start on a machine "
                         "that never set up a pulse)")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if a.max < 0 or a.max == 1 or not a.hours > 0:
        print("pulse: --max takes 0 (no cut) or 2 or more, and --hours more than 0", file=sys.stderr)
        return 2
    # The installer puts this script in ~/.claude/tools on every machine; one that never wrote a register must not
    # see a register-missing line at every session start (review kit-twins-0924a r1 finding 1)
    if a.if_set and not os.path.isfile(REGISTER):
        return 0
    # ASCII only, anything else as a backslash escape: no code page kills the block, and the ledger step's reader
    # (Get-Content with the ANSI default) cannot garble what it logs (review plsl r1 finding 2)
    sys.stdout.reconfigure(encoding="ascii", errors="backslashreplace")
    now = time.time()
    since = now - a.hours * 3600
    today = dt.date.fromtimestamp(now)
    mitems, counts, problems = mechanisms(since, today)
    w = warnings()
    if w is None:
        # a session start runs this with no PULSE_LOG, so the log it finds is CLAUDE_LEDGER_DIR's or ~/.claude's; a
        # ledger kept in the kit clone is found only when CLAUDE_LEDGER_DIR names it (review kit-twins-0924a r1 2)
        problems.append("pulse: cannot read the ledger log %s (set CLAUDE_LEDGER_DIR to the folder the ledger writes), "
                        "warnings not read [pulse:log-missing]" % LOG)
        witems, standing, recurring, texts, runs = [], 0, 0, 0, 0
    else:
        witems, standing, recurring, texts, runs = w
    total = sum(v for k, v in counts.items() if k != "DUE")
    head = ("pulse %s: mechanisms %d (%s), window %g h since %s; warnings flagged %d (standing %d, recurring %d) of "
            "%d read over %d runs" % (
                dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"), total,
                ", ".join("%s %d" % (k.lower(), counts.get(k, 0))
                          for k in ("ALIVE", "SILENT", "IDLE", "DARK", "NOMATCH", "DUE")),
                a.hours, dt.datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M"), standing + recurring, standing,
                recurring, texts, runs))
    body = problems + [x[1] for x in mitems] + witems
    if a.max and len(body) > a.max - 1:
        keep = max(a.max - 2, 0)
        body = body[:keep] + ["... %d more: python %s" % (len(body) - keep, SELF)]
    sys.stdout.write("\n".join([head] + body) + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
