"""SessionStart hook (startup and resume): one line when the kit's daily update moved the kit, or failed, since the
last notice. Nothing otherwise.

The daily run (install/auto-update.ps1, registered by install.ps1 -AutoUpdate) appends one JSON line to
<kit home>/logs/auto-update-pending.jsonl for each run that updated the kit or failed. This hook prints one line
naming the new kit commit, how many files changed and the hooks added, or the failure, and the log path. The file is
first claimed by renaming it to a name of this process's own: only the session whose rename wins prints, so two
sessions that start together print the line once, and the next session prints nothing. The claimed file ends as
auto-update-pending.seen.jsonl.

A run the task stopped at its time limit (waiting on something it could not answer, or hung) wrote no JSON line: its
log has a start line and no end line (done, failed or skipped). Such a run, once its start is older than the limit,
is reported here as a failure too. The session that creates <log>.claim first writes the end line into that log,
so the run is reported once.

The line goes out as JSON: systemMessage shows it to the user, additionalContext hands it to the session. The kit
home is KIT_HOME when set, else the folder above this hook's own. It never blocks and never fails the session: it
reads no stdin, and any error prints nothing and exits 0.
"""
import datetime
import json
import os
import re
import sys

LIMIT_MINUTES = 30  # the task's ExecutionTimeLimit (install/schedule.ps1)
LOG_NAME = re.compile(r"^auto-update-(\d{4}-\d{2}-\d{2})\.log$")
START = re.compile(r"^(\d\d:\d\d:\d\d) start:")
END = re.compile(r"^\d\d:\d\d:\d\d (done|failed|skipped):")
# the end line this hook writes for a stopped run names its start, since a later run may have begun after it
REPORTED = re.compile(r" failed: the run that started at \S+ (\d\d:\d\d:\d\d) never finished")


def kit_home():
    return os.environ.get("KIT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def entries(path):
    """The runs the file records, oldest first; a line that is not one is skipped."""
    found = []
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        for raw in handle.read().splitlines():
            raw = raw.strip().lstrip(chr(0xFEFF))
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("result") in ("updated", "failed"):
                found.append(entry)
    return found


def open_starts(path, day, now):
    """The start times of the runs in one day's log that never wrote an end line and are past the limit."""
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        lines = handle.read().splitlines()
    unended, current, reported = [], None, set()
    for line in lines:
        line = line.lstrip(chr(0xFEFF))
        named = REPORTED.search(line)
        if named:
            reported.add(named.group(1))
        started = START.match(line)
        if started:
            if current:
                unended.append(current)  # a later run began, so this one was stopped
            current = started.group(1)
        elif END.match(line):
            current = None
    if current:
        begun = datetime.datetime.strptime(day + " " + current, "%Y-%m-%d %H:%M:%S")
        if now - begun > datetime.timedelta(minutes=LIMIT_MINUTES):
            unended.append(current)
    return [start for start in unended if start not in reported]


def stopped_runs(logs, now):
    """The stopped runs no session has reported yet, as failure entries; each is claimed and closed in its log."""
    found = []
    for name in sorted(os.listdir(logs)):
        match = LOG_NAME.match(name)
        if not match:
            continue
        path = os.path.join(logs, name)
        if not open_starts(path, match.group(1), now):
            continue
        claim = path + ".claim"
        try:
            os.close(os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except OSError:
            continue  # another session is reporting this log
        try:
            # read again under the claim: a session that just closed it leaves nothing to report
            for start in open_starts(path, match.group(1), now):
                line = ("the run that started at %s %s never finished: the task stops a run after %d minutes, so it "
                        "was waiting on something it could not answer, or hung" % (match.group(1), start, LIMIT_MINUTES))
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write("%s failed: %s (reported at a session start)\n" % (now.strftime("%H:%M:%S"), line))
                found.append({"result": "failed", "line": line, "time": "%s %s" % (match.group(1), start[:5]),
                              "log": path})
        finally:
            os.remove(claim)
    return found


def notice(runs):
    updates = [run for run in runs if run["result"] == "updated"]
    last = runs[-1]
    parts = []
    if updates:
        text = "now at %s (was %s), %d files changed" % (
            updates[-1].get("to", "?"), updates[0].get("from", "?"), sum(int(run.get("files") or 0) for run in updates))
        if len(updates) > 1:
            text += " over %d runs" % len(updates)
        hooks = []
        for run in updates:
            for hook in run.get("hooks") or []:
                if hook not in hooks:
                    hooks.append(hook)
        if hooks:
            text += ", new hooks: " + ", ".join(hooks)
        parts.append(text)
    if last["result"] == "failed":
        parts.append(("the last run failed: " if updates else "failed: ") + str(last.get("line", "")).rstrip("."))
    return "Kit auto-update %s: %s. Log: %s" % (last.get("time", ""), "; ".join(parts), last.get("log", ""))


def main():
    logs = os.path.join(kit_home(), "logs")
    if not os.path.isdir(logs):
        return
    runs = []
    pending = os.path.join(logs, "auto-update-pending.jsonl")
    if os.path.isfile(pending):
        claimed = "%s.%d" % (pending, os.getpid())
        try:
            os.rename(pending, claimed)
        except OSError:
            claimed = None  # another session claimed it first: that session prints it
        if claimed:
            runs = entries(claimed)
            try:
                os.replace(claimed, os.path.join(logs, "auto-update-pending.seen.jsonl"))
            except OSError:
                pass
    runs += stopped_runs(logs, datetime.datetime.now())
    if not runs:
        return
    runs.sort(key=lambda run: str(run.get("time", "")))
    line = notice(runs)
    sys.stdout.write(json.dumps({"systemMessage": line, "hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": line}}) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
