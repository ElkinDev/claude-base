"""Pins for the two register row shapes (2026-09-22): scripts/row.sh writes `YYYY-MM-DD HH:Mx [kind] ...` with no
leading "- ", hand rows until 2026-09-17 carry it, and every reader must see both. Before the fix the recovery hook
and the state sheet printed 09-17 rows as the newest rulings, and the rework reader missed every row.sh row.

    python scripts/tests/test-register-row-shapes.py      # against the kit files
    COMPACT_RECOVER_PY=<p> LANE_STATE_PY=<p> REWORK_SHAPE_PY=<p> TOKEN_SHAPE_PY=<p> TURN_TYPING_PY=<p> \\
        OWNER_ASKED_PY=<p> python test-register-row-shapes.py

Each module is loaded by path and called on a temp register; nothing reads the real register or runs a hook.
"""
import datetime
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.join(HERE, "..", "..")
PATHS = {
    "hook": os.environ.get("COMPACT_RECOVER_PY") or os.path.join(KIT, "claude", "hooks", "compact-recover.py"),
    "lane": os.environ.get("LANE_STATE_PY") or os.path.join(KIT, "claude", "tools", "lane-state.py"),
    "rework": os.environ.get("REWORK_SHAPE_PY") or os.path.join(HERE, "..", "rework-shape.py"),
    "token": os.environ.get("TOKEN_SHAPE_PY") or os.path.join(HERE, "..", "token-shape.py"),
    "turn": os.environ.get("TURN_TYPING_PY") or os.path.join(HERE, "..", "turn-typing.py"),
    "asked": os.environ.get("OWNER_ASKED_PY") or os.path.join(HERE, "..", "owner-asked.py"),
}
REGISTER = (
    "# Rulings register\n"
    "2026-09-16 08:0x [decision] EARLY bare row, older than every dash row.\n"
    "- 2026-09-17 14:2x [analyst] OLDEST dash row.\n"
    "2026-09-20 09:1x [diagnosis] MIDDLE bare row, as row.sh writes it.\n"
    "- 2026-09-17 14:0x [owner] OLDER dash row written after a later one.\n"
    "not a row, a note line\n"
    "2026-09-22 18:3x [owner] NEWEST bare owner row. (analyst pane 18:3x)\n"
)


