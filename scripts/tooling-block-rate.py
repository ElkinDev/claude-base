"""tooling-block-rate.py: BLOCK reviews per tooling change over a window of review files, with no agent.

    python tooling-block-rate.py <first day> <day after the last>      e.g. 2026-01-05 2026-01-09
    python tooling-block-rate.py --last-complete [--today YYYY-MM-DD]   the newest complete anchored window

The proving number of the hostile-input practice (the probe table of the lane brief template's Report section):
BLOCK review files on tooling slugs per tooling slug reviewed, over the review files of <root>/reviews whose modified
time falls in the window (the first day from 00:00 inclusive, the day after the last exclusive). A slug is a review
file's name without its YYYY-MM-DD date and its -rN or -fixN round. A slug counts as tooling when any of its review
files names a script (scripts/ or a .py, .ps1, .sh or .ts file) in its first 8 lines, does not name drafts/ there,
and does not say plan or proposal(s) on line 1. A file's verdict is the CLEAR or BLOCK of its Disposition or
Verdict line in the first 12 lines (or on the line after a bare Disposition heading), else of the last such line of
the file, else the last CLEAR or BLOCK word of lines 1 to 3.

Windows. --last-complete reads the newest window of --window-days days, counted from the anchor, whose last day is
before today. A window counts only with --min-slugs tooling slugs or more; a smaller one prints "carries", and
--last-complete then reads it together with the next one, dates widened. A window that starts before the first
decision day (the first anchored window wholly after the practice was adopted) prints "before the practice" and is
never counted. The keep rule the number serves: the practice stays only if the number is at or under the keep
threshold in each of the next two counted windows; with no threshold the line says "counted" and no verdict. Measure
a baseline window before adopting the practice and set the threshold from it (half the baseline is a fair start).

Settings, each a flag or its variable (the flag wins):
  --root            TOOLING_BLOCK_ROOT, else EVIDENCE_ROOT, else the working directory; the reviews folder is under it
  --anchor          TOOLING_BLOCK_ANCHOR, the first day of an anchored window; default the first decision day
  --first-decision  TOOLING_BLOCK_FIRST_DECISION, a day on the anchor's grid; default none, every window can count
  --window-days     TOOLING_BLOCK_WINDOW_DAYS, default 4
  --min-slugs       TOOLING_BLOCK_MIN_SLUGS, default 15
  --keep-at         TOOLING_BLOCK_KEEP_AT, a number such as 0.5; default none

Prints one line starting "tooling-block-rate " for a ledger to keep. Exit 0, 2 on a usage error. Serves automation
(a scheduled task reads the number, no agent) and quality (it decides whether the practice stays).
"""
import argparse
import collections
import datetime as dt
import glob
import os
import re
import sys

VERDICT_RE = re.compile(r"\b(CLEAR|BLOCK)\b")


def head(path, n):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            return f.read().splitlines()[:n]
    except OSError:
        return []


def verdict_of_lines(lines, i):
    text = lines[i]
    if not VERDICT_RE.search(text):
        nxt = [x for x in lines[i + 1:] if x.strip()]
        text = nxt[0] if nxt else ""
    m = VERDICT_RE.search(text)
    return m.group(1) if m else None


def review_verdict(path):
    """CLEAR or BLOCK from the Disposition or Verdict line of the first 12 lines (a bare verdict line counts), else
    from the last such line of the file, else None."""
    first = head(path, 12)
    for i, line in enumerate(first):
        low = line.lower()
        if "disposition" in low or "verdict" in low or line.lstrip("# *").startswith(("CLEAR", "BLOCK")):
            return verdict_of_lines(first, i)
    lines = head(path, 10 ** 6)
    idx = [i for i, x in enumerate(lines) if "disposition" in x.lower() or "verdict" in x.lower()]
    return verdict_of_lines(lines, idx[-1]) if idx else None


def slug(path):
    base = os.path.basename(path)[:-3]
    return re.sub(r"-(r\d+|fix\d+[a-z]?)(?=-|$)", "", re.sub(r"-\d{4}-\d\d-\d\d", "", base))


def is_tooling(path):
    h = head(path, 8)
    text = "\n".join(h)
    return (bool(re.search(r"(scripts/|\b[\w.-]+\.(py|ps1|sh|ts)\b)", text)) and "drafts/" not in text
            and not re.search(r"\b(plan|proposals?)\b", h[0] if h else "", re.I))


def rate(root, lo, hi):
    files = []
    for f in glob.glob(os.path.join(root, "reviews", "*.md")):
        try:
            if os.path.isfile(f) and lo <= dt.datetime.fromtimestamp(os.path.getmtime(f)) < hi:
                files.append(f)
        except OSError:
            continue
    files.sort()

    def verdict(f):
        return review_verdict(f) or (VERDICT_RE.findall(" ".join(head(f, 3))) or [None])[-1]

    verdicts = {f: verdict(f) for f in files}
    by = collections.defaultdict(list)
    for f in files:
        by[slug(f)].append(f)
    tooling = {s for s, fs in by.items() if any(is_tooling(f) for f in fs)}
    tool_blocks = [f for s in tooling for f in by[s] if verdicts[f] == "BLOCK"]
    blocks = sum(1 for v in verdicts.values() if v == "BLOCK")
    return len(files), blocks, len(tooling), len(tool_blocks)


