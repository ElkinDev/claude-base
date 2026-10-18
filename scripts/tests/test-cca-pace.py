"""Pins for scripts/cca-pace.py: verdicts, walls, the week source, the account check and the refusals.

    python scripts/tests/test-cca-pace.py
    CCA_PACE_PY=<path> python scripts/tests/test-cca-pace.py      # against a staged copy

Every run reads a saved probe output in a temp folder (--probe-output) at a fixed clock (--now); no probe runs.
"""
import datetime
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


OPEN_UNTIL = "2026-10-14 04:00"  # after NOW in any zone, so the opening is in force; --now fixes the clock it is read against
OPEN_TEXT = OPEN_UNTIL + "\nthe owner lets this account run to its limit tonight\n"


class OpeningTest(unittest.TestCase):
    """--open-file: while the file names a moment still ahead, both walls read 100 (module docstring, Opening)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cca pace open ")
        self.opening = os.path.join(self.tmp, "open.txt")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, raw):
        with open(self.opening, "wb") as h:
            h.write(raw if isinstance(raw, bytes) else raw.encode("utf-8"))

    def read(self, hours, week, opening=True):
        path = os.path.join(self.tmp, "probe.txt")
        with open(path, "w", encoding="utf-8") as h:
            h.write(probe(week=week, seven_day=week, hours=hours))
        argv = [sys.executable, SCRIPT, "--probe-output", path, "--now", NOW]
        if opening:
            argv += ["--open-file", self.opening]
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)

    def assertOpensNothing(self, why):
        done = self.read(85, 90)
        self.assertEqual(3, done.returncode, (why, done.stdout, done.stderr))
        self.assertTrue(done.stdout.rstrip().endswith("-> wall"), (why, done.stdout))
        self.assertIn("wall 80", done.stdout, why)
        self.assertNotIn("open", done.stdout, why)

    def test_hours_85_and_week_90_with_an_opening_ahead_are_not_a_wall(self):
        self.put(OPEN_TEXT)
        done = self.read(85, 90)
        self.assertEqual(0, done.returncode, (done.stdout, done.stderr))
        self.assertRegex(done.stdout, r"^\S+ \d{4}-\d{2}-\d{2} \d{2}:\d{2}: week 90% \(pace 46%, wall 100, gap \+44\), "
                                      r"hours 85% \(wall 100, walls open until 2026-10-14 04:00\) -> launch\n$")

    def test_a_week_far_behind_pace_reads_behind_while_open(self):
        self.put(OPEN_TEXT)
        done = self.read(10, 20)
        self.assertEqual(0, done.returncode, done.stdout)
        self.assertTrue(done.stdout.rstrip().endswith("(wall 100, walls open until 2026-10-14 04:00) -> behind"),
                        done.stdout)

    def test_a_meter_at_100_is_still_a_wall_while_open(self):
        self.put(OPEN_TEXT)
        for hours, week in ((100, 50), (50, 100), (100, 100)):
            done = self.read(hours, week)
            self.assertEqual(3, done.returncode, (hours, week, done.stdout))
            self.assertTrue(done.stdout.rstrip().endswith(" -> wall"), done.stdout)
            self.assertEqual(2, done.stdout.count("wall 100"), done.stdout)
        self.assertEqual(0, self.read(99, 99).returncode)

    def test_utf8_with_a_bom_and_utf16_files_open_too(self):
        for raw in (b"\xef\xbb\xbf" + OPEN_TEXT.encode("utf-8"), OPEN_TEXT.replace("\n", "\r\n").encode("utf-16"),
                    ("\r\n " + OPEN_UNTIL + " reason on the same line").encode("utf-8"),
                    (OPEN_UNTIL.replace(" ", "T") + "\nreason").encode("utf-8")):
            self.put(raw)
            done = self.read(85, 90)
            self.assertEqual(0, done.returncode, (raw, done.stdout))
            self.assertIn("walls open until 2026-10-14 04:00", done.stdout, raw)

    def test_no_open_file_and_a_missing_one_keep_the_normal_walls(self):
        for done in (self.read(85, 90, opening=False), self.read(85, 90)):
            self.assertEqual(3, done.returncode, done.stdout)
            self.assertRegex(done.stdout, r"week 90% \(pace 46%, wall 80, gap \+44\), hours 85% \(wall 70\) -> wall\n$")

    def test_an_opening_whose_moment_has_passed_gives_the_bytes_of_no_file(self):
        none = self.read(85, 90, opening=False)
        self.put(OPEN_TEXT)
        lifted = self.read(85, 90)  # control: the same file is in force before its moment
        self.assertEqual(0, lifted.returncode, lifted.stdout)
        self.assertNotEqual(none.stdout, lifted.stdout)
        here = datetime.datetime.fromisoformat(NOW).astimezone().replace(tzinfo=None)  # the moment as the machine reads it
        past = datetime.timedelta(minutes=1)
        for moment in ((here - past).strftime("%Y-%m-%d %H:%M"), here.strftime("%Y-%m-%d %H:%M"), "2026-09-29 04:00"):
            self.put(moment + "\nan old opening\n")
            gone = self.read(85, 90)
            self.assertEqual((none.returncode, none.stdout, none.stderr), (gone.returncode, gone.stdout, gone.stderr),
                             moment)

    def test_garbage_an_empty_file_and_a_date_with_no_time_open_nothing(self):
        self.put(OPEN_TEXT)
        self.assertEqual(0, self.read(85, 90).returncode)  # control: a readable opening opens
        cases = (b"not a date at all\n", b"", b"2026-10-14\nno time on this one\n", b"2026-10-14 25:99 bad time\n",
                 b"\xff\xfe\x00", b"\x80\x81\x82\xfe", b"2026-10-14 4:00 one digit\n", b"20261014 04:00\n")
        for raw in cases:
            self.put(raw)
            self.assertOpensNothing(raw)


if __name__ == "__main__":
    unittest.main()
