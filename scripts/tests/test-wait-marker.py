#!/usr/bin/env python3
"""Tests for claude/tools/wait-marker.sh and its .ps1 twin, the two halves of one contract.

Run:
    python test-wait-marker.py

Both halves are run the way a lane runs them, as a subprocess against a marker in a temp
folder, and both are held to the same table: exit 0 the moment the file exists with nothing
on stdout, exit 3 at the deadline with one stderr line, exit 2 with a usage line on a bad
argument, and the ceiling note when the caller asks to wait longer than the prompt cache
survives. Nothing under the user's home is touched and no marker outlives its test.

Each half is skipped, with a note naming the missing interpreter, when it is not on PATH.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
# The pair ships with the kit, not with this repository: the installer copies the whole
# claude/tools directory to <kit home>/tools, where the record CLI lands too, so a lane
# calls it as ~/.claude/tools/wait-marker.sh. The tests run it from the source tree.
TOOLS = os.path.join(ROOT, "claude", "tools")
SH = os.path.join(TOOLS, "wait-marker.sh")
PS1 = os.path.join(TOOLS, "wait-marker.ps1")
CEILING = 270
# Git Bash first, the same candidates doctor.py pins: a bare `bash` on the Windows PATH may be
# WSL, which reads a different filesystem than the temp folder these cases write.
GIT_BASH = (r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe")


def find_bash():
    for candidate in GIT_BASH:
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("bash")


BASH = find_bash()
POWERSHELL = shutil.which("powershell")


class Contract:
    """The table both halves answer. A mixin, so it never runs on its own."""

    runner = None
    script = None
    skip_note = ""

    def setUp(self):
        if not self.runner:
            self.skipTest(self.skip_note)
        self.tmp = tempfile.mkdtemp(prefix="wait-marker-")
        self.marker = os.path.join(self.tmp, "gate.done")

    def tearDown(self):
        if getattr(self, "tmp", None):
            shutil.rmtree(self.tmp, ignore_errors=True)

    def argument(self, path):
        """The path spelling this interpreter reads."""
        return path

    def wait(self, *arguments, **kwargs):
        started = time.time()
        process = subprocess.run(
            list(self.runner) + [self.script] + list(arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=kwargs.get("timeout", 60),
        )
        return (process.returncode,
                process.stdout.decode("utf-8", "replace"),
                process.stderr.decode("utf-8", "replace"),
                time.time() - started)

    def touch(self):
        with open(self.marker, "w", encoding="utf-8") as handle:
            handle.write("done\n")

    # ------------------------------------------------------------- exit 0
    def test_a_marker_already_there_returns_at_once_and_prints_nothing(self):
        self.touch()
        code, out, err, elapsed = self.wait(self.argument(self.marker))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")
        self.assertEqual(err, "")
        self.assertLess(elapsed, 5.0, "the first check happens before the first sleep")

    def test_a_marker_written_after_two_seconds_is_seen_inside_eight(self):
        timer = threading.Timer(2.0, self.touch)
        timer.start()
        try:
            code, out, err, elapsed = self.wait(self.argument(self.marker), "20", timeout=40)
        finally:
            timer.cancel()
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")
        self.assertTrue(os.path.isfile(self.marker))
        self.assertLess(elapsed, 8.0, "a five second poll must catch a marker written at two")

    # ------------------------------------------------------------- exit 3
    def test_no_marker_times_out_with_one_stderr_line(self):
        code, out, err, elapsed = self.wait(self.argument(self.marker), "3", timeout=40)
        self.assertEqual(code, 3, err)
        self.assertEqual(out, "")
        self.assertEqual(err.strip().splitlines(),
                         ["timeout after 3 s: %s" % self.argument(self.marker)])
        self.assertFalse(os.path.exists(self.marker))
        self.assertGreaterEqual(elapsed, 3.0, "it must wait the seconds it was given")
        self.assertLess(elapsed, 8.0, "the deadline cuts the last sleep short")

    # ------------------------------------------------------------- exit 2
    def test_a_missing_argument_is_a_usage_error(self):
        code, out, err, _elapsed = self.wait()
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("usage:", err)
        self.assertIn("<file>", err)

    def test_a_non_numeric_second_argument_is_a_usage_error(self):
        code, out, err, _elapsed = self.wait(self.argument(self.marker), "ten")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("usage:", err)

    def test_a_negative_second_argument_is_a_usage_error(self):
        code, out, err, _elapsed = self.wait(self.argument(self.marker), "-5")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("usage:", err)

    # ------------------------------------------------------------- the ceiling
    def test_a_wait_over_the_ceiling_is_clamped_with_a_note(self):
        self.touch()
        code, out, err, _elapsed = self.wait(self.argument(self.marker), "600")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "", "the clamp note is stderr, stdout stays empty")
        self.assertIn("clamped", err)
        self.assertIn(str(CEILING), err)

    def test_the_ceiling_itself_carries_no_note(self):
        self.touch()
        code, out, err, _elapsed = self.wait(self.argument(self.marker), str(CEILING))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")
        self.assertEqual(err, "", "270 is inside the contract, not over it")

    def test_the_header_says_why_the_ceiling_is_two_hundred_and_seventy(self):
        with open(self.script, encoding="utf-8") as handle:
            header = "".join(handle.readlines()[:30]).lower()
        self.assertIn(str(CEILING), header)
        self.assertIn("cache", header)


class BashContract(Contract, unittest.TestCase):
    script = SH
    runner = [BASH] if BASH else None
    skip_note = "bash is not on PATH, so the POSIX half is not exercised here"

    def argument(self, path):
        # Forward slashes: a Windows path reaches a POSIX shell as an argv word, and a
        # backslash in it is an escape waiting to happen.
        return path.replace("\\", "/")


class PowerShellContract(Contract, unittest.TestCase):
    script = PS1
    runner = ([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
              if POWERSHELL else None)
    skip_note = "powershell is not on PATH, so the Windows half is not exercised here"


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    unittest.main(verbosity=2)
