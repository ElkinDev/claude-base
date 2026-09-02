"""Tests for claude/hooks/guard-read.py on both of its matchers, Read and the shell tools.

    python scripts/tests/test-guard-read.py

The hook runs the way the harness runs it, as a subprocess with the payload JSON on stdin,
against fixture files in a temp folder. Nothing under the user's home is touched.

The shell matcher exists because the Read tool is not the only way a file reaches the window:
`cat`, `sed -n`, `head`, `tail`, `type` and `Get-Content` put the same bytes there, and a
harness re-injects a Read-tool file at every compaction, so an orchestrator that follows the
context rules reads with the shell and would otherwise walk past the guard.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
GUARD = os.path.join(ROOT, "claude", "hooks", "guard-read.py")
SIZE_LIMIT_BYTES = 150 * 1024


def run_guard(payload, role="orchestrator"):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if role is None:
        env.pop("CLAUDE_ROLE", None)
    else:
        env["CLAUDE_ROLE"] = role
    process = subprocess.run(
        [sys.executable, GUARD],
        input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return process.returncode, process.stdout.decode("utf-8", "replace"), process.stderr.decode("utf-8", "replace")


class GuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="guard-read-")
        cls.image = os.path.join(cls.tmp, "screen.png")
        with open(cls.image, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * (200 * 1024))
        cls.big = os.path.join(cls.tmp, "report.txt")
        with open(cls.big, "w", encoding="utf-8") as handle:
            handle.write("".join("line %06d %s\n" % (i, "x" * 120) for i in range(40000)))
        cls.small = os.path.join(cls.tmp, "notes.md")
        with open(cls.small, "w", encoding="utf-8") as handle:
            handle.write("# notes\n\nshort enough to read whole.\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def bash(self, command, tool="Bash", cwd=None):
        return {
            "tool_name": tool,
            "tool_input": {"command": command},
            "cwd": cwd or self.tmp,
            "transcript_path": "",
        }

    def read(self, path, limit=None):
        tool_input = {"file_path": path}
        if limit is not None:
            tool_input["limit"] = limit
        return {"tool_name": "Read", "tool_input": tool_input, "cwd": self.tmp, "transcript_path": ""}

    def denial(self, payload, role="orchestrator"):
        code, out, err = run_guard(payload, role)
        self.assertEqual(code, 0, err)
        self.assertNotEqual(out, "", "expected a denial, got nothing")
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny", decision)
        return decision["permissionDecisionReason"]

    def allowed(self, payload, role="orchestrator"):
        code, out, err = run_guard(payload, role)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "", "expected no decision, got: %s" % out)

    # ------------------------------------------------------------- fixtures hold
    def test_the_fixtures_are_on_the_right_side_of_the_limit(self):
        self.assertGreater(os.path.getsize(self.image), SIZE_LIMIT_BYTES)
        self.assertGreater(os.path.getsize(self.big), 5 * 1024 * 1024)
        self.assertLess(os.path.getsize(self.small), SIZE_LIMIT_BYTES)

    # ------------------------------------------------------------- images
    def test_a_bash_cat_of_an_image_is_denied_for_the_orchestrator(self):
        reason = self.denial(self.bash('cat "%s"' % self.image))
        self.assertIn("orchestrator", reason)
        self.assertIn(self.image, reason)

    def test_a_bash_cat_of_an_image_is_allowed_for_a_lane(self):
        self.allowed(self.bash('cat "%s"' % self.image), role=None)

    def test_a_subagent_of_the_orchestrator_may_cat_an_image(self):
        payload = self.bash('cat "%s"' % self.image)
        payload["agent_id"] = "abc123"
        self.allowed(payload)

    # ------------------------------------------------------------- size
    def test_a_sed_over_a_five_megabyte_file_is_denied(self):
        reason = self.denial(self.bash("sed -n '1,20000p' \"%s\"" % self.big))
        self.assertIn(self.big, reason)
        self.assertIn("%d KB" % (os.path.getsize(self.big) // 1024), reason)
        self.assertIn("40000 lines", reason)

    def test_a_sed_with_a_range_under_the_line_limit_is_allowed(self):
        self.allowed(self.bash("sed -n '1,200p' \"%s\"" % self.big))

    def test_a_sed_without_quiet_prints_the_whole_file_and_is_denied(self):
        self.denial(self.bash("sed 's/x/y/' \"%s\"" % self.big))

    def test_a_sed_in_place_is_not_a_read(self):
        self.allowed(self.bash("sed -i 's/x/y/' \"%s\"" % self.big))

    def test_a_cat_of_a_big_file_is_denied(self):
        reason = self.denial(self.bash('cat -n "%s"' % self.big))
        self.assertIn("sed -n", reason)
        self.assertIn("grep -n", reason)

    def test_head_and_tail_default_to_ten_lines_and_are_allowed(self):
        self.allowed(self.bash('head "%s"' % self.big))
        self.allowed(self.bash('tail -f "%s"' % self.big))

    def test_head_with_a_count_under_the_limit_is_allowed(self):
        self.allowed(self.bash('head -n 200 "%s"' % self.big))
        self.allowed(self.bash('head -400 "%s"' % self.big))

    def test_head_with_a_count_over_the_limit_is_denied(self):
        self.denial(self.bash('head -n 5000 "%s"' % self.big))

    def test_a_cat_of_a_small_markdown_file_is_allowed(self):
        self.allowed(self.bash('cat "%s"' % self.small))

    def test_a_missing_file_is_allowed(self):
        self.allowed(self.bash('cat "%s"' % os.path.join(self.tmp, "not-here.txt")))

    # ------------------------------------------------------------- everything else passes
    def test_a_git_command_is_allowed(self):
        self.allowed(self.bash("git log --oneline -20"))
        self.allowed(self.bash('git show HEAD:"%s"' % self.big))

    def test_a_grep_over_a_big_file_is_allowed(self):
        self.allowed(self.bash('grep -n "line 000042" "%s"' % self.big))

    def test_a_heredoc_is_not_a_read(self):
        self.allowed(self.bash("python - <<'PY'\nprint(1)\nPY"))

    def test_output_that_goes_somewhere_else_is_not_context(self):
        self.allowed(self.bash('cat "%s" | head -5' % self.big))
        self.allowed(self.bash('cat "%s" > "%s"' % (self.big, os.path.join(self.tmp, "copy.txt"))))

    def test_a_read_after_a_separator_is_still_checked(self):
        self.denial(self.bash('cd "%s" && cat "%s"' % (self.tmp, self.big)))
        self.denial(self.bash('echo start; cat "%s"' % self.big))

    def test_a_relative_path_resolves_against_the_session_cwd(self):
        self.denial(self.bash("cat report.txt"))
        self.allowed(self.bash("cat notes.md"))

    def test_a_git_bash_style_path_is_recognised_on_windows(self):
        if os.name != "nt":
            self.skipTest("git bash paths are a Windows shape")
        drive, rest = os.path.splitdrive(self.big)
        posix = "/%s%s" % (drive[0].lower(), rest.replace("\\", "/"))
        self.denial(self.bash("cat %s" % posix))

    # ------------------------------------------------------------- PowerShell shape
    def test_get_content_of_a_big_file_is_denied(self):
        self.denial(self.bash('Get-Content "%s"' % self.big, tool="PowerShell"))
        self.denial(self.bash('type "%s"' % self.big, tool="PowerShell"))

    def test_get_content_with_a_count_is_allowed(self):
        self.allowed(self.bash('Get-Content "%s" -TotalCount 200' % self.big, tool="PowerShell"))
        self.allowed(self.bash('Get-Content -Path "%s" -Tail 50' % self.big, tool="PowerShell"))

    # ------------------------------------------------------------- the Read matcher is untouched
    def test_the_read_matcher_still_denies_a_big_file_without_a_limit(self):
        reason = self.denial(self.read(self.big), role=None)
        self.assertIn("offset and limit", reason)

    def test_the_read_matcher_still_allows_a_bounded_read(self):
        self.allowed(self.read(self.big, limit=200))

    def test_the_read_matcher_still_denies_an_image_to_the_orchestrator(self):
        self.assertIn("orchestrator", self.denial(self.read(self.image)))

    def test_an_unknown_tool_is_never_touched(self):
        self.allowed({"tool_name": "Edit", "tool_input": {"file_path": self.big}, "cwd": self.tmp})


if __name__ == "__main__":
    unittest.main(verbosity=2)
