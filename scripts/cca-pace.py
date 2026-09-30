"""cca-pace: is one Claude Code account's weekly quota used at pace, and may a lane launch on it now.

An account whose week resets with quota unused has wasted it; one that reaches its walls stops lanes mid-work. This
reader runs scripts/usage-probe.py once on one profile and prints one line:

    <label> <local time>: week <used>% (pace <p>%, wall <W>, gap <used - p>), hours <used>% (wall <H>) -> <verdict>

The week meter is the probe's `limit weekly_all` line when it prints one, else its seven_day line; the seven_day line
always gives the reset time, and its own percent is printed beside the week when the two differ by 2 or more. The hours
meter is the five_hour line. The pace is the week wall times the share of the week elapsed since the last reset (the
seven_day reset time minus seven days). Verdicts, and exit codes:
- launch (0): both meters are under their walls.
- behind (0): as launch, and the week is --behind points or more under pace, so the next unit of work goes here.
- wall (3): a meter is at or above its wall; no lane launches on the account, and a running one checkpoints and stops.
- unreadable (2): the probe failed, printed no five_hour and seven_day lines, or named another account than --account
  (a 401 means the stored token expired; opening a session on that profile once refreshes it). A bad argument exits 2.

    python cca-pace.py [--profile DIR] [--account NAME] [--week-wall 80] [--hours-wall 70] [--behind 5]
                       [--label NAME] [--row] [--probe-output FILE] [--now YYYY-MM-DDTHH:MM]

--profile is the account's profile folder, passed to the probe as CLAUDE_CONFIG_DIR (default: the current
CLAUDE_CONFIG_DIR, else ~/.claude). --account, when given, must equal the probe's `account:` line, so a wrong profile
never reads as this account's pace. --row prefixes the line with the account the probe names, for a ledger.
--label names the line (default: the profile folder's name).
--probe-output reads a saved probe output instead of running the probe, and --now fixes the clock (tests).
"""
import argparse
import datetime
import os
import re
import subprocess
import sys

PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage-probe.py")
METER_RE = re.compile(r"^(five_hour|seven_day): ([\d.]+)% used, resets (\S+)", re.M)
WEEKLY_ALL_RE = re.compile(r"^limit weekly_all: ([\d.]+)% used", re.M)
ACCOUNT_RE = re.compile(r"^account: (\S+)", re.M)


def percent(value):
    """An argparse type: a wall above 0 and at most 100."""
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("not a number: %r" % value)
    if not 0 < number <= 100:
        raise argparse.ArgumentTypeError("a wall is above 0 and at most 100: %r" % value)
    return number


def points(value):
    """An argparse type: a gap in points, 0 or more."""
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("not a number: %r" % value)
    if number < 0:
        raise argparse.ArgumentTypeError("--behind is 0 or more: %r" % value)
    return number


def default_profile():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def probe_text(path, profile):
    if path is not None:  # an empty --probe-output is refused as unreadable, never read as "run the real probe"
        if not path.strip():
            return "probe output not readable: empty path", 1
        try:
            with open(path, encoding="utf-8", errors="replace") as h:
                return h.read(), 0
        except OSError as e:
            return "probe output not readable: %s" % e, 1
    env = dict(os.environ, CLAUDE_CONFIG_DIR=profile)
    try:
        done = subprocess.run([sys.executable, PROBE], capture_output=True, text=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "probe did not run: %s" % e, 1
    return (done.stdout or "") + (done.stderr or ""), done.returncode


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", default=None, help="profile folder of the account (CLAUDE_CONFIG_DIR of the probe)")
    ap.add_argument("--account", default=None, help="the account name the probe must print")
    ap.add_argument("--week-wall", type=percent, default=80.0)
    ap.add_argument("--hours-wall", type=percent, default=70.0)
    ap.add_argument("--behind", type=points, default=5.0, help="points under pace that read as behind")
    ap.add_argument("--label", default=None, help="the word the line starts with (default: the profile folder name)")
    ap.add_argument("--row", action="store_true", help="prefix the line with the account name for a ledger")
    ap.add_argument("--probe-output")
    ap.add_argument("--now")
    a = ap.parse_args(argv)
    profile = a.profile or default_profile()
    label = (a.label or "").strip() or os.path.basename(os.path.normpath(profile)) or "usage"
    try:
        now = (datetime.datetime.fromisoformat(a.now.strip()).astimezone() if a.now is not None
               else datetime.datetime.now().astimezone())
    except ValueError as e:
        print("cca-pace: bad --now: %s" % e, file=sys.stderr)
        return 2
    stamp = now.strftime("%Y-%m-%d %H:%M")
    text, code = probe_text(a.probe_output, profile)
    meters = {m.group(1): (float(m.group(2)), m.group(3)) for m in METER_RE.finditer(text)}
    named = ACCOUNT_RE.search(text)
    account = named.group(1) if named else None
    if code != 0 or set(meters) != {"five_hour", "seven_day"} or (a.account and account != a.account):
        first = (text.strip().splitlines() or ["empty output"])[0][:160]
        why = "account %s, not %s" % (account, a.account) if code == 0 and set(meters) == {
            "five_hour", "seven_day"} else "probe exit %s" % code
        print("%s %s: unreadable (%s): %s" % (label, stamp, why, first))
        return 2
    seven_day, week_reset = meters["seven_day"]
    weekly_all = WEEKLY_ALL_RE.search(text)
    week = float(weekly_all.group(1)) if weekly_all else seven_day
    source = "" if weekly_all and abs(week - seven_day) < 2 else (
        " [seven_day %.0f%%]" % seven_day if weekly_all else " [no weekly_all line, seven_day read]")
    hours = meters["five_hour"][0]
    try:
        reset = datetime.datetime.fromisoformat(week_reset).astimezone()
    except ValueError:
        print("%s %s: unreadable: reset time %r" % (label, stamp, week_reset))
        return 2
    start = reset - datetime.timedelta(days=7)
    share = min(1.0, max(0.0, (now - start).total_seconds() / (7 * 86400)))
    pace = a.week_wall * share
    gap = week - pace
    if week >= a.week_wall or hours >= a.hours_wall:
        verdict, rc = "wall", 3
    elif gap <= -a.behind:
        verdict, rc = "behind", 0
    else:
        verdict, rc = "launch", 0
    line = "%s %s: week %.0f%%%s (pace %.0f%%, wall %g, gap %+.0f), hours %.0f%% (wall %g) -> %s" % (
        label, stamp, week, source, pace, a.week_wall, gap, hours, a.hours_wall, verdict)
    print(("%s %s" % (account or "-", line)) if a.row else line)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
