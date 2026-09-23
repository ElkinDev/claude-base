"""kit-twin-drift.py: which files of this kit trail the live copies a machine runs, with no agent.

    python scripts/kit-twin-drift.py            the table of trailing twins and the live tools with no twin
    python scripts/kit-twin-drift.py --row      one line for a ledger

A kit is only useful while it carries what the machine actually runs. A hook, script, law or agent definition that
is changed where it runs and never brought back here is lost to every other project. This script reads the drift
twice a day from a scheduled task (automation) and names each file that has to travel (quality). Not served: tokens.

Pairing. A live file pairs with a tracked kit file under claude/, scripts/, herdr/, install/ or project-template/ of
the same basename. When one kit file has that basename it pairs, except that a seat or agent twin pairs only with a
live folder of the same name. When several kit files share it (run-tests.py, SKILL.md, analyst.md), the live file
pairs only with the one whose path shares the longest tail with the live path, at least its folder and name; a tie
pairs none, except that a project-template/ file never ties with a kit file outside the template: the template
copies some kit-home files (project-template/scripts/hooks/tests/run-tests.py beside claude/hooks/tests/run-tests.py)
and holds originals of its own (project-template/scripts/hooks/run-logged.py), so a tie between the two sides goes
to the file outside it. A name the kit holds twice at a tail of one (CLAUDE.md, README.md) pairs with none and
counts in neither column, which is why the default live folders are the kit-home folders only.

Trailing and owed. A pair trails when the live file changed more than 60 s after the twin's last commit AND their
CR-stripped texts differ (a byte-equal twin is carried, whatever its mtime). Owed is a trailing pair whose live
change is older than 24 h. The lines differ column includes the kit's intended generalization: an upper bound, not
the owed change.

Carried by verdict. A trailing pair whose live file was read and judged to need no kit change, because every
difference left is intended (a machine setting such as a deny tier, a pinned model id, the kit's generalization of a
fix it already carries), is listed in kit-twin-carried.txt as `<hash> <kit twin> <reason>`, where the hash is the
first 12 hex of the sha256 of the live file's CR-stripped text and the kit twin is the tracked path the live file
pairs with. `--carry-line <live path> <reason>` prints the line to append; the live file must be in the live set and
have a twin. The key is the pair, never the basename: two live files with the same name and bytes that pair with two
kit files carry apart. Such a pair leaves the trailing and owed counts and is counted apart. The verdict holds for
those exact bytes only: the next change to the live file changes its hash and the pair trails again, so a carry
never hides a later change. A missing carried file carries nothing; one that exists and cannot be read carries
nothing and says so on stderr.

No twin. A .py, .sh, .ps1 or .md file of a flat live folder, changed after the floor, whose basename the kit does
not track and the waivers file does not list (one basename per line, then its reason). The floor is fixed, never the
kit tip, so a commit to the kit cannot erase the column. Backups never count: .bak, .new, .pyc, .log, or a
.YYYYMMDD-HHMMSS stamp in the name. In a folder named briefs only the lane template counts. A live folder ending in
/** is read recursively and feeds the pairing only (a skills folder holds third-party skills that have no twin by
design).

Environment:
  KIT_TWIN_KIT      the kit checkout; default the folder above the one holding this script
  KIT_TWIN_LIVE     the live folders, joined by the path separator; every one must exist. Default: hooks,
                    hooks/tests, tools, tools/tests, agents, seats and skills/** under the kit home (KIT_HOME, else
                    ~/.claude), the ones that exist; add a project's own scripts, tests and briefs folders here
  KIT_TWIN_WAIVERS  default kit-twin-waivers.txt beside this script
  KIT_TWIN_CARRIED  default kit-twin-carried.txt beside this script
  KIT_TWIN_SINCE    the floor of the no-twin column, YYYY-MM-DD HH:MM; default the time of the kit's first commit
Exit 0; 2 on a usage error, a KIT_TWIN_LIVE folder that does not exist or no default live folder at all (a wrong
folder would read as 0 trailing); 3 when the kit is not a git checkout with a commit.
"""
import argparse
import datetime as dt
import difflib
import glob
import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.environ.get("KIT_TWIN_KIT") or os.path.dirname(HERE)
KIT_HOME = (os.environ.get("KIT_HOME") or os.path.join(os.path.expanduser("~"), ".claude")).replace("\\", "/")
DEFAULT_LIVE = [KIT_HOME + "/" + d for d in ("hooks", "hooks/tests", "tools", "tools/tests", "agents", "seats",
                                             "skills/**")]
