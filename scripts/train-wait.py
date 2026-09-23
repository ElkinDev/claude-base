#!/usr/bin/env python3
"""train-wait: the numbers of the train rule, read from git and the evidence files, with no agent. Read-only.

Landings: every first-parent merge on main in the window whose subject reads "Merge lane <token> (...) into
train-<name>" or "merge(train): <sha> into train-<name>, <tail>" is a lane landed on the train its subject names
last; any other first-parent merge (merge(proxy), merge(scripts), merge(docs), merge(main)) is not a lane and is
only counted apart. From them: trains per day and lanes per train. The token of a merge(train) subject is the
tail's first word when it is a lane token (letters and digits, 3 to 8, ending the tail or followed by ":" or "-",
as in "csnv: ...", "pshq", "lrfq-lockrun-fifo-ticket"), else the first token of a closing "(item N, <token>...)";
a prose tail with neither ("the join screen reads the QR first") has no token and is matched by the sha route only.

Landing time: when main moved to hold the merge, read from main's reflog (the move after the train's union gate),
not the merge commit's time (the train's build). A merge the reflog does not reach (an expired or rewritten
reflog) is timed by its commit and counted on the row as "timed by commit"; a window where that count is above
zero is not comparable with one where it is zero.

Union gates: every <scratch>/*gate*/<run>.exit whose run name ends in -merge and whose GATE_START falls in the
window, over every session scratchpad; a train can take more than one (a RED union and its re-gate), so the
mutex price is union gate runs per lane landed, never trains per lane.

Wait: the hours from a lane's last CLEAR review to its landing on main. The review is found in four ways, the first
that matches wins: (a) the queue row of the token names a brief, a lane report or a review whose stem (the file name
without its date) is the review file's stem; (b) the review's first line names the lane ("Review of lane
<token>"); (b2) the review's stem is the token or starts with "<token>-"; (c) a CLEAR review cites a commit of the
lane branch (git rev-list <lane tip> ^<train base>). A review
whose first line names a lane must name THIS lane (two lanes can share a queue row's files), and a union review
(a stem starting with train- or holding -union) never matches a lane. Route (c) is the weakest (a state list can
cite a lane's tip): its matches are printed as unverified and stay out of the median. A review is CLEAR when
its LAST verdict line (CLEAR or BLOCK, with or without a Disposition or Verdict label, markdown stripped) reads
CLEAR, or when a line reads a BLOCK "as CLEAR". The review's time is the reviewer row of landings.md that names
the file when the verdict is the file's first round, else the file's mtime (a later round was appended), and the
landings row again when that mtime falls after the merge (a note written after the landing). A lane with no
CLEAR review found is unmatched, never zero wait.

Usage: python train-wait.py [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--repo <checkout>]
                            [--evidence <dir>] [--scratch-glob <glob of session scratchpads>] [--row] [--verbose]
  Both dates are whole days (00:00 to 23:59:59); the default window is the seven whole days ending today.
  The evidence directory holds landings.md, queue.md and reviews/; it defaults to EVIDENCE_ROOT, the variable
  scripts/evidence-path.py reads, else <repo parent>/evidence.
  --row prints one ledger line; --verbose one line per landing, stamped with main's move (a trailing c: timed
  by the merge commit, the reflog did not reach it).
  Exit 2 when the repo is not a git checkout, a day is not YYYY-MM-DD or --since is after --until.
"""
import argparse
import datetime
import glob
import io
import os
import re
import statistics
import subprocess
import sys
import tempfile

SCRATCH_GLOB = os.path.join(tempfile.gettempdir(), "claude", "*", "*", "scratchpad")
LANE_RE = re.compile(r"^(?:Merge lane (\S+) |merge\(train\): ([0-9a-f]{7,40}) into train-[^\s,]+,?\s*(.*))")
TRAIN_RE = re.compile(r"into (train-[0-9A-Za-z]+)")
SLUG_TOKEN_RE = re.compile(r"^([a-z0-9]{3,8})(?=$|[:\-\s]*\[skip ci\]|:|-)")
ITEM_TOKEN_RE = re.compile(r"\(item \d+, ([a-z0-9]{3,8})\b[^()]*\)\s*(?:\[skip ci\])?$")
STEM_RE = re.compile(r"(-r\d+)?-\d{4}-\d\d-\d\d(-r\d+)?\.md$")


