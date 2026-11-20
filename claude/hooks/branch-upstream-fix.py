#!/usr/bin/env python3
"""branch-upstream-fix.py: SessionStart + PostToolUse(git *).

Repoints the current feature branch upstream to origin/<branch> so a plain `git push` targets the
feature branch, not the protected integration branch. When the integration branch is PR-only, a
direct push to it is rejected (for example Azure DevOps TF402455); this keeps that from happening.
Idempotent; only touches local git config. Never commits/pushes/checks out. Exits 0.

The macOS and Linux twin of branch-upstream-fix.ps1, with the same behaviour and output.
"""
import subprocess
import sys

BASE_BRANCHES = ("devel", "main", "master", "HEAD")  # never touch base branches


def git(*args):
    try:
        done = subprocess.run(("git",) + args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def main():
    repo = git("rev-parse", "--show-toplevel")
    if not repo:
        return
    branch = git("-C", repo, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch or branch in BASE_BRANCHES:
        return
    want = "refs/heads/%s" % branch
    have = git("-C", repo, "config", "--get", "branch.%s.merge" % branch) or ""
    if have != want:
        git("-C", repo, "config", "branch.%s.remote" % branch, "origin")
        git("-C", repo, "config", "branch.%s.merge" % branch, want)
        git("-C", repo, "config", "push.default", "current")
        sys.stdout.write("[branch-upstream-fix] '%s' push target set to origin/%s (was merge='%s')\n"
                         % (branch, branch, have))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
