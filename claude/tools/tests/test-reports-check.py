"""Tests for reports-check.py. No network, nothing outside a temporary directory.

Every case builds its ledger, its landings file and its session files in a fresh
temporary directory. The pure cases call `check()` directly, because the flags depend on
a clock and a test that waits 24 hours is not a test; the exit codes, the corpus and the
two output modes are exercised through the CLI as a caller runs it, as a subprocess.

The script under test sits beside the lane-state file named by LANE_STATE_PATH, so the
lane runs this suite against `lane-state.py.new` and its `reports-check.py`, and the
landing re-runs it against the same pair in place.

    python test-reports-check.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
LANE_STATE = os.path.abspath(os.environ.get("LANE_STATE_PATH")
                             or os.path.join(os.path.dirname(HERE), "lane-state.py"))
CHECK = os.path.join(os.path.dirname(LANE_STATE), "reports-check.py")

HEADER = ("| id | reported | words | lane and commit | landed | validation | status |\n"
          "|---|---|---|---|---|---|---|\n")
SHAS = "0380ffc06 to 6225873ae"

FAILURES = []


def load_check():
    spec = importlib.util.spec_from_file_location("reports_check_under_test", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stamp(when):
    return when.strftime("%Y-%m-%d %H:%M")


def landed_row(when, body):
    """The shape a landing has: LANDED in capitals and the main move spelled out."""
    return "%s TRAIN 18 LANDED (tr18). main %s by fast-forward, %s\n" % (stamp(when), SHAS, body)


def put(path, text):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def run_cli(args, tmp):
    env = os.environ.copy()
    env["HOME"] = tmp
    env["USERPROFILE"] = tmp
    return subprocess.run([sys.executable, CHECK] + args, capture_output=True,
                          env=env, cwd=tmp, timeout=60)


def text_of(raw):
    return raw.decode("utf-8", "replace").replace("\r\n", "\n")


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
    module = load_check()
    check = module.check
    now = datetime(2026, 9, 8, 12, 0, 0)
    now_secs = now.timestamp()
    two_days = now - timedelta(days=2, hours=1)
    open_row = HEADER + row("OR-14", "2026-09-07", "las notas qr salen en blanco",
                            "90.8b 5b598683d", "not landed", "none", "OPEN")

    # 1. what a landing row is, and the four shapes that are not one
    case("a LANDED row with the main move is a landing",
         check(open_row, landed_row(two_days, "90.8b 5b598683d"), [], now_secs),
         ["OPEN with a landing row: OR-14 90.8b %s" % stamp(two_days)])
    case("main X to Y without the word LANDED is a landing",
         check(open_row, "%s TRAIN 19 (tr19). main %s by fast-forward, 90.8b 5b598683d\n"
               % (stamp(two_days), SHAS), [], now_secs),
         ["OPEN with a landing row: OR-14 90.8b %s" % stamp(two_days)])
    case("a CORRECTION row naming the token is not a landing",
         check(open_row, "%s CORRECTION to the 15:13 row: train 1 carried 90.8b 5b598683d\n"
               % stamp(two_days), [], now_secs),
         [])
    case("a REVIEW row naming a train and a commit is not a landing",
         check(open_row, "%s REVIEW CLEAR of 5b598683d: the 90.8b lane is CLEAR for train 3\n"
               % stamp(two_days), [], now_secs),
         [])
    case("the lowercase train row of the orchestrator is not a landing",
         check(open_row, "%s train 17 (train-0908a) ce152e2fe: 90.8b 5b598683d, 91b d5d533817\n"
               % stamp(two_days), [], now_secs),
         [])
    case("a row where landed is only lowercase prose is not a landing",
         check(open_row, "%s 2026-09-07 18:1x train 16 e7a8f1b0f (90.8b 5b598683d) landed fine\n"
               % stamp(two_days), [], now_secs),
         [])
    case("a row that says NOT MERGED is not a landing",
         check(open_row, "%s BUILD 90.1 LANDED, NOT MERGED (agent abf4c3791): 90.8b 5b598683d\n"
               % stamp(two_days), [], now_secs),
         [])

    # 2. the id is matched whole, hyphen included, so OR-1 is not OR-10
    verified = HEADER + row("OR-1", "2026-09-06", "merchant", "F33.13 ae1e40249", "TRAIN 1",
                            "none", "VERIFIED 2026-09-07")
    case("verified row without a cell flags",
         check(verified, "", [], now_secs), ["VERIFIED without a cell: OR-1"])
    case("a longer id is not the cell of a shorter one",
         check(verified, "", ["OR-10 and OR-11 measured PASS"], now_secs),
         ["VERIFIED without a cell: OR-1"])
    case("the id cited by a session file is a cell, with no session token",
         check(verified, "", ["cell 2 of OR-1 reads PASS"], now_secs), [])

    # 3. a session token in the validation cell resolves to a session file
    with_token = HEADER + row("OR-3", "2026-09-06 11:44", "merchant", "F33.13 ae1e40249",
                              "TRAIN 1", "0906e cell 2 PASS", "VERIFIED 2026-09-07")
    hit = [("s21u-session-2026-09-06e.md", "cell 2 | PASS")]
    miss = [("pixel-session-2026-09-07v.md", "cell 2 | PASS")]
    case("a session token that resolves to a session file is a cell",
         check(with_token, "", hit, now_secs), [])
    case("a session token with no file flags",
         check(with_token, "", miss, now_secs), ["VERIFIED without a cell: OR-3"])
    case("a two letter token resolves too",
         check(HEADER + row("OR-17", "2026-09-07", "share door", "91b d5d533817", "TRAIN 17",
                            "0907bb C1 REPRODUCED", "VERIFIED 2026-09-08"),
               "", [("pixel-session-2026-09-07bb.md", "C1")], now_secs),
         [])

    # 4. the device named in the validation cell narrows the files the token may use
    on_s21u = HEADER + row("OR-7", "2026-09-06", "dictation", "86.8.9 7f89a4bce", "TRAIN 7",
                           "S21U 0907w 5 of 5", "VERIFIED 2026-09-07")
    phones = ("pixel", "s21u")  # what a board configures; no default is assumed here
    case("a validation naming the s21u does not resolve on a pixel file",
         check(on_s21u, "", [("pixel-session-2026-09-07w.md", "cell 4")], now_secs,
               devices=phones),
         ["VERIFIED without a cell: OR-7"])
    case("a validation naming the s21u resolves on the s21u file",
         check(on_s21u, "", [("s21u-session-2026-09-07w.md", "cell 4")], now_secs,
               devices=phones),
         [])
    case("with no device named any file of that date resolves",
         check(HEADER + row("OR-8", "2026-09-07", "packs", "86.8.7 c910458ad", "TRAIN 11",
                            "0907w 5 of 5", "VERIFIED 2026-09-07"),
               "", [("pixel-session-2026-09-07w.md", "cell 4")], now_secs, devices=phones),
         [])
    case("with no device list configured nothing is narrowed",
         check(on_s21u, "", [("pixel-session-2026-09-07w.md", "cell 4")], now_secs,
               devices=()),
         [])

    # the device word applies to the tokens that follow it, one phone at a time
    two_phones = HEADER + row("OR-7", "2026-09-06", "dictation", "86.8.9 7f89a4bce", "TRAIN 7",
                              "S21U 0907w, Pixel 0907v", "VERIFIED 2026-09-07")
    case("a cell naming two devices resolves each token on its own phone",
         check(two_phones, "", [("s21u-session-2026-09-07w.md", "cell 4"),
                                ("pixel-session-2026-09-07v.md", "cell 2")], now_secs,
               devices=phones),
         [])
    case("a token is not resolved by the other phone's file of the same date",
         check(two_phones, "", [("s21u-session-2026-09-07v.md", "cell 2")], now_secs,
               devices=phones),
         ["VERIFIED without a cell: OR-7"])
    case("a token with no device word of its own keeps the one before it",
         check(HEADER + row("OR-7", "2026-09-06", "dictation", "86.8.9 7f89a4bce", "TRAIN 7",
                            "S21U 0907w 0907v", "VERIFIED 2026-09-07"),
               "", [("pixel-session-2026-09-07v.md", "cell 2")], now_secs, devices=phones),
         ["VERIFIED without a cell: OR-7"])
    case("a token before any device word resolves anywhere",
         check(HEADER + row("OR-7", "2026-09-06", "dictation", "86.8.9 7f89a4bce", "TRAIN 7",
                            "0907v then S21U 0907w", "VERIFIED 2026-09-07"),
               "", [("pixel-session-2026-09-07v.md", "cell 2")], now_secs, devices=phones),
         [])

    # 5. a token of January after a December report belongs to the following year
    new_year = HEADER + row("OR-9", "2025-12-31 19:40", "late report", "86.8.7 c910458ad",
                            "TRAIN 11", "0101a cell 1 PASS", "VERIFIED 2026-01-01")
    case("the token tries the year after the reported one",
         check(new_year, "", [("pixel-session-2026-01-01a.md", "cell 1")], now_secs), [])
    case("the token still tries the reported year first",
         check(HEADER + row("OR-9", "2025-12-31 19:40", "late report", "86.8.7 c910458ad",
                            "TRAIN 11", "1231b cell 1 PASS", "VERIFIED 2026-01-01"),
               "", [("pixel-session-2025-12-31b.md", "cell 1")], now_secs),
         [])

    # 6. a LANDED row is given 24 hours to reach a phone, then it is a flag
    landed = HEADER + row("OR-6", "2026-09-06", "botones que no existen en la app",
                          "90.8 547557281", "LANE 90.8", "none", "LANDED")
    old = now - timedelta(hours=30)
    young = now - timedelta(hours=2)
    case("landed over 24 h without a cell flags",
         check(landed, landed_row(old, "lane 90.8 547557281"), [], now_secs),
         ["LANDED over 24 h without a bench cell: OR-6 %s" % stamp(old)])
    case("landed under 24 h is silent",
         check(landed, landed_row(young, "lane 90.8 547557281"), [], now_secs), [])
    case("landed over 24 h with a resolving token is silent",
         check(HEADER + row("OR-6", "2026-09-06", "botones", "90.8 547557281", "LANE 90.8",
                            "0906e cell 5", "LANDED"),
               landed_row(old, "lane 90.8 547557281"), hit, now_secs),
         [])

    # 7. the 48 h count reads the date of the status cell, not the reported date
    counted = (HEADER
               + row("OR-3", "2026-09-06 11:44", "old report", "F33.13 ae1e40249", "TRAIN 1",
                     "0906e cell 2", "VERIFIED 2026-09-07 (0906e cell 2)")
               + row("OR-4", "2026-09-07", "recent report", "F33.15 aaaaaaa1", "TRAIN 8",
                     "0906e cell 2", "VERIFIED 2026-09-05 (0906e cell 2)")
               + row("OR-5", "2026-09-07", "no date", "F33.10 aaaaaaa2", "TRAIN 1",
                     "0906e cell 2", "VERIFIED"))
    case("the count takes the status date, one inside and one outside the window",
         module.verified_recently(counted, now_secs), 1)
    case("a verified row with no date is not counted and is not a flag",
         check(counted, "", hit, now_secs), [])

    # 8. a malformed row is one line and never raises
    case("malformed row is one line",
         check(HEADER + "| OR-7 | too | few |\n", "", [], now_secs), ["malformed row 3"])
    case("the header row is not a report row", check(HEADER, "", [], now_secs), [])

    with tempfile.TemporaryDirectory() as tmp:
        # 9. the corpus is the session files, never every file under lanes/
        lanes = os.path.join(tmp, "lanes")
        sessions_glob = os.path.join(lanes, "*-session-*.md")
        ledger_path = put(os.path.join(tmp, "owner-reports.md"), landed)
        landings_path = put(os.path.join(tmp, "landings.md"),
                            landed_row(old, "lane 90.8 547557281"))
        put(os.path.join(lanes, "owner-reports-ledger-2026-09-08.md"),
            "the flag reads LANDED over 24 h without a bench cell: OR-6\n")
        args = ["--ledger", ledger_path, "--landings", landings_path,
                "--sessions-glob", sessions_glob]

        done = run_cli(args, tmp)
        case("a lane report quoting the id is not a cell", done.returncode, 1)
        case("the flag survives a lane report that quotes it",
             text_of(done.stderr).splitlines(),
             ["LANDED over 24 h without a bench cell: OR-6 %s" % stamp(old)])
        case("a flagged sheet writes nothing to stdout", text_of(done.stdout), "")

        put(os.path.join(lanes, "s21u-session-2026-09-08a.md"), "cell 1 of OR-6 reads PASS\n")
        done = run_cli(args, tmp)
        case("a session file naming the id is a cell", done.returncode, 0)
        case("a clean sheet is silent", text_of(done.stderr) + text_of(done.stdout), "")

        # 10. --lines is what the state sheet prints, on stdout, always exit 0
        os.remove(os.path.join(lanes, "s21u-session-2026-09-08a.md"))
        done = run_cli(args + ["--lines"], tmp)
        case("--lines exits 0", done.returncode, 0)
        case("--lines prints the sheet lines",
             text_of(done.stdout).splitlines(),
             ["check: LANDED over 24 h without a bench cell: OR-6 %s" % stamp(old)])

        # 11. a ledger that is not there is one line, not a traceback
        done = run_cli(["--ledger", os.path.join(tmp, "gone.md"),
                        "--landings", landings_path, "--sessions-glob", sessions_glob], tmp)
        case("a missing ledger exits 1", done.returncode, 1)
        case("a missing ledger names itself once",
             len(text_of(done.stderr).strip().splitlines()), 1)
        case("a missing ledger does not raise", "Traceback" in text_of(done.stderr), False)

    print("\n%d cases, %d failed" % (case.count, len(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
