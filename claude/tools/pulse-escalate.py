"""pulse-escalate.py: the pulse's escalation to the owner decisions file, a ledger step after pulse.py, no agent.

    python pulse-escalate.py              append today's escalation row when one is owed
    python pulse-escalate.py --dry-run    print the row, write nothing
    python pulse-escalate.py --now "2026-09-24 08:00"    the clock, for a probe

A ledger step after pulse.py: an item still flagged after 2 runs goes into the owner decisions file with its class and
deadline, until a decision row cites its pulse:<key>. Serves quality (a flagged item reaches the open asks, which
compact-recover.py prints at every session start) and automation (no agent reads the ledger for it). Not served:
tokens. Number: ledger runs from a key's first appearance to the decision row that cites it, target 1; on the machine
this was built on, the two worst items had waited 12 and 10 runs.

The items are the lines pulse.py logged in ledger/nightly.log (`[stamp] pulse | <item> [pulse:<key>]`), grouped by
run (a run starts at a `] start,` line). Only the runs whose pulse step printed its summary line count, so a run whose
pulse step failed neither flags nor clears an item; the latest ledger run must be one of them, or the pair is stale and
nothing is owed. An item is escalated when its key is in both of the last two such
runs, it is not a RECURRING warning at streak 0 (one absent from the latest ledger run: it stopped, and the head line
counts it as left out), no line of the rulings register cites the key, and no row of today's decisions file names it
yet (the evening run
does not repeat the morning row; an item escalated yesterday and still uncited is escalated again, as an open ask is
carried until it is answered). All of them go into ONE row, each with its class and deadline: twelve rows would fill
the open-asks block of a session start (ASKS_CAP 1,200 characters, last written first) and push the owner's own asks
out of it. Every item is auto class, since each is tooling or ledger state the session can act on (wire, fix, waive or
revert); an item only the owner can act on is restated as its own owner-class row. No deadline is set past 21:15, so a
run after 21:15 writes nothing and says so.

Today's file is owner-decisions-<date>.md in the decisions folder; a missing one is created with the newest earlier
file's first line, its date replaced. A write run holds .pulse-escalate.lock in that folder (a second run at once writes
nothing and says so; a lock over 300 s old is a dead run's and is taken over). Exit 0 when the inputs were read (a row
written, none owed, or another run holding the lock), 1 when the log, the register or today's file cannot be read or
written, or the lock cannot be made, 2 on a usage error.

Environment: PULSE_LOG (default as pulse.py: nightly.log in CLAUDE_LEDGER_DIR, else <root>/ledger/nightly.log, where
the root is PULSE_ROOT, default ~/.claude); PULSE_RULINGS (default CLAUDE_RULINGS_FILE, else ~/.claude/rulings.md);
PULSE_DECISIONS_DIR (default the folder of CLAUDE_DECISIONS_GLOB, else ~/.claude/decisions). The last two defaults
are the ones compact-recover.py reads, so the row this writes is the one a session start prints.
"""
import argparse
import datetime as dt
import glob
import os
import re
import sys
import time

HOME_CLAUDE = os.path.join(os.path.expanduser("~"), ".claude")
ROOT = os.environ.get("PULSE_ROOT") or HOME_CLAUDE
LOG = os.environ.get("PULSE_LOG") or (os.path.join(os.environ["CLAUDE_LEDGER_DIR"], "nightly.log")
                                      if os.environ.get("CLAUDE_LEDGER_DIR") else os.path.join(ROOT, "ledger", "nightly.log"))
RULINGS = (os.environ.get("PULSE_RULINGS") or os.environ.get("CLAUDE_RULINGS_FILE")
           or os.path.join(HOME_CLAUDE, "rulings.md"))
DECISIONS = (os.environ.get("PULSE_DECISIONS_DIR")
             or (os.path.dirname(os.environ["CLAUDE_DECISIONS_GLOB"]) if os.environ.get("CLAUDE_DECISIONS_GLOB") else "")
             or os.path.join(HOME_CLAUDE, "decisions"))
