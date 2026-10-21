"""Tests for claude/hooks/kit-update-notice.py, the session-start line about the kit's daily update.

    python claude/hooks/tests/test-kit-update-notice.py

The hook runs the way the harness runs it, as a subprocess, with KIT_HOME pointed at a fresh temporary folder, so
no real kit home is read or changed. The pending file is written the way install/auto-update.ps1 writes it under
Windows PowerShell 5.1: UTF-8 with a byte order mark, one compact JSON object a line. No network.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(HERE), "kit-update-notice.py")

UPDATED = {"result": "updated", "from": "1111111", "to": "2222222", "files": 12, "hooks": ["new-hook.py"],
           "time": "2026-10-22 05:00", "log": "C:\\kit\\logs\\auto-update-2026-10-22.log"}
FAILED = {"result": "failed", "line": "git pull --ff-only in C:\\kit failed: could not resolve host; nothing was installed",
          "time": "2026-10-23 05:00", "log": "C:\\kit\\logs\\auto-update-2026-10-23.log"}


class KitUpdateNotice(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="kit-update-notice-")
        self.logs = os.path.join(self.base, "logs")
        self.pending = os.path.join(self.logs, "auto-update-pending.jsonl")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def write(self, *runs, raw=None):
        os.makedirs(self.logs, exist_ok=True)
        text = raw if raw is not None else "".join(json.dumps(run) + "\r\n" for run in runs)
        with open(self.pending, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(text)

    def run_hook(self):
        env = dict(os.environ, KIT_HOME=self.base)
        started = time.time()
        done = subprocess.run([sys.executable, HOOK], env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=20)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "")
        self.assertLess(time.time() - started, 10)
        return done.stdout

    def line_of(self, out):
        lines = out.splitlines()
        self.assertEqual(len(lines), 1, out)
        payload = json.loads(lines[0])
        line = payload["systemMessage"]
        self.assertEqual(payload["hookSpecificOutput"],
                         {"hookEventName": "SessionStart", "additionalContext": line})
        return line

    def test_no_logs_folder_prints_nothing(self):
        self.assertEqual(self.run_hook(), "")

    def test_an_update_is_one_line_once(self):
        self.write(UPDATED)
        line = self.line_of(self.run_hook())
        self.assertIn("now at 2222222 (was 1111111)", line)
        self.assertIn("12 files changed", line)
        self.assertIn("new hooks: new-hook.py", line)
        self.assertIn("Log: " + UPDATED["log"], line)
        self.assertFalse(os.path.exists(self.pending))
        self.assertTrue(os.path.exists(os.path.join(self.logs, "auto-update-pending.seen.jsonl")))
        self.assertEqual(self.run_hook(), "", "the second session start prints nothing")

    def test_a_failure_names_its_line_and_log(self):
        self.write(FAILED)
        line = self.line_of(self.run_hook())
        self.assertIn("failed: git pull --ff-only in C:\\kit failed: could not resolve host", line)
        self.assertIn("Log: " + FAILED["log"], line)
        self.assertNotIn("now at", line)

    def test_several_runs_since_the_last_notice_make_one_line(self):
        second = dict(UPDATED, **{"from": "2222222", "to": "3333333", "files": 3, "hooks": ["new-hook.py", "other.py"]})
        self.write(UPDATED, second, FAILED)
        line = self.line_of(self.run_hook())
        self.assertIn("now at 3333333 (was 1111111), 15 files changed over 2 runs", line)
        self.assertIn("new hooks: new-hook.py, other.py", line)
        self.assertIn("the last run failed: git pull", line)
        self.assertIn("Log: " + FAILED["log"], line)

    def test_an_update_after_a_failure_reports_only_the_update(self):
        # the pending file is in time order, as the daily run appends it
        self.write(dict(FAILED, time="2026-10-21 05:00"), UPDATED)
        line = self.line_of(self.run_hook())
        self.assertIn("now at 2222222", line)
        self.assertNotIn("failed", line)

    def test_a_file_with_no_run_in_it_prints_nothing(self):
        self.write(raw="not json\r\n{\"result\": \"other\"}\r\n[1, 2]\r\n\r\n")
        self.assertEqual(self.run_hook(), "")

    def test_a_bad_line_among_good_ones_is_skipped(self):
        self.write(raw="garbage\r\n" + json.dumps(UPDATED) + "\r\n")
        self.assertIn("now at 2222222", self.line_of(self.run_hook()))

    def test_an_unreadable_pending_path_never_fails_the_session(self):
        os.makedirs(self.pending)
        self.assertEqual(self.run_hook(), "")

    def test_a_pending_file_that_is_not_utf8_never_fails_the_session(self):
        os.makedirs(self.logs)
        with open(self.pending, "wb") as handle:
            handle.write(b"\xff\xfe\x00bad")
        self.assertEqual(self.run_hook(), "")

    def test_sessions_that_start_together_print_the_line_once(self):
        env = dict(os.environ, KIT_HOME=self.base)
        for _ in range(5):
            self.write(UPDATED)
            started = [subprocess.Popen([sys.executable, HOOK], env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
            outs = [process.communicate(timeout=20) for process in started]
            self.assertEqual([process.returncode for process in started], [0, 0, 0, 0])
            self.assertEqual([err for _, err in outs], ["", "", "", ""])
            self.assertEqual(sum(1 for out, _ in outs if out.strip()), 1, outs)
            self.assertEqual(sorted(os.listdir(self.logs)), ["auto-update-pending.seen.jsonl"])

    def day_log(self, when, lines):
        """A day's log of the daily run, its lines stamped with when's clock time."""
        os.makedirs(self.logs, exist_ok=True)
        path = os.path.join(self.logs, "auto-update-%s.log" % when.strftime("%Y-%m-%d"))
        with open(path, "a", encoding="utf-8-sig") as handle:
            for text in lines:
                handle.write("%s %s\n" % (when.strftime("%H:%M:%S"), text))
        return path

    def test_a_run_stopped_at_the_time_limit_is_a_failure_once(self):
        when = datetime.datetime.now() - datetime.timedelta(hours=3)
        path = self.day_log(when, ["start: clone C:/kit, kit home C:/home", "  git: Enter passphrase for key:"])
        line = self.line_of(self.run_hook())
        self.assertIn("failed: the run that started at %s never finished" % when.strftime("%Y-%m-%d %H:%M:%S"), line)
        self.assertIn("Log: " + path, line)
        with open(path, encoding="utf-8-sig") as handle:
            self.assertIn("(reported at a session start)", handle.read())
        self.assertEqual(self.run_hook(), "", "the next session start prints nothing")
        self.assertFalse(os.path.exists(path + ".claim"))

    def test_a_run_still_inside_the_time_limit_is_not_reported(self):
        self.day_log(datetime.datetime.now() - datetime.timedelta(minutes=5), ["start: clone C:/kit, kit home C:/home"])
        self.assertEqual(self.run_hook(), "")

    def test_a_stopped_run_with_a_later_run_after_it_is_reported_once(self):
        first = datetime.datetime.now() - datetime.timedelta(hours=3)
        self.day_log(first, ["start: clone C:/kit, kit home C:/home"])
        self.day_log(first + datetime.timedelta(minutes=40), [
            "start: clone C:/kit, kit home C:/home", "done: the kit is already installed at 1234567; nothing to install"])
        self.assertIn("the run that started at %s never finished" % first.strftime("%Y-%m-%d %H:%M:%S"),
                      self.line_of(self.run_hook()))
        self.assertEqual(self.run_hook(), "", "the next session start prints nothing")

    def test_a_stopped_run_before_an_update_makes_one_line_ending_on_the_update(self):
        self.day_log(datetime.datetime(2026, 10, 21, 5, 0, 1), ["start: clone C:/kit, kit home C:/home"])
        self.write(UPDATED)
        line = self.line_of(self.run_hook())
        self.assertIn("now at 2222222 (was 1111111)", line)
        self.assertNotIn("never finished", line, "an update after the stopped run is the news")
        self.assertIn("Log: " + UPDATED["log"], line)

    def test_sessions_that_start_together_report_a_stopped_run_once(self):
        env = dict(os.environ, KIT_HOME=self.base)
        for days in range(2, 7):
            self.day_log(datetime.datetime.now() - datetime.timedelta(days=days), ["start: clone C:/kit, kit home C:/home"])
            started = [subprocess.Popen([sys.executable, HOOK], env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
            outs = [process.communicate(timeout=20) for process in started]
            self.assertEqual([err for _, err in outs], ["", "", "", ""])
            self.assertEqual(sum(1 for out, _ in outs if out.strip()), 1, outs)

    def test_a_wait_for_another_clone_is_said_as_paused(self):
        self.write({"result": "waiting", "line": "the last install came from C:/b; the daily update waits for an "
                    "install from C:/a", "time": "2026-10-22 05:00", "log": UPDATED["log"]})
        line = self.line_of(self.run_hook())
        self.assertIn("paused: the last install came from C:/b; the daily update waits for an install from C:/a", line)

    def test_a_claim_left_by_a_killed_session_is_taken_after_a_minute(self):
        path = self.day_log(datetime.datetime.now() - datetime.timedelta(hours=3), ["start: clone C:/kit, kit home C:/h"])
        with open(path + ".claim", "w"):
            pass
        self.assertEqual(self.run_hook(), "", "a fresh claim is another session's")
        old = time.time() - 120
        os.utime(path + ".claim", (old, old))
        self.assertIn("never finished", self.line_of(self.run_hook()))
        self.assertFalse(os.path.exists(path + ".claim"))


if __name__ == "__main__":
    unittest.main()
