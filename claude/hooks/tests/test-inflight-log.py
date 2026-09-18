"""Tests for claude/hooks/inflight.py (the Stop hook that logs the live subagents of a turn)
and for scripts/agents-in-flight.py, which reads what it wrote.

    python claude/hooks/tests/test-inflight-log.py

The hook runs the way the harness runs it: a subprocess with the payload JSON on stdin, against a
transcript fixture this file writes into a temporary folder. CLAUDE_INFLIGHT_LOG and
CLAUDE_INFLIGHT_STATE_DIR point into that folder, so the log and the state files the live sessions
read are never touched. No network, no real transcript, no home directory.

The transcript records are written the way the harness writes them, with no space after the colon:
the hook looks for the literal '"type":"user"' before it parses a line, which is what keeps a hook
that runs at every turn end from parsing megabytes of tool output.
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
HOOK = os.path.join(HOOKS, "inflight.py")
READER = os.path.join(ROOT, "scripts", "agents-in-flight.py")

AGENT_ID = "a1b2c3d4e5f6"
DESCRIPTION = "Lane w99 implement"


def compact(record):
    return json.dumps(record, separators=(",", ":")) + "\n"


def launch_line():
    return compact({
        "type": "user",
        "timestamp": "2026-09-18T10:00:00.000Z",
        "toolUseResult": {"status": "async_launched", "agentId": AGENT_ID, "description": DESCRIPTION},
        "message": {"content": [{"type": "tool_result", "content": "launched"}]},
    })


def completion_line():
    text = "<task-notification><task-id>%s</task-id><status>completed</status></task-notification>" % AGENT_ID
    return compact({
        "type": "user",
        "timestamp": "2026-09-18T10:05:00.000Z",
        "message": {"content": [{"type": "text", "text": text}]},
    })


class InflightLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="inflight-")
        self.transcript = os.path.join(self.tmp, "session.jsonl")
        self.log = os.path.join(self.tmp, "inflight.log")
        self.state = os.path.join(self.tmp, "state")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def append(self, line):
        with open(self.transcript, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)

    def run_hook(self, session="deadbeef1234"):
        env = os.environ.copy()
        env["CLAUDE_INFLIGHT_LOG"] = self.log
        env["CLAUDE_INFLIGHT_STATE_DIR"] = self.state
        payload = json.dumps({"session_id": session, "transcript_path": self.transcript}).encode("utf-8")
        done = subprocess.run(
            [sys.executable, HOOK], input=payload, capture_output=True, env=env, cwd=self.tmp, timeout=60,
        )
        self.assertEqual(done.returncode, 0, "a Stop hook never blocks a turn: %r" % done.stderr[-300:])
        return done

    def log_lines(self):
        with open(self.log, encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in handle if line.strip()]

    def counts(self):
        return [int(line.split(" ", 4)[3]) for line in self.log_lines()]

    def test_one_launch_then_its_completion_logs_one_then_zero(self):
        self.append(launch_line())
        self.run_hook()
        self.assertEqual(self.counts(), [1], self.log_lines())
        self.assertIn(DESCRIPTION, self.log_lines()[0])
        self.append(completion_line())
        self.run_hook()
        self.assertEqual(self.counts(), [1, 0], self.log_lines())

    def test_the_state_file_keeps_the_offset_so_a_turn_reads_only_new_bytes(self):
        self.append(launch_line())
        self.run_hook()
        state_file = os.path.join(self.state, "deadbeef1234.json")
        with open(state_file, encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(state["offset"], os.path.getsize(self.transcript), state)
        self.assertEqual(list(state["live"]), [AGENT_ID], state)
        # A second turn with nothing new keeps the count: the live set lives in the state file.
        self.run_hook()
        self.assertEqual(self.counts(), [1, 1], self.log_lines())

    def test_a_transcript_rewritten_in_place_resets_the_state(self):
        self.append(launch_line())
        self.run_hook()
        with open(self.transcript, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(compact({"type": "user", "message": {"content": "a fresh file"}}))
        self.run_hook()
        self.assertEqual(self.counts(), [1, 0], self.log_lines())

    def test_a_blocked_turn_and_a_missing_transcript_log_nothing(self):
        self.append(launch_line())
        env = os.environ.copy()
        env["CLAUDE_INFLIGHT_LOG"] = self.log
        env["CLAUDE_INFLIGHT_STATE_DIR"] = self.state
        for payload in (
            {"session_id": "deadbeef1234", "transcript_path": self.transcript, "stop_hook_active": True},
            {"session_id": "deadbeef1234", "transcript_path": os.path.join(self.tmp, "gone.jsonl")},
            {},
        ):
            done = subprocess.run(
                [sys.executable, HOOK], input=json.dumps(payload).encode("utf-8"),
                capture_output=True, env=env, cwd=self.tmp, timeout=60,
            )
            self.assertEqual(done.returncode, 0, done.stderr[-300:])
        self.assertFalse(os.path.isfile(self.log), "nothing was logged")

    def test_the_reader_reports_the_same_counts_from_the_transcript(self):
        self.append(launch_line())
        out = self.read(["--transcript", self.transcript])
        self.assertIn("agents in flight: 1", out)
        self.assertIn(DESCRIPTION, out)
        self.append(completion_line())
        out = self.read(["--transcript", self.transcript])
        self.assertIn("agents in flight: 0", out)

    def test_the_reader_reads_the_hook_log(self):
        self.append(launch_line())
        self.run_hook()
        self.append(completion_line())
        self.run_hook()
        out = self.read(["--log", "--log-file", self.log, "--session", "deadbeef"])
        self.assertIn("2 entries", out)
        self.assertIn("live=0", out)

    def read(self, args):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        done = subprocess.run(
            [sys.executable, READER] + args, capture_output=True, env=env, cwd=self.tmp, timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        return done.stdout.decode("utf-8", "replace")


if __name__ == "__main__":
    unittest.main(verbosity=2)
