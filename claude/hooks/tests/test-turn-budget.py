"""Tests for claude/hooks/turn-budget.py, the PreToolUse hook that keeps a subagent's turn budget in sight.

    python claude/hooks/tests/test-turn-budget.py

The hook runs the way the harness runs it: a subprocess with the payload JSON on stdin. Every case
points CLAUDE_LEDGER_DIR, HOME and USERPROFILE into a fresh temporary folder, and the agent
definitions it reads are written there, so the real ledger and the real agents folder are never
touched. No network.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(HERE), "turn-budget.py")


def definition(folder, name, max_turns=None, body=""):
    os.makedirs(folder, exist_ok=True)
    lines = ["---", "name: " + name, "model: opus"]
    if max_turns is not None:
        lines.append("maxTurns: %d" % max_turns)
    lines += ["---", "", body]
    with open(os.path.join(folder, name + ".md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


class TurnBudget(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="turn-budget-")
        self.home = os.path.join(self.base, "home")
        self.project = os.path.join(self.base, "project")
        self.ledger = os.path.join(self.base, "ledger")
        os.makedirs(self.home)
        os.makedirs(self.project)
        self.env = dict(os.environ, CLAUDE_LEDGER_DIR=self.ledger, HOME=self.home, USERPROFILE=self.home)
        self.env.pop("BIG_RESULT_NOTIFY", None)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def call(self, agent_id=None, agent_type=None, cwd=None, event="PreToolUse"):
        payload = {"session_id": "s1", "tool_name": "Read", "tool_input": {"file_path": "x"},
                   "transcript_path": "", "cwd": cwd or self.project, "hook_event_name": event}
        if agent_id is not None:
            payload["agent_id"] = agent_id
        if agent_type is not None:
            payload["agent_type"] = agent_type
        done = subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(payload).encode("utf-8"),
                              capture_output=True, env=self.env, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        text = done.stdout.decode("utf-8").strip()
        return json.loads(text) if text else {}

    def notes(self, outputs):
        return [(i + 1, o["hookSpecificOutput"]["additionalContext"]) for i, o in enumerate(outputs)
                if "hookSpecificOutput" in o]

    def test_the_main_session_gets_no_note_and_no_counter(self):
        definition(os.path.join(self.project, ".claude", "agents"), "worker", 10)
        outputs = [self.call() for _ in range(12)]
        self.assertEqual([], self.notes(outputs))
        self.assertFalse(os.path.exists(os.path.join(self.ledger, "turn-budget")))

    def test_a_project_agent_gets_one_note_at_70_and_one_at_90_percent(self):
        definition(os.path.join(self.project, ".claude", "agents"), "worker", 10)
        outputs = [self.call("a1", "worker") for _ in range(12)]
        got = self.notes(outputs)
        self.assertEqual([7, 9], [n for n, _ in got])
        self.assertIn("Turn budget, 70 percent", got[0][1])
        self.assertIn("has made 7 tool calls; its limit is 10 turns", got[0][1])
        self.assertIn("Turn budget, 90 percent", got[1][1])
        self.assertEqual("PreToolUse", outputs[6]["hookSpecificOutput"]["hookEventName"])

    def test_the_user_folder_is_read_when_the_project_has_no_definition(self):
        definition(os.path.join(self.home, ".claude", "agents"), "worker", 100)
        outputs = [self.call("a2", "worker") for _ in range(70)]
        self.assertEqual([70], [n for n, _ in self.notes(outputs)])

    def test_the_project_definition_wins_over_the_user_one(self):
        definition(os.path.join(self.home, ".claude", "agents"), "worker", 100)
        definition(os.path.join(self.project, ".claude", "agents"), "worker", 10)
        outputs = [self.call("a3", "worker") for _ in range(7)]
        self.assertEqual([7], [n for n, _ in self.notes(outputs)])

    def test_an_agent_without_maxturns_or_without_a_definition_gets_nothing(self):
        definition(os.path.join(self.project, ".claude", "agents"), "free", None, body="maxTurns: 3")
        outputs = [self.call("a4", "free") for _ in range(5)] + [self.call("a5", "Explore") for _ in range(5)]
        self.assertEqual([], self.notes(outputs))
        self.assertFalse(os.path.exists(os.path.join(self.ledger, "turn-budget", "a4")))

    def test_parallel_calls_count_every_call_and_give_each_note_once(self):
        definition(os.path.join(self.project, ".claude", "agents"), "worker", 20)
        with ThreadPoolExecutor(max_workers=10) as pool:
            outputs = list(pool.map(lambda _: self.call("a6", "worker"), range(20)))
        calls = os.listdir(os.path.join(self.ledger, "turn-budget", "a6"))
        self.assertEqual(20, sum(1 for name in calls if not name.startswith("mark-")))
        texts = [o["hookSpecificOutput"]["additionalContext"] for o in outputs if "hookSpecificOutput" in o]
        self.assertEqual(1, sum("70 percent" in t for t in texts))
        self.assertEqual(1, sum("90 percent" in t for t in texts))

    def test_the_note_names_the_event_it_answers(self):
        definition(os.path.join(self.project, ".claude", "agents"), "worker", 1)
        out = self.call("a7", "worker", event="PostToolUseFailure")
        self.assertEqual("PostToolUseFailure", out["hookSpecificOutput"]["hookEventName"])
        self.assertIn("Turn budget, 90 percent", out["hookSpecificOutput"]["additionalContext"])

    def test_an_agent_id_cannot_leave_the_counter_folder(self):
        definition(os.path.join(self.project, ".claude", "agents"), "worker", 10)
        self.call("../../escape", "worker")
        self.assertFalse(os.path.exists(os.path.join(self.base, "escape")))
        self.assertEqual(["escape"], os.listdir(os.path.join(self.ledger, "turn-budget")))

    def test_a_dots_only_agent_id_counts_nothing_and_writes_nothing(self):
        definition(os.path.join(self.project, ".claude", "agents"), "worker", 3)
        os.makedirs(self.ledger)
        with open(os.path.join(self.ledger, "tool-sizes.csv"), "w") as handle:
            handle.write("time\n")
        outputs = [self.call(name, "worker") for name in ("..", ".", "...")]
        self.assertEqual([], self.notes(outputs))
        self.assertEqual(["tool-sizes.csv"], os.listdir(self.ledger))

    def test_old_counters_go_when_a_new_agent_starts(self):
        definition(os.path.join(self.project, ".claude", "agents"), "worker", 10)
        self.call("old", "worker")
        self.call("recent", "worker")
        old = os.path.join(self.ledger, "turn-budget", "old")
        past = time.time() - 3 * 86400
        os.utime(old, (past, past))
        self.call("recent", "worker")  # an agent already counting prunes nothing
        self.assertTrue(os.path.exists(old))
        self.call("new", "worker")
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(os.path.join(self.ledger, "turn-budget", "recent")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
