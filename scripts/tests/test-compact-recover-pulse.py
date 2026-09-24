"""Pins for the pulse block of claude/hooks/compact-recover.py: the hook prints tools/pulse.py's block after the open asks and before the rulings, at most PULSE_MAX lines at a
session start and PULSE_COMPACT_MAX at a compaction, trimmed to the room left under CAP, never to an agent and never
off the board; a missing, failing or hanging pulse prints nothing and costs the start at most its bound.

    python test-recovery-pulse.py
    COMPACT_RECOVER_PY=<p> python test-recovery-pulse.py    # against a staged copy

The hook runs from a copy in a temp folder with every board input pointed into it; the pulse is a stub script there
(CLAUDE_PULSE_PY), so nothing of the live board is read or written.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.environ.get("COMPACT_RECOVER_PY") or os.path.join(os.path.dirname(os.path.dirname(HERE)), "claude", "hooks",
                                                            "compact-recover.py")
ASK = "- 07:1x Owner class, waiting on the owner: the pinned ask row.\n"
RULING = "2026-09-24 07:0%d [decision] Pinned ruling %d. (analyst pane 07:0x)\n"


def stub(tmp, name, body):
    path = os.path.join(tmp, name + ".py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def run(hook, args, payload, env, timeout=60):
    t = time.time()
    r = subprocess.run([sys.executable, hook] + args, input=json.dumps(payload), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=timeout)
    return r.returncode, r.stdout, time.time() - t


def pulse_part(out):
    """The pulse block's lines (heading included), or [] when absent."""
    i = out.find("Pulse (python ")
    if i < 0:
        return []
    return out[i:].split("\n\n", 1)[0].split("\n")


