"""Pins for claude/tools/pulse-escalate.py: an item
flagged in both of the last two pulse runs and cited by no decision row goes into today's owner decisions file as one
auto-class row with a 21:15 deadline; a cited item, a one-run item, an item already in today's file and a run whose
pulse step failed do not; nothing is written after 21:15 or on a dry run; a missing file is made with the header of
the newest earlier one.

    python test-pulse-escalate.py
    PULSE_ESCALATE_PY=<p> python test-pulse-escalate.py    # against a staged copy

Every input is a temp file (PULSE_LOG, PULSE_RULINGS, PULSE_DECISIONS_DIR); nothing of the live board is read or
written.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("PULSE_ESCALATE_PY") or os.path.join(os.path.dirname(HERE), "pulse-escalate.py")
HOOK = os.environ.get("COMPACT_RECOVER_PY") or os.path.join(os.path.dirname(os.path.dirname(HERE)), "hooks",
                                                            "compact-recover.py")
ASK_ROW_RE = re.compile(r"^- \d\d:[0-9x]{2}\b")  # compact-recover.py's open-ask shape
SHUT_ASK_RE = re.compile(r"\b(DECIDED|DONE|RULED|APPLIED|LAPSED|CLOSED)\b")


def run_block(stamp, items, summary=True):
    lines = ["[%s] start, morning run, window from yesterday 18:00" % stamp]
    if summary:
        lines.append("[%s] pulse | pulse 2026-09-24 08:00: mechanisms 4 (alive 2, silent 1), warnings flagged 2" % stamp)
    lines += ["[%s] pulse | %s [%s]" % (stamp, text, key) for key, text in items]
    lines.append("[%s] pulse.py exit code 0" % stamp)
    return lines


A = ("pulse:maestro-route", "SILENT maestro-route: trigger 18, use 0")
B = ("pulse:w-988c72a9", "STANDING 10 runs (10 of the last 14): reports | OPEN with a landing row: OR-56 psyn2")
C = ("pulse:w-83436e11", "STANDING 9 runs: reports | OR-33 APPLIED and DONE, VERIFIED")
D = ("pulse:plugin-proof", "DUE plugin-proof since 2026-09-24: run the draft's method")


def main():
    tmp = tempfile.mkdtemp(prefix="pulse-escalate-")
    results = []

    def check(name, fn):
        try:
            ok, note = bool(fn()), ""
        except Exception as e:
            ok, note = False, " (%s: %s)" % (type(e).__name__, str(e)[:90])
        results.append(ok)
        print(("OK   " if ok else "FAIL ") + name + note)

    def setup(runs, rulings="", files=None):
        root = tempfile.mkdtemp(dir=tmp)
        log, reg, dd = os.path.join(root, "nightly.log"), os.path.join(root, "rulings.md"), os.path.join(root, "drafts")
        os.makedirs(dd)
        with open(log, "w", encoding="utf-8", newline="\n") as f:
            f.write("\ufeff" + "\n".join(sum(runs, [])) + "\n")  # the real log opens with a BOM
        with open(reg, "w", encoding="utf-8") as f:
            f.write(rulings)
        for name, text in (files or {}).items():
            with open(os.path.join(dd, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        env = dict(os.environ, PULSE_LOG=log, PULSE_RULINGS=reg, PULSE_DECISIONS_DIR=dd, PYTHONIOENCODING="utf-8")
        return env, dd

    def esc(env, *args):
        return subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=60)

    def read(dd, day="2026-09-24"):
        p = os.path.join(dd, "owner-decisions-%s.md" % day)
        return open(p, encoding="utf-8").read() if os.path.exists(p) else None

    header = "# Owner decisions, 2026-09-23 (analyst pane; S4 rows)\n\n- 10:1x an old row. DECIDED 11:0x.\n"
    two = [run_block("2026-09-23 18:00:05", [A, B, C, D]), run_block("2026-09-24 08:00:04", [A, B, C])]

    try:
        def writes_one_row():
            env, dd = setup(two, rulings="2026-09-23 19:0x [decision] waive pulse:w-83436e11 for now (analyst pane)\n",
                            files={"owner-decisions-2026-09-23.md": header})
            r = esc(env, "--now", "2026-09-24 08:01")
            text = read(dd)
            rows = [ln for ln in (text or "").splitlines() if ASK_ROW_RE.match(ln)]
            return (r.returncode == 0 and text.startswith("# Owner decisions, 2026-09-24 (analyst pane; S4 rows)\n\n")
                    and len(rows) == 1 and rows[0].startswith("- 08:0x Pulse escalation")
                    and "pulse:maestro-route (SILENT" in rows[0] and "pulse:w-988c72a9 (STANDING" in rows[0]
                    and "pulse:w-83436e11" not in rows[0] and "pulse:plugin-proof" not in rows[0]
                    and rows[0].startswith("- 08:0x Pulse escalation, 2 keys with no decision row after 2 ledger runs: "
                                           "pulse:maestro-route, pulse:w-988c72a9. Auto class each")
                    and "21:15" in rows[0] and not SHUT_ASK_RE.search(rows[0]) and "wrote 1 row with 2 keys" in r.stdout)
        check("two runs: one open auto-class row with the 2 uncited keys flagged in both; a cited key and a one-run key "
              "stay out; a missing file takes the earlier header with today's date", writes_one_row)

        def shut_words_lowered():
            env, dd = setup(two, files={"owner-decisions-2026-09-24.md": "# h\n"})
            esc(env, "--now", "2026-09-24 08:01")
            row = [ln for ln in read(dd).splitlines() if ASK_ROW_RE.match(ln)][0]
            return "pulse:w-83436e11" in row and "applied and done" in row and not SHUT_ASK_RE.search(row)
        check("an item quoting DECIDED, DONE, APPLIED and the like in capitals is written lower case, so the row stays "
              "open", shut_words_lowered)

        def second_run_repeats_nothing():
            env, dd = setup(two, files={"owner-decisions-2026-09-24.md": "# h\n"})
            esc(env, "--now", "2026-09-24 08:01")
            before = read(dd)
            r = esc(env, "--now", "2026-09-24 18:01")
            return r.returncode == 0 and read(dd) == before and "nothing owed" in r.stdout and "3 already in" in r.stdout
        check("the 18:00 run adds nothing for keys the 08:00 row already names", second_run_repeats_nothing)

        def failed_pulse_run_skipped():
            runs = [run_block("2026-09-23 08:00:01", [A]), run_block("2026-09-23 18:00:01", [], summary=False),
                    run_block("2026-09-24 08:00:01", [A])]
            env, dd = setup(runs, files={"owner-decisions-2026-09-24.md": "# h\n"})
            r = esc(env, "--now", "2026-09-24 08:01")
            one = [run_block("2026-09-23 18:00:01", [A], summary=False), run_block("2026-09-24 08:00:01", [A])]
            env1, dd1 = setup(one, files={"owner-decisions-2026-09-24.md": "# h\n"})
            r1 = esc(env1, "--now", "2026-09-24 08:01")
            stale = two + [run_block("2026-09-24 18:00:01", [], summary=False)]
            env2, dd2 = setup(stale, files={"owner-decisions-2026-09-24.md": "# h\n"})
            r2 = esc(env2, "--now", "2026-09-24 18:01")
            return ("pulse:maestro-route" in read(dd) and r.returncode == 0 and r1.returncode == 0
                    and read(dd1) == "# h\n" and "1 pulse runs in the log, 2 needed" in r1.stdout
                    and r2.returncode == 0 and read(dd2) == "# h\n" and "stale" in r2.stdout)
        check("a run whose pulse step printed no summary neither flags nor clears: the two runs around it count, one "
              "pulse run alone owes nothing, and a latest run with no pulse summary escalates nothing stale",
              failed_pulse_run_skipped)

        def after_last_and_dry_run():
            env, dd = setup(two, files={"owner-decisions-2026-09-24.md": "# h\n"})
            late = esc(env, "--now", "2026-09-24 21:16")
            dry = esc(env, "--dry-run", "--now", "2026-09-24 08:01")
            return (late.returncode == 0 and "no deadline past 21:15" in late.stdout and dry.returncode == 0
                    and "- 08:0x Pulse escalation" in dry.stdout and read(dd) == "# h\n")
        check("a run after 21:15 and a dry run write nothing; the dry run prints the row", after_last_and_dry_run)

        def no_earlier_file_minimal_header():
            env, dd = setup(two)
            r = esc(env, "--now", "2026-09-24 08:01")
            return r.returncode == 0 and read(dd).startswith("# Owner decisions, 2026-09-24\n\n- 08:0x Pulse escalation")
        check("with no earlier decisions file the new one opens with a minimal header", no_earlier_file_minimal_header)

        def no_newline_at_end():
            env, dd = setup(two, files={"owner-decisions-2026-09-24.md": "# h\n\n- 07:1x an open ask"})
            esc(env, "--now", "2026-09-24 08:01")
            lines = read(dd).splitlines()
            return lines[2] == "- 07:1x an open ask" and lines[3].startswith("- 08:0x Pulse escalation")
        check("a file that does not end in a newline gets one before the row, never a joined line", no_newline_at_end)

        def inputs_and_usage():
            env, _ = setup(two)
            missing = esc(dict(env, PULSE_LOG=os.path.join(tmp, "none.log")), "--now", "2026-09-24 08:01")
            noreg = esc(dict(env, PULSE_RULINGS=os.path.join(tmp, "none.md")), "--now", "2026-09-24 08:01")
            bad = [esc(env, "--now", x) for x in ("2026-09-24", "  ", "", "tomorrow")]
            return (missing.returncode == 1 and noreg.returncode == 1 and "Traceback" not in missing.stderr + noreg.stderr
                    and all(b.returncode == 2 and "--now" in b.stderr and "Traceback" not in b.stderr for b in bad))
        check("a missing log or register is exit 1 with one line; a bare date, a blank, an empty value or a word for "
              "--now is exit 2",
              inputs_and_usage)

        def key_boundary():
            env, dd = setup(two, rulings="row cites pulse:maestro-route-old and pulse:w-988c72a (analyst pane)\n",
                            files={"owner-decisions-2026-09-24.md": "# h\n"})
            esc(env, "--now", "2026-09-24 08:01")
            row = read(dd)
            return "pulse:maestro-route (" in row and "pulse:w-988c72a9 (" in row
        check("a longer key or a shorter prefix in a row does not count as citing the key", key_boundary)

        def lock_held_and_stale():
            env, dd = setup(two, files={"owner-decisions-2026-09-24.md": "# h\n"})
            lock = os.path.join(dd, ".pulse-escalate.lock")
            open(lock, "w").close()
            busy = esc(env, "--now", "2026-09-24 08:01")
            kept = os.path.exists(lock) and read(dd) == "# h\n"
            old = os.path.getmtime(lock) - 400
            os.utime(lock, (old, old))
            stale = esc(env, "--now", "2026-09-24 08:01")
            return (busy.returncode == 0 and "another run holds" in busy.stdout and kept and stale.returncode == 0
                    and "Pulse escalation" in read(dd) and not os.path.exists(lock))
        check("a fresh lock leaves the file and the lock alone, exit 0; a lock over 300 s old is taken over, the row "
              "written and the lock removed", lock_held_and_stale)

        def gone_recurring_not_escalated():
            gone = ("pulse:w-988c72a9", "RECURRING 10 of the last 14 (streak 0): reports | OPEN with a landing row: OR-56")
            back = ("pulse:w-58b64e3e", "RECURRING 8 of the last 14 (streak 1): reports | LANDED over 24 h: OR-29")
            # review plsg note 1: "(streak 0):" quoted inside a live item's own text is not its streak
            quoted = ("pulse:w-7a1c", "RECURRING 6 of the last 14 (streak 2): ledger | warn: row says (streak 0): still")
            env, dd = setup([run_block("2026-09-23 18:00:05", [A, gone, back, quoted]),
                             run_block("2026-09-24 08:00:04", [A, gone, back, quoted])],
                            files={"owner-decisions-2026-09-24.md": "# h\n"})
            r = esc(env, "--now", "2026-09-24 08:01")
            row = [ln for ln in read(dd).splitlines() if ln.startswith("- 08:0x")][0]
            return (r.returncode == 0 and "pulse:w-988c72a9" not in row and "pulse:w-58b64e3e" in row
                    and "pulse:maestro-route" in row and "pulse:w-7a1c" in row and "3 keys" in row
                    and "1 stopped (streak 0) left out" in r.stdout)
        check("a RECURRING warning with streak 0 (absent from the latest run) is not escalated and the head line counts "
              "it; one with a streak is, even when its text quotes '(streak 0):'", gone_recurring_not_escalated)

        def keys_survive_the_session_start_clip():
            # review plsf r1 findings 1 and 2: what a session start prints, not what the file holds. The hook runs
            # from a temp copy with its asks glob on the temp decisions folder and every other board input absent.
            many = [("pulse:w-%08x" % i, "STANDING %d runs: reports | LANDED over 24 h without a bench cell: OR-%d" % (i, i))
                    for i in range(1, 15)]
            env, dd = setup([run_block("2026-09-23 18:00:05", [A] + many), run_block("2026-09-24 08:00:04", [A] + many)],
                            files={"owner-decisions-2026-09-24.md": "# h\n"})
            esc(env, "--now", "2026-09-24 08:01")
            hooks = os.path.join(tmp, "hooks")
            os.makedirs(hooks, exist_ok=True)
            hook = shutil.copy(HOOK, os.path.join(hooks, "compact-recover.py"))
            henv = dict(os.environ, CLAUDE_DECISIONS_GLOB=os.path.join(dd, "owner-decisions-*.md"),
                        CLAUDE_RULINGS_FILE=os.path.join(tmp, "none-rulings.md"), CLAUDE_ROLE="analyst",
                        CLAUDE_CHECKPOINT_DIR=tmp, CLAUDE_PULSE_PY=os.path.join(tmp, "none.py"),
                        CLAUDE_LANE_STATE_SCRIPT=os.path.join(tmp, "none-ls.py"), PYTHONIOENCODING="utf-8")
            for name in ("CLAUDE_BRIEFS_DIR", "CLAUDE_LANDINGS_FILE", "CLAUDE_BOARD_ROOT", "CLAUDE_BOARD_PREFIXES"):
                henv.pop(name, None)
            payload = json.dumps({"session_id": "pin00000-0000", "cwd": tmp, "source": "startup"})
            out = subprocess.run([sys.executable, hook, "--rulings"], input=payload, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", env=henv, timeout=60).stdout
            asks = out[out.find("Open owner asks"):].split("\n\n", 1)[0] if "Open owner asks" in out else ""
            shown = re.findall(r"pulse:[a-z0-9-]+", asks)
            return "Pulse escalation, 15 keys" in asks and "pulse:maestro-route" in shown and len(shown) >= 4
        check("the escalation row keeps its keys through a session start's ask clip: the count and at least 4 keys "
              "print", keys_survive_the_session_start_clip)

        def file_made_between_read_and_write():
            # review plsf r1 finding 6: another session makes today's file after the read; the run appends to it
            import importlib.machinery
            import importlib.util
            import builtins
            env, dd = setup(two)
            loader = importlib.machinery.SourceFileLoader("pulse_escalate_under_test", SCRIPT)
            spec = importlib.util.spec_from_loader("pulse_escalate_under_test", loader)
            mod = importlib.util.module_from_spec(spec)
            real_open = builtins.open
            day_file = os.path.join(dd, "owner-decisions-2026-09-24.md")

            def racing_open(path, mode="r", *args, **kwargs):
                if mode == "x" and os.path.basename(str(path)) == "owner-decisions-2026-09-24.md":
                    with real_open(day_file, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write("# made by the other session\n\n- 07:5x its own ask naming pulse:maestro-route\n")
                    raise FileExistsError(path)
                return real_open(path, mode, *args, **kwargs)
            saved = {k: os.environ.get(k) for k in ("PULSE_LOG", "PULSE_RULINGS", "PULSE_DECISIONS_DIR")}
            argv = sys.argv
            try:
                os.environ.update({k: env[k] for k in saved})
                spec.loader.exec_module(mod)
                sys.argv = ["pulse-escalate.py", "--now", "2026-09-24 08:01"]
                builtins.open = racing_open
                code = mod.main()
            finally:
                builtins.open = real_open
                sys.argv = argv
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            text = read(dd)
            rows = [ln for ln in text.splitlines() if ln.startswith("- 08:0x Pulse escalation")]
            return (code == 0 and text.startswith("# made by the other session") and len(rows) == 1
                    and "2 keys with no decision row" in rows[0] and "pulse:w-988c72a9" in rows[0]
                    and "pulse:w-83436e11" in rows[0] and "pulse:maestro-route" not in rows[0])
        check("a day file made by another session between the read and the write gets the row appended, without the "
              "keys it already names", file_made_between_read_and_write)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of %d OK" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
