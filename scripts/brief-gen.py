"""brief-gen.py: the review, fix and notes briefs of a lane from its facts on disk.

A hand-written review, fix or notes brief is mostly the same text every time: the reader, deliverable, role and
budget paragraph, the lane's facts (worktree, branch, base, tip, commits, brief and report paths) and the standard
Checks, Report and Forbidden sections. This script writes those parts from disk and git, quotes the reviewer's
findings verbatim for a fix or notes brief, and leaves the architect's part (the attack points, the change, the
pins) to one argument or to a marked placeholder. One shell call replaces one long Write; the reasoning stays typed
by the orchestrator.

  python brief-gen.py review <token> [--delta N --review <path>] [--attack "1. ...\\n2. ..."] [--tools 14] [--no-git | --base <sha>] [--out <path>]
  python brief-gen.py fix    <token> --review <path> [--round N] [--change "..."] [--pins "..."] [--tests "..." | --no-git] [--out <path>]
  python brief-gen.py notes  <token> --review <path> [--change "..."] [--pins "..."] [--tests "..." | --no-git] [--out <path>]

Facts: the newest <lanes>/<slug>-<date>.md whose title starts with "# Lane <token>" gives the slug, the item (an id
in brackets such as (ABC-12), (item 7) or (3.2)) and the topic; the worktree is the configured pattern with the token
filled in, or the report's "Worktree <path>" word when the pattern is unset or names no folder; git gives branch,
tip, base (merge-base with the base branch) and the commits above the base. The output path defaults to
<briefs>/<slug>-review-<today>.md, -fixN-, -notes-; an existing file is never overwritten (--force). The text is
printed to stdout as well, so the caller reads what it launched. Placeholders are marked <<...>>. A fix or notes
brief goes to an implementer, whose launches the hook grades with brief-check.py (Report section present, a test
class name under Pins): fill --pins, or a deny tier of 2 or more refuses it. A review brief goes to a reviewer,
which the hook never grades (claude/hooks/guard-delegate.py, LOGGED_TARGETS), so it carries no Pins section.

With own_tests_command and own_tests_done set, a lane ends when it launches its own-tests run: the commit, the
precheck, the report, and the launch as the last tool call; a green run is not resumed (the review and the gate read
the report and the done file), a red one comes back as the next round. A resume and a fresh fix lane cost about
the same, so only the green share saves, and no lane waits for a verdict.

Guards. A review refuses --round (a review of round N is --delta N, 2 or more; --delta is refused on the other
kinds) and refuses a deliverable already under the reviews folder (--force-review writes over it; --force covers
only the brief file at --out). Since the reviewer, not this script, writes that file, the review brief also tells the
reviewer to write nothing when it exists at its start, unless --force-review was given. --no-git is the shape of a lane whose files are in no git repository (tooling kept in a plain folder): it
edits staged .new files beside the live ones and never a live file, runs its pins under timeout 120 against the
.new and once against the live file, and writes no commit, precheck or test run; it is refused with --tests.

Stacked lanes. A lane cut from another lane's unmerged tip gets `review <token> --base <sha or branch>`: the review's
base becomes that tip, refused unless it is a commit strictly below the lane's tip or when it leaves more than 40
commits above it (a wrong sha); it is the review kind's only, and refused with --no-git. Without it the base stays the
merge-base with the base branch, and when a local branch not on the base branch has its tip below the lane's on its
first-parent chain (the nearest one), the brief's Lane line and stderr name it with the --base to pass; a branch merged
into the lane sits on a second parent and is not named. The script warns and the caller decides. A review brief lists
at most 100 commits above its base, then one line with the git log command for the rest.

Configuration: brief-gen.json beside this script, or the file BRIEF_GEN_CONFIG names; copy brief-gen.example.json
and edit it. Every key is optional and the defaults write a brief that names no build tool and no machine path:
  evidence_root      the folder holding the lanes, briefs and reviews folders; default EVIDENCE_ROOT, else the
                     working directory
  lanes_dir, briefs_dir, reviews_dir   relative to the evidence root; default lanes, briefs, reviews
  template           the lane brief template the hostile-input line points at, relative to the evidence root
                     unless absolute; default briefs/TEMPLATE-lane-brief.md
  worktree           the worktree of a lane, {token} filled in, for example "C:/src/myapp-{token}"; default null
  base_branch        the branch the base is the merge-base with; default main
  laws               a laws file named in the implementer briefs; default null, the sentence is left out
  subject_suffix     what every commit subject ends with, for example "[skip ci]"; default "", nothing asked
  own_tests_command  the command that runs the lane's own test classes on its tip. {worktree} (as written),
                     {worktree_posix} (/c/... form), {worktree_native} (C:/... form), {tag} and {tests} are filled
                     in; default null, a placeholder
  own_tests_done     the file that run writes when it ends, same fields; when set, the lane ends its turn on it
                     instead of waiting; it needs own_tests_command; default null
  precheck_command   a static check of the tip run after the tests, {worktree}, {worktree_posix} and
                     {worktree_native} filled in; default null, the line is left out
  review_tools, fix_tools   the tool budgets; default 14 and 20
  run_verdict_line   the prefix of the line a done file ends with its verdict on, for example "exitCodes=" for a
                     done file reading exitCodes=0,0; when set, a review brief is refused while the newest test run
                     on the lane's tip is red (any code other than 0) or still running (no done file yet), and
                     --allow-red-run writes it on purpose, naming the run for the reviewer. The runs are the files
                     in the folder of own_tests_done, grouped by the tag before the first dot, so its file name must
                     start with {tag}. A run is on the tip when its done file names tip=<sha> of HEAD, or when the
                     HEAD reflog entry in force at its start names HEAD. Default null, no check
  guard_log          a file the check appends one line to per review brief it read, `<stamp> review <token>
                     green|refused|allowed <why>`, relative to the evidence root unless absolute, for example
                     "ledger/brief-gen-guard.log"; written only while its folder exists, and a write that fails
                     never stops the brief. It needs run_verdict_line. Default null, no log
A config file that is not a JSON object, or a key of the wrong type, is exit 2 with the reason: a brief written on
a half-read config would carry the wrong commands.
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys

TODAY = datetime.date.today().strftime("%Y-%m-%d")
DEFAULTS = {
    "evidence_root": None, "lanes_dir": "lanes", "briefs_dir": "briefs", "reviews_dir": "reviews",
    "template": "briefs/TEMPLATE-lane-brief.md", "worktree": None, "base_branch": "main", "laws": None,
    "subject_suffix": "", "own_tests_command": None, "own_tests_done": None, "precheck_command": None,
    "review_tools": 14, "fix_tools": 20, "run_verdict_line": None, "guard_log": None,
}
INT_KEYS = ("review_tools", "fix_tools")
REQUIRED_STR = ("lanes_dir", "briefs_dir", "reviews_dir", "template", "base_branch")
TEMPLATE_KEYS = ("own_tests_command", "own_tests_done", "precheck_command")


class ConfigError(Exception):
    pass


def load_config(path=None):
    """DEFAULTS overlaid with the config file; no file is the defaults, a bad file is ConfigError."""
    path = path or os.environ.get("BRIEF_GEN_CONFIG") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                      "brief-gen.json")
    cfg = dict(DEFAULTS)
    if not os.path.exists(path):
        if os.environ.get("BRIEF_GEN_CONFIG"):
            raise ConfigError("BRIEF_GEN_CONFIG names %s, which does not exist" % path)
        return cfg
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigError("%s cannot be read as JSON (%s)" % (path, e))
    if not isinstance(data, dict):
        raise ConfigError("%s is not a JSON object" % path)
    for k, v in data.items():
        if k.startswith("_"):
            continue
        if k not in DEFAULTS:
            raise ConfigError("%s has an unknown key %r" % (path, k))
        if k in INT_KEYS:
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise ConfigError("%s: %s must be a whole number above 0" % (path, k))
        elif k in REQUIRED_STR:
            if not isinstance(v, str) or not v.strip():
                raise ConfigError("%s: %s must be a string that is not blank" % (path, k))
        elif v is not None and not isinstance(v, str):
            raise ConfigError("%s: %s must be a string or null" % (path, k))
        cfg[k] = v
    if cfg["own_tests_done"] and not cfg["own_tests_command"]:  # the done file alone would render the old order
        raise ConfigError("%s: own_tests_done needs own_tests_command, the run that writes it" % path)
    if cfg["run_verdict_line"] is not None and not cfg["run_verdict_line"].strip():
        raise ConfigError("%s: run_verdict_line is blank; give the prefix of the done file's verdict line, or null" % path)
    if cfg["run_verdict_line"] and not cfg["own_tests_done"]:
        raise ConfigError("%s: run_verdict_line needs own_tests_done, the file whose verdict it reads" % path)
    if cfg["run_verdict_line"] and not os.path.basename(cfg["own_tests_done"].replace("\\", "/")).startswith("{tag}."):
        raise ConfigError("%s: run_verdict_line groups a run's files by the tag before the first dot, so the file name "
                          "of own_tests_done must start with {tag}." % path)
    if cfg["run_verdict_line"] and "{tag}" in os.path.dirname(cfg["own_tests_done"].replace("\\", "/")):
        raise ConfigError("%s: run_verdict_line reads one runs folder, so {tag} may appear only in the file name of "
                          "own_tests_done, not in its folder" % path)
    if cfg["guard_log"] is not None and not cfg["guard_log"].strip():
        raise ConfigError("%s: guard_log is blank; give the log file, or null" % path)
    if cfg["guard_log"] and not cfg["run_verdict_line"]:  # a log of a check that never runs would stay empty
        raise ConfigError("%s: guard_log needs run_verdict_line, the check whose verdicts it logs" % path)
    for k in TEMPLATE_KEYS:  # a stray brace would otherwise fail at render time, after the facts were read
        if cfg[k]:
            try:
                cfg[k].format(worktree="w", worktree_posix="w", worktree_native="w", tag="t", tests="s")
            except (KeyError, IndexError, ValueError) as e:
                raise ConfigError("%s: %s has a field this script does not fill (%s); write a literal brace as {{ or }}"
                                  % (path, k, e))
    return cfg


CFG = dict(DEFAULTS)


def root():
    return CFG["evidence_root"] or os.environ.get("EVIDENCE_ROOT") or os.getcwd()


def under_root(rel):
    return (rel if os.path.isabs(rel) else os.path.join(root(), rel)).replace("\\", "/")


def hostile_line():
    return ("If this round writes or changes a script (a file under scripts/, or a .py, .ps1, .sh or .ts file), "
            "the appended section also carries the hostile-input table of the Report section of %s: one row per "
            "probe H1 to H8, each under `timeout 120` and never against a shared build lock, a device lock, the "
            "register or a device." % under_root(CFG["template"]))


def native(p):
    """C:/... form of a path (a /c/... path is refused by Windows tools)."""
    p = p.replace("\\", "/")
    if len(p) > 2 and p[0] == "/" and p[2] == "/":
        return p[1].upper() + ":" + p[2:]
    return p


def posix(path):
    m = re.match(r"^([A-Za-z]):/(.*)$", path.replace("\\", "/"))
    return "/%s/%s" % (m.group(1).lower(), m.group(2)) if m else path


def fill(template, wt, **extra):
    return template.format(worktree=wt, worktree_posix=posix(wt), worktree_native=native(wt), **extra)


def git(wt, *args):
    r = subprocess.run(["git", "-C", wt] + list(args), capture_output=True, text=True, timeout=60)
    return r.stdout.strip() if r.returncode == 0 else ""


def git_ok(wt, *args):
    return subprocess.run(["git", "-C", wt] + list(args), capture_output=True, text=True, timeout=60).returncode == 0


class Refused(Exception):
    pass


RUN_TIP = re.compile(r"\btip=([0-9a-f]{7,64})\b")  # a runner may name the tip it ran on
UNREADABLE = object()
NO_RUN = object()  # red_run: no worktree, no commit or no run on the tip


def read_text(path, limit=65536):
    """The start of a run file, or None when it cannot be read (locked, a folder, gone between list and open)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit).lstrip("\ufeff")
    except OSError:
        return None


