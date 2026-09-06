"""Tests for the rulings block of claude/hooks/compact-recover.py.

    python scripts/tests/test-compact-recover-rulings.py

The hook runs the way the harness runs it, as a subprocess with the payload JSON on
stdin, against a fixture register in a temp folder. CLAUDE_RULINGS_FILE points at that
fixture, CLAUDE_CHECKPOINT_DIR at an empty temp dir, and CLAUDE_LANE_STATE_SCRIPT and
CLAUDE_LANE_STATE_SHEET at temp paths, so no register, checkpoint or state sheet of a
real session is read or written. Nothing under the user's home is touched.

The register is append-only by rule: a ruling decided at 11:47 and written at 12:30 is
appended below a 12:2x row while carrying the older stamp, so file order is not time
order. The block sorts the rows by their stamp before it cuts, which is what keeps a
cap from dropping the newest ruling and keeping an older one.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
HOOK = os.path.join(ROOT, "claude", "hooks", "compact-recover.py")

STDIN_JSON = json.dumps({"session_id": "zz", "cwd": ROOT}).encode("utf-8")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The numbers come from the hook itself: a copy here would keep passing after the hook
# changed them, which is the one thing these cases exist to catch.
hook = load("compact_recover", HOOK)
RULINGS_ROWS = hook.RULINGS_ROWS
RULINGS_CAP = hook.RULINGS_CAP
CUT_MARKER = hook.RULINGS_MARKER.strip()
HEADING_START = "Rulings (last %d, register " % RULINGS_ROWS

HEADER = """# Rulings register

Row shape: `- YYYY-MM-DD HH:MM [scope] ruling in one line (source)`.

