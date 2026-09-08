"""Tests for reports-check.py. No network, nothing outside a temporary directory.

Every case builds its ledger, its landings file and its lane files in a fresh temporary
directory. The pure cases call `check()` directly, because the flags depend on a clock
and a test that waits 24 hours is not a test; the exit codes and the two output modes
are exercised through the CLI as a caller runs it, as a subprocess.

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

FAILURES = []


def load_check():
    spec = importlib.util.spec_from_file_location("reports_check_under_test", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stamp(when):
    return when.strftime("%Y-%m-%d %H:%M")


def landing(when, body):
    return "%s %s LANDED. main aaaaaaa to bbbbbbb, %s\n" % (stamp(when), body.upper(), body)


def train_row(when, number, body):
    """The shape the orchestrator's train rows drifted to: no LANDED, a lowercase train."""
    return "%s train %d (train-0908a) ce152e2fe: members %s\n" % (stamp(when), number, body)


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

    # 1. an OPEN row whose lane has a landing row is the 19:16 error, caught mechanically
    ledger = HEADER + row("OR-1", "2026-09-06", "la captura no toma el comercio",
                          "F33.13 ae1e40249", "not landed", "none", "OPEN")
    case("open row with a landing row flags",
         check(ledger, landing(two_days, "train 1 F33.13 ae1e40249"), [], now_secs),
         ["OPEN with a landing row: OR-1 F33.13 %s" % stamp(two_days)])
    case("open row without a landing row is silent",
         check(ledger, landing(now, "train 1 F99.9 ffffff0"), [], now_secs),
         [])

    # 2. a train row carries no LANDED since 09-07; a lowercase train number is a landing
    train = train_row(now - timedelta(hours=2), 17, "F69 W4b.3 fc1ec2965, 90.8b 5b598683d")
    open_qr = HEADER + row("OR-14", "2026-09-07", "las notas qr salen en blanco",
                           "90.8b 5b598683d", "not landed", "none", "OPEN")
    case("a lowercase train row lands a lane",
         check(open_qr, train, [], now_secs),
         ["OPEN with a landing row: OR-14 90.8b %s" % stamp(now - timedelta(hours=2))])
    case("a timestamped row that is neither LANDED nor a train is not a landing",
         check(open_qr, "%s gate tr17-g2 green on 90.8b 5b598683d\n"
               % stamp(now - timedelta(hours=2)), [], now_secs),
         [])

    # 3. the id is matched whole, hyphen included, so OR-1 is not OR-10
    verified = HEADER + row("OR-1", "2026-09-06", "merchant", "F33.13 ae1e40249", "TRAIN 1",
                            "none", "VERIFIED 2026-09-07")
    case("verified row without a cell flags",
         check(verified, "", [], now_secs),
         ["VERIFIED without a cell: OR-1"])
    case("a longer id is not the cell of a shorter one",
         check(verified, "", ["OR-10 and OR-11 measured PASS"], now_secs),
         ["VERIFIED without a cell: OR-1"])
    case("the id cited by a lanes file is a cell, with no session token",
         check(verified, "", ["cell 2 of OR-1 reads PASS"], now_secs),
         [])

    # 4. a session token in the validation cell resolves to a file under lanes/
    with_token = HEADER + row("OR-3", "2026-09-06 11:44", "merchant", "F33.13 ae1e40249",
                              "TRAIN 1", "0906e cell 2 PASS", "VERIFIED 2026-09-07")
    lanes_hit = [("lanes/s21u-session-2026-09-06e.md", "cell 2 | PASS")]
    lanes_miss = [("lanes/pixel-session-2026-09-07v.md", "cell 2 | PASS")]
    case("a session token that resolves to a lanes file is a cell",
         check(with_token, "", lanes_hit, now_secs), [])
    case("a session token with no file under lanes flags",
         check(with_token, "", lanes_miss, now_secs),
         ["VERIFIED without a cell: OR-3"])
    case("the year of the token comes from the reported date",
         check(HEADER + row("OR-3", "2025-09-06 11:44", "merchant", "F33.13 ae1e40249",
                            "TRAIN 1", "0906e cell 2 PASS", "VERIFIED 2026-09-07"),
               "", lanes_hit, now_secs),
         ["VERIFIED without a cell: OR-3"])
    case("a two letter token resolves too",
         check(HEADER + row("OR-17", "2026-09-07", "share door", "91b d5d533817", "TRAIN 17",
                            "0907bb C1 REPRODUCED", "VERIFIED 2026-09-08"),
               "", [("lanes/pixel-session-2026-09-07bb.md", "C1")], now_secs),
         [])

    # 5. a LANDED row is given 24 hours to reach a phone, then it is a flag
    landed = HEADER + row("OR-6", "2026-09-06", "botones que no existen en la app",
                          "90.8 547557281", "LANE 90.8", "none", "LANDED")
    old = now - timedelta(hours=30)
    young = now - timedelta(hours=2)
    case("landed over 24 h without a cell flags",
         check(landed, landing(old, "lane 90.8 547557281"), [], now_secs),
         ["LANDED over 24 h without a bench cell: OR-6 %s" % stamp(old)])
    case("landed under 24 h is silent",
         check(landed, landing(young, "lane 90.8 547557281"), [], now_secs), [])
    case("landed over 24 h with a resolving token is silent",
         check(HEADER + row("OR-6", "2026-09-06", "botones", "90.8 547557281", "LANE 90.8",
                            "0906e cell 5", "LANDED"),
               landing(old, "lane 90.8 547557281"), lanes_hit, now_secs),
         [])

    # 6. the 48 h count reads the date of the status cell, not the reported date
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
         check(counted, "", lanes_hit, now_secs), [])

    # 7. a malformed row is one line and never raises
    broken = HEADER + "| OR-7 | too | few |\n"
    case("malformed row is one line", check(broken, "", [], now_secs), ["malformed row 3"])
    case("the header row is not a report row", check(HEADER, "", [], now_secs), [])

    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = put(os.path.join(tmp, "owner-reports.md"), ledger)
        landings_path = put(os.path.join(tmp, "landings.md"),
                            landing(now - timedelta(days=2), "train 1 F33.13 ae1e40249"))
        lanes_dir = os.path.join(tmp, "lanes")
        put(os.path.join(lanes_dir, "session.md"), "nothing about any report here\n")
        args = ["--ledger", ledger_path, "--landings", landings_path, "--lanes-dir", lanes_dir]

        # 8. the default mode is stderr and a non-zero exit, so a caller can gate on it
        done = run_cli(args, tmp)
        case("a flagged sheet exits 1", done.returncode, 1)
        case("a flagged sheet writes the flag to stderr",
             text_of(done.stderr).splitlines(),
             ["OPEN with a landing row: OR-1 F33.13 %s" % stamp(now - timedelta(days=2))])
        case("a flagged sheet writes nothing to stdout", text_of(done.stdout), "")

        # 9. --lines is what the state sheet prints, on stdout, always exit 0
        done = run_cli(args + ["--lines"], tmp)
        case("--lines exits 0", done.returncode, 0)
        case("--lines prints the sheet lines",
             text_of(done.stdout).splitlines(),
             ["check: OPEN with a landing row: OR-1 F33.13 %s"
              % stamp(now - timedelta(days=2))])

        # 10. a clean sheet is silent and exits 0, and the token resolves on real files
        put(ledger_path, HEADER + row("OR-3", "2026-09-06 11:44", "merchant",
                                      "F33.13 ae1e40249", "TRAIN 1", "0906e cell 2",
                                      "VERIFIED 2026-09-06 (0906e cell 2)"))
        put(os.path.join(lanes_dir, "s21u-session-2026-09-06e.md"), "cell 2 PASS\n")
        done = run_cli(args, tmp)
        case("a clean sheet exits 0", done.returncode, 0)
        case("a clean sheet is silent", text_of(done.stderr) + text_of(done.stdout), "")

        # 11. a ledger that is not there is one line, not a traceback
        done = run_cli(["--ledger", os.path.join(tmp, "gone.md"),
                        "--landings", landings_path, "--lanes-dir", lanes_dir], tmp)
        case("a missing ledger exits 1", done.returncode, 1)
        case("a missing ledger names itself once",
             len(text_of(done.stderr).strip().splitlines()), 1)
        case("a missing ledger does not raise", "Traceback" in text_of(done.stderr), False)

    print("\n%d cases, %d failed" % (case.count, len(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
