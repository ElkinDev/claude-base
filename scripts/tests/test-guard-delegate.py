#!/usr/bin/env python3
"""Tests for claude/hooks/guard-delegate.py, the PreToolUse hook on the Agent tool.

    python scripts/tests/test-guard-delegate.py

The reviewer and the analyst are read-only by construction: they return a verdict or an analysis
and never change a file. Letting them reach the bulk reader means putting the Agent tool on the
reviewer's allowlist, and an allowlist has no shape: with Agent on it the reviewer can launch an
implementer, which writes. A sentence in the definition asking it not to is prose, and prose is not
a boundary. This hook is the boundary, and these tests are what say so.

The rule is narrow on purpose. It fires only when the caller is one of the read-only agents, which
the harness names in the payload as agent_type, and it allows exactly one target. A main session
carries no agent_type and is never touched, because a person driving a session is not a read-only
agent and stopping them from delegating would break the kit for everyone. Any other tool is ignored
outright, and a payload the hook cannot parse leaves the call alone: a guard that blocks work when
it breaks is worse than no guard.

The hook runs the way the harness runs it, as a subprocess with the payload JSON on stdin.
"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
HOOKS = os.path.join(ROOT, "claude", "hooks")
GUARD = os.path.join(HOOKS, "guard-delegate.py")
SETTINGS = os.path.join(ROOT, "claude", "settings.json")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The two lists come from the hook itself, so a copy here cannot keep passing after the hook moved.
guard = load("guard_delegate", GUARD)


def run_guard(payload):
    """The hook as the harness runs it. A string payload is sent verbatim, so a malformed body
    can be tested the way it would really arrive."""
    body = payload if isinstance(payload, str) else json.dumps(payload)
    process = subprocess.run(
        [sys.executable, GUARD],
        input=body.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    return process.returncode, process.stdout.decode("utf-8", "replace"), process.stderr.decode("utf-8", "replace")


class GuardDelegateTest(unittest.TestCase):
    def agent_call(self, subagent_type, caller=None, tool="Agent"):
        payload = {
            "tool_name": tool,
            "tool_input": {"subagent_type": subagent_type, "prompt": "one question"},
            "cwd": ROOT,
            "transcript_path": "",
        }
        if caller is not None:
            payload["agent_id"] = "a1b2c3d4"
            payload["agent_type"] = caller
        return payload

    def denial(self, payload):
        code, out, err = run_guard(payload)
        self.assertEqual(code, 0, err)
        self.assertNotEqual(out, "", "expected a denial, got nothing")
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(decision["hookEventName"], "PreToolUse", decision)
        self.assertEqual(decision["permissionDecision"], "deny", decision)
        return decision["permissionDecisionReason"]

    def allowed(self, payload):
        code, out, err = run_guard(payload)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "", "expected no decision, got: %s" % out)

    # ------------------------------------------------------------- the lists the rule reads
    def test_the_callers_and_the_target_are_the_ones_the_definitions_carry(self):
        self.assertEqual(tuple(guard.READ_ONLY_CALLERS), ("reviewer", "analyst"))
        self.assertEqual(guard.ALLOWED_TARGET, "bulk-reader")

    # ------------------------------------------------------------- the rule
    def test_the_reviewer_may_not_launch_an_implementer(self):
        reason = self.denial(self.agent_call("implementer", caller="reviewer"))
        self.assertIn("bulk-reader", reason)
        self.assertIn("implementer", reason)
        self.assertIn("reviewer", reason)

    def test_the_reviewer_may_launch_the_bulk_reader(self):
        self.allowed(self.agent_call("bulk-reader", caller="reviewer"))

    def test_the_analyst_may_not_launch_an_implementer(self):
        self.assertIn("analyst", self.denial(self.agent_call("implementer", caller="analyst")))

    def test_the_analyst_may_launch_the_bulk_reader(self):
        self.allowed(self.agent_call("bulk-reader", caller="analyst"))

    def test_a_read_only_caller_may_not_launch_itself_either(self):
        # The interesting near miss: an agent fanning out into copies of itself is still a fan-out.
        self.denial(self.agent_call("reviewer", caller="reviewer"))

    # ------------------------------------------------------------- who the rule does not reach
    def test_a_main_session_delegates_to_whatever_it_likes(self):
        self.allowed(self.agent_call("implementer"))
        self.allowed(self.agent_call("bulk-reader"))

    def test_another_agent_type_is_not_touched(self):
        self.allowed(self.agent_call("implementer", caller="implementer"))

    def test_another_tool_is_not_touched(self):
        self.allowed(self.agent_call("implementer", caller="reviewer", tool="Read"))
        self.allowed(self.agent_call("implementer", caller="reviewer", tool="Bash"))

    # ------------------------------------------------------------- it never blocks work
    def test_a_malformed_payload_exits_zero_with_no_output(self):
        for body in ("", "{", "not json at all", "[]"):
            code, out, err = run_guard(body)
            self.assertEqual(code, 0, err)
            self.assertEqual(out, "", "a payload the hook cannot read must pass: %r" % body)

    # ------------------------------------------------------------- the field it keys on
    # An absent subagent_type is not an absent delegation. The harness resolves an omitted type
    # to the general-purpose agent, which carries every tool, so a rule that reads a missing
    # field as "nothing to check" hands a read-only agent the widest agent there is. For a
    # caller on the list the field is required, and its absence is a denial like any other.
    def test_a_read_only_caller_must_name_the_target(self):
        for missing in (None, "", "   "):
            payload = self.agent_call("implementer", caller="reviewer")
            if missing is None:
                del payload["tool_input"]["subagent_type"]
            else:
                payload["tool_input"]["subagent_type"] = missing
            reason = self.denial(payload)
            self.assertIn("subagent_type", reason, "the reason must say what is missing")
            self.assertIn("bulk-reader", reason)

    def test_a_null_subagent_type_is_denied_too(self):
        payload = self.agent_call("implementer", caller="analyst")
        payload["tool_input"]["subagent_type"] = None
        self.assertIn("subagent_type", self.denial(payload))

    def test_a_main_session_without_a_target_is_still_not_touched(self):
        # No agent_type means no rule, whatever the tool input looks like.
        payload = self.agent_call("implementer")
        del payload["tool_input"]["subagent_type"]
        self.allowed(payload)
        payload = self.agent_call("implementer")
        payload["tool_input"]["subagent_type"] = None
        self.allowed(payload)


class SettingsTemplate(unittest.TestCase):
    """A hook nobody wires is a file, not a guard."""

    def setUp(self):
        with open(SETTINGS, encoding="utf-8") as handle:
            self.settings = json.load(handle)
        self.entries = self.settings["hooks"]["PreToolUse"]

    def test_the_agent_matcher_points_at_the_hook(self):
        matched = [entry for entry in self.entries if entry.get("matcher") == "Agent"]
        self.assertEqual(1, len(matched), "expected exactly one Agent matcher")
        commands = [hook.get("command", "") for hook in matched[0]["hooks"]]
        self.assertTrue(
            any("guard-delegate.py" in command for command in commands),
            "the Agent matcher does not run guard-delegate.py: %s" % commands,
        )

    def test_it_keeps_the_shape_of_the_entries_beside_it(self):
        agent = [entry for entry in self.entries if entry.get("matcher") == "Agent"][0]["hooks"][0]
        read = [entry for entry in self.entries if entry.get("matcher") == "Read"][0]["hooks"][0]
        self.assertEqual(read["type"], agent["type"])
        self.assertEqual(read["timeout"], agent["timeout"])

    def test_the_read_guard_entries_are_still_there(self):
        matchers = [entry.get("matcher") for entry in self.entries]
        for matcher in ("Read", "Bash", "PowerShell"):
            self.assertIn(matcher, matchers)


if __name__ == "__main__":
    unittest.main(verbosity=2)
