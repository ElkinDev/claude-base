"""Renders a state sheet of the lanes from what is already on disk. No model runs.

Two subcommands:

  gates   one line per gate exit file, newest first, default the last 24 hours.
  law     the whole sheet (fixed lines, worktrees, rulings, gates, landings,
          lane reports and briefs) written atomically to the sheet path.

Sources are the gate exit files a gate runner writes, read-only git in the worktrees of
the configured repository, the tail of the landings file, the last rows of the rulings
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
lanes_glob, briefs_glob.

Env seams, each of which wins over the default and is what the recovery hook wires:
CLAUDE_LANE_STATE_CONFIG (the config file), CLAUDE_LANE_STATE_SHEET (the sheet written
when no --out is given), CLAUDE_RULINGS_FILE (the register, over the configured one).
"""
import argparse
import concurrent.futures
import glob
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

PHASE_RE = re.compile(r"^PHASE_(?P<name>.+?)_EXIT=(?P<code>-?\d+)(?:\s+secs=(?P<secs>\d+))?\s*$")
LANDING_ROW_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} ")
# A register row: the time is xx:xx when it is not on record, so both halves take x.
RULING_ROW_RE = re.compile(r"^- \d{4}-\d{2}-\d{2} [0-9x]{2}:[0-9x]{2} \[")


def defaults_for(base):
    """The defaults of a config living in `base`. Nothing here names one machine."""
    return {
        "fixed_lines": [],
        "gates_dirs": [],
        "landings_file": os.path.join(base, "landings.md"),
        "rulings_file": os.path.join(base, "rulings.md"),
        "project_repo": "",
        "lanes_glob": os.path.join(base, "lanes", "*.md"),
        "briefs_glob": os.path.join(base, "briefs", "*.md"),
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
        with open(path, encoding="utf-8") as handle:
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
                key = os.path.normcase(os.path.abspath(path))
                if key in seen or not os.path.isfile(path):
                    continue
                seen.add(key)
                owner = os.path.dirname(path) if depth else folder
                found.append((lane_name_from_dir(os.path.basename(owner.rstrip("\\/"))), path))
    return found


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
        "secs": sum(secs for _, _, secs in phases),
    }


def format_gate_line(row):
    return "%s %s %s %s exit=%s lock=%s moved=%s %s phases=%d secs=%d" % (
        row["lane"], row["run"], row["hhmm"], row["tip7"], row["exit"],
        row["lock"], row["moved"],
        ("failed=" + row["failed"]) if row["failed"] else "ok",
        row["phases"], row["secs"],
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(worktree_line, entries))


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


def ruling_key(row):
    """The sort key of a register row: its date, then its hour as written.

    The hour is compared as text, which is what the x of an unrecorded minute needs:
    x sorts after every real digit of its position, so 12:1x lands after 12:19 and
    before 12:20, and xx:xx sits at the end of its day, which is all that is known
    about it. The row shape is fixed width, so the two slices are exact.
    """
    return (row[2:12], row[13:18])


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
        section("## Gates (last 24 h)",
                lambda: gates_lines(dirs, RECENT_HOURS, None, now),
                cuttable=False, empty="no gate exit file in the last 24 h"),
        section("## Last landings",
                lambda: last_landings(config["landings_file"])),
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

    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.gates_dir:
        config["gates_dirs"] = args.gates_dir

    if args.command == "gates":
        for line in gates_lines(gate_dirs(config), None if args.all else args.since, args.lane):
            print(line)
        return 0

    target = sheet_file(args.out)
    text = render_law(config, max_lines=args.max_lines)
    write_atomic(target, text)
    print("wrote %s (%d lines, %d bytes)"
          % (posix(target), len(text.splitlines()), len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