def tip_history(wt, limit=2000):
    """The HEAD reflog of the worktree as [(unix time, sha)], newest first, or [] when it has none. The newest entry
    alone is not when the sha became the tip: a stash, a reset to HEAD or a checkout away and back writes an entry
    that keeps the sha. A worktree's HEAD reflog is its own."""
    out = git(wt, "log", "-g", "-n", str(limit), "--date=unix", "--format=%gd %H", "HEAD")
    hist = []
    for line in out.splitlines():
        m = re.match(r"^HEAD@\{(\d+)\} ([0-9a-f]{40}|[0-9a-f]{64})$", line.strip())  # SHA-1 or SHA-256
        if m:
            hist.append((int(m.group(1)), m.group(2)))
    return hist


def sha_at(history, t):
    """The sha HEAD held at time t: the newest reflog entry (by position) written at or before t, else None, a run
    older than the kept reflog."""
    for when, sha in history:
        if when <= t:
            return sha
    return None


def runs_on_tip(wt, head, history, head_time):
    """The test runs of the lane that ran on its tip, oldest first, as dicts (tag, start, done, path). The runs live in
    the folder of own_tests_done; a run's tag is its file names up to the first dot, its done file is own_tests_done
    for that tag, and its start is the oldest mtime of its files. A run whose done file names a tip= is on the tip when
    that sha is HEAD; any other run is on it when the HEAD reflog entry in force at its start names HEAD's sha, or,
    with no reflog, when it started at or after HEAD's committer time. done is the done file's text, None while it is
    absent, UNREADABLE when it exists and cannot be read."""
    def done_path(tag):  # a filesystem path: the /c/... form of {worktree_posix} is read as C:/... here
        return CFG["own_tests_done"].format(worktree=wt, worktree_posix=native(wt), worktree_native=native(wt),
                                            tag=tag, tests="")
    folder = os.path.dirname(done_path("x"))
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    times = {}
    for n in names:
        p = os.path.join(folder, n)
        if n.startswith(".") or not os.path.isfile(p):
            continue
        try:
            times.setdefault(n.split(".", 1)[0], []).append(os.path.getmtime(p))
        except OSError:
            continue
    runs = []
    for tag, stamps in times.items():
        path = done_path(tag)
        done = read_text(path) if os.path.exists(path) else None
        if done is None and os.path.exists(path):
            done = UNREADABLE
        start = min(stamps)
        mt = RUN_TIP.search(done if isinstance(done, str) else "")
        if mt:
            on_tip = head.startswith(mt.group(1)) or mt.group(1).startswith(head)
        elif history:
            on_tip = sha_at(history, start) == head
        else:
            on_tip = head_time is not None and start >= head_time
        if on_tip:
            runs.append({"tag": tag, "start": start, "done": done, "path": path.replace("\\", "/")})
    return sorted(runs, key=lambda r: (r["start"], r["tag"]))