def day(text):
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError("a day is YYYY-MM-DD, got %r" % text)


def last_complete(today, anchor, days):
    """The newest anchored window whose last day is before today."""
    k = (today - anchor).days // days  # index of the window holding today
    start = anchor + dt.timedelta(days=days * (k - 1))
    return dt.datetime.combine(start, dt.time()), dt.datetime.combine(start + dt.timedelta(days=days), dt.time())


def decision_segment(root, today, first, anchor, days, min_slugs):
    """From the first decision day on, walk the complete anchored windows in order; a window under min_slugs
    carries, so the next one is read from the carried window's first day. Returns the segment that ends at the
    newest complete window: (lo, hi), lo the first day of the carry run."""
    _, hi_last = last_complete(today, anchor, days)
    seg_lo = dt.datetime.combine(first, dt.time())
    end = seg_lo + dt.timedelta(days=days)
    while end < hi_last:
        if rate(root, seg_lo, end)[2] >= min_slugs:
            seg_lo = end
        end += dt.timedelta(days=days)
    return seg_lo, hi_last


def settings(ap, a):
    """Fill every setting from its flag, else its variable, else its default; a bad value is a usage error."""
    env = os.environ.get

    def pick(flag, var, parse, default):
        if flag is not None:
            return flag
        raw = (env(var) or "").strip()
        if not raw:
            return default
        try:
            return parse(raw)
        except (ValueError, argparse.ArgumentTypeError):
            ap.error("%s=%r is not a valid value" % (var, raw))

    a.root = a.root or env("TOOLING_BLOCK_ROOT") or env("EVIDENCE_ROOT") or os.getcwd()
    a.first_decision = pick(a.first_decision, "TOOLING_BLOCK_FIRST_DECISION", day, None)
    a.anchor = pick(a.anchor, "TOOLING_BLOCK_ANCHOR", day, None) or a.first_decision
    a.window_days = pick(a.window_days, "TOOLING_BLOCK_WINDOW_DAYS", int, 4)
    a.min_slugs = pick(a.min_slugs, "TOOLING_BLOCK_MIN_SLUGS", int, 15)
    a.keep_at = pick(a.keep_at, "TOOLING_BLOCK_KEEP_AT", float, None)
    if a.window_days < 1 or a.min_slugs < 1 or (a.keep_at is not None and a.keep_at < 0):
        ap.error("the window days and the slug floor are 1 or more, and the keep threshold is 0 or more")
    if a.first_decision and a.anchor and (a.first_decision - a.anchor).days % a.window_days:
        ap.error("the first decision day %s is not on the grid of %d-day windows from the anchor %s" % (
            a.first_decision.date(), a.window_days, a.anchor.date()))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lo", nargs="?", type=day, help="first day, YYYY-MM-DD, inclusive from 00:00")
    ap.add_argument("hi", nargs="?", type=day, help="the day after the last, YYYY-MM-DD, exclusive")
    ap.add_argument("--last-complete", action="store_true", help="the newest complete anchored window")
    ap.add_argument("--today", type=day, help="read --last-complete as of this day (default: the machine date)")
    ap.add_argument("--root", help="the folder holding reviews/")
    ap.add_argument("--anchor", type=day)
    ap.add_argument("--first-decision", type=day)
    ap.add_argument("--window-days", type=int)
    ap.add_argument("--min-slugs", type=int)
    ap.add_argument("--keep-at", type=float)
    a = ap.parse_args(argv)
    settings(ap, a)
    first = a.first_decision.date() if a.first_decision else None
    if a.last_complete:
        if a.lo or a.hi:
            ap.error("give either two days or --last-complete, not both")
        if not a.anchor:
            ap.error("--last-complete needs an anchor or a first decision day")
        anchor = a.anchor.date()
        today = (a.today or dt.datetime.now()).date()
        if today < anchor + dt.timedelta(days=a.window_days):
            ap.error("no complete window before %s" % (anchor + dt.timedelta(days=a.window_days)))
        lo, hi = last_complete(today, anchor, a.window_days)
        if lo.date() >= (first or anchor):
            lo, hi = decision_segment(a.root, today, first or anchor, anchor, a.window_days, a.min_slugs)
    else:
        if not (a.lo and a.hi):
            ap.error("give the first day and the day after the last, or --last-complete")
        lo, hi = a.lo, a.hi
        if lo >= hi:
            ap.error("the first day must be before the day after the last")
    n, blocks, slugs, tool_blocks = rate(a.root, lo, hi)
    per = tool_blocks / max(slugs, 1)
    if first and lo.date() < first:
        state = "before the practice (its first decision window starts %s)" % first.isoformat()
    elif slugs < a.min_slugs:
        state = "carries (under %d tooling slugs)" % a.min_slugs
    elif a.keep_at is None:
        state = "counted"
    else:
        state = "counted, %s the keep threshold %.2f" % ("at or under" if per <= a.keep_at else "over", a.keep_at)
    print("tooling-block-rate %s to %s: review files %d, BLOCK %d, tooling slugs %d, tooling BLOCK files %d, per slug "
          "%.2f, %s" % (lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d"), n, blocks, slugs, tool_blocks, per, state))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
