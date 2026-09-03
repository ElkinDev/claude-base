#!/usr/bin/env python3
"""Preflight check: the tools this kit needs, and the Herdr surface it drives.

Run it before installing, after a machine change, and whenever Herdr updates:

    python scripts/doctor.py
    python scripts/doctor.py --json

Each check prints one line, `ok | warn | FAIL  <name>  <detail>`. The exit code is 1 only
when a required check FAILs; warnings never fail, and neither does anything about Herdr,
which is optional. A `FAIL herdr <subcommand>` still matters: it means the CLI surface the
kit drives has moved under it, and the code that calls that subcommand needs re-verifying
before `herdr/cli-surface.txt` and `herdr/verified-version.txt` are updated.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent
SURFACE_FILE = KIT_ROOT / "herdr" / "cli-surface.txt"
VERIFIED_FILE = KIT_ROOT / "herdr" / "verified-version.txt"

WINDOWS = os.name == "nt"
MIN_PYTHON = (3, 8)
INTEGRATION_COMMAND = "herdr integration install claude"
HERDR_ABSENT = ("not installed (optional): pane tracking, notifications and the agent-driving "
                "commands stay off; see herdr/README.md")
# The same candidates project-template/scripts/hooks/run-logged.py uses. A bare `bash` on
# PATH is deliberately not one of them: on Windows it may be WSL, which is not Git Bash.
GIT_BASH_CANDIDATES = (
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files\Git\bin\bash.exe",
)


def run(cmd, timeout=30):
    """Run argv (never a shell) and return (exit code, output). (-1, reason) if the call fails.

    Call it with the path `shutil.which` returned, not the bare name: on Windows a tool
    installed through npm is a `.CMD` shim, and CreateProcess does not find it by name.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except Exception as error:
        return -1, str(error)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def is_file(path):
    """Its own function so a test can answer for a machine it is not running on."""
    try:
        return Path(path).is_file()
    except OSError:
        return False


def check(status, name, detail, required=False):
    return {"status": status, "name": name, "detail": detail, "required": required}


def first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def read_verified():
    try:
        return first_line(VERIFIED_FILE.read_text(encoding="utf-8"))
    except OSError:
        return ""


def read_surface():
    try:
        text = SURFACE_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


# ---------------------------------------------------------------- the checks

def check_git():
    path = shutil.which("git")
    if not path:
        return check("FAIL", "git", "not on PATH: install git and reopen the terminal", True)
    code, out = run([path, "--version"])
    version = first_line(out) if code == 0 else "version unreadable"
    return check("ok", "git", "%s (%s)" % (version, path), True)


def check_python():
    version = "%d.%d.%d" % sys.version_info[:3]
    wanted = "%d.%d" % MIN_PYTHON
    if sys.version_info[:2] < MIN_PYTHON:
        return check("FAIL", "python", "%s: the scripts need %s or newer" % (version, wanted), True)
    return check("ok", "python", "%s (%s)" % (version, sys.executable), True)


def check_claude():
    path = shutil.which("claude")
    if not path:
        return check("FAIL", "claude", "not on PATH: install Claude Code first", True)
    code, out = run([path, "--version"])
    if code != 0 or not first_line(out):
        return check("warn", "claude", "on PATH at %s, but `claude --version` did not answer" % path,
                     True)
    return check("ok", "claude", "%s (%s)" % (first_line(out), path), True)


def check_git_bash():
    """Windows only: the hooks run through Git Bash, so its absolute path must be resolvable."""
    candidates = []
    git = shutil.which("git") or "git"
    code, out = run([git, "--exec-path"])
    if code == 0 and first_line(out):
        base = Path(first_line(out))
        for parent in [base] + list(base.parents):
            candidates.append(parent / "bin" / "bash.exe")
            candidates.append(parent / "usr" / "bin" / "bash.exe")
    candidates.extend(Path(item) for item in GIT_BASH_CANDIDATES)
    for candidate in candidates:
        if is_file(candidate):
            return check("ok", "git bash", str(candidate), True)
    return check("FAIL", "git bash",
                 "not found: install Git for Windows; the hooks need its bash.exe and a bare "
                 "`bash` on PATH is not used because it may be WSL", True)


def check_herdr():
    """Herdr is optional, so none of these lines is required; a FAIL still names a moved surface."""
    out = []
    path = shutil.which("herdr")
    if not path:
        return [check("warn", "herdr", HERDR_ABSENT)]

    code, text = run([path, "--version"])
    installed = first_line(text) if code == 0 else ""
    verified = read_verified()
    if not installed:
        out.append(check("warn", "herdr",
                         "on PATH at %s, but `herdr --version` did not answer" % path))
    elif not verified:
        out.append(check("warn", "herdr",
                         "%s installed; no verified version recorded in herdr/verified-version.txt"
                         % installed))
    elif installed == verified:
        out.append(check("ok", "herdr", "%s (%s)" % (installed, path)))
    else:
        out.append(check("warn", "herdr",
                         "version differs from the verified one (%s vs %s): run the surface probe "
                         "below and, when everything passes, update herdr/verified-version.txt"
                         % (installed, verified)))

    surface = read_surface()
    if surface is None:
        out.append(check("warn", "herdr surface",
                         "herdr/cli-surface.txt is missing, so the surface was not probed"))
        return out
    for subcommand in surface:
        code, _ = run([path] + subcommand.split() + ["--help"])
        if code == 0:
            out.append(check("ok", "herdr %s" % subcommand, "present"))
        else:
            out.append(check("FAIL", "herdr %s" % subcommand, "missing or renamed"))
    return out


def collect():
    items = [check_git(), check_python(), check_claude()]
    if WINDOWS:
        items.append(check_git_bash())
    items.extend(check_herdr())
    return items


# ---------------------------------------------------------------- output

def render(items):
    width = max(len(item["name"]) for item in items) if items else 0
    for item in items:
        print("%-4s  %-*s  %s" % (item["status"], width, item["name"], item["detail"]))


def summary(items):
    counts = {"ok": 0, "warn": 0, "FAIL": 0}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    broken = [item["name"] for item in items if item["status"] == "FAIL" and item["required"]]
    verdict = ("required checks failed: %s" % ", ".join(broken)) if broken \
        else "no required check failed"
    return "summary  %d ok, %d warn, %d FAIL; %s" % (counts["ok"], counts["warn"], counts["FAIL"],
                                                     verdict)


def hints(items):
    out = []
    if any(item["name"].startswith("herdr ") for item in items):
        out.append("Wire Herdr's Claude Code integration with: %s" % INTEGRATION_COMMAND)
    if any(item["status"] == "FAIL" and item["name"].startswith("herdr ") for item in items):
        out.append("A moved Herdr subcommand: re-verify the code that drives it, then update "
                   "herdr/cli-surface.txt and herdr/verified-version.txt in the same commit "
                   "(see the herdr-driving skill, /orchestration:herdr-driving from the "
                   "marketplace).")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check the tools this kit needs and the Herdr surface it drives.")
    parser.add_argument("--json", action="store_true", help="print the checks as a JSON list")
    args = parser.parse_args(argv)

    items = collect()
    if args.json:
        print(json.dumps(items, indent=2))
    else:
        render(items)
        print(summary(items))
        for hint in hints(items):
            print(hint)
    return 1 if any(i["status"] == "FAIL" and i["required"] for i in items) else 0


if __name__ == "__main__":
    sys.exit(main())
