"""Tests for claude/hooks/idle-floor.py, the Stop hook that refuses a turn end of the orchestrator while fewer than
the floor of subagents are in flight.

    python claude/hooks/tests/test-idle-floor.py

Fixture transcripts, state and log live in a temporary folder; IDLE_FLOOR_NOW stands in for the clock."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(HERE), "idle-floor.py")


def noon():
    return datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def launch(i, ts):
    return json.dumps({"type": "user", "timestamp": ts, "toolUseResult": {
        "status": "async_launched", "agentId": "agent%d" % i, "description": "lane %d" % i}}) + "\n"


def ended(i, ts):
    note = "<task-notification><task-id>agent%d</task-id><status>completed</status></task-notification>" % i
    return json.dumps({"type": "user", "timestamp": ts, "message": {"content": note}}) + "\n"


class IdleFloor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.tpath = os.path.join(self.dir, "t.jsonl")
        self.now = noon()
        self.ts = stamp(self.now - 60)

    def tearDown(self):
        self.tmp.cleanup()

    def transcript(self, live, finished=1):
        with open(self.tpath, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "user", "timestamp": self.ts, "message": {"content": "hi"}}) + "\n")
            for i in range(live + finished):
                handle.write(launch(i, self.ts))
            for i in range(finished):
                handle.write(ended(i, self.ts))

    def run_hook(self, payload=None, now=None, role="orchestrator", sid="11111111-aaaa"):
        data = {"session_id": sid, "transcript_path": self.tpath}
        data.update(payload or {})
        env = dict(os.environ, CLAUDE_ROLE=role, IDLE_FLOOR_NOW=str(now or self.now),
                   IDLE_FLOOR_STATE=os.path.join(self.dir, "state"), IDLE_FLOOR_LOG=os.path.join(self.dir, "log"))
        env.pop("IDLE_FLOOR_ROLE", None)
        env.pop("IDLE_FLOOR_FLOOR", None)
        env.pop("IDLE_FLOOR", None)
        env.pop("IDLE_FLOOR_OPEN", None)
        env.pop("IDLE_FLOOR_CLOSE", None)
        env.update(getattr(self, "opening", {}))
        done = subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(data).encode(), env=env,
                              capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 0)
        return done.stdout.decode().strip()

    def test_zero_live_blocks(self):
        self.transcript(0)
        out = json.loads(self.run_hook())
        self.assertEqual(out["decision"], "block")
        self.assertTrue(out["reason"].startswith("0 subagents in flight, under the floor of 3:"))

    def test_two_live_blocks_ended_ones_not_counted(self):
        self.transcript(2, finished=2)
        self.assertIn("2 subagents in flight", json.loads(self.run_hook())["reason"])

    def test_three_live_passes(self):
        self.transcript(3)
        self.assertEqual(self.run_hook(), "")

    def test_stop_hook_active_passes(self):
        self.transcript(0)
        self.assertEqual(self.run_hook({"stop_hook_active": True}), "")

    def test_agent_id_passes(self):
        self.transcript(0)
        self.assertEqual(self.run_hook({"agent_id": "abc"}), "")

    def test_second_stop_inside_ten_minutes_passes(self):
        self.transcript(0)
        self.assertNotEqual(self.run_hook(), "")
        self.assertEqual(self.run_hook(now=self.now + 300), "")
        self.assertNotEqual(self.run_hook(now=self.now + 700), "")

    def test_after_hours_passes(self):
        self.transcript(0)
        late = datetime.now().replace(hour=21, minute=30, second=0, microsecond=0).timestamp()
        self.assertEqual(self.run_hook(now=late), "")
        early = datetime.now().replace(hour=7, minute=30, second=0, microsecond=0).timestamp()
        self.assertEqual(self.run_hook(now=early), "")

    def test_hours_are_configurable(self):
        self.transcript(0)
        late = datetime.now().replace(hour=22, minute=0, second=0, microsecond=0).timestamp()
        self.assertEqual(self.run_hook(now=late), "")
        self.opening = {"IDLE_FLOOR_OPEN": "07:00", "IDLE_FLOOR_CLOSE": "23:00"}
        self.assertNotEqual(self.run_hook(now=late), "")

    def test_malformed_transcript_passes_silently(self):
        with open(self.tpath, "w") as handle:
            handle.write("not json at all\n{broken\n")
        self.assertEqual(self.run_hook(), "")
        with open(self.tpath, "w") as handle:
            handle.write("")
        self.assertEqual(self.run_hook(sid="22222222-aaaa"), "")

    def test_damaged_marker_line_does_not_freeze_offset(self):
        self.transcript(0)
        with open(self.tpath, "a") as handle:
            # the head digest covers the first 4096 bytes, so the fixture must outgrow them to read incrementally
            handle.write(json.dumps({"type": "user", "timestamp": self.ts, "message": {"content": "x" * 5000}}) + chr(10))
            handle.write('{"async_launched": broken' + chr(10))
        self.assertNotEqual(self.run_hook(), "")  # bad line skipped, count still evaluated: 0 live blocks
        with open(self.tpath, "a", encoding="utf-8") as handle:
            for i in range(3):
                handle.write(launch(10 + i, self.ts))
        self.assertEqual(self.run_hook(now=self.now + 700), "")  # later launches are read past the bad row
        with open(os.path.join(self.dir, "log"), encoding="utf-8") as handle:
            self.assertEqual(handle.read().count("damaged-marker-line"), 1)

    def test_unsafe_sid_passes_and_writes_no_file(self):
        self.transcript(0)
        state = os.path.join(self.dir, "state")
        self.assertEqual(self.run_hook(sid="..\\..\\evil"), "")
        self.assertEqual(self.run_hook(sid="../evil"), "")
        self.assertFalse(os.path.exists(state) or os.path.exists(os.path.join(self.dir, "evil.json")))

    def test_other_role_passes(self):
        self.transcript(0)
        self.assertEqual(self.run_hook(role="lane"), "")

    def test_old_agent_no_longer_counts(self):
        old = stamp(self.now - 3 * 3600)
        with open(self.tpath, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "user", "timestamp": old, "message": {"content": "hi"}}) + "\n")
            for i in range(3):
                handle.write(launch(i, old))
        self.assertIn("0 subagents", json.loads(self.run_hook())["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
