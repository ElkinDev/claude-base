"""Rework shape of a window: how much work was done twice, one line a day.

Reads three sources and nothing else:
  - the gate exit files in the lane-state config's gates_dirs (each folder and one level below) whose
    mtime falls in the window: last GATE_EXIT, the PHASE_*_EXIT lines (code and the cumulative secs
    offset), FAILED_TESTS, LOCK_WAIT_SECS, and the run name (a name ending in -check is a lane check, in
    -merge a merge run); the same exit files the state sheet reads;
  - the review files (the config's reviews_dir, *.md by mtime in the window): BLOCK, CLEAR+notes, CLEAR
    from the Disposition line in the first 12 lines, and lanes with 3 or more review files; the files
    are the source of verdicts, the register rows only a floor;
  - the register rows (the config's rulings_file) whose stamp falls in the window: delta rounds,
    diagnoses launched, fresh-agent rounds, and the review round numbers named per lane (two rounds, a
    third only for MINOR notes, a fourth means the brief itself was wrong), read from rows of every kind.

Prints one line (or a report with --report). --phases prints per-phase seconds (count, median, p90)
of the window, gate runs and check runs apart: run it once on a baseline window and once on a trial
day and compare the medians, for example before keeping a second build slot.

Classifying rework by cause stays a hand job; this counts what the mechanisms change. Counts from the
register are floors. The review-round counter reads alphabetic lane tokens known from a gate run or a
<token>-gN mention and numeric item lanes (86.8.11, 58.7d) as the rows name them; a row that names the
round without the lane is not counted. The lock figure is printed twice, exit 1 and every non-zero exit,
both floors (teardown after the last phase line is never counted).

Configuration: the lane-state config (--config, else CLAUDE_LANE_STATE_CONFIG, else
~/.claude/lane-state.json): gates_dirs (empty means no gate is read), rulings_file (CLAUDE_RULINGS_FILE
wins over it; default rulings.md beside the config), reviews_dir (default reviews/ beside the register).

Usage:
  python rework-shape.py --since "2026-09-09 08:00" --until "2026-09-09 22:00" [--report] [--phases]
Times are local (the machine clock), matching the register stamps.
"""
import argparse
import collections
import glob
import json
import os
import re
import statistics
from datetime import datetime

# Filled by main() from the lane-state config; a test sets them directly and main() keeps what is set.
GATES_DIRS = None
RULINGS = None
REVIEWS_DIR = None


