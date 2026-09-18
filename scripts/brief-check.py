"""brief-check.py: the tiered brief rule, read on a lane brief and nothing else.

Five tiers (the launch hook's deny tier ranges over 1 to 3; tiers 4 and 5 are measured and reported as would-deny):
  tier 1  a section whose heading contains Report or Deliverable
  tier 2  a test identifier (an identifier ending in Test) or the word golden, anywhere in the brief
  tier 3  a test class named in a section whose heading contains Pins, Rule or Law, or in that heading
  tier 4  a budget with a number and a unit anywhere in the brief: "Budget 40 tool uses", "budget: 60 turns".
          A date, a time or a section number after the word is not a budget, and a heading holding the word
          proves nothing, so the number and its unit are what the rule reads.
  tier 5  the end-your-turn line: the brief says the lane ends its turn (end your turn, stop your turn) within
          two lines of naming a .done or .exit path or a detached launch. Read only on a brief that names a
          detached build run; a review, bench or reading brief has no run to end a turn on, prints T5=n/a and
          stays out of the tier 5 denominator, because an unscoped share measures reading volume and nothing else.

The deny tier is not chosen, it is measured: grade the launches the hook logged with --from-log over
hooks/brief-launches.log (distinct briefs, in log order) and set it to the highest tier at least 80 percent of
them pass, tier 1 at the start and 0 on a machine whose log is still empty. The tiers above it are logged as
would-deny, which is how the next raise is paid for. Budget, Forbidden, Why and Checks that a brief template
recommends are never checked here.

Exit codes: 0 when the deny tier passes, 1 when it fails (the missing element on stdout), 2 when the
file cannot be read (a hook treats 2 as pass; a checker failure never blocks a launch).

Usage:
  python brief-check.py <brief.md> [--deny-tier N]     one brief; prints "T1 T2 T3 T4 T5" verdicts and the result
  python brief-check.py --corpus <glob> [--deny-tier N] every matching brief, one line each, then the shares
  python brief-check.py --from-log <log> [--deny-tier N] every distinct brief the hook logged, one line each, then the shares
"""
import glob
import re
import sys

TEST_ID = re.compile(r"\b[A-Z][A-Za-z0-9]*Test\b")
GOLDEN = re.compile(r"\bgolden", re.I)
HEADING = re.compile(r"^#{1,4}\s*(.+?)\s*$")
# tier 4: the word budget, at most 12 non-digit chars, a 1 to 4 digit number and a unit (tool uses, tool calls, turns, uses, calls)
BUDGET = re.compile(r"\bbudget\b[^.\n\d]{0,12}?\b(\d{1,4})\s*(?:tool[ -]?(?:uses|calls)|turns|uses|calls)\b", re.I)
# tier 5: "end your turn" (or stop your turn) and, within the same or the next two lines, a .done or .exit path or a
# detached launch; the two orders are accepted (the line may name the path first)
END_TURN = re.compile(r"\b(?:end|stop|ends|stops)\s+(?:your|the|its)\s+turn\b", re.I)
DETACHED = re.compile(r"\.done\b|\.exit\b|gate-detach|gate\.py\s+launch|\bdetached\b", re.I)
# tier 5 applies to a brief that names a detached build run; the others print n/a and stay out of the denominator
BUILD_STEP = re.compile(r"gradle-lockrun|gate-detach|lane-gate\.sh|lockrun\.ps1|gate\.py\s+launch", re.I)


def sections(text):
    """[(heading, body)] with a leading ('', preamble) entry."""
    out = []
    head = ""
    buf = []
    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            out.append((head, "\n".join(buf)))
            head, buf = m.group(1), []
        else:
            buf.append(line)
    out.append((head, "\n".join(buf)))
    return out


