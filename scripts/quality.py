"""Three quality numbers per ledger window, so an optimization cannot pass as a win.

The twice-daily ledger measures spend. A change that shortens lanes or loosens reviews
would read as a win there and nowhere else, so this module computes what a shortcut
costs, over the same window the ledger uses:

  1. Red gates. One row per gate exit file, read through lane-state.py. A gate counts in
     the window by the mtime of its exit file. Green is a last `GATE_EXIT` of 0, red is
     1 to 96, 98 or 99, aborted is 143 or a sentinel still running or gone dead. The
     numbers are red / (red + green), the red count by cause taken from the phases that
     failed, and gate files of the window over landings of the window.
  2. Review blocks. The prose rows of landings.md inside the window whose head carries
     the word REVIEW. The head ends at the first colon, or at the first period that is
     followed by whitespace or by the end of the line, never at a period inside a lane
     id such as `invqr (90.1)`. With BLOCK it is a block, with CLEAR a clear. A row
     whose first 80 characters carry REVIEW but whose head carries neither verdict is
     unclassified: counted apart, listed, and printed as its own column, so a verdict
     this reader cannot place is never silently dropped toward zero.
  3. Post-landing defects. The rows of <ledger>/defects.md inside the window (declared),
     and the landings of the window whose tip subject starts with `fix` (a rework
     proxy). An owner item, a spec or a mockup is not a defect: only something that
     landed and then failed a check is.

Counts alone cannot be compared week over week, because two weeks rarely measure the
same amount of time: a skipped run leaves its hours unmeasured forever. So every row
carries its window as full dates, the head unions those windows into a coverage line per
bucket, and every count that moves with coverage prints its rate next to it.

Paths. The repository whose landings count is `repo` of ledger-config.json, read through
ledger-day.py beside this file. The landings file is <evidence>/landings.md, where the
evidence root is EVIDENCE_ROOT, else the `evidence` folder beside the repository. The
declared defects are <ledger>/defects.md, the ledger folder ledger-day.py writes. The gate
exit files are read through the installed lane-state tool (QUALITY_LANE_STATE, else
<KIT_HOME>/tools/lane-state.py with KIT_HOME defaulting to ~/.claude, else the kit's own
claude/tools/lane-state.py), in the `gates_dirs` of its config. Every flag overrides.

Every source that is missing or unreadable reports a field `unavailable` with the
reason. Nothing here raises on a missing source, and nothing here writes to the live
ledger folder: `append_quality` writes only the path it is given.

Window arithmetic: `parse_when` below is a copy of the rule in ledger-day.py (`24h`,
`3d` or a local `YYYY-MM-DD HH:MM`), copied rather than imported so this module stays
standalone and does not load the 60 KB ledger script to read a time.

Command line:

  python quality.py --since 24h [--until '2026-09-06 10:00'] [--landings PATH]
                    [--defects PATH] [--gate-dir DIR] [--repo DIR] [--no-git]

prints the dict as JSON. Landings come from the reflog of main through ledger-day.py
unless --no-git is given.
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT_HOME = os.environ.get("KIT_HOME") or os.path.join(os.path.expanduser("~"), ".claude")
LEDGER_DAY = os.path.join(HERE, "ledger-day.py")


def lane_state_path():
    """The lane-state tool this machine runs: QUALITY_LANE_STATE, the installed copy, else the kit's own."""
    for path in (os.environ.get("QUALITY_LANE_STATE"), os.path.join(KIT_HOME, "tools", "lane-state.py"),
                 os.path.join(os.path.dirname(HERE), "claude", "tools", "lane-state.py")):
        if path and os.path.isfile(path):
            return path
    return None

ROW_RE = re.compile(r"^(?:- )?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) ")
HEAD_END_RE = re.compile(r":|\.(?=\s|$)")
WINDOW_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) to "
                       r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
HEAD_CLIP = 80
COVERAGE_GAP_PCT = 20
CAUSES = ("compile", "ktlint", "detekt", "tests", "other")
RED_CODES = set(range(1, 97)) | {98, 99}
ABORT_CODES = {143}

