#!/usr/bin/env python3
"""auto-update.py - the daily kit update on macOS, the port of install/auto-update.ps1. Every install copies it to
<kit home>/kit-auto-update.py, and the launchd job that install.py --auto-update registers (install/kit_schedule.py)
runs that copy, so a clone that was moved or deleted is logged and reported rather than stopping the updates in
silence. Not meant to be run by hand, though nothing breaks if it is.

    /usr/bin/python3 <kit home>/kit-auto-update.py --dir <clone> --kit-home <kit home>

It replays the last install, it never makes one up. Every install that finishes, by hand or by this run, writes
<kit home>/.kit-install.json: the options it ran with and the clone commit it installed. This run pulls the clone and
runs install.py only when the clone's commit differs from that record's, with --no-hook-merge as the record has it
and the permission mode read from the live settings.json (bypassPermissions is bypass, anything else, a missing or
unreadable file included, is ask). It passes --unattended, so install.py changes nothing outside the kit home: no
PATH, no Herdr, no launchd job. A failed install writes no record, so the next run tries it again.

It never waits on a question: git runs with GIT_TERMINAL_PROMPT=0, GCM_INTERACTIVE=never and, unless the user has an
ssh of their own (GIT_SSH_COMMAND, GIT_SSH or core.sshCommand), an ssh in batch mode; every child reads its input
from /dev/null. When the last install came from another clone than the job's, the run installs nothing and waits
for an install from the job's clone. One run at a time per clone: a run holds an exclusive lock on
<clone>/.git/kit-auto-update.lock for its whole length (the system lets go of it when the process ends, however it
ends), and a run that cannot take it within KIT_AUTO_UPDATE_LOCK_WAIT seconds (600) logs a skip line and leaves no
notice.

When it does something. launchd starts it at 05:00 (a Mac asleep then starts it when it wakes) and at every sign-in
(RunAtLoad), because launchd drops a calendar start the machine was powered off for. One start per day does the
work: a run that launchd started (its parent is launchd, pid 1; KIT_AUTO_UPDATE_SCHEDULED=1 stands in for that in the
tests) reads logs/auto-update-last-day.txt, the day whose 05:00 the last run covered, and ends at once, with no log
line, when that day is already covered. kit_schedule.py writes the same file when it registers the job, so the start
launchd makes on loading the job does nothing. A run started any other way always runs.

Its time limit is its own, since launchd has none: 30 minutes after its start (KIT_AUTO_UPDATE_TIME_LIMIT seconds in
the tests) the run stops the child it is waiting on, git or install.py with every process that child started, and
ends on a failed line. A run stopped from outside (the machine shut down mid-run) has a start line and no end line,
which the session-start hook reports once the start is older than the limit.

Everything goes to <kit home>/logs/auto-update-<yyyy-MM-dd>.log, and the last 30 of those are kept. Each run starts
with a start line and ends with a done, failed or skipped line, the shapes claude/hooks/kit-update-notice.py reads.
A missing clone, a clone with changes of its own, or a pull that fails is logged and nothing is installed. A run that
moved the kit, or failed, appends one JSON line to logs/auto-update-pending.jsonl; the session-start hook
kit-update-notice.py prints it once.

Standard library only, and Python 3.8 or later: /usr/bin/python3 is what the job runs.
"""
import argparse
import datetime
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

