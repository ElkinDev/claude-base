"""Renders a state sheet of the lanes from what is already on disk. No model runs.

Three subcommands:

  gates   one line per gate exit file, newest first, default the last 24 hours.
  law     the whole sheet (fixed lines, worktrees, rulings, gates, landings,
          lane reports and briefs) written atomically to the sheet path.
  lane    one hand-back block for a lane token (its report, the tip check, its
          reviews, its gates, other recent reports naming a file it touches), at
          most 40 lines, and one line appended to the hand-back log.

Sources are the gate exit files a gate runner writes, read-only git in the worktrees of
the configured repository and in the merges its main branch took, the tail of the landings
file, the last rows of the rulings
register, and the mtimes of the lane reports and briefs. A source that is missing or
unreadable never fails the render: its section prints one line saying why. That matters
because the compaction recovery hook prints the Gates section at every compaction.

Configuration is a JSON file, by default `~/.claude/lane-state.json`. Every key is
optional. What a key does not set falls back to a path beside the config file itself, so
one folder of sources needs a config of three lines; with no config file at all the
fallbacks sit under `~/.claude`, and nothing is scanned that was never configured.

Keys: fixed_lines (list of lines printed first), gates_dirs (list of folders holding the
gate exit files, empty means no gate is scanned), landings_file, rulings_file,
project_repo (the repository whose worktrees are listed, empty means none),
lanes_glob, briefs_glob, reports_file (the owner reports ledger rendered by the
section below, checked by reports-check.py beside this file), sessions_glob (the
session files that count as evidence for that ledger: one glob or a list), devices (the device words
that bind a session token to one phone, empty for no narrowing), reviews_glob (the review
files the lane block reads), handback_log (the file the lane block appends one line to),
brief_gen (the brief-gen.py whose verdict reader the lane block uses, default beside this
file; the kit ships it under scripts/, so point this key at the project's copy).

Env seams, each of which wins over the default and is what the recovery hook wires:
CLAUDE_LANE_STATE_CONFIG (the config file), CLAUDE_LANE_STATE_SHEET (the sheet written
when no --out is given), CLAUDE_RULINGS_FILE (the register, over the configured one).
"""
import argparse
import concurrent.futures
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime

HOME_CLAUDE = os.path.join(os.path.expanduser("~"), ".claude")
DEFAULT_CONFIG = os.path.join(HOME_CLAUDE, "lane-state.json")
DEFAULT_SHEET = os.path.join(HOME_CLAUDE, "law.md")

DEAD_AFTER_SECS = 2 * 3600  # a sentinel exit file older than this is a dead gate
SENTINEL_EXIT = "97"
GIT_TIMEOUT = 10
LANDINGS_TAIL_BYTES = 64 * 1024
LANDINGS_ROWS = 8
LANDINGS_CLIP = 220
RULINGS_ROWS = 30
RULINGS_CLIP = 400
RECENT_HOURS = 24
# The rulings are never cut, so the sheet is allowed the lines they take.
MAX_LINES = 170
SUBJECT_CLIP = 90
FIRST_LINE_CLIP = 100
REPORTS_CLIP = 320
REPORT_WORDS_CLIP = 120
MERGES_CLIP = 320

PHASE_RE = re.compile(r"^PHASE_(?P<name>.+?)_EXIT=(?P<code>-?\d+)(?:\s+secs=(?P<secs>\d+))?\s*$")
LANDING_ROW_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} ")
# A register row: the time is xx:xx when it is not on record, so both halves take x. Two row
# shapes are both rows: "- YYYY-MM-DD HH:MM [scope]" as a list item, the shape written by hand,
# and the same row with no leading "- ", the shape a row-writing helper appends. A reader that
# knows only one of them silently drops every row of the other, newest included.
RULING_ROW_RE = re.compile(r"^(?:- )?\d{4}-\d{2}-\d{2} [0-9x]{2}:[0-9x]{2} \[")
# A lane merge on main, in the subjects scripts/train-wait.py reads: "Merge lane <token> (...) into
# <branch>", "Merge lane <token> into <branch>" and "merge(train): <sha> into <branch>, <token>: <topic>"
# ("<token> [skip ci]" with no colon before 2026-09-20). The last names the lane's commit first, so when the
# first word is a commit the word after the comma is the token.
LANE_MERGE_RE = re.compile(r"^(?:Merge lane|merge\(train\):) (\S+) (?:.*? )?into ([^\s,]+)(?:, ([\w.-]+)(?=[:\s]|$))?")
COMMIT_WORD_RE = re.compile(r"^[0-9a-f]{7,40}$")

