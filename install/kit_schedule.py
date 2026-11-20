"""kit_schedule.py - the daily kit update on macOS, --auto-update on install.py: a launchd job of this user, loaded
with no elevation, that runs <kit home>/kit-auto-update.py (install/auto-update.py, which every install copies there)
against the clone every day. --no-auto-update removes it. The port of install/schedule.ps1, imported by install.py
by file path.

    register(root, kit_home, dry_run=False, out=print) -> bool
    unregister(kit_home, dry_run=False, out=print) -> None
    save_install_record(root, kit_home, permissions, no_hook_merge) -> None

The job runs the copy in the kit home, not the one in the clone, so a clone that was moved or deleted is still logged
and reported instead of stopping the updates in silence. It carries the clone and the kit home and nothing else: the
daily run replays the last finished install's record (save_install_record, written by every install, by hand or
daily), reads the permission mode from the live settings.json, and installs with --unattended, which leaves
everything outside the kit home alone.

One job per kit home: a LaunchAgent in ~/Library/LaunchAgents whose label ends in kit-auto-update- and a short hash
of the kit home's path in one spelling (full path, no trailing separator, case folded on macOS, whose file system
ignores case by default; Get-KitHomeKey in schedule.ps1 is the same rule), so ~/.claude and ~/.CLAUDE/ are one job,
two kit homes on one account keep two, and --no-auto-update removes only its own, with no clone needed.

When it runs. StartCalendarInterval starts it at 05:00, and launchd starts a calendar job the Mac slept through when
it wakes, but not one it was powered off for. RunAtLoad covers that: launchd loads the job at every sign-in and starts
it then. The runner does the work once per day: a start that launchd made (on load or by the calendar) ends at once,
with no log line, when the latest 05:00 is already covered (logs/auto-update-last-day.txt). Registering writes that
file first, so the start launchd makes when the job is loaded does nothing while this install is still running.

The time limit is the runner's own (30 minutes, then it stops its child and logs a failed line): launchd has no
execution limit to give a job. launchd never starts a second copy of a job that is running.

The job is loaded with launchctl bootstrap gui/<uid> <plist> and removed with launchctl bootout gui/<uid>/<label>,
with no elevation. A job whose plist is unchanged and that is loaded is left as it is, so a hand install with
--auto-update never stops a daily run that is in progress.

On Linux there is no daily job yet: register says so in one line and answers False.

For the tests: KIT_LAUNCHAGENTS_DIR replaces ~/Library/LaunchAgents and KIT_LAUNCHCTL replaces /bin/launchctl, so no
test reaches the real launchd.
"""
import collections
import datetime
import hashlib
import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
import time
import unicodedata

AT_HOUR = 5  # 05:00, early, before a working day starts; install/auto-update.py DAY_START_HOURS is the same
AT_MINUTE = 0
PYTHON = "/usr/bin/python3"  # on every Mac with the command line tools, whatever the shell's PATH holds
RUNNER = "kit-auto-update.py"
LABEL_PREFIX = "local."
# Folders macOS guards (TCC): a background job is refused access to them unless the user grants it.
GUARDED = ("Desktop", "Documents", "Downloads", os.path.join("Library", "Mobile Documents"))


def kit_home_path(kit_home):
    """The kit home as a full path with no trailing separator."""
    return os.path.abspath(os.path.expanduser(kit_home))


def kit_home_key(kit_home):
    """One spelling per kit home, for the job's name: the full path, in one Unicode form (a path typed and the same
    path read from the file system can differ there), case folded on macOS."""
    key = unicodedata.normalize("NFC", kit_home_path(kit_home))
    return key.lower() if sys.platform == "darwin" else key


def auto_update_name(kit_home):
    """kit-auto-update-<8 hex>, the first 8 hex digits of the SHA-256 of the key's UTF-8 bytes, as schedule.ps1."""
    return "kit-auto-update-" + hashlib.sha256(kit_home_key(kit_home).encode("utf-8")).hexdigest()[:8]


def job_label(kit_home):
    return LABEL_PREFIX + auto_update_name(kit_home)


