"""Tests for scripts/row.sh (the register row wrapper) and scripts/row-refusals.py (its reader).

    python scripts/tests/test-row-wrapper.py

The wrapper is run as the seat runs it, through bash, with ROW_REGISTER pointing at a temporary file,
so no real register is ever appended to. The reader is fed a transcript built from the wrapper's own
stdout, which is the one thing that keeps the two from drifting apart: a reader whose patterns no
longer match what the writer prints counts zero of everything and says so in no way at all.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ROW = os.path.join(ROOT, "scripts", "row.sh").replace(os.sep, "/")
REFUSALS = os.path.join(ROOT, "scripts", "row-refusals.py")
BASH = shutil.which("bash")
CAP = 400

SENTENCES = ("The gate ran and the union is clean. " * 20)  # 740 characters, a boundary every 37


@unittest.skipUnless(BASH, "bash is not on PATH")
class RowWrapperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="row-wrapper-")
        self.register = os.path.join(self.tmp, "rulings.md").replace(os.sep, "/")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def row(self, kind, text, pane=None, register=None):
        args = [BASH, ROW, kind, text] + ([pane] if pane else [])
        env = dict(os.environ, ROW_REGISTER=register if register is not None else self.register,
                   PYTHONIOENCODING="utf-8")
        done = subprocess.run(args, capture_output=True, env=env, cwd=self.tmp, timeout=60)
        return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")

    def rows(self, path=None):
        path = path or self.register
        if not os.path.isfile(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in handle if line.strip()]

    def test_a_text_under_the_cap_is_written_whole(self):
        text = "a" * 299 + "."
        code, out, err = self.row("decision", text, "analyst pane")
        self.assertEqual(0, code, err)
        self.assertIn("ROW ok text 300/%d" % CAP, out)
        self.assertNotIn("CUT", out)
        rows = self.rows()
        self.assertEqual(1, len(rows), rows)
        self.assertIn(text, rows[0])
        self.assertIn("[decision]", rows[0])
        self.assertTrue(rows[0].endswith("x)"), "the row closes with the pane and the stamp: %r" % rows[0])
        self.assertTrue(rows[0].startswith("- 2"), "the shape the recovery hook reads: %r" % rows[0][:20])

    def test_a_text_over_the_cap_is_cut_at_a_sentence_boundary_and_says_so(self):
        text = SENTENCES[:500].strip()
        code, out, err = self.row("diagnosis", text)
        self.assertEqual(0, code, err)
        self.assertIn("ROW ok text %d/%d" % (len(text), CAP), out)
        self.assertIn("CUT", out)
        self.assertIn("dropped:", out, "the tail comes back so the seat can add a continuation row")
        row = self.rows()[0]
        self.assertIn("[cut ", row)
        body = row.split("] ", 1)[1].split(" [cut ", 1)[0]
        self.assertLessEqual(len(body), CAP)
        self.assertGreaterEqual(len(body), CAP // 2, "a cut never gives up more than half the cap")
        self.assertTrue(body.endswith("."), "the cut falls on a sentence boundary: %r" % body[-40:])

    def test_a_text_with_no_boundary_is_cut_at_the_cap_itself(self):
        text = "b" * 500
        code, out, err = self.row("note", text)
        self.assertEqual(0, code, err)
        row = self.rows()[0]
        body = row.split("] ", 1)[1].split(" [cut ", 1)[0]
        self.assertEqual(CAP, len(body), "with no sentence to cut at, the cap is the boundary")
        self.assertIn("[cut 100]", row)

    def test_a_blank_text_is_refused_and_writes_nothing(self):
        code, out, err = self.row("decision", "   ")
        self.assertEqual(1, code, out)
        self.assertIn("ROW REFUSED", out)
        self.assertEqual([], self.rows(), "a refused row never reaches the register")

    def test_a_kind_with_a_space_is_refused_and_writes_nothing(self):
        code, out, err = self.row("two words", "a real decision.")
        self.assertEqual(1, code, out)
        self.assertIn("ROW REFUSED: bad kind", out)
        self.assertEqual([], self.rows())

    def test_a_pane_that_is_not_a_short_line_is_refused(self):
        code, out, err = self.row("decision", "a real decision.", "pane 3 of the board")
        self.assertEqual(1, code, out)
        self.assertIn("ROW REFUSED: bad pane", out)
        self.assertEqual([], self.rows())

    def test_row_register_redirects_the_write(self):
        other = os.path.join(self.tmp, "dry-run.md").replace(os.sep, "/")
        code, out, err = self.row("decision", "a dry run row.", None, other)
        self.assertEqual(0, code, err)
        self.assertEqual(1, len(self.rows(other)), self.rows(other))
        self.assertEqual([], self.rows(), "the register named by the variable is the only file touched")

    def test_the_reader_counts_what_the_wrapper_printed(self):
        _, whole, _ = self.row("decision", "a row that fits.")
        _, cut, _ = self.row("diagnosis", SENTENCES[:500].strip())
        _, refused, _ = self.row("decision", "  ")
        transcript = os.path.join(self.tmp, "session.jsonl")
        with open(transcript, "w", encoding="utf-8", newline="\n") as handle:
            for text in (whole, cut, refused):
                handle.write(json.dumps({
                    "type": "user",
                    "timestamp": "2026-09-18T10:00:00.000Z",
                    "message": {"content": [{"type": "tool_result", "content": text}]},
                }) + "\n")
        done = subprocess.run(
            [sys.executable, REFUSALS, "--file", transcript],
            capture_output=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"), cwd=self.tmp, timeout=60,
        )
        self.assertEqual(0, done.returncode, done.stderr[-400:])
        out = done.stdout.decode("utf-8", "replace")
        self.assertIn("rows written 2 (cut 1) refused 1", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
