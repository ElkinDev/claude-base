"""Pins for scripts/token-shape.py: the shell-call shapes and one --row run on a fixture session in a temp folder.

    python scripts/tests/test-token-shape.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("TOKEN_SHAPE_PY") or os.path.join(HERE, "..", "token-shape.py")


def load():
    spec = importlib.util.spec_from_file_location("token_shape", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TS = load()


def turn(mid, when, cc=0, cr=0, out=10, command=None):
    content = [{"type": "tool_use", "id": "t" + mid, "name": "Bash", "input": {"command": command}}] if command else []
    return {"type": "assistant", "timestamp": when, "message": {"id": mid, "content": content, "usage": {
        "input_tokens": 1, "cache_creation_input_tokens": cc, "cache_read_input_tokens": cr, "output_tokens": out}}}


class ShapeTest(unittest.TestCase):
    def test_a_call_made_only_of_rows_is_register_row_only(self):
        self.assertEqual(TS.shape_of('bash scripts/row.sh decision "a; b" "pane"'), "register-row-only")

    def test_a_row_behind_an_action_is_register_row_with_action(self):
        self.assertEqual(TS.shape_of('git merge x && bash scripts/row.sh decision "t" "pane"'),
                         "register-row-with-action")

    def test_a_heredoc_body_is_text_not_a_command(self):
        cmd = "cat > f <<'EOF'\nit's; bash scripts/row.sh x\nEOF\nbash scripts/row.sh decision \"t\" \"p\""
        self.assertEqual(TS.shape_of(cmd), "register-row-with-action")

    def test_a_register_read_on_any_path_is_register_grep(self):
        self.assertEqual(TS.shape_of("grep -n rcpu /srv/evidence/rulings.md | tail -3"), "register-grep")
        self.assertEqual(TS.shape_of("R=/srv/evidence/rulings.md; tail -5 $R"), "register-grep")

    def test_a_sleeping_loop_on_a_gate_file_is_a_gate_poll(self):
        self.assertEqual(TS.shape_of("until [ -f run.exit ]; do sleep 30; done"), "gate-poll-or-read")
        self.assertEqual(TS.shape_of("while true; do echo; done"), "other")
        self.assertEqual(TS.shape_of(""), "other")


class RowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="token shape ")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_row_counts_the_window_and_a_cold_resume(self):
        session = os.path.join(self.tmp, "sess.jsonl")
        with open(session, "w", encoding="utf-8") as f:
            for d in (turn("m1", "2026-01-10T10:00:00Z", cr=1000, command="bash scripts/row.sh a \"b\" \"c\""),
                      turn("m1", "2026-01-10T10:00:00Z", cr=1000, out=5),  # the same response again
                      turn("m2", "2026-01-10T11:00:00Z", cr=2000),
                      turn("m3", "2026-01-10T13:00:00Z", cr=9999)):  # after the window
                f.write(json.dumps(d) + "\n")
        sub = os.path.join(self.tmp, "sess", "subagents")
        os.makedirs(sub)
        with open(os.path.join(sub, "agent-abcdefgh.jsonl"), "w", encoding="utf-8") as f:
            for d in (turn("s1", "2026-01-10T10:10:00Z", cc=30000), turn("s2", "2026-01-10T10:40:00Z", cc=60000)):
                f.write(json.dumps(d) + "\n")
        with open(os.path.join(sub, "agent-abcdefgh.meta.json"), "w", encoding="utf-8") as f:
            json.dump({"agentType": "implementer", "description": "lane x"}, f)
        r = subprocess.run([sys.executable, SCRIPT, session, "--since", "2026-01-10T09:00:00",
                            "--until", "2026-01-10T12:00:00", "--row"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("in 2 turns", r.stdout)
        self.assertIn("cold resumes 1 (0.1M)", r.stdout)
        self.assertIn("register-row-only shell calls 1 of 1", r.stdout)
        # the same window written with an offset reads the same turns (review f6f7 note 2: it was dropped)
        shifted = subprocess.run([sys.executable, SCRIPT, session, "--since", "2026-01-10T04:00:00-05:00",
                                  "--until", "2026-01-10T07:00:00-05:00", "--row"], capture_output=True, text=True, timeout=60)
        self.assertEqual(shifted.stdout, r.stdout)


class UsageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="token shape ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = os.path.join(self.tmp, "sess.jsonl")
        open(self.session, "w").close()

    def code(self, session, since, until):
        return subprocess.run([sys.executable, SCRIPT, session, "--since", since, "--until", until, "--row"],
                              capture_output=True, text=True, timeout=60).returncode

    def test_a_malformed_or_reversed_window_and_a_missing_transcript_are_usage_errors(self):
        self.assertEqual(self.code(self.session, "2026-1", "2026-01-10T12:00:00"), 2)
        self.assertEqual(self.code(self.session, "2026-01-10T12:00:00", "2026-01-10T09:00:00"), 2)
        self.assertEqual(self.code(os.path.join(self.tmp, "none.jsonl"), "2026-01-10T09:00:00", "2026-01-10T12:00:00"), 2)
        self.assertEqual(self.code(self.tmp, "2026-01-10T09:00:00", "2026-01-10T12:00:00"), 2)
        self.assertEqual(self.code(self.session, "2026-01-10T09:00:00Z", "2026-01-10T12:00:00Z"), 0)


if __name__ == "__main__":
    unittest.main()
