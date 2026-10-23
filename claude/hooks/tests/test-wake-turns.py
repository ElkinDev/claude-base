"""Tests for claude/hooks/wake-turns.py. The hook runs as the harness runs it, a subprocess with a payload on stdin,
against fixture transcripts in a temporary folder; WAKE_TURNS_DIR puts its state and log there too.

    python -I -B claude/hooks/tests/test-wake-turns.py
    WAKE_TURNS_PY=<path> python -I -B claude/hooks/tests/test-wake-turns.py      # against a staged copy (mutants)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.environ.get("WAKE_TURNS_PY") or os.path.join(os.path.dirname(HERE), "wake-turns.py")
SESSION = "11111111-2222-3333-4444-555555555555"
NOTICE = ("Wake rule: this wake (a turn started by a finished subagent or background command) has used %d API "
          "turns, and a wake takes at most 3, since every turn re-reads the whole context. Put what is left in one "
          "batched call, then end the turn.")
AGENT_DONE = ("<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>\n"
              "<summary>Agent \"x\" completed</summary>\n</task-notification>")
BG_DONE = ("<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n"
           "<summary>Background command \"y\" completed (exit code 0)</summary>\n</task-notification>")


def line(rec):
    return json.dumps(rec, separators=(",", ":")) + "\n"


def user(content, **extra):
    rec = {"parentUuid": None, "isSidechain": False, "type": "user", "uuid": str(uuid.uuid4()),
           "timestamp": "2026-10-23T15:00:00.000Z", "message": {"role": "user", "content": content}}
    rec.update(extra)
    return line(rec)


def notification(text=AGENT_DONE):
    return user(text, turnOrigin="task_notification", promptSource="system")


def assistant(mid, sidechain=False):
    tid = "toolu_" + uuid.uuid4().hex[:12]
    rec = {"parentUuid": None, "isSidechain": sidechain, "type": "assistant", "uuid": str(uuid.uuid4()),
           "timestamp": "2026-10-23T15:00:01.000Z",
           "message": {"id": mid, "role": "assistant", "type": "message",
                       "content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "ls"}}],
                       "stop_reason": "tool_use", "usage": {"input_tokens": 1, "output_tokens": 1}}}
    return line(rec), tid


def result(tid, size=10):
    return user([{"type": "tool_result", "tool_use_id": tid, "content": "x" * size}])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wake turns ")
        self.proj = os.path.join(self.tmp, "projects", "C--work-Fixture")
        os.makedirs(self.proj)
        self.path = os.path.join(self.proj, SESSION + ".jsonl")
        self.turn_no = 0
        open(self.path, "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, text):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(text)

    def turn(self, result_size=10, copies=1):
        """One API turn: an assistant tool_use record (streamed copies share the id), as the transcript
        holds it when PostToolUse fires. The previous turn's tool_result is written first."""
        self.turn_no += 1
        mid = "msg_%d_%s" % (self.turn_no, uuid.uuid4().hex[:6])
        if getattr(self, "pending", None):
            self.add(result(self.pending, result_size))
        rec, self.pending = assistant(mid)
        for _ in range(copies):
            self.add(rec)

    def start(self, text):
        if getattr(self, "pending", None):
            self.add(result(self.pending))
            self.pending = None
        self.add(text)

    def run_hook(self, payload=None):
        payload = payload or {"session_id": SESSION, "transcript_path": self.path, "tool_name": "Bash",
                              "hook_event_name": "PostToolUse"}
        env = dict(os.environ, WAKE_TURNS_DIR=self.tmp)
        proc = subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(payload).encode("utf-8"),
                              capture_output=True, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.decode("utf-8")

    def assertNotice(self, out, n):
        self.assertEqual(json.loads(out), {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                                  "additionalContext": NOTICE % n}})

    def log_lines(self):
        try:
            with open(os.path.join(self.tmp, "wake-turns.log"), encoding="utf-8") as fh:
                return fh.read().splitlines()
        except OSError:
            return []

    def wake_to(self, n, text=AGENT_DONE):
        """A wake run turn by turn up to n, the hook firing after each turn; returns the outputs."""
        self.start(notification(text) if text.startswith("<task-notification>") else user(text))
        outs = []
        for _ in range(n):
            self.turn()
            outs.append(self.run_hook())
        return outs


