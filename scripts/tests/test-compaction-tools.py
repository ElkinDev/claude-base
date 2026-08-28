#!/usr/bin/env python3
"""Tests for compact-at-boundary.py (decision logic, transcript tail, Herdr parsing) and
compaction-report.py (boundaries, cycles, cache breaks, recommendation).

Everything runs on synthetic transcripts in a temp folder; the Herdr CLI is replaced by a
fake subprocess result. Nothing runs a model, nothing submits a prompt anywhere.

Run:
    python test-compaction-tools.py
"""
import importlib.util
import json
import os
import shutil
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watcher = load("compact_at_boundary", "compact-at-boundary.py")
report = load("compaction_report", "compaction-report.py")

CFG = {"window": 200000, "threshold": 0.65, "idle": 90, "cooldown": 900}
HERDR_LIST = {
    "id": "cli:agent:list",
    "result": {
        "agents": [
            {"agent": "claude", "agent_session": {"kind": "id", "value": "aaaaaaaa-1111-2222-3333-444444444444"}, "agent_status": "idle", "cwd": "C:\\work", "pane_id": "w3:p1", "state_change_seq": 10},
            {"agent": "codex", "agent_session": {"kind": "id", "value": "bbbbbbbb-1111-2222-3333-444444444444"}, "agent_status": "working", "cwd": "C:\\work", "pane_id": "w3:p2", "state_change_seq": 3},
        ],
        "type": "agent_list",
    },
}


def assistant_row(index, ctx, cw=0, out=100, message_id=None, minute=0):
    cr = max(0, ctx - cw - 10)
    return {
        "type": "assistant",
        "timestamp": f"2026-01-01T10:{minute:02d}:{index % 60:02d}Z",
        "message": {"role": "assistant", "id": message_id or f"m{index}", "content": [{"type": "text", "text": f"turn {index}"}], "usage": {"input_tokens": 10, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr, "output_tokens": out}},
    }


class DecideTest(unittest.TestCase):
    def test_no_usage_is_skipped(self):
        self.assertEqual(watcher.decide({}, "idle", 1, None, 1000.0, CFG)[0], "skip")

    def test_below_threshold_holds(self):
        action, reason = watcher.decide({}, "idle", 1, 100000, 1000.0, CFG)
        self.assertEqual(action, "hold")
        self.assertIn("below", reason)

    def test_working_session_holds_and_resets_idle(self):
        state = {"idle_since": 500.0, "idle_seq": 1}
        action, _ = watcher.decide(state, "working", 2, 150000, 1000.0, CFG)
        self.assertEqual(action, "hold")
        self.assertIsNone(state["idle_since"])

    def test_idle_long_enough_fires_once_then_cools_down(self):
        state = {}
        self.assertEqual(watcher.decide(state, "idle", 7, 150000, 1000.0, CFG)[0], "wait")
        self.assertEqual(watcher.decide(state, "idle", 7, 150000, 1050.0, CFG)[0], "wait")
        action, reason = watcher.decide(state, "idle", 7, 150000, 1091.0, CFG)
        self.assertEqual(action, "fire")
        self.assertIn("150000 tokens", reason)
        state["last_fire"] = 1091.0
        action, reason = watcher.decide(state, "idle", 7, 150000, 1200.0, CFG)
        self.assertEqual(action, "hold")
        self.assertIn("cooldown", reason)

    def test_state_change_restarts_the_idle_clock(self):
        state = {}
        watcher.decide(state, "idle", 1, 150000, 1000.0, CFG)
        action, _ = watcher.decide(state, "idle", 2, 150000, 1100.0, CFG)
        self.assertEqual(action, "wait")
        self.assertEqual(state["idle_since"], 1100.0)

    def test_observed_compaction_is_recorded_and_respected(self):
        state = {}
        watcher.decide(state, "working", 1, 160000, 1000.0, CFG)
        watcher.decide(state, "working", 2, 70000, 1030.0, CFG)
        self.assertEqual(state["last_compaction"], 1030.0)
        watcher.decide(state, "idle", 3, 140000, 1100.0, CFG)
        action, reason = watcher.decide(state, "idle", 3, 140000, 1300.0, CFG)
        self.assertEqual(action, "hold")
        self.assertIn("compacted", reason)
        action, _ = watcher.decide(state, "idle", 3, 140000, 2000.0, CFG)
        self.assertEqual(action, "fire")


class TranscriptAndHerdrTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compaction-tools-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, rows):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        return path

    def test_last_context_reads_the_final_assistant_usage(self):
        path = self.write("t.jsonl", [assistant_row(1, 50000), {"type": "user", "message": {"content": "x"}}, assistant_row(2, 123456), {"type": "user", "message": {"content": "y"}}])
        self.assertEqual(watcher.last_context(path), 123456)

    def test_last_context_without_usage_is_none(self):
        path = self.write("t.jsonl", [{"type": "user", "message": {"content": "x"}}])
        self.assertIsNone(watcher.last_context(path))
        self.assertIsNone(watcher.last_context(os.path.join(self.tmp, "missing.jsonl")))

    def test_herdr_agents_parses_the_cli_payload(self):
        original = watcher.subprocess.run
        watcher.subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout=json.dumps(HERDR_LIST), stderr="", returncode=0)
        try:
            agents, error = watcher.herdr_agents("herdr")
        finally:
            watcher.subprocess.run = original
        self.assertEqual(error, "")
        self.assertEqual(agents, [{"pane": "w3:p1", "session": "aaaaaaaa-1111-2222-3333-444444444444", "status": "idle", "seq": 10, "cwd": "C:\\work"}])

    def test_one_pass_treats_done_as_a_boundary_state(self):
        session = "aaaaaaaa-1111-2222-3333-444444444444"
        transcript = self.write(f"{session}.jsonl", [assistant_row(1, 150000)])
        payload = json.loads(json.dumps(HERDR_LIST))
        payload["result"]["agents"][0]["agent_status"] = "done"
        original = watcher.subprocess.run
        watcher.subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)
        args = types.SimpleNamespace(herdr="herdr", panes="w3:p1", dry_run=True, prompt="/compact")
        cfg = dict(CFG, idle=0, idle_states={"idle", "done"})
        try:
            rows = watcher.one_pass(args, cfg, {}, {session: transcript}, quiet=True)
        finally:
            watcher.subprocess.run = original
        self.assertEqual(rows[0][4], "fire")
        cfg["idle_states"] = {"idle"}
        watcher.subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)
        try:
            rows = watcher.one_pass(args, cfg, {}, {session: transcript}, quiet=True)
        finally:
            watcher.subprocess.run = original
        self.assertEqual(rows[0][4], "hold")

    def test_herdr_agents_reports_failure(self):
        agents, error = watcher.herdr_agents(os.path.join(self.tmp, "no-such-herdr"))
        self.assertIsNone(agents)
        self.assertIn("failed", error)


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compaction-report-")
        rows = []
        for i in range(1, 11):
            rows.append(assistant_row(i, 100000 + i * 5000, minute=i))
        rows.append(assistant_row(10, 150000, message_id="m10", minute=10))  # streamed duplicate of turn 10
        rows.append({"type": "system", "timestamp": "2026-01-01T10:11:00Z", "compactMetadata": {"trigger": "auto", "preTokens": 150000, "postTokens": 2000, "durationMs": 120000}})
        rows.append({"type": "user", "timestamp": "2026-01-01T10:11:00Z", "isCompactSummary": True, "message": {"role": "user", "content": "S" * 8000}})
        for i in range(11, 26):
            cw = 60000 if i == 20 else 0
            rows.append(assistant_row(i, 70000 + (i - 11) * 2000, cw=cw, minute=i))
        self.path = os.path.join(self.tmp, "session.jsonl")
        with open(self.path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        self.args = types.SimpleNamespace(day="", window=200000, break_tokens=50000)
        self.weights = report.parse_weights("in=1,cw=1.25,cr=0.1,out=5")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_report_measures_boundary_cycles_breaks_and_recommendation(self):
        result = report.analyze(self.path, self.args, self.weights)
        self.assertEqual(result["turns"], 25)  # the duplicate row counts once
        self.assertEqual(len(result["compactions"]), 1)
        compaction = result["compactions"][0]
        self.assertEqual(compaction["before"], 150000)
        self.assertEqual(compaction["floor"], 70000)
        self.assertEqual(compaction["dropped"], 80000)
        self.assertEqual(compaction["summary_chars"], 8000)
        self.assertEqual(compaction["pre_tokens"], 150000)
        self.assertEqual(compaction["post_tokens"], 2000)
        self.assertEqual(compaction["duration_ms"], 120000)
        self.assertEqual(compaction["after_12"], 70000 + 11 * 2000)
        self.assertEqual([c["turns"] for c in result["cycles"]], [10, 15])
        self.assertEqual(len(result["cache_breaks"]), 1)
        self.assertEqual(result["cache_breaks"][0]["rewritten"], 60000)
        rec = result["recommendation"]
        self.assertEqual(rec["floor"], 70000)
        self.assertGreater(rec["trigger_tokens"], rec["floor"])
        self.assertLess(rec["trigger_fraction"], 1.0)

    def test_boundary_without_a_turn_after_it_is_still_listed(self):
        rows = [assistant_row(i, 100000 + i * 5000, minute=i) for i in range(1, 6)]
        rows.append({"type": "user", "timestamp": "2026-01-01T10:06:00Z", "isCompactSummary": True, "message": {"role": "user", "content": "This session is being continued from a previous conversation that ran out of context."}})
        path = os.path.join(self.tmp, "fresh.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        result = report.analyze(path, self.args, self.weights)
        self.assertEqual(len(result["compactions"]), 1)
        self.assertEqual(result["compactions"][0]["before"], 125000)
        self.assertIsNone(result["compactions"][0]["floor"])
        self.assertIsNone(result["recommendation"])

    def test_weights_parse_and_override(self):
        weights = report.parse_weights("out=3,cr=0.2")
        self.assertEqual(weights["out"], 3.0)
        self.assertEqual(weights["cr"], 0.2)
        self.assertEqual(weights["in"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
