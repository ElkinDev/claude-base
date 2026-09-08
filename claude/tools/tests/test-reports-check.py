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


def main():
    module = load_check()
    check = module.check
    now = datetime(2026, 9, 8, 12, 0, 0)
    now_secs = now.timestamp()

    # 1. an OPEN row whose lane has a landing row is the 19:16 error, caught mechanically
    ledger = HEADER + ("| R1 | 2026-09-06 | la captura no toma el comercio | "
                       "F33.13 ae1e40249 | not landed | none | OPEN |\n")
    landings = landing(now - timedelta(days=2, hours=1), "train 1 F33.13 ae1e40249")
    case("open row with a landing row flags",
         check(ledger, landings, [], now_secs),
         ["OPEN with a landing row: R1 F33.13 %s" % stamp(now - timedelta(days=2, hours=1))])

    # the same row with no landing row anywhere is silent
    case("open row without a landing row is silent",
         check(ledger, landing(now, "train 1 F99.9 ffffff0"), [], now_secs),
         [])

    # 2. a VERIFIED row needs a lane file that names its id
    verified = HEADER + ("| R2 | 2026-09-06 | la foto se guarda horizontal | "
                         "F33.13 ae1e40249 | TRAIN 1 | 0906e cell 3 | VERIFIED |\n")
    case("verified row without a cell flags",
         check(verified, "", [], now_secs),
         ["VERIFIED without a cell: R2"])
    case("verified row with a cell is silent",
         check(verified, "", ["cell 3 of R2 measured PASS"], now_secs),
         [])
    case("a longer id is not the cell of a shorter one",
         check(verified, "", ["R20 and R21 measured PASS"], now_secs),
         ["VERIFIED without a cell: R2"])

    # 3. a LANDED row is given 24 hours to reach a phone, then it is a flag
    landed = HEADER + ("| R6 | 2026-09-06 | botones que no existen en la app | "
                       "90.8 547557281 | LANE 90.8 | none | LANDED |\n")
    old = now - timedelta(hours=30)
    young = now - timedelta(hours=2)
    case("landed over 24 h without a cell flags",
         check(landed, landing(old, "lane 90.8 547557281"), [], now_secs),
         ["LANDED over 24 h without a bench cell: R6 %s" % stamp(old)])
    case("landed under 24 h is silent",
         check(landed, landing(young, "lane 90.8 547557281"), [], now_secs),
         [])
    case("landed over 24 h with a cell is silent",
         check(landed, landing(old, "lane 90.8 547557281"), ["R6 PASS on the S21U"], now_secs),
         [])

    # 4. a malformed row is one line and never raises
    broken = HEADER + "| R7 | too | few |\n"
    case("malformed row is one line", check(broken, "", [], now_secs), ["malformed row 3"])
    case("the header row is not a report row", check(HEADER, "", [], now_secs), [])

    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = put(os.path.join(tmp, "owner-reports.md"), ledger)
        landings_path = put(os.path.join(tmp, "landings.md"),
                            landing(now - timedelta(days=2), "train 1 F33.13 ae1e40249"))
        lanes_dir = os.path.join(tmp, "lanes")
        put(os.path.join(lanes_dir, "session.md"), "nothing about any report here\n")
        args = ["--ledger", ledger_path, "--landings", landings_path, "--lanes-dir", lanes_dir]

        # 5. the default mode is stderr and a non-zero exit, so a caller can gate on it
        done = run_cli(args, tmp)
        case("a flagged sheet exits 1", done.returncode, 1)
        case("a flagged sheet writes the flag to stderr",
             text_of(done.stderr).splitlines(),
             ["OPEN with a landing row: R1 F33.13 %s" % stamp(now - timedelta(days=2))])
        case("a flagged sheet writes nothing to stdout", text_of(done.stdout), "")

        # 6. --lines is what the state sheet prints, on stdout, always exit 0
        done = run_cli(args + ["--lines"], tmp)
        case("--lines exits 0", done.returncode, 0)
        case("--lines prints the sheet lines",
             text_of(done.stdout).splitlines(),
             ["check: OPEN with a landing row: R1 F33.13 %s"
              % stamp(now - timedelta(days=2))])

        # 7. a clean sheet is silent and exits 0
        put(ledger_path, HEADER + ("| R1 | 2026-09-06 | la captura no toma el comercio | "
                                   "F33.13 ae1e40249 | TRAIN 1 | 0906e cell 2 | VERIFIED |\n"))
        put(os.path.join(lanes_dir, "session.md"), "cell 2 of R1 reads PASS\n")
        done = run_cli(args, tmp)
        case("a clean sheet exits 0", done.returncode, 0)
        case("a clean sheet is silent", text_of(done.stderr) + text_of(done.stdout), "")

        # 8. a ledger that is not there is one line, not a traceback
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
