"""Tests for scripts/sanitize-check.py: the rules, the waiver policy, and the scan modes.

python scripts/tests/test-sanitize-check.py   (needs git on PATH)

The redaction, path-name and allowlist tests live in test-sanitize-redaction.py, the tag and
tree dispatch in test-sanitize-objects.py, and the hook tests in test-sanitize-hooks.py. Leak-shaped strings come from fixtures/sanitize/ or from the
assembled constants in sanitize_case.py, never written out here.
"""
import io
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_case import (ADDRESS, CODENAME, FIXTURES, KIT_ROOT, SCRIPT,  # noqa: E402
                           WIN_HOME, GuardCase, rules_of)

sys.path.insert(0, os.path.join(KIT_ROOT, "scripts"))
import sanitize_rules  # noqa: E402  (the path above is what makes it importable)

FIXTURE_WAIVER = "sanitize-ok:" + " all vendor sample\n"


class GuardTest(GuardCase):
    # the generic rules ---------------------------------------------------

    def test_each_seeded_leak_is_caught_by_its_own_rule(self):
        cases = {
            "home-windows.txt": ["home-windows"],
            "home-windows-json.txt": ["home-windows", "home-windows"],
            "home-mac.txt": ["home-mac"],
            "home-linux.txt": ["home-linux"],
            "email.txt": ["email"],
            "org-url.txt": ["tracker-azure", "tracker-jira", "tracker-github"],
            "private-key.txt": ["key-private"],
            "connection-string.txt": ["conn-password"],
            "github-token.txt": ["token-github"],
        }
        for name, expected in sorted(cases.items()):
            target = self.seed(name)
            result = self.run_guard(target)
            self.assertEqual(result.returncode, 1, name + ": " + result.stdout)
            self.assertEqual(rules_of(result.stdout), expected, name + ": " + result.stdout)
            self.assertIn("%d findings, 0 waivers, 1 files scanned" % len(expected),
                          result.stdout, name + ": " + result.stdout)

    def test_the_placeholders_pass(self):
        result = self.run_guard(self.seed("placeholders.txt"))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("0 findings, 0 waivers, 1 files scanned", result.stdout)

    # the waiver policy ---------------------------------------------------

    def test_a_per_rule_waiver_skips_the_finding_and_is_counted(self):
        result = self.run_guard(self.seed("waiver.txt"))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("0 findings, 1 waivers", result.stdout)
        self.assertIn("waived home-windows", result.stdout)

    def test_a_whole_file_waiver_under_fixtures_skips_the_file(self):
        self.file("fixtures/vendor.txt", FIXTURE_WAIVER + "open " + WIN_HOME + "\n")
        result = self.run_guard("fixtures")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("skipped whole: fixtures/vendor.txt", result.stdout)
        self.assertIn("1 files skipped whole by a fixture waiver", result.stdout)

    def test_a_whole_file_waiver_outside_fixtures_is_reported(self):
        self.file("docs/vendor.txt", FIXTURE_WAIVER + "open " + WIN_HOME + "\n")
        result = self.run_guard("docs")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(rules_of(result.stdout), ["waiver-abuse", "home-windows"], result.stdout)

    def test_a_whole_file_waiver_below_the_first_line_is_reported(self):
        self.file("fixtures/late.txt", "a note\n" + FIXTURE_WAIVER + "open " + WIN_HOME + "\n")
        result = self.run_guard("fixtures")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(rules_of(result.stdout), ["waiver-abuse", "home-windows"], result.stdout)

    # what is text and what is not ----------------------------------------

    def test_a_nul_byte_in_a_text_file_is_a_finding(self):
        target = os.path.join(self.repo, "notes.md")
        with io.open(target, "wb") as handle:
            handle.write(b"a note\x00 open " + WIN_HOME.encode("utf-8") + b"\n")
        result = self.run_guard("notes.md")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("binary-in-text", rules_of(result.stdout))
        self.assertIn("home-windows", rules_of(result.stdout))

    def test_a_binary_file_is_skipped(self):
        target = os.path.join(self.repo, "blob.bin")
        with io.open(target, "wb") as handle:
            handle.write(b"\x89PNG\x00\x00 open " + WIN_HOME.encode("utf-8") + b"\n")
        result = self.run_guard("blob.bin")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("0 findings, 0 waivers, 0 files scanned", result.stdout)

    # the scan modes ------------------------------------------------------

    def test_the_staged_contents_are_scanned(self):
        self.seed("email.txt")
        self.git("add", "-A")
        result = self.run_guard("--staged")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(rules_of(result.stdout), ["email"])

    def test_a_missing_path_is_a_hard_failure(self):
        result = self.run_guard("no-such-file.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("no-such-file.md", result.stderr)

    def test_a_range_catches_a_commit_message(self):
        base = self.commit("initial commit")
        tip = self.commit("port the " + CODENAME + " notes", name="ported.md")
        self.denylist(CODENAME)
        result = self.run_guard("--range", "%s..%s" % (base, tip))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("private-1: commit ", result.stdout)
        self.assertNoTerm(result)

    def test_a_range_scans_a_blob_added_and_deleted_inside_it(self):
        base = self.commit("initial commit")
        self.commit("add the file", name="leak.md",
                    body="the " + CODENAME + " migration notes\n")
        self.git("rm", "-q", "leak.md")
        self.git("commit", "-q", "-m", "remove it again")
        tip = self.git("rev-parse", "HEAD").stdout.strip()
        self.denylist(CODENAME)
        result = self.run_guard("--range", "%s..%s" % (base, tip))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("private-1: leak.md:1", result.stdout)
        self.assertNoTerm(result)

    def test_only_the_pushed_ref_names_are_scanned(self):
        base = self.commit("initial commit")
        self.git("checkout", "-q", "-b", "port-" + CODENAME + "-assets")
        tip = self.commit("a clean subject", name="ported.md")
        self.denylist(CODENAME)
        spec = "%s..%s" % (base, tip)
        clean = self.run_guard("--range", spec, "--ref-name", "refs/heads/main")
        self.assertEqual(clean.returncode, 0, clean.stdout)
        pushed = self.run_guard("--range", spec, "--ref-name",
                                "refs/heads/port-" + CODENAME + "-assets")
        self.assertEqual(pushed.returncode, 1, pushed.stdout)
        self.assertIn("private-1: refs:", pushed.stdout)
        self.assertNoTerm(pushed)

    def test_messages_only_skips_the_tree(self):
        base = self.commit("initial commit")
        tip = self.commit("a clean subject", name="ported.md",
                          body="the " + CODENAME + " migration notes\n")
        self.denylist(CODENAME)
        full = self.run_guard("--range", "%s..%s" % (base, tip))
        self.assertEqual(full.returncode, 1, full.stdout)
        self.assertIn("private-1: ported.md:1", full.stdout)
        only = self.run_guard("--range", "%s..%s" % (base, tip), "--messages-only")
        self.assertEqual(only.returncode, 0, only.stdout)

    def test_a_zero_remote_sha_scans_every_commit_that_is_on_no_remote(self):
        self.commit("initial commit")
        self.commit("port the " + CODENAME + " notes", name="ported.md")
        self.denylist(CODENAME)
        result = self.run_guard("--range", "%s..HEAD" % ("0" * 40))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("private-1: commit ", result.stdout)

    # git that answers badly ----------------------------------------------

    def test_an_unknown_remote_sha_falls_back_with_a_notice(self):
        self.commit("initial commit")
        self.commit("port the " + CODENAME + " notes", name="ported.md")
        self.denylist(CODENAME)
        result = self.run_guard("--range", "%s..HEAD" % ("dead" * 10))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("notice:", result.stdout + result.stderr)
        self.assertIn("private-1: commit ", result.stdout)

    def test_a_failed_git_call_is_a_hard_failure(self):
        result = self.run_guard("--staged", cwd=self.tmp)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("notice: git", result.stdout + result.stderr)

    # the repository this ships in ----------------------------------------

    def test_the_kit_repository_is_clean(self):
        result = subprocess.run([sys.executable, SCRIPT, "--all"], capture_output=True, text=True,
                                cwd=KIT_ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 findings,", result.stdout)

    def test_a_rule_line_without_an_id_or_a_pattern_is_a_hard_failure(self):
        for body in ("\tsomething\n", "a-rule-id\t\n"):
            path = self.file("rules.txt", body)
            with self.assertRaises(sanitize_rules.GuardError):
                sanitize_rules.load_rules(path)

    def test_every_fixture_carries_the_waiver_on_its_first_line(self):
        names = sorted(os.listdir(FIXTURES))
        self.assertTrue(names, FIXTURES)
        for name in names:
            if name == "placeholders.txt":
                continue
            with io.open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
                first = handle.readline()
            self.assertIn("sanitize-ok:", first, name)


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1).result.wasSuccessful() is False)
