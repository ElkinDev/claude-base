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


def record(kind, uuid, **extra):
    """One transcript line in the compact form the harness writes."""
    line = {"parentUuid": None, "isSidechain": True, "agentId": "a1"}
    if kind == "prompt":
        line.update(type="user", message={"role": "user", "content": extra.pop("text", "Do the task.")})
    elif kind == "result":
        line.update(type="user", message={"role": "user", "content": [
            {"tool_use_id": "t" + uuid, "type": "tool_result", "content": "ok"}]})
    elif kind == "image":
        line.update(type="user", isMeta=True, message={"role": "user", "content": [
            {"type": "text", "text": "[Image: original 1080x2400]"}]})
    elif kind == "tool":
        line.update(type="assistant", message={"id": "msg_" + extra.pop("turn", uuid), "type": "message", "role": "assistant",
                                               "content": [{"type": "tool_use", "id": "t" + uuid}],
                                               "stop_reason": "tool_use", "usage": {"input_tokens": 1}})
    elif kind == "end":
        line.update(type="assistant", message={"id": "msg_" + uuid, "type": "message", "role": "assistant",
                                               "content": [{"type": "text", "text": "Report: done."}],
                                               "stop_reason": "end_turn", "usage": {"input_tokens": 1}})
    elif kind == "attachment":
        line.update(type="attachment", attachment={"type": "total_tokens_reminder", "text": "left"})
    serial = int(uuid[1:])
    line.update(uuid=uuid, timestamp="2026-10-23T%02d:%02d:00.000Z" % (10 + serial // 60, serial % 60))
    line.update(extra)
    return json.dumps(line, separators=(",", ":"))


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

    def call(self, agent_id=None, agent_type=None, cwd=None, event="PreToolUse", transcript="", own=None):
        payload = {"session_id": "s1", "tool_name": "Read", "tool_input": {"file_path": "x"},
                   "transcript_path": transcript, "cwd": cwd or self.project, "hook_event_name": event}
        if own is not None:
            payload["agent_transcript_path"] = own
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
        self.assertIn("has started 7 tool calls; its limit is 10 turns", got[0][1])
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


class ResumedRun(unittest.TestCase):
    """A resumed agent keeps its agent_id but gets a fresh maxTurns: the count starts again."""

    tearDown, call, notes = TurnBudget.tearDown, TurnBudget.call, TurnBudget.notes

    def setUp(self):
        TurnBudget.setUp(self)
        definition(os.path.join(self.project, ".claude", "agents"), "worker", 10)
        self.sessions = os.path.join(self.home, ".claude", "projects", "p")
        self.main = os.path.join(self.sessions, "s1.jsonl")
        self.own = os.path.join(self.sessions, "s1", "subagents", "agent-a1.jsonl")
        os.makedirs(os.path.dirname(self.own))
        self.serial = 0
        self.write(record("prompt", self.uid()))

    def uid(self):
        self.serial += 1
        return "u%d" % self.serial

    def write(self, *lines):
        with open(self.own, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(line + "\n" for line in lines))

    def turns(self, n):
        """n turns of one call each, written to the transcript the way the harness writes them."""
        outputs = []
        for _ in range(n):
            uid = self.uid()
            self.write(record("tool", uid))
            outputs.append(self.call("a1", "worker", transcript=self.main))
            self.write(record("result", uid), record("attachment", self.uid()))
        return outputs

    def parallel(self, turns, per):
        """turns of per parallel calls each: one assistant message with per tool_use records."""
        outputs = []
        for _ in range(turns):
            turn = self.uid()
            ids = [self.uid() for _ in range(per)]
            self.write(*[record("tool", uid, turn=turn) for uid in ids])
            outputs += [self.call("a1", "worker", transcript=self.main) for _ in ids]
            self.write(*[record("result", uid) for uid in ids])
        return outputs

    def budget(self):
        return os.path.join(self.ledger, "turn-budget", "a1")

    def runs(self):
        return sorted(name for name in os.listdir(self.budget()) if name.startswith("run-"))

    def calls_in(self, folder):
        return sum(1 for name in os.listdir(folder) if name[0].isdigit())

    def test_a_first_run_reaches_70_percent_and_counts_in_the_agent_folder(self):
        got = self.notes(self.turns(8))
        self.assertEqual([7], [n for n, _ in got])
        self.assertIn("has started 7 tool calls", got[0][1])
        self.assertEqual([], self.runs())
        self.assertEqual(8, self.calls_in(self.budget()))

    def test_a_resume_after_the_final_report_starts_the_count_again(self):
        self.assertEqual([7, 9], [n for n, _ in self.notes(self.turns(9))])
        self.write(record("end", self.uid()), record("attachment", self.uid()),
                   record("prompt", self.uid(), text="Round 2.", isMeta=True, origin={"kind": "coordinator"}))
        got = self.notes(self.turns(9))
        self.assertEqual([7, 9], [n for n, _ in got])
        self.assertIn("has started 7 tool calls", got[0][1])
        self.assertEqual(1, len(self.runs()))
        self.assertEqual(9, self.calls_in(os.path.join(self.budget(), self.runs()[0])))
        self.assertEqual(9, self.calls_in(self.budget()))

    def test_a_second_resume_starts_a_third_run(self):
        for _ in range(2):
            self.turns(7)
            self.write(record("end", self.uid()), record("prompt", self.uid(), text="Next round."))
        self.assertEqual([7], [n for n, _ in self.notes(self.turns(7))])
        self.assertEqual(2, len(self.runs()))

    def test_a_resume_after_a_cut_off_at_the_limit_starts_the_count_again(self):
        self.turns(10)
        self.write(record("image", self.uid()),
                   record("prompt", self.uid(), text="You hit the limit.", isMeta=True, origin={"kind": "coordinator"}))
        self.assertEqual([7, 9], [n for n, _ in self.notes(self.turns(9))])

    def test_a_message_while_the_agent_works_starts_nothing_even_past_the_limit(self):
        self.turns(3)
        self.write(record("prompt", self.uid(), text="Also check B.", isMeta=True, origin={"kind": "coordinator"}),
                   record("image", self.uid()))
        self.assertEqual([4, 6], [n for n, _ in self.notes(self.turns(10))])
        self.assertEqual([], self.runs())
        self.assertEqual([], self.notes(self.turns(4)))  # 17 calls: the message never becomes a run
        # Judged once and marked, so no later call reads further back than the tail for it.
        self.assertEqual(1, sum(name.startswith("seen-") for name in os.listdir(self.budget())))

    def test_parallel_calls_past_the_limit_before_the_turns_do_start_no_run(self):
        self.assertEqual([7, 9], [n for n, _ in self.notes(self.parallel(3, 4))])  # 12 calls, 3 turns
        self.write(record("prompt", self.uid(), text="Also check B.", isMeta=True, origin={"kind": "coordinator"}))
        self.assertEqual([], self.notes(self.parallel(1, 3) + self.parallel(6, 1)))  # turns 4 to 10
        self.assertEqual([], self.runs())

    def test_a_resume_after_a_cut_off_with_parallel_calls_starts_the_count_again(self):
        self.parallel(3, 4)
        self.write(record("prompt", self.uid(), text="Also check B.", isMeta=True, origin={"kind": "coordinator"}))
        self.parallel(7, 1)  # cut off at turn 10, with 19 calls
        self.write(record("prompt", self.uid(), text="Go on.", isMeta=True, origin={"kind": "coordinator"}))
        got = self.notes(self.parallel(9, 1))
        self.assertEqual([7, 9], [n for n, _ in got])
        self.assertIn("has started 7 tool calls", got[0][1])
        self.assertEqual(1, len(self.runs()))

    def test_a_cut_off_whose_start_is_out_of_the_tail_reads_further_back(self):
        definition(os.path.join(self.project, ".claude", "agents"), "worker", 4)
        self.write(*[record("attachment", self.uid(), pad="x" * 4000) for _ in range(80)])  # 320 KB
        self.turns(4)
        self.write(*[record("attachment", self.uid(), pad="x" * 4000) for _ in range(80)])
        self.write(record("prompt", self.uid(), text="Go on.", isMeta=True, origin={"kind": "coordinator"}))
        self.assertEqual([3, 4], [n for n, _ in self.notes(self.turns(4))])
        self.assertEqual(1, len(self.runs()))

    def test_an_image_record_starts_nothing_even_past_the_limit(self):
        self.turns(10)  # parallel calls can pass the limit before the turns do
        self.write(record("image", self.uid()))
        self.assertEqual([], self.notes(self.turns(7)))
        self.assertEqual([], self.runs())

    def test_a_task_notification_after_the_report_is_a_new_run_too(self):
        self.turns(9)
        self.write(record("end", self.uid()), record("prompt", self.uid(), text="[SYSTEM NOTIFICATION]",
                                                     isMeta=True, origin={"kind": "task-notification"}))
        self.assertEqual([7], [n for n, _ in self.notes(self.turns(7))])

    def test_an_unreadable_transcript_gives_the_count_of_before(self):
        os.remove(self.own)
        os.makedirs(self.own)  # a folder where the transcript should be: found by the glob, unreadable
        outputs = [self.call("a1", "worker", transcript=self.main) for _ in range(12)]
        outputs += [self.call("a1", "worker", own=os.path.join(self.base, "missing.jsonl")) for _ in range(2)]
        self.assertEqual([7, 9], [n for n, _ in self.notes(outputs)])
        self.assertEqual(14, self.calls_in(self.budget()))
        self.assertEqual([], self.runs())

    def test_a_transcript_of_garbage_gives_the_count_of_before(self):
        with open(self.own, "wb") as handle:
            handle.write(b'\x00\xff{"type":"user" not json\n' * 50)
        outputs = [self.call("a1", "worker", own=self.own) for _ in range(12)]
        self.assertEqual([7, 9], [n for n, _ in self.notes(outputs)])
        self.assertEqual([], self.runs())

    def test_parallel_calls_in_a_resumed_run_count_once_each_and_give_each_note_once(self):
        definition(os.path.join(self.project, ".claude", "agents"), "worker", 20)
        self.turns(20)
        self.write(record("end", self.uid()), record("prompt", self.uid(), text="Round 2."))
        with ThreadPoolExecutor(max_workers=10) as pool:
            outputs = list(pool.map(lambda _: self.call("a1", "worker", own=self.own), range(20)))
        texts = [o["hookSpecificOutput"]["additionalContext"] for o in outputs if "hookSpecificOutput" in o]
        self.assertEqual(1, sum("70 percent" in t for t in texts))
        self.assertEqual(1, sum("90 percent" in t for t in texts))
        self.assertEqual(1, len(self.runs()))
        self.assertEqual(20, self.calls_in(os.path.join(self.budget(), self.runs()[0])))


class Judge(unittest.TestCase):
    """judge() in process: when the bytes read hold no start of the run, only a seen prompt is settled."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("turn_budget", HOOK)
        self.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.hook)

    def test_a_seen_prompt_needs_no_wider_read_and_an_unseen_one_does(self):
        found = [("u9", "t9", False, False, 2)]  # a message after a tool result, 2 turns into the tail
        self.assertEqual(("A", []), self.hook.judge("A", {"seen-u9"}, "A", found, False, 10))
        self.assertIsNone(self.hook.judge("A", set(), "A", found, False, 10))
        self.assertEqual(("A", ["u9"]), self.hook.judge("A", set(), "A", found, True, 10))


if __name__ == "__main__":
    unittest.main(verbosity=2)
