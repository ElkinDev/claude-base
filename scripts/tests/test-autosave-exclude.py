"""Tests for the exclude logic of claude/hooks/precompact-autosave.py:
python scripts/tests/test-autosave-exclude.py   (needs git on PATH)"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "..", "claude", "hooks", "precompact-autosave.py")
spec = importlib.util.spec_from_file_location("autosave", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def run_git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)


class ExcludeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autosave-exclude-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.assertEqual(run_git(self.repo, "init", "-q").returncode, 0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def exclude_lines(self):
        path = os.path.join(self.repo, ".git", "info", "exclude")
        return open(path, encoding="utf-8").read().splitlines() if os.path.isfile(path) else []

    def test_adds_the_rule_once_to_the_local_exclude_list(self):
        self.assertNotEqual(run_git(self.repo, "check-ignore", "-q", "NOTES.autosave.md").returncode, 0)
        self.assertTrue(hook.ensure_excluded(self.repo))
        self.assertTrue(hook.ensure_excluded(self.repo))  # idempotent
        self.assertEqual(self.exclude_lines().count("NOTES.autosave.md"), 1)
        self.assertEqual(run_git(self.repo, "check-ignore", "-q", "NOTES.autosave.md").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".gitignore")))  # the team file is untouched

    def test_leaves_the_exclude_list_alone_when_gitignore_already_covers_it(self):
        with open(os.path.join(self.repo, ".gitignore"), "w", encoding="utf-8") as handle:
            handle.write("NOTES.autosave.md\n")
        before = self.exclude_lines()
        self.assertTrue(hook.ensure_excluded(self.repo))
        self.assertEqual(self.exclude_lines(), before)

    def test_works_from_a_subfolder_and_a_worktree(self):
        sub = os.path.join(self.repo, "src", "deep")
        os.makedirs(sub)
        self.assertTrue(hook.ensure_excluded(sub))
        self.assertEqual(self.exclude_lines().count("NOTES.autosave.md"), 1)
        run_git(self.repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "root")
        wt = os.path.join(self.tmp, "wt")
        self.assertEqual(run_git(self.repo, "worktree", "add", "-q", wt, "-b", "side").returncode, 0)
        self.assertTrue(hook.ensure_excluded(wt))  # the exclude list is shared, so nothing is duplicated
        self.assertEqual(self.exclude_lines().count("NOTES.autosave.md"), 1)
        self.assertEqual(run_git(wt, "check-ignore", "-q", "NOTES.autosave.md").returncode, 0)

    def test_outside_a_repository_it_does_nothing(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        self.assertFalse(hook.ensure_excluded(plain))
        self.assertEqual(os.listdir(plain), [])

    def test_the_hook_end_to_end_writes_the_file_and_the_rule(self):
        transcript = os.path.join(self.tmp, "session.jsonl")
        with io.open(transcript, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "user", "message": {"role": "user", "content": "hello there"}}) + "\n")
            handle.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}}) + "\n")
        payload = json.dumps({"session_id": "abcdef12-0000", "transcript_path": transcript, "trigger": "auto", "cwd": self.repo})
        proc = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, text=True, cwd=self.repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "NOTES.autosave.md")))
        status = run_git(self.repo, "status", "--short", "--untracked-files=all").stdout
        self.assertNotIn("NOTES.autosave.md", status)


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1).result.wasSuccessful() is False)
