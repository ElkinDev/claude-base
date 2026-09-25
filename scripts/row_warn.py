"""row_warn.py: the time check row.sh runs after it writes a register row, and its replay over the register.

A row that names a time in the stamp's own HH:Mx form more than 10 minutes and at most 6 hours ahead of the clock
names a time guessed rather than read (a seat that writes 09:2x at 08:38). row.sh imports
ahead() and prints a ROW WARN line; the row is written as typed. A time right after a date other than the row's own
(09-08 11:3x, 2026-09-24 11:3x) cites another day and is left alone; the row's own date, or a pair that is no
date (08-38), hides nothing.

The replay is the reading a keep line uses: every row of the register in the window, its own stamp
plus 5 minutes as the clock (the stamp keeps only the ten-minute bucket), counted per pane and per day:

    python scripts/row_warn.py --since 2026-09-26 --until 2026-09-26 [--register <file>]

prints one line per day, "row-warn <day>: orchestrator W of N (P pct), analyst W of N (P pct)". A bad date or a since
after the until exits 2 with one REFUSED line; a register that cannot be read exits 3. Serves quality (times written
from the clock). The register is --register, else ROW_REGISTER, else CLAUDE_RULINGS_FILE, else
~/.claude/rulings.md, the same order row.sh writes in.
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta

TIME = re.compile(r"\b([01]\d|2[0-3]):([0-5])x\b")
DATE_BEFORE = re.compile(r"(?:(\d{4})-)?(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\s?$")
ROW = re.compile(r"^(?:- )?(\d{4}-\d\d-\d\d) (\d\d):(\d)x \[[^\]]*\] (.*?)(?: \[cut \d+\])? \((\w+) pane \d\d:\dx\)\s*$")
REGISTER = os.environ.get("ROW_REGISTER") or os.environ.get("CLAUDE_RULINGS_FILE") or os.path.join(
    os.path.expanduser("~"), ".claude", "rulings.md")


def ahead(body, day, now):
    """The HH:Mx times the body names more than 10 and at most 360 minutes ahead of now ("HH:MM"), sorted, each once;
    day is the row's own YYYY-MM-DD."""
    hh, mm = (int(x) for x in now.split(":"))
    clock = hh * 60 + mm
    found = set()
    for m in TIME.finditer(body):
        d = DATE_BEFORE.search(body[max(0, m.start() - 11):m.start()])
        if d and (d.group(2) + "-" + d.group(3) != day[5:] or (d.group(1) and d.group(1) != day[:4])):
            continue  # another day's time
        if 10 < int(m.group(1)) * 60 + int(m.group(2)) * 10 - clock <= 360:
            found.add("%s:%sx" % (m.group(1), m.group(2)))
    return sorted(found)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", required=True, help="YYYY-MM-DD, the first day read")
    ap.add_argument("--until", required=True, help="YYYY-MM-DD, the last day read")
    ap.add_argument("--register", default=REGISTER)
    a = ap.parse_args(argv)
    try:
        lo, hi = (datetime.strptime(x.strip(), "%Y-%m-%d") for x in (a.since, a.until))
    except ValueError:
        print("REFUSED --since and --until take YYYY-MM-DD")
        return 2
    if hi < lo:
        print("REFUSED --since must not be after --until")
        return 2
    days = {}
    try:
        with open(a.register, encoding="utf-8", errors="replace") as f:
            for line in f:
                r = ROW.match(line)
                if not r:
                    continue
                day = r.group(1)
                try:
                    when = datetime.strptime(day, "%Y-%m-%d")
                except ValueError:
                    continue
                if not (lo <= when <= hi):
                    continue
                pane = r.group(5).lower()
                now = "%s:%s5" % (r.group(2), r.group(3))
                hit = bool(ahead(r.group(4), day, now))
                w, n = days.setdefault(day, {}).get(pane, (0, 0))
                days[day][pane] = (w + hit, n + 1)
    except OSError as e:
        print("REFUSED the register %s cannot be read: %s" % (a.register, e))
        return 3
    day = lo
    while day <= hi:
        key = day.strftime("%Y-%m-%d")
        panes = days.get(key, {})
        print("row-warn %s: %s" % (key, ", ".join(
            "%s %d of %d (%s)" % (p, panes.get(p, (0, 0))[0], panes.get(p, (0, 0))[1],
                                  "%.1f pct" % (100.0 * panes[p][0] / panes[p][1]) if panes.get(p, (0, 0))[1] else "n/a")
            for p in ("orchestrator", "analyst"))))
        day += timedelta(days=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
