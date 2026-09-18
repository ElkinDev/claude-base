"""Tests for scripts/tool-errors.py: the producer gate on the rule file and the signature of a failure.

    python scripts/tests/test-tool-errors.py

The rules are what keep a read of an old failure from counting as a new one: a rule fires only when the
command that produced the result holds its producer, so a session that cats a report full of past errors
adds nothing to the day's count. Every case builds its own rule file and its own transcript in a temporary
folder, so the shipped rules can change without breaking the cases that test the mechanism.
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
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, "scripts", "tool-errors.py")
RULES = os.path.join(ROOT, "scripts", "tool-errors.rules")
TAB = chr(9)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool_errors = load("tool_errors", SCRIPT)


class ToolErrorsRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tool-errors-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rules(self, *lines):
        path = os.path.join(self.tmp, "rules")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
        return tool_errors.load_rules(path)

    def test_a_rule_counts_only_when_the_command_holds_its_producer(self):
        rules = self.rules("row.sh" + TAB + r"ROW REFUSED: [a-z ]+")
        text = "ROW REFUSED: empty text"
        self.assertEqual(["ROW REFUSED: empty text"], tool_errors.hits_for(text, "bash row.sh decision ''", rules, False))
        self.assertEqual([], tool_errors.hits_for(text, "cat lanes/report.md", rules, False),
                         "a read that quotes an old failure is not a new failure")

    def test_a_star_producer_matches_any_command(self):
        rules = self.rules("*" + TAB + r"fails the launch check at tier \d")
        text = "The brief fails the launch check at tier 1: no Report section."
        self.assertEqual(1, len(tool_errors.hits_for(text, "any command at all", rules, False)))

    def test_a_bang_in_the_producer_excludes(self):
        rules = self.rules("row.sh!ROW_REGISTER" + TAB + r"ROW ok text \d+/\d+ CUT \d+ chars")
        text = "ROW ok text 500/400 CUT 120 chars, dropped: 'the tail'"
        self.assertEqual(1, len(tool_errors.hits_for(text, "bash scripts/row.sh decision '...'", rules, False)))
        self.assertEqual([], tool_errors.hits_for(text, "ROW_REGISTER=/tmp/dry.md bash scripts/row.sh decision '...'", rules, False),
                         "a dry run is not a register row")

    def test_an_is_error_result_with_no_rule_takes_the_last_error_looking_line(self):
        rules = self.rules("# no rules at all")
        text = "building\nsome ordinary output\nerror: cannot open 'C:/repo/thing.txt'\nExit code 1"
        got = tool_errors.hits_for(text, "gradle build", rules, True)
        self.assertEqual(1, len(got), got)
        self.assertIn("error: cannot open", got[0])
        self.assertNotIn("Exit code", got[0], "the exit header is never the signature")
        self.assertIn("<path>", got[0], "a path is collapsed so repeats of one defect group together")

    def test_a_result_with_no_rule_and_no_error_flag_counts_nothing(self):
        rules = self.rules("row.sh" + TAB + "ROW REFUSED")
        self.assertEqual([], tool_errors.hits_for("all fine here", "bash row.sh decision 'x'", rules, False))

    def test_the_signature_falls_back_to_the_last_non_empty_line(self):
        self.assertEqual("just a line", tool_errors.signature("\njust a line\n\nExit code 2\n"))
        self.assertEqual("(empty)", tool_errors.signature("   \n\n"))

    def test_digits_and_ids_are_collapsed_so_repeats_group(self):
        first = tool_errors.normalise("error: task 12 failed for a1b2c3d4e5f6")
        second = tool_errors.normalise("error: task 47 failed for ffeeddccbbaa")
        self.assertEqual(first, second)

    def test_the_shipped_rule_file_parses_and_every_rule_has_a_producer(self):
        rules = tool_errors.load_rules(RULES)
        self.assertTrue(rules, "the kit ships rules")
        for producer, rule in rules:
            self.assertTrue(producer.strip(), "a rule with no producer fires on every command")
            self.assertTrue(rule.pattern.strip())

    def test_the_day_line_counts_calls_and_failures_from_a_transcript(self):
        transcript = os.path.join(self.tmp, "session.jsonl")
        call = {
            "type": "assistant",
            "timestamp": "2026-09-18T10:00:00.000Z",
            "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                     "input": {"command": "bash scripts/row.sh decision ''"}}]},
        }
        result = {
            "type": "user",
            "timestamp": "2026-09-18T10:00:01.000Z",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                     "content": [{"type": "text", "text": "ROW REFUSED: empty text"}]}]},
        }
        with open(transcript, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(call) + "\n" + json.dumps(result) + "\n")
        rules = os.path.join(self.tmp, "shipped.rules")
        with open(rules, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("row.sh" + TAB + "ROW REFUSED: [a-z ]+\n")
        done = subprocess.run(
            [sys.executable, SCRIPT, "--file", transcript, "--row"],
            capture_output=True, cwd=self.tmp, timeout=60,
            env=dict(os.environ, PYTHONIOENCODING="utf-8", CLAUDE_TOOL_ERRORS_RULES=rules),
        )
        self.assertEqual(0, done.returncode, done.stderr[-400:])
        out = done.stdout.decode("utf-8", "replace")
        self.assertIn("tool-errors 2026-09-18: calls 1, failed 1 (100 pct)", out)
        self.assertIn("ROW REFUSED", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
