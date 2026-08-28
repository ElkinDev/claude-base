"""Tests for claude/merge-settings.py: python scripts/tests/test-merge-settings.py"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "..", "..", "claude", "merge-settings.py")
spec = importlib.util.spec_from_file_location("merge_settings", PATH)
merger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merger)


class MergeTest(unittest.TestCase):
    def test_source_wins_except_for_profile_keys(self):
        source = {"model": "claude-fable-5", "hooks": {"PreCompact": [{"hooks": [{"command": "a"}]}]}, "statusLine": {"type": "command"}}
        dest = {"model": "opus", "skipDangerousModePermissionPrompt": True, "hooks": {"SessionStart": [{"hooks": [{"command": "s"}]}]}}
        out, changes = merger.merge(source, dest)
        self.assertEqual(out["model"], "opus")  # the account's own choice survives the sync
        self.assertTrue(out["skipDangerousModePermissionPrompt"])  # dest-only keys are kept
        self.assertEqual(sorted(out["hooks"]), ["PreCompact", "SessionStart"])  # dicts merge deeply
        self.assertEqual(out["statusLine"], {"type": "command"})
        self.assertEqual(changes, 2)
        self.assertEqual(dest["hooks"], {"SessionStart": [{"hooks": [{"command": "s"}]}]})  # not mutated

    def test_model_is_taken_when_the_profile_has_none(self):
        out, changes = merger.merge({"model": "claude-fable-5"}, {})
        self.assertEqual(out["model"], "claude-fable-5")
        self.assertEqual(changes, 1)

    def test_lists_are_replaced_whole_and_nested_model_is_ordinary(self):
        source = {"hooks": {"Stop": [{"hooks": [{"command": "new"}]}]}, "env": {"model": "x"}}
        dest = {"hooks": {"Stop": [{"hooks": [{"command": "old"}, {"command": "older"}]}]}, "env": {"model": "y"}}
        out, changes = merger.merge(source, dest)
        self.assertEqual(out["hooks"]["Stop"], [{"hooks": [{"command": "new"}]}])
        self.assertEqual(out["env"]["model"], "x")  # only top-level keys are profile keys
        self.assertEqual(changes, 2)

    def test_nothing_to_do_reports_zero_changes(self):
        out, changes = merger.merge({"a": 1}, {"a": 1, "model": "opus"})
        self.assertEqual(changes, 0)
        self.assertEqual(out, {"a": 1, "model": "opus"})


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1).result.wasSuccessful() is False)
