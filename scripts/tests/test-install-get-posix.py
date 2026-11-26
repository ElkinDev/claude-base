"""install/get.sh, the one-line installer for macOS and Linux:
python3 scripts/tests/test-install-get-posix.py

The repository it clones is a throwaway one made here, whose install.py and scripts/doctor.py only
record how they were called, so these cases test get.sh and nothing below it (the installer has its
own suite, test-install-posix.py). HOME is a temp folder; nothing reaches the network. POSIX only.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GET = os.path.abspath(os.path.join(HERE, "..", "..", "install", "get.sh"))

RECORDER = """import os, sys
with open(os.path.join(os.environ["GET_TEST_LOG"]), "a") as h:
    h.write("%s %s\\n" % (os.path.basename(sys.argv[0]), " ".join(sys.argv[1:])))
"""


@unittest.skipIf(os.name == "nt", "get.sh is the macOS and Linux one-liner")
class GetShTest(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="kit-get-"))
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.log = os.path.join(self.base, "calls.log")
        self.repo = os.path.join(self.base, "origin")
        os.makedirs(os.path.join(self.repo, "scripts"))
        for rel in ("install.py", os.path.join("scripts", "doctor.py")):
            with open(os.path.join(self.repo, rel), "w") as handle:
                handle.write(RECORDER)
        git = ["git", "-C", self.repo, "-c", "user.email=a@b.c", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        subprocess.run(git + ["add", "."], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "x"], check=True)
        self.dir = os.path.join(self.home, "claude-base")
        self.bin = os.path.join(self.base, "bin")
        os.makedirs(self.bin)
        # A claude on PATH, so the tool check passes unless a case says otherwise.
        self.fake("claude", "#!/bin/sh\necho 2.0.0\n")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def fake(self, name, body):
        path = os.path.join(self.bin, name)
        with open(path, "w") as handle:
            handle.write(body)
        os.chmod(path, 0o755)

    def run_get(self, *args, answer="n", missing="", extra_env=None):
        env = {"HOME": self.home, "PATH": os.pathsep.join([self.bin, os.path.dirname(sys.executable), "/usr/bin", "/bin"]),
               "CLAUDE_BASE_REPO": self.repo, "GET_TEST_LOG": self.log, "CLAUDE_BASE_NPM": "none",
               "TMPDIR": self.base}
        if answer is not None:
            env["CLAUDE_BASE_ANSWER"] = answer
        if missing:
            env["CLAUDE_BASE_MISSING"] = missing
        env.update(extra_env or {})
        done = subprocess.run(["bash", GET] + list(args), capture_output=True, text=True, env=env,
                              stdin=subprocess.DEVNULL, timeout=120)
        return done.returncode, done.stdout + done.stderr

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as handle:
            return handle.read().splitlines()

    def test_a_fresh_machine_clones_installs_and_runs_the_doctor(self):
        code, out = self.run_get()
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.isdir(os.path.join(self.dir, ".git")))
        self.assertEqual(self.calls(), ["install.py --permissions bypass --no-herdr", "doctor.py "])
        self.assertIn("cc in a new terminal (cc help lists the options)", out)

    def test_options_reach_the_installer(self):
        code, out = self.run_get("--herdr", "--permissions", "ask", "--no-hook-merge", "--auto-update")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls()[0], "install.py --permissions ask --no-hook-merge --auto-update --herdr")

    def test_the_powershell_spellings_work_too(self):
        code, out = self.run_get("-NoHerdr", "-Permissions", "ask")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls()[0], "install.py --permissions ask --no-herdr")

    def test_enter_is_yes_to_herdr(self):
        code, out = self.run_get(answer="")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls()[0], "install.py --permissions bypass --herdr")

    def test_a_second_run_updates_the_clone(self):
        self.run_get()
        code, out = self.run_get()
        self.assertEqual(code, 0, out)
        self.assertIn("updating %s" % self.dir, out)

    def test_a_folder_that_is_not_the_clone_is_refused(self):
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, "mine.txt"), "w") as handle:
            handle.write("x")
        code, out = self.run_get()
        self.assertEqual(code, 1)
        self.assertIn("is not a claude-base clone", out)
        self.assertEqual(self.calls(), [])

    def test_a_clone_of_another_repository_is_refused(self):
        subprocess.run(["git", "clone", "-q", self.repo, self.dir], check=True)
        subprocess.run(["git", "-C", self.dir, "remote", "set-url", "origin", "https://example.invalid/x.git"], check=True)
        code, out = self.run_get()
        self.assertEqual(code, 1)
        self.assertIn("not of %s" % self.repo, out)

    def test_a_dry_run_clones_nothing(self):
        code, out = self.run_get("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("would clone", out)
        self.assertFalse(os.path.exists(self.dir))

    def test_a_declined_claude_stops_before_the_kit(self):
        code, out = self.run_get(missing="claude", answer="n")
        self.assertEqual(code, 1)
        self.assertIn("Stopped: Claude Code is required", out)
        self.assertFalse(os.path.exists(self.dir))

    def test_a_missing_claude_is_installed_from_its_installer_saved_as_a_file(self):
        # The fake installer puts claude in ~/.local/bin, as the real one does, a folder this shell
        # does not have on PATH yet: get.sh has to find it there.
        target = os.path.join(self.home, ".local", "bin", "claude")
        installer = os.path.join(self.base, "claude-install.sh")
        with open(installer, "w") as handle:
            handle.write("mkdir -p '%s' && printf '#!/bin/sh\\n' > '%s' && chmod +x '%s'\n"
                         % (os.path.dirname(target), target, target))
        os.remove(os.path.join(self.bin, "claude"))
        code, out = self.run_get(answer="y", extra_env={"CLAUDE_BASE_CLAUDE_INSTALLER": installer})
        self.assertEqual(code, 0, out)
        self.assertIn("official installer (%s)" % installer, out)
        self.assertIn("ok    Claude Code", out)
        self.assertTrue(os.path.isfile(target))

if __name__ == "__main__":
    unittest.main()
