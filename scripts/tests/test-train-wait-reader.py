"""Pins for the reader of scripts/train-wait.py: the lane token of a merge(train) subject, the review-name route,
the verdict line with a byte order mark or an upper-case label, and the landing timed by main's reflog.

    python scripts/tests/test-train-wait.py
    TRAIN_WAIT_PY=<path> python scripts/tests/test-train-wait-reader.py      # against a staged copy

Every run is on a fixture repository and a fixture evidence folder in a temp folder, passed by --evidence.
"""
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("TRAIN_WAIT_PY") or os.path.join(HERE, "..", "train-wait.py")


def epoch(text):
    return int(time.mktime(datetime.datetime.strptime(text, "%Y-%m-%d %H:%M").timetuple()))


class TrainWaitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="train wait ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.ev = os.path.join(self.tmp, "ev")
        os.makedirs(os.path.join(self.ev, "reviews"))
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        self.commit("base", "2026-09-19 08:00")
        self.git("checkout", "-q", "-b", "train-t1")
        for name, tail in (("abcd", "abcd: the first lane [skip ci]"), ("efgh", "a prose tail (item 4, efgh) [skip ci]"),
                           ("ijkl", "the join screen reads the QR first [skip ci]")):
            self.git("checkout", "-q", "-b", name, "main")
            tip = self.commit(name, "2026-09-20 06:00")
            self.git("checkout", "-q", "train-t1")
            self.git("merge", "-q", "--no-ff", "-m", "merge(train): %s into train-t1, %s" % (tip[:9], tail), name,
                     when="2026-09-20 10:00")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--ff-only", "train-t1", when="2026-09-20 11:00")  # main moves after the union gate
        self.review("abcd-2026-09-20.md", "Disposition: CLEAR\n")
        self.review("efgh-lane-2026-09-20.md", "﻿# Review, lane efgh, branch x\n\nVERDICT: CLEAR with notes\n",
                    mtime="2026-09-20 09:00")
        # no token in its subject; named after the tail's first word, so a reader that took "the" as the token
        # would match it by name and the unmatched count would move (review r1 note 1)
        self.review("the-join-screen-2026-09-20.md", "Disposition: CLEAR\n", mtime="2026-09-20 08:30")
        with open(os.path.join(self.ev, "landings.md"), "w", encoding="utf-8") as f:
            f.write("| 2026-09-20 08:00 | [reviewer] reviews/abcd-2026-09-20.md CLEAR |\n")

    def git(self, *args, when="2026-09-19 08:00"):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@example.com", GIT_AUTHOR_DATE="%d +0000" % epoch(when),
                   GIT_COMMITTER_DATE="%d +0000" % epoch(when))
        r = subprocess.run(["git", "-C", self.repo] + list(args), capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, name, when):
        with open(os.path.join(self.repo, name + ".txt"), "w") as f:
            f.write(name + "\n")
        self.git("add", name + ".txt")
        self.git("commit", "-q", "-m", name, when=when)
        return self.git("rev-parse", "HEAD")

    def review(self, name, text, mtime=None):
        path = os.path.join(self.ev, "reviews", name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        stamp = epoch(mtime or "2026-09-23 12:00")
        os.utime(path, (stamp, stamp))

    def run_it(self, *args):
        r = subprocess.run([sys.executable, SCRIPT, "--repo", self.repo, "--evidence", self.ev, "--since",
                            "2026-09-20", "--until", "2026-09-20", "--scratch-glob", os.path.join(self.tmp, "none")]
                           + list(args), capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout, r.stderr

    def test_tokens_routes_and_the_reflog_landing(self):
        code, out, err = self.run_it("--verbose")
        self.assertEqual(code, 0, err)
        # abcd: token from "abcd: ...", review by its name, timed 08:00 by landings.md, landed 11:00 by the reflog
        self.assertRegex(out, r"abcd +train-t1 +wait +3\.0 h  abcd-2026-09-20\.md \(name\)")
        # efgh: token from "(item 4, efgh)", review by its header behind a byte order mark and an upper-case label
        self.assertRegex(out, r"efgh +train-t1 +wait +2\.0 h  efgh-lane-2026-09-20\.md \(header\)")
        # a prose tail has no token; its merge is counted and matched by no name
        self.assertIn("lanes with no CLEAR review found: 1 of 3", out)
        self.assertIn("timed by the merge commit, main's reflog did not reach them: 0 of 3", out)

    def test_an_expired_reflog_falls_back_to_the_commit_and_says_so(self):
        self.git("reflog", "expire", "--expire=now", "--all")
        code, out, err = self.run_it("--row")
        self.assertEqual(code, 0, err)
        self.assertIn("wait n=2 median 1.5", out)  # 2.0 and 1.0 h, timed by the 10:00 merge commits
        self.assertTrue(out.rstrip().endswith("timed by commit 3"), out)

    def test_verbose_stamps_the_move_of_main_and_marks_the_commit_clock(self):
        code, out, err = self.run_it("--verbose")
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"09-20 11:00  abcd ")  # main's move, not the 10:00 build
        self.git("reflog", "expire", "--expire=now", "--all")
        code, out, err = self.run_it("--verbose")
        self.assertRegex(out, r"09-20 10:00c abcd ")

    def test_a_day_that_is_not_a_day_and_a_reversed_window_are_usage_errors(self):
        for args, text in ((["--since", ""], "is not a day"), (["--until", "2026-9-x"], "is not a day"),
                           (["--since", "2026-09-21", "--until", "2026-09-20"], "is after --until")):
            r = subprocess.run([sys.executable, SCRIPT, "--repo", self.repo, "--evidence", self.ev] + args,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn(text, r.stderr, args)

    def test_not_a_checkout_is_refused(self):
        r = subprocess.run([sys.executable, SCRIPT, "--repo", os.path.join(self.tmp, "none"), "--evidence", self.ev],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