STAMP_RE = re.compile(r"^\[(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})\]")
LOCK_TAKEN_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] LOCK taken")


def defaults_for(base):
    """The defaults of a config living in `base`. Nothing here names one machine."""
    return {
        "fixed_lines": [],
        "gates_dirs": [],
        "landings_file": os.path.join(base, "landings.md"),
        "rulings_file": os.path.join(base, "rulings.md"),
        "reports_file": os.path.join(base, "owner-reports.md"),
        "sessions_glob": os.path.join(base, "lanes", "*-session-*.md"),
        "devices": [],
        "project_repo": "",
        "lanes_glob": os.path.join(base, "lanes", "*.md"),
        "briefs_glob": os.path.join(base, "briefs", "*.md"),
        "reviews_glob": os.path.join(base, "reviews", "*.md"),
        "handback_log": os.path.join(base, "handback.log"),
        "brief_gen": "",
    }


# What a fresh install renders with: the sheet, the register and the two globs sit
# under the home dir, no gate folder is scanned and no repository is listed.
DEFAULTS = defaults_for(HOME_CLAUDE)


def posix(path):
    """Paths are printed with forward slashes, the way the operator writes them."""
    return str(path).replace("\\", "/")


def clip_text(text, limit):
    text = text.rstrip()
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."


def complain(path, reason):
    """One line on stderr. The sheet still renders, so this is the only place a
    config the tool could not use is reported."""
    print("lane-state: config %s ignored, %s" % (posix(path), reason), file=sys.stderr)


def config_file(explicit=None):
    return explicit or os.environ.get("CLAUDE_LANE_STATE_CONFIG") or DEFAULT_CONFIG


def sheet_file(explicit=None):
    return explicit or os.environ.get("CLAUDE_LANE_STATE_SHEET") or DEFAULT_SHEET


def load_config(path=None):
    """The config, with every absent key filled in beside the config file.

    A config that is not there is not an error: a fresh install renders the sheet with
    the home defaults and empty sections, which is what says the kit is installed and
    nothing is wired yet.

    A config that is there and cannot be used is not an error either, for the same
    reason a missing source is not: the recovery hook prints this sheet at every
    compaction and swallows a renderer that fails, so a trailing comma in the config
    would take the paragraph away without a word. What it cannot read (unreadable, not
    valid JSON, or valid JSON that is not an object) it names on stderr, once, and
    renders with the defaults instead.
    """
    path = config_file(path)
    base = os.path.dirname(os.path.abspath(path)) if os.path.isfile(path) else HOME_CLAUDE
    config = defaults_for(base)
    loaded = {}
    try:
        # utf-8-sig, not utf-8: PowerShell's Out-File and Set-Content -Encoding utf8
        # write a BOM, and json reads one as a character and rejects the whole file.
        # first_line() strips a BOM for the same reason; the two readers agree.
        with open(path, encoding="utf-8-sig") as handle:
            loaded = json.load(handle)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as error:  # JSONDecodeError is a ValueError
        complain(path, error)
    if not isinstance(loaded, dict):
        complain(path, "not a JSON object but a %s" % type(loaded).__name__)
        loaded = {}
    config.update(loaded)
    register = os.environ.get("CLAUDE_RULINGS_FILE")
    if register:
        config["rulings_file"] = register
    return config


# --- gates ------------------------------------------------------------------------

def lane_name_from_dir(name):
    """`pwpgate` gives `pwp`, `bf-gate` gives `bf`, `bud68` and `gate` stay as they are."""
    if name.endswith("gate") and len(name) > 4:
        stripped = name[:-4].rstrip("-_")
        if stripped:
            return stripped
    return name


def find_exit_files(dirs):
    """(lane, path) for every .exit file directly in the given dirs or one level below."""
    seen = set()
    found = []
    for folder in dirs:
        for pattern, depth in ((os.path.join(folder, "*.exit"), 0),
                               (os.path.join(folder, "*", "*.exit"), 1)):
            for path in glob.glob(pattern):
                key = os.path.normcase(os.path.realpath(path))  # a junction and its target are one folder
                if key in seen or not os.path.isfile(path):
                    continue
                seen.add(key)
                owner = os.path.dirname(path) if depth else folder
                found.append((lane_name_from_dir(os.path.basename(owner.rstrip("\\/"))), path))
    return found


