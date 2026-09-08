"""Checks an owner reports ledger against the landings file and the session cells.

The ledger is one row per report the owner of a product made, in his own words, with the
lane, the landing and the validation beside it, so that "where does my report stand" is
answered from one row and never from a summary or from memory. This script is the part
that cannot be argued with: it reads what is on disk and says where a row and the disk
disagree. Three flags, and nothing else:

  OPEN with a landing row               the row says nobody shipped it and a landing row
                                        names its lane or its commit.
  VERIFIED without a cell               the row claims someone saw it work and no session
                                        file backs it.
  LANDED over 24 h without a bench cell  the fix has been on the main branch for a day
                                        and no session has measured it yet.

A landing row is a line of the landings file that starts with `YYYY-MM-DD HH:MM`, whose
first word after the stamp is neither CORRECTION nor REVIEW, whose text carries either
the word LANDED in capitals or the phrase `main <sha> to <sha>` (seven hex digits or
more each), and which names one of the row's tokens. Nothing else counts: a build row
that states a train and its tip says a train was built, not that the main branch moved,
a correction or a review narrative that quotes a commit is neither, and a row that
says NOT MERGED landed nothing however loudly it says LANDED. A token is a word
of the lane-and-commit cell that is at least three characters long and carries a digit, so
a lane id (F33.13, 86.8.11b, 7.1) and a commit are tokens and prose is not; it is matched
with dots and alphanumerics as boundaries, so 7.1 does not match 86.7.1 and 90.8 does not
match 90.8b. Keep the lane-and-commit cell to lane ids and commits: a figure written
there (an amount, a train number) would be read as a token.

The cells live in the session files and nowhere else, which is the glob `sessions_glob`
(`lanes/*-session-*.md`). A lane report that quotes a row id is not evidence that anyone
ran it, and reading a whole folder let one such report silence two of the three flags for
every row at once. A row has a cell when either a session file names the row id as a
whole word (punctuation of the id included, so OR-1 is never read inside OR-10), or the
validation cell carries a session token that resolves to one. A session token is `MMDD`
plus one or two lowercase letters (0906e, 0907bb); it resolves against a session file
whose name ends in `<year>-MM-DD<letters>.md`, the year taken from the row's reported date
and then the year after it, so a token of January under a December report still resolves.
A device word in the validation cell, taken from the `devices` list of the config
(empty by default, which narrows nothing), binds the tokens that follow it until the
next one, so a cell reading "S21U 0907w, Pixel 0907v" names one session on each phone
and neither answers for the other; a token before any device word resolves anywhere.

The status cell of a VERIFIED or OWNER-CLOSED row carries the date the evidence was taken
(`VERIFIED 2026-09-06 (0906e cell 2)`), and that date, not the reported one, is what the
48 hour count of the state sheet reads. A VERIFIED row without a date is not counted and
is not a flag of its own.

A row that is not seven cells is one line, `malformed row <n>`, and never an exception:
this runs inside the state sheet, which some setups print at every compaction recovery.

Paths come from the lane-state config (`--config`, then CLAUDE_LANE_STATE_CONFIG, then
`~/.claude/lane-state.json`) with the keys `reports_file`, `landings_file` and
`sessions_glob`, plus `devices`; what a key does not set falls back to a path beside
the config file.

    python reports-check.py                 flags to stderr, exit 1 if there are any
    python reports-check.py --lines         the state sheet lines to stdout, exit 0
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime

HOME_CLAUDE = os.path.join(os.path.expanduser("~"), ".claude")
DEFAULT_CONFIG = os.path.join(HOME_CLAUDE, "lane-state.json")
DEFAULT_DEVICES = ()  # a setup that names no device narrows nothing

STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+(\S*)")
NOT_A_LANDING = ("CORRECTION", "REVIEW")
LANDED_WORD_RE = re.compile(r"\bLANDED\b")  # capitals only: prose says landed, rows say LANDED
MAIN_MOVE_RE = re.compile(r"\bmain\s+[0-9a-fA-F]{7,}\s+to\s+[0-9a-fA-F]{7,}\b", re.IGNORECASE)
NOT_MERGED_RE = re.compile(r"NOT\s+MERGED", re.IGNORECASE)
SEPARATOR_RE = re.compile(r":?-{2,}:?$")
TOKEN_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.]*")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))?")
YEAR_RE = re.compile(r"(\d{4})-\d{2}-\d{2}")
SESSION_TOKEN_RE = re.compile(r"(?<![0-9A-Za-z])(\d{2})(\d{2})([a-z]{1,2})(?![0-9A-Za-z])")

LANDED_GRACE_SECS = 24 * 3600
VERIFIED_WINDOW_SECS = 48 * 3600
SESSION_FILE_BYTES = 256 * 1024  # a session report is prose; this is a guard, not a budget

COLUMNS = 7
ID, REPORTED, WORDS, LANE, LANDED, VALIDATION, STATUS = range(COLUMNS)


def posix(path):
    return str(path).replace("\\", "/")


def defaults_for(base):
    """The paths of a config living in `base`. Nothing here names one machine."""
    return {
        "reports_file": os.path.join(base, "owner-reports.md"),
        "landings_file": os.path.join(base, "landings.md"),
        "sessions_glob": os.path.join(base, "lanes", "*-session-*.md"),
        "devices": [],
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
        with open(path, encoding="utf-8-sig") as handle:
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


def is_landing_line(line):
    """A landing row says main moved: LANDED in capitals, or the main move spelled out.

    A build row that names a train and its tip says a train exists, not that it reached
    main; a CORRECTION or a REVIEW line quotes commits by the dozen and lands nothing.
    Reading those as landings is how a ledger answers "it is in" about work that is not.
    """
    match = STAMP_RE.match(line)
    if not match:
        return None
    if match.group(2).strip(".,:;()").upper() in NOT_A_LANDING:
        return None
    if NOT_MERGED_RE.search(line):
        return None
    if not (LANDED_WORD_RE.search(line) or MAIN_MOVE_RE.search(line)):
        return None
    return match.group(1)


def landing_rows(text):
    """(epoch, stamp, line) per landing row of the landings file, oldest first."""
    rows = []
    for line in (text or "").splitlines():
        found = is_landing_line(line)
        if not found:
            continue
        when = epoch_of(found)
        if when is not None:
            rows.append((when, found, line))
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


def as_pairs(session_files):
    """(name, text) per session file. A bare string is a text with no name, for the
    callers that only exercise the row id road."""
    pairs = []
    for item in session_files or []:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            pairs.append((os.path.basename(str(item[0])), item[1] or ""))
        else:
            pairs.append(("", item or ""))
    return pairs


def ids_with_cells(row_ids, texts):
    """The row ids named as a whole word by at least one session file.

    One alternation over the corpus instead of one search per row: this runs inside the
    state sheet, and a pass per row made the render several seconds slower than the rest
    of it together. The boundary is alphanumeric only, so the hyphen of OR-1 is part of
    the word and OR-1 never matches inside OR-10.
    """
    ids = [row_id for row_id in dict.fromkeys(row_ids) if row_id]
    if not ids:
        return set()
    pattern = re.compile(r"(?<![0-9A-Za-z])(%s)(?![0-9A-Za-z])"
                         % "|".join(re.escape(row_id) for row_id in ids))
    found = set()
    for text in texts:
        found.update(pattern.findall(text or ""))
        if len(found) == len(ids):
            break
    return found


def validation_tokens(validation, devices):
    """(device, (month, day, letters)) per session token of the cell, left to right.

    A device word binds the tokens that follow it until the next one, so a cell reading
    "S21U 0907w, Pixel 0907v" names one session on each phone and neither answers for the
    other. A token written before any device word is bound to no phone.
    """
    words = [device.lower() for device in devices or () if device]
    events = []
    if words:
        device_re = re.compile(r"(?<![0-9A-Za-z])(%s)(?![0-9A-Za-z])"
                               % "|".join(re.escape(word) for word in words), re.IGNORECASE)
        events += [(found.start(), 0, found.group(1).lower())
                   for found in device_re.finditer(validation or "")]
    events += [(found.start(), 1, found.groups())
               for found in SESSION_TOKEN_RE.finditer(validation or "")]
    events.sort(key=lambda event: (event[0], event[1]))
    found = []
    current = None
    for _, kind, value in events:
        if kind == 0:
            current = value
        else:
            found.append((current, value))
    return found


def token_resolves(reported, validation, names, devices=None):
    """True when a session token of the validation cell names a file of the corpus."""
    year = YEAR_RE.search(reported or "")
    if not year:
        return False
    years = (int(year.group(1)), int(year.group(1)) + 1)
    lowered = [name.lower() for name in names if name]
    devices = DEFAULT_DEVICES if devices is None else devices
    for device, (month, day, letters) in validation_tokens(validation, devices):
        allowed = ([name for name in lowered if name.startswith(device)]
                   if device else lowered)
        for candidate in years:
            suffix = "%d-%s-%s%s.md" % (candidate, month, day, letters)
            if any(name.endswith(suffix) for name in allowed):
                return True
    return False


def status_is(cells, word):
    return cells[STATUS].upper().startswith(word)


def check(ledger_text, landings_text, session_files, now=None, devices=None):
    """The flag lines, in the order the ledger writes its rows. Never raises."""
    now = time.time() if now is None else now
    landings = landing_rows(landings_text)
    pairs = as_pairs(session_files)
    names = [name for name, _ in pairs]
    rows = ledger_rows(ledger_text)
    named = ids_with_cells([cells[ID] for _, cells in rows if cells],
                           [text for _, text in pairs])
    flags = []
    for number, cells in rows:
        if cells is None:
            flags.append("malformed row %d" % number)
            continue
        row_id = cells[ID]
        has_cell = (row_id in named
                    or token_resolves(cells[REPORTED], cells[VALIDATION], names,
                                      devices))
        found = newest_landing(lane_tokens(cells[LANE]), landings)
        if status_is(cells, "OPEN") and found:
            flags.append("OPEN with a landing row: %s %s %s" % (row_id, found[0], found[1]))
        elif status_is(cells, "VERIFIED") and not has_cell:
            flags.append("VERIFIED without a cell: %s" % row_id)
        elif (status_is(cells, "LANDED") and found and now - found[2] > LANDED_GRACE_SECS
              and not has_cell):
            flags.append("LANDED over 24 h without a bench cell: %s %s" % (row_id, found[1]))
    return flags


def verified_recently(ledger_text, now=None):
    """How many VERIFIED rows carry a status date inside the last 48 hours.

    The date read is the one in the status cell, which is when the phone evidence was
    taken; the reported date is when the owner spoke, and a report of last week verified
    this morning belongs in this count.
    """
    now = time.time() if now is None else now
    count = 0
    for _, cells in ledger_rows(ledger_text):
        if cells is None or not status_is(cells, "VERIFIED"):
            continue
        when = epoch_of(cells[STATUS])
        if when is not None and when >= now - VERIFIED_WINDOW_SECS:
            count += 1
    return count


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def session_files_of(pattern):
    """(name, text) of every bench session file the glob names, unreadable ones skipped."""
    files = []
    for path in sorted(glob.glob(pattern or "")):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                files.append((os.path.basename(path), handle.read(SESSION_FILE_BYTES)))
        except OSError:
            continue
    return files


def flags_for(ledger_path, landings_path, sessions_glob, now=None, devices=None):
    """The flags of the files on disk. A source that is not there is a flag, not a crash."""
    try:
        ledger_text = read_text(ledger_path)
    except OSError:
        return ["no ledger at %s" % posix(ledger_path)]
    try:
        landings_text = read_text(landings_path)
    except OSError:
        return ["no landings file at %s" % posix(landings_path)]
    return check(ledger_text, landings_text, session_files_of(sessions_glob), now,
                 devices)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None,
                        help="lane-state config, default CLAUDE_LANE_STATE_CONFIG then "
                             "~/.claude/lane-state.json")
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--landings", default=None)
    parser.add_argument("--sessions-glob", default=None)
    parser.add_argument("--lines", action="store_true",
                        help="print the state sheet lines to stdout and exit 0")
    args = parser.parse_args(argv)

    config = load_config(config_file(args.config))
    flags = flags_for(args.ledger or config["reports_file"],
                      args.landings or config["landings_file"],
                      args.sessions_glob or config["sessions_glob"],
                      devices=config["devices"])
    if args.lines:
        for flag in flags:
            print("check: " + flag)
        return 0
    for flag in flags:
        print(flag, file=sys.stderr)
    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