def red_run(wt):
    """Why a review would read a red or unfinished run on the lane's tip, or None: the newest run on the tip by start
    decides, finished or not, and its done file must hold a run_verdict_line whose comma-separated codes are all 0.
    A second test run queued beside a review launched on a green one is the case --allow-red-run exists for. Off
    unless run_verdict_line is set; no worktree, no commit or no run on the tip gives no opinion: NO_RUN, which the
    guard log writes as none, never green (a round reviewed with no run on its tip must not read as a green one)."""
    if not CFG["run_verdict_line"]:
        return None
    head = git(wt, "rev-parse", "HEAD")
    if not head:
        return NO_RUN
    when = git(wt, "log", "-1", "--format=%ct", "HEAD")
    runs = runs_on_tip(wt, head, tip_history(wt), int(when) if when.isdigit() else None)
    if not runs:
        return NO_RUN
    r = runs[-1]
    if r["done"] is None:
        return "the newest run on the tip %s, %s, is still running or was killed (%s is absent)" % (head[:9], r["tag"], r["path"])
    if r["done"] is UNREADABLE:
        return "the newest run on the tip %s, %s, cannot be read" % (head[:9], r["path"])
    prefix = CFG["run_verdict_line"]
    m = re.search(r"^%s([^\r\n]*)" % re.escape(prefix), r["done"], re.M)
    if not m:
        return "the newest run on the tip %s has no %s line: %s" % (head[:9], prefix, r["path"])
    if any(c.strip() != "0" for c in m.group(1).split(",")):
        return "the newest run on the tip %s is red: %s reads %s%s" % (head[:9], r["path"], prefix, m.group(1).strip())
    return None


