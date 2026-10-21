"""Tests for claude/merge-hooks.py, the step that adds the kit's new hooks to a settings.json of the user's.

    python scripts/tests/test-merge-hooks.py

Every case writes its files into a fresh temporary folder and runs the tool the way install.ps1 does,
as a subprocess. No network, no real settings file.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(os.path.dirname(HERE)), "claude", "merge-hooks.py")

KIT = {
    "hooks": {
        "PreToolUse": [
            {"matcher": "Read", "hooks": [{"type": "command", "command": "python \"C:/kit/hooks/guard-read.py\"", "timeout": 10}]},
            {"matcher": "*", "hooks": [{"type": "command", "command": "python \"C:/kit/hooks/turn-budget.py\"", "timeout": 10}]},
        ],
        "Stop": [{"hooks": [{"type": "command", "command": "python \"C:/kit/hooks/landing.py\""}]}],
        "SubagentStop": [{"matcher": "*", "hooks": [{"type": "command", "command": "python \"C:/kit/hooks/inflight.py\""}]}],
    },
    "statusLine": {"type": "command", "command": "kit status"},
}


def user_settings():
    """A settings.json of the user's: the kit's guard-read and landing hooks, plus things of their own."""
    return {
        "model": "my-model",
        "hooks": {
            "PreToolUse": [
                {"matcher": "Read", "hooks": [{"type": "command", "command": "python \"C:\\kit\\hooks\\guard-read.py\"", "timeout": 10}]},
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "company-guard.exe"}]},
            ],
            "Stop": [{"hooks": [{"type": "command", "command": "python \"C:/kit/hooks/landing.py\""}]}],
        },
        "permissions": {"allow": ["Bash(git status)"]},
    }


class MergeHooks(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="merge-hooks-")
        self.kit = os.path.join(self.base, "kit-settings.json")
        self.settings = os.path.join(self.base, "settings.json")
        with open(self.kit, "w", encoding="utf-8") as handle:
            json.dump(KIT, handle, indent=2)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def write_user(self, data, eol="\n", indent=2, bom=False):
        text = json.dumps(data, indent=indent) + "\n"
        raw = text.replace("\n", eol).encode("utf-8")
        with open(self.settings, "wb") as handle:
            handle.write((b"\xef\xbb\xbf" if bom else b"") + raw)

    def run_tool(self, *extra, kit_b64=False):
        source = ["--kit-b64", base64.b64encode(self.raw(self.kit)).decode("ascii")] if kit_b64 \
            else ["--kit", self.kit]
        done = subprocess.run([sys.executable, "-I", TOOL] + source + [self.settings] + list(extra),
                              capture_output=True, timeout=60)
        return done.returncode, done.stdout.decode("utf-8")

    def raw(self, path=None):
        with open(path or self.settings, "rb") as handle:
            return handle.read()

    def read_user(self):
        with open(self.settings, encoding="utf-8") as handle:
            return json.load(handle)

    def handlers(self, data, event):
        return [(e.get("matcher"), h["command"]) for e in data["hooks"].get(event, []) for h in e["hooks"]]

    def test_the_missing_kit_hooks_are_added_under_their_event_and_matcher(self):
        self.write_user(user_settings())
        code, out = self.run_tool()
        self.assertEqual(0, code, out)
        data = self.read_user()
        self.assertIn(("*", "python \"C:/kit/hooks/turn-budget.py\""), self.handlers(data, "PreToolUse"))
        self.assertEqual([("*", "python \"C:/kit/hooks/inflight.py\"")], self.handlers(data, "SubagentStop"))
        self.assertEqual(2, out.count("added"))
        self.assertIn("PreToolUse[*] python \"C:/kit/hooks/turn-budget.py\"", out)

    def test_nothing_of_the_user_is_removed_or_changed(self):
        self.write_user(user_settings())
        self.run_tool()
        data = self.read_user()
        self.assertEqual("my-model", data["model"])
        self.assertEqual({"allow": ["Bash(git status)"]}, data["permissions"])
        self.assertNotIn("statusLine", data)
        pre = data["hooks"]["PreToolUse"]
        self.assertEqual(user_settings()["hooks"]["PreToolUse"], pre[:2])
        self.assertEqual(user_settings()["hooks"]["Stop"], data["hooks"]["Stop"])

    def test_a_handler_joins_the_users_entry_with_the_same_matcher(self):
        mine = user_settings()
        mine["hooks"]["PreToolUse"].append({"matcher": "*", "hooks": [{"type": "command", "command": "my-audit.exe"}]})
        self.write_user(mine)
        self.run_tool()
        star = [e for e in self.read_user()["hooks"]["PreToolUse"] if e.get("matcher") == "*"]
        self.assertEqual(1, len(star))
        self.assertEqual(["my-audit.exe", "python \"C:/kit/hooks/turn-budget.py\""], [h["command"] for h in star[0]["hooks"]])

    def test_a_second_run_finds_nothing_to_add_and_writes_nothing(self):
        self.write_user(user_settings())
        self.run_tool()
        before = self.raw()
        code, out = self.run_tool()
        self.assertEqual(0, code)
        self.assertIn("up to date", out)
        self.assertEqual(before, self.raw())

    def test_a_file_that_does_not_parse_is_never_touched(self):
        with open(self.settings, "wb") as handle:
            handle.write(b'{"hooks": {"Stop": [ broken')
        code, out = self.run_tool()
        self.assertEqual(1, code)
        self.assertIn("NOTHING TOUCHED", out)
        self.assertEqual(b'{"hooks": {"Stop": [ broken', self.raw())

    def test_hooks_that_are_not_an_object_are_never_touched(self):
        self.write_user({"hooks": ["not", "an", "object"]})
        before = self.raw()
        code, out = self.run_tool()
        self.assertEqual(1, code)
        self.assertEqual(before, self.raw())

    def test_a_file_with_none_of_the_kit_hooks_is_left_alone(self):
        mine = {"model": "x", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "company-guard.exe"}]}]}}
        self.write_user(mine)
        before = self.raw()
        code, out = self.run_tool()
        self.assertEqual(0, code)
        self.assertIn("left alone", out)
        self.assertEqual(before, self.raw())

    def test_crlf_indent_and_no_bom_are_kept(self):
        self.write_user(user_settings(), eol="\r\n", indent=4, bom=True)
        self.run_tool()
        raw = self.raw()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))
        self.assertTrue(raw.endswith(b"}\r\n"))
        self.assertIn(b'\r\n    "model"', raw)

    def test_a_dry_run_names_the_additions_and_writes_nothing(self):
        self.write_user(user_settings())
        before = self.raw()
        code, out = self.run_tool("--dry-run", kit_b64=True)
        self.assertEqual(0, code)
        self.assertEqual(2, out.count("would add"))
        self.assertEqual(before, self.raw())

    def test_a_name_in_the_skip_file_stays_out(self):
        self.write_user(user_settings())
        with open(os.path.join(self.base, "hook-merge-skip.txt"), "w", encoding="utf-8") as handle:
            handle.write("# taken out on purpose\nturn-budget.py\n")
        code, out = self.run_tool()
        self.assertEqual(0, code)
        self.assertIn("skipped", out)
        commands = [c for _, c in self.handlers(self.read_user(), "PreToolUse")]
        self.assertFalse(any("turn-budget" in c for c in commands))
        self.assertEqual([("*", "python \"C:/kit/hooks/inflight.py\"")], self.handlers(self.read_user(), "SubagentStop"))

    def test_the_write_is_in_place(self):
        self.write_user(user_settings())
        before = os.stat(self.settings)
        self.run_tool()
        after = os.stat(self.settings)
        self.assertEqual(before.st_ino, after.st_ino)
        self.assertNotEqual(before.st_size, after.st_size)


if __name__ == "__main__":
    unittest.main(verbosity=2)
