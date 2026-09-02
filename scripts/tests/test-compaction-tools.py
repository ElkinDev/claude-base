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


def boundary_row(pre, post, minute, trigger="manual"):
    """The row Claude Code writes at a compaction, as seen on 2.1.258."""
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "timestamp": f"2026-01-01T10:{minute:02d}:54Z",
        "content": "Compacted",
        "compactMetadata": {"trigger": trigger, "preTokens": pre, "postTokens": post, "durationMs": 122944},
    }


def refusal_row(minute):
    """What the CLI writes when it refuses a /compact: no assistant record, no usage, no API call."""
    return {
        "type": "system",
        "subtype": "local_command",
        "timestamp": f"2026-01-01T10:{minute:02d}:27Z",
        "content": "<local-command-stdout>Not enough messages to compact.</local-command-stdout>",
    }


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
        self.assertEqual(state["fires_without_effect"], 0)

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

    def test_fires_without_effect_back_off_the_cooldown(self):
        state = {}
        watcher.decide(state, "idle", 7, 151579, mark(100), 1000.0, CFG)
        watcher.decide(state, "idle", 7, 151579, mark(100), 1100.0, CFG)
        self.assertEqual(state.get("fires_without_effect", 0), 0)
        # one base cooldown later with the same position, the fire is counted as ineffective
        watcher.decide(state, "idle", 7, 151579, mark(100), 2100.0, CFG)
        self.assertEqual(state["fires_without_effect"], 1)
        watcher.decide(state, "idle", 7, 151579, mark(100), 3100.0, CFG)
        self.assertEqual(state["fires_without_effect"], 1)  # one count per fire, not per pass
        self.assertEqual(watcher.effective_cooldown(state, CFG), 2 * CFG["cooldown"])
        state["fires_without_effect"] = 9
        self.assertEqual(watcher.effective_cooldown(state, CFG), 16 * CFG["cooldown"])
        action, _ = watcher.decide(state, "idle", 7, 151579, mark(400), 4000.0, CFG)
        self.assertEqual(action, "fire")
        self.assertEqual(state["fires_without_effect"], 0)
        self.assertEqual(watcher.effective_cooldown(state, CFG), CFG["cooldown"])


class TranscriptAndHerdrTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compaction-tools-")
        # keep test decisions out of the real watcher log, which is the owner's evidence
        self._log_file = watcher.LOG_FILE
        watcher.LOG_FILE = os.path.join(self.tmp, "watcher.log")

    def tearDown(self):
        watcher.LOG_FILE = self._log_file
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, rows):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        return path

    def test_last_context_reads_the_final_assistant_usage(self):
        path = self.write("t.jsonl", [assistant_row(1, 50000), {"type": "user", "message": {"content": "x"}}, assistant_row(2, 123456), {"type": "user", "message": {"content": "y"}}])
        ctx, position = watcher.last_context(path)
        self.assertEqual(ctx, 123456)
        self.assertEqual(position.kind, "usage")
        self.assertGreater(position.offset, 0)
        # the position moves with the next turn, which is how a stale number is recognised
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(assistant_row(3, 130000)) + "\n")
        again, moved = watcher.last_context(path)
        self.assertEqual(again, 130000)
        self.assertGreater(moved.offset, position.offset)

    def test_last_context_without_usage_is_none(self):
        path = self.write("t.jsonl", [{"type": "user", "message": {"content": "x"}}])
        self.assertEqual(watcher.last_context(path), (None, None))
        self.assertEqual(watcher.last_context(os.path.join(self.tmp, "missing.jsonl")), (None, None))

    def test_last_context_reports_the_floor_when_a_boundary_is_newer(self):
        """After a compaction with no turn yet, the last usage row is the pre-compaction
        number and reading it as the live context over-states by 60000 tokens."""
        path = self.write("boundary.jsonl", [assistant_row(1, 151579, minute=16), boundary_row(153426, 19259, 19)])
        ctx, position = watcher.last_context(path)
        self.assertEqual(ctx, 19259)
        self.assertEqual(position.kind, "boundary")
        # the refusals the CLI writes are not turns and must not move the position
        with open(path, "a", encoding="utf-8") as handle:
            for minute in (33, 48):
                handle.write(json.dumps(refusal_row(minute)) + "\n")
        self.assertEqual(watcher.last_context(path), (ctx, position))
        # a new assistant turn is newer than the boundary and takes over again
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(assistant_row(2, 88000, minute=50)) + "\n")
        ctx, position = watcher.last_context(path)
        self.assertEqual(ctx, 88000)
        self.assertEqual(position.kind, "usage")

    def test_the_2026_09_02_log_submits_once_and_holds_until_a_new_turn(self):
        """One submission worked and four were refused with 'Not enough messages to compact.'
        at the same stale 151579. The watcher must hold on every pass after the first."""
        path = self.write("stale.jsonl", [assistant_row(1, 151579, minute=16)])
        state, cfg, now = {}, dict(CFG, idle=90), 1000.0
        actions = []

        def pass_at(seconds):
            ctx, position = watcher.last_context(path)
            return watcher.decide(state, "idle", 7, ctx, position, seconds, cfg)

        self.assertEqual(pass_at(now)[0], "wait")
        action, reason = pass_at(now + 100)
        self.assertEqual(action, "fire")
        self.assertIn("151579 tokens", reason)
        # 90 seconds later the compaction lands; every later pass sees a boundary or a refusal
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(boundary_row(153426, 19259, 19)) + "\n")
        for step, minute in enumerate((33, 48, 63, 78)):
            action, reason = pass_at(now + 100 + 900 * (step + 1))
            actions.append(action)
            self.assertEqual(action, "hold", reason)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(refusal_row(minute % 60)) + "\n")
        self.assertEqual(actions, ["hold"] * 4)
        # and it arms again only when a real turn comes back above the threshold
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(assistant_row(2, 90000, minute=50)) + "\n")
        self.assertEqual(pass_at(now + 5000)[0], "hold")  # 45 percent, below the threshold
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(assistant_row(3, 151000, minute=55)) + "\n")
        self.assertEqual(pass_at(now + 6000)[0], "fire")

    def test_herdr_agents_parses_the_cli_payload(self):
        original = watcher.subprocess.run
        watcher.subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout=json.dumps(HERDR_LIST), stderr="", returncode=0)
        try:
            agents, error = watcher.herdr_agents("herdr")
        finally:
            watcher.subprocess.run = original
        self.assertEqual(error, "")
        self.assertEqual(agents, [{"pane": "w3:p1", "session": "aaaaaaaa-1111-2222-3333-444444444444", "status": "idle", "seq": 10, "cwd": "C:\\work", "name": "", "title": "", "tab": "", "tab_label": ""}])

    def test_select_agents_by_pane_session_prefix_or_name_regex(self):
        agents = [
            {"pane": "w4:p1", "session": "f2109a6d-0000", "name": "", "title": "NewOrquestrator", "session_name": "orchestrator"},
            {"pane": "w4:p2", "session": "4a512d43-0000", "name": "analyst", "title": "Analizar epica y plan"},
            {"pane": "w1:p7", "session": "445ce75b-0000", "name": "", "title": "", "tab_label": "cc-agents-orchestrator"},
        ]
        panes = lambda rows: [a["pane"] for a in rows]
        self.assertEqual(panes(watcher.select_agents(agents)), ["w4:p1", "w4:p2", "w1:p7"])
        self.assertEqual(panes(watcher.select_agents(agents, panes="w1:p7")), ["w1:p7"])
        self.assertEqual(panes(watcher.select_agents(agents, sessions="F2109A6D")), ["w4:p1"])
        self.assertEqual(panes(watcher.select_agents(agents, titles="orques|orchestr")), ["w4:p1", "w1:p7"])
        self.assertEqual(panes(watcher.select_agents(agents, titles="agents")), ["w1:p7"])
        self.assertEqual(watcher.label_of(agents[2]), "cc-agents-orchestrator")
        self.assertEqual(panes(watcher.select_agents(agents, titles="^analyst$")), ["w4:p2"])
        self.assertEqual(panes(watcher.select_agents(agents, panes="w1:p7", titles="analista")), ["w1:p7"])
        self.assertEqual(panes(watcher.select_agents(agents, titles="nothing-like-this")), [])
        self.assertEqual(panes(watcher.select_agents(agents, titles="^orchestrator$")), ["w4:p1"])
        self.assertEqual(watcher.label_of(agents[0]), "orchestrator")

    def test_session_name_follows_renames_incrementally(self):
        path = self.write("named.jsonl", [assistant_row(1, 1000)])
        names = {}
        self.assertEqual(watcher.session_name(path, names), "")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "custom-title", "customTitle": "orchestrator", "sessionId": "x"}) + "\n")
            handle.write(json.dumps({"type": "agent-name", "agentName": "orchestrator", "sessionId": "x"}) + "\n")
        self.assertEqual(watcher.session_name(path, names), "orchestrator")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(assistant_row(2, 2000)) + "\n")
            handle.write('{"type":"custom-title","customTitle":"analyst"')  # half-written line
        self.assertEqual(watcher.session_name(path, names), "orchestrator")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(',"sessionId":"x"}\n')
        self.assertEqual(watcher.session_name(path, names), "analyst")
        self.assertEqual(watcher.session_name("", names), "")

    def test_one_pass_logs_a_missing_selector_once(self):
        session = "aaaaaaaa-1111-2222-3333-444444444444"
        transcript = self.write(f"{session}.jsonl", [assistant_row(1, 150000)])
        original_run, original_log = watcher.subprocess.run, watcher.log
        lines = []
        watcher.subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout=json.dumps(HERDR_LIST), stderr="", returncode=0)
        watcher.log = lambda message, quiet=False: lines.append(message)
        args = types.SimpleNamespace(herdr="herdr", panes="", sessions="", titles="orques", dry_run=True, prompt="/compact")
        states = {}
        try:
            self.assertEqual(watcher.one_pass(args, dict(CFG, idle_states={"idle"}), states, {session: transcript}, quiet=True), [])
            self.assertEqual(watcher.one_pass(args, dict(CFG, idle_states={"idle"}), states, {session: transcript}, quiet=True), [])
            args.titles = ""
            args.sessions = "aaaaaaaa"
            rows = watcher.one_pass(args, dict(CFG, idle_states={"idle"}), states, {session: transcript}, quiet=True)
        finally:
            watcher.subprocess.run, watcher.log = original_run, original_log
        self.assertEqual(len([l for l in lines if l.startswith("no Claude pane matches")]), 1)
        self.assertEqual(len([l for l in lines if "matches again" in l]), 1)
        self.assertEqual(rows[0][0], "w3:p1")
        self.assertNotIn("_nomatch", states)

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
