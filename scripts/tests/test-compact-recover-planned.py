"""Pins for the planned ledger block of claude/hooks/compact-recover.py and the session start's room split: the
planned seat gets the ledger module's block after the open asks and before the pulse (PLANNED_ROOM at a session start,
PLANNED_COMPACT_ROOM with compact=True after a compaction), a subagent, another seat or a folder off the board gets
none; no module at the default path prints nothing, a configured module that is missing or raises prints one line;
the rulings take the room the seat block, the asks and the planned block leave under CAP, less the pulse's minimum.

    python test-compact-recover-planned.py
    COMPACT_RECOVER_PY=<p> python test-compact-recover-planned.py    # against a staged copy

The hook runs from a copy in a temp folder with every board input pointed into it (the board root, the briefs, the
asks, the register, the checkpoints, the pulse and the ledger module are all stubs or files there), so nothing of the
live board is read or written.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.environ.get("COMPACT_RECOVER_PY") or os.path.join(os.path.dirname(os.path.dirname(HERE)), "claude", "hooks",
                                                            "compact-recover.py")
CAP = 9000
STUB_HEAD = "Planned ledger (stub)"
LEDGER_STUB = (
    "import os\n"
    "def session_block(room, compact=False):\n"
    "    n = int(os.environ.get('PLANNED_LEN') or 0)\n"
    "    head = 'Planned ledger (stub) room=%d compact=%s' % (room, compact)\n"
    "    return head + '\\n' + 'x' * (n - len(head) - 1) if n else head + '\\n#3 a dated row | 11-25 | armed | post'\n"
)
EMPTY_STUB = "def session_block(room, compact=False):\n    return ''\n"
TRACES = ("Pulse (python ", "[pulse cut to fit", "[pulse dropped for room")
HOOK_COPY = ""


def stub(folder, name, body):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + ".py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def run(args, payload, env):
    r = subprocess.run([sys.executable, HOOK_COPY] + args, input=json.dumps(payload), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=60)
    return r.returncode, r.stdout


def ruling(i, size):
    row = "2026-09-24 07:%02d [decision] " % i
    return row + "r" * (size - len(row)) + "\n"


def order(out, *marks):
    at = [out.find(m) for m in marks]
    return all(a >= 0 for a in at) and at == sorted(at)


def ledger_part(out):
    i = out.find(STUB_HEAD)
    return out[i:].split("\n\n", 1)[0] if i >= 0 else ""


def main():
    global HOOK_COPY
    tmp = tempfile.mkdtemp(prefix="recovery-planned-")
    results = []

    def check(name, fn):
        try:
            ok, note = bool(fn()), ""
        except Exception as e:
            ok, note = False, " (%s: %s)" % (type(e).__name__, str(e)[:90])
        results.append(ok)
        print(("OK   " if ok else "FAIL ") + name + note)

    try:
        HOOK_COPY = shutil.copy(HOOK, os.path.join(tmp, "compact-recover.py"))
        for d in ("drafts", "full", "checkpoints", "briefs", "home"):
            os.makedirs(os.path.join(tmp, d))
        open(os.path.join(tmp, "drafts", "owner-decisions-2026-09-24.md"), "w", encoding="utf-8").write(
            "# h\n- 07:1x Owner class, waiting on the owner: the pinned ask row.\n")
        open(os.path.join(tmp, "full", "owner-decisions-2026-09-24.md"), "w", encoding="utf-8").write(
            "# h\n" + "".join("- 07:%02d Owner class, waiting on the owner: %s\n" % (i, "a" * 190) for i in range(12)))
        reg = os.path.join(tmp, "rulings.md")
        open(reg, "w", encoding="utf-8").write("".join(ruling(i, 60) for i in range(1, 4)))
        big = os.path.join(tmp, "big-rulings.md")
        open(big, "w", encoding="utf-8").write("".join(ruling(i, 380) for i in range(40)))
        rows200 = os.path.join(tmp, "rows200.md")
        open(rows200, "w", encoding="utf-8").write("".join(ruling(i, 200) for i in range(40)))
        ledger = stub(tmp, "ledger", LEDGER_STUB)
        empty = stub(tmp, "empty", EMPTY_STUB)
        pulse = stub(tmp, "pulse", "print('p')\nprint('ITEM 1 [pulse:w-00000001]')\n")
        base = dict(os.environ, CLAUDE_DECISIONS_GLOB=os.path.join(tmp, "drafts", "owner-decisions-*.md"),
                    CLAUDE_RULINGS_FILE=reg, CLAUDE_CHECKPOINT_DIR=os.path.join(tmp, "checkpoints"),
                    CLAUDE_ROLE="analyst", CLAUDE_BOARD_ROOT=tmp, CLAUDE_BRIEFS_DIR=os.path.join(tmp, "briefs"),
                    CLAUDE_LANE_STATE_SCRIPT=os.path.join(tmp, "no-lane-state.py"),
                    CLAUDE_LANE_STATE_SHEET=os.path.join(tmp, "law.md"), PYTHONIOENCODING="utf-8",
                    CLAUDE_PULSE_PY=pulse, CLAUDE_PLANNED_PY=ledger)
        for name in ("CLAUDE_LANDINGS_FILE", "CLAUDE_BOARD_PREFIXES", "PLANNED_LEN"):
            base.pop(name, None)
        full = dict(base, CLAUDE_DECISIONS_GLOB=os.path.join(tmp, "full", "owner-decisions-*.md"))
        start = {"session_id": "pin00000-0000", "cwd": tmp, "source": "startup"}
        compact = dict(start, source="compact")
        agent = dict(start, agent_id="a1", agent_type="reviewer")

        def start_entry():
            rc, out = run(["--rulings"], start, base)
            return (rc == 0 and STUB_HEAD + " room=2800 compact=False\n#3 a dated row" in out
                    and order(out, "Open owner asks", STUB_HEAD, "Pulse (python ", "Rulings (last"))
        check("session start: the ledger block whole at room 2800, after the asks and before the pulse and the rulings",
              start_entry)

        def compaction_entry():
            rc, out = run([], compact, base)
            return (rc == 0 and STUB_HEAD + " room=1200 compact=True" in out
                    and order(out, "Open owner asks", STUB_HEAD, "Pulse (python ", "Rulings (last"))
        check("compaction: the ledger block at room 1200 with compact=True, after the asks and before the pulse",
              compaction_entry)

        def nobody_else():
            off = dict(base, CLAUDE_BOARD_ROOT=os.path.join(tmp, "board"))
            orch = dict(base, CLAUDE_ROLE="orchestrator")
            outs = [run(a, agent, base)[1] for a in (["--rulings"], [])]
            seated = [run(a, p, orch)[1] for a, p in ((["--rulings"], start), ([], compact))]
            outs += seated + [run(a, p, off)[1] for a, p in ((["--rulings"], start), ([], compact))]
            return all("Planned ledger" not in o for o in outs) and all("Rulings (last" in o for o in seated)
        check("an agent, another seat and a folder off the board get no ledger block at either entry", nobody_else)

        def default_path():
            home = os.path.join(tmp, "home")
            env = dict(base, USERPROFILE=home, HOME=home)
            env.pop("CLAUDE_PLANNED_PY")
            none = run(["--rulings"], start, env)[1]
            stub(os.path.join(home, ".claude", "tools"), "planned-check", LEDGER_STUB)
            found = run(["--rulings"], start, env)[1]
            return ("Planned ledger" not in none and "Rulings (last" in none
                    and STUB_HEAD + " room=2800 compact=False" in found)
        check("no module at the default path prints nothing; a module there is read", default_path)

        def faults_are_loud():
            cases = {"FileNotFoundError": os.path.join(tmp, "gone.py"),
                     "RuntimeError": stub(tmp, "raises", "def session_block(room, compact=False):\n    raise RuntimeError('x')\n"),
                     "SystemExit": stub(tmp, "exits", "import sys\nsys.exit(3)\n")}
            ok = []
            for kind, path in cases.items():
                out = run(["--rulings"], start, dict(base, CLAUDE_PLANNED_PY=path))[1]
                ok.append("Planned ledger: the block failed (%s), run python %s" % (kind, path) in out
                          and "Rulings (last" in out)
            return all(ok)
        check("a configured module that is missing, raises or exits prints one failed line naming it, the rulings stay",
              faults_are_loud)

        def full_day_room_split():
            out = run(["--rulings"], start, dict(full, CLAUDE_RULINGS_FILE=big, PLANNED_LEN="2800"))[1]
            return (len(out) <= CAP and "[recovery output capped]" not in out and len(ledger_part(out)) == 2800
                    and ruling(39, 380).rstrip() in out and "[rulings cut, open the file]" in out
                    and any(t in out for t in TRACES))
        check("asks and ledger at their rooms and a long register: under CAP with no cap cut, the ledger whole, the "
              "newest ruling whole, the oldest given up, a pulse trace", full_day_room_split)

        def overrunning_module():
            out = run(["--rulings"], start, dict(full, CLAUDE_RULINGS_FILE=big, PLANNED_LEN="5000"))[1]
            part = ledger_part(out)
            return (len(out) <= CAP and "[recovery output capped]" not in out and len(part) == 2800
                    and part.endswith("\n[planned ledger cut, open the ledger]") and ruling(39, 380).rstrip() in out)
        check("a module that overruns its room is cut to 2800 with its marker and leaves the rulings their room",
              overrunning_module)

        def pulse_keeps_its_minimum():
            # 200-character register rows, and a ledger length picked so that a room split without the pulse's minimum
            # leaves the rulings block 5 characters short of its room (no pulse line fits there), while the split with
            # it gives up one more row and leaves the pulse 206 characters.
            env = dict(full, CLAUDE_RULINGS_FILE=rows200, CLAUDE_PLANNED_PY=empty,
                       CLAUDE_PULSE_PY=os.path.join(tmp, "no-pulse.py"))
            probe = run(["--rulings"], start, env)[1]
            top = probe.find("Rulings (last")  # the seat block and the asks with their joins
            heading = len(probe[top:].split("\n", 1)[0])
            k = (CAP - top - 4 - 2000 - heading - 29 - 5) // 201
            length = CAP - top - 4 - (heading + 29 + k * 201 + 5)
            out = run(["--rulings"], start, dict(env, CLAUDE_PLANNED_PY=ledger, PLANNED_LEN=str(length),
                                                    CLAUDE_PULSE_PY=pulse))[1]
            return (top > 0 and 0 < k < 30 and len(ledger_part(out)) == length <= 2800 and len(out) <= CAP
                    and any(t in out for t in TRACES) and ruling(39, 200).rstrip() in out)
        check("a ledger and rulings that fill the room still leave the pulse its minimum at the session start",
              pulse_keeps_its_minimum)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of %d OK" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