"""


def row(index, stamp="09:00", scope="process", pad=0):
    text = "- 2026-09-06 %s [%s] ruling number %d" % (stamp, scope, index)
    return text + (" " + "d" * pad if pad else "") + " (source %d)" % index


class RulingsBlockCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compact-recover-rulings-").replace("\\", "/")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.checkpoints = os.path.join(self.tmp, "checkpoints")
        os.makedirs(self.checkpoints)
        self.sheet = self.tmp + "/law.md"
        self.renderer = self.tmp + "/no-lane-state.py"

    def write_register(self, rows, header=HEADER):
        path = self.tmp + "/rulings.md"
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(header + "\n".join(rows) + ("\n" if rows else ""))
        return path

    def run_hook(self, register, args=(), stdin=STDIN_JSON):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["CLAUDE_RULINGS_FILE"] = register
        env["CLAUDE_CHECKPOINT_DIR"] = self.checkpoints
        # A renderer path that is not a file, so the state sheet paragraph is skipped
        # and no run of this suite can render a sheet a live session reads.
        env["CLAUDE_LANE_STATE_SCRIPT"] = self.renderer
        env["CLAUDE_LANE_STATE_SHEET"] = self.sheet
        for name in ("CLAUDE_BRIEFS_DIR", "CLAUDE_LANDINGS_FILE"):
            env.pop(name, None)
        process = subprocess.run(
            [sys.executable, HOOK] + list(args),
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", "replace"))
        self.assertFalse(os.path.exists(self.sheet), "the run wrote a state sheet")
        return process.stdout.decode("utf-8", "replace")

    # --- the seeded register in full mode ---------------------------------
    def test_the_seeded_register_prints_whole_after_the_checkpoint_paragraph(self):
        rows = [row(i, "1%d:00" % (i % 10)) for i in range(1, 6)]
        out = self.run_hook(self.write_register(rows))
        self.assertIn(HEADING_START, out)
        for line in rows:
            self.assertIn(line, out)
        self.assertLess(out.index("[compaction recovery"), out.index(HEADING_START))
        self.assertLess(out.index(HEADING_START), out.index("NOTES.md"))
        self.assertNotIn(CUT_MARKER, out)

    def test_only_the_last_rows_are_kept_when_the_register_is_longer(self):
        rows = [row(i, "09:%02d" % i) for i in range(1, RULINGS_ROWS + 6)]
        out = self.run_hook(self.write_register(rows), args=("--rulings",))
        for line in rows[5:]:
            self.assertIn(line, out)
        for line in rows[:5]:
            self.assertNotIn(line, out)

    # --- the cap ----------------------------------------------------------
    def test_the_cap_drops_the_oldest_by_stamp_first_and_counts_its_marker(self):
        rows = [row(i, "09:%02d" % i, pad=500) for i in range(1, RULINGS_ROWS + 1)]
        out = self.run_hook(self.write_register(rows), args=("--rulings",))
        self.assertLessEqual(len(out), RULINGS_CAP)
        self.assertIn(CUT_MARKER, out)
        self.assertIn(rows[-1], out)
        self.assertNotIn(rows[0], out)
        self.assertLess(out.index(CUT_MARKER), out.index(rows[-1]))

    def test_the_cap_reads_the_stamp_and_not_the_file_order(self):
        newest = row(1, "12:2x", pad=500)
        older = [row(i, "09:%02d" % i, pad=500) for i in range(2, RULINGS_ROWS + 1)]
        # The newest ruling is written at the top of the file, the way an append made
        # after a later row leaves it: a cut that read file order would drop it first.
        out = self.run_hook(self.write_register([newest] + older), args=("--rulings",))
        self.assertIn(newest, out)
        self.assertNotIn(older[0], out)
        self.assertTrue(out.rstrip().endswith(newest), "the newest row is not last")

    # --- out of order -----------------------------------------------------
    def test_out_of_order_rows_are_printed_oldest_first_by_stamp(self):
        early = row(1, "11:4x", "rulings")
        late = row(2, "12:2x", "ops")
        older_day = "- 2026-09-05 21:15 [process] a ruling of the day before (source)"
        out = self.run_hook(self.write_register([late, early, older_day]),
                            args=("--rulings",))
        self.assertLess(out.index(older_day), out.index(early))
        self.assertLess(out.index(early), out.index(late))

    def test_an_x_hour_sorts_after_every_real_digit_of_its_position(self):
        exact, blurred, next_ten = row(1, "12:19"), row(2, "12:1x"), row(3, "12:20")
        unknown = row(4, "xx:xx")
        out = self.run_hook(self.write_register([unknown, next_ten, blurred, exact]),
                            args=("--rulings",))
        self.assertLess(out.index(exact), out.index(blurred))
        self.assertLess(out.index(blurred), out.index(next_ten))
        self.assertLess(out.index(next_ten), out.index(unknown))

    def test_rows_with_the_same_stamp_keep_file_order(self):
        first, second = row(1, "10:00"), row(2, "10:00")
        out = self.run_hook(self.write_register([second, first]), args=("--rulings",))
        self.assertLess(out.index(second), out.index(first))

    # --- the register that is not there -----------------------------------
    def test_a_missing_register_is_one_line_and_not_a_dropped_paragraph(self):
        missing = self.tmp + "/gone.md"
        out = self.run_hook(missing, args=("--rulings",))
        self.assertEqual(out, "No rulings register at %s." % missing)

    def test_an_empty_register_says_so_instead(self):
        path = self.write_register([], header="# Rulings register\n")
        out = self.run_hook(path, args=("--rulings",))
        self.assertEqual(out, "No rulings yet in %s." % path)

    # --- the --rulings mode -----------------------------------------------
    def test_rulings_alone_prints_the_block_and_nothing_else(self):
        rows = [row(i) for i in range(1, 4)]
        out = self.run_hook(self.write_register(rows), args=("--rulings",),
                            stdin=b"")
        self.assertTrue(out.startswith(HEADING_START), out[:80])
        self.assertNotIn("[compaction recovery", out)
        self.assertNotIn("NOTES.md", out)
        self.assertEqual(out.splitlines()[1:], rows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
