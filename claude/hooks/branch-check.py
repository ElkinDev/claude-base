#!/usr/bin/env python3
"""branch-check.py: SessionStart advisory. READ-ONLY: prints a note, never mutates git. Exits 0.

The macOS and Linux twin of branch-check.ps1, with the same output: a project scaffolded on those
systems wires this one (project-template/.claude/settings.local.posix.json).
"""
import os
import re
import subprocess
import sys


def git(*args):
    try:
        done = subprocess.run(("git",) + args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def main():
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if not branch:
        return
    repo = git("rev-parse", "--show-toplevel")
    active = None
    pointer = os.path.join(repo, ".claude", ".active-story") if repo else ""
    if pointer and os.path.isfile(pointer):
        with open(pointer, encoding="utf-8-sig", errors="replace") as handle:
            active = handle.read().strip() or None
    found = re.search(r"(\d{3,6})", branch)
    current = found.group(1) if found else None
    msg = None
    if current:
        msg = "On story branch '%s' (work item %s)." % (branch, current)
        if active and active != current:
            msg += " NOTE: last active work item was %s." % active
    elif active:
        existing = ""
        for line in git("branch", "--list", "*%s*" % active).splitlines():
            existing = line.lstrip("*+ ").strip()
            if existing:
                break
        state = ("branch '%s' exists" % existing) if existing else "no branch created yet"
        msg = ("You are on '%s', NOT a story branch. Active work item is %s (%s). "
               "ASK before switching/creating - never change git silently." % (branch, active, state))
    if msg:
        sys.stdout.write("[branch-check] %s\n" % msg)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
