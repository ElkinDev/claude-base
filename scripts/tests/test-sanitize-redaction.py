"""What the guard is allowed to print, and what it matches in a path.

python scripts/tests/test-sanitize-redaction.py   (needs git on PATH)

Every test here asserts the private term never reaches stdout, stderr or the JSON output, and
that a term in a file or directory name is caught the way one in the body is.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_case import (ADDRESS, CJK_NAME, CODENAME, FILE_WAIVER,  # noqa: E402
                           LINUX_HOME, SCRIPT, WIN_HOME, GuardCase, rules_of)

CAMEL = ["ZzquuxAppViewModel", "ZZQUUX_API_KEY", "zzquux_app_id", "MyZzquuxThing", "zzquux-app",
         "zzquux/x", "Zzquux.kt", "XZzquuxClient", "v2ZzquuxClient", "ZZQUUXapp", "ZZQUUXApp"]
NOT_A_TERM = ["zzquuxing", "ZZQUUXING", "prezzquux"]
# The documented limitation: an all-caps term followed by all-caps cannot be told apart from an
# ordinary word without false positives. The remedy is an explicit `regex:` line on the denylist.
GLUED = ["ZZQUUXAPP", "MYZZQUUX"]
# A short term that is also the head of ordinary words. Only the first two lines are the term.
PREFIX_HITS = ["ACHEPoint", "ACHE_KEY"]
PREFIX_MISSES = ["CACHED value", "a headache", "ACHEISH", "the cache"]


class RedactionTest(GuardCase):
    # what the output may carry -------------------------------------------

    def test_a_generic_finding_on_a_private_line_is_redacted(self):
        self.denylist(CODENAME)
        self.file("notes.md", "clone https://github.com/" + CODENAME + "/tool.git\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("tracker-github", rules_of(result.stdout))
        self.assertIn("redacted", result.stdout)
        self.assertNoTerm(result)

    def test_the_json_output_is_redacted_too(self):
        self.denylist(CODENAME)
        self.file("notes.md", "clone https://github.com/" + CODENAME + "/tool.git\n")
        result = self.run_guard("--json", "notes.md")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNoTerm(result)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["findings"]), 2, result.stdout)

    def test_a_waiver_reason_is_never_printed(self):
        self.denylist(CODENAME)
        self.file("notes.md",
                  "open " + LINUX_HOME + "/src   # sanitize-ok: home-linux the " + CODENAME
                  + " box\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("waived home-linux: notes.md:1", result.stdout)
        self.assertNoTerm(result)

    def test_a_private_path_is_printed_redacted(self):
        self.denylist(CODENAME)
        self.file("sub/" + CODENAME + "/leak.md", "open " + LINUX_HOME + "/data\n")
        result = self.run_guard("sub")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("private-path", rules_of(result.stdout))
        self.assertIn("<redacted path>", result.stdout)
        self.assertNoTerm(result)

    def test_the_denylist_itself_is_never_scanned(self):
        self.denylist(CODENAME, ADDRESS)
        self.file("notes.md", "nothing here\n")
        result = self.run_guard(".")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNoTerm(result)
        self.assertNotIn("acme-corp.io", result.stdout)

    def test_a_path_inside_the_denylist_folder_is_a_hard_failure(self):
        self.denylist(CODENAME)
        result = self.run_guard(os.path.join(".sanitize", "private-denylist.txt"))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(".sanitize", result.stderr)
        self.assertNoTerm(result)

    # names, not only contents --------------------------------------------

    def test_a_private_term_in_a_file_name_is_caught(self):
        self.denylist(CODENAME)
        self.file("fixtures/ZzquuxAppTest.kt", "class AppTest { }\n")
        result = self.run_guard("fixtures")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("private-path", rules_of(result.stdout))
        self.assertNoTerm(result)

    def test_an_address_in_a_file_name_is_caught(self):
        self.file("docs/" + ADDRESS + ".md", "a clean body\n")
        result = self.run_guard("docs")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("email", rules_of(result.stdout))
        self.assertIn("(name)", result.stdout)

    def test_a_non_ascii_path_reaches_a_console_that_cannot_encode_it(self):
        # The console the hook inherits on Windows is cp1252, which cannot encode this name.
        name = "fixtures/" + CJK_NAME + ".md"
        self.file(name, FILE_WAIVER + " sample\na body\n")
        result = self.run_guard("fixtures", env={"PYTHONIOENCODING": None, "PYTHONUTF8": None},
                                encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("skipped whole: fixtures/", result.stdout)
        self.assertIn("0 findings, 0 waivers, 0 files scanned", result.stdout)

    def test_a_report_the_reader_stops_reading_is_not_a_traceback(self):
        # A long report into a pipe nobody is draining. The exit code is what the caller reads,
        # and a traceback out of the report would read as the guard itself being broken.
        self.file("notes.md", ("open " + WIN_HOME + "\n") * 4001)
        proc = subprocess.Popen([sys.executable, SCRIPT, "notes.md"], cwd=self.repo,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=self.environment())
        proc.stdout.readline()
        proc.stdout.close()
        error = proc.stderr.read().decode("utf-8", "replace")
        proc.stderr.close()
        self.assertEqual(proc.wait(), 1, error)
        self.assertNotIn("Traceback", error)
        self.assertNotIn("Exception ignored", error)

    def test_a_range_name_scans_a_path_a_blob_moved_away_from(self):
        base = self.commit("initial commit")
        self.commit("add the note", name=CODENAME + "-doc.md", body="a clean body\n")
        self.git("mv", CODENAME + "-doc.md", "clean-doc.md")
        self.git("commit", "-q", "-m", "rename it")
        tip = self.git("rev-parse", "HEAD").stdout.strip()
        self.denylist(CODENAME)
        result = self.run_guard("--range", "%s..%s" % (base, tip))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("private-path", rules_of(result.stdout))
        self.assertNoTerm(result)

    def test_a_range_name_scans_every_path_that_shares_a_body(self):
        base = self.commit("initial commit")
        self.file("alpha.md", "the same clean body\n")
        self.file(CODENAME + "-notes.md", "the same clean body\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "two copies of one body")
        tip = self.git("rev-parse", "HEAD").stdout.strip()
        self.denylist(CODENAME)
        result = self.run_guard("--range", "%s..%s" % (base, tip))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("private-path", rules_of(result.stdout))
        self.assertNoTerm(result)

    def test_a_private_term_in_a_staged_path_is_caught(self):
        self.denylist(CODENAME)
        self.file("src/" + CODENAME + "-client.md", "a clean body\n")
        self.git("add", "-A")
        result = self.run_guard("--staged")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("private-path", rules_of(result.stdout))
        self.assertNoTerm(result)

    # boundaries ----------------------------------------------------------

    def test_the_term_is_found_across_camel_case_and_separators(self):
        self.denylist(CODENAME)
        self.file("notes.md", "\n".join(CAMEL) + "\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(rules_of(result.stdout), ["private-1"] * len(CAMEL), result.stdout)
        self.assertNoTerm(result)

    def test_the_term_inside_a_longer_word_is_not_a_match(self):
        self.denylist(CODENAME)
        self.file("notes.md", "\n".join(NOT_A_TERM) + "\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("0 findings, 0 waivers, 1 files scanned", result.stdout)

    def test_the_documented_glued_capitals_are_not_matched(self):
        self.denylist(CODENAME)
        self.file("notes.md", "\n".join(GLUED) + "\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("0 findings, 0 waivers, 1 files scanned", result.stdout)

    def test_a_short_term_is_told_apart_from_the_words_that_start_with_it(self):
        self.denylist("ache")
        self.file("notes.md", "\n".join(PREFIX_HITS + PREFIX_MISSES) + "\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(rules_of(result.stdout), ["private-1"] * len(PREFIX_HITS), result.stdout)
        for number in (1, 2):
            self.assertIn("private-1: notes.md:%d" % number, result.stdout)

    def test_a_denylist_term_is_caught_without_echoing_the_term(self):
        self.denylist(CODENAME)
        self.file("notes.md", "the " + CODENAME + " migration notes\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(rules_of(result.stdout), ["private-1"])
        self.assertNoTerm(result)

    def test_a_fixture_waiver_never_suppresses_a_private_rule(self):
        self.denylist(CODENAME)
        self.file("fixtures/vendor.txt",
                  FILE_WAIVER + " vendor sample\nthe " + CODENAME + " plan\nopen " + WIN_HOME
                  + "\n")
        result = self.run_guard("fixtures")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(rules_of(result.stdout), ["private-1"], result.stdout)
        self.assertIn("skipped whole: fixtures/vendor.txt", result.stdout)
        self.assertIn("1 findings, 0 waivers, 0 files scanned", result.stdout)
        self.assertNoTerm(result)

    def test_a_per_line_waiver_never_suppresses_a_private_rule(self):
        # Inside a fixtures directory as well as outside one: a fixture is the obvious place to
        # park a real name, so it is the one place the waiver must not be worth anything.
        self.denylist(CODENAME)
        marker = "sanitize-ok:" + " private-1 vendor sample"
        for folder in ("docs", "fixtures"):
            self.file(folder + "/vendor.txt", "the " + CODENAME + " plan   # " + marker + "\n")
            result = self.run_guard(folder)
            self.assertEqual(result.returncode, 1, folder + ": " + result.stdout)
            self.assertEqual(rules_of(result.stdout), ["waiver-abuse", "private-1"], folder)
            self.assertNotIn("waived", result.stdout)
            self.assertNoTerm(result)

    def test_a_catastrophic_private_regex_is_rejected_before_it_runs(self):
        self.denylist("regex:(a+)+$")
        self.file("notes.md", "a" * 42 + "!\n")
        result = self.run_guard("notes.md", timeout=90)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("rule private-1 rejected: nested quantifier", result.stderr)

    def test_an_ordinary_private_regex_still_loads_and_still_catches(self):
        self.denylist("regex:(?i)" + CODENAME + "[-_]?app")
        self.file("notes.md", CODENAME + "-app\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(rules_of(result.stdout), ["private-1"], result.stdout)
        self.assertNoTerm(result)

    def test_a_private_rule_that_does_not_compile_never_echoes_it(self):
        # A group name is the shape that puts the term inside re's own error message.
        self.denylist("regex:(?P<" + CODENAME + "-app>x)")
        self.file("notes.md", "a clean body\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("rule private-1 does not compile", result.stderr)
        self.assertNoTerm(result)

    def test_a_tracked_denylist_is_a_hard_failure(self):
        self.denylist(CODENAME)
        self.git("add", "-f", os.path.join(".sanitize", "private-denylist.txt"))
        result = self.run_guard("--all")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(".sanitize", result.stderr)
        self.assertNoTerm(result)

    # the allowlist -------------------------------------------------------

    def test_an_allowed_placeholder_does_not_clear_a_longer_name(self):
        self.file("notes.md", "cd /home/" + "you-know-who/repo\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(rules_of(result.stdout), ["home-linux"])

    def test_an_allowed_domain_does_not_clear_a_longer_domain(self):
        self.file("notes.md", "write to real.person@" + "example.com.attacker.io\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(rules_of(result.stdout), ["email"])

    def test_an_ssh_remote_does_not_exempt_the_org(self):
        self.file("notes.md",
                  "git@github.com:" + "acme-corp/widgets.git\n"
                  + "git@ssh.dev.azure.com:" + "v3/acme-corp/Widgets/api\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(rules_of(result.stdout), ["tracker-github", "tracker-azure"],
                         result.stdout)

    def test_a_credential_shape_is_never_allowlisted(self):
        self.file("notes.md", "Password=" + "you-should-not-pass\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(rules_of(result.stdout), ["conn-password"])


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1).result.wasSuccessful() is False)
