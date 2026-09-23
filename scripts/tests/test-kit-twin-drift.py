"""Pins for scripts/kit-twin-drift.py: the trailing rule with its content check, the 24 h owed rule, the pairing
(single basename, seat and agent folders, several kit files of one basename by the longest path tail), the recursive
skills folder, the no-twin list with its fixed floor, backups, briefs and waivers, the --row line, the defaults (the
kit above the script, the first commit as the floor, the kit home folders that exist) and the errors.

    python scripts/tests/test-kit-twin-drift.py
    KIT_TWIN_DRIFT_PY=<path> python scripts/tests/test-kit-twin-drift.py      # against a staged copy

Every case runs the script as a subprocess on a temp kit (a git repository made here, committed three days ago) and
temp live folders; nothing real is read.
"""
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("KIT_TWIN_DRIFT_PY") or os.path.join(HERE, "..", "kit-twin-drift.py")
SCRUBBED = ("KIT_TWIN_KIT", "KIT_TWIN_LIVE", "KIT_TWIN_WAIVERS", "KIT_TWIN_SINCE", "KIT_HOME")
NOW = time.time()
DAY = 24 * 3600
LIVE_NAMES = ("hooks", "hooks/tests", "seats", "other", "scripts", "briefs", "skills/**")
SINCE = dt.datetime.fromtimestamp(NOW - 3 * DAY - 120).strftime("%Y-%m-%d %H:%M")
KIT_FILES = ("claude/hooks/h.py", "claude/hooks/same.py", "claude/seats/analyst.md", "claude/agents/analyst.md",
             "scripts/s.py", "docs/notes.md", "claude/hooks/tests/run-tests.py", "claude/tools/tests/run-tests.py",
             "claude/skills/alpha/SKILL.md", "claude/skills/beta/SKILL.md", "claude/seats/orchestrator.md",
             "claude/skills/alpha/refs/deep.md")


