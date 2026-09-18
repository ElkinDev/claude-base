"""Tests for the board gate of claude/hooks/compact-recover.py.

    python claude/hooks/tests/test-board-gate.py

The board is the set of folders whose sessions hold a chair: the root and, when prefixes are
configured, the folders under it whose name starts with one of them. A session anywhere else
is handed nothing of the board, so a personal folder never gets the register, the seat block,
the state sheet or the resume brief of a board it has nothing to do with.

Every case builds its environment from scratch: CLAUDE_BOARD_ROOT and CLAUDE_BOARD_PREFIXES
name a temporary board, CLAUDE_RULINGS_FILE a fixture register, and the checkpoint directory
and the state sheet point into a temporary folder, so no register, checkpoint or sheet of a
real session is read or written. Nothing under the user's home is touched.
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
HOOKS = os.path.dirname(HERE)
HOOK = os.path.join(HOOKS, "compact-recover.py")

BOARD_VARS = (
    "CLAUDE_BOARD_ROOT",
    "CLAUDE_BOARD_PREFIXES",
    "CLAUDE_RULINGS_FILE",
    "CLAUDE_CHECKPOINT_DIR",
    "CLAUDE_LANE_STATE_SCRIPT",
    "CLAUDE_LANE_STATE_SHEET",
    "CLAUDE_BRIEFS_DIR",
    "CLAUDE_LANDINGS_FILE",
    "CLAUDE_ROLE",
)

REGISTER = """# Rulings register

- 2026-09-06 09:00 [process] a ruling the block must print (source 1)
"""


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = load("compact_recover_board", HOOK)


class OnBoardTest(unittest.TestCase):
    """The predicate itself, by path alone. It is called once per session start, so it reads
    the environment and nothing else: no disk, no git, no subprocess."""

    def setUp(self):
        self.saved = {name: os.environ.get(name) for name in ("CLAUDE_BOARD_ROOT", "CLAUDE_BOARD_PREFIXES")}
        os.environ["CLAUDE_BOARD_ROOT"] = "D:/Board"
        os.environ["CLAUDE_BOARD_PREFIXES"] = "AppRepo;evidence;kit"

    def tearDown(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_the_root_itself_is_on_the_board(self):
        self.assertTrue(hook.on_board("D:/Board"))
        self.assertTrue(hook.on_board("D:\\Board\\"))

    def test_a_prefixed_child_and_a_grandchild_are_on_the_board(self):
        self.assertTrue(hook.on_board("D:/Board/AppRepo"))
        self.assertTrue(hook.on_board("D:/Board/AppRepo-w42/app"))
        self.assertTrue(hook.on_board("d:/board/EVIDENCE/lanes"))

    def test_a_sibling_project_is_off_the_board(self):
        self.assertFalse(hook.on_board("D:/Board/OtherProject"))
        self.assertFalse(hook.on_board("E:/elsewhere/personal"))

    def test_a_prefix_trap_next_to_the_root_is_off_the_board(self):
        """A folder whose path starts with the root as text but is not under it: the
        separator is what decides, never the string prefix."""
        self.assertFalse(hook.on_board("D:/Boardsitory"))
        self.assertFalse(hook.on_board("D:/Boardsitory/AppRepo"))

    def test_an_empty_cwd_is_off_the_board(self):
        self.assertFalse(hook.on_board(""))
        self.assertFalse(hook.on_board(None))

    def test_an_empty_cwd_is_off_the_board_even_with_no_root_declared(self):
        """Nothing is known about an empty folder, so nothing is printed for it, whatever the board."""
        os.environ.pop("CLAUDE_BOARD_ROOT", None)
        self.assertFalse(hook.on_board(""))
        self.assertFalse(hook.on_board(None))

    def test_without_a_root_every_folder_is_on_the_board(self):
        """The kit ships no board. A project that never declares one keeps the whole block,
        which is the behaviour every install had before the gate."""
        os.environ.pop("CLAUDE_BOARD_ROOT", None)
        self.assertEqual(hook.BOARD_ROOT, "", "the kit default declares no board")
        self.assertTrue(hook.on_board("C:/anywhere/at/all"))

    def test_without_prefixes_the_whole_root_is_the_board(self):
        os.environ.pop("CLAUDE_BOARD_PREFIXES", None)
        self.assertEqual(hook.BOARD_PREFIXES, (), "the kit default configures no prefix")
        self.assertTrue(hook.on_board("D:/Board/OtherProject"))
        self.assertFalse(hook.on_board("D:/Elsewhere"))


class BoardGateTest(unittest.TestCase):
    """The hook as the harness runs it: a subprocess with the payload JSON on stdin."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="board-gate-")
        self.board = os.path.join(self.tmp, "board")
        self.off = os.path.join(self.tmp, "personal")
        os.makedirs(self.board)
        os.makedirs(self.off)
        self.register = os.path.join(self.tmp, "rulings.md")
        with open(self.register, "w", encoding="utf-8") as handle:
            handle.write(REGISTER)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_hook(self, cwd, args=()):
        env = os.environ.copy()
        for name in BOARD_VARS:
            env.pop(name, None)
        env["PYTHONIOENCODING"] = "utf-8"
        env["CLAUDE_BOARD_ROOT"] = self.board
        env["CLAUDE_RULINGS_FILE"] = self.register
        env["CLAUDE_CHECKPOINT_DIR"] = os.path.join(self.tmp, "checkpoints")
        env["CLAUDE_LANE_STATE_SCRIPT"] = os.path.join(self.tmp, "no-renderer.py")
        env["CLAUDE_LANE_STATE_SHEET"] = os.path.join(self.tmp, "no-sheet.md")
        payload = json.dumps({"session_id": "zz", "cwd": cwd}).encode("utf-8")
        done = subprocess.run(
            [sys.executable, HOOK] + list(args),
            input=payload,
            capture_output=True,
            env=env,
            cwd=self.tmp,
            timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        return done.stdout.decode("utf-8", "replace")

    def test_rulings_mode_prints_nothing_off_the_board(self):
        out = self.run_hook(self.off, ["--rulings"])
        self.assertEqual(out, "", "not a byte of the board off the board: %r" % out[:200])

    def test_rulings_mode_prints_the_seat_block_and_the_register_on_the_board(self):
        out = self.run_hook(self.board, ["--rulings"])
        self.assertIn("Seat: ", out)
        self.assertIn("a ruling the block must print", out)

    def test_compact_mode_off_the_board_prints_only_the_checkpoint_line(self):
        out = self.run_hook(self.off)
        self.assertTrue(out.startswith("[compaction recovery "), out[:120])
        self.assertIn("off the board", out)
        self.assertNotIn("Seat: ", out)
        self.assertNotIn("Rulings (", out)
        self.assertNotIn("NOTES.md", out)
        self.assertEqual(out.count("\n\n"), 0, "one paragraph and no more: %r" % out)

    def test_compact_mode_on_the_board_keeps_the_block(self):
        out = self.run_hook(self.board)
        self.assertIn("Seat: ", out)
        self.assertIn("a ruling the block must print", out)
        self.assertIn("No NOTES.md in", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
