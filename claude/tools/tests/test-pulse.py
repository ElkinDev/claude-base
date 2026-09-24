"""Pins for claude/tools/pulse.py: each mechanism status from its trigger and use globs, the ledger warnings that
stand or recur with their keys, the --max cut, and the exit codes for a missing input, a malformed register line and
a usage error.

    python test-pulse.py
    PULSE_PY=<p> python test-pulse.py    # against a staged copy

Every input is a temp file (PULSE_ROOT, PULSE_REGISTER, PULSE_LOG); nothing outside the temp folder is read or written.
"""
import datetime as dt
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("PULSE_PY") or os.path.join(os.path.dirname(HERE), "pulse.py")
OLD = time.time() - 30 * 86400  # outside any window the pins use


def key(text):
    return "pulse:w-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def main():
    tmp = tempfile.mkdtemp(prefix="pulse-")
    results = []

    def check(name, fn):
        try:
            ok, note = bool(fn()), ""
        except Exception as e:
            ok, note = False, " (%s: %s)" % (type(e).__name__, str(e)[:90])
        results.append(ok)
        print(("OK   " if ok else "FAIL ") + name + note)

    def touch(root, rel, when=None):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        if when is not None:
            os.utime(path, (when, when))
        return path

    def setup(register, log_lines=None, files=()):
        root = tempfile.mkdtemp(dir=tmp)
        for rel, when in files:
            touch(root, rel, when)
        reg = os.path.join(root, "pulse.md")
        with open(reg, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(register)
        log = os.path.join(root, "nightly.log")
        if log_lines is not None:
            with open(log, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(log_lines) + "\n")
        return dict(os.environ, PULSE_ROOT=root, PULSE_REGISTER=reg, PULSE_LOG=log, PYTHONIOENCODING="utf-8")

    def run(env, *args):
        return subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=60)

    later = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    today = dt.date.today().isoformat()
    head = "id | principle | ruling | trigger | use | number command | keep line | review date\n"

    def reg_line(mid, trigger, use, review=later, number="`python n.py --row`", keep="the number falls"):
        return "%s | quality | a ruling | %s | %s | %s | %s | %s\n" % (mid, trigger, use, number, keep, review)

    try:
        def statuses():
            register = head + "".join([
                reg_line("alive", "t/a-*.md", "u/a-*.md"),
                reg_line("silent", "t/s-*.md", "u/s-*.md"),
                reg_line("idle", "t/i-*.md", "u/i-*.md"),
                reg_line("dark", "t/d-*.md", "none"),
                reg_line("nomatch", "t/n-*.md", "u/typo-*.md"),
                reg_line("due", "t/a-*.md", "u/a-*.md", review=today),
                "# a comment line\n", "\n",
            ])
            env = setup(register, [], files=[("t/a-1.md", None), ("u/a-1.md", None), ("t/s-1.md", None),
                                               ("u/s-1.md", OLD), ("t/i-1.md", OLD), ("u/i-1.md", OLD),
                                               ("t/d-1.md", None), ("t/n-1.md", None)])
            r = run(env)
            out = r.stdout.splitlines()
            return (r.returncode == 0
                    and "mechanisms 6 (alive 2, silent 1, idle 1, dark 1, nomatch 1, due 1)" in out[0]
                    and "SILENT silent: trigger 1, use 0 [pulse:silent]" in out
                    and "DARK dark: no use evidence named, nothing on disk says it ran [pulse:dark]" in out
                    and "NOMATCH nomatch: no path matches the use glob at any time [pulse:nomatch]" in out
                    and any(ln.startswith("DUE due since %s: run `python n.py --row`" % today) for ln in out)
                    and not any("[pulse:alive]" in ln or "[pulse:idle]" in ln for ln in out))
        check("ALIVE and IDLE stay out of the block; SILENT, DARK, NOMATCH and DUE print with their keys; the head "
              "counts each status", statuses)

        def exclusions_and_folders():
            register = head + reg_line("ex", "t/*.md !t/skip-*.md", "u/") + reg_line("fold", "w/k.md", "v/")
            env = setup(register, [], files=[("t/skip-1.md", None), ("t/keep.md", OLD), ("u/f/x.md", OLD),
                                               ("w/k.md", None), ("v/y.md", None)])
            for folder in ("u", os.path.join("u", "f")):  # making u/f/x.md moved both folders' mtimes to now
                os.utime(os.path.join(env["PULSE_ROOT"], folder), (OLD, OLD))
            r = run(env)
            # "ex": the only fresh trigger is excluded and the use folder is old, so IDLE; "fold": v/ is a folder
            # whose mtime moved with y.md, so ALIVE
            return r.returncode == 0 and "alive 1" in r.stdout and "idle 1" in r.stdout and "[pulse:ex]" not in r.stdout
        check("a ' !<glob>' exclusion drops its matches and a glob ending in / counts folders only",
              exclusions_and_folders)

        def malformed_lines():
            register = head + "bad | only three\n" + reg_line("Upper", "t/*", "u/*") + reg_line("dup", "t/*", "u/*") \
                + reg_line("dup", "t/*", "u/*") + reg_line("baddate", "t/*", "u/*", review="soon") \
                + "piped | q | r | t/* | u/* | `a | b` | keep | %s\n" % later
            env = setup(register, [], files=[("t/1", None), ("u/1", None)])
            r = run(env)
            lines = [ln for ln in r.stdout.splitlines() if ln.startswith("MALFORMED")]
            return (r.returncode == 1 and len(lines) == 4 and "review date 'soon' [pulse:baddate]" in r.stdout
                    and all("[pulse:register-" in ln for ln in lines if "baddate" not in ln)
                    and "[pulse:piped]" not in r.stdout and "mechanisms 2 (alive 2" in r.stdout)
        check("a short line, a bad id, a repeated id and a bad date are MALFORMED with a key and exit 1; a number "
              "command in backticks may hold ' | '", malformed_lines)

        def warnings():
            standing = "reports | OPEN with a landing row: OR-5"
            recurring = "meters | no line appended, probe exit code 1"
            fine = "reports | seen once"
            lines = []
            for i in range(1, 8):
                lines.append("[2026-09-%02d 08:00:00] start, morning run" % i)
                if i >= 5:
                    lines.append("[2026-09-%02d 08:00:01] %s" % (i, standing))
                if i in (1, 2, 3):
                    lines.append("[2026-09-%02d 08:00:02] %s" % (i, recurring))
                if i == 4:
                    lines.append("[2026-09-%02d 08:00:03] %s" % (i, fine))
                lines.append("[2026-09-%02d 08:00:04] pulse | a pulse line is never a warning" % i)
            env = setup(head, lines)
            r = run(env)
            return (r.returncode == 0 and "warnings flagged 2 (standing 1, recurring 1) of 3 read over 7 runs" in r.stdout
                    and "STANDING 3 runs (3 of the last 14): %s [%s]" % (standing, key(standing)) in r.stdout
                    and "RECURRING 3 of the last 14 (streak 0): %s [%s]" % (recurring, key(recurring)) in r.stdout
                    and fine not in r.stdout)
        check("a warning in the last 3 runs is STANDING, one in 3 older runs is RECURRING at streak 0, one seen once is "
              "neither; each key is the sha1 of its text", warnings)

        def max_cut():
            register = head + "".join(reg_line("d%d" % i, "t/*", "none") for i in range(6))
            env = setup(register, [], files=[("t/1", None)])
            r = run(env, "--max", "4")
            out = r.stdout.splitlines()
            return (r.returncode == 0 and len(out) == 4 and out[3].startswith("... 4 more: python ")
                    and out[3].endswith("pulse.py") and "[pulse:" not in out[3])
        check("--max 4 prints the head, 2 items and a '... N more' line naming this script, which carries no key", max_cut)

        def missing_inputs_and_usage():
            env = setup(head, None)
            gone = dict(env, PULSE_REGISTER=os.path.join(tmp, "no-register.md"))
            r1, r2 = run(env), run(gone)
            bad = [run(env, "--max", "1"), run(env, "--max", "-2"), run(env, "--hours", "0"), run(env, "--max", "x")]
            return (r1.returncode == 1 and "[pulse:log-missing]" in r1.stdout
                    and r2.returncode == 1 and "[pulse:register-missing]" in r2.stdout
                    and [b.returncode for b in bad] == [2, 2, 2, 2] and all("Traceback" not in b.stderr for b in bad))
        check("a missing log or register still prints the block and exits 1 with its key; --max 1, a negative max, "
              "--hours 0 and a word are exit 2", missing_inputs_and_usage)

        def if_set():
            # review kit-twins-0924a r1 finding 1: the installer puts pulse.py on every machine, a register on none
            env = setup(head, None)
            gone = dict(env, PULSE_REGISTER=os.path.join(tmp, "no-register.md"))
            quiet, loud = run(gone, "--if-set"), run(env, "--if-set")
            return (quiet.returncode == 0 and quiet.stdout == "" and loud.returncode == 1
                    and "[pulse:log-missing]" in loud.stdout and "CLAUDE_LEDGER_DIR" in loud.stdout)
        check("--if-set prints nothing and exits 0 with no register; with one it prints as always, and a missing log "
              "names CLAUDE_LEDGER_DIR", if_set)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of %d OK" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
