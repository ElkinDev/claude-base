"""Tests for claude/merge-hooks.py, the step that adds the kit's new hooks to a settings.json of the user's.

    python scripts/tests/test-merge-hooks.py

Every case writes its files into a fresh temporary folder and runs the tool the way install.ps1 does,
as a subprocess. No network, no real settings file.
"""
import base64
import copy
import hashlib
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
        self.record = os.path.join(self.base, ".kit-hooks-merged.json")
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

    def added(self, out):
        return sum(1 for line in out.splitlines() if line.startswith("added "))

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
        self.assertEqual(2, self.added(out))
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
        self.assertIn("record-changes", out)
        self.assertEqual(before, self.raw())
        self.assertFalse(os.path.exists(self.record))

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

    # ---------------------------------------------------- handlers known by the script they run --

    def write_kit(self, data, name="kit-settings.json"):
        path = os.path.join(self.base, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        return path

    def test_a_command_whose_form_changed_is_never_wired_twice(self):
        self.kit = self.write_kit({"hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command":
                "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -File \"C:/kit/hooks/b.ps1\""}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "python \"C:/kit/hooks/landing.py\""}]}]}})
        self.write_user({"hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "powershell -NoProfile -File \"C:\\kit\\hooks\\b.ps1\""}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "py -3 \"C:/kit/hooks/landing.py\""}]}]}})
        before = self.raw()
        code, out = self.run_tool()
        self.assertEqual(0, code, out)
        self.assertIn("up to date", out)
        self.assertEqual(before, self.raw())

    def test_with_the_previous_render_a_hook_the_user_deleted_or_moved_stays_as_they_left_it(self):
        previous = copy.deepcopy(KIT)
        previous["hooks"]["PreToolUse"] = previous["hooks"]["PreToolUse"][:1]  # turn-budget is new since
        previous_path = self.write_kit(previous, "previous.json")
        mine = user_settings()
        mine["hooks"]["PreToolUse"][0]["matcher"] = "Grep"  # guard-read moved from Read to Grep
        self.write_user(mine)  # and inflight, in the previous render, was never taken: deleted
        code, out = self.run_tool("--previous", previous_path)
        self.assertEqual(0, code, out)
        data = self.read_user()
        self.assertIn(("*", "python \"C:/kit/hooks/turn-budget.py\""), self.handlers(data, "PreToolUse"))
        self.assertNotIn("SubagentStop", data["hooks"])
        self.assertNotIn("Read", [m for m, _ in self.handlers(data, "PreToolUse")])
        self.assertEqual(1, self.added(out))

    def test_a_handler_the_kit_adds_under_a_new_matcher_is_added_where_the_script_already_runs(self):
        previous_path = self.write_kit(KIT, "previous.json")
        kit = copy.deepcopy(KIT)
        kit["hooks"]["PreToolUse"].append({"matcher": "Grep", "hooks": [{"type": "command", "command": "python \"C:/kit/hooks/guard-read.py\""}]})
        self.kit = self.write_kit(kit)
        self.write_user(user_settings())
        code, out = self.run_tool("--previous", previous_path)
        self.assertEqual(0, code, out)
        self.assertEqual(["added        PreToolUse[Grep] python \"C:/kit/hooks/guard-read.py\""],
                         [line for line in out.splitlines() if line.startswith("added")])

    def test_an_unreadable_previous_render_falls_back_to_scripts_run_nowhere(self):
        broken = os.path.join(self.base, "previous.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        self.write_user(user_settings())
        code, out = self.run_tool("--previous", broken)
        self.assertEqual(0, code, out)
        self.assertIn("seeded from the kit hooks", out)
        self.assertEqual(2, self.added(out))

    # ------------------------------------------------------------- the layout gate --

    def assert_left_alone(self, text):
        with open(self.settings, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        before = self.raw()
        for extra in ((), ("--dry-run",)):
            code, out = self.run_tool(*extra)
            self.assertEqual(0, code, out)
            self.assertIn("left alone", out)
            self.assertNotIn("would add", out)
            self.assertEqual(before, self.raw())
            self.assertFalse(os.path.exists(self.record), "a file left alone never advances the record")

    def test_a_tab_indented_file_is_left_alone(self):
        self.assert_left_alone(json.dumps(user_settings(), indent="\t") + "\n")

    def test_a_compact_file_is_left_alone(self):
        self.assert_left_alone(json.dumps(user_settings(), separators=(",", ":")))

    def test_entries_kept_on_one_line_are_left_alone(self):
        text = json.dumps(user_settings(), indent=2) + "\n"
        entry = json.dumps(user_settings()["hooks"]["Stop"][0])
        pretty = json.dumps(user_settings()["hooks"]["Stop"][0], indent=2).replace("\n", "\n      ")
        self.assertIn(pretty, text)
        self.assert_left_alone(text.replace(pretty, entry))

    def test_a_key_written_twice_is_left_alone(self):
        text = json.dumps(user_settings(), indent=2) + "\n"
        self.assert_left_alone(text.replace('"model": "my-model",', '"env": {"A": "1"},\n  "env": {"B": "2"},\n  "model": "my-model",'))

    def test_the_layout_claude_code_writes_merges_and_keeps_raw_text(self):
        mine = user_settings()
        mine["statusLine"] = {"type": "command", "command": "señal.exe"}
        with open(self.settings, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(mine, indent=2, ensure_ascii=False))  # JSON.stringify(x, null, 2), no newline
        code, dry = self.run_tool("--dry-run")
        self.assertEqual(0, code, dry)
        code, out = self.run_tool()
        self.assertEqual(0, code, out)
        raw = self.raw()
        self.assertIn("señal.exe".encode("utf-8"), raw)
        self.assertFalse(raw.endswith(b"\n"))
        self.assertIn("after-sha256 " + hashlib.sha256(raw).hexdigest(), dry)

    # ------------------------------------------------------- shapes and links --

    def test_a_hooks_entry_of_the_wrong_shape_stops_dry_and_real_runs_alike(self):
        mine = user_settings()
        mine["hooks"]["SubagentStop"] = {}
        self.write_user(mine)
        before = self.raw()
        for extra in (("--dry-run",), ()):
            code, out = self.run_tool(*extra)
            self.assertEqual(1, code, out)
            self.assertIn("NOTHING TOUCHED", out)
            self.assertEqual(before, self.raw())

    def test_a_settings_file_that_is_a_link_is_left_alone(self):
        real = os.path.join(self.base, "real-settings.json")
        with open(real, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(user_settings(), indent=2) + "\n")
        try:
            os.symlink(real, self.settings)
        except (OSError, NotImplementedError):
            # No symlink privilege: a directory junction is a link of the other kind the check reads.
            if os.name != "nt":
                raise
            target = os.path.join(self.base, "a-folder")
            os.makedirs(target)
            subprocess.run(["cmd", "/c", "mklink", "/J", self.settings, target], capture_output=True, check=True)
        before = self.raw(real)
        code, out = self.run_tool()
        self.assertEqual(0, code, out)
        self.assertIn("it is a link", out)
        self.assertEqual(before, self.raw(real))


    # ------------------------------------------------- handler identity and the merge record --

    def test_a_handler_that_differs_only_by_its_arguments_is_its_own_hook(self):
        recover = "python \"C:/kit/hooks/compact-recover.py\""
        older = {"hooks": {"SessionStart": [
            {"matcher": "compact", "hooks": [{"type": "command", "command": recover}]}]}}
        newer = {"hooks": {"SessionStart": [
            {"matcher": "startup|resume|clear|fork", "hooks": [{"type": "command", "command": recover + " --rulings"}]},
            {"matcher": "compact", "hooks": [{"type": "command", "command": recover}]}]}}
        for extra in (("--previous", self.write_kit(older, "previous.json")), ()):
            if os.path.exists(self.record):
                os.remove(self.record)
            self.kit = self.write_kit(newer)
            self.write_user(older)
            code, out = self.run_tool(*extra)
            self.assertEqual(0, code, out)
            self.assertIn(("startup|resume|clear|fork", recover + " --rulings"), self.handlers(self.read_user(), "SessionStart"))
            self.assertEqual(1, self.added(out))

    def test_the_interpreter_and_the_kit_home_spelling_are_not_part_of_a_handler(self):
        self.kit = self.write_kit({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "python C:/Kit/hooks/x.py --log C:/Kit/logs"}]}]}})
        self.write_user({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "py -3 \"c:\\kit\\hooks\\x.py\"   --log  \"c:\\kit\\logs\""}]}]}})
        before = self.raw()
        code, out = self.run_tool("--kit-home", "C:/kit")
        self.assertEqual(0, code, out)
        self.assertIn("up to date", out)
        self.assertEqual(before, self.raw())

    def test_a_hook_skipped_once_is_offered_again_on_the_next_run(self):
        """A -NoHookMerge run, a refusal or a failure leaves the record as it was: the hooks a newer kit
        brought are offered on the next run, whatever the .new beside the file says by then."""
        self.write_user(user_settings())
        self.run_tool()  # the record now holds every key of KIT
        newer = copy.deepcopy(KIT)
        newer["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": "python \"C:/kit/hooks/new-one.py\""})
        self.kit = self.write_kit(newer)
        refreshed_new = self.write_kit(newer, "previous.json")  # the .new a skipped run refreshed
        code, out = self.run_tool("--previous", refreshed_new)
        self.assertEqual(0, code, out)
        self.assertIn((None, "python \"C:/kit/hooks/new-one.py\""), self.handlers(self.read_user(), "Stop"))

    def test_a_hook_the_user_removed_after_a_merge_is_not_offered_again(self):
        self.write_user(user_settings())
        self.run_tool()
        data = self.read_user()
        data["hooks"]["PreToolUse"] = [e for e in data["hooks"]["PreToolUse"] if e.get("matcher") != "*"]
        self.write_user(data)
        before = self.raw()
        code, out = self.run_tool()
        self.assertEqual(0, code, out)
        self.assertIn("up to date", out)
        self.assertEqual(before, self.raw())

    def test_the_first_run_seeds_the_record_from_the_previous_render_once(self):
        previous = copy.deepcopy(KIT)
        previous["hooks"]["PreToolUse"] = previous["hooks"]["PreToolUse"][:1]
        previous_path = self.write_kit(previous, "previous.json")
        self.write_user(user_settings())
        code, out = self.run_tool("--previous", previous_path)
        self.assertEqual(0, code, out)
        self.assertIn("seeded from the previous kit render", out)
        self.assertEqual(1, self.added(out))  # turn-budget; inflight was in the previous render
        code, out = self.run_tool("--previous", previous_path)
        self.assertNotIn("seeded", out)
        self.assertIn("up to date", out)

    def test_the_first_run_seeds_from_the_file_when_there_is_no_previous_render(self):
        mine = user_settings()
        mine["hooks"]["PreToolUse"].append({"matcher": "*", "hooks": [{"type": "command", "command": "python \"C:/kit/hooks/turn-budget.py\""}]})
        mine["hooks"]["SubagentStop"] = [{"matcher": "*", "hooks": [{"type": "command", "command": "python \"C:/kit/hooks/inflight.py\""}]}]
        self.write_user(mine)
        before = self.raw()
        code, out = self.run_tool()
        self.assertEqual(0, code, out)
        self.assertIn("seeded from the kit hooks", out)
        self.assertIn("up to date", out)
        self.assertEqual(before, self.raw())
        with open(self.record, encoding="utf-8") as handle:
            self.assertEqual(4, len(json.load(handle)["keys"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
