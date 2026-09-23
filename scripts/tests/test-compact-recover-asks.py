"""Tests for the open owner asks block of claude/hooks/compact-recover.py.

    python scripts/tests/test-compact-recover-asks.py

The hook prints the open ask rows of the newest dated decisions file before the rulings, at
both entry points: a full block at a session start (--rulings), the count and the last written
ask after a compaction, and nothing for a subagent. The hook runs the way the harness runs it,
as a subprocess with the payload JSON on stdin; CLAUDE_DECISIONS_GLOB, CLAUDE_RULINGS_FILE,
CLAUDE_CHECKPOINT_DIR and the state sheet variables all point into a temp folder, so nothing a
real session reads is read or written.
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


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The numbers come from the hook itself, so a change to them is read here and not copied.
hook = load("compact_recover_asks", HOOK)
ASKS_CAP = hook.ASKS_CAP
ASKS_COMPACT_CAP = hook.ASKS_COMPACT_CAP
ASK_CLIP = hook.ASK_CLIP
MARKER = hook.ASKS_MORE.strip()
SHUT_WORDS = ("DECIDED", "DONE", "RULED", "APPLIED", "LAPSED", "CLOSED")

OLD = "# Owner decisions, 2026-09-21\n\n- 10:0x old ask, PROPOSED.\n"
NEW = ("# Owner decisions, 2026-09-22 (a PROPOSED word in the heading is not an ask)\n\n"
       "- 17:4x usage options. PROPOSED 17:4x. DECIDED 18:3x.\n"
       + "".join("- 17:5x shut by %s. %s 18:0x.\n" % (word.lower(), word) for word in SHUT_WORDS) +
       "- 18:0x launch check: (1) option one (recommended); (2) option two; (3) as it is. PROPOSED.\n"
       "- 18:1x chip label: the session waits for it.\n"
       "- 18:4x unmarked plan ask, no deadline, review in flight.\n"
       "- 18:5x lowercase row, decided by nobody yet and done nowhere.\n"
       "- 19:0x marker test, UNDECIDED and UNDONE so far.\n"
       "- 19:1x " + "long ask " * 60 + "PROPOSED.\n"
       "A note line with no ask stamp, PROPOSED in it.\n"
       "- 19:2x newest ask, no deadline. PROPOSED.\n")
TEMPLATE = "- 20:0x template row, PROPOSED.\n"
RULINGS = "# Rulings register\n- 2026-09-22 18:3x [owner] go with the recommended option (source)\n"
RULINGS_HEADING = "Rulings (last "


def asks_block(out):
    """The asks block of a hook's output: from its heading to the first blank line."""
    start = out.find("Open owner asks")
    if start < 0:
        return ""
    end = out.find("\n\n", start)
    return out[start:] if end < 0 else out[start:end]


class OpenAsksCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compact-recover-asks-").replace("\\", "/")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.decisions = self.tmp + "/decisions"
        os.makedirs(self.decisions)
        self.write(self.decisions, "owner-decisions-2026-09-21.md", OLD)
        self.write(self.decisions, "owner-decisions-2026-09-22.md", NEW)
        self.write(self.decisions, "owner-decisions-template.md", TEMPLATE)
        self.register = self.write(self.tmp, "rulings.md", RULINGS)
        os.makedirs(self.tmp + "/checkpoints")
        self.payload = {"session_id": "pin00000-0000", "cwd": self.tmp, "source": "resume"}
        self.compact = dict(self.payload, source="compact")

    @staticmethod
    def write(folder, name, text):
        path = folder + "/" + name
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path

    def run_hook(self, payload, args=(), decisions_glob=None):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["CLAUDE_DECISIONS_GLOB"] = decisions_glob or self.decisions + "/owner-decisions-*.md"
        env["CLAUDE_RULINGS_FILE"] = self.register
        env["CLAUDE_CHECKPOINT_DIR"] = self.tmp + "/checkpoints"
        env["CLAUDE_LANE_STATE_SCRIPT"] = self.tmp + "/no-lane-state.py"
        env["CLAUDE_LANE_STATE_SHEET"] = self.tmp + "/law.md"
        env["CLAUDE_INFLIGHT_SCRIPT"] = self.tmp + "/no-inflight.py"
        env["CLAUDE_CODE_DISABLE_1M_CONTEXT"] = "1"
        # No board declared: the gate is open, so the machine running the suite decides nothing.
        for name in ("CLAUDE_BOARD_ROOT", "CLAUDE_BOARD_PREFIXES", "CLAUDE_ROLE", "CLAUDE_BRIEFS_DIR",
                     "CLAUDE_LANDINGS_FILE", "CLAUDE_CODE_AUTO_COMPACT_WINDOW"):
            env.pop(name, None)
        process = subprocess.run([sys.executable, HOOK] + list(args), input=json.dumps(payload).encode("utf-8"),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", "replace"))
        return process.stdout.decode("utf-8", "replace")

    def start_block(self):
        out = self.run_hook(self.payload, args=("--rulings",))
        return out, asks_block(out)

    def test_a_session_start_prints_the_open_rows_of_the_newest_dated_file_before_the_rulings(self):
        out, block = self.start_block()
        self.assertLess(out.index("Open owner asks"), out.index(RULINGS_HEADING))
        self.assertIn("owner-decisions-2026-09-22.md", block)
        for text in ("launch check", "chip label", "unmarked plan ask", "UNDECIDED", "lowercase row"):
            self.assertIn(text, block, "an open row is missing: " + text)

    def test_rows_print_last_written_first_under_a_heading_that_counts_them(self):
        _, block = self.start_block()
        rows = [line for line in block.split("\n")[1:] if line.startswith("- ")]
        self.assertTrue(rows[0].startswith("- 19:2x newest ask"), rows[:1])
        self.assertIn("Open owner asks (7)", block)
        self.assertIn("last written first", block)

    def test_shut_rows_undated_lines_older_files_and_undated_siblings_stay_out(self):
        _, block = self.start_block()
        for text in ("usage options", "old ask", "no ask stamp", "not an ask", "template row"):
            self.assertNotIn(text, block)

    def test_each_shut_word_in_capitals_closes_its_row(self):
        _, block = self.start_block()
        for word in SHUT_WORDS:
            self.assertNotIn("shut by %s" % word.lower(), block)

    def test_a_long_row_is_clipped_under_a_heading_that_points_at_the_file(self):
        _, block = self.start_block()
        self.assertIn("read the row in the file", block)
        long_rows = [line for line in block.split("\n") if line.startswith("- 19:1x")]
        self.assertEqual(len(long_rows), 1)
        self.assertLessEqual(len(long_rows[0]), ASK_CLIP)
        self.assertTrue(long_rows[0].endswith(" [cut]"))

    def test_a_compaction_prints_the_count_and_the_last_written_row_before_the_rulings(self):
        out = self.run_hook(self.compact)
        block = asks_block(out)
        self.assertLess(out.index("Open owner asks"), out.index(RULINGS_HEADING))
        self.assertLessEqual(len(block), ASKS_COMPACT_CAP)
        self.assertIn("Open owner asks (7)", block)
        self.assertIn("19:2x newest ask", block)
        self.assertTrue(block.endswith(MARKER))

    def test_the_cap_holds_its_marker_and_keeps_the_newest_row_at_every_width(self):
        # Row widths 130 to 168 move where the block is cut, so one of them ends it within a
        # marker's length of the cap: a cap that forgets the marker overruns there.
        sizes = []
        for pad in range(130, 169):
            folder = self.tmp + "/many%d" % pad
            os.makedirs(folder)
            self.write(folder, "owner-decisions-2026-09-23.md",
                       "".join("- %02d:0x ask number %02d %s PROPOSED.\n" % (i % 24, i, "x" * pad) for i in range(30)))
            out = self.run_hook(self.payload, args=("--rulings",), decisions_glob=folder + "/owner-decisions-*.md")
            block = asks_block(out)
            self.assertTrue(block.endswith(MARKER), pad)
            self.assertIn("ask number 29", block)
            sizes.append(len(block))
        self.assertLessEqual(max(sizes), ASKS_CAP)
        self.assertGreater(max(sizes), ASKS_CAP - len(MARKER) - 2)

    def test_the_newest_file_is_read_by_the_date_in_its_name_not_by_its_prefix(self):
        folder = self.tmp + "/mixed"
        os.makedirs(folder)
        self.write(folder, "asks-2026-09-23.md", "- 08:0x today ask, PROPOSED.\n")
        self.write(folder, "zz-notes-2026-01-05.md", "- 08:0x january row PROPOSED.\n")
        block = asks_block(self.run_hook(self.payload, args=("--rulings",), decisions_glob=folder + "/*.md"))
        self.assertIn("today ask", block)
        self.assertNotIn("january row", block)

    def test_a_long_path_never_drops_the_newest_ask_after_a_compaction(self):
        folder = self.tmp + "/" + "a-very-long-folder-name-" * 5
        os.makedirs(folder)
        self.write(folder, "owner-decisions-2026-09-23.md",
                   "".join("- %02d:0x ask %02d %s PROPOSED.\n" % (i, i, "y" * 190) for i in range(3)))
        block = asks_block(self.run_hook(self.compact, decisions_glob=folder + "/owner-decisions-*.md"))
        self.assertLessEqual(len(block), ASKS_COMPACT_CAP)
        self.assertIn("Open owner asks (3)", block)
        self.assertIn("owner-decisions-2026-09-23.md", block)
        self.assertIn("- 02:0x ask 02", block)
        self.assertTrue(block.endswith(MARKER))

    def test_one_open_row_keeps_the_full_path_when_it_fits_without_a_marker(self):
        folder = self.tmp + "/" + "d" * 40
        os.makedirs(folder)
        path = folder + "/owner-decisions-2026-09-23.md"
        heading = ("Open owner asks (1) in %s, last written first; rows clipped: read the row in the file"
                   " and restate an ask whole, never by number:" % path)
        width = ASKS_COMPACT_CAP - len(heading) - 1 - 10  # fits alone, not beside a marker
        self.assertTrue(20 < width <= ASK_CLIP and width + len(MARKER) + 1 > 10, width)
        self.write(folder, "owner-decisions-2026-09-23.md", "- 09:0x " + "z" * (width - 8) + "\n")
        block = asks_block(self.run_hook(self.compact, decisions_glob=folder + "/owner-decisions-*.md"))
        self.assertIn(path, block, "the full path fits beside the one row and no marker is owed")
        self.assertLessEqual(len(block), ASKS_COMPACT_CAP)
        self.assertFalse(block.endswith(MARKER))

    def test_a_subagent_gets_no_asks_at_either_entry_and_the_rulings_still_print(self):
        for extra in ({"agent_type": "reviewer"}, {"agent_id": "a0000000000000000"}):
            for payload, args in ((self.payload, ("--rulings",)), (self.compact, ())):
                out = self.run_hook(dict(payload, **extra), args=args)
                self.assertNotIn("Open owner asks", out)
                self.assertIn(RULINGS_HEADING, out)

    def test_no_decisions_file_means_no_block_and_the_rulings_still_print(self):
        out = self.run_hook(self.payload, args=("--rulings",), decisions_glob=self.tmp + "/none-*.md")
        self.assertNotIn("Open owner asks", out)
        self.assertIn(RULINGS_HEADING, out)


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    unittest.main(verbosity=2)