def agents_dir():
    return os.environ.get("KIT_LAUNCHAGENTS_DIR") or os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents")


def plist_path(kit_home):
    return os.path.join(agents_dir(), job_label(kit_home) + ".plist")


def job_arguments(root, kit_home):
    """What launchd runs: the kit home's copy of the runner, on the clone and the kit home, with no option at all: the
    run reads them from the last install's record and the live settings.json."""
    home = kit_home_path(kit_home)
    return [PYTHON, os.path.join(home, RUNNER), "--dir", os.path.abspath(root), "--kit-home", home]


def job_plist(root, kit_home):
    home = kit_home_path(kit_home)
    return collections.OrderedDict([
        ("Label", job_label(kit_home)),
        ("ProgramArguments", job_arguments(root, kit_home)),
        ("WorkingDirectory", home),
        ("StartCalendarInterval", collections.OrderedDict([("Hour", AT_HOUR), ("Minute", AT_MINUTE)])),
        ("RunAtLoad", True),
    ])


def covered_day(now):
    """The day whose 05:00 is the latest one at now (install/auto-update.py covered_day is the same)."""
    return (now + datetime.timedelta(minutes=1) - datetime.timedelta(hours=AT_HOUR, minutes=AT_MINUTE)).strftime(
        "%Y-%m-%d")


