"""Tests for the rulings block of claude/hooks/compact-recover.py.

    python scripts/tests/test-compact-recover-rulings.py
    COMPACT_RECOVER_HOOK=<installed hook> python scripts/tests/test-compact-recover-rulings.py

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
KIT_HOOK = os.path.join(ROOT, "claude", "hooks", "compact-recover.py")
# COMPACT_RECOVER_HOOK runs the same cases against an installed copy of the hook, which may carry
# its machine's defaults; only the kit's own copy is held to naming no machine path.
HOOK = os.environ.get("COMPACT_RECOVER_HOOK") or KIT_HOOK

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
# What the hook prints before the rulings for a pane that holds no seat.
LANE_BLOCK = "Seat: none (lane)" + "\n\n"

HEADER = """# Rulings register

Row shape: `- YYYY-MM-DD HH:MM [scope] ruling in one line (source)`.

"""


def row(index, stamp="09:00", scope="process", pad=0):
    text = "- 2026-09-06 %s [%s] ruling number %d" % (stamp, scope, index)
    return text + (" " + "d" * pad if pad else "") + " (source %d)" % index


# Stands in for the state renderer: writes a sheet with a Gates section the size a real one carries, so the case
# that measures the whole output measures the production shape.
STUB_RENDERER = '''import os
lines = ["# stub state sheet", "## Gates (last 24 h)"]
for index in range(20):
    lines.append("lan lan-g%02d 10:00 abc1234 exit=0 lock=0 moved=0 ok phases=5 secs=1200" % index)
with open(os.environ["CLAUDE_LANE_STATE_SHEET"], "w", encoding="utf-8", newline="\\n") as out:
    out.write("\\n".join(lines) + "\\n")
'''
# The name the checkpoint hook gives a checkpoint of session zz, the session_id of STDIN_JSON.
CHECKPOINT_NAME = "20260906-114500-zz-orchestrator.md"
# The sheet the hook renders when nothing moves it; no run of this suite may touch it.
REAL_SHEET = hook.LANE_STATE_SHEET
REAL_SHEET_MTIME = os.path.getmtime(REAL_SHEET) if os.path.isfile(REAL_SHEET) else None


class RulingsBlockCase(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        now = os.path.getmtime(REAL_SHEET) if os.path.isfile(REAL_SHEET) else None
        if now != REAL_SHEET_MTIME:
            raise AssertionError("the suite rewrote or created the real state sheet %s" % REAL_SHEET)

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

    def run_hook(self, register, args=(), stdin=STDIN_JSON, env_extra=None):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["CLAUDE_RULINGS_FILE"] = register
        env["CLAUDE_CHECKPOINT_DIR"] = self.checkpoints
        # A renderer path that is not a file, so the state sheet paragraph is skipped
        # and no run of this suite can render a sheet a live session reads.
        env["CLAUDE_LANE_STATE_SCRIPT"] = self.renderer
        env["CLAUDE_LANE_STATE_SHEET"] = self.sheet
        # A decisions glob that matches nothing, so the open-asks block never prints here and
        # the exact outputs below stay the rulings alone. The asks have their own suite,
        # test-compact-recover-asks.py.
        env["CLAUDE_DECISIONS_GLOB"] = self.tmp + "/no-decisions-*.md"
        for name in ("CLAUDE_BRIEFS_DIR", "CLAUDE_LANDINGS_FILE", "CLAUDE_ROLE",
                     "CLAUDE_CODE_AUTO_COMPACT_WINDOW"):
            env.pop(name, None)
        # The hook opens with the seat block, so the pane that runs the suite would otherwise
        # decide what the first line says. No role is a lane, and one launcher variable set is
        # what keeps the loud line of an unlaunched session out of these cases.
        env["CLAUDE_CODE_DISABLE_1M_CONTEXT"] = "1"
        # The payload's folder, and the working folder when the payload is empty, is the board
        # root, so a hook installed with a board of its own still prints its board blocks here.
        env["CLAUDE_BOARD_ROOT"] = ROOT
        env.update(env_extra or {})
        process = subprocess.run(
            [sys.executable, HOOK] + list(args),
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=ROOT,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", "replace"))
        if "CLAUDE_LANE_STATE_SCRIPT" not in (env_extra or {}):
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

    def test_the_session_shape_fills_the_cap_from_its_tail_and_keeps_every_ruling(self):
        """The shape a session gets at a compaction: a checkpoint, a register whose last rows
        nearly fill the rulings cap, a rendered state sheet, a landings file and a briefs dir.
        The whole output stays under the hook's CAP; what the cap takes is the tail, never a
        ruling; and the paragraphs keep their order: checkpoint, rulings, state sheet."""
        width = (RULINGS_CAP - 250) // RULINGS_ROWS - 1  # the block a few rows short of its cap
        rows = [row(i, "10:%02d" % i, pad=max(0, width - len(row(i, "10:%02d" % i)) - 1))
                for i in range(1, RULINGS_ROWS + 1)]
        with open(os.path.join(self.checkpoints, CHECKPOINT_NAME), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# Checkpoint\n\n## Disk truth\n" + "\n".join(
                "- C:/src/app-lane%02d: branch lane-%02d, tip abc%04d, 0 uncommitted" % (i, i, i)
                for i in range(40)) + "\n\n## Next\n- the next step\n")
        stub = self.tmp + "/stub-lane-state.py"
        with open(stub, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(STUB_RENDERER)
        landings, briefs = self.tmp + "/landings.md", self.tmp + "/briefs"
        with open(landings, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join("2026-09-06 11:%02d landing row %d" % (i, i) for i in range(1, 9)) + "\n")
        os.makedirs(briefs)
        with open(briefs + "/lane-brief.md", "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# Brief\n")
        out = self.run_hook(self.write_register(rows), env_extra={
            "CLAUDE_LANE_STATE_SCRIPT": stub, "CLAUDE_LANDINGS_FILE": landings, "CLAUDE_BRIEFS_DIR": briefs})
        self.assertLessEqual(len(out), hook.CAP)
        marker = hook.CAP_MARKER.strip()
        self.assertIn(marker, out, "this fixture no longer fills the cap (%d chars), so the cut is untested" % len(out))
        self.assertIn(CHECKPOINT_NAME, out)
        block = out[out.index(HEADING_START):].split("\n\n")[0]
        self.assertNotIn(CUT_MARKER, block, "a ruling was dropped in the shape a session gets")
        self.assertEqual([ln for ln in block.splitlines() if ln.startswith("- 20")], rows)
        self.assertLess(out.index(CHECKPOINT_NAME), out.index(HEADING_START))
        self.assertIn("## Gates (last 24 h)", out)
        self.assertLess(out.index(HEADING_START), out.index("## Gates (last 24 h)"))
        self.assertTrue(out.endswith(marker), out[-90:])
        self.assertGreater(out.index(marker), out.index(HEADING_START) + len(block),
                           "the output cap cut into the rulings block")

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

    # --- the two row shapes ----------------------------------------------
    def test_a_row_without_the_leading_dash_is_a_ruling_and_sorts_by_its_stamp(self):
        """A row-writing helper appends rows with no leading "- ", beside the hand rows
        that carry it. A reader that knows one shape drops every row of the other, and
        the newest rulings are then the ones missing."""
        rows = [
            "2026-09-16 08:0x [decision] EARLY bare row, older than every dash row.",
            "- 2026-09-17 14:2x [process] OLDEST dash row.",
            "2026-09-20 09:1x [process] MIDDLE bare row, as a helper writes it.",
            "- 2026-09-17 14:0x [owner] OLDER dash row written after a later one.",
            "not a row, a note line",
            "2026-09-22 18:3x [owner] NEWEST bare row. (source)",
        ]
        out = self.run_hook(self.write_register(rows), args=("--rulings",))
        printed = out[len(LANE_BLOCK):].splitlines()[1:]
        self.assertEqual(len(printed), 5, printed)
        self.assertIn("EARLY bare row", printed[0])
        self.assertIn("OLDER dash row", printed[1])
        self.assertIn("MIDDLE bare row", printed[-2])
        self.assertIn("NEWEST bare row", printed[-1])
        self.assertNotIn("not a row", out)

    def test_the_sort_key_reads_date_and_hour_from_either_shape(self):
        dash, bare = "- 2026-09-20 09:1x [x] dash", "2026-09-20 09:1x [x] bare"
        self.assertEqual(hook.ruling_key(dash), ("2026-09-20", "09:1x"))
        self.assertEqual(hook.ruling_key(bare), ("2026-09-20", "09:1x"))

    # --- the register that is not there -----------------------------------
    def test_a_missing_register_is_one_line_and_not_a_dropped_paragraph(self):
        missing = self.tmp + "/gone.md"
        out = self.run_hook(missing, args=("--rulings",))
        self.assertEqual(out, LANE_BLOCK + "No rulings register at %s." % missing)

    def test_an_empty_register_says_so_instead(self):
        path = self.write_register([], header="# Rulings register\n")
        out = self.run_hook(path, args=("--rulings",))
        self.assertEqual(out, LANE_BLOCK + "No rulings yet in %s." % path)

    # --- the defaults of a public hook ------------------------------------
    @unittest.skipUnless(os.path.normcase(os.path.abspath(HOOK)) == os.path.normcase(os.path.abspath(KIT_HOOK)),
                         "an installed copy names its machine's defaults by design")
    def test_the_hook_carries_no_path_of_one_machine(self):
        """The kit is installed on any home, so a default that names a drive letter
        and somebody's folder is a default that works on one machine only. The four
        defaults are home-relative and the env seams move them from there."""
        with open(HOOK, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        # The offending lines are named, not the whole file: a failure here has to be
        # readable, and printing the source drowns it.
        guilty = ["%d: %s" % (n, line) for n, line in enumerate(lines, 1)
                  if "C:/" in line or "C:\\" in line]
        self.assertEqual(guilty, [], "the hook names a path of one machine")
        home = os.path.expanduser("~")
        for name in ("RULINGS_FILE", "LANE_STATE_SCRIPT", "LANE_STATE_SHEET", "DECISIONS_GLOB"):
            value = getattr(hook, name)
            self.assertTrue(os.path.normcase(value).startswith(os.path.normcase(home)),
                            "%s is not under the home dir: %s" % (name, value))

    # --- the --rulings mode -----------------------------------------------
    def test_rulings_alone_prints_the_seat_block_and_the_rulings_and_nothing_else(self):
        rows = [row(i) for i in range(1, 4)]
        out = self.run_hook(self.write_register(rows), args=("--rulings",),
                            stdin=b"")
        self.assertTrue(out.startswith(LANE_BLOCK + HEADING_START), out[:80])
        self.assertNotIn("[compaction recovery", out)
        self.assertNotIn("NOTES.md", out)
        self.assertEqual(out[len(LANE_BLOCK):].splitlines()[1:], rows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