def guard_log(token, verdict, why=None, brief=None):
    """One line in the guard_log file per review brief the run check read, `<stamp> review <token> green|none|
    refused|allowed <why> [brief=<path>]`, so a reader can tell the check ran and what it said. none is a tip with no
    run, no commit or no worktree. brief= names the brief written, last on the line, spaces written %20, so a reader
    can pair the line with its review round by path while the fields before it keep their place. Written only while
    the log's folder exists; a write that fails never stops the brief or changes its exit code."""
    if not CFG["guard_log"]:
        return
    path = under_root(CFG["guard_log"].strip())
    if not os.path.isdir(os.path.dirname(path)):
        return
    line = "%s review %s %s %s%s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), " ".join(token.split()),
                                       verdict, " ".join((why or "-").split())[:300],
                                       (" brief=" + brief.replace("\\", "/").replace(" ", "%20")) if brief else "")
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
    except OSError:
        pass


MAX_BASE_COMMITS = 40  # a --base with more commits above it is refused: a lane has a handful, so the sha is wrong
MAX_COMMITS = 100      # commits listed above a review's base; more is cut with a pointer


def stacked_on(wt, head):
    """The nearest local branch whose tip is below HEAD, on HEAD's first-parent chain and not on the base branch, as
    (branch, tip), or None. A lane cut from another lane's unmerged tip has one, and the merge-base with the base
    branch then lists that lane's commits as this lane's. A branch merged INTO the lane (a train's members, a sibling
    merged in) has its tip on a second parent, off the first-parent chain, so it is not named. It only warns: --base
    decides."""
    out = git(wt, "branch", "--merged", "HEAD", "--no-merged", CFG["base_branch"], "--format=%(refname:short) %(objectname)")
    if not out:
        return None
    chain = set(git(wt, "rev-list", "--first-parent", "HEAD", "^" + CFG["base_branch"]).split())
    best = None
    for line in out.splitlines():
        name, _, tip = line.strip().partition(" ")
        if not tip or tip == head or tip not in chain:
            continue
        n = git(wt, "rev-list", "--count", "%s..HEAD" % tip)
        if n.isdigit() and (best is None or int(n) < best[0]):
            best = (int(n), name, tip)
    return (best[1], best[2]) if best else None


def subject_rule():
    """' ending `X`' when a subject suffix is configured, else nothing."""
    return " ending `%s`" % CFG["subject_suffix"] if CFG["subject_suffix"] else ""


def lane_facts(token):
    lanes = under_root(CFG["lanes_dir"])
    reports = sorted(glob.glob(os.path.join(lanes, "*.md")), key=os.path.getmtime, reverse=True)
    pat = re.compile(r"^# Lane %s\b" % re.escape(token))
    for rp in reports:
        with open(rp, encoding="utf-8", errors="replace") as f:
            head = f.read(4000)
        title = head.splitlines()[0] if head else ""
        if not pat.match(title):
            continue
        name = os.path.basename(rp)[:-3]
        m = re.search(r"^(.*)-(\d{4}-\d{2}-\d{2})$", name)
        slug, rdate = (m.group(1), m.group(2)) if m else (name, TODAY)
        item = re.search(r"\(([A-Za-z]+-\d+|item \d+|\d+(?:\.\d+)*)\)", title)
        topic = title.split(":", 1)[1].strip() if ":" in title else ""
        topic = re.sub(r",?\s*\d{4}-\d{2}-\d{2}$", "", topic)  # a report title that ends with its date
        wt = CFG["worktree"].replace("{token}", token) if CFG["worktree"] else ""
        # the first Worktree word of the head decides, by position; a backticked path there keeps its spaces
        # (`C:/src/my app-tok`), a bare one ends at the first space or comma
        mw = re.search(r"[Ww]orktree (?:`([A-Za-z]:/[^`\n]+|/[^`\n]+)`|`?([A-Za-z]:/[^\s,`]+|/[^\s,`]+))", head)
        if (not wt or not os.path.isdir(wt)) and mw:
            wt = mw.group(1) or mw.group(2).rstrip(".;:)")  # a bare path loses a sentence's period; a backticked one is exact
        wt = wt or "<<worktree: set worktree in the config or name it in the report>>"
        briefs = under_root(CFG["briefs_dir"])
        brief = os.path.join(briefs, "%s-%s.md" % (slug, rdate)).replace("\\", "/")
        if not os.path.exists(brief):
            cands = sorted(glob.glob(os.path.join(briefs, slug + "-20*.md")))
            brief = cands[-1].replace("\\", "/") if cands else brief + " <<brief not found>>"
        return {"token": token, "slug": slug, "item": item.group(1) if item else "<<item>>", "topic": topic,
                "report": rp.replace("\\", "/"), "brief": brief, "wt": wt}
    sys.exit("brief-gen: no lane report whose title starts with '# Lane %s' under %s" % (token, lanes))


