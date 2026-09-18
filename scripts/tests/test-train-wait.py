"""Tests for scripts/train-wait.py: the readers it builds its numbers from.

    python scripts/tests/test-train-wait.py

The whole-run cases build a real git repository in a temporary folder, with a lane merge on main and a
CLEAR review beside it, so the wait is measured end to end rather than asserted on a stub. Nothing
outside the temporary folder is read: the evidence folder, the scratchpad glob and the repository are
all created here, and no live register, queue or landings file is opened.
"""
import datetime
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, "scripts", "train-wait.py")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tw = load("train_wait", SCRIPT)
GIT = shutil.which("git")


class VerdictTest(unittest.TestCase):
    """A review's verdict is its last verdict line, whatever markdown it wears."""

    def test_a_labelled_and_a_bare_verdict_both_read(self):
        self.assertEqual("CLEAR", tw.verdict_of("**Disposition: CLEAR**"))
        self.assertEqual("CLEAR", tw.verdict_of("CLEAR"))
        self.assertEqual("BLOCK", tw.verdict_of("## Verdict: BLOCK, two findings"))
        self.assertEqual("", tw.verdict_of("the reviewer was not clear about the scope"))

    def test_a_stem_drops_the_date_and_the_round(self):
        self.assertEqual("lane-w99", tw.STEM_RE.sub("", "lane-w99-2026-09-18.md"))
        self.assertEqual("lane-w99", tw.STEM_RE.sub("", "lane-w99-2026-09-18-r2.md"))
        self.assertEqual("lane-w99", tw.STEM_RE.sub("", "lane-w99-r2-2026-09-18.md"))
        self.assertEqual("lane-w99-notes.md", tw.STEM_RE.sub("", "lane-w99-notes.md"))

    def test_the_distribution_says_nothing_when_it_has_nothing(self):
        self.assertEqual("n=0", tw.dist([]))
        self.assertIn("median 2.0", tw.dist([1.0, 2.0, 3.0]))

    def test_the_evidence_root_prefers_the_argument_then_the_variable(self):
        saved = os.environ.pop("EVIDENCE_ROOT", None)
        try:
            self.assertEqual("D:/given", tw.evidence_root("D:/repo", "D:/given"))
            os.environ["EVIDENCE_ROOT"] = "D:/from-env"
            self.assertEqual("D:/from-env", tw.evidence_root("D:/repo", ""))
            os.environ.pop("EVIDENCE_ROOT")
            beside = tw.evidence_root(os.path.join("D:", os.sep, "work", "repo"), "")
            self.assertTrue(beside.endswith("evidence"), beside)
        finally:
            if saved is not None:
                os.environ["EVIDENCE_ROOT"] = saved


class UnionGateRunsTest(unittest.TestCase):
    """Only a run whose name ends in -merge, and only inside the window."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="train-wait-gates-")
        self.scratch = os.path.join(self.tmp, "session", "scratchpad")
        self.gates = os.path.join(self.scratch, "gate")
        os.makedirs(self.gates)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def exit_file(self, name, when):
        with open(os.path.join(self.gates, name + ".exit"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("GATE_START=%d\nGATE_EXIT=0\n" % int(when.timestamp()))

    def test_only_merge_runs_inside_the_window_are_counted(self):
        now = datetime.datetime.now()
        self.exit_file("w99-merge", now)
        self.exit_file("w98-merge", now - datetime.timedelta(days=30))
        self.exit_file("w97-lane", now)
        self.exit_file("w96-merge-note", now)
        glob_pattern = os.path.join(self.tmp, "*", "scratchpad")
        got = tw.union_gate_runs(glob_pattern, now - datetime.timedelta(days=1), now + datetime.timedelta(days=1))
        self.assertEqual(1, got, "one -merge run in the window")

    def test_an_exit_file_without_a_start_stamp_is_not_a_run(self):
        with open(os.path.join(self.gates, "w95-merge.exit"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("GATE_EXIT=0\n")
        now = datetime.datetime.now()
        glob_pattern = os.path.join(self.tmp, "*", "scratchpad")
        self.assertEqual(0, tw.union_gate_runs(glob_pattern, now - datetime.timedelta(days=1), now))


@unittest.skipUnless(GIT, "git is not on PATH")
class TrainWaitRunTest(unittest.TestCase):
    """One lane merged on main, with its CLEAR review in the evidence folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="train-wait-run-")
        self.repo = os.path.join(self.tmp, "repo")
        self.ev = os.path.join(self.tmp, "evidence")
        os.makedirs(os.path.join(self.ev, "reviews"))
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "kit-tests")  # any string; a real address would trip the sanitize guard
        self.git("config", "user.name", "kit tests")
        self.write("a.txt", "one\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.git("checkout", "-q", "-b", "lane-w99")
        self.write("a.txt", "two\n")
        self.git("commit", "-q", "-a", "-m", "lane work")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "lane-w99", "-m", "Merge lane w99 (one commit) into train-a1")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args):
        done = subprocess.run([GIT, "-C", self.repo] + list(args), capture_output=True, text=True)
        self.assertEqual(0, done.returncode, done.stderr)
        return done.stdout

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def review(self, name, text, ago_hours=2):
        path = os.path.join(self.ev, "reviews", name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        when = time.time() - ago_hours * 3600
        os.utime(path, (when, when))

    def run_script(self, *args):
        done = subprocess.run(
            [sys.executable, SCRIPT, "--repo", self.repo, "--evidence", self.ev,
             "--scratch-glob", os.path.join(self.tmp, "no-scratch", "*")] + list(args),
            capture_output=True, cwd=self.tmp, timeout=120,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )
        return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")

    def test_a_lane_with_a_clear_review_named_in_its_header_reports_a_wait(self):
        self.review("lane-w99-2026-09-18.md", "Review of lane w99, round 1\n\nDisposition: CLEAR\n")
        code, out, err = self.run_script("--row")
        self.assertEqual(0, code, err)
        self.assertIn("trains 1, lanes 1", out)
        self.assertIn("n=1", out, "the wait was measured: %s" % out)
        self.assertIn("unmatched 0 of 1", out)

    def test_a_blocked_review_leaves_the_lane_unmatched(self):
        self.review("lane-w99-2026-09-18.md", "Review of lane w99, round 1\n\nDisposition: BLOCK\n")
        code, out, err = self.run_script("--row")
        self.assertEqual(0, code, err)
        self.assertIn("unmatched 1 of 1", out, out)

    def test_a_union_review_never_matches_a_lane(self):
        self.review("train-a1-union-2026-09-18.md", "Review of lane w99\n\nCLEAR\n")
        code, out, err = self.run_script("--row")
        self.assertEqual(0, code, err)
        self.assertIn("unmatched 1 of 1", out, out)

    def test_a_folder_that_is_not_a_checkout_is_refused(self):
        done = subprocess.run(
            [sys.executable, SCRIPT, "--repo", self.ev], capture_output=True, cwd=self.tmp, timeout=60,
        )
        self.assertEqual(2, done.returncode)
        self.assertIn("REFUSED", done.stdout.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
