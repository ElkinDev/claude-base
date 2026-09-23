"""Pins for scripts/cold-rewrites.py, on fixture transcripts in a temp projects folder (COLD_REWRITES_ROOT).

    python scripts/tests/test-cold-rewrites.py
"""
import json, os, shutil, subprocess, sys, tempfile, unittest
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("COLD_REWRITES_PY") or os.path.join(HERE, "..", "cold-rewrites.py")

# a local window of 2026-01-10 10:00 to 12:00; stamps are written in UTC from local times, as the harness writes them
def stamp(local_hhmm, day="2026-01-10"):
    t = dt.datetime.strptime("%s %s" % (day, local_hhmm), "%Y-%m-%d %H:%M").astimezone()
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def turn(mid, cc, when, tool=None):
    content = [{"type": "tool_use", "id": tool[0], "name": "Bash", "input": {"command": tool[1]}}] if tool else []
    return {"type": "assistant", "timestamp": stamp(when), "message": {"id": mid, "content": content,
            "usage": {"cache_creation_input_tokens": cc, "cache_read_input_tokens": 0}}}


def result(tool_id):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "x"}]}}


def said(text):
    return {"type": "user", "message": {"content": text}}


class ColdRewritesTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cold-rw-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def agent(self, name, kind, desc, lines, session="sess0001"):
        d = os.path.join(self.root, session, "subagents")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "agent-%s.jsonl" % name), "w", encoding="utf-8") as f:
            for ln in lines:
                f.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")
        with open(os.path.join(d, "agent-%s.meta.json" % name), "w", encoding="utf-8") as f:
            json.dump({"agentType": kind, "description": desc}, f)

    def run_it(self, *args, root=None):
        env = dict(os.environ, COLD_REWRITES_ROOT=root or self.root)
        r = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True, env=env, timeout=60)
        return r.returncode, r.stdout, r.stderr

    def window(self, *extra):
        return self.run_it("--since", "2026-01-10 10:00", "--until", "2026-01-10 12:00", *extra)

    def test_each_event_files_the_cold_turn_after_it(self):
        self.agent("a", "implementer", "lane x round 2 fix", [
            turn("m1", 200000, "10:00"),                                   # first turn: never counted
            turn("m2", 1000, "10:05", ("t1", "until [ -f g.exit ]; do sleep 30; done")),
            result("t1"), turn("m3", 60000, "10:10"),                      # gate-poll loop
            said("<task-notification>done</task-notification>"), turn("m4", 70000, "10:20"),
            said("the seat writes again"), turn("m5", 80000, "10:30"),     # seat message
            turn("m6", 1000, "10:31", ("t2", "adb -s x shell input tap 1 2")),
            result("t2"), turn("m7", 90000, "10:40"),                      # device script
            turn("m8", 1000, "10:41", ("t3", "git status")),
            result("t3"), turn("m9", 55000, "10:50"),                      # other tool result
            {"type": "user", "isCompactSummary": True, "message": {"content": "summary"}}, turn("m10", 51000, "11:00"),
        ])
        code, out, err = self.window()
        self.assertEqual(code, 0, err)
        for name in ("gate-poll loop", "background notification", "seat message", "device script",
                     "other tool result", "compaction"):
            self.assertRegex(out, r"\n  %s +n +1 " % name)
        self.assertIn("6 events 0.4M write; gate-class 2, 0.1M", out)

    def test_duplicates_small_turns_and_turns_outside_the_window_do_not_count(self):
        self.agent("b", "implementer-light", "lane y", [
            turn("m1", 100, "09:00"),
            said("<task-notification>a</task-notification>"), turn("m2", 60000, "10:10"),
            turn("m2", 60000, "10:10"),                                   # the same message id again
            said("<task-notification>b</task-notification>"), turn("m3", 50000, "10:20"),  # 50,000 is not over
            said("<task-notification>c</task-notification>"), turn("m4", 60000, "12:01"),  # after the window
            said("<task-notification>d</task-notification>"), turn("m5", 60000, "12:00"),  # the last minute counts
        ])
        code, out, _ = self.window("--row")
        self.assertEqual(code, 0)
        self.assertIn(": 2 events 0.1M write; gate-class 2, 0.1M;", out)

    def test_fix_launches_started_in_the_window(self):
        self.agent("f1", "implementer", "rstl round 2 fix", [turn("m1", 100000, "10:00"), turn("m2", 20000, "10:01")])
        self.agent("f2", "implementer-light", "qr3b delta", [turn("m1", 300000, "11:00")])
        self.agent("f3", "reviewer", "rstl r2 review", [turn("m1", 999999, "11:00")])        # not an implementer
        self.agent("f4", "implementer", "new lane", [turn("m1", 999999, "11:00")])           # not a fix round
        self.agent("f5", "implementer", "late fix", [turn("m1", 999999, "13:00")])           # started after
        code, out, _ = self.window("--row")
        self.assertEqual(code, 0)
        self.assertIn("fix-round launches 2, median write 210.0k", out)

    def test_malformed_lines_and_a_missing_meta_are_skipped(self):
        self.agent("g", "implementer", "x", ["{not json", "[1, 2]", turn("m1", 1, "10:00"),
                                              said("<task-notification>x</task-notification>"), turn("m2", 60000, "10:10")])
        os.makedirs(os.path.join(self.root, "sess0002", "subagents"))
        with open(os.path.join(self.root, "sess0002", "subagents", "agent-nometa.jsonl"), "w") as f:
            f.write(json.dumps(turn("m1", 1, "10:00")) + "\n")
        code, out, err = self.window("--row")
        self.assertEqual(code, 0, err)
        self.assertIn(": 1 events", out)

    def test_a_transcript_that_cannot_be_opened_is_said_on_stderr(self):
        self.agent("g", "implementer", "x", [turn("m1", 1, "10:00"),
                                              said("<task-notification>x</task-notification>"), turn("m2", 60000, "10:10")])
        d = os.path.join(self.root, "sess0003", "subagents")
        os.makedirs(os.path.join(d, "agent-dir.jsonl"))                   # a folder where a transcript belongs
        with open(os.path.join(d, "agent-dir.meta.json"), "w", encoding="utf-8") as f:
            json.dump({"agentType": "implementer", "description": "x"}, f)
        code, out, err = self.window("--row")
        self.assertEqual(code, 0, err)
        self.assertIn(": 1 events", out)
        self.assertIn("1 subagent transcript(s) or their meta could not be read", err)
        # a meta that is there and cannot be read hides its transcript too (review f6f7 note 3)
        with open(os.path.join(d, "agent-m.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(turn("m1", 1, "10:00")) + "\n")
        os.makedirs(os.path.join(d, "agent-m.meta.json"))
        self.assertIn("2 subagent transcript(s) or their meta could not be read", self.window("--row")[2])

    def test_a_stamp_with_no_offset_leaves_its_turn_out_and_the_run_reads(self):
        naive = turn("m3", 60000, "10:20")
        naive["timestamp"] = naive["timestamp"].rstrip("Z")                # parses, carries no offset
        self.agent("n", "implementer", "x", [turn("m1", 1, "10:00"),
                                              said("<task-notification>a</task-notification>"), turn("m2", 60000, "10:10"),
                                              said("<task-notification>b</task-notification>"), naive])
        code, out, err = self.window("--row")
        self.assertEqual(code, 0, err)
        self.assertIn(": 1 events", out)

    def test_a_root_whose_transcripts_all_lack_their_meta_is_exit_2(self):
        d = os.path.join(self.root, "sess0003", "subagents")
        os.makedirs(d)
        with open(os.path.join(d, "agent-x.jsonl"), "w") as f:
            f.write(json.dumps(turn("m1", 1, "10:00")) + "\n" + json.dumps(turn("m2", 60000, "10:10")) + "\n")
        with open(os.path.join(d, "agent-y.meta.json"), "w") as f:
            f.write("[1, 2]")                                              # a sidecar that is not an object
        with open(os.path.join(d, "agent-y.jsonl"), "w") as f:
            f.write(json.dumps(turn("m1", 1, "10:00")) + "\n")
        code, out, err = self.window("--row")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no readable subagent transcript", err)

    def test_a_projects_directory_root_reads_every_project(self):
        cold = [turn("m1", 1, "10:00"), said("<task-notification>x</task-notification>"), turn("m2", 60000, "10:10")]
        self.agent("p", "implementer", "lane p", cold, session=os.path.join("proj-a", "sess0001"))
        self.agent("q", "implementer", "lane q", cold, session=os.path.join("proj-b", "sess0002"))
        code, out, err = self.window("--row")
        self.assertEqual(code, 0, err)
        self.assertIn(": 2 events 0.1M write; gate-class 2, 0.1M;", out)
        code, out, err = self.run_it("--since", "2026-01-10 10:00", "--until", "2026-01-10 12:00", "--row",
                                     root=os.path.join(self.root, "proj-a"))
        self.assertEqual(code, 0, err)
        self.assertIn(": 1 events 0.1M write; gate-class 1, 0.1M;", out)

    def test_a_project_device_pattern_replaces_the_default(self):
        self.agent("d", "implementer", "lane d", [
            turn("m1", 1, "10:00"), turn("m2", 1000, "10:05", ("t1", "bash scripts/phone.sh tap 1 2")),
            result("t1"), turn("m3", 60000, "10:10")])
        code, out, _ = self.window()
        self.assertRegex(out, r"\n  other tool result +n +1 ")
        env = dict(os.environ, COLD_REWRITES_ROOT=self.root, COLD_REWRITES_DEVICE_RE="phone\\.sh")
        r = subprocess.run([sys.executable, SCRIPT, "--since", "2026-01-10 10:00", "--until", "2026-01-10 12:00"],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertRegex(r.stdout, r"\n  device script +n +1 ")

    def test_usage_errors_and_a_wrong_root_are_exit_2(self):
        self.agent("h", "implementer", "x", [turn("m1", 1, "10:00")])
        self.assertEqual(self.run_it("--since", "2026-01-10", "--until", "2026-01-10 12:00")[0], 2)
        self.assertEqual(self.run_it("--since", "  ", "--until", "2026-01-10 12:00")[0], 2)
        self.assertEqual(self.run_it("--since", "2026-01-10 12:00", "--until", "2026-01-10 10:00")[0], 2)
        self.assertEqual(self.run_it("--until", "2026-01-10 12:00")[0], 2)
        empty = tempfile.mkdtemp(prefix="cold-rw-empty-")
        self.addCleanup(shutil.rmtree, empty, True)
        code, out, err = self.run_it("--since", "2026-01-10 10:00", "--until", "2026-01-10 12:00", root=empty)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no readable subagent transcript", err)


if __name__ == "__main__":
    unittest.main(verbosity=1)
