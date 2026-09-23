#!/usr/bin/env python3
"""train-due: whether a lane train is due under the orchestrator seat's Landing trains rule, read from disk with no
agent. Read-only.

    python train-due.py [--repo <checkout>] [--evidence <dir>]     one line: `train DUE: ...` or `train not due: ...`
    python train-due.py --verbose                                  the line, then every unmerged lane not counted, why

A train leaves on readiness, never on a clock, so the trigger has to be read at every planning point; a rule held in
a head is how a trigger gets lost. Serves automation (the trigger is read from disk) and optimization (a lane stops
waiting for a clock). The number is train-wait.py's median wait from a lane's CLEAR review to its landing.

Waiting lanes. Every worktree of the repository (`git worktree list`) whose branch is not merged into main (`git
branch --no-merged main`), whose folder is <main folder>-<token>, whose newest review inside the last 3 days is
CLEAR (a BLOCK newer than a CLEAR takes the lane out), and that has a green gate on its current tip. A review belongs
to every lane folder (merged ones too) it names by any of three routes, none ranked above another: a mention of the
token on its line 1 (any case, between characters that are not letters or digits), its file name being the token or
the token and a dash, and a queue row of the lane citing a stem the file name is or starts with plus a dash (a union
review: line 1 only). Never a commit sha, never a
bare prefix, so a sibling's review (lnaa2 beside lnaa) never speaks for a lane. A newest review that names two lanes
keeps both out and listed: the reader never picks an owner when its evidence disagrees. The green gate: an exit file
of a <token>gate folder in any session scratchpad whose last GATE_EXIT is 0 and whose TIP_END (else TIP_START) is the
worktree's HEAD. Ready since is the later of the review and that exit file. Listed apart, and never counted: a lane
whose newest review is not CLEAR or also names another lane, one CLEAR with no green gate found on its tip (a later
commit, a lockrun round, a gate still running, a gate folder named otherwise), an unmerged branch in a folder not
named <main folder>-<token>, and a detached worktree whose HEAD main does not hold. The seat decides each; `apart N`
counts them, and --verbose also lists the quiet ones (no review in 3 days).

Held lanes. A lane the seat holds on purpose (an owner ruling it waits for, a candidate freeze) is a line of
train-holds.txt in the evidence folder (TRAIN_HOLDS moves it), `<token> <reason>`, written when the hold is decided
and removed when it is released; a held lane is listed as held and never makes a train due.

Due: nothing is due before 08:00, after 21:15, or while a union gate runs (a -merge exit file whose GATE_EXIT is
still the 97 sentinel and younger than two hours; one bound serves a dead union and a live one, so a union that runs
past two hours reads as not running and the seat checks it before building); else two or more waiting lanes are due,
or one that has waited --minutes (default 60). A fix a held release candidate waits on is the seat's trigger to name;
the line lists every waiting lane, so it is one read away.

The evidence folder holds landings.md, queue.md and reviews/: --evidence, else EVIDENCE_ROOT, else <repo
parent>/evidence (train-wait.py's rule). Exit 0 when due, 1 when not due, 2 on a usage error, a repository that is
not a checkout, or any failure to read (a git call that fails or passes its 60 s bound, a train-wait.py that does not
load): 1 always means read and not due.
"""
import argparse
import datetime
import glob
import importlib.util
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SENTINEL = "97"
RUNNING_FOR = 2 * 3600
REVIEW_DAYS = 3
FIRST_TRAIN = datetime.time(8, 0)
LAST_TRAIN = datetime.time(21, 15)


def holds(path):
    """{token: reason} of the holds file; comments and blank lines skipped; a missing file holds nothing, an
    unreadable one says so on stderr and holds nothing."""
    out = {}
    try:
        with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
            for ln in fh:
                parts = ln.strip().split(None, 1)
                if parts and not parts[0].startswith("#"):
                    out[parts[0]] = parts[1] if len(parts) > 1 else "held"
    except FileNotFoundError:
        pass
    except OSError as e:
        print("train-due: %s cannot be read, nothing held (%s)" % (path, e.__class__.__name__), file=sys.stderr)
    return out


def git(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args[0], repo, p.stderr.strip()[:200]))
    return p.stdout


