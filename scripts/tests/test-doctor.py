"""Tests for scripts/doctor.py:
python scripts/tests/test-doctor.py

Nothing is executed for real: `shutil.which` and the module-level `run` are replaced, so the
checks are driven from the fake tool table below on any OS."""
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "doctor.py")
spec = importlib.util.spec_from_file_location("doctor", SCRIPT)
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.saved_which = doctor.shutil.which
        self.saved_run = doctor.run
        self.saved_windows = doctor.WINDOWS

        doctor.WINDOWS = False  # the Git Bash check is Windows-only and is covered on its own
        self.present = {"git", "claude", "herdr"}
        self.versions = {
            "git": "git version 2.54.0",
            "claude": "2.1.250 (Claude Code)",
            "herdr": doctor.read_verified(),
        }
        self.missing_subcommands = set()
        doctor.shutil.which = self.fake_which
        doctor.run = self.fake_run

    def tearDown(self):
        doctor.shutil.which = self.saved_which
        doctor.run = self.saved_run
        doctor.WINDOWS = self.saved_windows

    # the fake machine ----------------------------------------------------

    def fake_which(self, name):
        return ("/usr/bin/" + name) if name in self.present else None

    def fake_run(self, cmd, timeout=30):
        tool = os.path.basename(cmd[0])
        if cmd[-1] == "--version":
            return (0, self.versions[tool]) if self.versions.get(tool) else (1, "")
        if cmd[-1] == "--exec-path":
            return 0, "/usr/lib/git-core"
        if cmd[-1] == "--help":
            subcommand = " ".join(cmd[1:-1])
            return (1, "unrecognized subcommand") if subcommand in self.missing_subcommands else (0, "")
        return 1, ""

    def capture(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = doctor.main(argv)
        return code, buffer.getvalue()

    def line(self, output, name):
        """The check line for `name`, or an empty string. Columns are separated by two spaces."""
        pattern = re.compile(r"^(?:ok|warn|FAIL)\s{2,}" + re.escape(name) + r"\s{2,}\S")
        for line in output.splitlines():
            if pattern.match(line):
                return line
        return ""

    def failed(self, output):
        return [line for line in output.splitlines() if line.startswith("FAIL")]

    # the cases -----------------------------------------------------------

    def test_everything_present_and_matching_passes(self):
        code, output = self.capture([])
        self.assertEqual(code, 0, output)
        self.assertEqual(self.failed(output), [])
        self.assertTrue(self.line(output, "git").startswith("ok"))
        self.assertTrue(self.line(output, "claude").startswith("ok"))
        self.assertTrue(self.line(output, "herdr").startswith("ok"))
        for subcommand in doctor.read_surface():
            self.assertTrue(self.line(output, "herdr " + subcommand).startswith("ok"), subcommand)
        self.assertIn("no required check failed", output)
        self.assertIn(doctor.INTEGRATION_COMMAND, output)

    def test_git_missing_is_a_required_failure(self):
        self.present.discard("git")
        code, output = self.capture([])
        self.assertEqual(code, 1)
        self.assertTrue(self.line(output, "git").startswith("FAIL"), output)
        self.assertIn("not on PATH", self.line(output, "git"))
        self.assertIn("required checks failed: git", output)

    def test_claude_missing_is_a_required_failure(self):
        self.present.discard("claude")
        code, output = self.capture([])
        self.assertEqual(code, 1)
        self.assertTrue(self.line(output, "claude").startswith("FAIL"), output)

    def test_a_tool_that_does_not_answer_its_version_only_warns(self):
        self.versions["claude"] = ""
        code, output = self.capture([])
        self.assertEqual(code, 0, output)
        self.assertTrue(self.line(output, "claude").startswith("warn"), output)

    def test_herdr_absent_is_a_warning_and_never_fails(self):
        self.present.discard("herdr")
        code, output = self.capture([])
        self.assertEqual(code, 0, output)
        line = self.line(output, "herdr")
        self.assertTrue(line.startswith("warn"), output)
        self.assertIn("not installed (optional)", line)
        self.assertIn("herdr/README.md", line)
        self.assertNotIn("herdr agent list", output)  # the surface is not probed without herdr

    def test_a_herdr_version_drift_warns_and_names_both_versions(self):
        self.versions["herdr"] = "herdr 0.9.0-preview.2099-01-01-ffffffffffff"
        code, output = self.capture([])
        self.assertEqual(code, 0, output)
        line = self.line(output, "herdr")
        self.assertTrue(line.startswith("warn"), output)
        self.assertIn("version differs from the verified one", line)
        self.assertIn(self.versions["herdr"], line)
        self.assertIn(doctor.read_verified(), line)
        self.assertIn("update herdr/verified-version.txt", line)

    def test_a_moved_subcommand_fails_its_line_but_not_the_run(self):
        self.missing_subcommands = {"agent prompt"}
        code, output = self.capture([])
        self.assertEqual(code, 0, output)  # Herdr is optional, so the exit code stays 0
        self.assertTrue(self.line(output, "herdr agent prompt").startswith("FAIL"), output)
        self.assertIn("missing or renamed", self.line(output, "herdr agent prompt"))
        self.assertTrue(self.line(output, "herdr agent list").startswith("ok"), output)
        self.assertIn("no required check failed", output)
        self.assertIn("re-verify the code that drives it", output)

    def test_the_surface_file_lists_the_subcommands_the_kit_drives(self):
        surface = doctor.read_surface()
        self.assertIsNotNone(surface)
        self.assertEqual(len(surface), len(set(surface)))
        for wanted in ("agent list", "agent prompt", "agent wait", "agent send-keys", "tab create",
                       "pane run", "workspace list", "notification show", "server reload-config"):
            self.assertIn(wanted, surface)

    def test_json_output_parses_and_carries_every_check(self):
        code, output = self.capture(["--json"])
        self.assertEqual(code, 0, output)
        items = json.loads(output)
        self.assertIsInstance(items, list)
        self.assertEqual(len(items), 3 + len(doctor.read_surface()) + 1)  # tools + surface + herdr
        for item in items:
            self.assertEqual(set(item), {"status", "name", "detail", "required"})
            self.assertIn(item["status"], ("ok", "warn", "FAIL"))
            self.assertIsInstance(item["required"], bool)
        self.assertEqual([i["name"] for i in items][:3], ["git", "python", "claude"])

    def test_json_output_reports_a_required_failure_too(self):
        self.present.discard("git")
        code, output = self.capture(["--json"])
        self.assertEqual(code, 1)
        items = json.loads(output)
        broken = [i for i in items if i["status"] == "FAIL"]
        self.assertEqual([i["name"] for i in broken], ["git"])
        self.assertTrue(broken[0]["required"])

    def test_the_windows_git_bash_check_reports_the_file_it_found(self):
        doctor.WINDOWS = True
        found = {}

        def fake_is_file(path):
            return str(path).replace("\\", "/").endswith("Git/bin/bash.exe")

        saved = doctor.is_file
        doctor.is_file = fake_is_file
        try:
            self.versions["git"] = "git version 2.54.0"
            original = self.fake_run

            def with_windows_exec_path(cmd, timeout=30):
                if cmd[-1] == "--exec-path":
                    return 0, "C:/Program Files/Git/mingw64/libexec/git-core"
                return original(cmd, timeout)

            doctor.run = with_windows_exec_path
            code, output = self.capture([])
            found["line"] = self.line(output, "git bash")
        finally:
            doctor.is_file = saved
        self.assertEqual(code, 0, found["line"])
        self.assertTrue(found["line"].startswith("ok"), found["line"])
        self.assertIn("bash.exe", found["line"])

    def test_the_windows_git_bash_check_fails_when_nothing_is_found(self):
        doctor.WINDOWS = True
        saved = doctor.is_file
        doctor.is_file = lambda path: False
        try:
            code, output = self.capture([])
            line = self.line(output, "git bash")
        finally:
            doctor.is_file = saved
        self.assertEqual(code, 1)
        self.assertTrue(line.startswith("FAIL"), line)
        self.assertIn("WSL", line)


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1).result.wasSuccessful() is False)