LOCK = os.path.join(DECISIONS, ".pulse-escalate.lock")
LOCK_STALE = 300  # seconds; a run reads two files and appends one row, so an older lock is a dead run's
START = re.compile(r"^\S*\[[^\]]+\] start,")
ITEM = re.compile(r"^\S*\[[^\]]+\] pulse \| (.*?)\s*\[(pulse:[a-z0-9-]+)\]\s*$")
SUMMARY = re.compile(r"^\S*\[[^\]]+\] pulse \| pulse \d{4}-\d{2}-\d{2} \d{2}:\d{2}: mechanisms ")
# A RECURRING warning whose streak is 0 is absent from the latest ledger run: it recurred before and is not happening
# now (the 08:04 run of 2026-09-24 printed OR-56, validated on 09-23, and rows 96 and 98, fixed on 09-23, that way).
# It stays in the pulse block for the recurrence reading, but escalating it would ask for a decision on nothing; if
# it comes back, it is escalated at the next run after that, once its key is in both of the last two runs. The two
# counts are spelled out, so only the header's own streak field matches, never "(streak 0):" quoted in the item's text
# (review plsg note 1).
GONE = re.compile(r"^RECURRING \d+ of the last \d+ \(streak 0\):")
LAST = dt.time(21, 15)
SHOWN = 70  # characters of an item's text kept beside its key
# The words that shut an ask row (compact-recover.py SHUT_ASK_RE): an item text quoting one in capitals would mark
# the whole escalation row as answered, and it would never print among the open asks, so they are written lower case.
SHUT = re.compile(r"\b(DECIDED|DONE|RULED|APPLIED|LAPSED|CLOSED)\b")


def pulse_runs(lines):
    """([({key: text}, stopped)] for each run whose pulse step printed its summary line, oldest first, stopped being
    how many streak-0 items that run left out; whether the latest run printed one). A stale pair must not escalate:
    with the pulse step failing for days, the last two pulse runs would be old ones, so the latest ledger run has to be
    a pulse run itself."""
    runs, cur, stopped, summary, started = [], {}, 0, False, False
    for ln in lines:
        if START.match(ln):
            if summary:
                runs.append((cur, stopped))
            cur, stopped, summary, started = {}, 0, False, True
            continue
        if SUMMARY.match(ln):
            summary = True
            continue
        m = ITEM.match(ln)
        if m and GONE.search(m.group(1)):
            stopped += 1
        elif m:
            cur[m.group(2)] = m.group(1)
    if summary:
        runs.append((cur, stopped))
    return runs, summary and started


def cited(key, text):
    return re.search(re.escape(key) + r"(?![a-z0-9-])", text) is not None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the row, write nothing")
    ap.add_argument("--now", help="the clock, YYYY-MM-DD HH:MM (default: the machine clock)")
    a = ap.parse_args()
    try:
        # an empty --now is a usage error, never the machine clock (S16 H1 probe of 07:4x)
        now = dt.datetime.strptime(a.now.strip(), "%Y-%m-%d %H:%M") if a.now is not None else dt.datetime.now()
    except ValueError:
        ap.error("--now takes YYYY-MM-DD HH:MM, got %r" % a.now)
    if a.dry_run:
        return escalate(a, now)
    got = take_lock(LOCK)
    if got != "held":
        print("escalate: %s; nothing written" % got)
        return 0 if got.startswith("another run") else 1
    try:
        return escalate(a, now)
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def take_lock(path):
    """'held' when this run made the lock file; else why not. Two runs at once would both read today's file before
    either writes, and both append the row; a lock older than LOCK_STALE was left by a run that died and is taken over."""
    for _ in range(2):
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return "held"
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) <= LOCK_STALE:
                    return "another run holds %s" % path.replace("\\", "/")
                os.remove(path)
            except FileNotFoundError:
                continue  # the other run just released it
            except OSError as e:
                return "cannot clear the stale lock %s: %s" % (path.replace("\\", "/"), e)
        except OSError as e:
            return "cannot take the lock %s: %s" % (path.replace("\\", "/"), e)
    return "another run holds %s" % path.replace("\\", "/")


