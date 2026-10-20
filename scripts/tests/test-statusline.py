"""Pins for the role badge of claude/statusline.ps1: the role the launcher passes in CLAUDE_ROLE is the first segment of
the line, with one colour per role; a session with no role gets no badge; $RoleByFolder relabels a lane or role-less
session by the folder of its transcript, and never a session that has another role.

  python scripts/tests/test-statusline.py              the statusline.ps1 under claude/
  STATUSLINE=<path> python scripts/tests/test-statusline.py   another copy (a staged one, or a mutant for a red run)

Windows only (PowerShell 5.1); skipped where powershell is not on PATH. Each case pipes a fixed payload with no
rate_limits, so nothing is logged, and the copies it edits live in a temp folder.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("STATUSLINE") or os.path.join(os.path.dirname(os.path.dirname(HERE)), "claude", "statusline.ps1")
ESC = "\x1b["


def payload(folder="C--work-my-app"):
    return ('{"model":{"display_name":"Opus 5"},"effort":{"level":"high"},"workspace":{"current_dir":"%s"},'
            '"transcript_path":"%s/projects/%s/s1.jsonl","context_window":{"used_percentage":12}}'
            % (tempfile.gettempdir().replace("\\", "/"), tempfile.gettempdir().replace("\\", "/"), folder))


@unittest.skipUnless(shutil.which("powershell"), "PowerShell not on PATH")
class StatuslineRoleTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="slr-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def render(self, role, script=SCRIPT, folder="C--work-my-app"):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_ROLE"}
        if role is not None:
            env["CLAUDE_ROLE"] = role
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
                           input=payload(folder).encode("utf-8"), capture_output=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        return r.stdout.decode("utf-8", "replace")

    def with_map(self, entry):
        with open(SCRIPT, "rb") as f:
            text = f.read().decode("ascii")
        line = "$RoleByFolder = @{}"
        self.assertEqual(text.count(line), 1, "the $RoleByFolder line moved")
        path = os.path.join(self.root, "statusline.ps1")
        with open(path, "wb") as f:
            f.write(text.replace(line, "$RoleByFolder = @{ " + entry + " }", 1).encode("ascii"))
        return path

    def test_each_role_is_the_first_segment_with_its_colour(self):
        for role, colour in (("analyst", "1;97;44"), ("orchestrator", "1;97;45"), ("lane", "30;47"),
                             ("research", "1;30;42"), ("reviewer", "1;30;43")):
            line = self.render(role)
            self.assertTrue(line.startswith(ESC + colour + "m " + role.upper() + " " + ESC + "0m"), (role, line[:40]))

    def test_role_is_read_trimmed_and_case_folded(self):
        self.assertTrue(self.render("  Analyst ").startswith(ESC + "1;97;44m ANALYST "))

    def test_no_role_no_badge(self):
        for role in (None, ""):
            line = self.render(role)
            self.assertTrue(line.startswith(ESC + "95;1m"), line[:40])
            self.assertNotIn(" LANE ", line)

    def test_folder_map_relabels_a_lane_by_its_transcript_folder(self):
        mapped = self.with_map("'C--work-my-app' = 'orchestrator my-app'")
        self.assertTrue(self.render("lane", mapped).startswith(ESC + "1;97;45m ORCHESTRATOR MY-APP "))
        self.assertTrue(self.render(None, mapped).startswith(ESC + "1;97;45m ORCHESTRATOR MY-APP "))
        self.assertTrue(self.render("lane", mapped, folder="C--work-other").startswith(ESC + "30;47m LANE "))

    def test_folder_map_never_relabels_another_role(self):
        mapped = self.with_map("'C--work-my-app' = 'orchestrator my-app'")
        self.assertTrue(self.render("analyst", mapped).startswith(ESC + "1;97;44m ANALYST "))


if __name__ == "__main__":
    unittest.main(verbosity=1)