def load(key):
    path = PATHS[key]
    loader = importlib.machinery.SourceFileLoader("pin_" + key, path)
    spec = importlib.util.spec_from_loader("pin_" + key, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def main():
    tmp = tempfile.mkdtemp(prefix="row-shape-pin-")
    reg = os.path.join(tmp, "rulings.md")
    open(reg, "w", encoding="utf-8").write(REGISTER)
    results = []

    def check(name, fn):
        try:
            ok = bool(fn())
            note = ""
        except Exception as e:
            ok, note = False, " (%s: %s)" % (type(e).__name__, str(e)[:90])
        results.append(ok)
        print(("OK   " if ok else "FAIL ") + name + note)

    try:
        def hook_block():
            h = load("hook")
            os.environ["CLAUDE_RULINGS_FILE"] = reg
            block = h.rulings_block()
            rows = [ln for ln in block.splitlines() if "row" in ln and ln[:1] in "-2"]
            return (len(rows) == 5 and "EARLY bare row" in rows[0] and "OLDER dash row" in rows[1]
                    and "MIDDLE bare row" in rows[-2] and "NEWEST bare owner row" in rows[-1])
        check("hook: both shapes are rulings, sorted by stamp, the newest bare row last", hook_block)

        def lane_rows():
            ls = load("lane")
            rows = ls.rulings_rows(reg)
            return (len(rows) == 5 and "EARLY bare row" in rows[0] and "OLDER dash row" in rows[1]
                    and "NEWEST bare owner row" in rows[-1])
        check("state sheet: both shapes, sorted by stamp", lane_rows)

        def keys_agree():
            h, ls = load("hook"), load("lane")
            a = "- 2026-09-20 09:1x [x] dash"
            b = "2026-09-20 09:1x [x] bare"
            return h.ruling_key(a) == h.ruling_key(b) == ls.ruling_key(a) == ls.ruling_key(b) == ("2026-09-20", "09:1x")
        check("sort key reads date and hour from either shape", keys_agree)

        def rework_rows():
            rw = load("rework")
            rw.RULINGS = reg
            rows = rw.register_rows(datetime.datetime(2026, 9, 16, 0, 0), datetime.datetime(2026, 9, 22, 23, 59))
            return len(rows) == 5 and any("NEWEST bare owner row" in r for r in rows)
        check("rework reader: both shapes inside the window", rework_rows)

        def token_shapes():
            ts = load("token")
            only = ts.shape_of('bash /repo/scripts/row.sh owner "text" "analyst pane"')
            two = ts.shape_of('bash /repo/scripts/row.sh owner "a" "analyst pane" && bash /repo/scripts/row.sh measurement "b" "analyst pane"')
            act = ts.shape_of('git merge --ff-only lane-x && bash /repo/scripts/row.sh analyst "landed" "analyst pane"')
            plain = ts.shape_of("cat /repo/law.md")
            semi = ts.shape_of('bash /repo/scripts/row.sh diagnosis "a; b && c" "analyst pane"')
            return ((only, two, act, plain, semi)
                    == ("register-row-only", "register-row-only", "register-row-with-action", "cat", "register-row-only"))
        check("token reader: row.sh calls counted, alone or with their action; ; and && in the text do not split",
              token_shapes)

        def turn_shapes():
            tt = load("turn")
            only = tt.bcat('bash /repo/scripts/row.sh diagnosis "a row; with a semicolon" "analyst pane"')
            act = tt.bcat('git merge --ff-only lane-x && '
                          'bash /repo/scripts/row.sh analyst "landed" "analyst pane"')
            grep = tt.bcat('grep -n "18:5x" /repo/rulings.md')
            old = tt.bcat("T=$(date +%H:%M); printf '%s\\n' \"- 2026-09-10 $T [x] y\" >> /repo/rulings.md")
            return (only, act, grep, old) == ("register-row-only", "register-row-with-action", "register-read",
                                              "register-row-with-action")
        check("nightly classifier: row.sh rows counted, a register grep is a read", turn_shapes)

        def heredoc_bodies():
            ts, tt = load("token"), load("turn")
            hidden = ("cat > drafts/x.md <<'EOF'\nthe launcher's line and Invoke-Step's\nEOF\n"
                      'bash /repo/scripts/row.sh owner "Owner 17:08" "analyst pane"')
            invented = ("python - <<'PYEOF'\nact = tt.bcat('python a && bash /repo/scripts/row.sh "
                        "analyst \"x\" \"p\"')\nPYEOF")
            return (ts.shape_of(hidden) == "register-row-with-action" and tt.bcat(hidden) == "register-row-with-action"
                    and not ts.shape_of(invented).startswith("register-row")
                    and not tt.bcat(invented).startswith("register-row"))
        check("a heredoc body neither hides a row after it nor invents one inside it", heredoc_bodies)

        def asked_bare_owner_row():
            import subprocess
            mem = os.path.join(tmp, "mem-empty")
            os.makedirs(mem, exist_ok=True)
            env = dict(os.environ, OWNER_ASKED_ROOT=tmp, OWNER_ASKED_MEMORY=mem)
            r = subprocess.run([sys.executable, PATHS["asked"], "NEWEST", "bare", "owner"], capture_output=True,
                               env=env, timeout=60)
            return r.returncode == 0 and b"rulings.md:7:" in r.stdout
        check("owner-asked finds a bare [owner] row", asked_bare_owner_row)
    finally:
        os.environ.pop("CLAUDE_RULINGS_FILE", None)
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of %d OK" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