def lock_hold_secs(exit_path):
    """Wall seconds the run held the gradle mutex.

    The gate runner stamps "LOCK taken" with [HH:MM:SS] but writes LOCK_RELEASED without a
    stamp, so the close of the hold is taken from the last timestamped line before
    LOCK_RELEASED. Returns None when the .log is missing or the pair is incomplete.
    """
    log_path = (exit_path[:-5] if exit_path.endswith(".exit") else exit_path) + ".log"
    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    taken = last = None
    released = False
    for line in lines:
        match = STAMP_RE.match(line)
        if match:
            stamp = (int(match.group("h")) * 3600 + int(match.group("m")) * 60
                     + int(match.group("s")))
            if taken is None and LOCK_TAKEN_RE.match(line):
                taken = stamp
            if taken is not None:
                last = stamp
        elif line.startswith("LOCK_RELEASED"):
            released = True
            break
    if taken is None or last is None or not released:
        return None
    hold = last - taken
    return hold + 86400 if hold < 0 else hold  # the stamps carry no date, so midnight wraps


def parse_exit_file(path):
    """The keys of an exit file, with the phases in the order the gate runner wrote them."""
    values = {}
    phases = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\r\n")
            match = PHASE_RE.match(line)
            if match:
                phases.append((match.group("name"),
                               int(match.group("code")),
                               int(match.group("secs") or 0)))
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                values[key] = value  # a repeated key keeps the last value written
    values["_phases"] = phases
    return values


def gate_row(lane, path, now):
    values = parse_exit_file(path)
    mtime = os.path.getmtime(path)
    phases = values["_phases"]
    exit_code = values.get("GATE_EXIT", "-")
    if exit_code == SENTINEL_EXIT:
        exit_code = "running" if now - mtime <= DEAD_AFTER_SECS else "dead"
    tip = values.get("TIP_END") or values.get("TIP_START") or ""
    failed = ",".join("%s:%d" % (name, code) for name, code, _ in phases if code != 0)
    return {
        "lane": lane,
        "run": values.get("RUN", os.path.basename(path)[:-5]),
        "mtime": mtime,
        "hhmm": datetime.fromtimestamp(mtime).strftime("%H:%M"),
        "tip7": tip[:7] if tip else "-",
        "exit": exit_code,
        "lock": values.get("LOCK_WAIT_SECS", "-") or "-",
        "moved": values.get("TIP_MOVED", "-") or "-",
        "failed": failed,
        "phases": len(phases),
        # The gate runner writes secs as the cumulative offset from the gate's launch
        # (START is taken before the lock wait), so the run's figure is the last offset,
        # never the sum of the per-phase lines.
        "secs": max([secs for _, _, secs in phases], default=0),
        "hold": lock_hold_secs(path),
    }


def format_gate_line(row):
    return "%s %s %s %s exit=%s lock=%s moved=%s %s phases=%d secs=%d hold=%s" % (
        row["lane"], row["run"], row["hhmm"], row["tip7"], row["exit"],
        row["lock"], row["moved"],
        ("failed=" + row["failed"]) if row["failed"] else "ok",
        row["phases"], row["secs"],
        "-" if row.get("hold") is None else row["hold"],
    )


def collect_gates(dirs, since_hours=RECENT_HOURS, lane=None, now=None):
    now = time.time() if now is None else now
    rows = []
    for found_lane, path in find_exit_files(dirs):
        if lane and found_lane.lower() != lane.lower():
            continue
        try:
            row = gate_row(found_lane, path, now)
        except OSError:
            continue
        if since_hours is not None and row["mtime"] < now - since_hours * 3600:
            continue
        rows.append(row)
    rows.sort(key=lambda r: (-r["mtime"], r["run"]))
    return rows


def gates_lines(dirs, since_hours=RECENT_HOURS, lane=None, now=None):
    return [format_gate_line(row) for row in collect_gates(dirs, since_hours, lane, now)]


def gate_dirs(config):
    """Only what the config names. Nothing is scanned that was never configured."""
    return [d for d in config.get("gates_dirs") or [] if d]


# --- other sources ----------------------------------------------------------------

def run_git(args, cwd=None):
    result = subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip().splitlines()[0]
                           if (result.stderr or result.stdout).strip() else "git failed")
    return result.stdout


