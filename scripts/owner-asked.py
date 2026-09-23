"""owner-asked.py: before any question to the owner, search what the owner already answered.

    python owner-asked.py <word> [<word> ...] [--since YYYY-MM-DD] [--max N] [--all-rows]

Finds the lines that carry every word, case and accent insensitive; a word also matches inside a longer one, so
"resume" finds "resumes" and "cafe" finds "café". Five sources, each optional (a source that is not on disk is
skipped):
  - rulings.md under the evidence root, the register: the rows of the kinds that carry owner answers, [owner],
    [decision], [product] and [design] by default (--all-rows: every row). Both row shapes count,
    "- YYYY-MM-DD HH:MM [kind]" written by hand and "YYYY-MM-DD HH:MM [kind]" as scripts/row.sh writes it; the
    minutes may be written as "x" ("10:0x");
  - every drafts/owner-decisions-*.md, one line per question with its state and the owner's answer. They are
    append logs, so a line is dated by the latest date written in it, and by the file name only when it has none;
  - ledger/owner-reports.md, the owner reports ledger (docs/ADOPTION.md, the reports_file of the state sheet): its
    table rows whose first cell is an id such as R-12, dated the same way;
  - the lines of landings.md that name the owner (ratifications, delegations), dated by their own date or the
    last date above them;
  - the memory folder of the project, where standing rules live, often verbatim: frontmatter skipped, a line dated
    by a date that opens it (within its first 22 characters after list and bold marks), else by the later of
    the file's modified field and its mtime.
A word found only inside a commit hash (a run of seven or more characters from 0-9 and a-f) is not a hit, so a
bare number of seven digits or more, such as an eight-digit date, cannot be searched: write the date with dashes.
A date later than today never dates a line (a line that names a future deadline would otherwise sort first in
every search). Output is UTF-8 whatever the console's codepage.

When the owner writes in another language than the register, search the topic in both. Prints one line per hit,
newest first: date, source:line, the line cut to 320 characters. Exit 0 on a hit, 1 on none, 2 on a usage error.
A hit that answers the question is applied and cited instead of asking; a question still asked cites this command
and what it printed.

Serves quality (fewer questions the owner already answered) and token reduction (each repeat costs an owner turn
and a session turn).

Environment:
  OWNER_ASKED_ROOT    the evidence root; default EVIDENCE_ROOT, else the working directory
  OWNER_ASKED_MEMORY  the memory folder; default <config dir>/projects/<working directory with every character
                      that is not a letter or a digit replaced by a dash>/memory, the config dir being
                      CLAUDE_CONFIG_DIR or ~/.claude
  OWNER_ASKED_KINDS   the register kinds searched without --all-rows, comma separated;
                      default owner,decision,product,design
  CLAUDE_RULINGS_FILE, CLAUDE_LANDINGS_FILE   the register and the landings file when they do not sit at the
                      evidence root, the same variables the state sheet and the board read
"""
import argparse
import datetime
import glob
import os
import re
import sys
import unicodedata


def default_memory():
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(config, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.getcwd()), "memory")


ROOT = os.environ.get("OWNER_ASKED_ROOT") or os.environ.get("EVIDENCE_ROOT") or os.getcwd()
MEMORY = os.environ.get("OWNER_ASKED_MEMORY") or default_memory()
OWNER_KINDS = tuple(k.strip() for k in (os.environ.get("OWNER_ASKED_KINDS") or "owner,decision,product,design")
                    .split(",") if k.strip())