EXPLICIT_LIVE = [d for d in os.environ.get("KIT_TWIN_LIVE", "").split(os.pathsep) if d]
WAIVERS = os.environ.get("KIT_TWIN_WAIVERS") or os.path.join(HERE, "kit-twin-waivers.txt")
CARRIED = os.environ.get("KIT_TWIN_CARRIED") or os.path.join(HERE, "kit-twin-carried.txt")
SINCE = os.environ.get("KIT_TWIN_SINCE")
HASH_RE = re.compile(r"^[0-9a-f]{12}$")
TRACKED = ("claude/", "scripts/", "herdr/", "install/", "project-template/")
TOOL_EXT = (".py", ".sh", ".ps1", ".md")
PAIRED_FOLDERS = ("seats", "agents")
TEMPLATE = "TEMPLATE-lane-brief.md"
SLACK = 60
OWED_AFTER = 24 * 3600
STAMP_RE = re.compile(r"\.20\d{6}-\d{6}")


def git(*args):
    r = subprocess.run(["git", "-C", KIT] + list(args), capture_output=True, text=True, timeout=60)
    return r.stdout if r.returncode == 0 else None


def is_backup(name):
    return name.endswith((".bak", ".new", ".pyc", ".log")) or bool(STAMP_RE.search(name))


def folder_root(d):
    return d[:-3] if d.endswith("/**") else d


def live_files(live):
    """(path, flat) for every live file; flat is False for a file found under a /** folder."""
    out = []
    for d in live:
        rec = d.endswith("/**")
        pattern = os.path.join(folder_root(d), "**", "*") if rec else os.path.join(d, "*")
        for f in glob.glob(pattern, recursive=rec):
            if os.path.isfile(f) and not is_backup(os.path.basename(f)) and "__pycache__" not in f:
                out.append((f.replace("\\", "/"), not rec))
    return out


def tail_len(a, b):
    """How many trailing path components two paths share."""
    pa, pb = a.split("/"), b.split("/")
    n = 0
    while n < min(len(pa), len(pb)) and pa[-1 - n] == pb[-1 - n]:
        n += 1
    return n


def pair(live, cands):
    """The kit file a live file pairs with, or None (module docstring, Pairing)."""
    if len(cands) == 1:
        t = cands[0]
        folder = os.path.basename(os.path.dirname(t))
        if folder in PAIRED_FOLDERS and os.path.basename(os.path.dirname(live)) != folder:
            return None
        if t.startswith("project-template/") and tail_len(live, t) < 2:
            return None  # a template file pairs by folder and name, never by name alone (README.md, CLAUDE.md)
        return t
    scored = sorted(((tail_len(live, t), t) for t in cands), reverse=True)
    best = scored[0][0]
    top = [t for s, t in scored if s == best]
    if len(top) > 1:  # a template copy never ties with the kit file outside the template (module docstring)
        top = [t for t in top if not t.startswith("project-template/")] or top
    if best < 2 or len(top) > 1:
        return None
    return top[0]


def mtime(path):
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return None


def waivers():
    try:
        with open(WAIVERS, encoding="utf-8") as fh:
            return {ln.split()[0] for ln in fh if ln.strip() and not ln.lstrip().startswith("#")}
    except OSError:
        return set()


def text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().replace("\r\n", "\n").splitlines()
    except OSError:
        return []


def changed_lines(a, b):
    return sum(1 for d in difflib.unified_diff(text(a), text(b), lineterm="", n=0)
               if d[:1] in "+-" and not d.startswith(("+++", "---")))


