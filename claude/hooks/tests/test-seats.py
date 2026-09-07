"""Tests for the seat: the two seat files the kit installs, the seat block compact-recover.py
prints, the closing notice prompt-log.py adds, and the settings the seat needs.

    python claude/hooks/tests/test-seats.py

A seat is a markdown file appended to the default system prompt by the launcher, plus
`CLAUDE_ROLE` in the environment. The hooks read the variable, never a file of the launcher, so
every case here runs the hook as a subprocess with the payload JSON on stdin and an environment
this file builds from scratch: the seat variables of the session that runs the suite are dropped
first, so the result never depends on the pane the tests were started from. Nothing under the
user's home is read or written.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(HOOKS))
SEATS = os.path.join(ROOT, "claude", "seats")
SEAT_NAMES = ("orchestrator", "analyst")


def seat_file(name):
    return os.path.join(SEATS, name + ".md")


def read(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


class SeatFilesTest(unittest.TestCase):
    """The seat text itself. It is prompt, so its shape is part of the contract: a file with
    frontmatter would be read as an agent definition, and a file the length of a manual would
    cost its tokens on every launch of the seat."""

    def test_both_seat_files_exist(self):
        for name in SEAT_NAMES:
            self.assertTrue(os.path.isfile(seat_file(name)), "missing seat file: " + seat_file(name))

    def test_each_seat_names_its_laws_in_the_words_the_kit_uses(self):
        for name in SEAT_NAMES:
            text = read(seat_file(name)).lower()
            for phrase in ("resume brief", "close", "never the seat"):
                self.assertIn(phrase, text, "%s says nothing about %r" % (name, phrase))

    def test_no_seat_file_carries_frontmatter(self):
        """An agent definition opens with `---`. A seat is not delegable and must never be
        mistaken for one, so it opens with its heading."""
        for name in SEAT_NAMES:
            lines = read(seat_file(name)).splitlines()
            self.assertNotEqual(lines[0].strip(), "---", name + " opens with frontmatter")
            self.assertTrue(lines[0].startswith("# "), name + " does not open with a heading")

    def test_each_seat_stays_under_sixty_lines(self):
        for name in SEAT_NAMES:
            lines = read(seat_file(name)).splitlines()
            self.assertLess(len(lines), 60, "%s is %d lines" % (name, len(lines)))


RECOVER = os.path.join(HOOKS, "compact-recover.py")
SEAT_VARS = ("CLAUDE_ROLE", "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_DISABLE_1M_CONTEXT")
LOUD = ("Not launched through the account launcher: no seat, no window, no --no-chrome. "
        "Relaunch through it before working.")
ROW = "- 2026-09-07 09:00 [process] a ruling row (source)"


class SeatBlockTest(unittest.TestCase):
    """The block compact-recover.py prints before anything else, at a session start and after a
    compaction: which chair this pane holds, whether it was launched through the launcher at all,
    and which resume brief that chair reads first."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="seat-block-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.briefs = os.path.join(self.tmp, "briefs")
        self.register = os.path.join(self.tmp, "rulings.md")
        with open(self.register, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# Rulings register\n\n" + ROW + "\n")

    def env(self, **extra):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        for name in SEAT_VARS + ("CLAUDE_BRIEFS_DIR", "CLAUDE_LANDINGS_FILE"):
            env.pop(name, None)
        env["CLAUDE_RULINGS_FILE"] = self.register
        env["CLAUDE_CHECKPOINT_DIR"] = os.path.join(self.tmp, "ckpt")
        # A renderer that is not a file, so no run of this suite renders the sheet a live
        # session reads, and a sheet path nobody writes.
        env["CLAUDE_LANE_STATE_SCRIPT"] = os.path.join(self.tmp, "no-lane-state.py")
        env["CLAUDE_LANE_STATE_SHEET"] = os.path.join(self.tmp, "law.md")
        for name, value in extra.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        return env

    def run_hook(self, env, payload=None, args=("--rulings",)):
        data = {"session_id": "zz", "cwd": self.tmp, "hook_event_name": "SessionStart"}
        data.update(payload or {})
        process = subprocess.run(
            [sys.executable, RECOVER] + list(args),
            input=json.dumps(data).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", "replace"))
        return process.stdout.decode("utf-8", "replace")

    def brief(self, name):
        os.makedirs(self.briefs, exist_ok=True)
        path = os.path.join(self.briefs, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# brief\n")
        return path

    # --- line 1, the chair ------------------------------------------------

    def test_the_first_line_names_the_seat_of_the_role(self):
        for role, expected in (("orchestrator", "Seat: orchestrator"),
                               ("analyst", "Seat: analyst"),
                               ("lane", "Seat: none (lane)")):
            out = self.run_hook(self.env(CLAUDE_ROLE=role))
            self.assertEqual(out.splitlines()[0], expected, role)

    def test_a_pane_with_no_role_is_a_lane_and_says_so(self):
        out = self.run_hook(self.env(CLAUDE_CODE_DISABLE_1M_CONTEXT="1"))
        self.assertEqual(out.splitlines()[0], "Seat: none (lane)")

    # --- line 2, the pane nobody launched ---------------------------------

    def test_the_loud_line_prints_only_when_every_launcher_variable_is_absent(self):
        out = self.run_hook(self.env())
        self.assertEqual(out.splitlines()[1], LOUD)
        for name in SEAT_VARS:
            out = self.run_hook(self.env(**{name: "orchestrator" if name == "CLAUDE_ROLE" else "1"}))
            self.assertNotIn(LOUD, out, name + " is set, so the pane was launched")

    # --- the resume brief -------------------------------------------------

    def test_the_newest_brief_of_that_seat_is_named_by_name_not_by_date_on_disk(self):
        """The briefs directory is shared, so the seat picks its own file, and it picks the
        newest by name: a brief edited yesterday is still tomorrow's brief."""
        self.brief("analyst-resume-2026-09-05.md")
        newest = self.brief("analyst-resume-2026-09-07.md")
        self.brief("analyst-resume-2026-09-06.md")
        self.brief("orchestrator-resume-2026-09-08.md")
        out = self.run_hook(self.env(CLAUDE_ROLE="analyst", CLAUDE_BRIEFS_DIR=self.briefs))
        self.assertIn("Resume brief: %s (read it first)" % newest, out)

    def test_a_briefs_directory_with_no_brief_of_that_seat_says_so(self):
        os.makedirs(self.briefs, exist_ok=True)
        out = self.run_hook(self.env(CLAUDE_ROLE="orchestrator", CLAUDE_BRIEFS_DIR=self.briefs))
        self.assertIn("Resume brief: none found in %s" % self.briefs, out)

    def test_an_absent_briefs_directory_prints_no_brief_line_at_all(self):
        """A project without a briefs directory is not a fault, so nothing is said about it."""
        gone = os.path.join(self.tmp, "gone")
        out = self.run_hook(self.env(CLAUDE_ROLE="orchestrator", CLAUDE_BRIEFS_DIR=gone))
        self.assertNotIn("Resume brief", out)
        self.assertEqual(out.splitlines()[0], "Seat: orchestrator")

    def test_a_lane_is_told_no_brief_because_a_lane_has_no_seat(self):
        self.brief("orchestrator-resume-2026-09-07.md")
        out = self.run_hook(self.env(CLAUDE_ROLE="lane", CLAUDE_BRIEFS_DIR=self.briefs))
        self.assertNotIn("Resume brief", out)

    # --- the subagent exclusion -------------------------------------------

    def test_a_subagent_payload_prints_no_seat_block(self):
        """A subagent of a seated session inherits the environment, so the variable is there and
        the payload is what tells them apart. An agent holds no chair."""
        for field in ("agent_id", "agent_type"):
            out = self.run_hook(self.env(CLAUDE_ROLE="orchestrator"), payload={field: "abc123"})
            self.assertNotIn("Seat:", out, field)
            self.assertNotIn(LOUD, out, field)
            self.assertTrue(out.startswith("Rulings (last"), out[:60])

    # --- where the block sits ---------------------------------------------

    def test_the_rulings_output_still_ends_with_the_rulings_block(self):
        out = self.run_hook(self.env(CLAUDE_ROLE="orchestrator"))
        self.assertEqual(out.splitlines()[0], "Seat: orchestrator")
        self.assertIn("Rulings (last", out)
        self.assertEqual(out.splitlines()[-1], ROW)

    def test_the_compact_block_comes_before_the_checkpoint_line(self):
        out = self.run_hook(self.env(CLAUDE_ROLE="orchestrator"), args=())
        self.assertEqual(out.splitlines()[0], "Seat: orchestrator")
        self.assertLess(out.index("Seat: orchestrator"), out.index("[compaction recovery"))


PROMPT_LOG = os.path.join(HOOKS, "prompt-log.py")
NOTICE = ("Closing round: the day ends at 22:00. Write the resume brief, land or stop every "
          "agent, nothing running at the hour.")


class ClosingNoticeTest(unittest.TestCase):
    """The one line prompt-log.py adds to a seated session once the closing round has opened.

    The hook writes the prompt log on every prompt and prints nothing the rest of the day, so
    the notice is the only thing it ever costs the window, and it costs it only in the chair
    that has a day to close.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="closing-notice-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.checkpoints = os.path.join(self.tmp, "ckpt")
        self.log = os.path.join(self.checkpoints, "abcdef12-prompts.md")

    def run_hook(self, role="orchestrator", now="21:30", hour="22:00", **payload):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["CLAUDE_CHECKPOINT_DIR"] = self.checkpoints
        for name in ("CLAUDE_ROLE", "CLAUDE_CLOSING_HOUR", "CLAUDE_TEST_NOW"):
            env.pop(name, None)
        for name, value in (("CLAUDE_ROLE", role), ("CLAUDE_CLOSING_HOUR", hour),
                            ("CLAUDE_TEST_NOW", now)):
            if value is not None:
                env[name] = value
        data = {"session_id": "abcdef12-3456-7890-abcd-ef1234567890",
                "cwd": self.tmp, "prompt": "carry on with the lane"}
        data.update(payload)
        process = subprocess.run(
            [sys.executable, PROMPT_LOG],
            input=json.dumps(data).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", "replace"))
        return process.stdout.decode("utf-8", "replace")

    def test_without_the_closing_hour_the_hook_prints_nothing(self):
        self.assertEqual(self.run_hook(hour=None), "")

    def test_a_minute_before_the_round_opens_prints_nothing(self):
        """The round opens 45 minutes before the hour, so 21:14 is still the working day."""
        self.assertEqual(self.run_hook(now="21:14"), "")

    def test_the_round_opens_forty_five_minutes_before_the_hour(self):
        self.assertEqual(self.run_hook(now="21:15"), NOTICE)

    def test_after_the_hour_the_notice_is_still_printed(self):
        self.assertEqual(self.run_hook(now="22:30"), NOTICE)

    def test_a_lane_has_no_day_to_close_and_is_told_nothing(self):
        self.assertEqual(self.run_hook(role="lane"), "")

    def test_a_subagent_of_a_seated_session_is_told_nothing(self):
        for field in ("agent_id", "agent_type"):
            self.assertEqual(self.run_hook(**{field: "abc123"}), "", field)

    def test_the_prompt_is_still_logged_while_the_notice_prints(self):
        """The notice is added to what the hook does, not put in its place."""
        self.assertEqual(self.run_hook(), NOTICE)
        self.assertIn("carry on with the lane", read(self.log))


if __name__ == "__main__":
    unittest.main(verbosity=2)