def put(path, body="x\n", age=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(body)
    if age is not None:
        os.utime(path, (NOW - age, NOW - age))


def make_live(root):
    for d in LIVE_NAMES:
        os.makedirs(os.path.join(root, "live", d.rstrip("/*")), exist_ok=True)


def commit(kit, age):
    when = "@%d +0000" % int(NOW - age)
    env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    for cmd in (["add", "-A"], ["-c", "user.name=fixture", "-c", "user.email=fixture@example.com",
                                "-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", "fixture"]):
        subprocess.run(["git", "-C", kit] + cmd, check=True, capture_output=True, env=env, timeout=60)


def make_kit(root):
    make_live(root)
    kit = os.path.join(root, "kit")
    for rel in KIT_FILES:
        put(os.path.join(kit, rel), "kit line\n")
    subprocess.run(["git", "-C", kit, "init", "-q"], check=True, capture_output=True, timeout=60)
    commit(kit, 3 * DAY)
    return kit


def clean_env():
    return {k: v for k, v in os.environ.items() if k not in SCRUBBED}


def run(root, kit, args, waivers=None):
    live = os.pathsep.join(os.path.join(root, "live", d) for d in LIVE_NAMES)
    env = dict(clean_env(), KIT_TWIN_KIT=kit, KIT_TWIN_LIVE=live, KIT_TWIN_SINCE=SINCE,
               KIT_TWIN_WAIVERS=waivers or os.path.join(root, "no-waivers.txt"))
    r = subprocess.run([sys.executable, SCRIPT] + args, capture_output=True, text=True, timeout=120, env=env)
    return r.returncode, r.stdout, r.stderr


def row_numbers(out):
    m = re.search(r"twins trailing (\d+) \(owed, live change older than 24 h: (\d+)\), live tools with no twin since "
                  r"[\d-]+ [\d:]+: (\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None


def main():
    results = []

    def check(name, fn):
        root = tempfile.mkdtemp(prefix="ktd-pin-")
        try:
            ok, note = bool(fn(root)), ""
        except Exception as e:
            ok, note = False, " (%s: %s)" % (type(e).__name__, str(e)[:100])
        finally:
            shutil.rmtree(root, ignore_errors=True)
        results.append(ok)
        print(("OK   " if ok else "FAIL ") + name + note)

    def world(root):
        kit = make_kit(root)
        L = os.path.join(root, "live")
        put(os.path.join(L, "hooks", "h.py"), "live line\n", age=2 * DAY)              # trails, owed
        put(os.path.join(L, "hooks", "same.py"), "kit line\n", age=2 * DAY)            # newer but byte-equal: carried
        put(os.path.join(L, "hooks", "tests", "run-tests.py"), "live\n", age=2 * DAY + 100)  # pairs with hooks/tests only
        put(os.path.join(L, "seats", "analyst.md"), "live seat\n", age=3600)           # trails, same day
        put(os.path.join(L, "other", "analyst.md"), "not a seat\n", age=3600)          # tie of 1: no pair
        put(os.path.join(L, "skills", "alpha", "SKILL.md"), "live skill\n", age=1800)  # nested twin, same day
        put(os.path.join(L, "skills", "gamma", "SKILL.md"), "third party\n", age=DAY)  # no twin, not a tool here
        put(os.path.join(L, "skills", "gamma", "helper.py"), age=DAY)                # a skills file is never an orphan
        put(os.path.join(L, "skills", "alpha", "refs", "deep.md"), "deep\n", age=1700)  # depth 2, same day
        put(os.path.join(L, "other", "orchestrator.md"), "not a seat\n", age=3600)  # one seat twin, wrong folder
        put(os.path.join(L, "scripts", "s.py"), "old\n", age=4 * DAY)                  # older than the kit commit
        put(os.path.join(L, "scripts", "new.py"), age=DAY)                             # no twin
        put(os.path.join(L, "scripts", "notes.2026.md"), age=DAY)                      # a dotted year is no backup
        put(os.path.join(L, "scripts", "new.py.20260922-120000.bak"), age=DAY)         # backups never count
        put(os.path.join(L, "scripts", "new.py.new"), age=DAY)
        put(os.path.join(L, "scripts", "new.20260922-120000.py"), age=DAY)
        put(os.path.join(L, "scripts", "priv.py"), age=DAY)                            # no twin, waived when asked
        put(os.path.join(L, "scripts", "data.json"), age=DAY)                          # not a tool
        put(os.path.join(L, "scripts", "ancient.py"), age=5 * DAY)                     # before the floor
        put(os.path.join(L, "briefs", "lane-x-2026-09-22.md"), age=DAY)                 # a brief is no tool
        put(os.path.join(L, "briefs", "TEMPLATE-lane-brief.md"), age=DAY)               # the template is
        return kit

    def row(root):
        kit = world(root)
        rc, out, _ = run(root, kit, ["--row"])
        return rc == 0 and out.startswith("kit-twin-drift ") and row_numbers(out) == (5, 2, 4)
    check("five twins trail (two owed after 24 h), four live tools have no twin", row)

    def table(root):
        kit = world(root)
        rc, out, _ = run(root, kit, [])
        lines = [ln for ln in out.splitlines() if ln.startswith("| claude/")]
        orphans = out.split("with no twin (", 1)[1]
        return (rc == 0 and len(lines) == 5 and "claude/skills/alpha/refs/deep.md" in lines[0]
                and "claude/skills/alpha/SKILL.md" in lines[1]
                and "claude/seats/analyst.md" in lines[2] and "| no |" in lines[2] and "orchestrator" not in out
                and "helper.py" not in out
                and any("claude/hooks/tests/run-tests.py" in ln and "| yes |" in ln for ln in lines)
                and "claude/tools/tests/run-tests.py" not in out and "same.py" not in out and "gamma" not in out
                and "/other/" not in out and "notes.2026.md" in orphans and "TEMPLATE-lane-brief.md" in orphans
                and "lane-x" not in out and ".bak" not in out and "20260922-120000" not in out and "data.json" not in out
                and "ancient" not in out)
    check("the table: newest first; byte-equal, tie-paired and third-party files never listed; one twin per duplicated "
          "basename", table)

    def waived(root):
        kit = world(root)
        w = os.path.join(root, "waivers.txt")
        put(w, "# private by design\npriv.py  stays private\n\n")
        rc, out, _ = run(root, kit, ["--row"], waivers=w)
        return rc == 0 and row_numbers(out) == (5, 2, 3)
    check("a waived basename leaves the no-twin count; comments and blank lines are skipped", waived)

    def kit_commit_keeps_orphans(root):
        kit = world(root)
        put(os.path.join(kit, "docs", "later.md"), "a later commit\n")
        commit(kit, 60)
        rc, out, _ = run(root, kit, ["--row"])
        return rc == 0 and row_numbers(out)[2] == 4
    check("a later kit commit does not erase the no-twin column (fixed floor)", kit_commit_keeps_orphans)

    def in_sync(root):
        kit = make_kit(root)
        put(os.path.join(root, "live", "hooks", "h.py"), age=4 * DAY)
        rc, out, _ = run(root, kit, ["--row"])
        return rc == 0 and row_numbers(out) == (0, 0, 0)
    check("a live file older than its twin's commit trails nothing", in_sync)

    def not_a_kit(root):
        make_live(root)
        os.makedirs(os.path.join(root, "plain"))
        rc, _, err = run(root, os.path.join(root, "plain"), ["--row"])
        rc2, _, _ = run(root, os.path.join(root, "absent"), ["--row"])
        return rc == 3 and "not a git checkout" in err and rc2 == 3
    check("a kit folder that is not a git checkout, or is absent, is exit 3", not_a_kit)

    def usage(root):
        kit = world(root)
        bogus = run(root, kit, ["--bogus"])[0] == 2
        env_since = subprocess.run([sys.executable, SCRIPT, "--row"], capture_output=True, text=True, timeout=120,
                                   env=dict(clean_env(), KIT_TWIN_KIT=kit, KIT_TWIN_SINCE="yesterday",
                                            KIT_TWIN_LIVE=os.path.join(root, "live", "hooks"))).returncode == 2
        shutil.rmtree(os.path.join(root, "live", "other"))
        rc, out, err = run(root, kit, ["--row"])
        return bogus and env_since and rc == 2 and "1 of 7 live folders do not exist" in err and out == ""
    check("an unknown flag, a bad KIT_TWIN_SINCE and a live folder that does not exist are exit 2, never a 0 reading",
          usage)

    def template_pairs(root):
        kit = world(root)
        put(os.path.join(kit, "project-template", "briefs", "TEMPLATE-lane-brief.md"), "kit template\n")
        commit(kit, 3 * DAY)
        put(os.path.join(root, "live", "briefs", "TEMPLATE-lane-brief.md"), "live template\n", age=2 * DAY)
        rc, out, _ = run(root, kit, ["--row"])
        return rc == 0 and row_numbers(out) == (6, 3, 3)
    check("a template tracked under project-template/ pairs with the live one and leaves the no-twin list", template_pairs)

    def template_copy_never_ties(root):
        kit = make_kit(root)
        put(os.path.join(kit, "project-template", "scripts", "hooks", "tests", "run-tests.py"), "template runner\n")
        put(os.path.join(kit, "project-template", "CLAUDE.md"), "template rules\n")
        commit(kit, 3 * DAY)
        put(os.path.join(root, "live", "hooks", "tests", "run-tests.py"), "live runner changed\n", age=2 * DAY)
        rc, out, _ = run(root, kit, ["--row"])
        return rc == 0 and row_numbers(out) == (1, 1, 0)
    check("a project-template copy of a kit-home file never ties with it: the live runner still trails", template_copy_never_ties)

    def template_original_pairs(root):
        kit = make_kit(root)
        put(os.path.join(kit, "project-template", "scripts", "hooks", "run-logged.py"), "template original\n")
        commit(kit, 3 * DAY)
        put(os.path.join(root, "live", "hooks", "run-logged.py"), "live copy drifted\n", age=2 * DAY)
        rc, out, _ = run(root, kit, ["--row"])
        return rc == 0 and row_numbers(out) == (1, 1, 0)
    check("a file only the template holds (run-logged.py) still pairs and trails", template_original_pairs)

    def two_template_files_tie(root):
        kit = make_kit(root)
        for side in ("a", "b"):
            put(os.path.join(kit, "project-template", side, "hooks", "tests", "runner-x.py"), side + " runner\n")
        commit(kit, 3 * DAY)
        put(os.path.join(root, "live", "hooks", "tests", "runner-x.py"), "live runner\n", age=2 * DAY)
        rc, out, err = run(root, kit, ["--row"])
        return rc == 0 and "Traceback" not in err and row_numbers(out) == (0, 0, 0)
    check("a tie between two template files pairs none and never crashes", two_template_files_tie)

    def template_name_alone_never_pairs(root):
        kit = make_kit(root)
        put(os.path.join(kit, "project-template", "docs", "README.md"), "template readme\n")
        commit(kit, 3 * DAY)
        put(os.path.join(root, "live", "scripts", "README.md"), "an unrelated readme\n", age=2 * DAY)
        rc, out, _ = run(root, kit, ["--row"])
        return rc == 0 and row_numbers(out)[0] == 0
    check("a file the template holds pairs by folder and name, never by name alone", template_name_alone_never_pairs)

    def default_kit_and_floor(root):
        kit = world(root)
        copy = os.path.join(kit, "scripts", "kit-twin-drift.py")
        shutil.copy(SCRIPT, copy)
        commit(kit, 3 * DAY)
        live = os.pathsep.join(os.path.join(root, "live", d) for d in LIVE_NAMES)
        r = subprocess.run([sys.executable, copy, "--row"], capture_output=True, text=True, timeout=120,
                           env=dict(clean_env(), KIT_TWIN_LIVE=live, KIT_TWIN_WAIVERS=os.path.join(root, "none.txt")))
        floor = dt.datetime.fromtimestamp(int(NOW - 3 * DAY)).strftime("%Y-%m-%d %H:%M")
        return r.returncode == 0 and row_numbers(r.stdout) == (5, 2, 4) and ("no twin since %s:" % floor) in r.stdout
    check("with no KIT_TWIN_KIT the kit is the folder above the script, and with no KIT_TWIN_SINCE the floor is its "
          "first commit", default_kit_and_floor)

    def default_live(root):
        kit = world(root)
        home = os.path.join(root, "home")
        put(os.path.join(home, "hooks", "h.py"), "live line\n", age=2 * DAY)
        put(os.path.join(home, "skills", "alpha", "SKILL.md"), "live skill\n", age=1800)
        env = dict(clean_env(), KIT_TWIN_KIT=kit, KIT_HOME=home, KIT_TWIN_SINCE=SINCE,
                   KIT_TWIN_WAIVERS=os.path.join(root, "none.txt"))
        r = subprocess.run([sys.executable, SCRIPT, "--row"], capture_output=True, text=True, timeout=120, env=env)
        empty = dict(env, KIT_HOME=os.path.join(root, "empty-home"))
        r2 = subprocess.run([sys.executable, SCRIPT, "--row"], capture_output=True, text=True, timeout=120, env=empty)
        return (r.returncode == 0 and row_numbers(r.stdout) == (2, 1, 0) and r2.returncode == 2
                and "no default live folder" in r2.stderr)
    check("with no KIT_TWIN_LIVE the kit home folders that exist are read, and none existing is exit 2", default_live)

    print("%d of %d OK" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