def git_facts(fx, base_ref=None):
    wt = fx["wt"]
    fx["base_how"] = "merge-base with %s" % CFG["base_branch"]
    if not os.path.isdir(wt):
        if base_ref:
            raise Refused("--base %s needs the lane's worktree, and %s is not a directory" % (base_ref, wt))
        fx.update(branch="<<branch>>", tip="<<tip>>", base="<<base>>",
                  commits=["<<commits: worktree %s not found; a lane with no git repository takes --no-git>>" % wt])
        return fx
    head = git(wt, "rev-parse", "HEAD")
    if not head:
        if base_ref:
            raise Refused("--base %s needs a git worktree, and %s is not one" % (base_ref, wt))
        why = "<<%s is not a git worktree (directory present, worktree pruned)>>" % wt
        fx.update(branch=why, tip=why, base=why, commits=[why])
        return fx
    fx["branch"] = git(wt, "rev-parse", "--abbrev-ref", "HEAD") or "<<branch>>"
    fx["tip"] = head[:9]
    if base_ref:  # a stacked lane: the caller names the tip it was cut from
        base = git(wt, "rev-parse", "--verify", "--quiet", base_ref + "^{commit}")
        if not base:
            raise Refused("--base %s is not a commit in %s" % (base_ref, wt))
        if base == head:
            raise Refused("--base %s is the tip itself: nothing above it to review" % base_ref)
        if not git_ok(wt, "merge-base", "--is-ancestor", base, head):
            raise Refused("--base %s (%s) is not below the tip %s" % (base_ref, base[:9], head[:9]))
        fx["base_how"] = "given by --base: the lane was cut from that tip, not from %s" % CFG["base_branch"]
    else:
        base = git(wt, "merge-base", "HEAD", CFG["base_branch"])
        below = stacked_on(wt, head)
        if below:
            fx["base_how"] = ("merge-base with %s; branch %s at %s is below the tip and not on %s: if the lane was cut "
                              "from it, the commits up to %s are that lane's, not this one's (brief-gen ... --base %s)"
                              % (CFG["base_branch"], below[0], below[1][:9], CFG["base_branch"], below[1][:9], below[1][:9]))
            sys.stderr.write("brief-gen: warning, %s\n" % fx["base_how"])
    fx["base"] = base[:9] if base else "<<base>>"
    log = git(wt, "log", "--format=%h %s", "%s..HEAD" % base) if base else ""
    lines = log.splitlines()
    if base_ref and len(lines) > MAX_BASE_COMMITS:  # --base HEAD~999 would list 999 commits
        raise Refused("--base %s leaves %d commits above it, more than a lane's %d; check the sha"
                      % (base_ref, len(lines), MAX_BASE_COMMITS))
    if len(lines) > MAX_COMMITS:  # a filled fact, not a placeholder: no << >> marker
        lines = lines[:MAX_COMMITS] + ["(%d more commits above the base: git log --oneline %s..%s)"
                                       % (len(lines) - MAX_COMMITS, base[:9], head[:9])]
    fx["commits"] = lines or ["<<no commits above the base>>"]
    return fx


