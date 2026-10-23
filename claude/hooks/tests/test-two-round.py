"""Tests for claude/hooks/two-round.py. The hook runs as the harness runs it, a subprocess with a payload on stdin,
against fixture reviews folders in a temporary folder; TWO_ROUND_DIR puts its state and log there too.

    python -I -B claude/hooks/tests/test-two-round.py
    TWO_ROUND_PY=<path> python -I -B claude/hooks/tests/test-two-round.py      # against a staged copy (mutants)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.environ.get("TWO_ROUND_PY") or os.path.join(os.path.dirname(HERE), "two-round.py")
SESSION = "11111111-2222-3333-4444-555555555555"
DAY = "2026-10-23"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="two round ")
        self.reviews = os.path.join(self.tmp, "proj", "reviews")
        os.makedirs(self.reviews)
        self.state = os.path.join(self.tmp, "hookdir")
        os.makedirs(self.state)
        self.clock = time.time() - 3600

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, text, folder=None):
        """Write a fixture file with an mtime one minute after the previous one."""
        path = os.path.join(folder or self.reviews, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.clock += 60
        os.utime(path, (self.clock, self.clock))
        return path

    def review(self, name, verdict):
        return self.put(name, "# Review of %s\n\nVerdict: %s\n\nNotes.\n" % (name, verdict))

    def hook(self, path, tool="Write", raw=None):
        env = dict(os.environ, TWO_ROUND_DIR=self.state)
        if raw is None:
            raw = json.dumps({"session_id": SESSION, "hook_event_name": "PostToolUse", "tool_name": tool,
                              "tool_input": {"file_path": path, "content": "x"}, "tool_response": {}})
        p = subprocess.run([sys.executable, "-I", HOOK], input=raw.encode("utf-8"), capture_output=True, env=env,
                           timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.decode("utf-8")

    def notice(self, out):
        self.assertTrue(out, "expected a notice")
        ctx = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "PostToolUse")
        return ctx["additionalContext"]


class TwoRound(Base):
    def test_first_block_nothing(self):
        self.assertEqual(self.hook(self.review("review-proto-x-%s.md" % DAY, "BLOCK")), "")

    def test_second_block_notice_once_then_third(self):
        self.hook(self.review("review-proto-x-%s.md" % DAY, "BLOCK"))
        p2 = self.review("review-proto-x-r2-%s.md" % DAY, "BLOCK")
        text = self.notice(self.hook(p2))
        today = time.strftime("%Y-%m-%d")
        self.assertEqual(text, "Two-round rule: proto-x has 2 BLOCK reviews since its last diagnosis "
                         "(review-proto-x-%s.md, review-proto-x-r2-%s.md). After a second BLOCK a piece is not resent as it "
                         "is. Before round 3, write "
                         "diagnosis-proto-x-%s.md in this reviews folder naming the wrong premise, then rewrite "
                         "under it and bound the next round to the rewrite." % (DAY, DAY, today))
        self.assertEqual(self.hook(p2, tool="Edit"), "", "the same save again must say nothing")
        p3 = self.review("review-proto-x-r3-%s.md" % DAY, "BLOCK")
        self.assertIn("has 3 BLOCK reviews", self.notice(self.hook(p3)))
        with open(os.path.join(self.state, "two-round.log"), encoding="utf-8") as fh:
            rows = [r.split("\t") for r in fh.read().splitlines()]
        self.assertEqual([r[4] for r in rows], ["2", "3"])
        self.assertEqual(rows[0][1], SESSION[:8])
        self.assertEqual(rows[0][3], "proto-x")

    def test_diagnosis_after_second_block(self):
        self.review("review-proto-x-%s.md" % DAY, "BLOCK")
        self.review("review-proto-x-r2-%s.md" % DAY, "BLOCK")
        self.put("diagnosis-proto-x-%s.md" % DAY, "# Diagnosis of proto-x\n\nWrong premise.\n")
        self.assertEqual(self.hook(self.review("review-proto-x-r3-%s.md" % DAY, "BLOCK")), "")

    def test_clear_newest_nothing(self):
        self.review("review-proto-x-%s.md" % DAY, "BLOCK")
        self.review("review-proto-x-r2-%s.md" % DAY, "BLOCK")
        self.assertEqual(self.hook(self.review("review-proto-x-r3-%s.md" % DAY, "CLEAR")), "")

    def test_diagnosis_named_after_problem_counts(self):
        self.review("review-proto-x-%s.md" % DAY, "BLOCK")
        self.review("review-proto-x-r2-%s.md" % DAY, "BLOCK")
        self.put("diagnosis-wrong-frame-%s.md" % DAY, "# Diagnosis for proto-x: the frame premise\n\nBody.\n")
        self.assertEqual(self.hook(self.review("review-proto-x-r3-%s.md" % DAY, "BLOCK")), "")

    def test_outside_reviews_nothing(self):
        other = os.path.join(self.tmp, "proj", "notes")
        os.makedirs(other)
        self.put("review-proto-x-%s.md" % DAY, "Verdict: BLOCK\n", folder=other)
        p2 = self.put("review-proto-x-r2-%s.md" % DAY, "Verdict: BLOCK\n", folder=other)
        self.assertEqual(self.hook(p2), "")

    def test_reviews_only_higher_up_nothing(self):
        """A folder below a reviews folder is not a reviews folder, though the path holds the word."""
        other = os.path.join(self.reviews, "archive")
        os.makedirs(other)
        self.put("review-proto-x-%s.md" % DAY, "Verdict: BLOCK\n", folder=other)
        p2 = self.put("review-proto-x-r2-%s.md" % DAY, "Verdict: BLOCK\n", folder=other)
        self.assertEqual(self.hook(p2), "")

    def test_diagnosis_written_nothing(self):
        self.review("review-proto-x-%s.md" % DAY, "BLOCK")
        self.review("review-proto-x-r2-%s.md" % DAY, "BLOCK")
        d = self.put("diagnosis-proto-x-%s.md" % DAY, "# Diagnosis\n\nBLOCK twice.\n")
        self.assertEqual(self.hook(d), "")

    def test_slash_forms_and_drive_case(self):
        self.review("review-proto-x-%s.md" % DAY, "BLOCK")
        p2 = self.review("review-proto-x-r2-%s.md" % DAY, "BLOCK")
        alt = p2.replace("/", "\\")
        if len(alt) > 1 and alt[1] == ":":
            alt = alt[0].lower() + alt[1:]
        self.assertIn("has 2 BLOCK reviews", self.notice(self.hook(alt)))
        self.assertEqual(self.hook(p2.replace("\\", "/")), "", "the same piece in another path form is one piece")

    def test_other_tool_nothing(self):
        self.review("review-proto-x-%s.md" % DAY, "BLOCK")
        p2 = self.review("review-proto-x-r2-%s.md" % DAY, "BLOCK")
        self.assertEqual(self.hook(p2, tool="Read"), "")

    def test_bad_stdin_and_missing_file(self):
        self.assertEqual(self.hook("", raw="not json {"), "")
        self.assertEqual(self.hook("", raw=""), "")
        self.assertEqual(self.hook("", raw="[1, 2]"), "")
        self.assertEqual(self.hook(os.path.join(self.reviews, "review-gone-r2-%s.md" % DAY)), "")
        self.assertEqual(self.hook("", raw=json.dumps({"tool_name": "Write", "tool_input": {"file_path": 5}})), "")


if __name__ == "__main__":
    unittest.main(verbosity=1)
