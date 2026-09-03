"""Tests for claude/hooks/guard-read.py on both of its matchers, Read and the shell tools.

    python scripts/tests/test-guard-read.py

The hook runs the way the harness runs it, as a subprocess with the payload JSON on stdin,
against fixture files in a temp folder. Nothing under the user's home is touched.

The shell matcher exists because the Read tool is not the only way a file reaches the window:
`cat`, `sed -n`, `head`, `tail`, `type` and `Get-Content` put the same bytes there, and a
harness re-injects a Read-tool file at every compaction, so an orchestrator that follows the
context rules reads with the shell and would otherwise walk past the guard.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
HOOKS = os.path.join(ROOT, "claude", "hooks")
GUARD = os.path.join(HOOKS, "guard-read.py")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The two numbers come from the hook itself: a copy here would keep passing after the hook moved.
shell_read = load("shell_read", os.path.join(HOOKS, "shell_read.py"))
SIZE_LIMIT_BYTES = shell_read.SIZE_LIMIT_BYTES
ALLOWED_LIMIT_LINES = shell_read.ALLOWED_LIMIT_LINES


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
        cls.pdf = os.path.join(cls.tmp, "manual.pdf")
        with open(cls.pdf, "wb") as handle:
            handle.write(b"%PDF-1.7\n" + b"\x00" * (200 * 1024))
        # The three text sizes the rule turns on: one clearly over, and the two neighbours of
        # the 48 KB line, so a moved constant is caught by a test and not by a lane.
        cls.sixty = cls.text_file("sixty.txt", 60 * 1024)
        cls.over = cls.text_file("just-over.txt", 49 * 1024)
        cls.under = cls.text_file("just-under.txt", 47 * 1024)

    @classmethod
    def text_file(cls, name, size):
        """A text file of exactly `size` bytes, in lines the guard can count."""
        path = os.path.join(cls.tmp, name)
        line = "%s\n" % ("x" * 79)
        whole, remainder = divmod(size, len(line))
        body = line * whole
        if remainder:
            body += "y" * (remainder - 1) + "\n"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(body)
        assert os.path.getsize(path) == size, os.path.getsize(path)
        return path

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

    # ------------------------------------------------------------- where the line falls
    def test_the_limit_is_forty_eight_kilobytes(self):
        self.assertEqual(SIZE_LIMIT_BYTES, 48 * 1024)
        self.assertEqual(ALLOWED_LIMIT_LINES, 400)

    def test_a_sixty_kilobyte_text_file_is_denied_without_a_limit(self):
        reason = self.denial(self.read(self.sixty), role=None)
        self.assertIn("60 KB", reason)
        self.assertIn("768 lines", reason)
        self.denial(self.bash('cat "%s"' % self.sixty))

    def test_the_same_sixty_kilobytes_pass_with_a_limit_of_four_hundred(self):
        self.allowed(self.read(self.sixty, limit=ALLOWED_LIMIT_LINES))
        self.allowed(self.bash("sed -n '1,400p' \"%s\"" % self.sixty))

    def test_forty_nine_kilobytes_are_denied_and_forty_seven_are_allowed(self):
        self.denial(self.read(self.over), role=None)
        self.allowed(self.read(self.under), role=None)
        self.denial(self.bash('cat "%s"' % self.over))
        self.allowed(self.bash('cat "%s"' % self.under))

    def test_a_byte_slice_is_measured_against_the_same_limit(self):
        # `head -c N` is bounded by bytes, so the guard reads N against the ceiling itself.
        # The boundary follows the file rule: at exactly the ceiling it passes, one over it does
        # not, which is the same comparison that lets a file of exactly 48 KB be read whole.
        self.allowed(self.bash('head -c %d "%s"' % (SIZE_LIMIT_BYTES - 1, self.big)))
        self.allowed(self.bash('head -c %d "%s"' % (SIZE_LIMIT_BYTES, self.big)))
        self.denial(self.bash('head -c %d "%s"' % (SIZE_LIMIT_BYTES + 1, self.big)))

    # ------------------------------------------------------------- pixels and pages are exempt
    def test_a_two_hundred_kilobyte_png_reaches_a_lane_on_both_routes(self):
        self.allowed(self.read(self.image), role=None)
        self.allowed(self.bash('cat "%s"' % self.image), role=None)
        self.allowed(self.bash('head -c 200000 "%s"' % self.image), role=None)
        self.allowed(self.bash('Get-Content "%s"' % self.image, tool="PowerShell"), role=None)

    def test_a_pdf_over_the_limit_is_exempt_on_both_routes_for_either_role(self):
        self.allowed(self.read(self.pdf), role=None)
        self.allowed(self.read(self.pdf))
        self.allowed(self.bash('cat "%s"' % self.pdf), role=None)
        self.allowed(self.bash('cat "%s"' % self.pdf))
        self.allowed(self.bash('Get-Content "%s"' % self.pdf, tool="PowerShell"))

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

    # ------------------------------------------------------------- an input redirect is a read
    def test_a_file_fed_through_an_input_redirect_is_still_read(self):
        self.denial(self.bash('cat < "%s"' % self.big))
        self.denial(self.bash('cat <"%s"' % self.big))
        self.assertIn("orchestrator", self.denial(self.bash('cat < "%s"' % self.image)))
        self.allowed(self.bash('cat < "%s"' % self.small))

    def test_a_slice_taken_through_a_redirect_is_allowed(self):
        self.allowed(self.bash('head -n 5 < "%s"' % self.big))
        self.allowed(self.bash('wc -l < "%s"' % self.big))

    def test_a_here_string_and_a_heredoc_name_no_file(self):
        self.allowed(self.bash("cat <<<'report.txt'"))
        self.allowed(self.bash("cat << report.txt\nline\nreport.txt"))

    # ------------------------------------------------------------- pipelines
    def test_the_last_stage_of_a_pipeline_prints_to_the_window(self):
        self.denial(self.bash('cat "%s" | cat' % self.big))
        self.denial(self.bash('cat "%s" | base64' % self.big))
        self.denial(self.bash('Get-Content "%s" | Out-String' % self.big, tool="PowerShell"))

    def test_a_stage_that_narrows_the_stream_keeps_the_pipeline_allowed(self):
        self.allowed(self.bash('cat "%s" | grep "line 000042"' % self.big))
        self.allowed(self.bash('cat "%s" | wc -l' % self.big))
        self.allowed(self.bash('cat "%s" | head -5 | cat' % self.big))
        self.allowed(self.bash('cat "%s" | cat > "%s"'
                               % (self.big, os.path.join(self.tmp, "copy.txt"))))

    # ------------------------------------------------------------- substitutions
    def test_a_command_substitution_is_parsed_like_any_other_command(self):
        self.denial(self.bash('echo $(cat "%s")' % self.big))
        self.denial(self.bash('echo `cat "%s"`' % self.big))
        self.denial(self.bash('echo $(echo $(cat "%s"))' % self.big))
        self.assertIn("orchestrator", self.denial(self.bash('echo $(cat "%s")' % self.image)))
        self.allowed(self.bash('echo $(head -n 5 "%s")' % self.big))

    def test_a_substitution_inside_single_quotes_is_literal(self):
        self.allowed(self.bash("""echo '$(cat "%s")'""" % self.big))

    def test_a_backtick_substitutes_in_bash_and_escapes_in_powershell(self):
        command = 'Write-Output "x`cat \"%s\"`y"' % self.big
        self.denial(self.bash(command))
        self.allowed(self.bash(command, tool="PowerShell"))

    # ------------------------------------------------------------- slice arithmetic
    def test_a_tail_offset_is_not_a_line_count(self):
        self.denial(self.bash('tail -n +100 "%s"' % self.big))
        self.denial(self.bash('tail -c +100 "%s"' % self.big))
        self.allowed(self.bash('tail -n 100 "%s"' % self.big))

    def test_a_sed_slice_written_with_e_is_allowed(self):
        self.allowed(self.bash("sed -n -e '1,200p' \"%s\"" % self.big))
        self.allowed(self.bash("sed -n -e '1,10p' -e '20,30p' \"%s\"" % self.big))
        self.allowed(self.bash("sed -ne '1,200p' \"%s\"" % self.big))

    def test_a_sed_that_prints_too_much_with_e_is_still_denied(self):
        self.denial(self.bash("sed -n -e '1,20000p' \"%s\"" % self.big))
        self.denial(self.bash("sed -n -e '1,200p' -e '300,20000p' \"%s\"" % self.big))
        self.denial(self.bash("sed -n -f slice.sed \"%s\"" % self.big))

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
