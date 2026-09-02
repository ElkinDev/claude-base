#!/usr/bin/env python3
"""Tests for the decision the watcher takes about one session, compact-at-boundary.py decide().

decide() is pure: it reads a state dictionary, the Herdr status, the context number and the
position that number came from, and returns hold, wait, fire or skip. So these tests need no
transcript, no Herdr and no clock. The ones that build synthetic transcripts and a fake Herdr,
and the tests for compaction-report.py, are in test-compaction-tools.py beside this file.

Run:
    python test-compaction-decide.py
"""
import importlib.util
import os
import unittest


def load(name, filename):
    scripts = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(name, os.path.join(scripts, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watcher = load("compact_at_boundary", "compact-at-boundary.py")

CFG = {"window": 200000, "threshold": 0.65, "idle": 90, "cooldown": 900}


def mark(offset=0, kind="usage"):
    return watcher.Mark(offset, kind)


class DecideTest(unittest.TestCase):
    def test_no_usage_is_skipped(self):
        self.assertEqual(watcher.decide({}, "idle", 1, None, None, 1000.0, CFG)[0], "skip")

    def test_below_threshold_holds(self):
        action, reason = watcher.decide({}, "idle", 1, 100000, mark(1), 1000.0, CFG)
        self.assertEqual(action, "hold")
        self.assertIn("below", reason)

    def test_working_session_holds_and_resets_idle(self):
        state = {"idle_since": 500.0, "idle_seq": 1}
        action, _ = watcher.decide(state, "working", 2, 150000, mark(1), 1000.0, CFG)
        self.assertEqual(action, "hold")
        self.assertIsNone(state["idle_since"])

    def test_idle_long_enough_fires_once_then_cools_down(self):
        state = {}
        self.assertEqual(watcher.decide(state, "idle", 7, 150000, mark(1), 1000.0, CFG)[0], "wait")
        self.assertEqual(watcher.decide(state, "idle", 7, 150000, mark(1), 1050.0, CFG)[0], "wait")
        action, reason = watcher.decide(state, "idle", 7, 150000, mark(1), 1091.0, CFG)
        self.assertEqual(action, "fire")
        self.assertIn("150000 tokens", reason)
        state["last_fire"] = 1091.0
        # the session answered, so the transcript moved on: the cooldown is what holds it now
        action, reason = watcher.decide(state, "idle", 7, 150000, mark(2), 1200.0, CFG)
        self.assertEqual(action, "hold")
        self.assertIn("cooldown", reason)

    def test_state_change_restarts_the_idle_clock(self):
        state = {}
        watcher.decide(state, "idle", 1, 150000, mark(1), 1000.0, CFG)
        action, _ = watcher.decide(state, "idle", 2, 150000, mark(1), 1100.0, CFG)
        self.assertEqual(action, "wait")
        self.assertEqual(state["idle_since"], 1100.0)

    def test_observed_compaction_is_recorded_and_respected(self):
        state = {}
        watcher.decide(state, "working", 1, 160000, mark(1), 1000.0, CFG)
        watcher.decide(state, "working", 2, 70000, mark(2), 1030.0, CFG)
        self.assertEqual(state["last_compaction"], 1030.0)
        watcher.decide(state, "idle", 3, 140000, mark(3), 1100.0, CFG)
        action, reason = watcher.decide(state, "idle", 3, 140000, mark(3), 1300.0, CFG)
        self.assertEqual(action, "hold")
        self.assertIn("compacted", reason)
        action, _ = watcher.decide(state, "idle", 3, 140000, mark(4), 2000.0, CFG)
        self.assertEqual(action, "fire")

    def test_a_position_that_has_not_moved_since_the_fire_holds(self):
        """The 2026-09-02 case: five submissions at one number. A session with no new
        assistant turn since its last /compact is by definition not compactable."""
        state = {}
        watcher.decide(state, "idle", 7, 151579, mark(100), 1000.0, CFG)
        action, _ = watcher.decide(state, "idle", 7, 151579, mark(100), 1100.0, CFG)
        self.assertEqual(action, "fire")
        for now in (1200.0, 2100.0, 3000.0, 3900.0):
            action, reason = watcher.decide(state, "idle", 7, 151579, mark(100), now, CFG)
            self.assertEqual(action, "hold", now)
            self.assertEqual(reason, "no turn since the last /compact")
        # only a new assistant turn clears it
        action, _ = watcher.decide(state, "idle", 7, 151579, mark(200), 3950.0, CFG)
        self.assertEqual(action, "fire")

    def test_a_boundary_newer_than_the_last_turn_holds_at_the_floor(self):
        state = {}
        action, _ = watcher.decide(state, "idle", 7, 151579, mark(100), 1000.0, CFG)
        self.assertEqual(action, "wait")  # the idle clock has just started
        action, _ = watcher.decide(state, "idle", 7, 151579, mark(100), 1100.0, CFG)
        self.assertEqual(action, "fire")
        action, reason = watcher.decide(state, "idle", 7, 19259, mark(300, "boundary"), 1300.0, CFG)
        self.assertEqual(action, "hold")
        self.assertIn("19259", reason)
        self.assertIn("no turn since", reason)
        self.assertEqual(state["last_compaction"], 1300.0)

    def test_the_wait_between_two_submissions_is_always_the_plain_cooldown(self):
        """Positions that go nowhere are held, never resubmitted, so the cooldown is only ever
        the wait between two submissions to a session that did move on: 900 seconds, every
        time, however many held passes came before."""
        state = {}
        watcher.decide(state, "idle", 7, 151579, mark(100), 1000.0, CFG)
        action, _ = watcher.decide(state, "idle", 7, 151579, mark(100), 1100.0, CFG)
        self.assertEqual(action, "fire")
        for now in (1200.0, 2100.0, 3100.0, 4100.0):  # four passes, nothing new in the transcript
            self.assertEqual(watcher.decide(state, "idle", 7, 151579, mark(100), now, CFG)[0], "hold")
        action, _ = watcher.decide(state, "idle", 7, 151579, mark(200), 4200.0, CFG)
        self.assertEqual(action, "fire")  # a turn at last, and the cooldown is long spent
        action, reason = watcher.decide(state, "idle", 7, 151579, mark(300), 4400.0, CFG)
        self.assertEqual(action, "hold")
        self.assertEqual(reason, "76%, cooldown 700s left")  # 900 - 200, not a multiple of it
        action, _ = watcher.decide(state, "idle", 7, 151579, mark(300), 5100.0, CFG)
        self.assertEqual(action, "fire")  # exactly one cooldown after the previous submission


if __name__ == "__main__":
    unittest.main(verbosity=2)