def live_hash(path):
    """First 12 hex of the sha256 of the CR-stripped text, or None when the file cannot be read (no verdict holds)."""
    try:
        with open(path, "rb") as fh:
            body = fh.read().replace(b"\r\n", b"\n")
    except OSError:
        return None
    return hashlib.sha256(body).hexdigest()[:12]


def carried():
    """{(hash, kit twin)} of the carried file; a line that does not start with a 12-hex hash and a path is skipped,
    as are comments and blank lines. utf-8-sig, so an editor's byte order mark never hides the first verdict."""
    out = set()
    try:
        with open(CARRIED, encoding="utf-8-sig", errors="replace") as fh:
            for ln in fh:
                parts = ln.split()
                if len(parts) >= 2 and HASH_RE.match(parts[0].lower()):
                    out.add((parts[0].lower(), parts[1]))
    except FileNotFoundError:
        pass
    except OSError as e:
        print("kit-twin-drift: %s cannot be read, no verdict applied (%s)" % (CARRIED, e.__class__.__name__),
              file=sys.stderr)
    return out


def kit_index():
    """{basename: [tracked kit paths under the tool roots]}, or None when the kit is not a checkout."""
    listed = git("ls-files")
    if listed is None:
        return None
    by_base = {}
    for t in listed.splitlines():
        if t.startswith(TRACKED):
            by_base.setdefault(os.path.basename(t), []).append(t)
    return by_base


def first_commit():
    """The time of the kit's first commit (the earliest root when there are several), or None."""
    out = git("log", "--max-parents=0", "--format=%ct", "HEAD")
    stamps = [int(x) for x in (out or "").split() if x.isdigit()]
    return min(stamps) if stamps else None


def reading(now, since, live):
    last = git("log", "-1", "--format=%ct")
    if not last or not last.strip():
        return None
    kit_last = int(last.strip())
    by_base = kit_index() or {}
    rows, paired, carry, kept = [], set(), carried(), 0
    files = live_files(live)  # one walk; a file gone before its mtime is read is skipped, never a crash
    for f, flat in files:
        cands = by_base.get(os.path.basename(f))
        t = pair(f, cands) if cands else None
        if t is None:
            continue
        paired.add(f)
        ct = int((git("log", "-1", "--format=%ct", "--", t) or "0").strip() or 0)
        mt = mtime(f)
        if mt is None or mt <= ct + SLACK:
            continue
        n = changed_lines(os.path.join(KIT, t), f)
        if n == 0:
            continue
        if (live_hash(f), t) in carry:
            kept += 1  # judged for these exact bytes; a later change trails again
            continue
        rows.append({"twin": t, "live": f, "ct": ct, "mt": mt, "owed": now - mt > OWED_AFTER, "lines": n})
    waived = waivers()
    orphans = []
    for f, flat in files:
        base = os.path.basename(f)
        if (not flat or base in by_base or base in waived or not base.endswith(TOOL_EXT) or f in paired
                or (mtime(f) or 0) <= since):
            continue
        if "/briefs/" in f and base != TEMPLATE:
            continue
        orphans.append(f)
    rows.sort(key=lambda r: -r["mt"])
    return kit_last, rows, sorted(orphans), kept


def stamp(t):
    return dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def live_folders():
    """(the live folders, None) or (None, the reason they cannot be read)."""
    if EXPLICIT_LIVE:
        missing = [d for d in EXPLICIT_LIVE if not os.path.isdir(folder_root(d))]
        if missing:  # a wrong live folder would read as 0 trailing, a false "in sync"
            return None, "%d of %d live folders do not exist (first: %s)" % (len(missing), len(EXPLICIT_LIVE),
                                                                            missing[0])
        return EXPLICIT_LIVE, None
    live = [d for d in DEFAULT_LIVE if os.path.isdir(folder_root(d))]
    if not live:
        return None, "no default live folder exists under %s; set KIT_HOME or KIT_TWIN_LIVE" % KIT_HOME
    return live, None


