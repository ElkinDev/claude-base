"""SessionStart hook (startup and resume): one line when the kit's daily update moved the kit, or failed, since the
last notice. Nothing otherwise.

The daily run (install/auto-update.ps1, registered by install.ps1 -AutoUpdate) appends one JSON line to
<kit home>/logs/auto-update-pending.jsonl for each run that updated the kit or failed. This hook reads that file,
prints one line naming the new kit commit, how many files changed and the hooks added, or the failure, and the log
path, then moves the file aside to auto-update-pending.seen.jsonl so the next session prints nothing.

The line goes out as JSON: systemMessage shows it to the user, additionalContext hands it to the session. The kit
home is KIT_HOME when set, else the folder above this hook's own. It never blocks and never fails the session: it
reads no stdin, and any error prints nothing and exits 0.
"""
import json
import os
import sys


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
    pending = os.path.join(logs, "auto-update-pending.jsonl")
    if not os.path.isfile(pending):
        return
    runs = entries(pending)
    # Moved aside before the line is printed: a notice that cannot be moved is shown again next time, never lost.
    try:
        os.replace(pending, os.path.join(logs, "auto-update-pending.seen.jsonl"))
    except OSError:
        pass
    if not runs:
        return
    line = notice(runs)
    sys.stdout.write(json.dumps({"systemMessage": line, "hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": line}}) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
