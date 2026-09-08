"""Checks an owner reports ledger against the landings file and the session cells.

The ledger is one row per report the owner of a product made, in his own words, with the
lane, the landing and the validation beside it, so that "where does my report stand" is
answered from one row and never from a summary or from memory. This script is the part
that cannot be argued with: it reads what is on disk and says where a row and the disk
disagree. Three flags, and nothing else:

  OPEN with a landing row               the row says nobody shipped it and a landing row
                                        names its lane or its commit.
  VERIFIED without a cell               the row claims someone saw it work and no file
                                        under the lanes folder names the row.
  LANDED over 24 h without a bench cell  the fix has been on the main branch for a day
                                        and no session has measured it yet.

A landing row is a line of the landings file matching `^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}`
that carries the word LANDED and names one of the row's tokens. A token is a word of the
lane-and-commit cell that is at least three characters long and carries a digit, so a
lane id (F33.13, 86.8.11b, 7.1) and a commit are tokens and prose is not; it is matched
with dots and alphanumerics as boundaries, so 7.1 does not match 86.7.1 and 90.8 does not
match 90.8b. A cell is any file under the lanes folder whose text names the row id as a
word, so give the rows ids that no other list uses. Keep the lane-and-commit cell to lane
ids and commits: a figure written there (an amount, a train number) reads as a token.

A row that is not seven cells is one line, `malformed row <n>`, and never an exception:
this runs inside the state sheet, which some setups print at every compaction recovery.

Paths come from the lane-state config (`--config`, then CLAUDE_LANE_STATE_CONFIG, then
`~/.claude/lane-state.json`) with the keys `reports_file`, `landings_file` and
`lanes_glob`; what a key does not set falls back to a path beside the config file itself.

    python reports-check.py                 flags to stderr, exit 1 if there are any
    python reports-check.py --lines         the state sheet lines to stdout, exit 0
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

HOME_CLAUDE = os.path.join(os.path.expanduser("~"), ".claude")
DEFAULT_CONFIG = os.path.join(HOME_CLAUDE, "lane-state.json")

LANDED_ROW_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) .*LANDED")
SEPARATOR_RE = re.compile(r":?-{2,}:?$")
TOKEN_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.]*")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))?")

LANDED_GRACE_SECS = 24 * 3600
VERIFIED_WINDOW_SECS = 48 * 3600
LANE_FILE_BYTES = 256 * 1024  # a lane report is prose; this is a guard, not a budget

COLUMNS = 7
ID, REPORTED, WORDS, LANE, LANDED, VALIDATION, STATUS = range(COLUMNS)


def posix(path):
    return str(path).replace("\\", "/")


def defaults_for(base):
    """The paths of a config living in `base`. Nothing here names one machine."""
    return {
        "reports_file": os.path.join(base, "owner-reports.md"),
        "landings_file": os.path.join(base, "landings.md"),
        "lanes_glob": os.path.join(base, "lanes", "*.md"),
    }


def config_file(explicit=None):
    return explicit or os.environ.get("CLAUDE_LANE_STATE_CONFIG") or DEFAULT_CONFIG


def load_config(path):
    """The config, with every absent key filled in beside the config file itself.

    A config that is not there, or that cannot be used, is not an error here for the same
    reason it is not one in the renderer: this tool must still say something.
    """
    config = defaults_for(os.path.dirname(os.path.abspath(path)))
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return config
    if isinstance(loaded, dict):
        config.update({key: value for key, value in loaded.items() if value})
    return config


def epoch_of(stamp):
    """The epoch seconds of `YYYY-MM-DD` or `YYYY-MM-DD HH:MM`, or None if it is not one."""
    match = DATE_RE.search(stamp or "")
    if not match:
        return None
    text = match.group(1) + " " + (match.group(2) or "00:00")
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp()
    except ValueError:  # 2026-13-45 matches the shape and is not a date
        return None


def ledger_rows(text):
    """(line number, seven cells) per report row, and (line number, None) per bad row.

    The header row and the separator row are not report rows. Everything that is not a
    table line is ignored, so the file keeps its prose header.
    """
    rows = []
    for number, line in enumerate((text or "").splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and all(SEPARATOR_RE.match(cell) for cell in cells):
            continue
        if cells and cells[0].lower() == "id":
            continue
        rows.append((number, cells if len(cells) == COLUMNS else None))
    return rows


def lane_tokens(cell):
    """The lane ids and commits of the lane-and-commit cell, in the order written."""
    tokens = []
    for raw in TOKEN_RE.findall(cell or ""):
        token = raw.strip(".")
        if len(token) < 3 or not any(char.isdigit() for char in token):
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def landing_rows(text):
    """(epoch, stamp, line) per LANDED row of the landings file, oldest first."""
    rows = []
    for line in (text or "").splitlines():
        match = LANDED_ROW_RE.match(line)
        if not match:
            continue
        when = epoch_of(match.group(1))
        if when is not None:
            rows.append((when, match.group(1), line))
    rows.sort(key=lambda row: row[0])
    return rows


def names_token(line, token):
    pattern = r"(?<![0-9A-Za-z.])%s(?![0-9A-Za-z.])" % re.escape(token)
    return re.search(pattern, line, re.IGNORECASE) is not None


def newest_landing(tokens, rows):
    """(token, stamp, epoch) of the newest landing row naming any token, else None."""
    for when, stamp, line in reversed(rows):
        for token in tokens:
            if names_token(line, token):
                return token, stamp, when
    return None


def ids_with_cells(row_ids, lane_texts):
    """The row ids named as a word by at least one lane file.

    One alternation over the whole folder instead of one search per row: this runs
    inside the state sheet, the lanes folder is the largest thing the sheet reads, and
    a pass per row made the render several seconds slower than the rest of it together.
    """
    ids = [row_id for row_id in dict.fromkeys(row_ids) if row_id]
    if not ids:
        return set()
    pattern = re.compile(r"(?<![0-9A-Za-z])(%s)(?![0-9A-Za-z])"
                         % "|".join(re.escape(row_id) for row_id in ids))
    found = set()
    for text in lane_texts:
        found.update(pattern.findall(text or ""))
        if len(found) == len(ids):
            break
    return found


def status_is(cells, word):
    return cells[STATUS].upper().startswith(word)


def check(ledger_text, landings_text, lane_texts, now=None):
    """The flag lines, in the order the ledger writes its rows. Never raises."""
    now = time.time() if now is None else now
    landings = landing_rows(landings_text)
    rows = ledger_rows(ledger_text)
    named = ids_with_cells([cells[ID] for _, cells in rows if cells], list(lane_texts or []))
    flags = []
    for number, cells in rows:
        if cells is None:
            flags.append("malformed row %d" % number)
            continue
        row_id = cells[ID]
        found = newest_landing(lane_tokens(cells[LANE]), landings)
        if status_is(cells, "OPEN") and found:
            flags.append("OPEN with a landing row: %s %s %s" % (row_id, found[0], found[1]))
        elif status_is(cells, "VERIFIED") and row_id not in named:
            flags.append("VERIFIED without a cell: %s" % row_id)
        elif (status_is(cells, "LANDED") and found and now - found[2] > LANDED_GRACE_SECS
              and row_id not in named):
            flags.append("LANDED over 24 h without a bench cell: %s %s" % (row_id, found[1]))
    return flags


def verified_recently(ledger_text, now=None):
    """How many VERIFIED rows carry a reported date inside the last 48 hours."""
    now = time.time() if now is None else now
    count = 0
    for _, cells in ledger_rows(ledger_text):
        if cells is None or not status_is(cells, "VERIFIED"):
            continue
        when = epoch_of(cells[REPORTED])
        if when is not None and when >= now - VERIFIED_WINDOW_SECS:
            count += 1
    return count


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def lane_texts_of(folder):
    """The text of every file directly under the lanes folder, unreadable ones skipped."""
    texts = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return texts
    for name in names:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                texts.append(handle.read(LANE_FILE_BYTES))
        except OSError:
            continue
    return texts


def flags_for(ledger_path, landings_path, lanes_dir, now=None):
    """The flags of the files on disk. A source that is not there is a flag, not a crash."""
    try:
        ledger_text = read_text(ledger_path)
    except OSError:
        return ["no ledger at %s" % posix(ledger_path)]
    try:
        landings_text = read_text(landings_path)
    except OSError:
        return ["no landings file at %s" % posix(landings_path)]
    return check(ledger_text, landings_text, lane_texts_of(lanes_dir), now)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None,
                        help="lane-state config, default CLAUDE_LANE_STATE_CONFIG then "
                             "~/.claude/lane-state.json")
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--landings", default=None)
    parser.add_argument("--lanes-dir", default=None)
    parser.add_argument("--lines", action="store_true",
                        help="print the state sheet lines to stdout and exit 0")
    args = parser.parse_args(argv)

    config = load_config(config_file(args.config))
    ledger = args.ledger or config["reports_file"]
    landings = args.landings or config["landings_file"]
    lanes_dir = args.lanes_dir or os.path.dirname(config["lanes_glob"])

    flags = flags_for(ledger, landings, lanes_dir)
    if args.lines:
        for flag in flags:
            print("check: " + flag)
        return 0
    for flag in flags:
        print(flag, file=sys.stderr)
    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