DATE = re.compile(r"(20\d\d-\d\d-\d\d)")
ROW = re.compile(r"^(?:- )?(20\d\d-\d\d-\d\d) [0-9x]{2}:[0-9x]{2} \[([A-Za-z0-9_-]+)\]")
REPORT_ROW = re.compile(r"^\|\s*[A-Za-z]+-\d+\s*\|")
CUT = 320
HEAD = 22  # a memory line takes its own date only when the date opens it ("Superseded 2026-01-05 ...")
HEX = re.compile(r"\b[0-9a-f]{7,40}\b")  # commit hashes: a word found only inside one is not a hit


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def lines_of(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def latest_date(text):
    """The latest date written in the line that is not in the future."""
    today = datetime.date.today().isoformat()
    found = [d for d in DATE.findall(text) if d <= today]
    return max(found) if found else ""


def file_date(path, text_lines):
    """The later of the frontmatter's modified field and the file's mtime: a modified field is often older than
    the last edit, which would date a fresh rule into the past."""
    mtime = datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
    for ln in text_lines[:12]:
        m = re.match(r"\s*modified:\s*(20\d\d-\d\d-\d\d)", ln)
        if m:
            return max(m.group(1), mtime)
    return mtime


def body_start(text_lines):
    """Index of the first line after a leading '---' frontmatter block, 0 when there is none."""
    if text_lines and text_lines[0].strip() == "---":
        for i in range(1, len(text_lines)):
            if text_lines[i].strip() == "---":
                return i + 1
    return 0


def candidates(all_rows):
    """Yield (date, source, line number, text) for every searchable line."""
    rulings = os.environ.get("CLAUDE_RULINGS_FILE") or os.path.join(ROOT, "rulings.md")
    for i, ln in enumerate(lines_of(rulings), 1):
        m = ROW.match(ln)
        if m and (all_rows or m.group(2) in OWNER_KINDS):
            yield m.group(1), os.path.basename(rulings), i, ln
    for path in sorted(glob.glob(os.path.join(ROOT, "drafts", "owner-decisions-*.md"))):
        m = DATE.search(os.path.basename(path))
        named = m.group(1) if m else ""
        for i, ln in enumerate(lines_of(path), 1):
            if ln.strip():
                yield latest_date(ln) or named, "drafts/" + os.path.basename(path), i, ln
    for i, ln in enumerate(lines_of(os.path.join(ROOT, "ledger", "owner-reports.md")), 1):
        if REPORT_ROW.match(ln):
            yield latest_date(ln), "ledger/owner-reports.md", i, ln
    carry = ""
    landings = os.environ.get("CLAUDE_LANDINGS_FILE") or os.path.join(ROOT, "landings.md")
    for i, ln in enumerate(lines_of(landings), 1):
        own = latest_date(ln)
        carry = own or carry
        if re.search(r"\bowner\b", ln, re.I):
            yield own or carry, os.path.basename(landings), i, ln
    for path in sorted(glob.glob(os.path.join(MEMORY, "*.md"))):
        text = lines_of(path)
        fdate = file_date(path, text)
        for i in range(body_start(text), len(text)):
            ln = text[i]
            if ln.strip():
                m = DATE.search(ln.lstrip(" *#-")[:HEAD])
                yield (m.group(1) if m else fdate), "memory/" + os.path.basename(path), i + 1, ln


def main(argv):
    # A console in a legacy codepage cannot encode arrows or accents: an encode error there would print no hit
    # and exit 1, the "question is new" code.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("words", nargs="+", help="every word must appear in the line")
    ap.add_argument("--since", help="only lines dated on or after YYYY-MM-DD")
    ap.add_argument("--max", type=int, default=25, help="hits printed, newest first (default 25)")
    ap.add_argument("--all-rows", action="store_true", help="every register row, not only the owner-answer kinds")
    a = ap.parse_args(argv)
    if a.since and not re.fullmatch(r"20\d\d-\d\d-\d\d", a.since):
        ap.error("--since takes YYYY-MM-DD")
    words = [fold(w) for w in a.words if w.strip()]
    if not words:
        ap.error("give at least one word that is not blank")
    hits = []
    for day, src, n, text in candidates(a.all_rows):
        if a.since and day and day < a.since:
            continue
        folded = HEX.sub(" ", fold(text))
        if all(w in folded for w in words):
            hits.append((day, src, n, re.sub(r"\s+", " ", text).strip()))
    hits.sort(key=lambda h: (h[0], h[1], h[2]), reverse=True)
    try:
        for day, src, n, text in hits[: a.max]:
            cut = text if len(text) <= CUT else text[: CUT - 6] + " [cut]"
            print("%s %s:%d: %s" % (day or "----------", src, n, cut))
        if len(hits) > a.max:
            print("... %d more; narrow with another word or --since" % (len(hits) - a.max))
    except OSError:  # a reader that stops early (| head): BrokenPipeError, or EINVAL on Windows; not a failed search
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())  # so the exit flush cannot raise again
        except OSError:
            pass
        return 0 if hits else 1
    if not hits:
        print("no hit for %s in the register, the decisions files, the owner reports, the landings or the memory;"
              " the question is new" % " ".join(a.words))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
