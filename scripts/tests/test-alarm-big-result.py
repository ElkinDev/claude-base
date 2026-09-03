#!/usr/bin/env python3
"""Tests for claude/hooks/alarm-big-result.py: the ledger row it appends, and the agent column
that says whose window a tool result landed in.

Run:
    python test-alarm-big-result.py

The hook runs the way the harness runs it, as a subprocess with the payload JSON on stdin and
CLAUDE_LEDGER_DIR pointed at a temp folder, so the ledger the owner reads is never touched. The
PATH the cases hand it carries no `herdr`, so no notification is fired at a real desktop.
"""
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ALARM = os.path.join(ROOT, "claude", "hooks", "alarm-big-result.py")
OLD_HEADER = "time,session_id,tool_name,chars"
MINIMAL_PATH = os.pathsep.join([
    os.path.dirname(sys.executable),
    os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32"),
])


def run_alarm(payload, ledger_dir):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["CLAUDE_LEDGER_DIR"] = ledger_dir
    env["PATH"] = MINIMAL_PATH
    process = subprocess.run(
        [sys.executable, ALARM],
        input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return (process.returncode,
            process.stdout.decode("utf-8", "replace"),
            process.stderr.decode("utf-8", "replace"))


class AlarmTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNone(shutil.which("herdr", path=MINIMAL_PATH),
                          "the test PATH must not carry herdr")
        self.tmp = tempfile.mkdtemp(prefix="alarm-big-result-")
        self.ledger = os.path.join(self.tmp, "ledger")
        self.csv = os.path.join(self.ledger, "tool-sizes.csv")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def payload(self, response="x" * 40, transcript="MISSING", tool="Grep"):
        data = {"tool_name": tool, "session_id": "sess-0001", "tool_response": response}
        if transcript != "MISSING":
            data["transcript_path"] = transcript
        return data

    def fire(self, **kwargs):
        code, out, err = run_alarm(self.payload(**kwargs), self.ledger)
        self.assertEqual(code, 0, err)
        return out

    def rows(self):
        with open(self.csv, encoding="utf-8", newline="") as handle:
            return [row for row in csv.reader(handle) if row]

    def lines(self):
        with open(self.csv, encoding="utf-8", newline="") as handle:
            return [line for line in handle.read().splitlines() if line]

    # ------------------------------------------------------------- the column
    def test_a_new_file_gets_the_five_column_header(self):
        self.fire(transcript=os.path.join(self.tmp, "projects", "C--Repo", "sess.jsonl"))
        self.assertEqual(self.rows()[0], ["time", "session_id", "tool_name", "chars", "agent"])

    def test_a_main_transcript_writes_main(self):
        self.fire(transcript="C:\\Users\\you\\.claude\\projects\\C--Repo\\sess-0001.jsonl")
        row = self.rows()[1]
        self.assertEqual(row[2:], ["Grep", "40", "main"])

    def test_a_subagents_transcript_writes_the_agent_id(self):
        self.fire(transcript="C:\\Users\\you\\.claude\\projects\\C--Repo\\subagents\\agent-9f3c21.jsonl")
        self.assertEqual(self.rows()[1][4], "9f3c21")

    def test_a_posix_subagents_transcript_writes_the_agent_id(self):
        self.fire(transcript="/home/you/.claude/projects/repo/subagents/agent-abc123.jsonl")
        self.assertEqual(self.rows()[1][4], "abc123")

    def test_a_missing_transcript_field_writes_an_empty_value(self):
        self.fire()
        self.assertEqual(self.rows()[1][4], "")
        self.assertTrue(self.lines()[1].endswith(",Grep,40,"), self.lines()[1])

    def test_an_empty_transcript_field_writes_an_empty_value(self):
        self.fire(transcript="")
        self.assertEqual(self.rows()[1][4], "")

    # ------------------------------------------------------------- the file already there
    def test_a_four_column_file_keeps_its_header_and_takes_a_five_column_row(self):
        os.makedirs(self.ledger, exist_ok=True)
        with open(self.csv, "w", encoding="utf-8", newline="") as handle:
            handle.write(OLD_HEADER + "\n2026-09-02T10:00:00,sess-old,Read,1234\n")
        self.fire(transcript="/home/you/.claude/projects/repo/subagents/agent-abc123.jsonl")
        lines = self.lines()
        self.assertEqual(lines[0], OLD_HEADER, "an existing header is never rewritten")
        self.assertEqual(len(lines), 3)
        self.assertEqual(self.rows()[1], ["2026-09-02T10:00:00", "sess-old", "Read", "1234"])
        self.assertEqual(self.rows()[2][2:], ["Grep", "40", "abc123"])

    # ------------------------------------------------------------- nothing else moved
    def test_a_small_result_is_only_logged(self):
        out = self.fire(transcript="/home/you/.claude/projects/repo/sess.jsonl")
        self.assertEqual(out, "", "below the threshold nothing reaches the context")

    def test_a_big_result_still_alarms_and_still_logs(self):
        response = "gradle noise nobody reads\n" * 2600
        out = self.fire(response=response,
                        transcript="/home/you/.claude/projects/repo/sess.jsonl",
                        tool="Bash")
        message = json.loads(out)["systemMessage"]
        self.assertIn("Bash", message)
        self.assertIn(str(len(response)), message)
        self.assertEqual(self.rows()[1][2:], ["Bash", str(len(response)), "main"])


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    unittest.main(verbosity=2)