def load_train_wait():
    path = os.path.join(HERE, "train-wait.py")
    spec = importlib.util.spec_from_file_location("train_wait", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worktrees(repo):
    """[(folder, head sha, branch or None)] from `git worktree list --porcelain`."""
    out, cur = [], {}
    for ln in git(repo, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not ln.strip():
            if cur.get("worktree"):
                out.append((cur["worktree"].replace("\\", "/"), cur.get("HEAD", ""), cur.get("branch")))
            cur = {}
            continue
        key, _, value = ln.partition(" ")
        cur[key] = value.replace("refs/heads/", "") if key == "branch" else value
    return out


def exit_values(path):
    values = {}
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                key, sep, value = ln.rstrip("\r\n").partition("=")
                if sep and re.match(r"^[A-Z_]+$", key):
                    values[key] = value  # a repeated key keeps the last value written
    except OSError:
        return None
    return values


def lane_of_dir(name):
    """`rcpugate` gives `rcpu`, `bf-gate` gives `bf`."""
    if name.endswith("gate") and len(name) > 4:
        stripped = name[:-4].rstrip("-_")
        if stripped:
            return stripped
    return name


def gates(scratch_glob, now):
    """({lane: [(mtime, exit, tip)]} of lane gates, the number of union gates still running)."""
    lanes, running = {}, 0
    for path in glob.glob(os.path.join(scratch_glob, "*gate*", "*.exit")):
        values = exit_values(path)
        if values is None:
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        run = os.path.basename(path)[:-5]
        code = values.get("GATE_EXIT", "")
        if run.endswith("-merge"):
            if code == SENTINEL and now - mtime <= RUNNING_FOR:
                running += 1
            continue
        tip = values.get("TIP_END") or values.get("TIP_START") or ""
        lanes.setdefault(lane_of_dir(os.path.basename(os.path.dirname(path))), []).append((mtime, code, tip))
    return lanes, running


def names(stem, key):
    """Whether a review stem names this key: the key itself or the key and a dash (qr3c-fix1 names qr3c, never qr3)."""
    return stem == key or stem.startswith(key + "-")


def mention_patterns(tokens):
    """{token: pattern} matching the token anywhere, in any case, between characters that are not letters or digits,
    so `ovcd-fix1.done`, `C:/work/app-pshq`, `Lane ptrw`, `(aud2, lnaa, pfx)` and `F102` all name their lane and
    `lnaa2` never names lnaa."""
    return dict((t, re.compile(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])", re.I)) for t in tokens)


def header_lanes(path, patterns):
    """The lane tokens line 1 of a review mentions, by mention_patterns. Line 1 has no fixed shape, so the route is a
    mention, never a keyword or a whole word: a narrower parse misses real shapes (a capital Lane, a trailing dot, no
    keyword at all, a token inside a dashed path). An extra mention (a lane named row in "an account row") only adds
    an apart line, the safe side."""
    try:
        with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return set()
    return set(t for t, p in patterns.items() if p.search(first))


def lanes_of(r, tokens, qst, header):
    """Every lane a review may belong to: the lanes its line 1 names, each token its stem names, and each lane whose
    queue row cites a stem it names; a union review (train-*, *-union*) belongs only to the lanes its line 1 names.
    No route outranks another: a gate never picks an owner when the evidence disagrees, since a wrong pick boards a
    BLOCKed lane and an ambiguity costs one line the seat reads."""
    out = set(header)
    if r["union"]:
        return out
    out.update(t for t in tokens if names(r["stem"], t))
    for t, stems in qst.items():
        if t in tokens and any(s and names(r["stem"], s) for s in stems):
            out.add(t)
    return out


def natural(base):
    """fix10 after fix9 when two rounds carry one time."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", base)]


def newest_reviews(tw, ev, revs, qst, tokens, now):
    """{token: (time, file, clear, other lanes it names)} of each lane's newest review inside the bound, CLEAR or
    not. The newest decides: a BLOCK written after a CLEAR (rounds are files of their own) takes the lane out until
    a newer CLEAR."""
    late, early = now, now - datetime.timedelta(days=REVIEW_DAYS)
    best, patterns = {}, mention_patterns(tokens)
    for r in revs:
        when = tw.review_time(r, late)
        if not (early <= when <= late):
            continue
        lanes = lanes_of(r, tokens, qst, header_lanes(os.path.join(ev, "reviews", r["base"]), patterns))
        if not lanes:
            continue
        key = (when, natural(r["base"]))
        for token in lanes:
            if token not in best or key > best[token][0]:
                best[token] = (key, (when, r["base"], r["clear"], sorted(lanes - {token})))
    return {t: v[1] for t, v in best.items()}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--evidence", default="", help="the folder holding landings.md, queue.md and reviews/")
    ap.add_argument("--scratch-glob", default=None, help="glob of session scratchpads (train-wait.py's default)")
    ap.add_argument("--minutes", type=int, default=60, help="the wait that makes one lane due")
    ap.add_argument("--now", default=None, help="YYYY-MM-DD HH:MM, for a repeatable reading")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.minutes < 1:
        ap.error("--minutes is a positive whole number of minutes")
    try:
        # an explicit --now that is blank is a caller's empty variable, never the real clock by accident
        now = datetime.datetime.strptime(a.now.strip(), "%Y-%m-%d %H:%M") if a.now is not None else datetime.datetime.now()
    except ValueError:
        ap.error("--now is YYYY-MM-DD HH:MM, got %r" % a.now)
    if not os.path.exists(os.path.join(a.repo, ".git")):
        print("train-due: %s is not a git checkout" % a.repo, file=sys.stderr)
        return 2
    tw = load_train_wait()
    ev = tw.evidence_root(a.repo, a.evidence)
    revs = tw.reviews(ev, tw.landings(ev))
    qst = tw.queue_stems(ev)
    unmerged = set(git(a.repo, "branch", "--no-merged", "main", "--format=%(refname:short)").split())
    lane_gates, running = gates(a.scratch_glob or tw.SCRATCH_GLOB, now.timestamp())
    trees = worktrees(a.repo)
    # the main worktree comes first and git prints its real path, so a --repo through a junction or a linked
    # worktree still names the lanes <main folder>-<token>
    prefix = os.path.basename(trees[0][0].rstrip("/")) + "-"
    waiting, apart, held, quiet = [], [], [], []
    hold = holds(os.environ.get("TRAIN_HOLDS") or os.path.join(ev, "train-holds.txt"))
    main_tip = git(a.repo, "rev-parse", "main").strip()
    # every lane folder's token, merged ones too, so a review that names a merged sibling is seen to name it
    tokens = set(os.path.basename(f)[len(prefix):] for f, _, b in trees
                 if b and os.path.basename(f).startswith(prefix) and len(os.path.basename(f)) > len(prefix))
    newest = newest_reviews(tw, ev, revs, qst, tokens, now)
    for folder, head, branch in trees:
        name = os.path.basename(folder)
        if branch is None:
            # a detached worktree is a candidate build or a probe, never a lane; one whose HEAD main does not hold
            # is listed, so a lane checked out detached is never lost without a line
            if head and head != main_tip and subprocess.run(
                    ["git", "-C", a.repo, "merge-base", "--is-ancestor", head, "main"],
                    capture_output=True, timeout=60).returncode == 1:
                apart.append("%s: detached at %s, not on main; no branch, so no lane token" % (name, head[:9]))
            continue
        if branch not in unmerged or branch.startswith("train-"):
            continue
        if not name.startswith(prefix):
            apart.append("%s: branch %s is not on main but the folder is not %s<token>" % (name, branch, prefix))
            continue
        token = name[len(prefix):]
        review = newest.get(token)
        if review is None:
            quiet.append("%s: no review in %d days" % (token, REVIEW_DAYS))
            continue
        if review[3]:
            apart.append("%s: the newest review %s also names %s; the seat decides"
                         % (token, review[1], ", ".join(review[3])))
            continue
        if not review[2]:
            apart.append("%s: the newest review %s is not CLEAR" % (token, review[1]))
            continue
        green = [m for m, code, tip in lane_gates.get(token, []) if code == "0" and tip and head and tip == head]
        if not green:
            apart.append("%s: CLEAR (%s) but no green gate found on its tip %s in a %sgate folder"
                         % (token, review[1], head[:9], token))
            continue
        if token in hold:
            held.append("%s (%s)" % (token, hold[token]))
            continue
        since = max(review[0], datetime.datetime.fromtimestamp(max(green)))
        waiting.append((since, token))
    waiting.sort()
    listed = ", ".join("%s %d min" % (t, max(0, int((now - s).total_seconds() // 60))) for s, t in waiting) or "none"
    listed += "; held %s" % (", ".join(held) if held else "none")
    if now.time() > LAST_TRAIN:
        verdict, due = "train not due: after 21:15, later lanes leave at 08:00", False
    elif now.time() < FIRST_TRAIN:
        verdict, due = "train not due: before 08:00, lanes leave at 08:00", False
    elif running:
        verdict, due = "train not due: a union gate is running", False
    elif len(waiting) >= 2:
        verdict, due = "train DUE: %d lanes CLEAR and green" % len(waiting), True
    elif waiting and (now - waiting[0][0]).total_seconds() >= a.minutes * 60:
        verdict, due = "train DUE: %s has waited %d min" % (waiting[0][1], (now - waiting[0][0]).total_seconds() // 60), True
    elif waiting:
        at = waiting[0][0] + datetime.timedelta(minutes=a.minutes)
        verdict, due = "train not due: one lane waiting, due at %s" % at.strftime("%H:%M"), False
    else:
        verdict, due = "train not due: no lane CLEAR and green on its tip", False
    print("%s; lanes %s; apart %d" % (verdict, listed, len(apart)))
    if a.verbose:
        for line in apart:
            print("  apart: " + line)
        for line in quiet:
            print("  quiet: " + line)
    return 0 if due else 1


def cli(argv):
    """main, with every failure to read as exit 2 and one line on stderr; argparse's own exit 2 passes through."""
    for stream in (sys.stdout, sys.stderr):  # a hold reason outside the console code page never stops the read
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        return main(argv)
    except Exception as e:  # noqa: BLE001, a reader that cannot read must never print a verdict or exit 1
        print("train-due: cannot read (%s: %s)" % (e.__class__.__name__, str(e)[:300]), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