QUALITY_COLUMNS = ("Time", "Window", "Landings", "Gates red/total", "Red by cause",
                   "Gates per landing", "Review block/total", "Review unclassified",
                   "Defects declared", "Fix landings/landings")
QUALITY_HEADER = "| " + " | ".join(QUALITY_COLUMNS) + " |"
QUALITY_SEP = "| " + " | ".join("---" for _ in QUALITY_COLUMNS) + " |"
QUALITY_TITLE = "# Quality ledger"
HEAD_TITLE = "## Last 7 days against the 7 before"
TABLE_TITLE = "## Quality per run, 08:00 and 18:00"
QUALITY_INTRO = (
    "One row per ledger run, next to the spend numbers of `ledger.md`. An optimization "
    "holds only if the cost per landing falls and none of these numbers rises, read "
    "week over week, never day over day. Red gates are `red / (red + green)` of the "
    "gate exit files of the window, gates per landing counts every gate file including "
    "the aborted ones and keeps the file count even when the window landed nothing, "
    "review blocks come from the REVIEW rows of `landings.md`, review unclassified "
    "counts the rows that carry REVIEW without a verdict this reader can place, "
    "declared defects from `defects.md` and fix landings from the tip subjects. The "
    "window is written as full dates so the head can union the time each week "
    "actually measured. The head table is re-rendered from the rows below on every run."
)


# --------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------
def parse_when(text, now):
    """Parse `24h`, `3d` or a local `YYYY-MM-DD HH:MM` into a naive local time.

    Copied from ledger-day.py so the two scripts cut the same window.
    """
    if not text:
        return None
    m = re.fullmatch(r"(\d+)([dh])", text.strip())
    if m:
        n = int(m.group(1))
        delta = dt.timedelta(hours=n) if m.group(2) == "h" else dt.timedelta(days=n)
        return now - delta
    s = text.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise SystemExit("cannot parse a time from %r" % text)