def main():
    tmp = tempfile.mkdtemp(prefix="recovery-pulse-")
    results = []

    def check(name, fn):
        try:
            ok, note = bool(fn()), ""
        except Exception as e:
            ok, note = False, " (%s: %s)" % (type(e).__name__, str(e)[:90])
        results.append(ok)
        print(("OK   " if ok else "FAIL ") + name + note)

    try:
        hooks = os.path.join(tmp, "hooks")
        os.makedirs(hooks)
        hook = shutil.copy(HOOK, os.path.join(hooks, "compact-recover.py"))
        ddir = os.path.join(tmp, "drafts")
        os.makedirs(ddir)
        open(os.path.join(ddir, "owner-decisions-2026-09-24.md"), "w", encoding="utf-8").write("# header\n" + ASK)
        reg = os.path.join(tmp, "rulings.md")
        open(reg, "w", encoding="utf-8").write("".join(RULING % (i, i) for i in range(1, 4)))
        cks = os.path.join(tmp, "checkpoints")
        os.makedirs(cks)
        fifteen = stub(tmp, "fifteen", "import sys\nprint('pulse 2026-09-24 07:00: summary line')\n"
                                        "[print('ITEM %d [pulse:w-%08d]' % (i, i)) for i in range(1, 15)]\n")
        base = dict(os.environ, CLAUDE_DECISIONS_GLOB=os.path.join(ddir, "owner-decisions-*.md"),
                    CLAUDE_RULINGS_FILE=reg, CLAUDE_CHECKPOINT_DIR=cks, CLAUDE_ROLE="analyst",
                    CLAUDE_LANE_STATE_SCRIPT=os.path.join(tmp, "no-lane-state.py"),
                    CLAUDE_LANE_STATE_SHEET=os.path.join(tmp, "law.md"), PYTHONIOENCODING="utf-8",
                    CLAUDE_PULSE_PY=fifteen)
        for name in ("CLAUDE_BRIEFS_DIR", "CLAUDE_LANDINGS_FILE", "CLAUDE_BOARD_ROOT", "CLAUDE_BOARD_PREFIXES"):
            base.pop(name, None)
        payload = {"session_id": "pin00000-0000", "cwd": tmp, "source": "startup"}
        compact = dict(payload, source="compact")
        agent = dict(payload, agent_id="a1", agent_type="reviewer")

        rc, out, _ = run(hook, ["--rulings"], payload, base)
        block = pulse_part(out)

        def start_order_and_cap():
            i_a, i_p, i_r = out.find("Open owner asks"), out.find("Pulse (python "), out.find("Rulings (last")
            return rc == 0 and 0 <= i_a < i_p < i_r and len(block) == 11 and block[1].startswith("pulse 2026")
        check("session start: asks, then the pulse (heading plus 10 of its 15 lines), then the rulings",
              start_order_and_cap)

        def compaction_three():
            rc2, out2, _ = run(hook, [], compact, base)
            b2 = pulse_part(out2)
            i_a, i_p, i_r = out2.find("Open owner asks"), out2.find("Pulse (python "), out2.find("Rulings (last")
            return rc2 == 0 and 0 <= i_a < i_p < i_r and len(b2) == 4
        check("compaction: the pulse is its heading plus 3 lines, between the asks and the rulings", compaction_three)

        def no_pulse_for_an_agent():
            return all("Pulse (python " not in run(hook, args, agent, base)[1] for args in (["--rulings"], []))
        check("an agent's payload gets no pulse at either entry", no_pulse_for_an_agent)

        def off_board():
            off = dict(payload, cwd=tmp)
            envo = dict(base, CLAUDE_BOARD_ROOT=os.path.join(tmp, "board"))
            return all("Pulse (python " not in run(hook, args, off, envo)[1] for args in (["--rulings"], []))
        check("a folder off the board gets no pulse at either entry", off_board)

        def missing_or_failing():
            outs = []
            for script in (os.path.join(tmp, "none.py"), stub(tmp, "exit2", "import sys\nprint('x')\nsys.exit(2)\n"),
                           stub(tmp, "silent", "")):
                rc3, o3, _ = run(hook, ["--rulings"], payload, dict(base, CLAUDE_PULSE_PY=script))
                outs.append(rc3 == 0 and "Pulse (python " not in o3 and "Rulings (last" in o3)
            return all(outs)
        check("a missing script, an exit 2 and an empty output print no pulse and keep the rulings", missing_or_failing)

        def exit_one_prints():
            s1 = stub(tmp, "exit1", "import sys\nprint('pulse head')\nprint('pulse: cannot read the ledger log x "
                                    "[pulse:log-missing]')\nsys.exit(1)\n")
            return "[pulse:log-missing]" in run(hook, ["--rulings"], payload, dict(base, CLAUDE_PULSE_PY=s1))[1]
        check("an exit 1 still prints: it names a missing input or a malformed line", exit_one_prints)

        def hang_is_bounded():
            sh = stub(tmp, "hang", "import time\ntime.sleep(30)\n")
            rc4, o4, took = run(hook, ["--rulings"], payload, dict(base, CLAUDE_PULSE_PY=sh))
            return rc4 == 0 and "Pulse (python " not in o4 and "Rulings (last" in o4 and took < 8
        check("a hanging pulse is cut at its bound (3 s): no block, the start stays under 8 s", hang_is_bounded)

        def room_under_cap():
            long_lines = stub(tmp, "long", "print('pulse head')\n[print('L%d ' % i + 'x' * 880) for i in range(12)]\n")
            big = os.path.join(tmp, "big-rulings.md")
            open(big, "w", encoding="utf-8").write("".join(
                "2026-09-24 07:%02d [decision] %s (analyst pane)\n" % (i, "r" * 380) for i in range(40)))
            env5 = dict(base, CLAUDE_PULSE_PY=long_lines, CLAUDE_RULINGS_FILE=big)
            _, o5, _ = run(hook, ["--rulings"], payload, env5)
            _, o6, _ = run(hook, [], compact, env5)
            b5, b6 = pulse_part(o5), pulse_part(o6)
            # the compaction entry cuts its whole output at CAP, so the pin also wants no cap marker there: an
            # overflowing pulse would pass a length check alone (review plsh r1 finding 1)
            return (len(o5) <= 9000 and len(o6) <= 9000 and b5 and b5[-1] == "[pulse cut to fit, run pulse.py]"
                    and b6 and 2 <= len(b6) <= 4 and "[recovery output capped]" not in o6
                    and "[recovery output capped]" not in o5 and "Rulings (last" in o5 and "Rulings (last" in o6)
        check("long pulse lines are trimmed to the room under CAP 9,000 at both entries, no cap cut, the block present",
              room_under_cap)

        def full_day_leaves_a_trace():
            # asks at ASKS_CAP and rulings near RULINGS_CAP (13 rows of 470 chars, 6,850) leave the pulse about
            # 1,040 chars. A summary of 962 chars fits with the cut marker (996) but not under the heading too, so
            # only the first fallback prints it; one of 1,232 does not fit at all, so only the second prints the
            # notice. Neither run may print the heading: the case proves the fallback, not the trimming (review
            # plsh r1 finding 3; the no-fallback mutant passed the earlier 420-char version of this case; the
            # marker after the summary is review plsh r2 note 1).
            dd = os.path.join(tmp, "full")
            os.makedirs(dd, exist_ok=True)
            open(os.path.join(dd, "owner-decisions-2026-09-24.md"), "w", encoding="utf-8").write(
                "# h\n" + "".join("- 07:%02d Owner class, waiting on the owner: %s\n" % (i, "a" * 190) for i in range(12)))
            big = os.path.join(tmp, "full-rulings.md")
            open(big, "w", encoding="utf-8").write("".join(
                "2026-09-24 07:%02d [decision] %s (analyst pane)\n" % (i, "r" * 470) for i in range(40)))
            outs = {}
            for n in (930, 1200):
                long_pulse = stub(tmp, "longsum%d" % n, "print('pulse 2026-09-24 07:00: summary ' + 's' * %d)\n"
                                                        "[print('L%%d ' %% i + 'x' * 880) for i in range(12)]\n" % n)
                env7 = dict(base, CLAUDE_PULSE_PY=long_pulse, CLAUDE_RULINGS_FILE=big,
                            CLAUDE_DECISIONS_GLOB=os.path.join(dd, "owner-decisions-*.md"))
                outs[n] = run(hook, ["--rulings"], payload, env7)[1]
            whole = all(len(o) <= 9000 and "Pulse (python " not in o and "Rulings (last" in o and "Open owner asks"
                        in o for o in outs.values())
            return (whole and "summary " + "s" * 930 + "\n[pulse cut to fit, run pulse.py]" in outs[930]
                    and "[pulse dropped for room" not in outs[930]
                    and "[pulse dropped for room, run python " in outs[1200] and "summary sss" not in outs[1200])
        check("a full day (asks and rulings at their caps): the pulse's summary with the cut marker, or else a dropped notice",
              full_day_leaves_a_trace)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of %d OK" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
