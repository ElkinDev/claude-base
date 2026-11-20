"""Tests for claude/claude-account.py and claude/bin/cca, the macOS and Linux account switcher:

    python3 scripts/tests/test-claude-account-posix.py

Every case runs in a temporary HOME. A fake claude sits first on PATH and records its arguments,
its working folder and the variables the switcher hands it, so a launch is asserted without a real
session. A fake osascript records the AppleScript and the command instead of opening a window, and a
fake herdr answers with canned JSON instead of driving a live Herdr. Nothing here reaches the real
~/.claude, ~/.claude-accounts, ~/.claude.json, the Keychain or the network.
"""
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(REPO, "claude", "claude-account.py")
LAUNCHER = os.path.join(REPO, "claude", "bin", "cca")
MERGER = os.path.join(REPO, "claude", "merge-settings.py")
START_LINE = ("Session start: read the newest resume brief of your seat, then the state sheet, "
              "then continue with its first actions.")
LINKED_PRESENT = ("skills", "agents", "hooks", "projects")
LINKED_ABSENT = ("plugins", "tools", "commands")

# The fakes are Python scripts run by this interpreter, so the suite needs nothing else installed.
FAKE_CLAUDE = r'''
import json, os, sys
keys = ("CLAUDE_CONFIG_DIR", "CLAUDE_ROLE", "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
        "CLAUDE_CODE_DISABLE_1M_CONTEXT", "CLAUDE_BRIEFS_DIR", "CLAUDE_CLOSING_HOUR")
record = {"argv": sys.argv[1:], "cwd": os.getcwd(), "env": {k: os.environ.get(k) for k in keys}}
with open(os.path.join(os.environ["FAKE_LOG_DIR"], "claude.json"), "w") as fh:
    json.dump(record, fh)
'''

FAKE_OSASCRIPT = r'''
import json, os, sys
with open(os.path.join(os.environ["FAKE_LOG_DIR"], "osascript.json"), "w") as fh:
    json.dump(sys.argv[1:], fh)
sys.exit(int(os.environ.get("FAKE_OSASCRIPT_EXIT", "0")))
'''

FAKE_HERDR = r'''
import json, os, sys
with open(os.path.join(os.environ["FAKE_LOG_DIR"], "herdr.jsonl"), "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
args = sys.argv[1:]
if args[:2] == ["workspace", "list"]:
    print(json.dumps({"result": {"workspaces": [{"number": 5, "workspace_id": "w8"}]}}))
elif args[:2] == ["tab", "create"]:
    print(json.dumps({"result": {"root_pane": {"pane_id": "p42"}}}))
'''


class Run(object):
    def __init__(self, proc):
        self.code = proc.returncode
        self.out = proc.stdout.decode("utf-8", "replace")


