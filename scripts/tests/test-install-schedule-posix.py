"""The daily kit update on macOS: the launchd job install.py --auto-update registers and --no-auto-update removes
(install/kit_schedule.py), the install record every install leaves (<kit home>/.kit-install.json), the run the job
starts (install/auto-update.py, copied to <kit home>/kit-auto-update.py), and the session-start line the run leaves
(claude/hooks/kit-update-notice.py, whose own suite is claude/hooks/tests/test-kit-update-notice.py).

    python3 scripts/tests/test-install-schedule-posix.py

Fully sandboxed. launchd is replaced by a fake launchctl (KIT_LAUNCHCTL) that keeps its loaded jobs in a JSON file,
and ~/Library/LaunchAgents by a temporary folder (KIT_LAUNCHAGENTS_DIR), which starts with the job of another kit
home in it that no test may touch; HOME points into the sandbox too. The kit is a small git repository made here: an
origin, and the clone the job updates cloned from it, so an upstream commit is a commit in the sandbox. Its
install.py is a stand-in that records how it was called and writes the install record the way the real one does,
through kit_schedule.save_install_record. git runs with no system or global configuration. Skipped on Windows, where
scripts/tests/test-install-auto-update.ps1 covers the scheduled task.
"""
import datetime
import hashlib
import importlib.util
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

if os.name != "nt":
    import fcntl

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SCHEDULE = os.path.join(REPO, "install", "kit_schedule.py")
RUNNER = os.path.join(REPO, "install", "auto-update.py")
NOTICE = os.path.join(REPO, "claude", "hooks", "kit-update-notice.py")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if os.name != "nt":
    schedule = load("kit_schedule", SCHEDULE)
    runner = load("kit_auto_update", RUNNER)
    notice_hook = load("kit_update_notice", NOTICE)

FAKE_LAUNCHCTL = r'''#!/bin/sh
exec "%(python)s" - "$@" <<'EOF'
import json, os, plistlib, sys
state_path = os.environ["FAKE_LAUNCHCTL_STATE"]
try:
    state = json.load(open(state_path))
except (OSError, ValueError):
    state = {"loaded": {}, "calls": []}
args = sys.argv[1:]
state["calls"].append(args)
code, said = 0, ""
if args[:1] == ["print"]:
    label = args[1].split("/", 2)[2]
    if label not in state["loaded"]:
        code, said = 113, "Could not find service \"%%s\" in domain" %% label
elif args[:1] == ["bootstrap"]:
    with open(args[2], "rb") as handle:
        label = plistlib.load(handle)["Label"]
    if os.environ.get("FAKE_LAUNCHCTL_REFUSE") or label in state["loaded"]:
        code, said = 5, "Bootstrap failed: 5: Input/output error"
    else:
        state["loaded"][label] = args[2]
elif args[:1] == ["bootout"]:
    label = args[1].split("/", 2)[2]
    if label in state["loaded"]:
        del state["loaded"][label]
    else:
        code, said = 3, "Boot-out failed: 3: No such process"
json.dump(state, open(state_path, "w"))
if said:
    print(said)
sys.exit(code)
EOF
'''

# The stand-in install.py: records its call, can fail or hang on request, and leaves the record the real one leaves.
FAKE_INSTALL = r'''import importlib.util, json, os, subprocess, sys, time
home = os.environ["KIT_HOME"]
root = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
seen = {name: os.environ.get(name) for name in ("GIT_TERMINAL_PROMPT", "GCM_INTERACTIVE", "GIT_SSH_COMMAND")}
with open(os.path.join(home, "install-calls.jsonl"), "a") as handle:
    handle.write(json.dumps({"args": args, "env": seen, "stdin": os.isatty(0)}) + "\n")
print("stand-in install from " + root)
if os.environ.get("FAKE_INSTALL_SLEEP"):
    child = subprocess.Popen(["sleep", os.environ["FAKE_INSTALL_SLEEP"]])
    with open(os.path.join(home, "install-pids.txt"), "w") as handle:
        handle.write("%d %d" % (os.getpid(), child.pid))
    time.sleep(float(os.environ["FAKE_INSTALL_SLEEP"]))
if os.environ.get("FAKE_INSTALL_EXIT"):
    print("stand-in install failing")
    sys.exit(int(os.environ["FAKE_INSTALL_EXIT"]))
spec = importlib.util.spec_from_file_location("kit_schedule", os.environ["FAKE_KIT_SCHEDULE"])
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)
schedule.save_install_record(root, home, args[args.index("--permissions") + 1], "--no-hook-merge" in args)
'''

