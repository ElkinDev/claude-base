"""Pins for scripts/train-due.py (2026-09-23): the Landing trains trigger read from worktrees, reviews and gate exit files.

    python scripts/tests/test-train-due.py
    TRAIN_DUE_PY=<path> python scripts/tests/test-train-due.py      # against a staged copy (evidence by EVIDENCE_ROOT)

Every run is on a fixture repository with worktrees, a fixture evidence folder and a fixture scratchpad in a temp
folder; the real repository and the machine's scratchpads are never read.
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
SCRIPT = os.environ.get("TRAIN_DUE_PY") or os.path.join(HERE, "..", "train-due.py")
NOW = "2026-09-23 12:00"


def epoch(text):
    return int(time.mktime(datetime.datetime.strptime(text, "%Y-%m-%d %H:%M").timetuple()))


class TrainDueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="train due ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "App")
        self.ev = os.path.join(self.tmp, "ev")
        self.scratch = os.path.join(self.tmp, "scratch", "s1", "scratchpad")
        os.makedirs(os.path.join(self.ev, "reviews"))
        os.makedirs(self.scratch)
        os.makedirs(self.repo)
        self.git(self.repo, "init", "-q", "-b", "main")
        self.commit(self.repo, "base")
        self.heads = {}
        for token in ("lnaa", "lnbb", "lncc"):
            wt = os.path.join(self.tmp, "App-" + token)
            self.git(self.repo, "worktree", "add", "-q", "-b", "lane-" + token, wt)
            self.heads[token] = self.commit(wt, token)

    def git(self, cwd, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@example.com")
        r = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, cwd, name):
        with open(os.path.join(cwd, name + ".txt"), "w") as f:
            f.write(name + "\n")
        self.git(cwd, "add", name + ".txt")
        self.git(cwd, "commit", "-q", "-m", name)
        return self.git(cwd, "rev-parse", "HEAD")

    def review(self, token, at, text="Disposition: CLEAR\n"):
        path = os.path.join(self.ev, "reviews", "%s-2026-09-23.md" % token)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        os.utime(path, (epoch(at), epoch(at)))

    def gate(self, token, at, code="0", tip=None, run=None):
        folder = os.path.join(self.scratch, token + "gate")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, (run or token + "-g1") + ".exit")
        with open(path, "w", encoding="utf-8") as f:
            f.write("RUN=%s\nGATE_EXIT=97\nTIP_START=%s\nGATE_EXIT=%s\nTIP_END=%s\n"
                    % (run or token + "-g1", tip or self.heads.get(token, ""), code, tip or self.heads.get(token, "")))
        os.utime(path, (epoch(at), epoch(at)))

    def run_it(self, *args, holds=None):
        env = dict(os.environ, EVIDENCE_ROOT=self.ev, TRAIN_HOLDS=holds or os.path.join(self.tmp, "no-holds.txt"))
        r = subprocess.run([sys.executable, SCRIPT, "--repo", self.repo, "--scratch-glob",
                            os.path.join(self.tmp, "scratch", "*", "scratchpad"), "--now", NOW] + list(args),
                           capture_output=True, text=True, env=env, timeout=120)
        return r.returncode, r.stdout, r.stderr

    def test_two_lanes_clear_and_green_make_a_train_due(self):
        for t in ("lnaa", "lnbb"):
            self.review(t, "2026-09-23 11:30")
            self.gate(t, "2026-09-23 11:40")
        code, out, err = self.run_it()
        self.assertEqual(code, 0, err)
        self.assertIn("train DUE: 2 lanes CLEAR and green", out)

    def test_one_lane_is_due_after_its_minutes_and_says_when_before(self):
        self.review("lnaa", "2026-09-23 11:10")
        self.gate("lnaa", "2026-09-23 11:30")  # ready since the later of the two, 11:30
        code, out, _ = self.run_it()
        self.assertEqual(code, 1)
        self.assertIn("train not due: one lane waiting, due at 12:30; lanes lnaa 30 min", out)
        code, out, _ = self.run_it("--minutes", "30")
        self.assertEqual(code, 0)
        self.assertIn("train DUE: lnaa has waited 30 min", out)

    def test_a_gate_on_an_older_tip_is_listed_apart_never_counted(self):
        self.review("lnaa", "2026-09-23 10:00")
        self.gate("lnaa", "2026-09-23 10:10", tip=self.git(self.repo, "rev-parse", "main"))
        self.review("lnbb", "2026-09-23 10:00")
        self.gate("lnbb", "2026-09-23 10:10", code="1")
        code, out, _ = self.run_it("--verbose")
        self.assertEqual(code, 1)
        self.assertIn("no lane CLEAR and green on its tip", out)
        self.assertIn("apart 2", out)

    def test_a_running_union_and_the_evening_cut_block_a_due_train(self):
        for t in ("lnaa", "lnbb"):
            self.review(t, "2026-09-23 10:00")
            self.gate(t, "2026-09-23 10:10")
        self.gate("t1", "2026-09-23 11:50", code="97", tip="x", run="t1-g1-merge")
        code, out, _ = self.run_it()
        self.assertEqual(code, 1)
        self.assertIn("a union gate is running", out)
        env_now = ["--now", "2026-09-23 21:20"]
        r = subprocess.run([sys.executable, SCRIPT, "--repo", self.repo, "--scratch-glob",
                            os.path.join(self.tmp, "none")] + env_now, capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, EVIDENCE_ROOT=self.ev, TRAIN_HOLDS=os.path.join(self.tmp, "x")))
        self.assertEqual(r.returncode, 1)
        self.assertIn("after 21:15", r.stdout)

    def test_a_held_lane_never_makes_a_train_due(self):
        for t in ("lnaa", "lnbb"):
            self.review(t, "2026-09-23 10:00")
            self.gate(t, "2026-09-23 10:10")
        holds = os.path.join(self.tmp, "holds.txt")
        with open(holds, "w", encoding="utf-8-sig") as f:  # a byte order mark never hides the first hold
            f.write("lnaa waits for the owner\n# a comment\n\n")
        code, out, _ = self.run_it(holds=holds)
        self.assertEqual(code, 0)
        self.assertIn("held lnaa (waits for the owner)", out)
        self.assertIn("lanes lnbb", out)
        with open(holds, "a", encoding="utf-8") as f:
            f.write("lnbb freeze\n")
        code, out, _ = self.run_it(holds=holds)
        self.assertEqual(code, 1)

    def test_a_merged_lane_and_an_old_review_are_not_waiting(self):
        self.git(self.repo, "merge", "-q", "--no-ff", "-m", "merge lane-lnaa", "lane-lnaa")
        self.review("lnaa", "2026-09-23 10:00")
        self.gate("lnaa", "2026-09-23 10:10")
        self.review("lnbb", "2026-09-19 10:00")  # older than the 3-day bound
        self.gate("lnbb", "2026-09-19 10:10")
        code, out, _ = self.run_it()
        self.assertEqual(code, 1)
        self.assertIn("lanes none", out)

    def test_a_newer_block_takes_a_clear_lane_out_until_a_newer_clear(self):
        # rounds are files of their own (qr3c, qr3c-fix1, qr3c-fix2): the newest of any verdict decides (r1 finding 1)
        self.review("lnaa", "2026-09-23 10:00")
        self.review("lnaa-fix1", "2026-09-23 11:00", "Disposition: BLOCK\n")
        self.gate("lnaa", "2026-09-23 11:10")
        code, out, _ = self.run_it("--minutes", "30", "--verbose")
        self.assertEqual(code, 1, out)
        self.assertIn("lanes none", out)
        self.assertIn("apart 1", out)
        self.assertIn("apart: lnaa: the newest review lnaa-fix1-2026-09-23.md is not CLEAR", out)
        self.review("lnaa-fix2", "2026-09-23 11:20", "Disposition: CLEAR with notes\n")
        code, out, _ = self.run_it("--minutes", "30")
        self.assertEqual(code, 0, out)
        self.assertIn("train DUE: lnaa has waited 40 min", out)

    def test_a_sibling_lanes_review_never_speaks_for_a_blocked_lane(self):
        # r2 finding 1: a queue row citing briefs/lnaa-<date>.md made the bare stem "lnaa" match lnaa2's CLEAR
        wt = os.path.join(self.tmp, "App-lnaa2")
        self.git(self.repo, "worktree", "add", "-q", "-b", "lane-lnaa2", wt)
        self.heads["lnaa2"] = self.commit(wt, "lnaa2")
        with open(os.path.join(self.ev, "queue.md"), "w", encoding="utf-8") as f:
            f.write("| a | b | lnaa | briefs/lnaa-2026-09-23.md |\n")
        self.review("lnaa", "2026-09-23 10:00")
        self.review("lnaa-r2", "2026-09-23 11:00", "Disposition: BLOCK\n")
        self.review("lnaa2", "2026-09-23 11:10")
        for t in ("lnaa", "lnaa2"):
            self.gate(t, "2026-09-23 11:10")
        code, out, _ = self.run_it("--verbose")
        self.assertEqual(code, 1, out)  # lnaa2 alone has waited 50 min, under the 60
        self.assertIn("train not due: one lane waiting, due at 12:10; lanes lnaa2 50 min", out)
        self.assertIn("apart: lnaa: the newest review lnaa-r2-2026-09-23.md is not CLEAR", out)

    def test_the_header_and_queue_routes_own_a_review_and_a_header_word_that_is_no_lane_is_ignored(self):
        # r3 finding 4: a fixture on each route; r3 finding 1: "(OR-75 lane 1)" on line 1 never takes a review away
        with open(os.path.join(self.ev, "queue.md"), "w", encoding="utf-8") as f:
            f.write("| a | b | lncc | briefs/cc-slug-2026-09-23.md |\n")
        self.review("slugx", "2026-09-23 11:00", "# Review of lane lnaa\nDisposition: CLEAR\n")
        self.review("lnbb", "2026-09-23 10:00")
        self.review("lnbb-fix1", "2026-09-23 10:30", "# Review lnbb: the order axis (OR-75 lane 1)\nDisposition: BLOCK\n")
        self.review("cc-slug", "2026-09-23 10:00")
        self.review("cc-slug-fix1", "2026-09-23 10:40", "Disposition: BLOCK\n")
        for t in ("lnaa", "lnbb", "lncc"):
            self.gate(t, "2026-09-23 11:10")
        code, out, _ = self.run_it("--minutes", "30", "--verbose")
        self.assertEqual(code, 0, out)
        self.assertIn("train DUE: lnaa has waited 50 min; lanes lnaa 50 min", out)
        self.assertIn("apart: lnbb: the newest review lnbb-fix1-2026-09-23.md is not CLEAR", out)
        self.assertIn("apart: lncc: the newest review cc-slug-fix1-2026-09-23.md is not CLEAR", out)
        self.assertIn("apart 2", out)

    def test_a_review_that_names_two_lanes_keeps_both_out_whatever_the_routes(self):
        # r3 findings 2 and 3: one stem cited by two queue rows, a name against a queue stem, a header against a name
        with open(os.path.join(self.ev, "queue.md"), "w", encoding="utf-8") as f:
            f.write("| a | b | lnaa | briefs/shared-2026-09-23.md |\n| a | b | lnbb | briefs/shared-2026-09-23.md |\n"
                    "| a | b | lncc | briefs/lnaa-render-2026-09-23.md |\n")
        for t in ("lnaa", "lnbb", "lncc"):
            self.review(t, "2026-09-23 09:00")
            self.gate(t, "2026-09-23 09:10")
        self.review("shared", "2026-09-23 10:00", "Disposition: BLOCK\n")
        self.review("lnaa-render", "2026-09-23 10:30", "Disposition: BLOCK\n")
        code, out, _ = self.run_it("--verbose")
        self.assertEqual(code, 1, out)
        self.assertIn("lanes none", out)
        self.assertIn("apart: lnaa: the newest review lnaa-render-2026-09-23.md also names lncc; the seat decides", out)
        self.assertIn("apart: lnbb: the newest review shared-2026-09-23.md also names lnaa; the seat decides", out)
        self.assertIn("apart: lncc: the newest review lnaa-render-2026-09-23.md also names lnaa; the seat decides", out)
        self.review("lncc-fix3", "2026-09-23 11:00", "# same renderer as lane lnbb\nDisposition: BLOCK\n")
        code, out, _ = self.run_it("--verbose")
        self.assertEqual(code, 1, out)
        self.assertIn("apart: lncc: the newest review lncc-fix3-2026-09-23.md also names lnbb; the seat decides", out)
        self.assertIn("apart 3", out)

    def test_a_block_named_only_by_its_first_line_keeps_the_lane_out_in_every_real_header_shape(self):
        # r4 finding 1: capital Lane, a trailing dot, backticks, a union-named file; each BLOCK is newer than the CLEAR
        # r5 finding 1: "sctw round 1: BLOCK" and a union's parenthesised member list carry no `lane` keyword
        shapes = [("slug-a", "BLOCK. Lane lnaa round 2\n"), ("slug-b", "# Review of lane lnaa.\nDisposition: BLOCK\n"),
                  ("slug-c", "# Review of lane `lnaa`\nDisposition: BLOCK\n"),
                  ("train-0923a-union-notes", "# Review round 1, lane lnaa, notes\nDisposition: BLOCK\n"),
                  ("slug-d", "lnaa round 2: BLOCK\n"),
                  ("train-21-union", "# Union review, train 21 (aud2, lnaa, pfx)\nDisposition: BLOCK\n"),
                  # r6: a token inside a dashed word or a path, and in capitals
                  ("slug-e", "BLOCK. The gate build/lockrun/lnaa-fix1.done exitCodes=1\n"),
                  ("slug-f", "# Round 3, worktree C:/Repo/App-lnaa\nDisposition: BLOCK\n"),
                  ("slug-g", "# Review LNAA lane 4\nDisposition: BLOCK\n")]
        self.gate("lnaa", "2026-09-23 09:10")
        for stem, text in shapes:
            for old in os.listdir(os.path.join(self.ev, "reviews")):
                os.remove(os.path.join(self.ev, "reviews", old))
            self.review("lnaa", "2026-09-23 09:00")
            self.review(stem, "2026-09-23 10:00", text)
            code, out, _ = self.run_it("--verbose")
            self.assertEqual(code, 1, (stem, out))
            self.assertIn("apart: lnaa: the newest review %s-2026-09-23.md is not CLEAR" % stem, out)

    def test_a_longer_token_on_line_1_never_names_the_shorter_lane(self):
        # r7 note 1: without the boundaries lnaa2 would name lnaa and keep it out; lnaa must still wait
        self.review("lnaa", "2026-09-23 09:00")
        self.gate("lnaa", "2026-09-23 09:10")
        self.review("slug-h", "2026-09-23 10:00", "lnaa2 round 1: BLOCK\n")
        code, out, _ = self.run_it("--verbose")
        self.assertEqual(code, 0, out)
        self.assertIn("train DUE: lnaa has waited 170 min", out)
        self.assertIn("apart 0", out)

    def test_an_ambiguous_clear_keeps_both_lanes_out(self):
        # r4 finding 2: the also-names test comes before the CLEAR test, so a shared CLEAR boards neither lane
        with open(os.path.join(self.ev, "queue.md"), "w", encoding="utf-8") as f:
            f.write("| a | b | lnaa | briefs/shared-2026-09-23.md |\n| a | b | lnbb | briefs/shared-2026-09-23.md |\n")
        self.review("shared", "2026-09-23 10:00")
        for t in ("lnaa", "lnbb"):
            self.gate(t, "2026-09-23 10:10")
        code, out, _ = self.run_it("--verbose")
        self.assertEqual(code, 1, out)
        self.assertIn("lanes none", out)
        self.assertIn("apart: lnaa: the newest review shared-2026-09-23.md also names lnbb; the seat decides", out)
        self.assertIn("apart: lnbb: the newest review shared-2026-09-23.md also names lnaa; the seat decides", out)

    def test_same_minute_rounds_order_by_their_number_and_any_reason_prints(self):
        # r2 findings 2 and 3: fix10 is newer than fix9; a reason outside the console code page never stops the read
        self.review("lnaa-fix9", "2026-09-23 10:00", "Disposition: BLOCK\n")
        self.review("lnaa-fix10", "2026-09-23 10:00")
        self.gate("lnaa", "2026-09-23 10:10")
        self.review("lnbb", "2026-09-23 10:00")
        self.gate("lnbb", "2026-09-23 10:10")
        holds = os.path.join(self.tmp, "holds.txt")
        with open(holds, "w", encoding="utf-8") as f:
            f.write("lnbb waits → owner\n")
        env = dict(os.environ, EVIDENCE_ROOT=self.ev, TRAIN_HOLDS=holds, PYTHONIOENCODING="cp1252")
        r = subprocess.run([sys.executable, SCRIPT, "--repo", self.repo, "--scratch-glob",
                            os.path.join(self.tmp, "scratch", "*", "scratchpad"), "--now", NOW],
                           capture_output=True, env=env, timeout=120)
        out = r.stdout.decode("cp1252", "replace")
        self.assertEqual(r.returncode, 0, out + r.stderr.decode("cp1252", "replace"))
        self.assertIn("train DUE: lnaa has waited 110 min", out)
        self.assertIn("held lnbb (waits ? owner)", out)

    def test_an_unparsed_worktree_a_detached_lane_and_an_unfound_gate_are_listed_apart(self):
        # r1 findings 2, 3 and 4: nothing unmerged is dropped without a line, and apart N counts the lines it lists
        wt = os.path.join(self.tmp, "wt-lnzz")
        self.git(self.repo, "worktree", "add", "-q", "-b", "lane-lnzz", wt)
        self.commit(wt, "lnzz")
        det = os.path.join(self.tmp, "App-probe")
        self.git(self.repo, "worktree", "add", "-q", "--detach", det, "main")
        self.commit(det, "probe")
        self.git(self.repo, "worktree", "add", "-q", "--detach", os.path.join(self.tmp, "App-cand"), "main")
        self.review("lnaa", "2026-09-23 10:00")
        folder = os.path.join(self.scratch, "gate-lnaa")  # a green gate in a folder the reader cannot tie to lnaa
        os.makedirs(folder)
        with open(os.path.join(folder, "lnaa-g1.exit"), "w", encoding="utf-8") as f:
            f.write("GATE_EXIT=0\nTIP_END=%s\n" % self.heads["lnaa"])
        code, out, _ = self.run_it("--verbose")
        self.assertEqual(code, 1, out)
        self.assertIn("apart 3", out)
        self.assertEqual(out.count("  apart: "), 3, out)
        self.assertIn("apart: wt-lnzz: branch lane-lnzz is not on main but the folder is not App-<token>", out)
        self.assertIn("apart: App-probe: detached at", out)
        self.assertNotIn("App-cand", out)  # a detached worktree main holds is a candidate build, never a line
        self.assertIn("apart: lnaa: CLEAR (lnaa-2026-09-23.md) but no green gate found on its tip", out)
        self.assertIn("quiet: lnbb: no review in 3 days", out)

    def test_usage_errors_are_exit_2(self):
        broken = os.path.join(self.tmp, "broken")
        os.makedirs(os.path.join(broken, ".git"))  # a checkout git cannot read: a failure to read, never "not due"
        for args in (["--minutes", "0"], ["--now", "noon"], ["--now", ""], ["--now", "  "], ["--now", "2026-09-23"],
                     ["--repo", os.path.join(self.tmp, "none")], ["--repo", broken]):
            r = subprocess.run([sys.executable, SCRIPT] + args, capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, EVIDENCE_ROOT=self.ev))
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr))
            self.assertEqual(r.stdout, "", args)

    def test_a_repo_given_as_a_linked_worktree_reads_the_same_lanes(self):
        # the lane prefix comes from the main worktree git reports, never from the --repo spelling (a junction too)
        for t in ("lnaa", "lnbb"):
            self.review(t, "2026-09-23 11:30")
            self.gate(t, "2026-09-23 11:40")
        r = subprocess.run([sys.executable, SCRIPT, "--repo", os.path.join(self.tmp, "App-lncc") + os.sep,
                            "--scratch-glob", os.path.join(self.tmp, "scratch", "*", "scratchpad"), "--now", NOW],
                           capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, EVIDENCE_ROOT=self.ev, TRAIN_HOLDS=os.path.join(self.tmp, "x")))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("train DUE: 2 lanes CLEAR and green; lanes lnaa 20 min, lnbb 20 min", r.stdout)

    def test_nothing_is_due_before_eight(self):
        for t in ("lnaa", "lnbb"):
            self.review(t, "2026-09-22 20:00")
            self.gate(t, "2026-09-22 20:10")
        r = subprocess.run([sys.executable, SCRIPT, "--repo", self.repo, "--scratch-glob",
                            os.path.join(self.tmp, "scratch", "*", "scratchpad"), "--now", "2026-09-23 07:59"],
                           capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, EVIDENCE_ROOT=self.ev, TRAIN_HOLDS=os.path.join(self.tmp, "x")))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("before 08:00", r.stdout)
        self.assertIn("lanes lnaa", r.stdout)
        code, out, _ = self.run_it()  # 12:00 the same lanes are due
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
