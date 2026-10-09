"""Tests for claude/hooks/ask-inline.py, the PreToolUse hook that denies the question selector.

    python claude/hooks/tests/test-ask-inline.py

The hook runs the way the harness runs it: a subprocess with the PreToolUse payload on stdin. ASK_INLINE_LOG points
into a temporary folder whose name holds a space, so no real log is touched. Exit 0 when every case passes, 1
otherwise.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(HERE), "ask-inline.py")
TMP = tempfile.mkdtemp(prefix="ask inline ")
LOG = os.path.join(TMP, "ask.log")
results = []


def run(payload, **extra):
    env = dict(os.environ, ASK_INLINE_LOG=LOG)
    env.pop("ASK_INLINE_ALLOW", None)
    env.update(extra)
    data = payload if isinstance(payload, str) else json.dumps(payload)
    p = subprocess.run([sys.executable, HOOK], input=data, capture_output=True, text=True, env=env, timeout=30)
    return p.returncode, p.stdout.strip()


def log_lines():
    if not os.path.exists(LOG):
        return []
    with open(LOG, encoding="utf-8") as h:
        return h.read().splitlines()


def case(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else ": " + detail))


def ask(n=2, session="0123456789abcdef", cwd="C:/work/project one"):
    qs = [{"question": "Which cut, private words here?", "header": "Cut", "multiSelect": False,
           "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]}] * n
    return {"session_id": session, "cwd": cwd, "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
            "tool_input": {"questions": qs}}


try:
    code, out = run(ask())
    decision = json.loads(out)["hookSpecificOutput"] if out else {}
    case("the selector is denied", code == 0 and decision.get("permissionDecision") == "deny"
         and decision.get("hookEventName") == "PreToolUse", out)
    reason = decision.get("permissionDecisionReason", "")
    case("the reason says how to ask", all(w in reason for w in ("reply text", "numbered", "recommended",
                                                                  "reversible", "wait")), reason)
    lines = log_lines()
    case("one log line per denial, session prefix, count and folder, no question text",
         len(lines) == 1 and " 01234567 questions=2 C:/work/project one" in lines[0]
         and "private" not in lines[0], repr(lines))

    run(ask(n=1, session="ff"))
    case("a second denial appends", len(log_lines()) == 2 and "ff questions=1" in log_lines()[1], repr(log_lines()))

    code, out = run(dict(ask(), tool_name="Bash"))
    case("another tool passes silently", (code, out) == (0, "") and len(log_lines()) == 2, out)

    code, out = run(ask(), ASK_INLINE_ALLOW="1")
    case("ASK_INLINE_ALLOW=1 lets the selector through, no log", (code, out) == (0, "") and len(log_lines()) == 2, out)

    code, out = run("not json {")
    case("bad input fails open", (code, out) == (0, ""), out)

    code, out = run('["a list"]')
    case("a payload that is not an object fails open", (code, out) == (0, ""), out)

    code, out = run(dict(ask(), tool_input={"questions": "odd"}))
    case("an odd input is still denied and logged with no count",
         "deny" in out and log_lines()[-1].split()[3] == "questions=-", repr(log_lines()[-1:]))

    code, out = run(dict(ask(), tool_input=["q"]))
    case("a tool input that is a list is still denied", code == 0 and '"deny"' in out, out)

    code, out = run('{"tool_name": "AskUserQuestion", "cwd": "C:/x\\udc80", "session_id": "s1"}')
    case("a folder name the log cannot encode is still denied and logged",
         code == 0 and '"deny"' in out and log_lines()[-1].split()[2] == "s1", out + repr(log_lines()[-1:]))

    code, out = run(ask())
    case("the reason asks for a bounded wait on a reversible one", "bounded wait" in out, out)

    code, out = run(ask(), ASK_INLINE_LOG=os.path.join(TMP, "no such folder", "x.log"))
    case("a log that cannot be written still denies", code == 0 and '"deny"' in out, out)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("%d of %d passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