def worktree_line(entry):
    path, branch, head = entry
    if not os.path.isdir(path):
        return "%s %s folder missing" % (posix(path), branch)
    try:
        subject = run_git(["-C", path, "log", "-1", "--format=%h|%s"]).strip()
        short, _, text = subject.partition("|")
        dirty = len([ln for ln in run_git(["-C", path, "status", "--porcelain"]).splitlines() if ln])
    except Exception as error:  # a single bad worktree never sinks the section
        return "%s %s %s read failed: %s" % (posix(path), branch, head[:7], error)
    return "%s %s %s dirty=%d %s" % (posix(path), branch, short, dirty, clip_text(text, SUBJECT_CLIP))


def worktrees(repo):
    if not repo:
        return []
    entries = worktree_entries(repo)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(worktree_line, entries))


def worktree_entries(repo):
    """(path, branch, head) for every worktree of the repository, as git lists them."""
    entries = []
    path = branch = head = ""
    for line in run_git(["-C", repo, "worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            path, branch, head = line[9:].strip(), "(detached)", ""
        elif line.startswith("HEAD "):
            head = line[5:].strip()
        elif line.startswith("branch "):
            branch = line[7:].strip().replace("refs/heads/", "")
        elif not line.strip() and path:
            entries.append((path, branch, head))
            path = ""
    if path:
        entries.append((path, branch, head))
    return entries


def last_landings(path, count=LANDINGS_ROWS, clip=LANDINGS_CLIP):
    """The last prose rows of the landings file, read from the tail, hook rows dropped."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        if size > LANDINGS_TAIL_BYTES:
            handle.seek(size - LANDINGS_TAIL_BYTES)
            handle.readline()  # the first line of the window is usually a partial one
        text = handle.read().decode("utf-8", "replace")
    rows = [clip_text(line, clip) for line in text.splitlines() if LANDING_ROW_RE.match(line)]
    return rows[-count:]


def main_merges(repo, hours=RECENT_HOURS, clip=MERGES_CLIP):
    """The merges main took in the window, from git, newest first: one line per train (the
    lane merges naming the same "into <branch>" target, lanes in merge order) and one line
    per other merge. There is no row cap: a cap of eight hid most of a busy day's merges.
    A landings file kept by hand reads "none" on a day that landed trains once landings stop
    being written there, and git is the record of what landed. No repository configured
    means no line; a git failure is one line and never sinks the sheet.
    """
    if not repo:
        return []
    try:
        out = run_git(["-C", repo, "log", "--merges", "--since=%d hours ago" % int(hours),
                       "--format=%h|%ci|%s", "main"])
    except Exception as error:  # a git failure never sinks the sheet
        return ["git log failed: %s" % error]
    rows, trains = [], {}
    for line in out.splitlines():  # newest first
        if not line.strip():
            continue
        sha, when, subject = line.split("|", 2)
        match = LANE_MERGE_RE.match(subject)
        if not match:
            rows.append((when, clip_text("%s %s %s" % (sha, when[:16], subject), clip)))
            continue
        first = match.group(1)
        lane = match.group(3) if match.group(3) and COMMIT_WORD_RE.match(first) else first
        target = match.group(2)
        train = trains.setdefault(target, {"lanes": [], "last": sha, "when": when})
        train["lanes"].append(lane)
        train["first"] = sha
    for target, train in trains.items():
        rows.append((train["when"], clip_text("%s %s %d lanes %s..%s: %s" % (
            target, train["when"][:16], len(train["lanes"]), train["first"], train["last"],
            " ".join(reversed(train["lanes"]))), clip)))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in rows]


def ruling_key(row):
    """The sort key of a register row: its date, then its hour as written.

    The hour is compared as text, which is what the x of an unrecorded minute needs:
    x sorts after every real digit of its position, so 12:1x lands after 12:19 and
    before 12:20, and xx:xx sits at the end of its day, which is all that is known
    about it. The row shape is fixed width once the optional leading "- " is set aside,
    so the two slices are exact for both shapes.
    """
    bare = row[2:] if row.startswith("- ") else row
    return (bare[0:10], bare[11:16])


def rulings_rows(path, count=RULINGS_ROWS):
    """The last rows of the rulings register, whole and oldest first by stamp.

    The register is append-only by rule, so a ruling made at 11:47 and written at
    12:30 sits below a 12:2x row while carrying the older stamp: file order is not
    time order. The rows are sorted by their stamp before the cut, so what a cut
    gives up is the oldest ruling and never the newest. The sort is stable, so rows
    that carry the same stamp keep the order the file wrote them in.

    A line that does not carry the row shape (the header, a blank line, a note) is
    ignored, and a register that is missing or unreadable gives one row saying so:
    this section is printed at every compaction recovery and must never raise.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            rows = [clip_text(line, RULINGS_CLIP) for line in handle
                    if RULING_ROW_RE.match(line)]
    except OSError:
        return ["no rulings file at %s" % posix(path)]
    return sorted(rows, key=ruling_key)[-count:]


def first_line(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.readline().lstrip("﻿").strip()


def recent_files(specs, hours=RECENT_HOURS, now=None):
    """One row per file touched in the window, all specs merged and newest first.

    A spec is (glob pattern, label, whether to append the file's first line). Merging
    before the sort keeps the newest of both groups when the sheet has to be cut.
    """
    now = time.time() if now is None else now
    rows = []
    for pattern, label, with_first_line in specs:
        for path in glob.glob(pattern):
            try:
                mtime = os.path.getmtime(path)
                size = os.path.getsize(path)
            except OSError:
                continue
            if mtime < now - hours * 3600:
                continue
            row = "%s/%s %s %.1f KB" % (
                label, os.path.basename(path),
                datetime.fromtimestamp(mtime).strftime("%H:%M"), size / 1024.0,
            )
            if with_first_line:
                try:
                    row += " " + clip_text(first_line(path), FIRST_LINE_CLIP)
                except OSError:
                    pass
            rows.append((mtime, row))
    rows.sort(key=lambda item: (-item[0], item[1]))
    return [row for _, row in rows]


# --- the owner reports ledger ------------------------------------------------------

def load_reports_check():
    """The reports-check module beside this file, or None and the reason it did not load.

    It is imported by path, because the file name carries a hyphen, and imported rather
    than run as a subprocess: the sheet is rendered on every compaction recovery and a
    process start per render is a cost with no return. It owns the ledger parser, the
    count and the flags; this file keeps none of its own, so the two can never drift.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports-check.py")
    try:
        spec = importlib.util.spec_from_file_location("reports_check", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, None
    except Exception as error:
        return None, "%s: %s" % (posix(path), error.__class__.__name__)


def reports_heading(config):
    """The heading names the file the config points at, not one board's file name."""
    name = os.path.basename(config.get("reports_file") or "")
    return "## Owner reports not VERIFIED (%s)" % (name or "no reports file")


def owner_reports(config, now=None):
    """Every report that is not VERIFIED yet, the recent VERIFIED count, then the flags.

    This is the section that answers "where does my report stand" after a compaction, so
    it fails loudly and never raises: no key and a file that is not there give the same
    single line, and a checker that cannot be loaded gives one line naming why, rather
    than half a section rendered by a second parser that nobody keeps in step.
    """
    path = config.get("reports_file")
    if not path:
        return ["no reports file configured"]
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return ["no reports file configured"]

    module, reason = load_reports_check()
    if module is None:
        return ["reports-check unavailable: " + reason]

    rows = [cells for _, cells in module.ledger_rows(text) if cells]
    if not rows:
        return []

    lines = []
    for cells in rows:
        status = cells[module.STATUS]
        upper = status.upper()
        if upper.startswith("OPEN") or upper.startswith("LANDED"):
            lines.append(clip_text(" | ".join([
                cells[module.ID], status,
                clip_text(cells[module.WORDS], REPORT_WORDS_CLIP),
                cells[module.LANE], cells[module.LANDED], cells[module.VALIDATION],
            ]), REPORTS_CLIP))
    if not lines:
        lines.append("no open owner report")
    lines.append("VERIFIED in the last 48 h: %d" % module.verified_recently(text, now))

    try:
        with open(config["landings_file"], encoding="utf-8", errors="replace") as handle:
            landings_text = handle.read()
    except (OSError, KeyError):
        landings_text = ""
    try:
        sessions = module.session_files_of(config.get("sessions_glob") or "")
        for flag in module.check(text, landings_text, sessions, now,
                                 devices=config.get("devices")):
            lines.append("check: " + flag)
    except Exception as error:
        lines.append("check: reports-check failed: "
                     + posix(str(error).strip() or error.__class__.__name__))
    return lines


# --- the sheet --------------------------------------------------------------------

def section(heading, builder, cuttable=True, empty="none"):
    try:
        lines = builder()
    except Exception as error:
        text = str(error).strip() or error.__class__.__name__
        return {"heading": heading, "lines": ["section unavailable: " + posix(text)],
                "cuttable": False, "cut": 0}
    if not lines:
        lines = [empty]
    return {"heading": heading, "lines": list(lines), "cuttable": cuttable, "cut": 0}


def section_len(sect):
    return len(sect["lines"]) + (1 if sect["cut"] else 0)


def apply_max_lines(sections, max_lines, title_lines=1):
    """Cut from the bottom of the longest cuttable section. Fixed lines and gates stay."""
    def total():
        return title_lines + sum(1 + section_len(sect) for sect in sections)

    guard = 0
    while total() > max_lines and guard < 10000:
        guard += 1
        candidates = [s for s in sections if s["cuttable"] and s["lines"]]
        if not candidates:
            break
        target = max(candidates, key=section_len)
        target["lines"].pop()
        target["cut"] += 1
    return sections


LANDINGS_HEADING = ("## Last landings (every merge on main in the last %d h from git, one line "
                    "per train, then the landings file's prose rows)" % RECENT_HOURS)


def build_sections(config, now=None):
    dirs = gate_dirs(config)
    return [
        section("## Fixed lines",
                lambda: list(config.get("fixed_lines") or []),
                cuttable=False, empty="no fixed lines configured"),
        section("## Worktrees",
                lambda: worktrees(config.get("project_repo") or ""),
                empty="no repository configured"),
        section("## Rulings (last %d of %s, append there in the same turn the "
                "owner rules)" % (RULINGS_ROWS, posix(config["rulings_file"])),
                lambda: rulings_rows(config["rulings_file"]),
                cuttable=False, empty="no rulings yet"),
        section(reports_heading(config),
                lambda: owner_reports(config, now),
                cuttable=False, empty="no open owner report"),
        section("## Gates (last 24 h)",
                lambda: gates_lines(dirs, RECENT_HOURS, None, now),
                cuttable=False, empty="no gate exit file in the last 24 h"),
        section(LANDINGS_HEADING,
                lambda: main_merges(config.get("project_repo") or "")
                + last_landings(config["landings_file"])),
        section("## Lane reports and briefs (last 24 h)",
                lambda: recent_files([(config["lanes_glob"], "lanes", True),
                                      (config["briefs_glob"], "briefs", False)], now=now)),
    ]


def render_law(config, max_lines=MAX_LINES, now=None):
    stamp = datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y-%m-%d %H:%M")
    title = ("# State sheet, rendered %s by lane-state.py; sources on disk, no model" % stamp)
    sections = apply_max_lines(build_sections(config, now), max_lines)
    out = [title]
    for sect in sections:
        out.append(sect["heading"])
        out.extend(sect["lines"])
        if sect["cut"]:
            out.append("[%d lines cut]" % sect["cut"])
    return "\n".join(out) + "\n"


def write_atomic(path, text):
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=folder, prefix=".law-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


# --- command line -----------------------------------------------------------------
# --- lane hand-back ---------------------------------------------------------------
# One block per finished agent, in place of the seat reading the report, the reviews, the gate
# files and git by hand. It adds two checks no other script makes: the report names the tip its
# worktree holds, and other recent reports name a file the lane's diff touches, which is how two
# lanes find out they changed the same file before a train does.

TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
HANDBACK_MAX_LINES = 40
HANDBACK_CLIP = 160
OVERLAP_DAYS = 7
OVERLAP_SHOWN = 5
GENERIC_SHARE = 0.25  # a touched name found in more of the recent files than this says nothing,
GENERIC_MIN = 5  # and in more than this many of them, so a handful of reports never mutes a name
READ_LIMIT = 256 * 1024
OPEN_ITEMS_RE = re.compile(r"^#+\s*open items", re.IGNORECASE)


def token_files(pattern, token):
    """The files of the glob named <token>-*, newest first: `crk1` never takes `crk1x-...`."""
    prefix = token.lower() + "-"
    paths = [p for p in glob.glob(pattern) if os.path.basename(p).lower().startswith(prefix)]
    return sorted(paths, key=lambda p: (os.path.getmtime(p), p), reverse=True)


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read(READ_LIMIT)


def open_items(text):
    """The bullets under a report's Open items heading, up to the next heading."""
    items, inside_section = [], False
    for line in text.splitlines():
        if OPEN_ITEMS_RE.match(line):
            inside_section = True
            continue
        if inside_section and line.startswith("#"):
            break
        if inside_section and re.match(r"^\s*(?:[-*]|\d+[.)])\s+\S", line):
            items.append(re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", line))
    return items


def load_disposition(config):
    """brief-gen.py's reader of a review's verdict, so the two never disagree."""
    path = config.get("brief_gen") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "brief-gen.py")
    spec = importlib.util.spec_from_file_location("brief_gen_for_lane", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.disposition


def lane_worktree(repo, token):
    """(path, branch, head) of the worktree named <repo>-<token>, or None."""
    want = (os.path.basename(repo.rstrip("\\/")) + "-" + token).lower()
    for path, branch, head in worktree_entries(repo):
        if os.path.basename(path.rstrip("\\/")).lower() == want:
            return path, branch, head
    return None


def touched_names(path, base="main"):
    """The base names of the files the lane's branch changes against its merge base with main."""
    out = run_git(["-C", path, "diff", "--name-only", base + "...HEAD"])
    return sorted({os.path.basename(ln.strip()) for ln in out.splitlines() if ln.strip()})


def overlaps(names, patterns, token, now, days=OVERLAP_DAYS):
    """(docs naming a specific touched file, the names too common to say anything, docs read)."""
    prefix = token.lower() + "-"
    docs = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            if os.path.basename(path).lower().startswith(prefix):
                continue
            try:
                if os.path.getmtime(path) < now - days * 86400:
                    continue
                docs.append((path, read_text(path)))
            except OSError:
                continue
    hits = {name: [p for p, text in docs if name in text] for name in names}
    generic = sorted(n for n, found in hits.items() if len(found) > max(GENERIC_SHARE * len(docs), GENERIC_MIN))
    found = {}
    for name, paths in hits.items():
        if name in generic:
            continue
        for p in paths:
            found.setdefault(p, name)
    ordered = sorted(found.items(), key=lambda item: os.path.getmtime(item[0]), reverse=True)
    return ordered, generic, len(docs)


def when_text(mtime, now):
    """HH:MM for today, MM-DD HH:MM for an older day, so a stale report never reads as this morning's."""
    stamp = datetime.fromtimestamp(mtime)
    same = stamp.date() == datetime.fromtimestamp(now).date()
    return stamp.strftime("%H:%M" if same else "%m-%d %H:%M")


def rel_to(path, root):
    try:
        return posix(os.path.relpath(path, root))
    except ValueError:  # another drive
        return posix(path)


def handback_lines(config, token, now=None, dirs=None):
    """The block and the log fields (tip check state, overlap count). Every source that fails prints
    one line saying so; the block never raises for a missing file or a git failure."""
    now = time.time() if now is None else now
    root = os.path.dirname(os.path.dirname(os.path.abspath(config["lanes_glob"])))
    stamp = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
    lines = ["lane %s %s" % (token, stamp)]
    reports = token_files(config["lanes_glob"], token)
    text = ""
    if reports:
        report = reports[0]
        try:
            text = read_text(report)
            first = next((ln for ln in text.splitlines() if ln.strip()), "(empty)")
            lines.append("report %s %s | %s" % (
                rel_to(report, root), when_text(os.path.getmtime(report), now), clip_text(first, FIRST_LINE_CLIP)))
        except OSError as error:
            lines.append("report %s unreadable: %s" % (rel_to(report, root), error))
        items = open_items(text)
        lines.append("open items %d" % len(items))
        lines += ["  - " + clip_text(item, HANDBACK_CLIP) for item in items[:3]]
        if len(reports) > 1:
            lines.append("older reports: " + ", ".join(os.path.basename(p) for p in reports[1:4]))
    else:
        lines.append("report none: no %s-* file under %s" % (token, posix(os.path.dirname(config["lanes_glob"]))))

    s3 = "no-report" if not reports else "no-worktree"
    names = []
    repo = config.get("project_repo") or ""
    entry = None
    if not repo:
        lines.append("worktree none: no project_repo configured; s3=%s" % s3)
    else:
        try:
            entry = lane_worktree(repo, token)
        except Exception as error:
            lines.append("worktree read failed: %s" % error)
        if entry:
            path, branch, head = entry
            if reports:
                s3 = "ok" if head and head[:7] in text else "mismatch"
            lines.append("worktree %s branch %s head %s; s3=%s%s" % (
                posix(path), branch, head[:9] if head else "-", s3,
                "" if s3 != "mismatch" else " (the report does not name %s)" % head[:9]))
            try:
                names = touched_names(path)
            except Exception as error:
                lines.append("diff against main failed: %s" % error)
        elif not any(ln.startswith("worktree read failed") for ln in lines):
            lines.append("worktree none named %s-%s; s3=%s" % (os.path.basename(repo.rstrip("\\/")), token, s3))

    reviews = token_files(config["reviews_glob"], token)
    if reviews:
        try:
            verdict = load_disposition(config)
        except Exception as error:
            verdict = None
            lines.append("disposition reader failed: %s" % error)
        shown = []
        for path in reviews[:3]:
            try:
                what = verdict(path) if verdict else "?"
            except OSError as error:
                what = "unreadable: %s" % error
            shown.append("%s %s %s" % (os.path.basename(path), when_text(os.path.getmtime(path), now), what))
        lines.append("reviews: " + "; ".join(shown))
    else:
        lines.append("reviews none")

    gate_rows = [format_gate_line(row) for row in collect_gates(
        dirs if dirs is not None else gate_dirs(config), 72, token, now)]
    lines.append("gates %d in 72 h%s" % (len(gate_rows), ":" if gate_rows else ""))
    lines += ["  " + row for row in gate_rows[:3]]

    count = None  # the log says "-" when no diff ran, never a false zero
    if names:
        found, generic, read = overlaps(names, [config["lanes_glob"], config["reviews_glob"]], token, now)
        count = len(found)
        lines.append("touched %d files; overlaps %d in %d reports of %d days%s" % (
            len(names), count, read, OVERLAP_DAYS,
            "" if not generic else "; too common to count: " + ", ".join(generic[:5])))
        lines += ["  %s (%s)" % (rel_to(p, root), name) for p, name in found[:OVERLAP_SHOWN]]
    if len(lines) > HANDBACK_MAX_LINES:
        cut = len(lines) - HANDBACK_MAX_LINES + 1
        lines = lines[:HANDBACK_MAX_LINES - 1] + ["... %d lines cut" % cut]
    return lines, s3, count


def append_handback(path, token, s3, count, now=None):
    """One line per call: date, time, lane, s3=, overlaps= (a number, or - when no diff ran)."""
    now = time.time() if now is None else now
    line = "%s %s s3=%s overlaps=%s\n" % (datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
                                          token, s3, "-" if count is None else count)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None,
                        help="config file, default CLAUDE_LANE_STATE_CONFIG then "
                             "~/.claude/lane-state.json")
    subs = parser.add_subparsers(dest="command", required=True)

    gates = subs.add_parser("gates", help="one line per gate exit file, newest first")
    gates.add_argument("--lane")
    gates.add_argument("--since", type=float, default=float(RECENT_HOURS), metavar="HOURS")
    gates.add_argument("--all", action="store_true", help="no time filter")
    gates.add_argument("--gates-dir", action="append", default=[], metavar="DIR")

    law = subs.add_parser("law", help="render the state sheet")
    law.add_argument("--out", help="sheet path, default CLAUDE_LANE_STATE_SHEET then "
                                   "~/.claude/law.md")
    law.add_argument("--max-lines", type=int, default=MAX_LINES)
    law.add_argument("--gates-dir", action="append", default=[], metavar="DIR")

    lane = subs.add_parser("lane", help="one hand-back block for a lane token")
    lane.add_argument("token")
    lane.add_argument("--gates-dir", action="append", default=[], metavar="DIR")
    lane.add_argument("--no-log", action="store_true", help="print the block, append nothing")

    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.gates_dir:
        config["gates_dirs"] = args.gates_dir

    if args.command == "gates":
        for line in gates_lines(gate_dirs(config), None if args.all else args.since, args.lane):
            print(line)
        return 0

    if args.command == "lane":
        token = (args.token or "").strip().lower()
        if not TOKEN_RE.match(token):
            print("REFUSED lane token %r: 1 to 24 of a-z, 0-9 and -, starting with a letter or digit" % args.token)
            return 2
        lines, s3, count = handback_lines(config, token, dirs=gate_dirs(config))
        for line in lines:
            print(line)
        if not args.no_log:
            try:
                append_handback(config["handback_log"], token, s3, count)
            except OSError as error:
                print("handback log not written: %s" % error)
                return 1
        return 0

    target = sheet_file(args.out)
    text = render_law(config, max_lines=args.max_lines)
    write_atomic(target, text)
    print("wrote %s (%d lines, %d bytes)"
          % (posix(target), len(text.splitlines()), len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