def load_module(path, name):
    """Load a sibling script by path. Their names carry a dash, so import cannot."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("no loader for %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def share(num, den, digits=4):
    return round(float(num) / den, digits) if den else None


def pct_cell(num, den):
    if not den:
        return "-"
    return "%d/%d (%.1f%%)" % (num, den, 100.0 * num / den)


def ratio_cell(num, den):
    """A ratio cell that never loses its numerator: a window with gate files and no
    landing prints `52/0 (-)`, so the weekly sum still sees the 52 files."""
    if not den:
        return "%d/0 (-)" % num
    return "%d/%d (%.2f)" % (num, den, float(num) / den)


# --------------------------------------------------------------------------
# 1. gates
# --------------------------------------------------------------------------
def cause_of(phase):
    """The bucket a failed phase belongs to. Names come from lane-gate.sh."""
    low = phase.lower()
    if "compile" in low:
        return "compile"
    if "ktlint" in low:
        return "ktlint"
    if "detekt" in low:
        return "detekt"
    if low.startswith("test_") or "app" in low:
        return "tests"
    return "other"


def failed_phases(failed):
    """The phase names of a `failed=name:code,name:code` cell, in the order written."""
    names = []
    for part in (failed or "").split(","):
        part = part.strip()
        if not part:
            continue
        names.append(part.rsplit(":", 1)[0])
    return names


def class_of(exit_code):
    if exit_code in ("running", "dead"):
        return "aborted"
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        return "unknown"
    if code == 0:
        return "green"
    if code in ABORT_CODES:
        return "aborted"
    if code in RED_CODES:
        return "red"
    return "unknown"


def gate_numbers(start, end, gate_dirs, now, landings):
    out = {"total": 0, "green": 0, "red": 0, "aborted": 0, "unknown": 0,
           "red_share": None, "per_landing": None,
           "by_cause": {name: 0 for name in CAUSES}, "rows": []}
    path = lane_state_path()
    if path is None:
        out["unavailable"] = ("lane-state.py not found (QUALITY_LANE_STATE, %s/tools, the kit's claude/tools)"
                              % KIT_HOME)
        return out
    try:
        lane_state = load_module(path, "lane_state")
    except (ImportError, OSError, SyntaxError) as exc:
        out["unavailable"] = "lane-state.py did not load: %s" % exc
        return out
    if gate_dirs is not None:
        dirs = list(gate_dirs)
    elif hasattr(lane_state, "gate_dirs") and hasattr(lane_state, "load_config"):
        dirs = lane_state.gate_dirs(lane_state.load_config())
    else:
        dirs = lane_state.default_gate_dirs()
    existing = [d for d in dirs if os.path.isdir(d)]
    if not existing:
        out["unavailable"] = ("no gate directory exists (%d looked at)" % len(dirs))
        return out
    try:
        rows = lane_state.collect_gates(existing, since_hours=None, now=now.timestamp())
    except OSError as exc:
        out["unavailable"] = "gate files unreadable: %s" % exc
        return out
    for row in rows:
        when = dt.datetime.fromtimestamp(row["mtime"])
        if not (start <= when <= end):
            continue
        kind = class_of(row["exit"])
        out["total"] += 1
        out[kind] += 1
        cause = ""
        if kind == "red":
            phases = failed_phases(row["failed"])
            cause = cause_of(phases[0]) if phases else "other"
            out["by_cause"][cause] += 1
        out["rows"].append("%s class=%s%s"
                           % (lane_state.format_gate_line(row), kind,
                              (" cause=" + cause) if cause else ""))
    out["red_share"] = share(out["red"], out["red"] + out["green"])
    out["per_landing"] = share(out["total"], landings, 2)
    return out


# --------------------------------------------------------------------------
# 2. reviews
# --------------------------------------------------------------------------
def head_of(rest):
    """The head of a landings row: everything before the first colon, or before the
    first period that ends a sentence (a period followed by whitespace or by the end
    of the line). A period inside a lane id such as `invqr (90.1)` is not a break, so
    a verdict written after the lane id is still read.
    """
    m = HEAD_END_RE.search(rest)
    return rest[:m.start()] if m else rest


def review_numbers(start, end, landings_path):
    out = {"block": 0, "clear": 0, "block_share": None, "heads": [], "unclassified": []}
    if not (landings_path and os.path.isfile(landings_path)):
        out["unavailable"] = "no landings file at %s" % landings_path
        return out
    try:
        handle = open(landings_path, encoding="utf-8", errors="replace")
    except OSError as exc:
        out["unavailable"] = "landings file unreadable: %s" % exc
        return out
    with handle:
        for line in handle:
            row = line.rstrip("\r\n")
            if row.startswith("| "):  # a hook row, never a review
                continue
            m = ROW_RE.match(row)
            if not m:
                continue
            try:
                when = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if not (start <= when <= end):
                continue
            head = head_of(row[m.end():])
            clipped = row[:HEAD_CLIP]
            if "REVIEW" in head and "BLOCK" in head:
                out["block"] += 1
                out["heads"].append(clipped)
            elif "REVIEW" in head and "CLEAR" in head:
                out["clear"] += 1
                out["heads"].append(clipped)
            elif "REVIEW" in clipped:
                out["unclassified"].append(clipped)
    out["block_share"] = share(out["block"], out["block"] + out["clear"])
    return out


# --------------------------------------------------------------------------
# 3. defects
# --------------------------------------------------------------------------
def defect_numbers(start, end, defects_path, features, landings):
    out = {"declared": 0, "rows": [], "fix_landings": 0, "fix_share": None,
           "fix_subjects": []}
    for entry in (features or []):
        subject = (entry.get("subject") or "").strip()
        if subject.lower().startswith("fix"):
            out["fix_landings"] += 1
            out["fix_subjects"].append(("%s %s" % (entry.get("sha", "-"),
                                                   subject)).strip())
    out["fix_share"] = share(out["fix_landings"], landings)
    if not (defects_path and os.path.isfile(defects_path)):
        out["unavailable"] = "no defects file at %s" % defects_path
        return out
    try:
        handle = open(defects_path, encoding="utf-8", errors="replace")
    except OSError as exc:
        out["unavailable"] = "defects file unreadable: %s" % exc
        return out
    with handle:
        for line in handle:
            row = line.rstrip("\r\n")
            m = ROW_RE.match(row)
            if not m:
                continue
            try:
                when = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if start <= when <= end:
                out["rows"].append(row.strip())
    out["declared"] = len(out["rows"])
    return out


def compute(start, end, landings_path, defects_path, features,
            gate_dirs=None, now=None):
    """The three numbers and their audit lists for the window [start, end]."""
    now = dt.datetime.now() if now is None else now
    entries = list(features or [])
    landings = len(entries)
    return {
        "window": {"start": start.isoformat(sep=" "), "end": end.isoformat(sep=" ")},
        "landings": landings,
        "gates": gate_numbers(start, end, gate_dirs, now, landings),
        "review": review_numbers(start, end, landings_path),
        "defects": defect_numbers(start, end, defects_path, entries, landings),
    }


# --------------------------------------------------------------------------
# The quality.md table
# --------------------------------------------------------------------------
def cause_cell(by_cause):
    parts = ["%s %d" % (name, by_cause.get(name, 0))
             for name in CAUSES if by_cause.get(name)]
    return ", ".join(parts) if parts else "-"


def format_row(now, start, end, rep):
    """One row of quality.md. Cells stay parseable: every ratio prints `num/den` first,
    and the window carries full dates so the head can union the coverage of a bucket."""
    gates, review, defects = rep["gates"], rep["review"], rep["defects"]
    landings = rep["landings"]
    window = "%s to %s" % (start.strftime("%Y-%m-%d %H:%M"),
                           end.strftime("%Y-%m-%d %H:%M"))
    cells = [
        now.strftime("%Y-%m-%d %H:%M"),
        window,
        str(landings),
        "-" if "unavailable" in gates else pct_cell(gates["red"],
                                                    gates["red"] + gates["green"]),
        "-" if "unavailable" in gates else cause_cell(gates["by_cause"]),
        "-" if "unavailable" in gates else ratio_cell(gates["total"], landings),
        "-" if "unavailable" in review else pct_cell(review["block"],
                                                     review["block"] + review["clear"]),
        "-" if "unavailable" in review else str(len(review["unclassified"])),
        "-" if "unavailable" in defects else str(defects["declared"]),
        pct_cell(defects["fix_landings"], landings),
    ]
    return "| " + " | ".join(cells) + " |"


def _fraction(cell):
    m = re.search(r"(\d+)\s*/\s*(\d+)", cell or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _int(cell):
    """A count cell that is always written, such as landings."""
    try:
        return int((cell or "").strip())
    except ValueError:
        return 0


def _count(cell):
    """A count cell, or None when that row did not measure the number: a source
    that was unavailable writes `-`, and a `-` is not a measured zero."""
    text = (cell or "").strip()
    if text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _window_span(cell):
    """The interval a row's `YYYY-MM-DD HH:MM to ...` cell names, or None when the
    cell cannot be read or does not run forward. A row with no readable window
    measured no time that can be counted, and the head says how many rows that hit."""
    m = WINDOW_RE.search(cell or "")
    if not m:
        return None
    try:
        low = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
        high = dt.datetime.strptime(m.group(2), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return (low, high) if high > low else None


def parse_rows(text):
    """The data rows of the quality table: a first cell that reads as a run time."""
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(QUALITY_COLUMNS):
            continue
        try:
            when = dt.datetime.strptime(cells[0], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        red, red_green = _fraction(cells[3])
        gate_files, gate_landings = _fraction(cells[5])
        block, review_total = _fraction(cells[6])
        fix, fix_landings = _fraction(cells[9])
        rows.append({"time": when, "raw": line, "landings": _int(cells[2]),
                     "span": _window_span(cells[1]),
                     "red": red, "red_green": red_green,
                     "gate_files": gate_files, "gate_landings": gate_landings,
                     "block": block, "review_total": review_total,
                     "unclassified": _count(cells[7]), "declared": _count(cells[8]),
                     "fix": fix, "fix_landings": fix_landings})
    return rows


def _bucket(rows, low, high):
    return [r for r in rows if low < r["time"] <= high]


def coverage_hours(bucket):
    """The hours a bucket measured, as the union of its row windows.

    Windows do overlap: a manual `--since 24h` run sits beside the scheduled one
    and measures the same hours again. Adding the rows would count those hours
    twice and halve every rate, so the intervals are merged before they are added.
    """
    spans = sorted((r["span"] for r in bucket if r["span"]), key=lambda s: s[0])
    hours, low, high = 0.0, None, None
    for start, end in spans:
        if low is None:
            low, high = start, end
        elif start <= high:
            high = max(high, end)
        else:
            hours += (high - low).total_seconds() / 3600.0
            low, high = start, end
    if low is not None:
        hours += (high - low).total_seconds() / 3600.0
    return hours


def span_hours(bucket):
    """The hours the rows of a bucket claim, before the overlaps are merged out."""
    return sum((r["span"][1] - r["span"][0]).total_seconds() / 3600.0
               for r in bucket if r["span"])


def rows_overlap(bucket):
    """True when some hour of a bucket was measured by more than one row.

    The rows carry no identity, so a landing counted by two overlapping runs cannot
    be told apart from two landings and no dedupe is possible. What is possible is
    to see the overlap, the spans add up to more than their union, and to stop
    printing a rate whose numerator is doubled while its denominator is not.
    """
    return span_hours(bucket) - coverage_hours(bucket) > 1e-6


def no_window_rows(bucket):
    """How many rows of a bucket carry no readable window, so their counts sum
    while their time does not."""
    return sum(1 for r in bucket if not r["span"])


def coverage_cell(hours, blind, overlap=False):
    """The coverage of a bucket, and what the reader needs to use it: rows that
    overlap, so its counts are upper bounds, and rows with no readable window,
    whose counts sum while their time does not."""
    text = days_cell(hours)
    if overlap:
        text += ", rows overlap"
    if blind:
        text += ", %d row%s with no window" % (blind, "" if blind == 1 else "s")
    return text


def skipped_note(missing):
    """What to add to a summed count when some rows could not measure it."""
    if not missing:
        return ""
    return ", %d row%s not measured" % (missing, "" if missing == 1 else "s")


def count_cell(count, missing):
    return "-" if count is None else "%d%s" % (count, skipped_note(missing))


def overlap_cell(num, den):
    """A ratio the rows of a bucket cannot support. They overlap, so both sums count
    part of the time twice, and rows of different lengths make the quotient a
    weighted average of two populations rather than the share of the covered time.
    The sums print, marked, and the quotient does not."""
    return "%d/%d (rows overlap)" % (num, den)


def declared_cell(count, landings, missing, overlap=False):
    if count is None:
        return "-"
    body = (overlap_cell(count, landings) if overlap
            else per_landing_cell(count, landings))
    return body + skipped_note(missing)


def days_cell(hours):
    return "%.1f days" % (hours / 24.0)


def per_day_cell(count, hours, overlap=False):
    """A count and its rate over the coverage. A bucket whose rows overlap counts
    the same landing twice while the union counts its hours once, so the rate would
    read high: the count prints alone, marked, and the reader is told why."""
    if overlap:
        return "%d (rows overlap, no rate)" % count
    if hours <= 0:
        return "%d (-)" % count
    return "%d (%.1f/day)" % (count, count / (hours / 24.0))


def per_landing_cell(count, landings):
    if not landings:
        return "%d (-)" % count
    return "%d (%.2f/landing)" % (count, float(count) / landings)


def coverage_gap(first, second):
    """How far apart two coverages are, in percent of the larger one, or None when
    neither bucket measured anything. Not rounded: a gap of 20.4 percent is over
    the threshold and rounding it to 20 would silence the warning."""
    top = max(first, second)
    if top <= 0:
        return None
    return 100.0 * abs(first - second) / top


def gap_text(gap):
    """A gap as text, always with a decimal: a true 20.04 printed as a bare 20 would
    show a number that does not clear the threshold the line is warning about."""
    return "%.1f" % gap


def render_head(rows, now):
    """The week over week head, summed from the rows of the table.

    A count is only comparable against a bucket that measured the same amount of
    time, so the coverage of each bucket is a row of its own, the counts that move
    with coverage print their rate next to them, and a coverage gap wide enough to
    flip a reading prints a warning under the table.
    """
    week = dt.timedelta(days=7)
    last = _bucket(rows, now - week, now)
    prev = _bucket(rows, now - 2 * week, now - week)
    last_hours, prev_hours = coverage_hours(last), coverage_hours(prev)
    last_over, prev_over = rows_overlap(last), rows_overlap(prev)

    def total(bucket, key):
        return sum(r[key] for r in bucket)

    def share(bucket, num, den, overlap):
        first, second = total(bucket, num), total(bucket, den)
        return overlap_cell(first, second) if overlap else pct_cell(first, second)

    def ratio(bucket, num, den, overlap):
        first, second = total(bucket, num), total(bucket, den)
        return overlap_cell(first, second) if overlap else ratio_cell(first, second)

    def measured(bucket, key):
        """A count summed over the rows that measured it, and how many rows did
        not. A bucket where no row measured it reads None, never zero."""
        seen = [r[key] for r in bucket if r[key] is not None]
        return (sum(seen) if seen or not bucket else None), len(bucket) - len(seen)

    lines = [HEAD_TITLE, "",
             "Summed from the rows below. Both windows end at a run time, so a week "
             "with no run reads as zero rather than as an improvement. The counts "
             "are the rows as they ran: a manual run beside the scheduled one "
             "measures the same hours again, so its landings, gates and defects are "
             "counted twice. The coverage line is the union of the row windows, so "
             "those hours are counted once, and when the two coverages differ the "
             "rates are the comparison and the counts are not.", "",
             "| Number | %s to %s | %s to %s |"
             % ((now - week).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"),
                (now - 2 * week).strftime("%Y-%m-%d"), (now - week).strftime("%Y-%m-%d")),
             "| --- | --- | --- |"]
    lines.append("| Runs | %d | %d |" % (len(last), len(prev)))
    lines.append("| Coverage | %s | %s |"
                 % (coverage_cell(last_hours, no_window_rows(last), last_over),
                    coverage_cell(prev_hours, no_window_rows(prev), prev_over)))
    lines.append("| Landings | %s | %s |"
                 % (per_day_cell(total(last, "landings"), last_hours, last_over),
                    per_day_cell(total(prev, "landings"), prev_hours, prev_over)))
    lines.append("| Gates red / (red + green) | %s | %s |"
                 % (share(last, "red", "red_green", last_over),
                    share(prev, "red", "red_green", prev_over)))
    lines.append("| Gates per landing | %s | %s |"
                 % (ratio(last, "gate_files", "gate_landings", last_over),
                    ratio(prev, "gate_files", "gate_landings", prev_over)))
    lines.append("| Review block / (block + clear) | %s | %s |"
                 % (share(last, "block", "review_total", last_over),
                    share(prev, "block", "review_total", prev_over)))
    lines.append("| Review unclassified | %s | %s |"
                 % (count_cell(*measured(last, "unclassified")),
                    count_cell(*measured(prev, "unclassified"))))
    last_declared, last_missing = measured(last, "declared")
    prev_declared, prev_missing = measured(prev, "declared")
    lines.append("| Defects declared | %s | %s |"
                 % (declared_cell(last_declared, total(last, "landings"),
                                  last_missing, last_over),
                    declared_cell(prev_declared, total(prev, "landings"),
                                  prev_missing, prev_over)))
    lines.append("| Fix landings / landings | %s | %s |"
                 % (share(last, "fix", "fix_landings", last_over),
                    share(prev, "fix", "fix_landings", prev_over)))
    gap = coverage_gap(last_hours, prev_hours)
    if gap is not None and gap > COVERAGE_GAP_PCT:
        lines.append("")
        lines.append("Coverage differs by %s percent; read the rates, not the "
                     "counts." % gap_text(gap))
    if last_over or prev_over:
        lines.append("")
        lines.append("Rows overlap in a bucket: a manual run beside the scheduled "
                     "one measured the same hours again, so every number of that "
                     "bucket is an upper bound on the counts and no rate, share or "
                     "quotient is printed from it. The daily file of each of those "
                     "runs holds its own window.")
    return lines


def append_quality(path, now, start, end, rep):
    """Append one row to quality.md and re-render the head from every row it holds."""
    text = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    rows = parse_rows(text)
    row = format_row(now, start, end, rep)
    rows.extend(parse_rows(row))
    lines = [QUALITY_TITLE, "", QUALITY_INTRO, ""]
    lines.extend(render_head(rows, now))
    lines.extend(["", TABLE_TITLE, "", QUALITY_HEADER, QUALITY_SEP])
    lines.extend(r["raw"] for r in rows)
    body = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    return row


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
def landings_from_git(ledger_day, repo, start, end):
    """The landings of the window through the reflog reader of ledger-day.py."""
    if not repo:
        return [], "no repository configured, set `repo` in ledger-config.json or pass --repo"
    features, _others, source = ledger_day.read_merges(repo, start, end, None)
    return features, source


def evidence_root(repo):
    """EVIDENCE_ROOT, else the evidence folder beside the repository, else None."""
    if os.environ.get("EVIDENCE_ROOT"):
        return os.environ["EVIDENCE_ROOT"]
    if repo:
        return os.path.join(os.path.dirname(os.path.abspath(repo)), "evidence")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--since", default="24h")
    parser.add_argument("--until", default=None)
    parser.add_argument("--now", default=None,
                        help="pin the clock, for a repeatable run")
    parser.add_argument("--landings", default=None, help="default <evidence root>/landings.md")
    parser.add_argument("--defects", default=None, help="default <ledger>/defects.md")
    parser.add_argument("--gate-dir", dest="gate_dirs", action="append", default=None)
    parser.add_argument("--repo", default=None, help="default `repo` of ledger-config.json")
    parser.add_argument("--no-git", action="store_true",
                        help="do not read the reflog; landings count as zero")
    args = parser.parse_args(argv)

    now = (parse_when(args.now, dt.datetime.now()) if args.now
           else dt.datetime.now().replace(microsecond=0))
    start = parse_when(args.since, now)
    if start is None:  # a blank --since parses to nothing: a usage error, never a traceback
        raise SystemExit("--since is blank; give 24h, 3d or YYYY-MM-DD HH:MM")
    end = parse_when(args.until, now) if args.until else now
    if start >= end:
        raise SystemExit("the window starts after it ends: %s to %s" % (start, end))

    try:
        ledger_day = load_module(LEDGER_DAY, "ledger_day")
    except (ImportError, OSError, SyntaxError) as exc:
        ledger_day = None
        load_note = "ledger-day.py did not load: %s" % exc
    repo = args.repo or (ledger_day.DEFAULT_REPO if ledger_day else None)
    root = evidence_root(repo)
    landings = args.landings or (os.path.join(root, "landings.md") if root else "")
    defects = args.defects or (os.path.join(ledger_day.DEFAULT_LEDGER_DIR, "defects.md") if ledger_day else "")
    features, source = [], "not read (--no-git)"
    if not args.no_git:
        if ledger_day is None:
            source = "landings unavailable: " + load_note
        else:
            try:
                features, source = landings_from_git(ledger_day, repo, start, end)
            except (OSError, ValueError) as exc:
                features, source = [], "landings unavailable: %s" % exc
    rep = compute(start, end, landings, defects, features,
                  gate_dirs=args.gate_dirs, now=now)
    rep["landings_source"] = source
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    sys.exit(main())
