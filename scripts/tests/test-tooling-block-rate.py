"""Pins for scripts/tooling-block-rate.py: the slug, tooling and verdict rules, the window by modified time, the
slug floor, the anchored window of --last-complete with its carry, the first decision day and the keep threshold as
settings, and the usage errors.

    python scripts/tests/test-tooling-block-rate.py
    TOOLING_BLOCK_RATE_PY=<path> python scripts/tests/test-tooling-block-rate.py

Every case runs the script as a subprocess on a temp reviews folder (TOOLING_BLOCK_ROOT) with the anchor, the first
decision day and the keep threshold given as variables; nothing real is read.
"""
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("TOOLING_BLOCK_RATE_PY") or os.path.join(HERE, "..", "tooling-block-rate.py")
SETTINGS = dict(TOOLING_BLOCK_ANCHOR="2026-09-15", TOOLING_BLOCK_FIRST_DECISION="2026-09-23", TOOLING_BLOCK_KEEP_AT="0.54")
DAY = dt.datetime(2026, 9, 24, 12, 0)


def run(args, root, **over):
    env = {k: v for k, v in os.environ.items() if not k.startswith("TOOLING_BLOCK_") and k != "EVIDENCE_ROOT"}
    env.update(SETTINGS, TOOLING_BLOCK_ROOT=root)
    env.update(over)
    env = {k: v for k, v in env.items() if v is not None}
    r = subprocess.run([sys.executable, SCRIPT] + args, capture_output=True, text=True, timeout=120, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def write(root, name, verdict, head_line, when=DAY):
    path = os.path.join(root, "reviews", name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("Disposition: %s\n\n%s\n" % (verdict, head_line))
    t = when.timestamp()
    os.utime(path, (t, t))
    return path


def numbers(out):
    m = re.search(r"review files (\d+), BLOCK (\d+), tooling slugs (\d+), tooling BLOCK files (\d+), per slug ([\d.]+)", out)
    return tuple(int(x) for x in m.groups()[:4]) + (float(m.group(5)),) if m else None


def main():
    results = []

    def check(name, fn):
        tmp = tempfile.mkdtemp(prefix="tbr-pin-")
        os.makedirs(os.path.join(tmp, "reviews"))
        try:
            ok = bool(fn(tmp))
            note = ""
        except Exception as e:
            ok, note = False, " (%s: %s)" % (type(e).__name__, str(e)[:90])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        results.append(ok)
        print(("OK   " if ok else "FAIL ") + name + note)

    def rules(root):
        write(root, "gate-py-2026-09-24.md", "BLOCK (1 MAJOR)", "Review of scripts/gate.py at abc1234.")
        write(root, "gate-py-2026-09-24-r2.md", "CLEAR", "Round 2 of scripts/gate.py.")
        write(root, "row-sh-fix1-2026-09-24.md", "BLOCK (2 MAJOR)", "Review of row.sh at def5678.")
        write(root, "money-card-2026-09-24.md", "BLOCK (1 MAJOR)", "Review of MoneyCard.kt at 1234567.")
        write(root, "cost-plan-2026-09-24.md", "BLOCK (1 MAJOR)", "Review of drafts/cost-plan.md naming ledger.py.")
        rc, out, _ = run(["2026-09-24", "2026-09-25"], root)
        # tooling slugs: gate-py (2 files, one BLOCK), row-sh (one BLOCK); money-card is app code; cost-plan names drafts/
        return rc == 0 and numbers(out) == (5, 4, 2, 2, 1.0)
    check("slug, round grouping, tooling rule and verdicts give 2 BLOCK files over 2 tooling slugs", rules)

    def plan_line_one(root):
        path = os.path.join(root, "reviews", "gate-plan-2026-09-24.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Review of the gate plan\nDisposition: BLOCK\nscripts/gate.py is named here.\n")
        os.utime(path, (DAY.timestamp(), DAY.timestamp()))
        rc, out, _ = run(["2026-09-24", "2026-09-25"], root)
        return rc == 0 and numbers(out)[2] == 0
    check("a review whose line 1 says plan is not tooling", plan_line_one)

    def window_edges(root):
        write(root, "a-py-2026-09-24.md", "BLOCK", "scripts/a.py", when=dt.datetime(2026, 9, 24, 0, 0, 0))
        write(root, "b-py-2026-09-24.md", "BLOCK", "scripts/b.py", when=dt.datetime(2026, 9, 24, 23, 59, 59))
        write(root, "c-py-2026-09-25.md", "BLOCK", "scripts/c.py", when=dt.datetime(2026, 9, 25, 0, 0, 0))
        rc, out, _ = run(["2026-09-24", "2026-09-25"], root)
        return rc == 0 and numbers(out)[0] == 2
    check("the window takes 00:00 of the first day and stops before 00:00 of the day after", window_edges)

    def floor(root):
        for i in range(14):
            write(root, "s%02d-py-2026-09-24.md" % i, "CLEAR", "scripts/s%02d.py" % i)
        rc14, out14, _ = run(["2026-09-24", "2026-09-25"], root)
        write(root, "s14-py-2026-09-24.md", "BLOCK", "scripts/s14.py")
        rc15, out15, _ = run(["2026-09-24", "2026-09-25"], root)
        return (rc14 == 0 and "carries (under 15 tooling slugs)" in out14 and rc15 == 0
                and "counted, at or under the keep threshold 0.54" in out15 and numbers(out15)[4] == round(1 / 15, 2))
    check("14 tooling slugs carry; 15 count", floor)

    def anchored(root):
        a = run(["--last-complete", "--today", "2026-09-27"], root)[1]
        b = run(["--last-complete", "--today", "2026-09-26"], root)[1]
        c = run(["--last-complete", "--today", "2026-09-19"], root)[1]
        return ("2026-09-23 to 2026-09-27" in a and "2026-09-19 to 2026-09-23" in b and "before the practice" in b
                and "2026-09-15 to 2026-09-19" in c)
    check("--last-complete reads the newest anchored window that has ended", anchored)

    def before_s16(root):
        for i in range(16):
            write(root, "p%02d-py-2026-09-16.md" % i, "BLOCK", "scripts/p%02d.py" % i, when=dt.datetime(2026, 9, 16, 12))
        rc, out, _ = run(["2026-09-15", "2026-09-19"], root)
        rc2, out2, _ = run(["--last-complete", "--today", "2026-09-22"], root)
        return (rc == 0 and "before the practice" in out and "counted" not in out and rc2 == 0
                and "2026-09-15 to 2026-09-19" in out2 and "before the practice" in out2)
    check("a window that starts before the first decision day is never counted, explicit or --last-complete", before_s16)

    def carry_widens(root):
        # 09-23..09-26: 10 slugs (carries); 09-27..09-30: 6 more; read on 10-01 the segment is 09-23..10-01, 16 slugs
        for i in range(10):
            write(root, "q%02d-py-2026-09-24.md" % i, "CLEAR", "scripts/q%02d.py" % i, when=dt.datetime(2026, 9, 24, 12))
        for i in range(6):
            write(root, "w%02d-py-2026-09-28.md" % i, "CLEAR", "scripts/w%02d.py" % i, when=dt.datetime(2026, 9, 28, 12))
        first = run(["--last-complete", "--today", "2026-09-27"], root)[1]
        second = run(["--last-complete", "--today", "2026-10-01"], root)[1]
        return ("2026-09-23 to 2026-09-27" in first and "carries" in first
                and "2026-09-23 to 2026-10-01" in second and numbers(second)[2] == 16 and "counted" in second)
    check("a short window carries: the next --last-complete reads both, dates widened", carry_widens)

    def counted_resets(root):
        for i in range(15):
            write(root, "c%02d-py-2026-09-24.md" % i, "CLEAR", "scripts/c%02d.py" % i, when=dt.datetime(2026, 9, 24, 12))
        out = run(["--last-complete", "--today", "2026-10-01"], root)[1]
        return "2026-09-27 to 2026-10-01" in out
    check("after a counted window the next segment starts at its end", counted_resets)

    def carry_then_reset(root):
        # 8 slugs at 09-24 carry, 9 more at 09-28 make 09-23..10-01 count (17), so on 10-05 the segment
        # restarts at 10-01; a walk that read only the last window would carry all three and count 09-23..10-05
        for i in range(8):
            write(root, "d%02d-py-2026-09-24.md" % i, "CLEAR", "scripts/d%02d.py" % i, when=dt.datetime(2026, 9, 24, 12))
        for i in range(9):
            write(root, "e%02d-py-2026-09-28.md" % i, "CLEAR", "scripts/e%02d.py" % i, when=dt.datetime(2026, 9, 28, 12))
        out = run(["--last-complete", "--today", "2026-10-05"], root)[1]
        return "2026-10-01 to 2026-10-05" in out and "carries" in out
    check("a carried window joined by the next counts, and the segment after it starts fresh", carry_then_reset)

    def verdict_past_line_three(root):
        path = os.path.join(root, "reviews", "deep-py-2026-09-24.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Review of scripts/deep.py\n\nRound 1.\n\nDisposition: BLOCK (1 MAJOR)\n")
        os.utime(path, (DAY.timestamp(), DAY.timestamp()))
        rc, out, _ = run(["2026-09-24", "2026-09-25"], root)
        return rc == 0 and numbers(out)[1] == 1
    check("a disposition on line 5 is read by review_verdict, which the 3-line fallback would miss",
          verdict_past_line_three)

    def usage(root):
        cases = [[], ["2026-09-25", "2026-09-24"], ["2026-09-24", "x"], ["--last-complete", "--today", "2026-09-18"],
                 ["2026-09-24", "2026-09-25", "--last-complete"]]
        return all(run(c, root)[0] == 2 for c in cases)
    check("no days, reversed days, a bad day, no complete window and both forms are usage errors (exit 2)", usage)

    def unreadable(root):
        bad = os.path.join(root, "reviews", "dir-as-review-2026-09-24.md")
        os.makedirs(bad)
        os.utime(bad, (DAY.timestamp(), DAY.timestamp()))  # inside the window, so only the file check can drop it
        write(root, "a-py-2026-09-24.md", "BLOCK", "scripts/a.py")
        rc, out, _ = run(["2026-09-24", "2026-09-25"], root)
        return rc == 0 and numbers(out)[:2] == (1, 1)
    check("a directory named like a review is skipped, not a crash", unreadable)

    def settings_optional(root):
        for i in range(15):
            write(root, "k%02d-py-2026-09-24.md" % i, "CLEAR", "scripts/k%02d.py" % i)
        rc, out, _ = run(["2026-09-19", "2026-09-25"], root, TOOLING_BLOCK_KEEP_AT=None,
                         TOOLING_BLOCK_FIRST_DECISION=None)
        return rc == 0 and out.endswith(", counted") and "before the practice" not in out and "keep" not in out
    check("with no first decision day and no keep threshold an early window counts with no verdict", settings_optional)

    def flags_win(root):
        for i in range(15):
            write(root, "f%02d-py-2026-09-24.md" % i, "BLOCK", "scripts/f%02d.py" % i)
        rc, out, _ = run(["2026-09-24", "2026-09-25", "--keep-at", "1.5", "--min-slugs", "10"], root)
        return rc == 0 and "counted, at or under the keep threshold 1.50" in out
    check("a flag wins over its variable", flags_win)

    def fallback_last_disposition(root):
        path = os.path.join(root, "reviews", "tail-py-2026-09-24.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Review of scripts/tail.py" + chr(10) * 14 + "Body." + chr(10) + "## Disposition" + chr(10) + chr(10) + "BLOCK (2 MAJOR)" + chr(10))
        os.utime(path, (DAY.timestamp(), DAY.timestamp()))
        rc, out, _ = run(["2026-09-24", "2026-09-25"], root)
        return rc == 0 and numbers(out)[1] == 1
    check("a disposition past line 12 is read from the last Disposition heading of the file", fallback_last_disposition)

    def bad_settings(root):
        cases = [(["2026-09-24", "2026-09-25"], dict(TOOLING_BLOCK_MIN_SLUGS="x")),
                 (["2026-09-24", "2026-09-25"], dict(TOOLING_BLOCK_KEEP_AT="half")),
                 (["2026-09-24", "2026-09-25"], dict(TOOLING_BLOCK_FIRST_DECISION="2026-09-24")),
                 (["2026-09-24", "2026-09-25", "--window-days", "0"], {}),
                 (["--last-complete", "--today", "2026-10-01"], dict(TOOLING_BLOCK_ANCHOR=None,
                                                                      TOOLING_BLOCK_FIRST_DECISION=None))]
        return all(run(args, root, **env)[0] == 2 for args, env in cases)
    check("a bad variable, a first decision day off the grid, a zero window and --last-complete with no anchor are "
          "usage errors", bad_settings)

    def anchor_from_first_decision(root):
        out = run(["--last-complete", "--today", "2026-09-27"], root, TOOLING_BLOCK_ANCHOR=None)[1]
        return "2026-09-23 to 2026-09-27" in out
    check("with no anchor the first decision day anchors the windows", anchor_from_first_decision)

    def no_reviews_folder(root):
        empty = os.path.join(root, "no-reviews-here")
        os.makedirs(empty, exist_ok=True)
        rc, out, err = run(["2026-09-19", "2026-09-23"], empty)
        return rc == 2 and out == "" and "no reviews folder" in err
    check("a root with no reviews folder is a usage error, never a quiet window", no_reviews_folder)

    print("%d of %d OK" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