def mark_covered(kit_home):
    """Marks the current day covered, so the start launchd makes when it loads the job does nothing: this install is
    the day's update. A day already marked stays marked."""
    logs = os.path.join(kit_home_path(kit_home), "logs")
    os.makedirs(logs, exist_ok=True)
    stamp = os.path.join(logs, "auto-update-last-day.txt")
    day = covered_day(datetime.datetime.now())
    try:
        with open(stamp, encoding="utf-8-sig") as handle:
            if handle.read().strip() >= day:
                return
    except OSError:
        pass
    with open(stamp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(day + "\n")


def launchctl(args):
    """Runs launchctl; returns its exit code and its last non-blank output line."""
    command = [os.environ.get("KIT_LAUNCHCTL") or "/bin/launchctl"] + list(args)
    process = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=60)
    lines = [line.strip() for line in process.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    return process.returncode, (lines[-1] if lines else "")


def domain():
    return "gui/%d" % os.getuid()


def is_loaded(label):
    code, _ = launchctl(["print", "%s/%s" % (domain(), label)])
    return code == 0


def guarded(path):
    home = os.path.expanduser("~")
    for name in GUARDED:
        folder = os.path.join(home, name)
        if path == folder or path.startswith(folder + os.sep):
            return folder
    return None


def read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def register(root, kit_home, dry_run=False, out=print):
    """Registers this kit home's daily job. False when it could not: no scheduler on this system, or launchd refused
    the job; the install then ends with exit 1 like install.ps1 does. A root that is not a git clone is no failure
    (install.ps1 does not count it as one either): it is said in one line and nothing is registered."""
    if sys.platform != "darwin":
        out("auto-update  not registered: the daily update is macOS and Windows only for now. Update by hand: git pull "
            "in the clone, then install.py.")
        return False
    label = job_label(kit_home)
    # A folder, not a file: a git worktree's .git is a file, and the run keeps its lock inside the .git folder.
    if not os.path.isdir(os.path.join(root, ".git")):
        out("auto-update  not registered: %s is not a git clone, and the daily run updates it with git pull. "
            "Install from a clone." % root)
        return True
    home = kit_home_path(kit_home)
    path = plist_path(kit_home)
    command = " ".join(shlex.quote(part) for part in job_arguments(root, kit_home))
    if dry_run:
        out("auto-update  would register the daily job %s, at 05:00 or at the next sign-in after a missed day:" % label)
        out("             " + command)
        out("             job file: " + path)
        return True
    try:
        data = plistlib.dumps(job_plist(root, kit_home), sort_keys=False)
        mark_covered(kit_home)
        loaded = is_loaded(label)
        if not (loaded and read_bytes(path) == data):
            if loaded:
                code, said = launchctl(["bootout", "%s/%s" % (domain(), label)])
                if code != 0 and is_loaded(label):
                    raise RuntimeError("launchctl bootout exited %d: %s" % (code, said))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            partial = path + ".partial"
            with open(partial, "wb") as handle:
                handle.write(data)
            os.replace(partial, path)
            # A job just booted out can still be on its way out, and bootstrap then answers 5: Input/output error.
            for attempt in range(5):
                if attempt:
                    time.sleep(1)
                code, said = launchctl(["bootstrap", domain(), path])
                if code == 0:
                    break
            if code != 0:
                raise RuntimeError("launchctl bootstrap exited %d: %s" % (code, said))
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        out("auto-update  FAILED to register the daily job %s: %s" % (label, error))
        return False
    out("auto-update  registered the daily job %s, at 05:00 or at the next sign-in after a missed day." % label)
    out("             It installs only when the clone moved past the last install, with that install's --no-hook-merge")
    out("             and the permission mode settings.json has then, and changes nothing outside the kit home (no PATH,")
    out("             no Herdr).")
    out("             Log: %s. The next session start prints one line when it updated the kit or failed."
        % os.path.join(home, "logs", "auto-update-<date>.log"))
    out("             Job file: %s. Remove it with --no-auto-update." % path)
    for where in sorted(set(filter(None, (guarded(os.path.abspath(root)), guarded(home))))):
        out("  note  %s is a folder macOS guards: a background job may be refused access to it. A clone and a kit "
            "home outside Desktop, Documents, Downloads and iCloud Drive avoid that." % where)
    return True


def unregister(kit_home, dry_run=False, out=print):
    """Removes this kit home's daily job, and nothing else: the logs and the install record stay."""
    if sys.platform != "darwin":
        out("auto-update  no daily job to remove: the daily update is macOS and Windows only for now")
        return
    label = job_label(kit_home)
    path = plist_path(kit_home)
    home = kit_home_path(kit_home)
    try:
        loaded = is_loaded(label)
        if not (loaded or os.path.exists(path)):
            out("auto-update  no daily job %s for %s to remove" % (label, home))
            return
        if dry_run:
            out("auto-update  would remove the daily job %s" % label)
            return
        if loaded:
            code, said = launchctl(["bootout", "%s/%s" % (domain(), label)])
            if code != 0 and is_loaded(label):
                out("auto-update  FAILED to remove the daily job %s: launchctl bootout exited %d: %s"
                    % (label, code, said))
                return
        if os.path.exists(path):
            os.remove(path)
    except (OSError, subprocess.SubprocessError) as error:
        out("auto-update  FAILED to remove the daily job %s: %s" % (label, error))
        return
    out("auto-update  removed the daily job %s; nothing else was changed, and the logs stay in %s"
        % (label, os.path.join(home, "logs")))


def save_install_record(root, kit_home, permissions, no_hook_merge):
    """What the daily run replays (install/auto-update.py): the options the last install that finished ran with (the
    permission mode as rendered, --no-hook-merge) and the clone commit it installed. Every install that reaches its
    end writes it, by hand or by the job, so the daily run follows the user's last choice, never the enabling run's,
    and does not reinstall what a hand update already installed. The keys are schedule.ps1's."""
    commit = None
    if os.path.exists(os.path.join(root, ".git")):
        try:
            process = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
            lines = process.stdout.decode("utf-8", "replace").split()
            if process.returncode == 0 and lines and re.match(r"^[0-9a-f]{40}$", lines[-1]):
                commit = lines[-1]
        except (OSError, subprocess.SubprocessError):
            pass
    # No time stamp: a run that changes nothing leaves the record, like every other file, byte for byte as it was.
    text = json.dumps(collections.OrderedDict([
        ("permissions", permissions),
        ("noHookMerge", bool(no_hook_merge)),
        ("commit", commit),
        ("clone", os.path.abspath(root)),
    ]), indent=4, ensure_ascii=False) + "\n"
    home = kit_home_path(kit_home)
    path = os.path.join(home, ".kit-install.json")
    try:
        with open(path, encoding="utf-8-sig") as handle:
            if handle.read().strip() == text.strip():
                return
    except OSError:
        pass
    os.makedirs(home, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