def parse_local(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def load_paths(config_path):
    """(gates_dirs, rulings_file, reviews_dir) from the lane-state config; an absent or unreadable config
    gives the defaults beside it, never an error, like the state sheet."""
    path = ((config_path or "").strip() or os.environ.get("CLAUDE_LANE_STATE_CONFIG")
            or os.path.join(os.path.expanduser("~"), ".claude", "lane-state.json"))
    base = os.path.dirname(os.path.abspath(path))
    try:
        with open(path, encoding="utf-8-sig") as f:
            loaded = json.load(f)
    except (OSError, ValueError):
        loaded = {}
    if not isinstance(loaded, dict):
        loaded = {}
    text = lambda v: v if isinstance(v, str) and v.strip() else None  # a number or a list where a path belongs is absent
    rulings = os.environ.get("CLAUDE_RULINGS_FILE") or text(loaded.get("rulings_file")) or os.path.join(base, "rulings.md")
    reviews = text(loaded.get("reviews_dir")) or os.path.join(os.path.dirname(os.path.abspath(rulings)), "reviews")
    gates = loaded.get("gates_dirs")
    gates = [gates] if isinstance(gates, str) else gates if isinstance(gates, list) else []
    return [d for d in gates if text(d)], rulings, reviews


def exit_files(since, until):
    out, seen = [], set()
    for folder in GATES_DIRS or []:
        for fp in glob.glob(os.path.join(folder, "*.exit")) + glob.glob(os.path.join(folder, "*", "*.exit")):
            key = os.path.normcase(os.path.realpath(fp))  # a junction and its target are one folder
            if key in seen or not os.path.isfile(fp):
                continue
            seen.add(key)
            try:
                mt = datetime.fromtimestamp(os.path.getmtime(fp))
            except (OSError, OverflowError, ValueError):
                continue  # an impossible mtime is not a run
            if since <= mt <= until:
                out.append((mt, fp))
    return sorted(out)


def read_exit(fp):
    """Returns run, last GATE_EXIT, {phase: code}, FAILED_TESTS, REFUSED, [(phase, secs)] in file order, lock wait."""
    run = os.path.basename(fp)[:-5]
    last = None
    phases = {}
    offsets = []
    failed = ""
    refused = ""
    lock_wait = None
    with open(fp, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("GATE_EXIT="):
                last = line.split("=", 1)[1]
            elif line.startswith("PHASE_") and "_EXIT=" in line:
                name = line[6:line.index("_EXIT=")]
                rest = line[line.index("_EXIT=") + 6:].split()
                phases[name] = rest[0]
                m = re.search(r"secs=(\d+)", line)
                if m:
                    offsets.append((name, int(m.group(1))))
            elif line.startswith("FAILED_TESTS="):
                failed = line[13:]
            elif line.startswith("REFUSED="):
                refused = line[8:]
            elif line.startswith("LOCK_WAIT_SECS="):
                try:
                    lock_wait = int(line[15:])
                except ValueError:
                    pass
    return run, last, phases, failed, refused, offsets, lock_wait


def phase_durations(offsets, lock_wait=None):
    """The secs= values are cumulative offsets from the run start, so the first phase's offset
    carries the queue wait (a run that waited 1376 s for the lock reads secs=1428 on its first phase). The duration of a phase is its offset minus the previous one, the first minus LOCK_WAIT_SECS."""
    out = []
    prev = lock_wait or 0
    for name, secs in offsets:
        out.append((name, max(0, secs - prev)))
        prev = secs
    return out


def norm_phase(name):
    """test__feature_tasks_testDebugUnitTest -> test:feature:tasks; app suites keep their flavor."""
    if name.startswith("test__"):
        body = name[6:]
        body = re.sub(r"_test([A-Z][A-Za-z0-9]*?)?DebugUnitTest$", lambda m: ":" + (m.group(1) or "").lower() if m.group(1) else "", body)
        return "test:" + body.replace("_", ":").strip(":")
    return name


def failed_phase(phases, failed):
    if any(v == "running" for v in phases.values()):
        return "killed"  # a TERMed gate leaves the literal running in its last phase line; not a red test
    if phases.get("compile", "0") not in ("0", "running"):
        return "compile"
    for k, v in phases.items():
        if v not in ("0",) and k.startswith("test_"):
            return "app" if "_app_" in k else "test"
    if phases.get("app", "0") != "0" or phases.get("appscoped", "0") != "0":
        return "app"
    for k in ("format", "ktlint", "detekt"):
        if phases.get(k, "0") != "0":
            return k
    return "?" if failed == "" else "test"


def register_rows(since, until):
    rows = []
    # both row shapes: "- " as older hand rows carry it, none as scripts/row.sh writes it
    pat = re.compile(r"^(?:- )?(\d{4}-\d{2}-\d{2}) (\d{2}):(\d)x ")
    try:
        f = open(RULINGS, encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return rows  # no register configured or on disk: the register half reads 0
    with f:
        for line in f:
            m = pat.match(line)
            if not m:
                continue
            stamp = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}0", "%Y-%m-%d %H:%M")
            if since <= stamp <= until:
                rows.append(line)
    return rows


ROUND_PAT = re.compile(r"(?:^|[\s(])([a-z][a-z0-9]{1,7}(?:-[a-z0-9]{1,6})?|\d{2,3}(?:\.\d{1,2}){1,3}[a-z]?)(?: review)?(?: delta)?(?: fix)? round (\d)\b", re.I)


GATE_TOKEN = re.compile(r"\b([a-z][a-z0-9]{1,7})-g\d")


def register_gate_tokens():
    """Every token named as <token>-gN anywhere in the register, not only in the window: a lane whose gate ran the day
    before still names its round today."""
    try:
        with open(RULINGS, encoding="utf-8", errors="replace") as f:
            return {m.lower() for line in f for m in GATE_TOKEN.findall(line)}
    except OSError:
        return set()


def review_rounds(rows, known):
    """Highest review round number named per lane token in the register rows of the window, counting
    only tokens that are lane tokens (they own a gate run in the window, or appear as <token>-gN in the
    rows given or in the known set, which main() fills from the whole register), plus numeric item lanes
    (86.8.11, 58.7d). A floor: a row that names the round without the lane token is not counted."""
    known = set(known)
    for r in rows:
        known.update(m.lower() for m in GATE_TOKEN.findall(r))
    best = {}
    for r in rows:
        for lane, n in ROUND_PAT.findall(r):
            lane = lane.lower()
            if lane not in known and not lane[0].isdigit():
                continue  # a numeric lane (86.8.11, 58.7d) is an item number and always a lane
            best[lane] = max(best.get(lane, 0), int(n))
    return best


VERDICT_RE = re.compile(r"\b(CLEAR|BLOCK)\b")


def review_verdict(fp):
    """Verdict of a review file from its first 12 lines: the line holding 'Disposition' (a '## Disposition: X'
    heading, a 'Disposition: X' line, or a bare '## Disposition' heading followed by the verdict line; 41 of
    the 42 files of 2026-09-15 carry one of the three). BLOCK, CLEAR+notes, CLEAR, or None when no verdict
    line is found in the first 12 lines nor at the last 'Disposition' line of the file (listed under --report)."""
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            head = [next(f, "") for _ in range(12)]
    except OSError:
        return None
    for i, line in enumerate(head):
        low = line.lower()
        bare = line.lstrip("# *").startswith(("CLEAR", "BLOCK"))  # the 09-10 and 09-11 files open with a bare verdict line
        if "disposition" not in low and "verdict" not in low and not bare:
            continue
        text = line
        if not VERDICT_RE.search(text):
            nxt = [x for x in head[i + 1:] if x.strip()]
            text = nxt[0] if nxt else ""
        m = VERDICT_RE.search(text)
        if not m:
            return None
        if m.group(1) == "BLOCK":
            return "BLOCK"
        return "CLEAR+notes" if re.search(r"notes|MINOR", text) else "CLEAR"
    # fallback: the last line of the file holding 'disposition' (the 09-10 and 09-11 files close with it)
    with open(fp, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    idx = [i for i, x in enumerate(lines) if "disposition" in x.lower() or "verdict" in x.lower()]
    if not idx:
        return None
    i = idx[-1]
    text = lines[i]
    if not VERDICT_RE.search(text):
        nxt = [x for x in lines[i + 1:] if x.strip()]
        text = nxt[0] if nxt else ""
    m = VERDICT_RE.search(text)
    if not m:
        return None
    if m.group(1) == "BLOCK":
        return "BLOCK"
    return "CLEAR+notes" if re.search(r"notes|MINOR", text) else "CLEAR"


def review_files(since, until):
    """Review files whose mtime falls in the window: (mtime, name, lane slug, verdict). The slug is the file
    name without its date and without a round suffix (-fixN, -rN, -roundN, -notes, -waivers), so the files
    of one lane count as its review rounds (fix1 is round 2)."""
    out = []
    for fp in glob.glob(os.path.join(REVIEWS_DIR, "*.md")):
        mt = datetime.fromtimestamp(os.path.getmtime(fp))
        if not (since <= mt <= until):
            continue
        name = os.path.basename(fp)[:-3]
        slug = re.sub(r"-\d{4}-\d{2}-\d{2}(-\d{4})?$", "", name)
        slug = re.sub(r"-(fix\d*|r\d+|round\d*|notes|waivers|rework)$", "", slug)
        out.append((mt, name, slug, review_verdict(fp)))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--phases", action="store_true", help="per-phase seconds of the window, gate and check runs apart")
    ap.add_argument("--config", default=None, help="lane-state config, default CLAUDE_LANE_STATE_CONFIG then ~/.claude/lane-state.json")
    a = ap.parse_args()
    try:
        since, until = parse_local(a.since.strip()), parse_local(a.until.strip())
    except ValueError:  # a bare date is a usage error, never a traceback
        ap.error("--since and --until take local YYYY-MM-DD HH:MM")
    global GATES_DIRS, RULINGS, REVIEWS_DIR
    gates, rulings, reviews = load_paths(a.config)
    GATES_DIRS = gates if GATES_DIRS is None else GATES_DIRS
    RULINGS = rulings if RULINGS is None else RULINGS
    REVIEWS_DIR = reviews if REVIEWS_DIR is None else REVIEWS_DIR

    runs = []
    for mt, fp in exit_files(since, until):
        try:
            run, last, phases, failed, refused, offsets, lock_wait = read_exit(fp)
        except OSError:
            continue  # a file held open by its writer is read on the next run
        runs.append((mt, run, last, phases, failed, refused, offsets, lock_wait))
    reds = [r for r in runs if r[2] == "1"]
    by_phase = collections.Counter(failed_phase(r[3], r[4]) for r in reds)
    # lock seconds the red runs held: the last phase offset net of the queue wait (the mutex time a red run costs). A floor
    # twice over: teardown after the last phase is not counted, and a run that died before any PHASE line adds 0.
    # The exit-1 figure is the plan's baseline (17,091 s on the map window); the second adds every other non-zero
    # exit that reached a phase, a TERMed or killed run included.
    red_lock_secs = sum(max(0, r[6][-1][1] - (r[7] or 0)) for r in reds if r[6])
    nonzero_lock_secs = sum(max(0, r[6][-1][1] - (r[7] or 0)) for r in runs if r[2] not in (None, "0") and r[6])
    checks = [r for r in runs if r[1].endswith("-check")]
    check_green = sum(1 for r in checks if r[2] == "0")
    merges = [r for r in runs if r[1].endswith("-merge")]
    refused_stamp = sum(1 for r in runs if r[2] in ("92", "94"))
    moved_tip = sum(1 for r in runs if r[2] == "96")
    lanes = collections.Counter(re.sub(r"-(g\d+[a-z]?|merge|check)$", "", r[1]) for r in runs if not r[1].endswith("-check"))
    three_plus = sum(1 for v in lanes.values() if v >= 3)

    rows = register_rows(since, until)
    delta_block = sum(1 for r in rows if re.search(r"delta", r, re.I) and re.search(r"\bBLOCK\b", r))
    diagnoses = sum(1 for r in rows if re.search(r"diagnos", r, re.I) and re.search(r"launch|brief", r, re.I))
    fresh = sum(1 for r in rows if re.search(r"fresh (implementer|agent|reviewer)", r, re.I))
    rounds = review_rounds(rows, set(lanes.keys()) | register_gate_tokens())
    at_three = sorted((k, v) for k, v in rounds.items() if v >= 3)
    rfiles = review_files(since, until)
    rv = collections.Counter(v or "none" for _, _, _, v in rfiles)
    per_slug = collections.Counter(sl for _, _, sl, _ in rfiles)
    files_three = sorted((k, v) for k, v in per_slug.items() if v >= 3)

    line = (f"rework-shape {a.since}..{a.until}: gate runs {len(runs)} (merge {len(merges)}, checks {len(checks)} green {check_green}), "
            f"red at exit 1: {len(reds)} [{', '.join(f'{k} {v}' for k, v in by_phase.most_common())}] holding the lock {red_lock_secs} s at exit 1 ({nonzero_lock_secs} s over every non-zero exit; floors, teardown uncounted), refused for a stamp {refused_stamp}, moved tip {moved_tip}, "
            f"lanes at 3+ gates {three_plus} of {len(lanes)}; register: delta BLOCK rows {delta_block}, diagnoses {diagnoses}, fresh-agent rounds {fresh}, "
            f"lanes at review round 3+ {len(at_three)}{' (' + ', '.join(f'{k} r{v}' for k, v in at_three) + ')' if at_three else ''} (register counts are floors)")
    line += (f"; review files {len(rfiles)}: BLOCK {rv['BLOCK']}, CLEAR+notes {rv['CLEAR+notes']}, CLEAR {rv['CLEAR']}, "
             f"no verdict {rv['none']}; lanes at 3+ review files {len(files_three)} of {len(per_slug)}")
    print(line)
    if a.report:
        print("--- review files (verdict from the Disposition line in the first 12 lines; slug = name minus date and round suffix)")
        for _, name, _, v in rfiles:
            if v is None:
                print(f"  no verdict: {name}")
        for k, v in files_three:
            print(f"  {v:3d} review files: {k}")
    if a.report:
        print("--- red runs")
        for r in reds:
            print(f"  {r[0]:%m-%d %H:%M} {r[1]:22s} phase={failed_phase(r[3], r[4])} failed={r[4][:80]}")
        print("--- lanes by gates")
        for k, v in lanes.most_common(15):
            print(f"  {v:3d} {k}")
        print("--- review rounds per lane (floor, from register rows naming '<lane> round N')")
        for k, v in sorted(rounds.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {v:3d} {k}")
    if a.phases:
        for kind, subset in (("gate", [r for r in runs if not r[1].endswith("-check")]), ("check", checks)):
            if not subset:
                print(f"--- {kind} runs 0 in the window: no run named -{kind}, this half of the comparison is empty")
                continue
            per = collections.defaultdict(list)
            waits = [r[7] for r in subset if r[7] is not None]
            for r in subset:
                for name, secs in phase_durations(r[6], r[7]):
                    per[norm_phase(name)].append(secs)
            print(f"--- {kind} runs {len(subset)}, lock waits n={len(waits)}"
                  + (f" median={int(statistics.median(waits))} p90={sorted(waits)[max(0, int(len(waits) * 0.9) - 1)]}" if waits else ""))
            for name in sorted(per, key=lambda n: -len(per[n])):
                v = sorted(per[name])
                print(f"  {name:36s} n={len(v):3d} median={int(statistics.median(v)):5d} p90={v[max(0, int(len(v) * 0.9) - 1)]:5d} max={v[-1]:5d}")


if __name__ == "__main__":
    main()