class WakeTurns(Base):
    def test_two_turns_no_output(self):
        self.assertEqual(self.wake_to(2), ["", ""])

    def test_three_turns_notice(self):
        outs = self.wake_to(3)
        self.assertEqual(outs[:2], ["", ""])
        self.assertNotice(outs[2], 3)
        log = self.log_lines()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0].split("\t")[1:], [SESSION[:8], "C--work-Fixture", "3"])

    def test_background_command_wake(self):
        self.assertNotice(self.wake_to(3, BG_DONE)[2], 3)

    def test_four_nothing_more(self):
        outs = self.wake_to(4)
        self.assertNotice(outs[2], 3)
        self.assertEqual(outs[3], "")

    def test_six_second_notice(self):
        outs = self.wake_to(7)
        self.assertNotice(outs[2], 3)
        self.assertEqual(outs[3:5], ["", ""])
        self.assertNotice(outs[5], 6)
        self.assertEqual(outs[6], "")
        self.assertEqual(len(self.log_lines()), 2)

    def test_parallel_tools_once(self):
        """Several PostToolUse calls in the same 3rd turn give one notice."""
        outs = self.wake_to(3)
        self.assertNotice(outs[2], 3)
        self.assertEqual(self.run_hook(), "")
        self.assertEqual(self.run_hook(), "")

    def test_streamed_copies_count_once(self):
        self.start(notification())
        outs = []
        for _ in range(2):
            self.turn(copies=3)
            outs.append(self.run_hook())
        self.assertEqual(outs, ["", ""])

    def test_sidechain_records_ignored(self):
        self.start(notification())
        self.turn()
        self.run_hook()
        for i in range(4):
            self.add(assistant("msg_side_%d" % i, sidechain=True)[0])
        self.assertEqual(self.run_hook(), "")

    def test_real_prompt_five_turns_nothing(self):
        self.assertEqual(self.wake_to(5, "please do the thing"), [""] * 5)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "wake-turns-state", SESSION + ".json")))

    def test_subagent_input_nothing(self):
        self.wake_to(2)
        self.turn()
        for key, value in (("agent_id", "a123"), ("agent_type", "general-purpose")):
            payload = {"session_id": SESSION, "transcript_path": self.path, "tool_name": "Bash", key: value}
            self.assertEqual(self.run_hook(payload), "")
        self.assertEqual(self.log_lines(), [])

    def test_missing_transcript(self):
        payload = {"session_id": SESSION, "transcript_path": os.path.join(self.tmp, "nope.jsonl"),
                   "tool_name": "Bash"}
        self.assertEqual(self.run_hook(payload), "")

    def test_bad_input(self):
        env = dict(os.environ, WAKE_TURNS_DIR=self.tmp)
        for raw in (b"", b"not json", b"[1,2]", b'{"session_id": "../../x", "transcript_path": "y"}'):
            proc = subprocess.run([sys.executable, "-I", HOOK], input=raw, capture_output=True, env=env,
                                  timeout=30)
            self.assertEqual((proc.returncode, proc.stdout), (0, b""))

    def test_new_wake_resets(self):
        outs = self.wake_to(4)
        self.assertNotice(outs[2], 3)
        outs = self.wake_to(3, BG_DONE)
        self.assertEqual(outs[:2], ["", ""])
        self.assertNotice(outs[2], 3)

    def test_prompt_between_wakes(self):
        self.wake_to(3)
        self.assertEqual(self.wake_to(4, "owner prompt"), [""] * 4)
        self.assertNotice(self.wake_to(3)[2], 3)

    def test_meta_and_interrupt_keep_the_wake(self):
        self.start(notification())
        self.turn()
        self.run_hook()
        self.add(user("[Request interrupted by user]"))
        self.add(user("<system-reminder>x</system-reminder>", isMeta=True))
        self.turn()
        self.run_hook()
        self.turn()
        self.assertNotice(self.run_hook(), 3)

    def test_anchor_beyond_tail_uses_state(self):
        """A wake whose results pass the 512 KB tail is continued from the state file."""
        self.start(notification())
        self.turn()
        self.assertEqual(self.run_hook(), "")
        self.turn(result_size=700 * 1024)
        self.assertEqual(self.run_hook(), "")
        self.turn(result_size=700 * 1024)
        self.assertNotice(self.run_hook(), 3)


if __name__ == "__main__":
    unittest.main(verbosity=1)
