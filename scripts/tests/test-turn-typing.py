"""Pins for scripts/turn-typing.py on a fixture transcript, lanes folder and profile in a temp folder.

    python scripts/tests/test-turn-typing.py
"""
import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("TURN_TYPING_PY") or os.path.join(HERE, "..", "turn-typing.py")


def load():
    spec = importlib.util.spec_from_file_location("turn_typing", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TT = load()
DAY = "2026-01-10"


def utc_of(local_hhmm, day=DAY):
    """The UTC stamp the harness writes for a local time of the day."""
    t = dt.datetime.strptime("%s %s" % (day, local_hhmm), "%Y-%m-%d %H:%M").astimezone()
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def record(mid, local_hhmm, tools, day=DAY, sidechain=False):
    content = [{"type": "tool_use", "id": "%s-%d" % (mid, k), "name": n, "input": i} for k, (n, i) in enumerate(tools)]
    return {"type": "assistant", "timestamp": utc_of(local_hhmm, day), "isSidechain": sidechain,
            "message": {"id": mid, "content": content}}


class CategoryTest(unittest.TestCase):
    def test_register_shapes(self):
        self.assertEqual(TT.bcat('bash scripts/row.sh decision "t" "p"'), "register-row-only")
        self.assertEqual(TT.bcat('git commit -m x && bash scripts/row.sh decision "t" "p"'), "register-row-with-action")
        self.assertEqual(TT.bcat('printf "%s" x >> /srv/ev/rulings.md'), "register-row-with-action")
        self.assertEqual(TT.bcat("grep -n x /srv/ev/rulings.md"), "register-read")

    def test_other_categories(self):
        self.assertEqual(TT.bcat("bash scripts/lane-gate.sh /w/x"), "gate-launch")
        self.assertEqual(TT.bcat("tail -3 build/lockrun/x.log"), "gate-or-log-read")
        self.assertEqual(TT.bcat("git status --short"), "git")
        self.assertEqual(TT.bcat("sed -n 1,5p lanes/x.md"), "report-or-brief-read")
        self.assertEqual(TT.bcat("herdr pane list"), "device-or-pane")
        self.assertEqual(TT.bcat("python x.py"), "script")
        self.assertEqual(TT.bcat("ls"), "other")

    def test_the_round_pattern_keeps_lanes_and_drops_parts_rounds_and_sessions(self):
        kept = [n for n in ("qr3b-2026-01-10", "qr3b-r2-2026-01-10", "rcpu-round4-2026-01-10", "x-part2-2026-01-10",
                            "phone-session-2026-01-10a", "spdk-fix1-2026-01-10") if not TT.LANE_ROUND_RE.search(n)]
        self.assertEqual(kept, ["qr3b-2026-01-10", "spdk-fix1-2026-01-10"])


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="turn typing ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.profile = os.path.join(self.tmp, "profile")
        self.project = os.path.join(self.profile, "projects", "some-project")
        os.makedirs(self.project)
        self.jsonl = os.path.join(self.project, "abcdef12-0000.jsonl")
        records = [
            record("m1", "09:00", [("Bash", {"command": "git status"}), ("Write", {"file_path": "/e/briefs/b.md", "content": "x" * 10})]),
            record("m1", "09:00", [("Bash", {"command": "git status"}), ("Write", {"file_path": "/e/briefs/b.md", "content": "x" * 10})]),
            record("m2", "23:30", [("Agent", {"prompt": "p" * 20})]),
            record("m3", "10:00", [("Bash", {"command": "ls"})], sidechain=True),
            record("m4", "10:00", [("Bash", {"command": "ls"})], day="2026-01-11"),
        ]
        with open(self.jsonl, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
            f.write("not json\n")
        self.evidence = os.path.join(self.tmp, "evidence")
        os.makedirs(os.path.join(self.evidence, "lanes"))
        for name in ("qr3b-2026-01-10.md", "qr3b-r2-2026-01-10.md", "other-2026-01-09.md"):
            open(os.path.join(self.evidence, "lanes", name), "w").close()

    def run_it(self, *args):
        r = subprocess.run([sys.executable, SCRIPT, "--day", DAY, "--profile", self.profile,
                            "--evidence-root", self.evidence, "--scratch-root", os.path.join(self.tmp, "scratch")] + list(args),
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout, r.stderr

    def test_the_report_counts_the_local_day_main_thread_once(self):
        code, out, err = self.run_it("--jsonl", self.jsonl)
        self.assertEqual(code, 0, err)
        # records are lines (the repeated m1 line counts twice); a tool use counts once by its ids
        self.assertIn("assistant records 3, tool uses 3", out)
        self.assertIn("Write by target: brief 1", out)
        self.assertIn("Agent prompts: 1", out)

    def test_the_session_prefix_is_found_in_any_project_or_the_one_named(self):
        self.assertEqual(self.run_it("--session", "abcdef12")[0], 0)
        self.assertEqual(self.run_it("--session", "abcdef12", "--project", "some-project")[0], 0)
        self.assertNotEqual(self.run_it("--session", "abcdef12", "--project", "another")[0], 0)

    def test_the_ledger_line_counts_delivered_lanes_without_rounds(self):
        code, out, err = self.run_it("--jsonl", self.jsonl, "--ledger")
        self.assertEqual(code, 0, err)
        self.assertIn("main-thread tool uses 3, lanes delivered 1, per lane 3.0", out)

    def test_pick_from_with_no_qualifying_transcript_is_exit_3(self):
        code, out, _ = self.run_it("--pick-from", self.project, "--ledger")
        self.assertEqual(code, 3)
        self.assertIn("no transcript with 50 or more", out)

    def test_a_malformed_day_is_a_usage_error(self):
        self.assertEqual(subprocess.run([sys.executable, SCRIPT, "--day", "2026-1", "--jsonl", self.jsonl],
                                        capture_output=True, timeout=60).returncode, 2)


if __name__ == "__main__":
    unittest.main()