def carry_line(path, reason, live):
    """Print the carried line for a live file as it is now: its hash, the kit twin it pairs with, the reason."""
    msys = re.match(r"^/([A-Za-z])/(.*)$", path)
    if msys and not os.path.isfile(path):  # a Git Bash spelling a Windows Python cannot open
        path = "%s:/%s" % (msys.group(1).upper(), msys.group(2))
    h = live_hash(path) if os.path.isfile(path) else None
    if h is None or not reason:
        print("kit-twin-drift: --carry-line takes a readable live file and a reason", file=sys.stderr)
        return 2
    key = os.path.normcase(os.path.realpath(path))
    found = [f for f, _ in live_files(live) if os.path.normcase(os.path.realpath(f)) == key]
    if not found:
        print("kit-twin-drift: %s is not in the live set (KIT_TWIN_LIVE), so no row reads it" % path, file=sys.stderr)
        return 2
    by_base = kit_index()
    if by_base is None:
        print("kit-twin-drift: %s is not a git checkout" % KIT, file=sys.stderr)
        return 3
    cands = by_base.get(os.path.basename(found[0]))
    twin = pair(found[0], cands) if cands else None
    if twin is None:
        print("kit-twin-drift: %s has no kit twin, so there is nothing to carry" % path, file=sys.stderr)
        return 2
    if len(twin.split()) != 1:
        print("kit-twin-drift: the kit twin %r has a space and the line splits on spaces; rename it" % twin,
              file=sys.stderr)
        return 2
    print("%s %s %s" % (h, twin, reason))
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--row", action="store_true", help="one line for a ledger")
    ap.add_argument("--carry-line", nargs=2, metavar=("LIVE_PATH", "REASON"),
                    help="print the kit-twin-carried.txt line for this live file as it is now, and exit")
    a = ap.parse_args(argv)
    live, why = live_folders()
    if live is None:
        print("kit-twin-drift: " + why, file=sys.stderr)
        return 2
    if a.carry_line:
        return carry_line(a.carry_line[0], " ".join(a.carry_line[1].split()), live)
    if SINCE:
        try:
            since = dt.datetime.strptime(SINCE, "%Y-%m-%d %H:%M").timestamp()
        except ValueError:
            print("kit-twin-drift: KIT_TWIN_SINCE is YYYY-MM-DD HH:MM, got %r" % SINCE, file=sys.stderr)
            return 2
    now = int(dt.datetime.now().timestamp())
    if not SINCE:
        since = first_commit()
        if since is None:
            print("kit-twin-drift: %s is not a git checkout with a commit" % KIT, file=sys.stderr)
            return 3
    r = reading(now, since, live)
    if r is None:
        print("kit-twin-drift: %s is not a git checkout with a commit" % KIT, file=sys.stderr)
        return 3
    kit_last, rows, orphans, kept = r
    owed = sum(1 for x in rows if x["owed"])
    floor = SINCE or stamp(since)
    if a.row:
        print("kit-twin-drift %s: kit last commit %s, twins trailing %d (owed, live change older than 24 h: %d), "
              "live tools with no twin since %s: %d, carried by verdict: %d"
              % (stamp(now), stamp(kit_last), len(rows), owed, floor, len(orphans), kept))
        return 0
    print("kit last commit %s; %d twins trail their live file, %d owed; %d carried by verdict"
          % (stamp(kit_last), len(rows), owed, kept))
    print("| twin | live | twin commit | live change | owed | lines differ |")
    print("|---|---|---|---|---|---|")
    for x in rows:
        print("| %s | %s | %s | %s | %s | %d |" % (x["twin"], x["live"], stamp(x["ct"]), stamp(x["mt"]),
                                                  "yes" if x["owed"] else "no", x["lines"]))
    print("\nLive tools changed after %s with no twin (%d):" % (floor, len(orphans)))
    for f in orphans:
        print("- %s (%s)" % (f, stamp(mtime(f) or 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