def git(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("REFUSED: git %s failed in %s: %s" % (args[0], repo, p.stderr.strip()[:200]))
    return p.stdout


def evidence_root(repo, given):
    """The folder holding landings.md, queue.md and reviews/: the argument, the variable, else beside the repo."""
    if given:
        return given
    if os.environ.get("EVIDENCE_ROOT"):
        return os.environ["EVIDENCE_ROOT"]
    return os.path.join(os.path.dirname(os.path.abspath(repo)), "evidence")


def token_of(m):
    """The lane token of a LANE_RE match, or "" for a merge(train) subject whose tail names none."""
    if m.group(1):
        return m.group(1)
    tail = m.group(3).strip()
    t = SLUG_TOKEN_RE.match(tail) or ITEM_TOKEN_RE.search(tail)
    return t.group(1) if t else ""


def main_moves(repo, start):
    """{merge commit sha: datetime main moved to hold it}, from main's reflog, oldest move first; only moves from a
    day before the window's start are read. A move that went backward brings nothing; a commit brought twice (a reset
    back past it, then a new landing) keeps its last move, the landing that stood (review r1 note 4)."""
    out = {}
    try:
        text = git(repo, "reflog", "show", "main", "--date=unix", "--format=%H %gd")
    except SystemExit:
        return out
    moves = []
    for ln in text.splitlines():
        m = re.match(r"^([0-9a-f]{40}) \S*@\{(\d+)\}$", ln.strip())
        if m:
            moves.append((int(m.group(2)), m.group(1)))
    moves.reverse()  # the reflog prints newest first
    floor = (start - datetime.timedelta(days=1)).timestamp()
    for i, (ts, sha) in enumerate(moves):
        if ts < floor or i == 0:
            continue
        prev = moves[i - 1][1]
        if prev == sha:
            continue
        try:
            brought = git(repo, "rev-list", "--first-parent", "--merges", sha, "^" + prev).split()
        except SystemExit:  # a tip the reflog names but gc removed: that move is unreadable, its merges fall back
            continue
        when = datetime.datetime.fromtimestamp(ts)
        for c in brought:
            out[c] = when  # oldest move first, so the last landing wins
    return out


def verdict_of(line):
    # a byte order mark and an upper-case label ("VERDICT: CLEAR with notes") are verdicts
    norm = re.sub(r"[*_`#\ufeff]+", "", line).strip()
    m = re.match(r"(?i:Disposition|Verdict|Result)?\s*:?\s*(CLEAR|BLOCK)\b", norm)
    return m.group(1) if m else ""


def landings(ev):
    """{review basename: datetime of the first reviewer row of landings.md that names it}."""
    out = {}
    path = os.path.join(ev, "landings.md")
    if not os.path.exists(path):
        return out
    for line in io.open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"\| (\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})(?::(\d{2}))? \|", line)
        if not m or "[reviewer]" not in line:
            continue
        ts = datetime.datetime(int(m.group(1)[:4]), int(m.group(1)[5:7]), int(m.group(1)[8:10]), int(m.group(2)), int(m.group(3)))
        for name in re.findall(r"reviews/([A-Za-z0-9._-]+\.md)", line):
            out.setdefault(name, ts)
    return out


