"""Tests for the owner-reports section of lane-state.py. Temporary directories only.

Every case writes a config, a ledger, a landings file and a lanes folder into a fresh
temporary directory, renders the sheet with `law --out` into that directory, and reads
the rendered section back. HOME and USERPROFILE point at the temporary directory and the
gates folder is passed explicitly, so no source of this machine is scanned and neither
the real ledger nor the real law.md is read or written.

The file under test is named by LANE_STATE_PATH, so the lane runs this suite against
`lane-state.py.new` and the landing re-runs it against `lane-state.py` in place.

    python test-lane-state-reports.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
LANE_STATE = os.path.abspath(os.environ.get("LANE_STATE_PATH")
                             or os.path.join(os.path.dirname(HERE), "lane-state.py"))

HEADING_PREFIX = "## Owner reports not VERIFIED ("
HEADER = ("| id | reported | words | lane and commit | landed | validation | status |\n"
          "|---|---|---|---|---|---|---|\n")
SHAS = "0380ffc06 to 6225873ae"

FAILURES = []
LAST = {"heading": ""}


def put(path, text):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def render(tmp, ledger_text, landings_text="", session_text="", reports_key=True,
           reports_path=None, script=None):
    """Renders the sheet in `tmp` and gives back the lines of the reports section."""
    lanes = os.path.join(tmp, "lanes")
    gates = os.path.join(tmp, "gates")
    os.makedirs(gates, exist_ok=True)
    put(os.path.join(lanes, "s21u-session-2026-09-06e.md"),
        session_text or "no report id here\n")
    put(os.path.join(tmp, "briefs", "brief.md"), "brief\n")
    ledger = reports_path or os.path.join(tmp, "ledger", "owner-reports.md")
    if ledger_text is not None:
        put(ledger, ledger_text)
    config = {
        "fixed_lines": [],
        "gates_dirs": [gates],
        "landings_file": put(os.path.join(tmp, "landings.md"), landings_text),
        "rulings_file": os.path.join(tmp, "rulings.md"),
        "project_repo": "",
        "lanes_glob": os.path.join(lanes, "*.md"),
        "sessions_glob": os.path.join(lanes, "*-session-*.md"),
        "briefs_glob": os.path.join(tmp, "briefs", "*.md"),
    }
    if reports_key:
        config["reports_file"] = ledger
    config_path = put(os.path.join(tmp, "law-config.json"),
                      json.dumps(config, indent=2) + "\n")
    out = os.path.join(tmp, "law-out.md")
    env = os.environ.copy()
    env["HOME"] = tmp
    env["USERPROFILE"] = tmp
    done = subprocess.run(
        [sys.executable, script or LANE_STATE, "--config", config_path, "law",
         "--out", out, "--gates-dir", gates],
        capture_output=True, env=env, cwd=tmp, timeout=120)
    if done.returncode != 0:
        return ["renderer exited %d: %s" % (done.returncode,
                                            done.stderr.decode("utf-8", "replace"))]
    with open(out, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    headings = [ln for ln in lines if ln.startswith(HEADING_PREFIX)]
    if not headings:
        return ["section missing, headings: %s"
                % ",".join(ln for ln in lines if ln.startswith("## "))]
    LAST["heading"] = headings[0]
    start = lines.index(headings[0]) + 1
    section = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        section.append(line)
    return section


def case(name, got, want):
    case.count = getattr(case, "count", 0) + 1
    if got == want:
        print("ok   %s" % name)
        return
    FAILURES.append(name)
    print("FAIL %s\n  want %r\n  got  %r" % (name, want, got))


def row(rid, reported, words, lane, landed, validation, status):
    return "| %s | %s | %s | %s | %s | %s | %s |\n" % (
        rid, reported, words, lane, landed, validation, status)


def main():
    now = datetime.now()
    fresh = (now - timedelta(hours=3)).strftime("%Y-%m-%d")
    stale = (now - timedelta(days=10)).strftime("%Y-%m-%d")
    landing_stamp = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
    train_stamp = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")

    with tempfile.TemporaryDirectory() as tmp:
        # 1. an empty ledger says so with the section's own empty line
        case("an empty ledger prints the empty text",
             render(tmp, ""), ["no open owner report"])
        case("a ledger of headers only prints the empty text",
             render(tmp, HEADER), ["no open owner report"])
        case("the heading names the basename of the configured file",
             LAST["heading"], HEADING_PREFIX + "owner-reports.md)")

    with tempfile.TemporaryDirectory() as tmp:
        # 2. the heading follows the config, it is not the name of one board's file
        render(tmp, HEADER, reports_path=os.path.join(tmp, "ledger", "reports-x.md"))
        case("a ledger under another name is named in the heading",
             LAST["heading"], HEADING_PREFIX + "reports-x.md)")

    with tempfile.TemporaryDirectory() as tmp:
        # 3. an OPEN row is rendered whole, six fields, and the count line follows
        ledger = HEADER + row("OR-1", fresh, "la captura no toma el comercio",
                              "F33.13 ae1e40249", "not landed", "none", "OPEN")
        case("one open row renders whole",
             render(tmp, ledger),
             ["OR-1 | OPEN | la captura no toma el comercio | F33.13 ae1e40249 | "
              "not landed | none",
              "VERIFIED in the last 48 h: 0"])

    with tempfile.TemporaryDirectory() as tmp:
        # 4. the check lines ride the section, prefixed, so a compaction cannot lose them
        ledger = HEADER + row("OR-1", fresh, "la captura no toma el comercio",
                              "F33.13 ae1e40249", "not landed", "none", "OPEN")
        landings = ("%s TRAIN 1 LANDED (tr1). main %s by fast-forward, F33.13 ae1e40249\n"
                    % (landing_stamp, SHAS))
        case("the check flags ride the section",
             render(tmp, ledger, landings_text=landings),
             ["OR-1 | OPEN | la captura no toma el comercio | F33.13 ae1e40249 | "
              "not landed | none",
              "VERIFIED in the last 48 h: 0",
              "check: OPEN with a landing row: OR-1 F33.13 %s" % landing_stamp])

    with tempfile.TemporaryDirectory() as tmp:
        # 5. the lowercase train row is a build row, not a landing, and raises no flag
        ledger = HEADER + row("OR-14", fresh, "las notas qr salen en blanco",
                              "90.8b 5b598683d", "not landed", "none", "OPEN")
        train = "%s train 17 (train-0908a) ce152e2fe: 90.8b 5b598683d, 91b d5d533817\n" % train_stamp
        case("a lowercase train row raises no flag in the sheet",
             render(tmp, ledger, landings_text=train),
             ["OR-14 | OPEN | las notas qr salen en blanco | 90.8b 5b598683d | "
              "not landed | none",
              "VERIFIED in the last 48 h: 0"])

    with tempfile.TemporaryDirectory() as tmp:
        # 6. a VERIFIED row leaves the sheet, and the count reads its status date
        ledger = (HEADER
                  + row("OR-3", stale, "merchant", "F33.13 ae1e40249", "TRAIN 1",
                        "none", "VERIFIED %s (0906e cell 2)" % fresh)
                  + row("OR-4", fresh, "old evidence", "F33.15 aaaaaaa1", "TRAIN 2",
                        "none", "VERIFIED %s (0906e cell 2)" % stale)
                  + row("OR-5", fresh, "still open", "none", "not landed", "none", "OPEN"))
        case("a verified row is hidden and only the recent status date is counted",
             render(tmp, ledger, session_text="cells OR-3 and OR-4 read PASS\n"),
             ["OR-5 | OPEN | still open | none | not landed | none",
              "VERIFIED in the last 48 h: 1"])

    with tempfile.TemporaryDirectory() as tmp:
        # 7. a malformed row is one line, and the good rows around it still render
        ledger = (HEADER
                  + row("OR-1", fresh, "words", "none", "not landed", "none", "OPEN")
                  + "| OR-2 | too | few |\n")
        case("a malformed row is one line",
             render(tmp, ledger),
             ["OR-1 | OPEN | words | none | not landed | none",
              "VERIFIED in the last 48 h: 0",
              "check: malformed row 4"])

    with tempfile.TemporaryDirectory() as tmp:
        # 8. a long row is clipped twice, so one report can never take the sheet: the
        # words at 120 and then the whole line at 320
        ledger = HEADER + row("OR-1", fresh, "x" * 400, "y9 " * 100, "not landed",
                              "none", "OPEN")
        lines = render(tmp, ledger)
        case("a long row is clipped to 320", len(lines[0]), 320)
        case("a long row ends in an ellipsis", lines[0][-3:], "...")
        case("the words are clipped to 120 first", lines[0].count("x"), 117)

    with tempfile.TemporaryDirectory() as tmp:
        # 9. no key and a key pointing nowhere are the same line, and never a traceback
        case("an absent reports_file prints the configured-absent line",
             render(tmp, "", reports_key=False), ["no reports file configured"])
        case("a reports_file that is not there prints the same line",
             render(tmp, None, reports_path=os.path.join(tmp, "ledger", "gone.md")),
             ["no reports file configured"])

    with tempfile.TemporaryDirectory() as tmp:
        # 10. the renderer keeps no parser of its own, so a copy without the checker
        # beside it says so in one line instead of rendering half a section
        alone = os.path.join(tmp, "copy", "lane-state.py")
        os.makedirs(os.path.dirname(alone), exist_ok=True)
        shutil.copyfile(LANE_STATE, alone)
        ledger = HEADER + row("OR-1", fresh, "words", "none", "not landed", "none", "OPEN")
        lines = render(tmp, ledger, script=alone)
        case("a renderer with no checker beside it says so in one line", len(lines), 1)
        case("and names the reason",
             lines[0].startswith("reports-check unavailable: "), True)

    print("\n%d cases, %d failed" % (case.count, len(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
