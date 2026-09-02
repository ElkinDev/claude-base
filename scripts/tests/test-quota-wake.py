#!/usr/bin/env python3
"""Tests for quota-wake.py: the states, the wait on the announced reset, the one wake per reset
per pane, the cap that refuses, and the process discipline.

Nothing here calls the usage endpoint or a model. The probe is a callable that answers a queue
of readings and records who asked; Herdr is a fake `subprocess.run` that answers `agent list`
and `tab list` and records every prompt it was asked to submit. The clock is passed in, so a
two hour wait costs no time.

Run:
    python test-quota-wake.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
NOW = 1772582400.0  # 2026-03-04, a Wednesday, at 00:00 UTC
SENTENCE = "resume from your brief; measure before launching anything"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wake = load("quota_wake", "quota-wake.py")


def iso(when):
    return datetime.fromtimestamp(when, timezone.utc).isoformat()


def reading(five=10, seven=20, resets_at="", error="", account="acct-a"):
    return {"account": account, "five_hour": five, "seven_day": seven, "resets_at": resets_at,
            "seven_day_resets_at": "", "meters": {}, "limits": {}, "error": error}


class FakeProbe:
    """Answers a queue of readings, then repeats the last one, and remembers every call."""

    def __init__(self, *readings):
        self.queue = list(readings) or [reading()]
        self.calls = []

    def __call__(self, account):
        self.calls.append(account)
        return self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]


def agent(pane, session, status="idle", tab="t1", name="orchestrator", title=""):
    """One `herdr agent list` entry. `name` is Herdr's own name for the pane, `title` the terminal
    title Claude Code writes, which is a summary of the current task and not a name at all."""
    return {"agent": "claude", "agent_session": {"kind": "id", "value": session}, "agent_status": status,
            "cwd": "/work", "pane_id": pane, "state_change_seq": 4, "name": name, "tab_id": tab,
            "terminal_title_stripped": title}


class FakeHerdr:
    """The Herdr CLI as a recorder: `agent list`, `tab list`, and every `agent prompt` submitted."""

    def __init__(self, agents, tabs=None):
        self.agents = agents
        self.tabs = tabs if tabs is not None else [{"tab_id": "t1", "label": "cc-acct-a-orchestrator"}]
        self.prompts = []

    def run(self, cmd, **_):
        verb = " ".join(cmd[1:3])
        if verb == "agent list":
            out = json.dumps({"result": {"agents": self.agents, "type": "agent_list"}})
        elif verb == "tab list":
            out = json.dumps({"result": {"tabs": self.tabs}})
        elif verb == "agent prompt":
            self.prompts.append((cmd[3], cmd[4]))
            out = "submitted"
        else:
            out = "{}"
        return types.SimpleNamespace(stdout=out, stderr="", returncode=0)


def no_herdr(cmd, **_):
    """Herdr not installed: what `subprocess.run` raises when the binary is not on PATH."""
    raise FileNotFoundError(2, "The system cannot find the file specified", cmd[0])


def make_args(**over):
    base = dict(panes="", sessions="", titles="", account="acct-a", accounts_dir="", herdr="herdr",
                dry_run=False, prompt_file="", interval=300, once=True, status=False, stop=False)
    base.update(over)
    return types.SimpleNamespace(**base)


def make_cfg(**over):
    base = dict(cap=80, grace=120, resume_below=50, retry_for=1800)
    base.update(over)
    return base


class WakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="quota-wake-")
        self.saved = (wake.LOG_FILE, wake.STATE_FILE, wake.LOCK_FILE, wake.STOP_FILE, subprocess.run)
        wake.LOG_FILE = os.path.join(self.tmp, "quota-wake.log")
        wake.STATE_FILE = os.path.join(self.tmp, "quota-wake.state.json")
        wake.LOCK_FILE = os.path.join(self.tmp, "quota-wake.lock")
        wake.STOP_FILE = os.path.join(self.tmp, "quota-wake.stop")
        self.herdr = FakeHerdr([agent("w1:p1", "aaaaaaaa-1111-2222-3333-444444444444")])
        subprocess.run = self.herdr.run
        self.store = wake.new_store()

    def tearDown(self):
        wake.LOG_FILE, wake.STATE_FILE, wake.LOCK_FILE, wake.STOP_FILE, subprocess.run = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log_text(self):
        if not os.path.isfile(wake.LOG_FILE):
            return ""
        with open(wake.LOG_FILE, encoding="utf-8") as handle:
            return handle.read()

    def run_pass(self, probe, when, args=None, cfg=None, store=None):
        rows, _ = wake.one_pass(args or make_args(), cfg or make_cfg(), store or self.store, probe, when, quiet=True)
        return rows

    def stub_probe(self, *readings):
        """The usage endpoint replaced by a queue of readings, so main() reads no network and no
        real profile directory. Returns the probe, which records every account it was asked for."""
        probe = FakeProbe(*readings)
        saved = (wake.meters.read_snapshot, wake.meters.account_name)
        wake.meters.read_snapshot = lambda config_dir=None, timeout=20: probe("acct-a")
        wake.meters.account_name = lambda config_dir=None: "acct-a"

        def restore():
            wake.meters.read_snapshot, wake.meters.account_name = saved

        self.addCleanup(restore)
        return probe

    def run_main(self, *argv):
        """main() driven by its own command line: (exit code, everything it printed)."""
        console, saved = io.StringIO(), sys.argv
        sys.argv = ["quota-wake.py"] + list(argv)
        try:
            with contextlib.redirect_stdout(console):
                code = wake.main()
        finally:
            sys.argv = saved
        return code, console.getvalue()

    def wake_cycle(self, args=None, due=NOW + 60, seven=61):
        """One dry pass and one recovered pass, and the panes woken by the pair."""
        probe = FakeProbe(reading(five=100, seven=seven, resets_at=iso(due)), reading(five=5, seven=62))
        self.run_pass(probe, NOW, args=args)
        self.run_pass(probe, due + 120, args=args)
        return [pane for pane, _ in self.herdr.prompts]

    # ------------------------------------------------------------ states

    def test_classify_reads_the_two_meters_and_the_cap(self):
        self.assertEqual(wake.classify(reading(five=40, seven=30), 80), "ok")
        self.assertEqual(wake.classify(reading(five=100, seven=30), 80), "dry")
        self.assertEqual(wake.classify(reading(five=100, seven=85), 80), "capped")
        self.assertEqual(wake.classify(reading(five=100, seven=85), 100), "dry")
        self.assertEqual(wake.classify(reading(five=None, error="down"), 80), "unknown")

    def test_classify_reads_a_number_however_the_payload_spells_it(self):
        """The endpoint owns the shape of the utilization field. A number that arrives as the string
        spelling it still reads; anything that is not a number reads as unknown and never raises."""
        for value, expected in ((100, "dry"), ("100", "dry"), (100.0, "dry"), ("40", "ok"), (99.5, "ok"),
                                (None, "unknown"), ("n/a", "unknown"), ({}, "unknown"), (True, "unknown")):
            with self.subTest(five_hour=value):
                self.assertEqual(wake.classify(reading(five=value, seven=30), 80), expected)
        self.assertEqual(wake.classify(reading(five=100, seven="85"), 80), "capped")

    def test_a_probe_that_raises_is_read_as_unknown_and_the_pass_survives(self):
        """The loop's whole value is surviving unattended, so an exception from the reader is a
        reading of unknown, not the end of the night."""
        def angry(account):
            raise RuntimeError("the reader changed under the loop")

        self.run_pass(angry, NOW)
        self.assertEqual(self.store["accounts"]["acct-a"]["state"], "unknown")
        self.assertEqual(self.store["accounts"]["acct-a"]["unknown_streak"], 1)
        self.assertIn("probe raised", self.log_text())
        self.assertEqual(self.herdr.prompts, [])

    def test_a_string_utilization_runs_the_whole_cycle(self):
        """The coercion has to reach the state machine and the resume text, not only classify."""
        due = NOW + 60
        probe = FakeProbe(reading(five="100", seven="61", resets_at=iso(due)), reading(five="5", seven="62"))
        self.run_pass(probe, NOW)
        self.run_pass(probe, due + 120)
        self.assertEqual([pane for pane, _ in self.herdr.prompts], ["w1:p1"])
        self.assertIn("5 percent", self.herdr.prompts[0][1])

    def test_ok_never_wakes(self):
        probe = FakeProbe(reading(five=40, seven=30))
        self.run_pass(probe, NOW)
        self.assertEqual(self.herdr.prompts, [])
        self.assertEqual(self.store["accounts"]["acct-a"]["state"], "ok")

    def test_capped_never_wakes_and_says_so_every_pass(self):
        probe = FakeProbe(reading(five=100, seven=85, resets_at=iso(NOW - 60)))
        for step in (0, 300, 3600):
            self.run_pass(probe, NOW + step)
        self.assertEqual(self.herdr.prompts, [])
        self.assertEqual(self.store["accounts"]["acct-a"]["state"], "capped")
        self.assertEqual(self.log_text().count("capped"), 3)

    def test_dry_waits_for_the_announced_time_plus_grace_then_wakes_once(self):
        due = NOW + 600
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)), reading(five=5, seven=62))
        self.run_pass(probe, NOW)
        self.assertEqual(self.store["accounts"]["acct-a"]["state"], "dry")
        self.assertEqual(self.store["accounts"]["acct-a"]["due_at"], due + 120)
        self.run_pass(probe, NOW + 300)
        self.assertEqual(len(probe.calls), 1, "the account is not probed again before the reset")
        self.assertEqual(self.herdr.prompts, [])
        self.run_pass(probe, due + 120)
        self.assertEqual(len(self.herdr.prompts), 1)
        self.assertEqual(self.herdr.prompts[0][0], "w1:p1")
        self.run_pass(probe, due + 420)
        self.assertEqual(len(self.herdr.prompts), 1, "the same reset never wakes the pane twice")

    def test_a_restart_inside_the_same_window_does_not_wake_again(self):
        due = NOW + 600
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)), reading(five=5, seven=62))
        self.run_pass(probe, NOW)
        self.run_pass(probe, due + 120)
        self.assertEqual(len(self.herdr.prompts), 1)
        wake.save_state(self.store)
        restarted = wake.load_state()
        again = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)), reading(five=5, seven=62))
        self.run_pass(again, due + 600, store=restarted)
        self.run_pass(again, due + 1200, store=restarted)
        self.assertEqual(len(self.herdr.prompts), 1)
        self.assertIn("already woken", self.log_text())

    def test_the_meter_still_at_the_ceiling_retries_then_gives_up(self):
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(NOW - 3600)))
        self.run_pass(probe, NOW)
        self.assertEqual(self.store["accounts"]["acct-a"]["due_at"], NOW, "a reset in the past is due now")
        self.run_pass(probe, NOW + 300)
        self.assertIn("still dry", self.log_text())
        self.assertEqual(self.herdr.prompts, [])
        self.run_pass(probe, NOW + 1801 + 300)
        self.assertIn("retry budget", self.log_text())
        self.assertEqual(self.store["accounts"]["acct-a"]["state"], "dry")
        self.assertEqual(self.store["accounts"]["acct-a"].get("due_at", 0), 0, "the wait is over")
        self.assertEqual(self.herdr.prompts, [])

    def test_a_meter_back_but_above_resume_below_does_not_wake(self):
        due = NOW + 60
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)), reading(five=70, seven=62))
        self.run_pass(probe, NOW)
        self.run_pass(probe, due + 120)
        self.assertEqual(self.herdr.prompts, [])
        self.assertIn("not below", self.log_text())

    def test_an_endpoint_error_keeps_the_previous_state_and_warns_once(self):
        probe = FakeProbe(reading(five=40, seven=30), reading(five=None, seven=None, error="request failed: down"))
        self.run_pass(probe, NOW)
        for step in (1, 2, 3, 4):
            self.run_pass(probe, NOW + 300 * step)
        self.assertEqual(self.store["accounts"]["acct-a"]["state"], "ok")
        self.assertEqual(self.herdr.prompts, [])
        self.assertEqual(self.log_text().count("in a row"), 1)

    # ------------------------------------------------------------ panes

    def test_a_working_pane_is_skipped(self):
        self.herdr.agents = [agent("w1:p1", "aaaaaaaa-1111-2222-3333-444444444444", status="working")]
        due = NOW + 60
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)), reading(five=5, seven=62))
        self.run_pass(probe, NOW)
        self.run_pass(probe, due + 120)
        self.assertEqual(self.herdr.prompts, [])
        self.assertIn("working", self.log_text())

    def test_every_waiting_state_is_a_candidate(self):
        for status in ("idle", "done", "stalled"):
            with self.subTest(status=status):
                self.setUp()
                self.herdr.agents = [agent("w1:p1", "aaaaaaaa-1111-2222-3333-444444444444", status=status)]
                due = NOW + 60
                probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)), reading(five=5, seven=62))
                self.run_pass(probe, NOW)
                self.run_pass(probe, due + 120)
                self.assertEqual(len(self.herdr.prompts), 1)

    def test_only_the_orchestrator_pane_is_a_candidate_by_default(self):
        self.herdr.agents = [agent("w1:p1", "1" * 36, name="orchestrator"), agent("w1:p2", "2" * 36, name="lane")]
        due = NOW + 60
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)), reading(five=5, seven=62))
        self.run_pass(probe, NOW)
        self.run_pass(probe, due + 120)
        self.assertEqual([pane for pane, _ in self.herdr.prompts], ["w1:p1"])

    def test_a_pane_with_only_a_terminal_title_is_found_through_its_tab(self):
        """Review case (d): the lane carries a Herdr name, the orchestrator carries only the title
        Claude Code wrote for it, and the tab label is what is left to decide."""
        self.herdr.agents = [agent("w1:p1", "1" * 36, name="", title="Reviewing the merge queue"),
                             agent("w1:p2", "2" * 36, name="lane", title="Google sign-in first tap fix")]
        self.assertEqual(self.wake_cycle(), ["w1:p1"])
        self.assertNotIn("ambiguous", self.log_text())

    def test_a_terminal_title_alone_never_makes_a_pane_the_orchestrator(self):
        """The title changes with whatever the pane is doing, so it answers for nothing: a pane on
        a lane tab stays a lane pane however its current task reads."""
        self.herdr.tabs = [{"tab_id": "t1", "label": "cc-acct-a-lane"}]
        self.herdr.agents = [agent("w1:p1", "1" * 36, name="", title="orchestrator handoff notes")]
        self.assertEqual(self.wake_cycle(), [])
        self.assertIn("no pane matches", self.log_text())

    def test_unnamed_panes_under_a_role_tab_wake_the_lowest_and_name_the_rest(self):
        """Review cases (b) and (c): with no pane name at all, or with terminal titles only, the tab
        label speaks for every pane under it, and nothing in the payload tells them apart."""
        for case, title in (("b", ""), ("c", "Reviewing the merge queue")):
            with self.subTest(case=case):
                self.setUp()
                self.herdr.agents = [agent("w1:p2", "2" * 36, name="", title=title),
                                     agent("w1:p1", "1" * 36, name="", title=title)]
                self.assertEqual(self.wake_cycle(), ["w1:p1"])
                self.assertIn("w1:p2 skipped, ambiguous", self.log_text())

    def test_a_selector_replaces_the_default_and_takes_every_pane_it_names(self):
        self.herdr.agents = [agent("w1:p1", "1" * 36, name="orchestrator"), agent("w1:p2", "2" * 36, name="lane")]
        due = NOW + 60
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)), reading(five=5, seven=62))
        args = make_args(panes="w1:p1,w1:p2")
        self.run_pass(probe, NOW, args=args)
        self.run_pass(probe, due + 120, args=args)
        self.assertEqual(sorted(pane for pane, _ in self.herdr.prompts), ["w1:p1", "w1:p2"])

    def test_a_pane_of_another_account_is_left_alone(self):
        self.herdr.agents = [agent("w1:p1", "1" * 36, tab="t1"), agent("w2:p1", "2" * 36, tab="t2")]
        self.herdr.tabs = [{"tab_id": "t1", "label": "cc-acct-a-orchestrator"}, {"tab_id": "t2", "label": "cc-acct-b-orchestrator"}]
        due = NOW + 60
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)), reading(five=5, seven=62))
        self.run_pass(probe, NOW)
        self.run_pass(probe, due + 120)
        self.assertEqual([pane for pane, _ in self.herdr.prompts], ["w1:p1"])

    def test_the_account_of_a_pane_comes_from_the_tab_label(self):
        self.assertEqual(wake.pane_account({"tab_label": "cc-acct-a-orchestrator", "name": ""}, "fallback"), "acct-a")
        self.assertEqual(wake.pane_account({"tab_label": "cc-acct-b", "name": ""}, "fallback"), "acct-b")
        self.assertEqual(wake.pane_account({"tab_label": "", "name": "orchestrator"}, "fallback"), "fallback")

    # ------------------------------------------------------------ the text and the process

    def test_the_resume_text_carries_the_facts_the_pane_cannot_know(self):
        due = NOW + 60
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)), reading(five=5, seven=62))
        self.run_pass(probe, NOW)
        self.run_pass(probe, due + 120)
        text = self.herdr.prompts[0][1]
        for fragment in ("acct-a", "100", "5 percent", "62 percent", "80 percent", SENTENCE):
            self.assertIn(fragment, text)
        self.assertIn(datetime.fromtimestamp(due + 120).strftime("%Y-%m-%d %H:%M"), text)
        self.assertNotIn("--", text)

    def test_a_prompt_file_replaces_the_text(self):
        path = os.path.join(self.tmp, "brief.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("read the brief for {account}, the window is at {after} percent")
        due = NOW + 60
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)), reading(five=5, seven=62))
        args = make_args(prompt_file=path)
        self.run_pass(probe, NOW, args=args)
        self.run_pass(probe, due + 120, args=args)
        self.assertEqual(self.herdr.prompts[0][1], "read the brief for acct-a, the window is at 5 percent")

    def test_a_prompt_file_that_cannot_be_read_falls_back_and_says_so(self):
        """An unreadable brief is a note in the log, never a skipped wake: the account earned it."""
        args = make_args(prompt_file=os.path.join(self.tmp, "gone", "brief.txt"))
        self.assertEqual(self.wake_cycle(args=args), ["w1:p1"])
        self.assertIn(SENTENCE, self.herdr.prompts[0][1])
        self.assertIn("--prompt-file not read", self.log_text())

    def test_dry_run_writes_only_the_log(self):
        due = NOW + 60
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)), reading(five=5, seven=62))
        args = make_args(dry_run=True)
        self.run_pass(probe, NOW, args=args)
        self.run_pass(probe, due + 120, args=args)
        self.assertEqual(self.herdr.prompts, [])
        self.assertIn("would wake", self.log_text())
        wake.save_state(self.store, dry_run=True)
        self.assertFalse(os.path.exists(wake.STATE_FILE))

    def test_the_state_file_survives_a_restart_and_prunes_old_wakes(self):
        self.store["wakes"] = {f"session-{index}|reset": {"at": NOW + index} for index in range(wake.WAKE_HISTORY + 40)}
        wake.save_state(self.store)
        restored = wake.load_state()
        self.assertEqual(len(restored["wakes"]), wake.WAKE_HISTORY)
        self.assertIn(f"session-{wake.WAKE_HISTORY + 39}|reset", restored["wakes"])

    def test_the_lock_refuses_a_second_instance(self):
        self.assertEqual(wake.take_lock(), 0)
        with open(wake.LOCK_FILE, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid() if False else 1))
        self.assertEqual(wake.take_lock(), 0, "a dead pid never blocks a start")
        with open(wake.LOCK_FILE, "w", encoding="utf-8") as handle:
            handle.write(str(os.getppid()))
        self.assertEqual(wake.take_lock(), os.getppid())
        wake.release_lock()
        self.assertFalse(os.path.exists(wake.LOCK_FILE))

    def test_stop_is_a_file_and_is_consumed_once(self):
        with open(wake.STOP_FILE, "w", encoding="utf-8") as handle:
            handle.write("now")
        self.assertTrue(wake.stop_requested())
        self.assertFalse(wake.stop_requested())

    def test_status_prints_a_row_per_account_and_never_submits(self):
        due = NOW + 600
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(due)))
        rows = self.run_pass(probe, NOW, args=make_args(status=True, dry_run=True))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["account"], "acct-a")
        self.assertEqual((row["five_hour"], row["seven_day"], row["state"]), (100, 61, "dry"))
        self.assertEqual(row["panes"], ["w1:p1"])
        self.assertEqual(self.herdr.prompts, [])

    def test_no_herdr_socket_is_survived_and_said_in_words(self):
        subprocess.run = no_herdr
        probe = FakeProbe(reading(five=100, seven=61, resets_at=iso(NOW - 60)))
        self.run_pass(probe, NOW)
        self.run_pass(probe, NOW + 300)
        self.assertIn("herdr agent list failed", self.log_text())

    def test_once_answers_two_when_no_pane_was_reachable_and_zero_when_one_was(self):
        """The exit code is the whole report a scheduler reads. A pass that read the meters but
        found nothing to wake is not a success, and the two cases have to be told apart."""
        self.stub_probe(reading(five=10, seven=20))
        subprocess.run = no_herdr
        self.assertEqual(self.run_main("--once", "--account", "acct-a")[0], 2)
        subprocess.run = self.herdr.run
        self.assertEqual(self.run_main("--once", "--account", "acct-a")[0], 0)

    def test_status_says_on_the_console_why_the_panes_column_is_empty(self):
        """--status is read by a person at a console. The reason no pane can be woken belongs
        there, not only in a log file that person never opened."""
        self.stub_probe(reading(five=10, seven=20))
        subprocess.run = no_herdr
        code, console = self.run_main("--status", "--account", "acct-a")
        self.assertEqual(code, 0)
        self.assertIn("herdr agent list failed", console)
        self.assertIn("no pane can be woken", console)
        self.assertIn("acct-a", console, "the meters are still read and still reported")


if __name__ == "__main__":
    unittest.main(verbosity=2)