def reviews(ev, land):
    """One dict per reviews/*.md: base, stem, shas, token (first line), clear, first_round, row time, mtime, union."""
    out = []
    for f in glob.glob(os.path.join(ev, "reviews", "*.md")):
        try:
            with io.open(f, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        lines = text.splitlines()
        verdict_idx = [i for i, l in enumerate(lines) if verdict_of(l)]
        last = verdict_of(lines[verdict_idx[-1]]) if verdict_idx else ""
        read_as_clear = any(re.search(r"\bread as CLEAR\b", l) for l in lines)
        base = os.path.basename(f)
        stem = STEM_RE.sub("", base)
        head = re.search(r"\blane ([A-Za-z0-9._-]+)", lines[0]) if lines else None
        out.append({
            "base": base, "stem": stem,
            "shas": set(s[:7] for s in re.findall(r"\b[0-9a-f]{7,12}\b", text)),
            "token": head.group(1).rstrip(",;:") if head else "",
            "clear": last == "CLEAR" or read_as_clear,
            "first_round": len(verdict_idx) <= 1 and not read_as_clear,
            "row": land.get(base),
            "mtime": datetime.datetime.fromtimestamp(os.path.getmtime(f)),
            "union": stem.startswith("train-") or "-union" in stem,
        })
    return out


def review_time(r, late):
    """The verdict's time: the landings row for a one-round file, the mtime for an appended round unless that
    mtime is after the merge, then the landings row again."""
    if r["first_round"] and r["row"]:
        return r["row"]
    if r["mtime"] <= late:
        return r["mtime"]
    return r["row"] or r["mtime"]


def queue_stems(ev):
    """{token: set of stems} from every cell of every queue.md row."""
    out = {}
    path = os.path.join(ev, "queue.md")
    if not os.path.exists(path):
        return out
    for line in io.open(path, encoding="utf-8", errors="replace"):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        stems = set(STEM_RE.sub("", m) for m in re.findall(r"(?:lanes|briefs|reviews)/([A-Za-z0-9._-]+\.md)", line))
        if cells[2] and stems:
            out.setdefault(cells[2], set()).update(stems)
    return out


def union_gate_runs(scratch_glob, start, end):
    """-merge gate runs started in [start, end]: the .exit files with a GATE_START epoch, over every scratchpad."""
    n = 0
    for ex in glob.glob(os.path.join(scratch_glob, "*gate*", "*.exit")):
        if not os.path.basename(ex)[:-5].endswith("-merge"):
            continue
        try:
            with io.open(ex, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        m = re.search(r"^GATE_START=(\d+)", text, re.M)
        if not m:
            continue
        when = datetime.datetime.fromtimestamp(int(m.group(1)))
        if start <= when <= end:
            n += 1
    return n


def day(text):
    """A whole day, YYYY-MM-DD, spaces around it dropped; anything else is a usage error, never a traceback."""
    try:
        return datetime.date.fromisoformat(text.strip()).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a day (YYYY-MM-DD)" % text)


def dist(v):
    v = sorted(v)
    if not v:
        return "n=0"
    return "n=%d median %.1f p75 %.1f max %.1f h" % (len(v), statistics.median(v), v[int(0.75 * (len(v) - 1))], v[-1])


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # the default window is seven whole days ending today (today-6 to today, both inclusive), the same span as
    # the rule's baseline week; today-7 would read eight days and let a pre-rule day into the ledger row
    ap.add_argument("--since", type=day, default=(datetime.date.today() - datetime.timedelta(days=6)).isoformat())
    ap.add_argument("--until", type=day, default=datetime.date.today().isoformat())
    ap.add_argument("--repo", default=".")
    ap.add_argument("--evidence", default="", help="the folder holding landings.md, queue.md and reviews/")
    ap.add_argument("--scratch-glob", default=SCRATCH_GLOB)
    ap.add_argument("--row", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.since > a.until:
        ap.error("--since %s is after --until %s" % (a.since, a.until))
    if not os.path.exists(os.path.join(a.repo, ".git")):
        print("REFUSED: %s is not a git checkout" % a.repo)
        return 2
    start = datetime.datetime.fromisoformat(a.since)
    end = datetime.datetime.fromisoformat(a.until) + datetime.timedelta(days=1) - datetime.timedelta(seconds=1)
    ev = evidence_root(a.repo, a.evidence)
    land = landings(ev)
    moves = main_moves(a.repo, start)
    by_commit = 0
    revs = reviews(ev, land)
    qst = queue_stems(ev)
    # explicit clock bounds: a bare date to git means "that date at the run's own time"
    log = git(a.repo, "log", "main", "--first-parent", "--merges",
              "--since=%s 00:00:00" % a.since, "--until=%s 23:59:59" % a.until, "--format=%H|%ct|%P|%s")
    trains = {}
    verified = []
    unverified = []
    unmatched = []
    lanes = 0
    other = 0
    for line in log.splitlines():
        sha, ct, parents, subject = line.split("|", 3)
        mt = datetime.datetime.fromtimestamp(int(ct))  # the train build: the review bounds read from it
        ps = parents.split()
        m = LANE_RE.match(subject)
        if len(ps) < 2 or not m:
            other += 1
            continue
        token = token_of(m)
        landed, clock = moves.get(sha), " "
        if landed is None:  # printed with a c in --verbose: timed by the commit, not by main's move
            landed, clock, by_commit = mt, "c", by_commit + 1
        tms = TRAIN_RE.findall(subject)
        train = tms[-1] if tms else "unnamed"
        trains.setdefault(train, []).append(mt)
        lanes += 1
        late = mt + datetime.timedelta(minutes=5)
        early = mt - datetime.timedelta(days=3)  # a token is reused across weeks; an older review is another lane's

        def usable(r):
            if not r["clear"] or r["union"]:
                return False
            if r["token"] and r["token"] != token:
                return False
            return early <= review_time(r, late) <= late

        cands = [(review_time(r, late), r["base"], "queue") for r in revs if usable(r) and any(r["stem"] == s or r["stem"].startswith(s) for s in qst.get(token, ()))]
        if not cands:
            cands = [(review_time(r, late), r["base"], "header") for r in revs if usable(r) and token and r["token"] == token]
        if not cands and token:
            cands = [(review_time(r, late), r["base"], "name") for r in revs
                     if usable(r) and (r["stem"] == token or r["stem"].startswith(token + "-"))]
        if not cands:
            lane_shas = set(x[:7] for x in git(a.repo, "rev-list", "--max-count=80", ps[1], "^" + ps[0]).split())
            cands = [(review_time(r, late), r["base"], "sha") for r in revs if usable(r) and (r["shas"] & lane_shas)]
        if cands:
            when, name, how = max(cands)
            hours = (landed - when).total_seconds() / 3600
            (unverified if how == "sha" else verified).append(hours)
            if a.verbose:
                print("%s %-8s %-14s wait %6.1f h  %s (%s%s)" % (landed.strftime("%m-%d %H:%M") + clock, token[:8], train, hours, name, how, ", unverified" if how == "sha" else ""))
        else:
            unmatched.append(token[:8])
            if a.verbose:
                print("%s %-8s %-14s wait      ? h  no CLEAR review found" % (landed.strftime("%m-%d %H:%M") + clock, token[:8], train))
    days = {}
    for name, times in trains.items():
        days.setdefault(min(times).date(), []).append(name)
    per_day = sorted((d, len(v)) for d, v in days.items())
    ntr = len(trains)
    gates = union_gate_runs(a.scratch_glob, start, end)
    per_train = (lanes / ntr) if ntr else 0.0
    gates_per_lane = (gates / lanes) if lanes else 0.0
    if a.row:
        print("%s train-wait %s to %s: trains %d, lanes %d, lanes/train %.1f, union gate runs %d (%.2f per lane), trains/day %s, wait %s (sha-route %d apart), unmatched %d of %d, other merges %d, timed by commit %d" % (
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), a.since, a.until, ntr, lanes, per_train, gates, gates_per_lane,
            " ".join("%s:%d" % (d.strftime("%m-%d"), n) for d, n in per_day), dist(verified), len(unverified), len(unmatched), lanes, other, by_commit))
        return 0
    print("window %s to %s (whole days): %d trains, %d lanes landed, %.1f lanes per train; %d union gate runs, %.2f per lane landed; %d other first-parent merges, not lanes" % (
        a.since, a.until, ntr, lanes, per_train, gates, gates_per_lane, other))
    print("trains per day: " + ", ".join("%s %d" % (d.isoformat(), n) for d, n in per_day))
    print("wait from the last CLEAR review to the merge, verified matches (queue row or review header): " + dist(verified))
    print("sha-route matches, unverified and outside the median: " + dist(unverified))
    print("landings timed by the merge commit, main's reflog did not reach them: %d of %d" % (by_commit, lanes))
    print("lanes with no CLEAR review found: %d of %d%s" % (len(unmatched), lanes, (" (" + ", ".join(unmatched[:12]) + ")") if unmatched else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
