"""Pins for scripts/cca-pace.py: verdicts, walls, the week source, the account check and the refusals.

    python scripts/tests/test-cca-pace.py
    CCA_PACE_PY=<path> python scripts/tests/test-cca-pace.py      # against a staged copy

Every run reads a saved probe output in a temp folder (--probe-output) at a fixed clock (--now); no probe runs.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("CCA_PACE_PY") or os.path.join(os.path.dirname(HERE), "cca-pace.py")
# The week reset is 2026-10-03 12:00 UTC, so the week began 2026-09-26 12:00 and NOW is 4 of its 7 days in:
# the pace at the default wall of 80 is 45.7.
NOW = "2026-09-30T12:00:00+00:00"


def probe(week=None, seven_day=30.0, hours=20.0, account="acct-a"):
    lines = ["account: %s" % account] if account else []
    lines += ["five_hour: %.1f%% used, resets 2026-10-01T15:00:00+00:00" % hours,
              "seven_day: %.1f%% used, resets 2026-10-03T12:00:00+00:00" % seven_day]
    if week is not None:
        lines.append("limit weekly_all: %.1f%% used" % week)
    return "\n".join(lines) + "\n"


class CcaPaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cca-pace-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_on(self, text, *args, now=NOW):
        path = os.path.join(self.tmp, "probe.txt")
        with open(path, "w", encoding="utf-8") as h:
            h.write(text)
        argv = [sys.executable, SCRIPT, "--probe-output", path] + (["--now", now] if now is not None else [])
        done = subprocess.run(argv + list(args), capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout + done.stderr

    def test_a_week_well_under_pace_is_behind_and_exits_zero(self):
        code, out = self.run_on(probe(week=31.0))
        self.assertEqual(0, code, out)
        self.assertIn("week 31% (pace 46%, wall 80, gap -15), hours 20% (wall 70) -> behind", out)

    def test_a_week_near_pace_is_launch(self):
        code, out = self.run_on(probe(week=45.0, seven_day=45.0))
        self.assertEqual(0, code, out)
        self.assertTrue(out.strip().endswith("-> launch"), out)

    def test_either_wall_is_exit_three(self):
        for text in (probe(week=80.0, seven_day=80.0), probe(week=10.0, seven_day=10.0, hours=70.0)):
            code, out = self.run_on(text)
            self.assertEqual(3, code, out)
            self.assertIn("-> wall", out)

    def test_the_walls_and_the_behind_gap_are_arguments(self):
        code, out = self.run_on(probe(week=50.0, seven_day=50.0), "--week-wall", "50")
        self.assertEqual(3, code, out)
        code, out = self.run_on(probe(week=31.0), "--behind", "20")
        self.assertEqual(0, code, out)
        self.assertIn("-> launch", out)

    def test_the_seven_day_line_stands_in_and_a_differing_one_is_printed_beside(self):
        code, out = self.run_on(probe(seven_day=30.0))
        self.assertIn("[no weekly_all line, seven_day read]", out)
        code, out = self.run_on(probe(week=40.0, seven_day=30.0))
        self.assertIn("week 40% [seven_day 30%]", out)

    def test_another_account_than_the_one_asked_is_unreadable(self):
        code, out = self.run_on(probe(week=31.0, account="acct-b"), "--account", "acct-a")
        self.assertEqual(2, code, out)
        self.assertIn("unreadable (account acct-b, not acct-a)", out)
        code, out = self.run_on(probe(week=31.0), "--account", "acct-a", "--row")
        self.assertEqual(0, code, out)
        code, out = self.run_on(probe(week=31.0), "--profile", os.path.join(self.tmp, "acct-a-profile"), "--row")
        self.assertTrue(out.startswith("acct-a acct-a-profile 2026-"), out)
        code, out = self.run_on(probe(week=31.0), "--label", "pace", "--row")
        self.assertTrue(out.startswith("acct-a pace 2026-"), out)

    def test_an_empty_or_missing_probe_output_is_unreadable(self):
        code, out = self.run_on("")
        self.assertEqual(2, code, out)
        self.assertIn("unreadable (probe exit 0): empty output", out)
        done = subprocess.run([sys.executable, SCRIPT, "--probe-output", os.path.join(self.tmp, "none.txt"),
                               "--now", NOW], capture_output=True, text=True, timeout=60)
        self.assertEqual(2, done.returncode, done.stdout)
        self.assertIn("probe output not readable", done.stdout)

    def test_bad_arguments_exit_two(self):
        for args in (("--week-wall", "0"), ("--hours-wall", "101"), ("--behind", "-1"), ("--week-wall", "x")):
            code, out = self.run_on(probe(week=31.0), *args)
            self.assertEqual(2, code, args)
        for now in ("", "  ", "30-09-2026"):
            code, out = self.run_on(probe(week=31.0), now=now)
            self.assertEqual(2, code, repr(now) + out)
            self.assertIn("bad --now", out)

    def test_a_bare_date_is_its_local_midnight(self):
        code, out = self.run_on(probe(week=31.0), now="2026-09-30")
        self.assertEqual(0, code, out)
        self.assertIn("2026-09-30 00:00", out)


if __name__ == "__main__":
    unittest.main()
