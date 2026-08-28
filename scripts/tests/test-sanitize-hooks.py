"""The hook side of the guard: installing the wrappers, and running them the way git does.

python scripts/tests/test-sanitize-hooks.py   (needs git on PATH, and Git Bash on Windows)

The wrapper tests execute scripts/git-hooks/pre-push and pre-commit through Git Bash with the
stdin git feeds a pre-push hook, because the wrapper is the part that reads that stdin, skips a
deletion and fails closed, and none of that is exercised by driving the Python entry point.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_case import (CJK_NAME, CODENAME, FILE_WAIVER, KIT_ROOT,  # noqa: E402
                           SCRIPT, GuardCase, git_bash, read, write)

spec = importlib.util.spec_from_file_location("sanitize_check", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

GUARD_FILES = ("sanitize-check.py", "sanitize_git.py", "sanitize_guard.py", "sanitize_rules.py",
               "sanitize-rules.txt", "sanitize-allow.txt")
ZERO = "0" * 40
REMOTE = "http://example.invalid/repo.git"
BASH = git_bash()
NO_BASH = ("the hook wrappers need Git Bash and none was found; git --exec-path, its parents and "
           "the standard Git for Windows install paths were tried, and a bare bash on PATH is "
           "deliberately not used because on Windows it is often WSL")


class InstallTest(GuardCase):
    def test_install_writes_the_hook_when_none_is_there(self):
        result = self.run_guard("--install-hook")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        body = read(self.hook())
        self.assertIn(guard.MARKER, body)
        self.assertNotIn("\r\n", body)
        self.assertFalse(os.path.exists(self.hook("pre-commit")))

    def test_install_with_pre_commit_writes_both_hooks(self):
        result = self.run_guard("--install-hook", "--pre-commit")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for kind in ("pre-push", "pre-commit"):
            self.assertIn(guard.MARKER, read(self.hook(kind)))

    def test_install_rewrites_a_hook_that_is_ours(self):
        self.run_guard("--install-hook")
        write(self.hook(), read(self.hook()) + "\necho drifted\n")
        result = self.run_guard("--install-hook")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("rewrote", result.stdout)
        self.assertNotIn("drifted", read(self.hook()))

    def test_install_refuses_a_foreign_hook_and_writes_nothing(self):
        write(self.hook(), "#!/bin/sh\necho someone else owns this\n")
        result = self.run_guard("--install-hook")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("someone else owns this", read(self.hook()))
        self.assertIn("scripts/git-hooks/pre-push", result.stdout)

    def test_install_refuses_a_managed_hooks_path_and_writes_nothing(self):
        self.git("config", "core.hooksPath", ".githooks")
        result = self.run_guard("--install-hook")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("core.hooksPath", result.stdout)
        self.assertIn("scripts/git-hooks/pre-push", result.stdout)
        self.assertFalse(os.path.exists(self.hook()))
        self.assertFalse(os.path.isdir(os.path.join(self.repo, ".githooks")))

    def test_uninstall_removes_only_our_hooks(self):
        self.run_guard("--install-hook")
        write(self.hook("pre-commit"), "#!/bin/sh\necho someone else owns this\n")
        result = self.run_guard("--uninstall-hook")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(os.path.exists(self.hook()))
        self.assertTrue(os.path.exists(self.hook("pre-commit")))


@unittest.skipIf(BASH is None, NO_BASH)
class WrapperTest(GuardCase):
    """The sandbox gets a copy of the guard under scripts/, which is what the wrapper looks for."""

    def setUp(self):
        GuardCase.setUp(self)
        target = os.path.join(self.repo, "scripts", "git-hooks")
        os.makedirs(target)
        for name in GUARD_FILES:
            shutil.copy(os.path.join(KIT_ROOT, "scripts", name),
                        os.path.join(self.repo, "scripts"))
        for kind in ("pre-push", "pre-commit"):
            shutil.copy(os.path.join(KIT_ROOT, "scripts", "git-hooks", kind), target)
        self.denylist(CODENAME)
        install = self.run_guard("--install-hook", "--pre-commit")
        self.assertEqual(install.returncode, 0, install.stdout + install.stderr)

    def run_hook(self, kind, stdin="", command=None, **kwargs):
        argv = [BASH, "-c", command] if command else [BASH, ".git/hooks/" + kind, "origin", REMOTE]
        return subprocess.run(argv, cwd=self.repo, input=stdin, capture_output=True, text=True,
                              env=self.environment(kwargs.pop("env", None)), **kwargs)

    def history(self):
        """A clean base commit, then a dirty one. Returns (base, tip)."""
        base = self.commit("initial commit")
        tip = self.commit("port the " + CODENAME + " notes", name="ported.md")
        return base, tip

    def test_the_pre_push_wrapper_blocks_a_dirty_range(self):
        base, tip = self.history()
        result = self.run_hook("pre-push", "refs/heads/f %s refs/heads/f %s\n" % (tip, base))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("sanitize guard: blocked", result.stderr)
        self.assertNoTerm(result)

    def test_the_pre_push_wrapper_passes_a_clean_range(self):
        base = self.commit("initial commit")
        tip = self.commit("a clean subject", name="ported.md")
        result = self.run_hook("pre-push", "refs/heads/f %s refs/heads/f %s\n" % (tip, base))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 findings", result.stdout)

    def test_the_pre_push_wrapper_scans_a_new_branch(self):
        base, tip = self.history()
        result = self.run_hook("pre-push", "refs/heads/f %s refs/heads/f %s\n" % (tip, ZERO))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNoTerm(result)

    def test_the_pre_push_wrapper_scans_an_annotated_tag_message(self):
        self.commit("initial commit")
        self.commit("a clean subject", name="ported.md")
        self.git("tag", "-a", "v9.9.9", "-m", "port the " + CODENAME + " notes")
        tag = self.git("rev-parse", "v9.9.9").stdout.strip()
        result = self.run_hook("pre-push", "refs/tags/v9.9.9 %s refs/tags/v9.9.9 %s\n"
                               % (tag, ZERO))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("sanitize guard: blocked", result.stderr)
        self.assertNoTerm(result)

    def test_the_wrappers_survive_a_non_ascii_path(self):
        base = self.commit("initial commit")
        # The commit itself runs the pre-commit wrapper over the staged name.
        tip = self.commit("add the sample", name="fixtures/" + CJK_NAME + ".md",
                          body=FILE_WAIVER + " sample\na body\n")
        result = self.run_hook("pre-push", "refs/heads/f %s refs/heads/f %s\n" % (tip, base),
                               env={"PYTHONIOENCODING": None, "PYTHONUTF8": None},
                               encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("0 findings", result.stdout)

    def test_the_pre_push_wrapper_skips_a_deletion(self):
        base, tip = self.history()
        result = self.run_hook("pre-push", "(delete) %s refs/heads/f %s\n" % (ZERO, tip))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "", result.stdout)

    def test_the_pre_push_wrapper_reads_every_ref_on_stdin(self):
        base, tip = self.history()
        clean = self.commit("another clean subject", name="more.md")
        stdin = ("refs/heads/clean %s refs/heads/clean %s\n" % (clean, tip)
                 + "refs/heads/f %s refs/heads/f %s\n" % (tip, base))
        result = self.run_hook("pre-push", stdin)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("files scanned"), 2, result.stdout)

    def test_the_pre_push_wrapper_fails_closed_without_python(self):
        base, tip = self.history()
        stripped = ('PATH="/usr/bin:/bin"; export PATH; '
                    'sh .git/hooks/pre-push origin ' + REMOTE)
        result = self.run_hook("pre-push", "refs/heads/f %s refs/heads/f %s\n" % (tip, base),
                               command=stripped)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("sanitize guard", result.stderr)

    def test_the_pre_commit_wrapper_blocks_a_staged_leak(self):
        self.commit("initial commit")
        self.file("staged.md", "the " + CODENAME + " plan\n")
        self.git("add", "staged.md")
        result = self.run_hook("pre-commit")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("private-1", result.stdout)
        self.assertNoTerm(result)


if __name__ == "__main__":
    if BASH is None:
        skipped = [name for name in dir(WrapperTest) if name.startswith("test_")]
        print("SKIPPED, %d wrapper tests: %s" % (len(skipped), NO_BASH))
    sys.exit(unittest.main(verbosity=1).result.wasSuccessful() is False)
