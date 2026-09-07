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


if __name__ == "__main__":
    unittest.main(verbosity=2)