def escalate(a, now):
    try:
        with open(LOG, encoding="utf-8-sig", errors="replace") as fh:
            runs, latest = pulse_runs(fh.read().splitlines())
        with open(RULINGS, encoding="utf-8", errors="replace") as fh:
            register = fh.read()
    except OSError as e:
        print("escalate: cannot read an input: %s" % e)
        return 1
    if len(runs) < 2:
        print("escalate: %d pulse runs in the log, 2 needed; nothing owed" % len(runs))
        return 0
    if not latest:
        print("escalate: the latest ledger run printed no pulse summary, so the last pulse runs are stale; nothing owed")
        return 0
    (last, stopped), before = runs[-1], runs[-2][0]
    both = [k for k in last if k in before]
    day = now.strftime("%Y-%m-%d")
    path = os.path.join(DECISIONS, "owner-decisions-%s.md" % day).replace("\\", "/")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            today = fh.read()
    except FileNotFoundError:
        today = None
    except OSError as e:
        print("escalate: cannot read %s: %s" % (path, e))
        return 1
    uncited = [k for k in both if not cited(k, register)]
    owed = [k for k in uncited if not (today and cited(k, today))]
    head = ("escalate: %d items flagged in both of the last 2 pulse runs, %d stopped (streak 0) left out, %d cited by a "
            "decision row, %d already in %s"
            % (len(both), stopped, len(both) - len(uncited), len(uncited) - len(owed), path))
    if not owed:
        print(head + "; nothing owed")
        return 0
    if now.time() > LAST:
        print(head + "; %d owed, but there is no deadline past 21:15: carried to the next run" % len(owed))
        return 0
    if a.dry_run:
        print(head + "; dry run, the row:")
        print(build_row(owed, last, now).rstrip("\n"))
        return 0
    written = False
    for attempt in (1, 2, 3):
        try:
            if today is None:
                text = first_line(day, path) + "\n\n" + build_row(owed, last, now)
                mode = "x"  # a file made by someone else between the read and this write is never overwritten
            else:
                text = ("" if today.endswith("\n") or not today else "\n") + build_row(owed, last, now)
                mode = "a"
            with open(path, mode, encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            written = True
            break
        except FileExistsError:
            # another session made today's file since the read (review plsf r1 finding 6): read it again and
            # append to it, dropping the keys it now names
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    today = fh.read()
            except FileNotFoundError:
                today = None  # and removed it again before the read: the next pass makes it (plsf r2 note 2)
                continue
            except OSError as e:
                print(head + "; cannot read %s: %s" % (path, e))
                return 1
            owed = [k for k in owed if not cited(k, today)]
            if not owed:
                print(head + "; nothing owed once %s was read again" % path)
                return 0
        except OSError as e:
            print(head + "; cannot write %s: %s" % (path, e))
            return 1
    if not written:
        print(head + "; %s kept appearing and vanishing, nothing written" % path)
        return 1
    print(head + "; wrote 1 row with %d keys to %s" % (len(owed), path))
    return 0


def build_row(owed, last, now):
    """The escalation row. The keys come first: a session start clips each ask row to ASK_CLIP 200 characters
    (compact-recover.py), and a row that opened with its boilerplate showed no key at all (review plsf r1 finding 1)."""
    items = []
    for k in owed:
        text = SHUT.sub(lambda m: m.group(1).lower(), " ".join(last[k].split()))
        items.append("%s (%s)" % (k, text if len(text) <= SHOWN else text[:SHOWN - 3] + "..."))
    return ("- %sx Pulse escalation, %d keys with no decision row after 2 ledger runs: %s. Auto class each: with no "
            "answer by 21:15 the recommended action runs (a decision row today citing the key: wire, fix, waive or "
            "revert), veto open; an item only the owner can act on becomes its own owner-class row "
            "(pulse-escalate.py, ledger run %s). Items: %s.\n"
            % (now.strftime("%H:%M")[:-1], len(owed), ", ".join(owed), now.strftime("%H:%M"), "; ".join(items)))


def first_line(day, path):
    """The header of a new day file: the newest earlier file's first line with its date replaced, else a plain one."""
    header = "# Owner decisions, %s" % day
    earlier = sorted(p for p in glob.glob(os.path.join(DECISIONS, "owner-decisions-*.md"))
                     if re.search(r"owner-decisions-(\d{4}-\d{2}-\d{2})\.md$", p.replace("\\", "/"))
                     and os.path.basename(p) < os.path.basename(path))
    if earlier:
        with open(earlier[-1], encoding="utf-8", errors="replace") as fh:
            first = fh.readline().rstrip("\r\n")
        old = re.search(r"owner-decisions-(\d{4}-\d{2}-\d{2})\.md$", earlier[-1].replace("\\", "/")).group(1)
        if first.startswith("# ") and old in first:
            header = first.replace(old, day, 1)
    return header


if __name__ == "__main__":
    sys.exit(main())