@unittest.skipIf(os.name == "nt", "the POSIX switcher; Windows runs claude-account.ps1")
class SwitcherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cca-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.default = os.path.join(self.home, ".claude")
        self.base = os.path.join(self.home, ".claude-accounts")
        self.work = os.path.join(self.tmp, "code", "my app")
        self.logs = os.path.join(self.tmp, "logs")
        self.bin = os.path.join(self.tmp, "fakebin")
        self.seats = os.path.join(self.tmp, "seats")
        for d in (self.work, self.logs, self.bin, self.seats,
                  os.path.join(self.default, "skills", "demo"), os.path.join(self.default, "agents"),
                  os.path.join(self.default, "hooks"), os.path.join(self.default, "projects", "-code")):
            os.makedirs(d)
        self.write(os.path.join(self.default, "skills", "demo", "SKILL.md"), "a shared skill\n")
        self.write(os.path.join(self.default, "projects", "-code", "session.jsonl"), "{}\n")
        self.write(os.path.join(self.default, "CLAUDE.md"), "default rules v1\n")
        self.write_json(os.path.join(self.default, "settings.json"),
                        {"model": "opus", "statusLine": {"type": "command"}, "hooks": {}})
        for name, body in (("claude", FAKE_CLAUDE), ("osascript", FAKE_OSASCRIPT), ("herdr", FAKE_HERDR)):
            self.fake(name, body)
        # Built from scratch, not copied from this shell: an inherited CLAUDE_CONFIG_DIR, role,
        # window, model or HERDR_ENV would change what every case asserts.
        self.env = {
            "HOME": self.home,
            "PATH": self.bin + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
            "PWD": self.work,
            "FAKE_LOG_DIR": self.logs,
            "CLAUDE_SEATS_DIR": self.seats,
            "TERM_PROGRAM": "Apple_Terminal",
            "LANG": "en_US.UTF-8",
        }

    # --- helpers ------------------------------------------------------------------

    def write(self, path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def write_json(self, path, data):
        self.write(path, json.dumps(data, indent=2))

    def read_json(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def fake(self, name, body):
        path = os.path.join(self.bin, name)
        self.write(path, "#!" + sys.executable + "\n" + body)
        os.chmod(path, 0o755)

    def cca(self, *args, **kw):
        env = dict(self.env)
        env.update(kw.get("env") or {})
        proc = subprocess.run([sys.executable, SCRIPT] + list(args), cwd=self.work, env=env,
                              input=(kw.get("input") or "").encode("utf-8"),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        return Run(proc)

    def claude_record(self):
        path = os.path.join(self.logs, "claude.json")
        return self.read_json(path) if os.path.exists(path) else None

    def profile(self, name="work"):
        return os.path.join(self.base, name)

    def names(self):
        return self.read_json(os.path.join(self.base, ".names.json"))

    # --- prepare and sync -----------------------------------------------------------

    def test_prepare_links_shared_folders_and_copies_settings(self):
        r = self.cca("work", "-p")
        self.assertEqual(r.code, 0, r.out)
        self.assertIn("Profile ready. Not opening a session because you passed -Prepare.", r.out)
        prof = self.profile()
        for name in LINKED_PRESENT:
            link = os.path.join(prof, name)
            self.assertTrue(os.path.islink(link), name + " is a link, not a copy")
            self.assertEqual(os.readlink(link), os.path.join(self.default, name))
        for name in LINKED_ABSENT:
            self.assertFalse(os.path.lexists(os.path.join(prof, name)), name + " is skipped when the default has none")
        self.assertTrue(os.path.isfile(os.path.join(prof, "skills", "demo", "SKILL.md")))
        self.assertEqual(self.read(os.path.join(prof, "CLAUDE.md")), "default rules v1\n")
        self.assertEqual(self.read_json(os.path.join(prof, "settings.json")),
                         self.read_json(os.path.join(self.default, "settings.json")))
        stamp = self.read_json(os.path.join(prof, ".sync-source.json"))
        self.assertEqual(sorted(stamp), ["CLAUDE.md", "settings.json"])
        self.assertEqual(sorted(stamp["CLAUDE.md"]), ["dest", "source"])
        trust = self.read_json(os.path.join(prof, ".claude.json"))["projects"]
        self.assertTrue(trust[self.work]["hasTrustDialogAccepted"])
        real = os.path.realpath(self.work)
        if real != self.work:
            self.assertTrue(trust[real]["hasTrustDialogAccepted"], "the physical path is trusted too")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(prof, ".claude.json")).st_mode), 0o600)
        self.assertIn("not signed in yet", r.out)
        self.assertIsNone(self.claude_record(), "-p opens no session")

    def test_no_share_links_nothing(self):
        r = self.cca("solo", "-p", "-n")
        self.assertEqual(r.code, 0, r.out)
        for name in LINKED_PRESENT:
            self.assertFalse(os.path.lexists(os.path.join(self.profile("solo"), name)))
        self.assertTrue(os.path.isfile(os.path.join(self.profile("solo"), "CLAUDE.md")))

    def test_a_profile_edit_survives_until_sync_forces_it(self):
        self.assertEqual(self.cca("work", "-p").code, 0)
        mine = os.path.join(self.profile(), "CLAUDE.md")
        self.write(mine, "my own rules\n")
        self.write(os.path.join(self.default, "CLAUDE.md"), "default rules v2\n")
        r = self.cca("work", "-p")
        self.assertEqual(r.code, 0, r.out)
        self.assertIn("CLAUDE.md changed inside this profile, leaving it alone.", r.out)
        self.assertIn("cca work -s", r.out)
        self.assertEqual(self.read(mine), "my own rules\n")
        r = self.cca("work", "-p", "-s")
        self.assertEqual(r.code, 0, r.out)
        self.assertEqual(self.read(mine), "default rules v2\n")
        self.assertEqual(self.read(mine + ".bak"), "my own rules\n", "the edit is kept as .bak")

    def test_an_untouched_profile_takes_a_new_default(self):
        self.assertEqual(self.cca("work", "-p").code, 0)
        self.write(os.path.join(self.default, "CLAUDE.md"), "default rules v2\n")
        r = self.cca("work", "--prepare")
        self.assertEqual(r.code, 0, r.out)
        self.assertNotIn("changed inside this profile", r.out)
        self.assertEqual(self.read(os.path.join(self.profile(), "CLAUDE.md")), "default rules v2\n")

    def test_sync_merges_settings_and_keeps_the_profile_model(self):
        self.assertEqual(self.cca("work", "-p").code, 0)
        settings = os.path.join(self.profile(), "settings.json")
        own = self.read_json(settings)
        own["model"] = "haiku"
        own["skipDangerousModePermissionPrompt"] = True
        self.write_json(settings, own)
        default = self.read_json(os.path.join(self.default, "settings.json"))
        default["hooks"] = {"Stop": [{"hooks": [{"command": "x"}]}]}
        self.write_json(os.path.join(self.default, "settings.json"), default)
        r = self.cca("work", "-Prepare", "-Sync")
        self.assertEqual(r.code, 0, r.out)
        merged = self.read_json(settings)
        self.assertEqual(merged["model"], "haiku", "the model stays with the profile")
        self.assertTrue(merged["skipDangerousModePermissionPrompt"], "keys the profile owns are kept")
        self.assertEqual(merged["hooks"], default["hooks"], "the default's hooks arrive")

    def test_sync_refuses_a_profile_that_does_not_exist(self):
        for args in (("nosuch", "-s"), ("second", "-p", "-s")):
            r = self.cca(*args)
            self.assertEqual(r.code, 1, r.out)
            self.assertIn("no such profile '{}'".format(args[0]), r.out)
            self.assertIn("cca {} -p".format(args[0]), r.out)
            self.assertFalse(os.path.exists(self.profile(args[0])), "nothing was built")
        self.assertIsNone(self.claude_record())

    # --- list -------------------------------------------------------------------------

    def test_list_shows_accounts_shortcuts_and_the_active_one(self):
        self.assertEqual(self.cca("work", "-p").code, 0)
        self.assertEqual(self.cca("personal", "-p").code, 0)
        self.assertEqual(self.cca("work", "-a", "w").code, 0)
        data = self.read_json(os.path.join(self.profile(), ".claude.json"))
        data["oauthAccount"] = {"emailAddress": "someone@example.com"}
        self.write_json(os.path.join(self.profile(), ".claude.json"), data)
        r = self.cca()
        self.assertEqual(r.code, 0, r.out)
        self.assertIn("Claude Code accounts", r.out)
        self.assertRegex(r.out, r"(?m)^  \* \S+ default\s+not signed in yet$")
        self.assertIn("(~/.claude, the one plain 'claude' uses)", r.out)
        self.assertRegex(r.out, r"(?m)^    \S+ work \(w\)\s+someone@example\.com$")
        self.assertRegex(r.out, r"(?m)^    \S+ personal\s+not signed in yet$")
        self.assertIn("cca -h for every option.", r.out)
        r = self.cca("-l", env={"CLAUDE_CONFIG_DIR": self.profile()})
        self.assertRegex(r.out, r"(?m)^  \* \S+ work \(w\)", "the active profile carries the asterisk")
        self.assertRegex(r.out, r"(?m)^    \S+ default", "and the default does not")

    # --- open ---------------------------------------------------------------------------

    def test_open_sets_the_config_dir_and_passes_arguments_through(self):
        r = self.cca("work", "--", "-c", "--model", "opus",
                     env={"CLAUDE_CODE_DISABLE_1M_CONTEXT": "1", "CLAUDE_CONFIG_DIR": "/elsewhere"})
        self.assertEqual(r.code, 0, r.out)
        rec = self.claude_record()
        self.assertIsNotNone(rec, r.out)
        self.assertEqual(rec["argv"], ["-c", "--model", "opus", "--no-chrome"])
        self.assertEqual(rec["env"]["CLAUDE_CONFIG_DIR"], self.profile())
        self.assertEqual(rec["env"]["CLAUDE_ROLE"], "lane")
        self.assertEqual(rec["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "300000")
        self.assertIsNone(rec["env"]["CLAUDE_CODE_DISABLE_1M_CONTEXT"], "an inherited cap is removed")
        self.assertIsNone(rec["env"]["CLAUDE_BRIEFS_DIR"], "a lane has no briefs")
        self.assertEqual(os.path.realpath(rec["cwd"]), os.path.realpath(self.work))
        self.assertTrue(os.path.islink(os.path.join(self.profile(), "skills")), "the first open prepares")

    def test_flags_cca_does_not_know_reach_claude_without_the_separator(self):
        self.assertEqual(self.cca("work", "-c").code, 0)
        self.assertEqual(self.claude_record()["argv"], ["-c", "--no-chrome"])
        self.assertEqual(self.cca("work", "--model", "opus", "-Window", "230000").code, 0)
        rec = self.claude_record()
        self.assertEqual(rec["argv"], ["--model", "opus", "--no-chrome"])
        self.assertEqual(rec["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "230000")

    def test_a_seated_role_gets_its_seat_name_and_start_line(self):
        seat = os.path.join(self.seats, "orchestrator.md")
        self.write(seat, "You are the orchestrator.\n")
        for spelling in (("-Role", "orchestrator"), ("--role", "orchestrator"), ("-o", "Orchestrator")):
            r = self.cca("work", *spelling)
            self.assertEqual(r.code, 0, r.out)
            rec = self.claude_record()
            argv = rec["argv"]
            self.assertEqual(argv[0], "--name")
            self.assertRegex(argv[1], r"^orchestrator-\d{4}-\d{4}$")
            self.assertEqual(argv[2:], ["--append-system-prompt-file", seat, "--no-chrome", START_LINE])
            self.assertEqual(rec["env"]["CLAUDE_ROLE"], "orchestrator")
            self.assertEqual(rec["env"]["CLAUDE_BRIEFS_DIR"], os.path.join(self.home, ".claude", "briefs"))
            self.assertEqual(rec["env"]["CLAUDE_CLOSING_HOUR"], "22:00")
        r = self.cca("work", "-Role", "analyst")
        self.assertIn("Seat file missing: " + os.path.join(self.seats, "analyst.md"), r.out)
        self.assertNotIn("--append-system-prompt-file", self.claude_record()["argv"])

    def test_a_seated_role_refuses_continue(self):
        for tail in (("--", "-c"), ("--", "--continue"), ("-c",)):
            r = self.cca("work", "-Role", "orchestrator", *tail)
            self.assertEqual(r.code, 1, r.out)
            self.assertIn("A seated role never continues the most recent conversation of a folder", r.out)
        self.assertIsNone(self.claude_record())
        self.assertFalse(os.path.exists(self.profile()), "refused before any profile is built")

    def test_default_opens_without_a_config_dir(self):
        r = self.cca("default", env={"CLAUDE_CONFIG_DIR": self.profile("other")})
        self.assertEqual(r.code, 0, r.out)
        self.assertIsNone(self.claude_record()["env"]["CLAUDE_CONFIG_DIR"])
        self.assertFalse(os.path.exists(self.profile("default")))

    # --- delete -------------------------------------------------------------------------

    def test_delete_removes_the_profile_and_never_the_shared_targets(self):
        self.assertEqual(self.cca("work", "-p").code, 0)
        self.assertEqual(self.cca("work", "-a", "w", "-i", "1F4BC").code, 0)
        prof = self.profile()
        # Links outside the known list, at the top and nested, must be unlinked and not followed.
        os.symlink(os.path.join(self.default, "skills"), os.path.join(prof, "more-skills"))
        os.makedirs(os.path.join(prof, "deep", "er"))
        os.symlink(os.path.join(self.default, "projects"), os.path.join(prof, "deep", "er", "proj"))
        self.write(os.path.join(prof, "own.txt"), "profile data\n")
        r = self.cca("work", "-d", input="n\n")
        self.assertIn("cancelled", r.out)
        self.assertTrue(os.path.isdir(prof))
        r = self.cca("w", "-d", input="y\n")
        self.assertEqual(r.code, 0, r.out)
        self.assertFalse(os.path.lexists(prof))
        self.assertTrue(os.path.isfile(os.path.join(self.default, "skills", "demo", "SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.default, "projects", "-code", "session.jsonl")))
        for name in LINKED_PRESENT:
            self.assertTrue(os.path.isdir(os.path.join(self.default, name)), name + " survives")
        self.assertIn("profile deleted, the default directory is untouched", r.out)
        self.assertEqual(self.names(), {"shortcuts": {}, "icons": {}}, "orphaned shortcut and icon removed")

    def test_dot_names_are_refused(self):
        for name in ("..", "."):
            r = self.cca(name, "-d", input="y\n")
            self.assertEqual(r.code, 1, r.out)
            self.assertIn("invalid name", r.out)
        self.assertTrue(os.path.isdir(self.default))
        self.assertTrue(os.path.isdir(self.work))

    # --- rename, alias, icon, default ---------------------------------------------------

    def test_rename_moves_the_profile_its_shortcut_and_icon(self):
        self.assertEqual(self.cca("work", "-p").code, 0)
        self.assertEqual(self.cca("work", "-a", "w").code, 0)
        self.assertEqual(self.cca("work", "-i", "1F4BC").code, 0)
        r = self.cca("work", "-r", "job")
        self.assertEqual(r.code, 0, r.out)
        self.assertIn("Profile renamed: work -> job", r.out)
        self.assertFalse(os.path.exists(self.profile("work")))
        self.assertTrue(os.path.isfile(os.path.join(self.profile("job"), "skills", "demo", "SKILL.md")),
                        "absolute links still resolve after the move")
        self.assertEqual(self.names(), {"shortcuts": {"w": "job"}, "icons": {"job": "1F4BC"}})
        self.assertNotIn("Keychain", r.out, "a profile never signed in has no Keychain entry to mention")
        data = self.read_json(os.path.join(self.profile("job"), ".claude.json"))
        data["oauthAccount"] = {"emailAddress": "someone@example.com"}
        self.write_json(os.path.join(self.profile("job"), ".claude.json"), data)
        r = self.cca("job", "--rename=personal")
        self.assertEqual(r.code, 0, r.out)
        self.assertTrue(os.path.isdir(self.profile("personal")))
        if sys.platform == "darwin":
            # The item name hashes the old CLAUDE_CONFIG_DIR string, so the renamed profile signs in again.
            self.assertIn("Claude Code-credentials-", r.out, "the Keychain consequence is named")

    def test_alias_and_icon_land_in_names_json_in_the_status_line_shape(self):
        self.assertEqual(self.cca("work", "-p").code, 0)
        self.assertEqual(self.cca("home-acct", "-p").code, 0)
        self.assertEqual(self.cca("work", "-Alias", "w").code, 0)
        r = self.cca("work", "--alias=k")
        self.assertIn("previous shortcut removed: w", r.out)
        self.assertEqual(self.cca("work", "-i", "\U0001F4BC").code, 0)
        self.assertEqual(self.cca("home-acct", "-a", "h", "-i", "1f3e0").code, 0)
        self.assertEqual(self.cca("default", "-i", "2b50").code, 0)
        self.assertEqual(self.cca("default", "-r", "main").code, 0)
        names = self.names()
        self.assertEqual(names, {
            "shortcuts": {"k": "work", "h": "home-acct"},
            "icons": {"work": "1F4BC", "home-acct": "1F3E0", "default": "2B50"},
            "default": "main",
        })
        # What the status line reads: the display name of ~/.claude and hex code points per target.
        self.assertIsInstance(names["default"], str)
        for value in names["icons"].values():
            self.assertRegex(value, r"^[0-9A-F]{4,6}$")
        with open(os.path.join(self.base, ".names.json"), "rb") as fh:
            self.assertFalse(fh.read(3) == b"\xef\xbb\xbf", "no BOM: Node's JSON.parse rejects one")
        r = self.cca("work", "-a", "nosuch-target-is-fine")
        self.assertEqual(r.code, 0, r.out)
        r = self.cca("ghost", "-a", "g")
        self.assertEqual(r.code, 1, r.out)
        self.assertIn("no such account 'ghost'", r.out)

    def test_default_is_reserved(self):
        self.assertEqual(self.cca("work", "-p").code, 0)
        r = self.cca("work", "-r", "default")
        self.assertEqual(r.code, 1, r.out)
        self.assertIn("'default' is reserved", r.out)
        r = self.cca("default", "-d", input="y\n")
        self.assertEqual(r.code, 1, r.out)
        self.assertIn("the default account cannot be deleted", r.out)
        r = self.cca("work", "-a", "default")
        self.assertEqual(r.code, 1, r.out)
        r = self.cca("default", "-p")
        self.assertEqual(r.code, 0, r.out)
        self.assertIn("Nothing to prepare", r.out)
        self.assertFalse(os.path.exists(self.profile("default")))
        # A relabelled default still resolves to ~/.claude, by its new name and by 'default'.
        self.assertEqual(self.cca("default", "-r", "main").code, 0)
        r = self.cca("main", "-p")
        self.assertIn("Nothing to prepare", r.out)
        self.assertFalse(os.path.exists(self.profile("main")))
        r = self.cca("main", "-r", "default")
        self.assertIn("The default account is called 'default' again.", r.out)
        self.assertNotIn("default", self.names())

    # --- the dry run --------------------------------------------------------------------

    def test_show_env_prints_the_plan_and_builds_nothing(self):
        r = self.cca("demo", "-ShowEnv")
        self.assertEqual(r.code, 0, r.out)
        self.assertRegex(r.out, r"(?m)^CLAUDE_ROLE=lane$")
        self.assertRegex(r.out, r"(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=\(unset\)$")
        self.assertRegex(r.out, r"(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000$")
        self.assertRegex(r.out, r"(?m)^EXTRA=--no-chrome$")
        self.assertRegex(r.out, r"(?m)^PANE_COMMAND=export CLAUDE_ROLE=lane; unset CLAUDE_CODE_DISABLE_1M_CONTEXT; "
                                r"export CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000$")
        self.assertRegex(r.out, r"(?m)^SEAT=none$")
        self.assertFalse(os.path.exists(self.base), "a dry run touches no profile")
        r = self.cca("demo", "--show-env", "--window", "230000")
        self.assertRegex(r.out, r"(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=230000$")
        r = self.cca("demo", "-Sh", "-Role", "research")
        self.assertRegex(r.out, r"(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=\(unchanged\)$")
        self.assertRegex(r.out, r"(?m)^EXTRA=--name research$")
        r = self.cca("demo", "-ShowEnv", "-c", "1")
        self.assertRegex(r.out, r"(?m)^EXTRA=-c 1 --no-chrome$")
        r = self.cca("demo", "-ShowEnv", "-Window", "-5")
        self.assertNotEqual(r.code, 0)
        self.assertNotIn("CLAUDE_ROLE=", r.out)

    def test_show_env_orders_effort_model_name_and_start_line(self):
        self.write(os.path.join(self.seats, "orchestrator.md"), "seat\n")
        r = self.cca("demo", "-ShowEnv", "-Role", "orchestrator",
                     env={"CLAUDE_EFFORT_ORCHESTRATOR": " XHigh ", "CLAUDE_MODEL_ORCHESTRATOR": "opus"})
        self.assertEqual(r.code, 0, r.out)
        self.assertRegex(r.out, r"(?m)^COMMAND=claude --effort xhigh --model opus --name orchestrator-\d{4}-\d{4} "
                                r"--append-system-prompt-file \S+ --no-chrome "
                                + re.escape(shlex.quote(START_LINE)) + "$")
        self.assertRegex(r.out, r"(?m)^EFFORT=xhigh \(CLAUDE_EFFORT_ORCHESTRATOR\)$")
        self.assertRegex(r.out, r"(?m)^FRESH=true$")
        self.assertIn("export CLAUDE_CLOSING_HOUR=22:00", r.out)
        r = self.cca("demo", "-ShowEnv", "-Role", "orchestrator", "--", "-r", "abc")
        self.assertRegex(r.out, r"(?m)^FRESH=false$")
        self.assertRegex(r.out, r"(?m)^START=none$")
        self.assertNotIn("--name", r.out)

    # --- a separate tab or window -------------------------------------------------------

    def test_tab_opens_terminal_with_the_account_command(self):
        if sys.platform != "darwin":
            self.skipTest("Terminal.app and iTerm2 are macOS")
        self.assertEqual(self.cca("work", "-p").code, 0)
        r = self.cca("work", "-w", "-Role", "research")
        self.assertEqual(r.code, 0, r.out)
        self.assertIsNone(self.claude_record(), "nothing runs in this terminal")
        argv = self.read_json(os.path.join(self.logs, "osascript.json"))
        self.assertIn('tell application "Terminal"', argv)
        command = argv[-1]
        self.assertTrue(command.startswith("cd " + shlex.quote(self.work) + "; "), command)
        self.assertIn("export CLAUDE_CONFIG_DIR=" + shlex.quote(self.profile()), command)
        self.assertIn("export CLAUDE_ROLE=research", command)
        self.assertTrue(command.endswith("; claude --name research"), command)
        self.assertIn("Window opened on account work in Terminal", r.out)
        r = self.cca("work", "-w", env={"TERM_PROGRAM": "iTerm.app"})
        self.assertIn('tell application "iTerm"', self.read_json(os.path.join(self.logs, "osascript.json")))
        self.assertIn("in iTerm2", r.out)

    def test_tab_prints_the_command_when_no_window_can_be_opened(self):
        self.assertEqual(self.cca("work", "-p").code, 0)
        r = self.cca("work", "-w", env={"FAKE_OSASCRIPT_EXIT": "1"})
        self.assertEqual(r.code, 0, r.out)
        self.assertIn("Could not open a new terminal from here. Open one and run:", r.out)
        self.assertIn("export CLAUDE_CONFIG_DIR=" + shlex.quote(self.profile()), r.out)
        self.assertIsNone(self.claude_record())

    def test_tab_inside_herdr_creates_a_labelled_tab_in_the_workspace(self):
        self.write(os.path.join(self.seats, "orchestrator.md"), "seat\n")
        self.assertEqual(self.cca("work", "-p").code, 0)
        r = self.cca("work", "-w", "-x", "5", "-Role", "orchestrator", env={"HERDR_ENV": "1"})
        self.assertEqual(r.code, 0, r.out)
        self.assertIn("Opened in Herdr tab cc-work-orchestrator (pane p42, role orchestrator).", r.out)
        with open(os.path.join(self.logs, "herdr.jsonl")) as fh:
            calls = [json.loads(line) for line in fh]
        self.assertEqual(calls[0], ["workspace", "list"])
        self.assertEqual(calls[1], ["tab", "create", "--workspace", "w8", "--cwd", self.work,
                                    "--label", "cc-work-orchestrator", "--no-focus"])
        self.assertEqual(calls[2][:3], ["pane", "run", "p42"])
        pane = calls[2][3]
        self.assertTrue(pane.startswith("export CLAUDE_CONFIG_DIR=" + shlex.quote(self.profile()) + "; "), pane)
        self.assertIn("export CLAUDE_ROLE=orchestrator", pane)
        self.assertIn("export CLAUDE_BRIEFS_DIR=", pane)
        self.assertIn("--append-system-prompt-file", pane)
        self.assertFalse(os.path.exists(os.path.join(self.logs, "osascript.json")), "no window as well")
        self.assertIsNone(self.claude_record())
        r = self.cca("work", "-w", "-x", "9", env={"HERDR_ENV": "1"})
        self.assertEqual(r.code, 1, r.out)
        self.assertIn("there is no Herdr workspace number 9", r.out)
        r = self.cca("default", "-w", env={"HERDR_ENV": "1"})
        with open(os.path.join(self.logs, "herdr.jsonl")) as fh:
            last = [json.loads(line) for line in fh][-1]
        self.assertTrue(last[3].startswith("unset CLAUDE_CONFIG_DIR; "), last[3])

    # --- the launcher -------------------------------------------------------------------

    def test_cca_finds_the_switcher_from_a_link_on_path(self):
        with open(LAUNCHER, "rb") as fh:
            raw = fh.read()
        self.assertTrue(raw.startswith(b"#!/bin/sh\n"))
        self.assertNotIn(b"\r", raw, "LF line endings")
        kit = os.path.join(self.tmp, "kit")
        os.makedirs(os.path.join(kit, "bin"))
        shutil.copy(SCRIPT, os.path.join(kit, "claude-account.py"))
        shutil.copy(MERGER, os.path.join(kit, "merge-settings.py"))
        shutil.copy(LAUNCHER, os.path.join(kit, "bin", "cca"))
        os.chmod(os.path.join(kit, "bin", "cca"), 0o755)
        links = os.path.join(self.tmp, "links")
        os.makedirs(links)
        os.symlink(os.path.join(kit, "bin", "cca"), os.path.join(links, "cca"))
        env = dict(self.env)
        env["PATH"] = links + os.pathsep + env["PATH"]
        proc = subprocess.run(["cca", "work", "-p"], cwd=self.work, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        out = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, out)
        self.assertTrue(os.path.islink(os.path.join(self.profile(), "skills")))
        proc = subprocess.run([os.path.join(kit, "bin", "cca"), "-h"], cwd=self.work, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertIn("cca  -  switch Claude Code accounts", proc.stdout.decode("utf-8", "replace"))
        os.remove(os.path.join(kit, "claude-account.py"))
        proc = subprocess.run(["cca"], cwd=self.work, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"the switcher is not at", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
