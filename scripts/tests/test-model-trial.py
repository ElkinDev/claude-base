"""Pins for scripts/model-trial.py: the model split, the dedupe by message id, the lane and review counts, the
price table and the refusals.

    python scripts/tests/test-model-trial.py
    MODEL_TRIAL_PY=<path> python scripts/tests/test-model-trial.py      # against a staged copy

Every run reads a fixture projects folder and a fixture reviews folder in a temp folder, both passed by argument.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("MODEL_TRIAL_PY") or os.path.join(os.path.dirname(HERE), "model-trial.py")
CHEAP, STRONG = "claude-sonnet-5-5", "claude-opus-5-5"


class ModelTrialTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="model-trial-")
        self.projects = os.path.join(self.tmp, "projects")
        self.reviews = os.path.join(self.tmp, "reviews")
        os.makedirs(self.reviews)
        self.n = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def agent(self, kind, description, turns, when="2026-09-20T15:00:00Z", project="proj-a"):
        """One agent: meta.json plus a jsonl of assistant turns (model, message id, output tokens)."""
        self.n += 1
        folder = os.path.join(self.projects, project, "session-%d" % self.n, "subagents")
        os.makedirs(folder, exist_ok=True)
        stem = os.path.join(folder, "agent-%d" % self.n)
        with open(stem + ".meta.json", "w", encoding="utf-8") as h:
            json.dump({"agentType": kind, "description": description}, h)
        with open(stem + ".jsonl", "w", encoding="utf-8") as h:
            for model, mid, out in turns:
                h.write(json.dumps({"type": "assistant", "timestamp": when, "message": {
                    "id": mid, "model": model, "usage": {
                        "input_tokens": 1000000, "output_tokens": out, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0}}}) + "\n")

    def review(self, name, first_line):
        with open(os.path.join(self.reviews, name), "w", encoding="utf-8") as h:
            h.write(first_line + "\n\nbody\n")

    def run_on(self, *args):
        argv = [sys.executable, SCRIPT, "--since", "2026-09-20", "--until", "2026-09-20", "--projects", self.projects,
                "--reviews", self.reviews] + list(args)
        done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout + done.stderr

    def test_agents_split_by_model_priced_per_lane_with_blocked_pure_lanes(self):
        self.agent("implementer-light", "Lane abcd fix the thing", [(CHEAP, "m1", 0)])
        self.agent("implementer-light", "Lane efgh fix another", [(STRONG, "m2", 0)])
        self.agent("implementer", "Lane efgh the heavy half", [(STRONG, "m3", 0)])
        self.review("abcd-2026-09-20.md", "BLOCK")
        self.review("efgh-r1-2026-09-20.md", "# Review\nCLEAR")
        code, out = self.run_on()
        self.assertEqual(0, code, out)
        cheap = next(ln for ln in out.splitlines() if ln.startswith("implementer-light " + CHEAP))
        self.assertIn("agents 1, lanes 1", cheap)
        self.assertIn("cost $2.00", cheap)
        self.assertIn("blocked lanes 1 of 1 (1.00)", cheap)
        strong = next(ln for ln in out.splitlines() if ln.startswith("implementer-light " + STRONG))
        self.assertIn("pure lanes 0", strong)

    def test_a_message_id_seen_twice_is_counted_once_and_a_mixed_agent_is_not_priced(self):
        self.agent("implementer-light", "Lane abcd one", [(CHEAP, "m1", 10), (CHEAP, "m1", 20)])
        self.agent("implementer-light", "Lane abcd two", [(CHEAP, "m2", 0), (STRONG, "m3", 0)])
        self.review("abcd-2026-09-20.md", "CLEAR")
        code, out = self.run_on()
        self.assertEqual(0, code, out)
        self.assertIn("out 20;", out)
        self.assertIn("mixed-model agents 1 (not priced)", out)

    def test_a_price_row_is_added_and_a_model_without_one_is_not_priced(self):
        self.agent("implementer-light", "Lane abcd one", [("claude-other-1", "m1", 0)])
        self.review("abcd-2026-09-20.md", "CLEAR")
        code, out = self.run_on()
        self.assertIn("cost -", out)
        code, out = self.run_on("--price", "claude-other-1=1,0,0,0,0")
        self.assertEqual(0, code, out)
        self.assertIn("cost $1.00", out)

    def test_an_agent_outside_the_window_or_of_another_type_is_not_counted(self):
        self.agent("implementer-light", "Lane abcd one", [(CHEAP, "m1", 0)], when="2026-09-22T15:00:00Z")
        self.agent("reviewer", "Lane abcd review", [(CHEAP, "m2", 0)])
        code, out = self.run_on()
        self.assertEqual(0, code, out)
        self.assertNotIn("implementer-light " + CHEAP, out)
        self.assertIn("meta files 2", out)

    def test_unreadable_meta_files_are_counted_never_thrown(self):
        self.agent("implementer-light", "Lane abcd one", [(CHEAP, "m1", 0)])
        folder = os.path.join(self.projects, "proj-a", "broken", "subagents")
        os.makedirs(folder)
        for name, body in (("a.meta.json", "{not json"), ("b.meta.json", "[1, 2]"), ("c.meta.json", "")):
            with open(os.path.join(folder, name), "w", encoding="utf-8") as h:
                h.write(body)
        code, out = self.run_on()
        self.assertEqual(0, code, out)
        self.assertIn("unreadable 3", out)

    def test_a_bad_first_timestamp_is_counted_unreadable_never_thrown(self):
        self.agent("implementer-light", "Lane abcd one", [(CHEAP, "m1", 0)])
        self.agent("implementer-light", "Lane abce two", [(CHEAP, "m2", 0)], when="x")
        code, out = self.run_on()
        self.assertEqual(0, code, out)
        self.assertIn("unreadable 1", out)

    def test_bad_arguments_and_an_empty_projects_folder_exit_two(self):
        self.agent("implementer-light", "Lane abcd one", [(CHEAP, "m1", 0)])
        for args in (("--since", ""), ("--until", "2026-09-19"), ("--baseline-share", "1"),
                     ("--price", "x=1,2"), ("--price", "=1,2,3,4,5"), ("--price", "m=1,2,3,4,-5")):
            code, out = self.run_on(*args)
            self.assertEqual(2, code, repr(args) + out)
        code, out = self.run_on("--reviews", os.path.join(self.tmp, "no-such-folder"))
        self.assertEqual(2, code, out)
        self.assertIn("no reviews folder", out)
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        code, out = self.run_on("--projects", empty)
        self.assertEqual(2, code, out)
        self.assertIn("no meta.json", out)


if __name__ == "__main__":
    unittest.main()
