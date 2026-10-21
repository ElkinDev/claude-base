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

    # ------------------------------------------------------------- images
    def image(self, width=1080, height=1920, base64_len=378936):
        dims = {"originalWidth": width, "originalHeight": height,
                "displayWidth": width, "displayHeight": height}
        return {"type": "image",
                "file": {"base64": "A" * base64_len, "type": "image/jpeg",
                         "originalSize": 284200, "dimensions": dims}}

    def test_an_image_read_counts_its_pixels_not_its_base64(self):
        out = self.fire(response=self.image(), tool="Read",
                        transcript="/home/you/.claude/projects/repo/sess/subagents/agent-a451.jsonl")
        self.assertEqual(out, "", "a full-size frame is not a flood")
        self.assertEqual(self.rows()[1][2:], ["Read", str(1080 * 1920 * 4 // 750), "a451"])

    def test_an_image_without_dimensions_is_counted_without_its_base64(self):
        response = self.image()
        del response["file"]["dimensions"]
        out = self.fire(response=response, tool="Read")
        self.assertEqual(out, "")
        self.assertLess(int(self.rows()[1][3]), 200)

    def test_base64_inside_any_result_is_left_out(self):
        response = {"content": [{"kind": "png", "base64": "B" * 90000}], "note": "ok"}
        out = self.fire(response=response, tool="mcp__shot")
        self.assertEqual(out, "")
        self.assertLess(int(self.rows()[1][3]), 200)

    def test_string_dimensions_and_the_original_size_are_read(self):
        response = self.image()
        response["file"]["dimensions"] = {"originalWidth": "1080", "originalHeight": "1920",
                                          "displayWidth": 0, "displayHeight": None}
        self.fire(response=response, tool="Read")
        self.assertEqual(self.rows()[1][3], str(1080 * 1920 * 4 // 750))

    def test_a_malformed_image_result_still_writes_its_row(self):
        shapes = [{"type": "image", "file": "x"},
                  {"type": "image", "file": {"dimensions": [1, 2]}},
                  {"type": "image", "file": {"dimensions": {"displayWidth": float("inf"),
                                                            "displayHeight": 10}}}]
        for response in shapes:
            self.assertEqual(self.fire(response=response, tool="Read"), "")
        self.assertEqual(len(self.rows()), 1 + len(shapes), "every call keeps its ledger row")

    def test_a_huge_image_still_alarms(self):
        out = self.fire(response=self.image(width=8000, height=6000), tool="Read")
        self.assertIn("Read", json.loads(out)["systemMessage"])


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    unittest.main(verbosity=2)