SETTINGS = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [
    {"type": "command", "command": "python3 \"%USERPROFILE%/.claude/hooks/kit-update-notice.py\""}]}]}}


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # a child that exited but was not reaped yet is a zombie, which is gone for this purpose
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], stdout=subprocess.PIPE,
                           universal_newlines=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


@unittest.skipIf(os.name == "nt", "the launchd job is macOS; install.ps1 has its own suite on Windows")
class Sandbox(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="kit-schedule-test-"))
        self.home_dir = os.path.join(self.base, "user")
        self.kit_home = os.path.join(self.home_dir, ".claude")
        self.agents = os.path.join(self.base, "LaunchAgents")
        self.state = os.path.join(self.base, "launchctl.json")
        os.makedirs(self.kit_home)
        os.makedirs(self.agents)
        fake = os.path.join(self.base, "launchctl")
        with open(fake, "w") as handle:
            handle.write(FAKE_LAUNCHCTL % {"python": sys.executable})
        os.chmod(fake, 0o755)
        # the job of another kit home, which no test may touch
        self.decoy = os.path.join(self.agents, "local.kit-auto-update-deadbeef.plist")
        with open(self.decoy, "wb") as handle:
            plistlib.dump({"Label": "local.kit-auto-update-deadbeef", "ProgramArguments": ["another kit home"]}, handle)
        with open(self.state, "w") as handle:
            json.dump({"loaded": {"local.kit-auto-update-deadbeef": self.decoy}, "calls": []}, handle)
        self.env = mock.patch.dict(os.environ, {
            "HOME": self.home_dir, "KIT_LAUNCHAGENTS_DIR": self.agents, "KIT_LAUNCHCTL": fake,
            "FAKE_LAUNCHCTL_STATE": self.state, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
        self.env.start()
        for name in ("KIT_HOME", "FAKE_LAUNCHCTL_REFUSE", "XDG_CONFIG_HOME", "GIT_SSH", "GIT_SSH_COMMAND"):
            os.environ.pop(name, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.base, ignore_errors=True)

    def launchd(self):
        with open(self.state) as handle:
            return json.load(handle)

    def git(self, repo, *args):
        process = subprocess.run(["git", "-C", repo] + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(process.returncode, 0, "git %s: %s" % (" ".join(args), process.stdout))
        return process.stdout.strip()

    def make_repo(self, path):
        os.makedirs(path)
        self.git(path, "init", "--quiet")
        self.git(path, "config", "user.email", "suite-identity")
        self.git(path, "config", "user.name", "suite")
        return path


class JobTest(Sandbox):
    def setUp(self):
        Sandbox.setUp(self)
        self.clone = self.make_repo(os.path.join(self.base, "kit"))
        self.label = schedule.job_label(self.kit_home)
        self.plist = os.path.join(self.agents, self.label + ".plist")
        self.lines = []

    def register(self, **kwargs):
        with mock.patch.object(sys, "platform", "darwin"):
            return schedule.register(kwargs.pop("root", self.clone), self.kit_home, out=self.lines.append, **kwargs)

    def unregister(self, **kwargs):
        with mock.patch.object(sys, "platform", "darwin"):
            schedule.unregister(self.kit_home, out=self.lines.append, **kwargs)

    def test_one_job_per_kit_home_however_its_path_is_spelled(self):
        with mock.patch.object(sys, "platform", "darwin"):
            label = schedule.job_label(self.kit_home)
            self.assertRegex(label, r"^local\.kit-auto-update-[0-9a-f]{8}$")
            expected = hashlib.sha256(self.kit_home.lower().encode("utf-8")).hexdigest()[:8]
            self.assertEqual(label, "local.kit-auto-update-" + expected, "the hash is schedule.ps1's, on the folded path")
            for spelling in (self.kit_home + "/", self.kit_home + "/./", self.kit_home.upper(),
                             os.path.join(self.kit_home, "..", ".claude")):
                self.assertEqual(schedule.job_label(spelling), label, spelling)
            self.assertNotEqual(schedule.job_label(os.path.join(self.base, "other", ".claude")), label)

    def test_register_writes_and_loads_the_job(self):
        self.assertTrue(self.register())
        self.assertIn("auto-update  registered the daily job %s, at 05:00 or at the next sign-in after a missed day."
                      % self.label, self.lines)
        with open(self.plist, "rb") as handle:
            job = plistlib.load(handle)
        self.assertEqual(job["Label"], self.label)
        self.assertEqual(job["ProgramArguments"], ["/usr/bin/python3", os.path.join(self.kit_home, "kit-auto-update.py"),
                                                   "--dir", self.clone, "--kit-home", self.kit_home],
                         "the kit home's runner on the clone and the kit home, with no option")
        self.assertEqual(job["WorkingDirectory"], self.kit_home, "it starts in the kit home, not in the clone")
        self.assertEqual(job["StartCalendarInterval"], {"Hour": 5, "Minute": 0}, "every day at 05:00")
        self.assertIs(job["RunAtLoad"], True, "and at each sign-in, for a day the Mac was off")
        state = self.launchd()
        self.assertEqual(state["loaded"].get(self.label), self.plist)
        self.assertIn(["bootstrap", "gui/%d" % os.getuid(), self.plist], state["calls"], "bootstrapped in gui/<uid>")
        self.assertIn("local.kit-auto-update-deadbeef", state["loaded"], "the job of another kit home is still loaded")
        self.assertTrue(os.path.exists(self.decoy), "and its file is still there")
        with open(os.path.join(self.kit_home, "logs", "auto-update-last-day.txt")) as handle:
            self.assertEqual(handle.read().strip(), schedule.covered_day(datetime.datetime.now()),
                             "today is marked covered, so the start launchd makes on load does nothing")
        if shutil.which("plutil"):
            lint = subprocess.run(["plutil", "-lint", self.plist], stdout=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(lint.returncode, 0, lint.stdout)

    def test_register_again_leaves_a_loaded_unchanged_job_alone(self):
        self.register()
        calls = len(self.launchd()["calls"])
        before = os.stat(self.plist).st_mtime_ns
        self.assertTrue(self.register())
        new_calls = self.launchd()["calls"][calls:]
        self.assertFalse([call for call in new_calls if call[0] in ("bootout", "bootstrap")],
                         "no reload, so a run in progress is never stopped")
        self.assertEqual(os.stat(self.plist).st_mtime_ns, before)

    def test_register_with_another_clone_reloads_the_job(self):
        self.register()
        other = self.make_repo(os.path.join(self.base, "kit-b"))
        calls = len(self.launchd()["calls"])
        self.assertTrue(self.register(root=other))
        verbs = [call[0] for call in self.launchd()["calls"][calls:] if call[0] in ("bootout", "bootstrap")]
        self.assertEqual(verbs, ["bootout", "bootstrap"])
        with open(self.plist, "rb") as handle:
            self.assertEqual(plistlib.load(handle)["ProgramArguments"][3], other)

    def test_a_refused_job_is_a_failure(self):
        os.environ["FAKE_LAUNCHCTL_REFUSE"] = "1"
        self.assertFalse(self.register())
        self.assertTrue(self.lines[-1].startswith("auto-update  FAILED to register the daily job %s: launchctl "
                                                  "bootstrap exited 5" % self.label), self.lines)

    def test_a_folder_that_is_not_a_clone_registers_nothing(self):
        folder = os.path.join(self.base, "zip-download")
        os.makedirs(folder)
        self.assertTrue(self.register(root=folder), "not a failure, as in install.ps1")
        self.assertIn("is not a git clone", self.lines[0])
        self.assertFalse(os.path.exists(self.plist))
        self.assertEqual(self.launchd()["calls"], [])

    def test_a_dry_run_registers_and_removes_nothing(self):
        self.assertTrue(self.register(dry_run=True))
        self.assertIn("would register the daily job " + self.label, self.lines[0])
        self.assertIn("/usr/bin/python3 %s --dir %s --kit-home %s" % (
            os.path.join(self.kit_home, "kit-auto-update.py"), self.clone, self.kit_home), self.lines[1])
        self.assertNotRegex(self.lines[1], r"--permissions|--no-hook-merge", "the job carries no option")
        self.assertFalse(os.path.exists(self.plist))
        self.assertFalse(os.path.exists(os.path.join(self.kit_home, "logs")))
        self.register()
        calls = len(self.launchd()["calls"])
        self.unregister(dry_run=True)
        self.assertEqual(self.lines[-1], "auto-update  would remove the daily job " + self.label)
        self.assertTrue(os.path.exists(self.plist))
        self.assertFalse([call for call in self.launchd()["calls"][calls:] if call[0] == "bootout"])

    def test_unregister_removes_only_this_kit_homes_job(self):
        self.register()
        self.unregister()
        self.assertEqual(self.lines[-1], "auto-update  removed the daily job %s; nothing else was changed, and the logs "
                                         "stay in %s" % (self.label, os.path.join(self.kit_home, "logs")))
        state = self.launchd()
        self.assertNotIn(self.label, state["loaded"])
        self.assertFalse(os.path.exists(self.plist))
        self.assertIn("local.kit-auto-update-deadbeef", state["loaded"])
        self.assertTrue(os.path.exists(self.decoy))
        self.unregister()
        self.assertEqual(self.lines[-1], "auto-update  no daily job %s for %s to remove" % (self.label, self.kit_home))

    def test_linux_has_no_daily_job_yet(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertFalse(schedule.register(self.clone, self.kit_home, out=self.lines.append))
            schedule.unregister(self.kit_home, out=self.lines.append)
        self.assertEqual(len(self.lines), 2)
        self.assertIn("macOS and Windows only for now", self.lines[0])
        self.assertFalse(os.path.exists(self.plist))
        self.assertFalse(os.path.exists(os.path.join(self.kit_home, "logs")))
        self.assertEqual(self.launchd()["calls"], [])

    def test_the_runner_and_the_registration_agree_on_the_day(self):
        for clock in ("2026-11-20 04:58:00", "2026-11-20 04:59:30", "2026-11-20 05:00:00", "2026-11-20 23:59:59",
                      "2026-11-21 00:00:00", "2026-03-08 05:00:00"):
            now = datetime.datetime.strptime(clock, "%Y-%m-%d %H:%M:%S")
            self.assertEqual(schedule.covered_day(now), runner.covered_day(now), clock)
        self.assertEqual(runner.covered_day(datetime.datetime(2026, 11, 20, 4, 58)), "2026-11-19")
        self.assertEqual(runner.covered_day(datetime.datetime(2026, 11, 20, 4, 59, 30)), "2026-11-20",
                         "a start a moment before 05:00 is that day's")


class RecordTest(Sandbox):
    def test_the_record_holds_the_options_and_the_commit(self):
        clone = self.make_repo(os.path.join(self.base, "kit"))
        with open(os.path.join(clone, "README.md"), "w") as handle:
            handle.write("kit\n")
        self.git(clone, "add", "-A")
        self.git(clone, "commit", "--quiet", "--no-verify", "-m", "first")
        head = self.git(clone, "rev-parse", "HEAD")
        schedule.save_install_record(clone, self.kit_home, "ask", False)
        path = os.path.join(self.kit_home, ".kit-install.json")
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(list(record), ["permissions", "noHookMerge", "commit", "clone"], "schedule.ps1's keys")
        self.assertEqual(record, {"permissions": "ask", "noHookMerge": False, "commit": head, "clone": clone})
        os.utime(path, (1000000000, 1000000000))
        schedule.save_install_record(clone, self.kit_home, "ask", False)
        self.assertEqual(os.stat(path).st_mtime, 1000000000, "a run that changes nothing leaves the record as it was")
        schedule.save_install_record(clone, self.kit_home, "bypass", True)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["noHookMerge"], True)
        folder = os.path.join(self.base, "zip-download")
        os.makedirs(folder)
        schedule.save_install_record(folder, self.kit_home, "ask", False)
        with open(path, encoding="utf-8") as handle:
            self.assertIsNone(json.load(handle)["commit"], "no clone, no commit")


class RunTest(Sandbox):
    """The run the job starts, against a sandbox origin and clone."""

    def setUp(self):
        Sandbox.setUp(self)
        self.origin = self.make_repo(os.path.join(self.base, "origin"))
        self.write(self.origin, "install.py", FAKE_INSTALL)
        self.write(self.origin, "README.md", "kit\n")
        self.write(self.origin, "claude/settings.json", json.dumps(SETTINGS, indent=2) + "\n")
        self.write(self.origin, "claude/hooks/kit-update-notice.py", "pass\n")
        self.git(self.origin, "add", "-A")
        self.git(self.origin, "commit", "--quiet", "--no-verify", "-m", "first")
        self.clone = os.path.join(self.base, "kit")
        self.git(self.base, "clone", "--quiet", self.origin, self.clone)
        self.logs = os.path.join(self.kit_home, "logs")
        self.pending = os.path.join(self.logs, "auto-update-pending.jsonl")
        self.record = os.path.join(self.kit_home, ".kit-install.json")
        self.runner = os.path.join(self.kit_home, "kit-auto-update.py")
        shutil.copyfile(RUNNER, self.runner)
        self.set_mode("default")
        os.environ["FAKE_KIT_SCHEDULE"] = SCHEDULE
        for name in ("FAKE_INSTALL_EXIT", "FAKE_INSTALL_SLEEP", "KIT_AUTO_UPDATE_SCHEDULED",
                     "KIT_AUTO_UPDATE_TIME_LIMIT", "KIT_AUTO_UPDATE_LOCK_WAIT"):
            os.environ.pop(name, None)
        self.hand_install(self.clone, "ask")

    # ---- helpers ------------------------------------------------------------------------------------------------

    def write(self, repo, name, text):
        path = os.path.join(repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def set_mode(self, mode):
        with open(os.path.join(self.kit_home, "settings.json"), "w") as handle:
            json.dump({"permissions": {"defaultMode": mode}}, handle, indent=2)

    def hand_install(self, clone, permissions, no_hook_merge=False):
        """An install by hand: it leaves the record of the clone it ran from."""
        schedule.save_install_record(clone, self.kit_home, permissions, no_hook_merge)

    def upstream(self, message, files=("README.md",)):
        for name in files:
            with open(os.path.join(self.origin, name), "a") as handle:
                handle.write("# %s\n" % message)
        self.git(self.origin, "add", "-A")
        self.git(self.origin, "commit", "--quiet", "--no-verify", "-m", message)
        return self.git(self.origin, "rev-parse", "HEAD")

    def head(self, repo=None):
        return self.git(repo or self.clone, "rev-parse", "HEAD")

    def day_log(self):
        return os.path.join(self.logs, "auto-update-%s.log" % datetime.date.today().strftime("%Y-%m-%d"))

    def log_lines(self):
        if not os.path.exists(self.day_log()):
            return []
        with open(self.day_log(), encoding="utf-8") as handle:
            return handle.read().splitlines()

    def daily(self, argv=None, **env):
        """One run, started as the job starts it, with the sandbox environment; answers its exit code."""
        full = dict(os.environ)
        full.update(env)
        command = argv or [sys.executable, self.runner, "--dir", self.clone, "--kit-home", self.kit_home]
        process = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 universal_newlines=True, env=full, cwd=self.kit_home, timeout=120)
        self.assertEqual(process.stdout, "", "the run prints nothing; its log is where the outcome is")
        return process.returncode

    def last(self):
        lines = self.log_lines()
        return lines[-1] if lines else ""

    def pending_runs(self):
        if not os.path.exists(self.pending):
            return []
        with open(self.pending, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def clear_pending(self):
        if os.path.exists(self.pending):
            os.remove(self.pending)

    def installs(self):
        path = os.path.join(self.kit_home, "install-calls.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def notice(self):
        process = subprocess.run([sys.executable, NOTICE], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 universal_newlines=True, env=dict(os.environ, KIT_HOME=self.kit_home), timeout=60)
        return json.loads(process.stdout)["systemMessage"] if process.stdout.strip() else ""

    def kit_home_state(self):
        """The kit home as the installer leaves it; the logs folder is the run's own record, so it is left out."""
        found = {}
        for folder, dirs, files in os.walk(self.kit_home):
            dirs[:] = [name for name in dirs if os.path.join(folder, name) != self.logs]
            for name in files:
                with open(os.path.join(folder, name), "rb") as handle:
                    found[os.path.join(folder, name)] = hashlib.sha256(handle.read()).hexdigest()
        return found

    def assert_shapes(self):
        """Every run in the log opens with the start line and closes with the end line the session-start hook reads."""
        lines = self.log_lines()
        starts = [line for line in lines if " start: " in line]
        self.assertTrue(starts)
        for line in starts:
            self.assertRegex(line, notice_hook.START)
        ends = [line for line in lines if re.match(r"^\d\d:\d\d:\d\d (done|failed|skipped):", line)]
        self.assertEqual(len(ends), len(starts), "one end line for each start line")
        for line in ends:
            self.assertRegex(line, notice_hook.END)

    # ---- the runs -----------------------------------------------------------------------------------------------

    def test_a_clone_already_installed_installs_nothing(self):
        before = self.kit_home_state()
        self.assertEqual(self.daily(), 0)
        self.assertRegex(self.last(), r"^\d\d:\d\d:\d\d done: the kit is already installed at [0-9a-f]{7}; nothing to "
                                      r"install$")
        self.assertEqual(self.installs(), [], "install.py did not run")
        self.assertEqual(self.kit_home_state(), before)
        self.assertEqual(self.pending_runs(), [])
        self.assertEqual(self.notice(), "")
        self.assert_shapes()

    def test_an_upstream_commit_with_a_new_hook_is_installed_and_reported(self):
        installed = self.head()
        settings = os.path.join(self.origin, "claude", "settings.json")
        with open(settings) as handle:
            data = json.load(handle)
        data["hooks"]["SessionStart"].append({"matcher": "startup", "hooks": [{"type": "command",
                                              "command": "python3 \"%USERPROFILE%/.claude/hooks/zz-new-hook.py\""}]})
        self.write(self.origin, "claude/settings.json", json.dumps(data, indent=2) + "\n")
        self.write(self.origin, "claude/hooks/zz-new-hook.py", "pass\n")
        new = self.upstream("upstream change with a hook")
        self.assertEqual(self.daily(), 0)
        self.assertEqual(self.head(), new, "the clone was pulled")
        self.assertEqual(self.last()[9:], "done: updated to %s, 3 files changed, new hooks: zz-new-hook.py" % new[:7])
        calls = self.installs()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["args"], ["--unattended", "--permissions", "ask"],
                         "unattended, in the mode settings.json has, hooks merged as the last install chose")
        self.assertEqual(calls[0]["env"], {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
                                           "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}, "never a prompt")
        self.assertFalse(calls[0]["stdin"], "nothing to answer on")
        self.assertIn("ssh: ssh -o BatchMode=yes", [line[9:] for line in self.log_lines()])
        with open(self.record) as handle:
            self.assertEqual(json.load(handle)["commit"], new, "the install it ran left the new record")
        runs = self.pending_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual({key: runs[0][key] for key in ("result", "from", "to", "files", "hooks")},
                         {"result": "updated", "from": installed[:7], "to": new[:7], "files": 3,
                          "hooks": ["zz-new-hook.py"]})
        self.assertEqual(runs[0]["log"], self.day_log())
        self.assertRegex(runs[0]["time"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        line = self.notice()
        self.assertIn("now at %s (was %s), 3 files changed, new hooks: zz-new-hook.py" % (new[:7], installed[:7]), line)
        self.assertIn("Log: " + self.day_log(), line)
        self.assertEqual(self.notice(), "", "the next session start prints nothing")
        self.assert_shapes()

    def test_the_last_hand_installs_choices_are_replayed(self):
        self.set_mode("bypassPermissions")
        self.hand_install(self.clone, "bypass", no_hook_merge=True)
        self.upstream("upstream change two")
        self.assertEqual(self.daily(), 0)
        self.assertEqual(self.installs()[-1]["args"], ["--unattended", "--permissions", "bypass", "--no-hook-merge"])
        self.assertIn("install.py --unattended --permissions bypass --no-hook-merge (the mode read from settings.json, "
                      "--no-hook-merge as the last install had it)", [line[9:] for line in self.log_lines()])
        self.set_mode("default")
        self.hand_install(self.clone, "ask")
        self.upstream("upstream change three")
        self.daily()
        self.assertEqual(self.installs()[-1]["args"], ["--unattended", "--permissions", "ask"])

    def test_a_hand_update_leaves_nothing_to_install(self):
        new = self.upstream("upstream change four")
        self.git(self.clone, "pull", "--quiet", "--ff-only")
        self.hand_install(self.clone, "ask")
        self.daily()
        self.assertEqual(self.last()[9:], "done: the kit is already installed at %s; nothing to install" % new[:7])
        self.assertEqual(self.pending_runs(), [])

    def test_an_install_from_another_clone_is_not_undone(self):
        other = os.path.join(self.base, "kit-b")
        self.git(self.base, "clone", "--quiet", self.origin, other)
        self.hand_install(other, "ask")
        new = self.upstream("upstream change after the other install")
        head = self.head()
        before = self.kit_home_state()
        self.assertEqual(self.daily(), 0)
        self.assertEqual(self.last()[9:], "skipped: the last install came from %s; the daily update waits for an "
                                          "install from %s" % (other, self.clone))
        self.assertEqual(self.head(), head, "the job's clone was not pulled")
        self.assertEqual(self.kit_home_state(), before)
        runs = self.pending_runs()
        self.assertEqual([run["result"] for run in runs], ["waiting"])
        self.assertIn("paused: the last install came from " + other, self.notice())
        self.daily()
        self.assertIn("skipped: the last install came from", self.last())
        self.assertEqual(self.pending_runs(), [], "said once")
        self.hand_install(self.clone, "ask")
        self.daily()
        self.assertEqual(self.last()[9:], "done: updated to %s, 1 files changed" % new[:7], "it resumes by itself")
        self.assertFalse(os.path.exists(os.path.join(self.logs, "auto-update-waiting.txt")), "and forgets the wait")
        self.assert_shapes()

    def test_a_lost_record_never_merges_hooks(self):
        os.remove(self.record)
        new = self.upstream("upstream change with no record")
        self.daily()
        self.assertIn("no readable install record, so --no-hook-merge: an unknown choice never merges hooks into "
                      "settings.json", [line[9:] for line in self.log_lines()])
        self.assertEqual(self.installs()[-1]["args"], ["--unattended", "--permissions", "ask", "--no-hook-merge"])
        with open(self.record) as handle:
            record = json.load(handle)
        self.assertEqual((record["commit"], record["noHookMerge"]), (new, True), "the install wrote the record again")

    def test_a_failed_install_is_reported_and_tried_again(self):
        installed = self.head()
        broken = self.upstream("upstream change the install fails on")
        self.assertEqual(self.daily(FAKE_INSTALL_EXIT="3"), 1)
        self.assertEqual(self.last()[9:], "failed: install.py exited 3 after the pull to %s; the clone is at that "
                                          "commit, the kit home as that run left it (its output is above in the log), "
                                          "and the next run tries the install again" % broken[:7])
        self.assertIn("  install: stand-in install failing", [line[9:] for line in self.log_lines()])
        with open(self.record) as handle:
            self.assertEqual(json.load(handle)["commit"], installed, "the record did not move")
        self.assertIn("failed: install.py exited 3", self.notice())
        fixed = self.upstream("upstream change after the fix")
        self.assertEqual(self.daily(), 0)
        self.assertEqual(self.last()[9:16], "done: u")
        self.assertEqual(self.pending_runs()[-1]["from"], installed[:7], "reported from the last commit installed")
        self.assertEqual(self.pending_runs()[-1]["to"], fixed[:7])
        self.assert_shapes()

    def test_a_held_lock_skips_with_no_notice(self):
        new = self.upstream("upstream change five")
        before = self.kit_home_state()
        with open(os.path.join(self.clone, ".git", "kit-auto-update.lock"), "a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self.assertEqual(self.daily(KIT_AUTO_UPDATE_LOCK_WAIT="2"), 0)
        self.assertRegex(self.last(), r" skipped: another update of the clone at .* held its lock for 2 s; this run "
                                      r"did nothing$")
        self.assertNotEqual(self.head(), new, "nothing was pulled")
        self.assertEqual(self.kit_home_state(), before)
        self.assertEqual(self.pending_runs(), [])
        self.assertEqual(self.notice(), "", "a skip is no failure at the session start either")

    def test_an_ssh_of_the_users_own_is_left_alone(self):
        self.git(self.clone, "config", "core.sshCommand", "ssh -i suite-key")
        self.daily()
        self.assertIn("ssh: the user's own (GIT_SSH_COMMAND, GIT_SSH or core.sshCommand), left as it is",
                      [line[9:] for line in self.log_lines()])
        self.git(self.clone, "config", "--unset", "core.sshCommand")
        self.daily(GIT_SSH="/usr/bin/false")
        self.assertEqual([line[9:] for line in self.log_lines() if " ssh: " in line][-1][:20], "ssh: the user's own ")
        self.daily()
        self.assertEqual([line[9:] for line in self.log_lines() if " ssh: " in line][-1], "ssh: ssh -o BatchMode=yes")

    def test_a_clone_with_changes_of_its_own_is_not_pulled(self):
        self.upstream("upstream change six")
        with open(os.path.join(self.clone, "README.md"), "a") as handle:
            handle.write("A local edit.\n")
        head = self.head()
        before = self.kit_home_state()
        self.assertEqual(self.daily(), 1)
        self.assertEqual(self.last()[9:], "failed: the clone at %s has changes of its own (1 files); nothing was "
                                          "pulled or installed" % self.clone)
        self.assertEqual(self.head(), head)
        self.assertEqual(self.kit_home_state(), before)
        self.assertEqual([run["result"] for run in self.pending_runs()], ["failed"])

    def test_a_pull_that_fails_installs_nothing(self):
        self.git(self.clone, "remote", "set-url", "origin", os.path.join(self.base, "no-such-origin"))
        before = self.kit_home_state()
        self.assertEqual(self.daily(), 1)
        self.assertRegex(self.last(), r" failed: git pull --ff-only in .* failed: fatal: .+; nothing was installed$",
                         "git's verdict, not its hint lines")
        self.assertEqual(self.kit_home_state(), before)
        self.assertIn("failed: git pull --ff-only in", self.notice())

    def test_a_clone_that_was_moved_is_still_reported(self):
        os.rename(self.clone, self.clone + "-moved")
        self.assertEqual(self.daily(), 1)
        self.assertEqual(self.last()[9:], "failed: the clone at %s is gone or is not a git clone; nothing was pulled "
                                          "or installed. Run install.py --auto-update from a clone to point the job "
                                          "at it" % self.clone)
        self.assertIn("is gone", self.pending_runs()[0]["line"])
        self.assert_shapes()

    def test_the_last_30_logs_are_kept(self):
        os.makedirs(self.logs, exist_ok=True)
        first = datetime.date(2020, 1, 1)
        for day in range(35):
            name = "auto-update-%s.log" % (first + datetime.timedelta(days=day)).strftime("%Y-%m-%d")
            with open(os.path.join(self.logs, name), "w") as handle:
                handle.write("old\n")
        self.daily()
        kept = sorted(name for name in os.listdir(self.logs) if runner.LOG_NAME.match(name))
        self.assertEqual(len(kept), 30)
        self.assertEqual(kept[-1], os.path.basename(self.day_log()))
        self.assertNotIn("auto-update-2020-01-01.log", kept)
        self.assertIn("auto-update-2020-02-04.log", kept)

    def test_the_time_limit_stops_the_install_and_everything_it_started(self):
        installed = self.head()
        new = self.upstream("upstream change that hangs the install")
        began = time.time()
        self.assertEqual(self.daily(KIT_AUTO_UPDATE_TIME_LIMIT="4", FAKE_INSTALL_SLEEP="60"), 1)
        self.assertLess(time.time() - began, 40, "the run stopped at its limit, not when the install would have ended")
        self.assertEqual(self.last()[9:], "failed: install.py did not finish within the run's limit of 4 s and was "
                                          "stopped after the pull to %s; the clone is at that commit, the kit home as "
                                          "that run left it (its output is above in the log), and the next run tries "
                                          "the install again" % new[:7])
        with open(os.path.join(self.kit_home, "install-pids.txt")) as handle:
            pids = [int(pid) for pid in handle.read().split()]
        self.assertEqual(len(pids), 2)
        for pid in pids:
            self.assertFalse(pid_alive(pid), "process %d was stopped" % pid)
        with open(self.record) as handle:
            self.assertEqual(json.load(handle)["commit"], installed, "the record did not move")
        line = self.notice()
        self.assertIn("failed: install.py did not finish", line)
        self.assertNotIn("never finished", line, "the run ended its own log, so it is reported once")
        self.assert_shapes()

    def test_a_start_by_launchd_does_the_days_work_once(self):
        stamp = os.path.join(self.logs, "auto-update-last-day.txt")
        os.makedirs(self.logs, exist_ok=True)
        today = runner.covered_day(datetime.datetime.now())
        with open(stamp, "w") as handle:
            handle.write(today + "\n")
        self.upstream("upstream change on a covered day")
        self.assertEqual(self.daily(KIT_AUTO_UPDATE_SCHEDULED="1"), 0)
        self.assertEqual(self.log_lines(), [], "a covered day: no log line, nothing done")
        self.assertEqual(self.installs(), [])
        self.assertEqual(self.daily(), 0, "a start that launchd did not make always runs")
        self.assertEqual(self.last()[9:16], "done: u")
        self.upstream("upstream change on the next day")
        with open(stamp, "w") as handle:
            handle.write("2020-01-01\n")
        lines = len(self.log_lines())
        self.assertEqual(self.daily(KIT_AUTO_UPDATE_SCHEDULED="1"), 0)
        self.assertGreater(len(self.log_lines()), lines, "a day not covered yet runs")
        with open(stamp) as handle:
            self.assertEqual(handle.read().strip(), today, "and is marked covered")
        self.assert_shapes()

    @unittest.skipUnless(os.path.exists("/usr/bin/python3"), "the job runs /usr/bin/python3")
    def test_the_registered_command_runs_and_its_load_start_does_nothing(self):
        lines = []
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertTrue(schedule.register(self.clone, self.kit_home, out=lines.append))
        with open(os.path.join(self.agents, schedule.job_label(self.kit_home) + ".plist"), "rb") as handle:
            job = plistlib.load(handle)
        new = self.upstream("upstream change for the job's own command")
        self.assertEqual(self.daily(job["ProgramArguments"], KIT_AUTO_UPDATE_SCHEDULED="1"), 0)
        self.assertEqual(self.log_lines(), [], "the start launchd makes on loading the job finds the day covered")
        os.remove(os.path.join(self.logs, "auto-update-last-day.txt"))
        self.assertEqual(self.daily(job["ProgramArguments"], KIT_AUTO_UPDATE_SCHEDULED="1"), 0)
        self.assertEqual(self.last()[9:], "done: updated to %s, 1 files changed" % new[:7],
                         "the job's own command line updates the kit")
        self.assert_shapes()


if __name__ == "__main__":
    unittest.main()
