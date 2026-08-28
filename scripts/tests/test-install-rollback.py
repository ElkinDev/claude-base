"""Rollback cases driven by install.ps1 itself:
python scripts/tests/test-install-rollback.py

The hand built fixtures in test-kit-restore.py cover the restore tool on its own. These two cases
run the real installer into a throwaway home under TEMP and then roll it back, so the two halves of
the backup format cannot drift apart unnoticed. Windows only: install.ps1 is the Windows
installer, and install/install.sh (F01) will get its own file."""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "kit-restore.py")
spec = importlib.util.spec_from_file_location("kit_restore", SCRIPT)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)
INSTALLER = os.path.abspath(os.path.join(HERE, "..", "..", "install.ps1"))


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def run_restore(argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = restore.main(argv)
    return code, buffer.getvalue()


@unittest.skipUnless(os.name == "nt", "install.ps1 is the Windows installer")
class InstallerRollbackTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="kit-rollback-test-")
        self.profile = os.path.join(self.base, "user")
        self.home = os.path.join(self.profile, ".claude")
        self.manifest = os.path.join(self.home, ".kit-manifest.json")
        self.saved = os.environ.copy()
        os.environ["KIT_HOME"] = self.home
        os.environ["USERPROFILE"] = self.profile

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)
        shutil.rmtree(self.base, ignore_errors=True)

    def install(self):
        done = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", INSTALLER],
            capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout

    def managed(self):
        return sorted(json.loads(read_text(self.manifest))["files"])

    def stamps(self):
        return sorted(os.listdir(os.path.join(self.home, "backups")))

    def test_rolling_back_a_run_that_kept_my_file_leaves_my_file_alone(self):
        """A second run over a file the adopter edited records it as kept, and the rollback of
        that run leaves it exactly as the adopter wrote it."""
        self.install()
        mine = os.path.join(self.home, "CLAUDE.md")
        self.assertTrue(os.path.isfile(mine), "the installer writes the kit CLAUDE.md")
        with open(mine, "w", encoding="utf-8") as handle:
            handle.write("my own rules, merged by hand")
        output = self.install()
        # Not the word "kept" on its own: the summary line carries it whatever the run did.
        self.assertIn("backup+new   " + mine, output)
        stamp = self.stamps()[-1]
        recorded = read_text(os.path.join(self.home, "backups", stamp, "manifest.txt"))
        self.assertIn("kept " + mine, recorded)
        self.assertNotIn("overwritten " + mine, recorded)
        code, output = run_restore(["--stamp", stamp])
        self.assertEqual(code, 0, output)
        self.assertIn("0 skipped", output)
        self.assertEqual(read_text(mine), "my own rules, merged by hand")
        self.assertTrue(os.path.isfile(mine + ".new"), "the kit version is still beside it")

    def test_a_run_after_the_record_was_lost_takes_the_files_back_onto_the_record(self):
        """The manifest is the only thing that says a file is the kit's. If it is lost, a re-run
        finds every file byte identical and would otherwise walk past all of them, leaving the
        record at nothing and the first run's rollback with nothing to remove."""
        self.install()
        first = self.stamps()[-1]
        installed = self.managed()
        self.assertGreater(len(installed), 10, "the install manages more than a handful of files")
        with open(self.manifest, "w", encoding="utf-8") as handle:
            handle.write('{"version": 1, "installed": "2026-08-28T10:00:00", "files": {}}')
        self.install()
        self.assertEqual(self.managed(), installed, "every kit file is on the record again")
        code, output = run_restore(["--stamp", first])
        self.assertEqual(code, 0, output)
        self.assertIn("0 skipped", output)
        for path in installed:
            self.assertFalse(os.path.exists(path), path + " was put back the way it was found")


if __name__ == "__main__":
    unittest.main(verbosity=1)