LIMIT_SECONDS = 30 * 60  # the session-start hook's LIMIT_MINUTES; the two must agree
LOCK_WAIT = 600
KEEP_LOGS = 30
DAY_START_HOURS = 5  # the job's 05:00 (kit_schedule.AT_HOUR); the two must agree
LOG_NAME = re.compile(r"^auto-update-\d{4}-\d{2}-\d{2}\.log$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
HOOK_SCRIPT = re.compile(r"hooks[/\\]+([A-Za-z0-9_.-]+\.(?:py|ps1|sh))")


class End(Exception):
    """The run is over; code is the process exit code."""

    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


def covered_day(now):
    """The day whose 05:00 is the latest one at now, as yyyy-mm-dd. A start up to a minute before 05:00 counts as that
    day's, so a timer that fires a moment early never skips its own day. kit_schedule.covered_day is the same."""
    return (now + datetime.timedelta(minutes=1) - datetime.timedelta(hours=DAY_START_HOURS)).strftime("%Y-%m-%d")


def same_path(path):
    """One spelling of a folder, to compare two: full path, no trailing separator, case folded where the file system
    ignores case (macOS by default)."""
    try:
        full = os.path.abspath(os.path.expanduser(path))
    except (TypeError, ValueError):
        return path
    return full.lower() if sys.platform == "darwin" else full


def scheduled_start():
    """True when launchd started this run: launchd is the parent of every job it starts."""
    return os.getppid() == 1 or os.environ.get("KIT_AUTO_UPDATE_SCHEDULED") == "1"


def widen_path():
    """launchd starts a job with PATH /usr/bin:/bin:/usr/sbin:/sbin. A git that Homebrew installed lives outside it, so
    its folders go after those, where a git the shell finds is found by the job too."""
    parts = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    for folder in ("/opt/homebrew/bin", "/usr/local/bin"):
        if folder not in parts and os.path.isdir(folder):
            parts.append(folder)
    os.environ["PATH"] = os.pathsep.join(parts)


def stop_group(process):
    """Stops a child and every process it started: each child leads a process group of its own."""
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(process.pid, sig)
        except OSError:
            return
        try:
            process.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


class Update(object):
    def __init__(self, clone, kit_home, limit):
        self.dir = clone
        self.kit_home = kit_home
        self.limit = limit
        self.logs = os.path.join(kit_home, "logs")
        self.started = datetime.datetime.now()
        self.deadline = time.time() + limit
        self.log_path = os.path.join(self.logs, "auto-update-" + self.started.strftime("%Y-%m-%d") + ".log")
        self.pending = os.path.join(self.logs, "auto-update-pending.jsonl")
        self.record = os.path.join(kit_home, ".kit-install.json")
        self.lock = None  # held open for the whole run

    # ---- the log and the notice --------------------------------------------------------------------------------

    def log(self, line):
        with open(self.log_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("%s %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), line))

    def notice(self, entry):
        entry["time"] = self.started.strftime("%Y-%m-%d %H:%M")
        entry["log"] = self.log_path
        with open(self.pending, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, separators=(",", ":"), ensure_ascii=False) + "\n")

    def fail(self, why):
        self.log("failed: " + why)
        self.notice({"result": "failed", "line": why})
        raise End(1)

    # ---- the day guard -----------------------------------------------------------------------------------------

    def due(self):
        """False when the day this start would cover is already covered; otherwise records it and answers True."""
        day = covered_day(datetime.datetime.now())
        stamp = os.path.join(self.logs, "auto-update-last-day.txt")
        try:
            with open(stamp, encoding="utf-8-sig") as handle:
                last = handle.read().strip()
        except OSError:
            last = ""
        if last >= day and scheduled_start():
            return False
        if last < day:
            with open(stamp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(day + "\n")
        return True

    # ---- children ----------------------------------------------------------------------------------------------

    def limit_text(self):
        if self.limit % 60 == 0:
            return "%d minutes" % (self.limit // 60)
        return "%d s" % self.limit

    def child(self, argv, prefix=None, stopped=None, env=None, merge=True):
        """Runs one child to its end or to the run's deadline. Returns its output lines and exit code; each non-blank
        line goes to the log behind prefix when one is given. Past the deadline the child and every process it
        started are stopped, and the run fails with the stopped line."""
        remaining = self.deadline - time.time()
        if remaining <= 0:
            self.fail(stopped)
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT if merge else subprocess.DEVNULL,
                                       env=env, start_new_session=True)
        except OSError as error:
            if prefix:
                self.log("%s%s" % (prefix, error))
            return [str(error)], 127
        timed_out = False
        try:
            output, _ = process.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_group(process)
            try:
                output, _ = process.communicate(timeout=10)
            except (subprocess.TimeoutExpired, ValueError):
                output = b""
        lines = output.decode("utf-8", "replace").splitlines() if output else []
        if prefix:
            for line in lines:
                if line.strip():
                    self.log(prefix + line)
        if timed_out:
            self.fail(stopped)
        return lines, process.returncode

    def git(self, args, log=True, merge=True):
        what = "git %s in %s did not finish within the run's limit of %s and was stopped; nothing was installed" % (
            " ".join(args), self.dir, self.limit_text())
        return self.child(["git", "-C", self.dir] + list(args), prefix="  git: " if log else None, stopped=what,
                          merge=merge)

    def hook_scripts(self, commit):
        """The hook scripts the kit's settings.json names at a commit, or None when git cannot show it."""
        lines, code = self.git(["show", commit + ":claude/settings.json"], log=False, merge=False)
        if code != 0:
            return None
        found = []
        for name in HOOK_SCRIPT.findall("\n".join(lines)):
            if name not in found:
                found.append(name)
        return found

    # ---- what the last install left ----------------------------------------------------------------------------

    def read_record(self):
        """The last finished install's record, or None."""
        try:
            with open(self.record, encoding="utf-8-sig") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def live_permissions(self):
        """The mode the user has now, read from the settings.json in use: an automatic run never changes it."""
        try:
            with open(os.path.join(self.kit_home, "settings.json"), encoding="utf-8-sig") as handle:
                mode = json.load(handle)["permissions"]["defaultMode"]
        except (OSError, ValueError, KeyError, TypeError):
            mode = None
        return "bypass" if mode == "bypassPermissions" else "ask"

    # ---- the run -----------------------------------------------------------------------------------------------

    def keep_last_logs(self):
        names = sorted((name for name in os.listdir(self.logs) if LOG_NAME.match(name)), reverse=True)
        for name in names[KEEP_LOGS:]:
            try:
                os.remove(os.path.join(self.logs, name))
            except OSError:
                pass

    def take_lock(self):
        wait = LOCK_WAIT
        if re.match(r"^\d+$", os.environ.get("KIT_AUTO_UPDATE_LOCK_WAIT", "")):
            wait = int(os.environ["KIT_AUTO_UPDATE_LOCK_WAIT"])
        until = min(time.time() + wait, self.deadline)
        self.lock = open(os.path.join(self.dir, ".git", "kit-auto-update.lock"), "a+")
        while True:
            try:
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if time.time() >= until:
                    self.log("skipped: another update of the clone at %s held its lock for %d s; this run did nothing"
                             % (self.dir, wait))
                    raise End(0)
                time.sleep(min(2, max(0.1, until - time.time())))

    def run(self):
        self.log("start: clone %s, kit home %s" % (self.dir, self.kit_home))
        self.keep_last_logs()

        if not os.path.isdir(os.path.join(self.dir, ".git")):
            self.fail("the clone at %s is gone or is not a git clone; nothing was pulled or installed. Run install.py "
                      "--auto-update from a clone to point the job at it" % self.dir)
        self.take_lock()

        if not shutil.which("git"):
            self.fail("git is not on PATH; nothing was pulled or installed")

        # ssh in batch mode, so a key with a passphrase fails the pull instead of waiting; but only when the user has
        # no ssh of their own (GIT_SSH_COMMAND, GIT_SSH, or core.sshCommand), which is left as it is.
        own, _ = self.git(["config", "--get", "core.sshCommand"], log=False, merge=False)
        if os.environ.get("GIT_SSH_COMMAND") or os.environ.get("GIT_SSH") or "".join(own).strip():
            self.log("ssh: the user's own (GIT_SSH_COMMAND, GIT_SSH or core.sshCommand), left as it is")
        else:
            os.environ["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
            self.log("ssh: ssh -o BatchMode=yes")

        # The last finished install, by hand or by this job (kit_schedule.save_install_record). One that came from
        # another clone is the user's latest choice: the job's clone, older or newer, must not undo it, so nothing is
        # pulled or installed until an install comes from the job's clone again. Said once in the notice.
        prior = self.read_record()
        waiting = os.path.join(self.logs, "auto-update-waiting.txt")
        if prior and prior.get("clone") and same_path(str(prior["clone"])) != same_path(self.dir):
            why = "the last install came from %s; the daily update waits for an install from %s" % (
                prior["clone"], self.dir)
            self.log("skipped: " + why)
            try:
                with open(waiting, encoding="utf-8-sig") as handle:
                    said = (handle.read().splitlines() or [""])[0]
            except OSError:
                said = ""
            if said != str(prior["clone"]):
                with open(waiting, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(str(prior["clone"]) + "\n")
                self.notice({"result": "waiting", "line": why})
            raise End(0)
        if os.path.exists(waiting):
            os.remove(waiting)

        lines, code = self.git(["rev-parse", "HEAD"])
        if code != 0 or not lines:
            self.fail("git could not read the clone at %s; nothing was pulled or installed" % self.dir)
        before = lines[-1].strip()
        lines, _ = self.git(["status", "--porcelain", "--untracked-files=no"])
        changes = [line for line in lines if line.strip()]
        if changes:
            self.fail("the clone at %s has changes of its own (%d files); nothing was pulled or installed"
                      % (self.dir, len(changes)))
        lines, code = self.git(["pull", "--ff-only"])
        if code != 0:
            # git's own verdict, not the hint lines it prints after it ("and the repository exists.")
            said = [line.strip() for line in lines if line.strip()]
            verdict = [line for line in said if line.startswith(("fatal:", "error:"))]
            self.fail("git pull --ff-only in %s failed: %s; nothing was installed"
                      % (self.dir, (verdict or said or ["exit %d" % code])[-1]))
        lines, code = self.git(["rev-parse", "HEAD"])
        if code != 0 or not lines:
            self.fail("git could not read the clone at %s after the pull; nothing was installed" % self.dir)
        after = lines[-1].strip()

        # The record's commit equal to the clone's: nothing to install. No record (lost, or a kit installed before
        # records existed): install once, and report from the commit the clone had before the pull.
        start = str(prior.get("commit") or "") if prior else ""
        start = start if COMMIT.match(start) else None
        if start == after:
            self.log("done: the kit is already installed at %s; nothing to install" % after[:7])
            raise End(0)
        if not start:
            start = before
        self.log("kit at %s (installed %s)" % (after, start))

        flags = ["--unattended", "--permissions", self.live_permissions()]
        if not prior:
            # A choice nobody can read is never a reason to touch the user's settings.json; this install writes
            # the record.
            flags.append("--no-hook-merge")
            self.log("no readable install record, so --no-hook-merge: an unknown choice never merges hooks into "
                     "settings.json")
        elif prior.get("noHookMerge") is True:
            flags.append("--no-hook-merge")
        self.log("install.py %s (the mode read from settings.json, --no-hook-merge as the last install had it)"
                 % " ".join(flags))
        env = dict(os.environ, KIT_HOME=self.kit_home)
        stopped = ("install.py did not finish within the run's limit of %s and was stopped after the pull to %s; the "
                   "clone is at that commit, the kit home as that run left it (its output is above in the log), and "
                   "the next run tries the install again" % (self.limit_text(), after[:7]))
        python = sys.executable or "python3"
        _, code = self.child([python, os.path.join(self.dir, "install.py")] + flags, prefix="  install: ",
                             stopped=stopped, env=env)
        if code != 0:
            self.fail("install.py exited %d after the pull to %s; the clone is at that commit, the kit home as that "
                      "run left it (its output is above in the log), and the next run tries the install again"
                      % (code, after[:7]))

        if after != start:
            names, _ = self.git(["diff", "--name-only", start, after], log=False, merge=False)
            files = len([name for name in names if name.strip()])
            old = self.hook_scripts(start)
            new = self.hook_scripts(after)
            hooks = [name for name in new if name not in old] if old is not None and new is not None else []
            self.notice({"result": "updated", "from": start[:7], "to": after[:7], "files": files, "hooks": hooks})
            self.log("done: updated to %s, %d files changed%s"
                     % (after[:7], files, (", new hooks: " + ", ".join(hooks)) if hooks else ""))
        else:
            self.log("done: installed %s, the commit the clone already had" % after[:7])
        raise End(0)


def main(argv=None):
    parser = argparse.ArgumentParser(description="The daily kit update the launchd job runs.")
    parser.add_argument("--dir", required=True, help="the clone the kit is installed from")
    parser.add_argument("--kit-home", required=True, help="the kit home it installs into")
    args = parser.parse_args(argv)

    # Never a prompt: no terminal question, no credential dialog, no ssh passphrase.
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    os.environ["GCM_INTERACTIVE"] = "never"
    widen_path()
    limit = LIMIT_SECONDS
    if re.match(r"^\d+$", os.environ.get("KIT_AUTO_UPDATE_TIME_LIMIT", "")):
        limit = max(1, int(os.environ["KIT_AUTO_UPDATE_TIME_LIMIT"]))

    update = Update(args.dir, args.kit_home, limit)
    try:
        os.makedirs(update.logs, exist_ok=True)
        if not update.due():
            return 0
        update.run()
    except End as end:
        return end.code
    except Exception as error:  # noqa: BLE001 - any error still ends the log with a failed line and a notice
        why = "the run stopped on an error of its own (%s: %s); the next run tries again" % (
            type(error).__name__, error)
        try:
            update.log("failed: " + why)
            update.notice({"result": "failed", "line": why})
        except OSError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