def findings(review_path, kinds):
    """The numbered or bulleted items under the '## MAJOR' and '## MINOR' headings of a review file, verbatim,
    plus lines that open with MAJOR n or MINOR n outside those headings (the two shapes reviews are written in)."""
    with open(review_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    out, section, last_section = [], None, None
    for line in text.splitlines():
        m = re.match(r"^##\s+(MAJOR|MINOR)\b", line)
        if m:
            section = m.group(1)
            continue
        if line.startswith("#"):
            section = None
            continue
        s = line.strip()
        if not s or s.lower().rstrip(".") in ("none", "no findings", "nothing"):
            continue
        if section in kinds and (re.match(r"^(\d+\.|-|\*)\s+", s) or re.match(r"^(MAJOR|MINOR)\b", s)):
            n = re.match(r"^(\d+)\.", s)
            body = re.sub(r"^(\d+\.|-|\*)\s+", "", s)
            if re.match(r"^(MAJOR|MINOR)\b", body):
                out.append(body)  # the item names its own severity ("1. MAJOR file:line ..." or "MAJOR 1: ...")
            else:
                out.append("%s %s: %s" % (section, n.group(1) if n else "", body))
            last_section = section
        elif section in kinds and out and section == last_section:
            out[-1] += " " + s  # a continuation line of the item above, same heading only
        elif section is None and re.match(r"^(MAJOR|MINOR)\s*\d*\s*[:,.]", s) and s[:5] in kinds:
            out.append(s)
            last_section = None
    return out


def disposition(review_path):
    with open(review_path, encoding="utf-8", errors="replace") as f:
        head = [next(f, "") for _ in range(12)]
    for i, l in enumerate(head):
        if "disposition" in l.lower() or l.lstrip("# *").startswith(("CLEAR", "BLOCK")):
            t = l if re.search(r"CLEAR|BLOCK", l) else next((x for x in head[i + 1:] if x.strip()), "")
            m = re.search(r"(CLEAR with notes|CLEAR|BLOCK)[^.\n]*", t)
            if m:
                # a review opening `Disposition: BLOCK, lane <token>` (the review brief's first line) quotes the verdict only
                return re.sub(r",\s*lane\s+\S+\s*$", "", m.group(0).strip())
    return "<<disposition>>"


def own_tests_line(fx, tag, tests):
    """The Checks line that runs the lane's own test classes on its committed tip. With a done file the launch is the
    round's last act: the report is written before it and a green run is not resumed (the lane ends at its launch)."""
    tests = tests or "<<the test selection: your own test classes, never a whole suite>>"
    cmd = CFG["own_tests_command"]
    if not cmd:
        return ("<<the command that runs your own test classes (%s) on the committed tip, tag %s; the report names "
                "the tag and the exit>>" % (tests, tag))
    line = "`%s`" % fill(cmd, fx["wt"], tag=tag, tests=tests)
    if CFG["own_tests_done"]:
        done = fill(CFG["own_tests_done"], fx["wt"], tag=tag, tests=tests)
        line += (" as the LAST tool call of the round, then END YOUR TURN with one line naming %s and %s: no watcher, "
                 "no polling, no sleep; the launch is a FOREGROUND tool call that returns at once, never "
                 "run_in_background. A green run is not resumed: the review and the gate read the report and the done "
                 "file. A red run comes back as the next round with the run's reading." % (fx["report"], done))
    else:
        line += "; the report names the tag and the exit."
    return line


NOGIT_WHERE = ("No worktree, no branch, no commit: the lane's files are in no git repository. They are the staged "
               "`.new` files the report names, each beside its live file with a `.bak` of it")


def review_deliverable(fx, delta):
    """The file a review of this round writes: <reviews>/<slug>-<date>.md, or <slug>-fix<N-1>-<date>.md for --delta N."""
    name = "%s-fix%d-%s.md" % (fx["slug"], delta - 1, TODAY) if delta else "%s-%s.md" % (fx["slug"], TODAY)
    return "%s/%s" % (under_root(CFG["reviews_dir"]), name)


def review_brief(fx, a):
    delta = a.delta
    kind = "Delta review of lane %s round %d" % (fx["token"], delta) if delta else "Review of lane %s" % fx["token"]
    nogit = getattr(a, "no_git", False)
    out_name = "%s-fix%d-review-%s.md" % (fx["slug"], delta - 1, TODAY) if delta else "%s-review-%s.md" % (fx["slug"], TODAY)
    report = review_deliverable(fx, delta)
    lane_word = "The delta" if delta else "The lane"
    tools = a.tools or CFG["review_tools"]
    commits = "; ".join(fx.get("commits") or [])
    suffix = "subjects end %s, " % CFG["subject_suffix"] if CFG["subject_suffix"] else ""
    hygiene = ("no live file touched, a .bak beside each staged file, no git command, only the named paths touched"
               if nogit else "%sno attribution, no amend, only the named paths touched" % suffix)
    attack = (a.attack.replace("\\n", "\n") if a.attack else
              "1. <<what must be true at the %s, with file:line; three to six numbered points, the last one hygiene: "
              "%s>>" % ("staged .new files" if nogit else "tip", hygiene))
    prev = ("Previous review: %s (read it first; every MAJOR must be closed at the new %s, every MINOR answered or "
            "carried).\n\n" % (a.review, "files" if nogit else "tip")) if delta and a.review else ""
    runs = ""
    if CFG["own_tests_done"] and not nogit:
        runs = " Test runs under %s/." % os.path.dirname(fill(CFG["own_tests_done"], fx["wt"], tag="x", tests=""))
    purpose = a.purpose or "<<one of quality, optimization, automation, token reduction, and the number it proves>>"
    at = "the `.new` file as it is on disk when you start" if nogit else fx["tip"]
    if nogit:
        where = ("%s; read `diff <live> <live>.new` for the change. Brief: %s. Report: %s (read it whole, Open items "
                 "included)." % (NOGIT_WHERE, fx["brief"], fx["report"]))
    else:
        where = ("Worktree %s, branch %s, base %s (%s), tip %s, commits above the base: %s. Brief: %s. "
                 "Report: %s (read it whole, Open items included).%s" % (fx["wt"], fx["branch"], fx["base"],
                 fx.get("base_how", "merge-base with %s" % CFG["base_branch"]), fx["tip"], commits, fx["brief"],
                 fx["report"], runs))
        if fx.get("run_note"):
            where += " Written with --allow-red-run on purpose: %s. Read that run before you trust a pin." % fx["run_note"]
    # the reviewer writes the file, so a brief generated while an earlier reviewer of the round still runs passes the
    # existence check in main(); the reviewer checks again when it starts
    guard = ("" if getattr(a, "force_review", False) else
             " If %s already exists when you start, write nothing and end with one line naming it: a review of this "
             "round is already on disk." % report)
    body = f"""# {kind} ({fx['item']}): {fx['topic']}, {TODAY}

Your reader is a session, never a person. Write in English. The deliverable is {report}; your last message is one line naming that path, no summary. Role: reviewer (read only toward the repo: no build, no lock, no git write; if your tools carry no Write tool, write the report through Bash with a quoted heredoc). Plan for {tools} tool uses and write the report by turn {max(4, tools - 2)}. Purpose: {purpose}.

## {lane_word}

{where}

{prev}## Attack

{attack}

## Report

{report}, at most 40 lines: the first line is exactly `Disposition: <CLEAR, CLEAR with notes or BLOCK>, lane {fx['token']}` (the train readers, train-due.py and train-wait.py, find the lane on that line), then MAJOR, MINOR, Verified sound, Not covered. Every finding with file:line at {at}.{guard}
"""
    return out_name, body


def fix_or_notes_brief(fx, a, kind):
    rnd = a.round if kind == "fix" else None
    kinds = ("MAJOR", "MINOR") if kind == "fix" else ("MINOR",)
    quoted = findings(a.review, kinds) if a.review else []
    quoted_text = ("\n".join("- %s" % q for q in quoted) if quoted else
                   "- <<no %s item found under a '## MAJOR' or '## MINOR' heading of %s; quote them here by hand>>"
                   % ("/".join(kinds), a.review))
    disp = disposition(a.review) if a.review else "<<disposition>>"
    section = "## Round %d" % rnd if kind == "fix" else "## Notes applied"
    out_name = "%s-fix%d-%s.md" % (fx["slug"], rnd - 1, TODAY) if kind == "fix" else "%s-notes-%s.md" % (fx["slug"], TODAY)
    title = "Lane %s round %d" % (fx["token"], rnd) if kind == "fix" else "Lane %s notes" % fx["token"]
    budget = a.tools or CFG["fix_tools"]
    tag = "%s-fix%d" % (fx["token"], rnd - 1) if kind == "fix" else "%s-notes" % fx["token"]
    ending = subject_rule()
    change = a.change or ("<<the change: one commit%s, the items above; every other file stays>>" % (
        ", subject" + ending if ending else "") if kind == "fix" else
        "<<the change per note, one commit%s>>" % (", subject" + ending if ending else ""))
    pins = a.pins or "<<the test class names that prove it, one per line; a red-by-mutation line when the review asked for one>>"
    purpose_kind = ("blocked round %d (%s)" % (rnd - 1, disp)) if kind == "fix" else ("is %s" % disp)
    laws = " Laws: %s." % CFG["laws"] if CFG["laws"] else ""
    lines = '40' if kind == 'fix' else '25'
    nobody = "the delta review" if kind == "fix" else "the train"
    if getattr(a, "no_git", False):
        if not a.change:
            change = "<<the change per item, in the staged .new files the report names; every other file stays>>"
        on = "the staged .new files"
        where = ("%s. Edit those `.new` files in place; a file the change needs that has none gets `cp -p <live> "
                 "<live>.<stamp>.bak` and `cp -p <live> <live>.new` first. Never a live file: the files are swapped "
                 "into place after the review. No build, no git." % NOGIT_WHERE)
        change_head = "## Change, in the staged .new files"
        checks = ("In this order (the lane ends with its report; the swap into place is the orchestrator's):\n\n"
                  "- First the change, in the `.new` files only.\n"
                  "- Then each pin under Pins, under `timeout 120`, against the `.new` file (the suite's variable naming "
                  "the script under test) and once against the live file; paste the green line and the red one. Never "
                  "poll in a loop of tool calls, never tail -f.\n"
                  "- Last, the report, appended whole as the Report section says; then END YOUR TURN with one line "
                  "naming %s." % fx["report"])
        report_what = ("each `.new` file with the line count of `diff <live> <live>.new`, the changes at their `.new` "
                       "line numbers, the pin lines green on the `.new` and red on the live file, the hostile-input "
                       "table when a script changed (below), and Open items LAST")
        report_when = ""
        forbidden = ("Any file outside the ones the review names; any edit of a live file (a file without the .new "
                     "suffix) or of a .bak, beyond the two cp -p copies the Where section orders (reading and running "
                     "the live file for the red pin is allowed); any git command; any worktree; a shared build lock, a "
                     "device lock, any scratchpad of another session.")
    else:
        on = fx["tip"]
        where = ("Worktree %s, branch %s, tip %s (verify with git rev-parse; commit on top, never amend or rebase). No "
                 "build run except the one under Checks." % (fx["wt"], fx["branch"], fx["tip"]))
        change_head = "## Change, one commit%s" % (", subject" + ending if ending else "")
        pre = fill(CFG["precheck_command"], fx["wt"]) if CFG["precheck_command"] else None
        forbidden = ("Any file outside the ones the review names; any amend or rebase; any worktree other than %s."
                     % fx["wt"])
        if CFG["own_tests_command"] and CFG["own_tests_done"]:
            # a lane ends when it launches its own-tests run: the report first, the launch last, no resume on green
            done = fill(CFG["own_tests_done"], fx["wt"], tag=tag, tests=a.tests or "")
            checks = ("In this order (a lane ends when it launches its own-tests run; the gate and %s are the "
                      "orchestrator's):\n\n- First the commit of the Change section, on top of %s.\n" % (nobody, fx["tip"]))
            if pre:
                checks += ("- Then `%s` on the committed tip; paste its line. No commit after it. Never poll in a loop "
                           "of tool calls, never tail -f.\n" % pre)
            checks += ("- Then the report, appended whole as the Report section says, before the run it names.\n"
                       "- Last, the own-tests run on the committed tip: %s" % own_tests_line(fx, tag, a.tests))
            report_what = ("the new tip and the commit subject, the changes, the own-tests tag %s and its done file %s "
                           "with the words \"verdict in the done file\"%s, the hostile-input table when a script "
                           "changed (below), and Open items LAST" % (tag, done, ", the precheck line" if pre else ""))
            report_when = " before the own-tests launch"
        else:
            if pre:
                precheck = ("- Then `%s` on the new tip; paste its line. No commit after it. Never poll in a loop of "
                            "tool calls, never tail -f.\n" % pre)
            else:
                precheck = "- Never poll in a loop of tool calls, never tail -f.\n"
            checks = ("- Green, on the committed tip: %s\n%s- The gate and %s are the orchestrator's."
                      % (own_tests_line(fx, tag, a.tests), precheck, nobody))
            report_what = ("the new tip and the commit subject, the changes, the test run tag and exit%s, and Open items "
                           "if any" % (", the precheck line" if pre else ""))
            report_when = ""
    body = f"""# {title} ({fx['item']}): {fx['topic']}, {TODAY}

Your reader is a session, never a person. Write in English. The deliverable is a "{section}" section appended to {fx['report']}; your last message is one line naming that path, no summary. No attribution lines in commits or reports. Role: implementer-light.{laws} Budget {budget} tool uses.

## Purpose

Quality. The review {a.review or '<<review path>>'} {purpose_kind} on {on}. The number: <<the count that proves it, one today, zero after>>.

## Where

{where}

## The review, quoted

{quoted_text}

{change_head}

{change}

## Pins

{pins}

## Checks

{checks}

## Report

Append "{section}" to {fx['report']}{report_when}, at most {lines} lines: {report_what}.

{hostile_line()}

## Forbidden

{forbidden}
"""
    return out_name, body


def main():
    global CFG
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["review", "fix", "notes"])
    ap.add_argument("token")
    ap.add_argument("--review", help="the review file the fix or notes brief quotes (or the previous review of a delta review)")
    ap.add_argument("--delta", type=int, default=0, help="review: the round this delta review reads (2 for the first fix)")
    ap.add_argument("--round", type=int, default=None, help="fix: the round the implementer opens, 2 or more (2 by default); refused on the other kinds")
    ap.add_argument("--attack", help="review: the numbered attack points (\\n separated)")
    ap.add_argument("--change", help="fix or notes: the change section")
    ap.add_argument("--pins", help="fix or notes: the pins section; name a test class (ending in Test) or a golden")
    ap.add_argument("--tests", help="fix or notes: the test selection of the own-tests run")
    ap.add_argument("--purpose", help="review: the purpose line (one of the four and its number)")
    ap.add_argument("--tools", type=int, default=None, help="the tool budget (review_tools and fix_tools by default)")
    ap.add_argument("--no-git", action="store_true", help="a lane with no git repository: staged .new files, no branch, tip, commit or test run")
    ap.add_argument("--base", help="review: the sha or branch a stacked lane was cut from (default the merge-base with the base branch)")
    ap.add_argument("--out")
    ap.add_argument("--force", action="store_true", help="overwrite an existing brief file at --out")
    ap.add_argument("--force-review", action="store_true", help="review: write the brief although its deliverable exists, "
                    "and drop the line that stops its reviewer on an existing file")
    ap.add_argument("--allow-red-run", action="store_true", help="review: write the brief although the newest test run "
                    "on the tip is red or still running; the brief then tells the reviewer so (needs run_verdict_line)")
    a = ap.parse_args()
    if not a.token.strip():  # a blank token would write a brief of placeholders
        ap.error("give a lane token that is not blank")
    if a.delta and a.kind != "review":  # --delta was dropped silently on the other kinds
        ap.error("--delta is the review kind's; a fix opens its round with --round N")
    if a.delta and a.delta < 2:
        ap.error("--delta N is the round the review reads, 2 or more (the first fix is round 2)")
    if a.round is not None and a.kind != "fix":  # a review of round N is --delta N
        ap.error("--round is the fix kind's; a review of round N takes --delta N")
    if a.kind == "fix":
        a.round = 2 if a.round is None else a.round
        if a.round < 2:
            ap.error("--round is the round the fix opens, 2 or more")
    if a.no_git and a.tests:
        ap.error("--tests names the own-tests run, which a --no-git lane does not launch; its pins run under timeout 120")
    if a.base is not None and a.kind != "review":  # the base only shapes the review's commit list
        ap.error("--base is the review kind's: it sets the base whose commits the reviewer reads")
    if a.base is not None and a.no_git:
        ap.error("--base names a commit, which a --no-git lane does not have")
    if a.base is not None and not a.base.strip():
        ap.error("--base is blank; give the sha or branch the lane was cut from")
    if a.allow_red_run and a.kind != "review":
        ap.error("--allow-red-run is the review kind's: only a review brief reads the lane's test runs")
    if a.allow_red_run and a.no_git:
        ap.error("--allow-red-run reads the lane's test runs, which a --no-git lane does not have")
    try:
        CFG = load_config()
    except ConfigError as e:
        print("brief-gen: %s" % e, file=sys.stderr)
        return 2
    if a.allow_red_run and not CFG["run_verdict_line"]:  # a flag that could change nothing is refused (review 0923m note 3)
        ap.error("--allow-red-run needs run_verdict_line in the config: with no verdict line no run is read")
    if a.review:
        a.review = os.path.abspath(a.review).replace("\\", "/")
        if not os.path.exists(a.review):
            sys.exit("brief-gen: review file not found: %s" % a.review)
    fx = lane_facts(a.token)
    guard = None  # the run check's verdict, logged once the brief is on disk
    if not a.no_git:
        try:
            fx = git_facts(fx, a.base.strip() if a.base else None)
        except Refused as e:
            sys.exit("brief-gen: refused, %s" % e)
    if a.kind == "review":
        deliverable = review_deliverable(fx, a.delta)
        if os.path.exists(deliverable) and not a.force_review:
            sys.exit("brief-gen: refused, %s exists: a review of this round already wrote it; a review of round N "
                     "takes --delta N, and --force-review writes over it" % deliverable)
        why = None if a.no_git else red_run(fx["wt"])
        if why is NO_RUN:
            why = None
            guard = ("none", "no run on the tip of %s" % posix(fx["wt"]))  # no worktree, no commit or no run
        elif not a.no_git and CFG["run_verdict_line"]:
            guard = ("allowed", why) if why else ("green", None)
        if why and not a.allow_red_run:
            guard_log(a.token, "refused", why)
            sys.exit("brief-gen: refused, %s; a review reads a finished green run: fix the red, wait for the run or run "
                     "the tests again, or pass --allow-red-run to write the brief on purpose" % why)
        if why:
            sys.stderr.write("brief-gen: note, --allow-red-run: %s\n" % why)
            fx["run_note"] = why
        name, body = review_brief(fx, a)
    else:
        if not a.review:
            sys.exit("brief-gen: %s needs --review <file>" % a.kind)
        name, body = fix_or_notes_brief(fx, a, a.kind)
    out = a.out or os.path.join(under_root(CFG["briefs_dir"]), name)
    if os.path.exists(out) and not a.force:
        sys.exit("brief-gen: %s exists; pass --force to overwrite" % out)
    if os.path.isdir(out):  # --force never writes over a folder, and a folder is never a traceback
        sys.exit("brief-gen: refused, --out is a folder, not a file: %s" % out.replace("\\", "/"))
    folder = os.path.dirname(os.path.abspath(out))
    if a.out and not os.path.isdir(folder):  # a mistyped --out is refused, never a stray folder (S16 H2)
        sys.exit("brief-gen: refused, the folder of --out does not exist: %s" % folder.replace("\\", "/"))
    os.makedirs(folder, exist_ok=True)  # the default path: a fresh evidence root has no briefs folder yet
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    if guard:
        guard_log(a.token, *guard, brief=os.path.abspath(out))
    sys.stdout.write(body)
    print("brief-gen wrote %s (%d chars, %d placeholders)" % (out.replace("\\", "/"), len(body), body.count("<<")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
