"""Pins for scripts/gate-detach.ps1: no parameter ever prompts for a missing value.

The script runs through `powershell -NoProfile -File` with stdin left open (a pipe never written, never closed)
and a hard 20 s subprocess timeout. A missing -Log or -Command exits 2 within the timeout and names the parameter;
a full valid call in the Command set starts `bash -lc "echo ok"` and the temp log reads ok. Nothing outside a temp folder
is written and no build lock is taken.

  python scripts/tests/test-gate-detach-no-prompt.py       the gate-detach.ps1 under scripts/
"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("DETACH_DIR") or os.path.dirname(HERE)
SCRIPTS = ["gate-detach.ps1"]
TIMEOUT = 20


def run(script, *args):
    """Run a script with stdin an open pipe nobody writes to; a prompt would block on it until the timeout."""
    p = subprocess.Popen(["powershell", "-NoProfile", "-File", os.path.join(DIR, script)] + list(args),
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = p.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        p.communicate()
        raise AssertionError("%s %s prompted or hung: no exit within %d s" % (script, " ".join(args), TIMEOUT))
    return p.returncode, out + err


def run_on_a_console(script, *args):
    """The case that hung in an agent's tool shell: stdin is the hidden console's own input, never written."""
    p = subprocess.Popen(["powershell", "-NoProfile", "-File", os.path.join(DIR, script)] + list(args),
                         creationflags=0x08000000, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = p.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        p.communicate()
        raise AssertionError("%s %s prompted on the console: no exit within %d s" % (script, " ".join(args), TIMEOUT))
    return p.returncode, out + err


class GateDetachNoPromptTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dnp-")
        self.posix = "/" + self.root[0].lower() + self.root[2:].replace("\\", "/")

    def tearDown(self):
        for _ in range(20):
            try:
                shutil.rmtree(self.root)
                return
            except OSError:
                time.sleep(1)

    def refused(self, script, name, *args):
        rc, text = run(script, *args)
        self.assertEqual(rc, 2, text)
        self.assertIn("-" + name, text)
        self.assertNotRegex(text.strip().splitlines()[-1] if text.strip() else "", r"^\d+$", "a pid was printed")

    def test_missing_log_in_the_command_set_exits_2_and_names_it(self):
        for s in SCRIPTS:
            with self.subTest(script=s):
                self.refused(s, "Log", "-Command", "echo hi")

    def test_a_console_stdin_never_prompts_either(self):
        for s in SCRIPTS:
            with self.subTest(script=s):
                rc, text = run_on_a_console(s, "-Command", "echo hi")
                self.assertEqual(rc, 2, text)
                self.assertIn("-Log", text)

    def test_missing_command_exits_2_and_names_it(self):
        for s in SCRIPTS:
            with self.subTest(script=s):
                self.refused(s, "Command", "-Log", self.posix + "/x.log")

    def test_nothing_at_all_exits_2(self):
        for s in SCRIPTS:
            with self.subTest(script=s):
                rc, text = run(s)
                self.assertEqual(rc, 2, text)
                self.assertIn("-Command", text)

    def test_an_empty_value_counts_as_missing(self):
        for s in SCRIPTS:
            with self.subTest(script=s):
                self.refused(s, "Log", "-Command", "echo hi", "-Log", "")

    def test_a_full_valid_call_in_the_command_set_still_runs_and_logs(self):
        for s in SCRIPTS:
            with self.subTest(script=s):
                log = self.posix + "/" + s + ".log"
                rc, text = run(s, "-Command", "echo ok", "-Log", log)
                self.assertEqual(rc, 0, text)
                self.assertRegex(text.strip(), r"^\d+$")
                path = os.path.join(self.root, s + ".log")
                end = time.time() + 30
                got = None
                while time.time() < end and got is None:
                    if os.path.exists(path):
                        with open(path, encoding="utf-8") as f:
                            if "ok" in f.read():
                                got = True
                    time.sleep(0.3)
                self.assertTrue(got, "the log never read ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
