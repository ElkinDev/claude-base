"""Tests for landing.py and board.py. No network, no real board, no real landings file.

Every case points CLAUDE_LANDINGS_FILE and CLAUDE_BOARD_DIR at a fresh temporary
directory, so the suite can never touch the files the owner reads. The hook is run
the way the harness runs it: as a subprocess with the fixture JSON on stdin.

    python run-tests.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
LANDING = os.path.join(os.path.dirname(HERE), "landing.py")

BOARD_CANDIDATES = [
    os.environ.get("BOARD_PY") or "",
    os.path.normpath(os.path.join(HERE, "..", "..", "..", "scripts", "board.py")),
    r"C:\Repo\claude-base\scripts\board.py",
]


def board_script():
    for candidate in BOARD_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise AssertionError("board.py not found. Set BOARD_PY to its path.")


def clean_env(**extra):
    env = os.environ.copy()
    for name in (
        "HERDR_ENV",
        "CLAUDE_ROLE",
        "CLAUDE_LANDINGS_FILE",
        "CLAUDE_BOARD_DIR",
        "CLAUDE_LANDINGS_ALL",
    ):
        env.pop(name, None)
    for name, value in extra.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def fixture(name, tmp):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        text = handle.read()
    text = text.replace("{FIXTURES}", FIXTURES.replace("\\", "/"))
    text = text.replace("{TMP}", tmp.replace("\\", "/"))
    json.loads(text)  # the fixture must stay valid JSON after substitution
    return text


def run_hook(name, tmp, landings, role=None, herdr=None, land_all=None):
    env = clean_env(
        CLAUDE_LANDINGS_FILE=landings,
        CLAUDE_ROLE=role,
        HERDR_ENV=herdr,
        CLAUDE_LANDINGS_ALL=land_all,
    )
    done = subprocess.run(
        [sys.executable, LANDING],
        input=fixture(name, tmp).encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=tmp,
        timeout=60,
    )
    assert done.returncode == 0, "the hook must never fail: %r" % done.stderr[-400:]
    return done


def rows(landings):
    if not os.path.isfile(landings):
        return []
    out = []
    for line in open(landings, encoding="utf-8"):
        line = line.strip()
        if not line.startswith("|") or line.startswith("| time |") or set(line) <= set("| -"):
            continue
        out.append([cell.strip() for cell in line.strip("|").split("|")])
    return out


def run_board(script, args, board_dir, landings):
    env = clean_env(CLAUDE_BOARD_DIR=board_dir, CLAUDE_LANDINGS_FILE=landings)
    done = subprocess.run(
        [sys.executable, script] + args,
        capture_output=True,
        env=env,
        timeout=60,
    )
    return done


# --- landing.py -------------------------------------------------------------


def test_subagent_stop_lands_one_row_with_the_agent_summary(tmp):
    landings = os.path.join(tmp, "landings.md")
    run_hook("subagent-stop.json", tmp, landings)
    got = rows(landings)
    assert len(got) == 1, "expected one row, got %d" % len(got)
    time, event, role, where, ident, summary = got[0]
    assert event == "SubagentStop", event
    assert ident == "abcdef12", ident
    assert summary.startswith("[general-purpose] Lane w99 landed."), summary
    assert "/ 12 tests passed" in summary, "pipes must become slashes: %s" % summary
    assert "system-reminder" not in summary, summary
    assert "must not be used" not in summary, "the agent transcript wins: %s" % summary
    assert len(time) == 19, time


def test_an_untyped_subagent_is_noise_and_does_not_land(tmp):
    """The harness fires SubagentStop for its own internal agents, about one per
    tool call. They carry no agent_type, and they would bury the real landings."""
    landings = os.path.join(tmp, "landings.md")
    run_hook("subagent-stop-untyped.json", tmp, landings)
    assert rows(landings) == [], "an untyped subagent stop must not land"
    run_hook("subagent-stop-untyped.json", tmp, landings, land_all="1")
    got = rows(landings)
    assert len(got) == 1, "CLAUDE_LANDINGS_ALL=1 lands it again: %r" % got
    assert got[0][4] == "00112233", got[0]


def test_the_header_is_written_once(tmp):
    landings = os.path.join(tmp, "landings.md")
    run_hook("subagent-stop.json", tmp, landings)
    run_hook("subagent-stop.json", tmp, landings)
    text = open(landings, encoding="utf-8").read()
    assert text.count("| time | event |") == 1, "one header only"
    assert len(rows(landings)) == 2, "both landings kept"


def test_stop_does_not_land_without_the_lane_role(tmp):
    landings = os.path.join(tmp, "landings.md")
    run_hook("stop.json", tmp, landings, role="orchestrator")
    run_hook("stop.json", tmp, landings, role=None)
    assert rows(landings) == [], "an orchestrator turn must never land"


def test_stop_without_notes_does_not_land(tmp):
    """A session that inherited the default role but never wrote NOTES.md is
    the owner talking, not a lane; its turns stay out of the landings file."""
    landings = os.path.join(tmp, "landings.md")
    run_hook("stop.json", tmp, landings, role="lane")
    assert rows(landings) == [], "a lane role without NOTES.md must not land"


def test_stop_lands_for_a_lane(tmp):
    landings = os.path.join(tmp, "landings.md")
    with open(os.path.join(tmp, "NOTES.md"), "w", encoding="utf-8") as handle:
        handle.write("# NOTES\n")
    run_hook("stop.json", tmp, landings, role="lane")
    got = rows(landings)
    assert len(got) == 1, len(got)
    assert got[0][1] == "Stop", got[0]
    assert got[0][2] == "lane", got[0]
    assert got[0][4] == "99998888", got[0]
    assert got[0][5] == "Wave closed. Two files written and the gate is queued.", got[0]


def test_stop_hook_active_never_lands(tmp):
    landings = os.path.join(tmp, "landings.md")
    run_hook("stop-hook-active.json", tmp, landings, role="lane")
    assert rows(landings) == [], "a blocked turn must never land"


def test_task_completed_lands_the_subject(tmp):
    landings = os.path.join(tmp, "landings.md")
    run_hook("task-completed.json", tmp, landings)
    got = rows(landings)
    assert len(got) == 1, len(got)
    assert got[0][1] == "TaskCompleted", got[0]
    assert got[0][4] == "77665544", got[0]
    assert got[0][5].startswith("Day 0 step 7: landing hooks"), got[0]
    assert "description" not in got[0][5], got[0]


def test_task_completed_accepts_the_documented_spelling(tmp):
    landings = os.path.join(tmp, "landings.md")
    run_hook("task-completed-docs-spelling.json", tmp, landings)
    got = rows(landings)
    assert len(got) == 1, len(got)
    assert got[0][5] == "Union of the four landed branches", got[0]


def load_landing_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("landing_under_test", LANDING)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_herdr_is_skipped_without_herdr_env(tmp):
    module = load_landing_module()
    calls = []
    original = module.subprocess.run

    def recorder(argv, **kwargs):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    module.subprocess.run = recorder
    try:
        os.environ.pop("HERDR_ENV", None)
        assert module.notify("w99 landed") is False, "no notification without HERDR_ENV"
        assert calls == [], "nothing may be executed: %r" % calls

        os.environ["HERDR_ENV"] = "1"
        called = module.notify("w99 landed")
        if shutil.which("herdr"):
            assert called is True, "with HERDR_ENV=1 and herdr on PATH it must fire"
            assert calls and calls[0][:3] == ["herdr", "notification", "show"], calls
            assert "--sound" in calls[0] and "done" in calls[0], calls
        else:
            assert called is False, "no herdr on PATH means no notification"
    finally:
        module.subprocess.run = original
        os.environ.pop("HERDR_ENV", None)


# --- board.py ---------------------------------------------------------------


def test_board_set_creates_the_lane(tmp):
    script = board_script()
    board_dir = os.path.join(tmp, "board")
    os.makedirs(board_dir)
    landings = os.path.join(tmp, "landings.md")
    done = run_board(script, ["set", "w99", "branch", "r42-f99-w1"], board_dir, landings)
    assert done.returncode == 0, done.stderr
    run_board(script, ["set", "w99", "status", "pilot pending"], board_dir, landings)
    state = json.load(open(os.path.join(board_dir, "board.json"), encoding="utf-8"))
    assert state["lanes"]["w99"]["branch"] == "r42-f99-w1", state
    assert state["lanes"]["w99"]["status"] == "pilot pending", state
    page = open(os.path.join(board_dir, "board.md"), encoding="utf-8").read()
    assert "r42-f99-w1" in page and "pilot pending" in page, page
    bad = run_board(script, ["set", "w99", "nonsense", "x"], board_dir, landings)
    assert bad.returncode == 2, "an unknown field is a usage error"


def test_board_add_landing_keeps_three(tmp):
    script = board_script()
    board_dir = os.path.join(tmp, "board")
    os.makedirs(board_dir)
    landings = os.path.join(tmp, "landings.md")
    for n in range(1, 5):
        done = run_board(script, ["add-landing", "w89", "landing %d" % n], board_dir, landings)
        assert done.returncode == 0, done.stderr
    state = json.load(open(os.path.join(board_dir, "board.json"), encoding="utf-8"))
    kept = [entry["text"] for entry in state["lanes"]["w89"]["landings"]]
    assert kept == ["landing 2", "landing 3", "landing 4"], kept
    page = open(os.path.join(board_dir, "board.md"), encoding="utf-8").read()
    assert "landing 4" in page and "landing 1" not in page, page


def test_board_show_renders_the_lanes_and_the_pointers(tmp):
    script = board_script()
    board_dir = os.path.join(tmp, "board")
    os.makedirs(board_dir)
    landings = os.path.join(tmp, "landings.md")
    run_board(script, ["set", "main", "status", "Day 0 in progress"], board_dir, landings)
    run_board(script, ["note", "the gate daemon is next"], board_dir, landings)
    done = run_board(script, ["show"], board_dir, landings)
    assert done.returncode == 0, done.stderr
    out = done.stdout.decode("utf-8", "replace")
    assert "| lane | branch | tip | status | next | owner action | last landing |" in out, out
    assert "Day 0 in progress" in out, out
    assert "the gate daemon is next" in out, out
    assert landings.replace("\\", "/") in out.replace("\\", "/"), out
    assert "ledger.md" in out, out
    assert "Rendered " in out.splitlines()[2], out


def test_board_note_keeps_ten(tmp):
    script = board_script()
    board_dir = os.path.join(tmp, "board")
    os.makedirs(board_dir)
    landings = os.path.join(tmp, "landings.md")
    for n in range(1, 13):
        run_board(script, ["note", "note %d" % n], board_dir, landings)
    state = json.load(open(os.path.join(board_dir, "board.json"), encoding="utf-8"))
    kept = [entry["text"] for entry in state["notes"]]
    assert len(kept) == 10, kept
    assert kept[0] == "note 3" and kept[-1] == "note 12", kept


def test_board_remove_drops_the_lane(tmp):
    script = board_script()
    board_dir = os.path.join(tmp, "board")
    os.makedirs(board_dir)
    landings = os.path.join(tmp, "landings.md")
    run_board(script, ["set", "w33", "branch", "r42-f33-w1"], board_dir, landings)
    run_board(script, ["set", "w98", "branch", "r42-f98-w1"], board_dir, landings)
    done = run_board(script, ["remove", "w33"], board_dir, landings)
    assert done.returncode == 0, done.stderr
    state = json.load(open(os.path.join(board_dir, "board.json"), encoding="utf-8"))
    assert "w33" not in state["lanes"] and "w98" in state["lanes"], state
    page = open(os.path.join(board_dir, "board.md"), encoding="utf-8").read()
    assert "r42-f33-w1" not in page and "r42-f98-w1" in page, page
    missing = run_board(script, ["remove", "w33"], board_dir, landings)
    assert missing.returncode == 1, "removing an absent lane reports, it does not crash"


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    passed, failed = 0, []
    for test in tests:
        tmp = tempfile.mkdtemp(prefix="landing-tests-")
        try:
            test(tmp)
            passed += 1
            print("pass  %s" % test.__name__)
        except Exception as error:
            failed.append((test.__name__, error))
            print("FAIL  %s: %s" % (test.__name__, error))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("")
    print("%d passed, %d failed, %d total" % (passed, len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
