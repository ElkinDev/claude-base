#!/usr/bin/env python3
"""Tests for lane-state.py lane <token>, the hand-back block.

    python claude/tools/tests/test-lane-handback.py

Every test builds its own evidence root and, where a worktree is needed, its own git repository with
one lane worktree under tempfile.mkdtemp(); no test reads a real evidence root or a real repository.
The verdict reader is the kit's own scripts/brief-gen.py, wired through the brief_gen key.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "lane-state.py")
BRIEF_GEN = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "scripts", "brief-gen.py")


def load_script():
    spec = importlib.util.spec_from_file_location("lane_state_handback", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LS = load_script()


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, check=True,
                          timeout=60).stdout.strip()


class HandbackTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="lh root ")
        self.root = os.path.join(self.base, "evidence")
        for folder in ("lanes", "reviews", "gates"):
            os.makedirs(os.path.join(self.root, folder))
        self.repo = os.path.join(self.base, "myapp")
        self.config = {
            "lanes_glob": os.path.join(self.root, "lanes", "*.md"),
            "reviews_glob": os.path.join(self.root, "reviews", "*.md"),
            "handback_log": os.path.join(self.root, "handback.log"),
            "project_repo": self.repo,
            "brief_gen": BRIEF_GEN,
            "gates_dirs": [os.path.join(self.root, "gates")],
        }

    def tearDown(self):
        self.assertTrue(os.path.abspath(self.base).startswith(os.path.abspath(tempfile.gettempdir())))
        subprocess.run(["git", "-C", self.repo, "worktree", "prune"], capture_output=True, timeout=60)
        shutil.rmtree(self.base, ignore_errors=True)

    def write(self, rel, text, age_days=0):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def make_repo(self, token="abcd"):
        """main with one commit, and a worktree myapp-<token> one commit ahead touching Widget.kt."""
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        with open(os.path.join(self.repo, "Base.kt"), "w") as handle:
            handle.write("base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        wt = os.path.join(self.base, "myapp-" + token)
        git(self.repo, "worktree", "add", "-q", "-b", "lane-" + token, wt)
        with open(os.path.join(wt, "Widget.kt"), "w") as handle:
            handle.write("widget\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "lane")
        return wt, git(wt, "rev-parse", "HEAD")

    def block(self, token="abcd"):
        return LS.handback_lines(dict(self.config), token, dirs=self.config["gates_dirs"])

    def run_main(self, *args):
        path = os.path.join(self.base, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.config, handle)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = LS.main(["--config", path] + list(args))
        return code, buffer.getvalue()

    # -- files by token ---------------------------------------------------------------

    def test_a_token_takes_its_own_files_and_never_a_longer_token(self):
        self.write("lanes/abcd-2026-09-23.md", "# old\n", age_days=1)
        self.write("lanes/abcd-fix1-2026-09-24.md", "# new\n")
        self.write("lanes/abcdx-2026-09-24.md", "# other lane\n")
        found = [os.path.basename(p) for p in LS.token_files(self.config["lanes_glob"], "abcd")]
        self.assertEqual(["abcd-fix1-2026-09-24.md", "abcd-2026-09-23.md"], found)

    def test_open_items_reads_its_section_only(self):
        text = "# r\n\n- not an item\n\n## Open items\n\n- one\n2. two\n* three\n\n## Next\n\n- after\n"
        self.assertEqual(["one", "two", "three"], LS.open_items(text))
        self.assertEqual([], LS.open_items("# r\n\nno section\n"))

    # -- the tip check ------------------------------------------------------------------

    def test_s3_is_ok_when_the_report_names_the_worktree_head(self):
        _, head = self.make_repo()
        self.write("lanes/abcd-2026-09-24.md", "# Lane abcd\n\nTip %s on lane-abcd.\n" % head[:9])
        lines, s3, _ = self.block()
        self.assertEqual("ok", s3, "\n".join(lines))
        self.assertTrue(any(ln.startswith("worktree ") and "s3=ok" in ln for ln in lines))

    def test_s3_is_a_mismatch_when_the_report_names_another_tip(self):
        self.make_repo()
        self.write("lanes/abcd-2026-09-24.md", "# Lane abcd\n\nTip 1234567abc on lane-abcd.\n")
        lines, s3, _ = self.block()
        self.assertEqual("mismatch", s3, "\n".join(lines))
        self.assertTrue(any("the report does not name" in ln for ln in lines))

    def test_s3_without_a_report_or_a_worktree_says_which(self):
        lines, s3, count = self.block()
        self.assertEqual(("no-report", None), (s3, count), "\n".join(lines))
        self.write("lanes/abcd-2026-09-24.md", "# Lane abcd, no repository\n")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        lines, s3, _ = self.block()
        self.assertEqual("no-worktree", s3, "\n".join(lines))

    def test_a_missing_repository_is_one_line_and_never_a_raise(self):
        self.write("lanes/abcd-2026-09-24.md", "# Lane abcd\n")
        lines, s3, _ = self.block()
        self.assertEqual("no-worktree", s3)
        self.assertTrue(any(ln.startswith("worktree read failed") for ln in lines), "\n".join(lines))

    def test_no_project_repo_is_one_line_and_never_a_git_call(self):
        self.write("lanes/abcd-2026-09-24.md", "# Lane abcd\n")
        self.config["project_repo"] = ""
        lines, s3, count = self.block()
        self.assertEqual(("no-worktree", None), (s3, count))
        self.assertIn("worktree none: no project_repo configured; s3=no-worktree", lines)

    def test_reviews_show_their_verdict_and_an_old_report_shows_its_date(self):
        self.write("lanes/abcd-2026-09-22.md", "# Lane abcd\n", age_days=2)
        self.write("reviews/abcd-2026-09-24.md", "Disposition: BLOCK, lane abcd round 1\n\n1. MAJOR x\n")
        self.write("reviews/abcd-r2-2026-09-24.md", "Disposition: CLEAR with notes, lane abcd round 2\n")
        lines, _, _ = self.block()
        reviews = next(ln for ln in lines if ln.startswith("reviews: "))
        self.assertIn("abcd-2026-09-24.md", reviews)
        self.assertIn("BLOCK", reviews, reviews)
        self.assertIn("CLEAR with notes", reviews, reviews)
        report = next(ln for ln in lines if ln.startswith("report "))
        self.assertRegex(report, r"report lanes/abcd-2026-09-22\.md \d\d-\d\d \d\d:\d\d \|", report)

    def test_a_verdict_reader_that_cannot_load_is_one_line(self):
        self.write("reviews/abcd-2026-09-24.md", "Disposition: CLEAR, lane abcd round 1\n")
        self.config["brief_gen"] = os.path.join(self.base, "no-such-brief-gen.py")
        lines, _, _ = self.block()
        self.assertTrue(any(ln.startswith("disposition reader failed") for ln in lines), "\n".join(lines))
        self.assertTrue(any(ln.startswith("reviews: abcd-2026-09-24.md") and ln.endswith(" ?") for ln in lines),
                        "\n".join(lines))

    # -- overlaps -------------------------------------------------------------------------

    def test_a_recent_report_naming_a_touched_file_is_an_overlap_and_a_common_name_is_not(self):
        _, head = self.make_repo()
        self.write("lanes/abcd-2026-09-24.md", "# Lane abcd\n\nTip %s, Widget.kt.\n" % head[:9])
        self.write("reviews/efgh-2026-09-24.md", "Disposition: CLEAR\n\nWidget.kt:12 read.\n")
        self.write("lanes/ijkl-2026-09-01.md", "Widget.kt too, but three weeks old.\n", age_days=23)
        lines, _, count = self.block()
        self.assertEqual(1, count, "\n".join(lines))
        self.assertTrue(any("reviews/efgh-2026-09-24.md (Widget.kt)" in ln for ln in lines), "\n".join(lines))
        for i in range(8):  # Widget.kt now in 9 of 10 recent reports: too common to say anything
            self.write("lanes/w%d-2026-09-24.md" % i, "Widget.kt\n")
        lines, _, count = self.block()
        self.assertEqual(0, count, "\n".join(lines))
        self.assertTrue(any("too common to count: Widget.kt" in ln for ln in lines), "\n".join(lines))

    # -- gates ----------------------------------------------------------------------------

    def test_the_lanes_gates_show_and_another_lanes_do_not(self):
        for lane in ("abcd", "efgh"):
            gate_dir = os.path.join(self.root, "gates", lane + "gate")
            os.makedirs(gate_dir)
            with open(os.path.join(gate_dir, lane + "-g1.exit"), "w") as handle:
                handle.write("RUN=%s-g1\nGATE_EXIT=0\n" % lane)
        lines, _, _ = self.block()
        self.assertIn("gates 1 in 72 h:", lines, "\n".join(lines))
        self.assertTrue(any("abcd-g1" in ln for ln in lines), "\n".join(lines))
        self.assertFalse(any("efgh-g1" in ln for ln in lines), "\n".join(lines))

    # -- the block and the log -------------------------------------------------------------

    def test_the_block_never_passes_forty_lines(self):
        items = "".join("- item %d\n" % i for i in range(60))
        self.write("lanes/abcd-2026-09-24.md", "# Lane abcd\n\n## Open items\n\n" + items)
        gate_dir = os.path.join(self.root, "gates", "abcdgate")
        os.makedirs(gate_dir)
        for i in range(10):
            with open(os.path.join(gate_dir, "abcd-g%d.exit" % i), "w") as handle:
                handle.write("RUN=abcd-g%d\nGATE_EXIT=0\n" % i)
        for i in range(30):
            self.write("reviews/abcd-r%d-2026-09-24.md" % i, "Disposition: CLEAR\n")
        lines, _, _ = self.block()
        self.assertLessEqual(len(lines), 40, "\n".join(lines))
        self.assertIn("open items 60", lines)

    def test_main_prints_the_block_and_appends_one_log_line_the_keep_rule_can_read(self):
        self.write("lanes/abcd-2026-09-24.md", "# Lane abcd\n")
        code, out = self.run_main("lane", "ABCD")
        self.assertEqual(0, code, out)
        self.assertTrue(out.startswith("lane abcd "), out)
        with open(self.config["handback_log"], encoding="utf-8") as handle:
            rows = handle.read().splitlines()
        self.assertEqual(1, len(rows))
        fields = rows[0].split()
        self.assertEqual(["abcd", "s3=no-worktree", "overlaps=-"], fields[2:])
        code, out = self.run_main("lane", "abcd", "--no-log")
        self.assertEqual(0, code)
        with open(self.config["handback_log"], encoding="utf-8") as handle:
            self.assertEqual(1, len(handle.read().splitlines()))

    def test_a_bad_token_is_refused_and_logs_nothing(self):
        for bad in ("", "  ", "../x", "a b", "a/b", "x" * 25, "\u00e9"):
            code, out = self.run_main("lane", bad)
            self.assertEqual(2, code, repr(bad) + out)
            self.assertTrue(out.startswith("REFUSED"), out)
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
            self.run_main("lane", "-x")  # argparse reads it as an option: its own usage error, exit 2
        self.assertEqual(2, caught.exception.code)
        self.assertFalse(os.path.exists(self.config["handback_log"]))

    def test_an_unwritable_log_is_exit_one_after_the_block(self):
        self.write("lanes/abcd-2026-09-24.md", "# Lane abcd\n")
        self.config["handback_log"] = os.path.join(self.root, "no-such-folder", "handback.log")
        code, out = self.run_main("lane", "abcd")
        self.assertEqual(1, code, out)
        self.assertIn("handback log not written", out)
        self.assertTrue(out.startswith("lane abcd "))

    def test_the_config_defaults_put_reviews_and_the_log_beside_the_config(self):
        path = os.path.join(self.base, "only.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({}, handle)
        config = LS.load_config(path)
        self.assertEqual(os.path.join(self.base, "reviews", "*.md"), config["reviews_glob"])
        self.assertEqual(os.path.join(self.base, "handback.log"), config["handback_log"])
        self.assertEqual("", config["brief_gen"])
        self.assertEqual(os.path.dirname(os.path.abspath(path)), config["evidence_root"])


    def test_a_file_gone_between_the_glob_and_its_date_never_raises(self):
        """A retention sweep or another lane can remove a review between the glob and a later date read
        (review kit-twins-0924d note 1): the block still prints, the file sorts last."""
        _, head = self.make_repo()
        self.write("lanes/abcd-2026-09-24.md", "# Lane abcd\n\nTip %s, Widget.kt.\n" % head[:9])
        self.write("reviews/abcd-2026-09-24.md", "Disposition: CLEAR\n")
        self.write("reviews/efgh-2026-09-24.md", "Disposition: CLEAR\n\nWidget.kt:12 read.\n")
        real, seen = os.path.getmtime, {}

        def flaky(path):
            # a review is found by its first date read and gone for every later one
            if "reviews" in str(path):
                seen[path] = seen.get(path, 0) + 1
                if seen[path] > 1:
                    raise FileNotFoundError(path)
            return real(path)

        from unittest import mock
        with mock.patch("os.path.getmtime", flaky):
            lines, _, count = self.block()
        self.assertTrue(any(ln.startswith("reviews: abcd-2026-09-24.md") for ln in lines), "\n".join(lines))
        self.assertEqual(1, count, "\n".join(lines))

if __name__ == "__main__":
    unittest.main(verbosity=2)