def tiers(path):
    """Returns (t1, t2, t3, t4, t5) booleans, or None when unreadable."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    secs = sections(text)
    t1 = any(re.search(r"\b(report|deliverable)\b", h, re.I) for h, _ in secs)
    t2 = bool(TEST_ID.search(text) or GOLDEN.search(text))
    pin = [(h, b) for h, b in secs if re.search(r"\b(pins?|rule|law)\b", h, re.I)]
    t3 = any(TEST_ID.search(h) or TEST_ID.search(b) for h, b in pin)
    # tier 4: a budget with a number and a unit anywhere in the brief; no section rule, because the H1 title is a
    # heading whose body is the whole preamble, so a title holding "budget" passed on any digit below it
    t4 = bool(BUDGET.search(text))
    # tier 5: the end-your-turn line, in a window of three lines, so "END YOUR TURN with the .done path" and a
    # sentence split over two lines both pass
    lines = text.splitlines()
    t5 = False
    for i, line in enumerate(lines):
        if END_TURN.search(line):
            window = "\n".join(lines[max(0, i - 2):i + 3])
            if DETACHED.search(window):
                t5 = True
                break
    if not BUILD_STEP.search(text):
        t5 = None  # not applicable: no detached run to end a turn on
    return t1, t2, t3, t4, t5


MISSING = {
    1: "no Report or Deliverable section",
    2: "no test identifier (ending in Test) and no golden named anywhere",
    3: "no test class named in a Pins, Rule or Law section or its heading",
    4: "no budget with a number and a unit (Budget N tool uses, or N turns)",
    5: "no end-your-turn line naming a .done or .exit path or a detached launch within two lines",
}


def verdict(t, deny_tier):
    """(code, line) for one brief at the given deny tier."""
    if t is None:
        return 2, "SKIP unreadable"
    marks = " ".join(f"T{i + 1}={'n/a' if v is None else ('ok' if v else 'miss')}" for i, v in enumerate(t))
    fails = [i + 1 for i, v in enumerate(t) if v is False and i + 1 <= deny_tier]
    if fails:
        return 1, f"DENY {marks}: " + "; ".join(MISSING[i] for i in fails)
    return 0, f"PASS {marks}"


def main(argv):
    deny_tier = 1
    if "--deny-tier" in argv:
        i = argv.index("--deny-tier")
        deny_tier = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if argv and argv[0] in ("--corpus", "--from-log"):
        if len(argv) != 2:
            print("usage: brief-check.py --corpus <glob> | --from-log <log> [--deny-tier N]")
            return 2
        if argv[0] == "--corpus":
            files = sorted(glob.glob(argv[1]))
            label = "corpus"
        else:
            files = []
            try:
                with open(argv[1], encoding="utf-8", errors="replace") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 4 and parts[3] not in ("NONE", "UNREADABLE", "UNPARSED") and parts[3] not in files:
                            files.append(parts[3])
            except OSError:
                print(f"cannot read {argv[1]}")
                return 2
            label = "hook log"
        if not files:
            print(f"no files from {argv[1]}")
            return 2
        passes = [0, 0, 0, 0, 0]
        detached_briefs = 0
        deny = 0
        for fp in files:
            t = tiers(fp)
            code, line = verdict(t, deny_tier)
            deny += code == 1
            if t:
                for i, v in enumerate(t):
                    passes[i] += bool(v)
                detached_briefs += t[4] is not None
            print(f"{line} {fp.replace(chr(92), '/').rsplit('/', 1)[-1]}")
        n = len(files)
        print(f"{label} {n} briefs: tier 1 pass {passes[0]}, tier 2 pass {passes[1]}, tier 3 pass {passes[2]}, tier 4 pass {passes[3]}, tier 5 pass {passes[4]} of {detached_briefs} detached-run briefs; "
              f"deny tier {deny_tier} would deny {deny} ({(100 * deny // n) if n else 0} percent)")
        return 0
    if len(argv) != 1:
        print(__doc__)
        return 2
    code, line = verdict(tiers(argv[0]), deny_tier)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
