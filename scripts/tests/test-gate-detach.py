"""Pins for scripts/gate-detach.ps1, the detached no-window launcher: the -Command form starts a detached bash command
whose output lands in the -Log file (stdout and stderr, stdin closed), a missing bash and a command over the length bound
are refused with exit 2 and no pid, the process is started with no console window, and a call piped to tail from Git
bash returns at once because the job inherits no handle of the calling shell (the pipe guard).

  python scripts/tests/test-gate-detach.py            the gate-detach.ps1 under scripts/
  GATE_DETACH=<path> python scripts/tests/test-gate-detach.py   another copy (a staged one, or a mutant for a red run)

Git's bin/bash.exe launcher exits 2 and writes nothing above about 8200 characters of command line, so the script refuses
above 8000 instead of launching a run that leaves no log. Every case runs in a temp folder and starts only echo, cat and
sleep; nothing outside the temp folder is written.
"""
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DETACH = os.environ.get("GATE_DETACH") or os.path.join(os.path.dirname(HERE), "gate-detach.ps1")
LIMIT = 8000


def bash_arg_len(cmd, log):
    """The bash argument gate-detach.ps1 builds for a command and a log path (its Get-BashArgs)."""
    inner = "( " + cmd + ' ) > "' + log + '" 2>&1 < /dev/null'
    return len('-lc "' + inner.replace('"', "\\" + '"') + '"')


class GateDetachCommandTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gdc-")
        self.posix = "/" + self.root[0].lower() + self.root[2:].replace("\\", "/")

    def tearDown(self):
        for _ in range(20):
            try:
                shutil.rmtree(self.root)
                return
            except OSError:
                time.sleep(1)

    def detach(self, *args):
        return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-File", DETACH] + list(args), capture_output=True,
                              text=True, timeout=120)

    def log(self, name):
        return self.posix + "/" + name

    def read_log(self, name, want, secs=60):
        path = os.path.join(self.root, name)
        end = time.time() + secs
        while time.time() < end:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                if want in text:
                    return text
            time.sleep(0.5)
        return None

    def test_a_command_starts_detached_and_its_log_is_written(self):
        # The launcher's own output goes to files here, not pipes: a child that inherited a pipe handle would keep a
        # reader waiting after the launcher exited, which says nothing about whether the launcher detached.
        out = os.path.join(self.root, "launcher.out")
        with open(out, "w", encoding="utf-8") as fo:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-File", DETACH, "-Command",
                                "sleep 8; echo started-ok; echo to-stderr 1>&2", "-Log", self.log("a.launch.log")],
                               stdout=fo, stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(r.returncode, 0)
        with open(out, encoding="utf-8", errors="replace") as f:
            self.assertRegex(f.read().strip(), r"^\d+$")
        # the launcher returned while the command was still sleeping: nothing is in the log yet
        path = os.path.join(self.root, "a.launch.log")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.assertNotIn("started-ok", f.read(), "the launcher waited for the command instead of detaching")
        text = self.read_log("a.launch.log", "to-stderr")
        self.assertIsNotNone(text, "the log never got the command's output")
        self.assertIn("started-ok", text)

    def test_the_command_reads_a_closed_stdin_and_quotes_survive(self):
        r = self.detach("-Command", 'cat; echo "quoted arg ok"', "-Log", self.log("b.launch.log"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.read_log("b.launch.log", "quoted arg ok")
        self.assertIsNotNone(text, "cat on stdin did not return at once, or a double quote was lost")

    def test_a_long_command_is_refused_naming_the_bound(self):
        log = self.log("c.launch.log")
        r = self.detach("-Command", "echo " + "x" * 9000, "-Log", log)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("above the 8000", " ".join(r.stderr.split()))
        self.assertEqual(r.stdout.strip(), "", "a refused command printed a pid")
        time.sleep(1)
        self.assertFalse(os.path.exists(os.path.join(self.root, "c.launch.log")), "a refused command still wrote a log")

    def test_the_bound_is_exactly_8000_characters_of_bash_argument(self):
        log = self.log("d.launch.log")
        base = "echo edge-ok; echo "
        pad = LIMIT - bash_arg_len(base, log)
        at_limit = base + "x" * pad
        self.assertEqual(bash_arg_len(at_limit, log), LIMIT)
        r = self.detach("-Command", at_limit, "-Log", log)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIsNotNone(self.read_log("d.launch.log", "edge-ok"), "an argument of exactly 8000 did not start")
        r = self.detach("-Command", at_limit + "x", "-Log", self.log("e.launch.log"))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("above the 8000", " ".join(r.stderr.split()))

    def test_a_missing_bash_is_refused_with_no_pid(self):
        r = self.detach("-Command", "echo x", "-Log", self.log("f.launch.log"), "-Bash", os.path.join(self.root, "no-bash.exe"))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("bash not found", " ".join(r.stderr.split()))
        self.assertEqual(r.stdout.strip(), "")

    def test_a_missing_log_argument_is_refused(self):
        r = self.detach("-Command", "echo x")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_the_process_is_started_with_no_console_window(self):
        # The window is a property of how the process is created, so the pin reads the script: no shell execute and
        # CreateNoWindow on, the only start that leaves no Windows Terminal window behind.
        with open(DETACH, encoding="utf-8") as f:
            src = f.read()
        self.assertRegex(src, r"(?m)^\$psi\.UseShellExecute = \$false\s*$")
        self.assertRegex(src, r"(?m)^\$psi\.CreateNoWindow = \$true\s*$")
        self.assertIsNone(re.search(r"RedirectStandard", src), "output must go through bash redirects, not .NET pipes")


BASH = os.environ.get("GATE_DETACH_BASH") or r"C:\Program Files\Git\bin\bash.exe"
PIPE_TIMEOUT = 60


def posix(path):
    win = os.path.abspath(path)
    return "/" + win[0].lower() + win[2:].replace("\\", "/")


@unittest.skipUnless(os.path.isfile(BASH), "Git bash not found (set GATE_DETACH_BASH)")
class GateDetachPipeTest(unittest.TestCase):
    """The pipe guard: called from Git bash as `gate-detach.ps1 ... 2>&1 | tail -2`, the launcher returns at once
    although the job runs on, because no handle of the calling shell is inherited by the job. Each call goes through
    Git bash -lc, as a tool shell runs it, with the shell's own stdout on a file, so only the pipe inside the command
    line can hold the call open. A timeout is a failure."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gdp-")
        self.p = posix(self.root)
        self.detach = DETACH.replace("\\", "/")

    def tearDown(self):
        for _ in range(30):
            try:
                shutil.rmtree(self.root)
                return
            except OSError:
                time.sleep(1)

    def shell(self, cmdline):
        """Runs one line through Git bash -lc; returns (exit code, seconds, output)."""
        out_path = os.path.join(self.root, "shell.out")
        t0 = time.time()
        with open(out_path, "wb") as out:
            try:
                rc = subprocess.run([BASH, "-lc", cmdline], stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, timeout=PIPE_TIMEOUT).returncode
            except subprocess.TimeoutExpired:
                self.fail("timed out after %d s: %s" % (PIPE_TIMEOUT, cmdline))
        secs = time.time() - t0
        with open(out_path, encoding="utf-8", errors="replace") as f:
            return rc, secs, f.read()

    def test_a_piped_call_returns_at_once(self):
        cmdline = "powershell -NoProfile -File '%s' -Command 'sleep 20' -Log %s/p.log 2>&1 | tail -2" % (
            self.detach, self.p)
        rc, secs, out = self.shell(cmdline)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out.strip(), r"^\d+$")
        self.assertLess(secs, 6, "the piped call waited %.1f s, the job's length" % secs)

    def test_a_piped_jobs_own_output_reaches_its_log(self):
        cmdline = ("powershell -NoProfile -File '%s' -Command 'echo out-c; echo err-c >&2; sleep 2; echo late-c' "
                   "-Log %s/c.log 2>&1 | tail -2") % (self.detach, self.p)
        rc, _, out = self.shell(cmdline)
        self.assertEqual(rc, 0, out)
        path = os.path.join(self.root, "c.log")
        end = time.time() + 30
        text = ""
        while time.time() < end and "late-c" not in text:
            time.sleep(0.5)
            if os.path.exists(path):
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
        self.assertEqual(text.split(), ["out-c", "err-c", "late-c"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
