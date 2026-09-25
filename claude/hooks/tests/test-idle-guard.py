"""Tests for claude/hooks/idle-guard.py, the Stop hook that refuses one idle turn end of the orchestrator while a
work mutex still holds runs.

    python claude/hooks/tests/test-idle-guard.py

The hook runs the way the harness runs it: a subprocess with the Stop payload on stdin, against transcript fixtures
written into a temporary folder whose name holds a space. IDLE_GUARD_LOG, IDLE_GUARD_STATE and IDLE_GUARD_LOCK_ROOT
point into that folder and IDLE_GUARD_NOW stands in for the clock, so no real log, state, mutex or clock is touched.
Records are written the way the harness writes them, with no space after the colon. Exit 0 when every case passes,
1 otherwise.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(HERE), "idle-guard.py")
T0 = 1790319600.0  # 2026-09-25T07:00:00Z, the fixtures' default stamp; the hook's clock is T0 + 5 min
TMP = tempfile.mkdtemp(prefix="idle guard ")
ROOT = os.path.join(TMP, "root").replace("\\", "/")
results = []


def reset():
    shutil.rmtree(ROOT, ignore_errors=True)
    for sub in ("state", "lock/queue", "t"):
        os.makedirs(ROOT + "/" + sub, exist_ok=True)


def env(**extra):
    e = dict(os.environ, CLAUDE_ROLE="orchestrator", IDLE_GUARD_LOG=ROOT + "/guard.log",
             IDLE_GUARD_STATE=ROOT + "/state", IDLE_GUARD_LOCK_ROOT=ROOT + "/lock", IDLE_GUARD_NOW=str(T0 + 300))
    e.pop("IDLE_GUARD_HELD", None)
    e.pop("IDLE_GUARD_MAX_AGE", None)
    e.update(extra)
    return {k: v for k, v in e.items() if v is not None}


def run(stdin, **extra):
    p = subprocess.run([sys.executable, HOOK], input=stdin if isinstance(stdin, bytes) else stdin.encode("utf-8"),
                       capture_output=True, env=env(**extra), timeout=120)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def payload(path, sid="sess0001", active=False):
    return json.dumps({"session_id": sid, "transcript_path": path, "stop_hook_active": active, "hook_event_name": "Stop"})


def rec(**kw):
    kw.setdefault("timestamp", "2026-09-25T07:00:00.000Z")
    kw.setdefault("isSidechain", False)
    return json.dumps(kw, separators=(",", ":")) + "\n"


def agent(aid, desc="Lane x"):
    return rec(type="user", toolUseResult={"status": "async_launched", "agentId": aid, "description": desc})


def monitor(tid, ts="2026-09-25T07:00:00.000Z", ms=1800000, persistent=False):
    return rec(type="user", timestamp=ts, toolUseResult={"taskId": tid, "timeoutMs": ms, "persistent": persistent})


def shell(tid):
    return rec(type="user", toolUseResult={"stdout": "", "backgroundTaskId": tid})


def note(tid, status="completed", as_kind="user"):
    body = "<task-notification>\n<task-id>%s</task-id>\n<status>%s</status>\n</task-notification>" % (tid, status)
    if as_kind == "enqueue":
        return rec(type="queue-operation", operation="enqueue", content=body)
    if as_kind in ("remove", "dequeue"):
        return rec(type="queue-operation", operation=as_kind, content=body)
    if as_kind == "attach":
        return rec(type="attachment", attachment={"type": "queued_command", "prompt": body})
    return rec(type="user", message={"role": "user", "content": body})


def write(name, text):
    p = ROOT + "/t/" + name
    with open(p, "w", encoding="utf-8", newline="\n") as h:
        h.write(text)
    return p


def append(path, text):
    with open(path, "a", encoding="utf-8", newline="\n") as h:
        h.write(text)


def ticket(tag):
    open(ROOT + "/lock/queue/1-1790342712-2670885-%s.ticket" % tag, "w").close()


def check(label, cond, detail=""):
    results.append((label, bool(cond), detail))


def blocked(out):
    try:
        return json.loads(out).get("decision") == "block"
    except ValueError:
        return False


def state(sid="sess0001"):
    with open(ROOT + "/state/%s.json" % sid, encoding="utf-8") as h:
        return json.load(h)


def main():
    reset()
    for label, stdin in (("empty", ""), ("whitespace", "   \n"), ("malformed", "{not json"), ("a list", "[1,2]"),
                         ("no transcript", json.dumps({"session_id": "s"}))):
        code, out, err = run(stdin)
        check("H1 %s stdin passes silently" % label, code == 0 and out == "" and err == "", out[:40])

    # opt-in: no lock root named, or one that is no folder, guards nothing and writes nothing
    reset(); ticket("x-g1")
    t = write("optin.jsonl", rec(type="user", message={"content": "hi"}))
    code, out, _ = run(payload(t), IDLE_GUARD_LOCK_ROOT=None)
    check("opt-in: with no IDLE_GUARD_LOCK_ROOT every turn end passes", out == "" and not os.listdir(ROOT + "/state")
          and not os.path.exists(ROOT + "/guard.log"))
    code, out, _ = run(payload(t), IDLE_GUARD_LOCK_ROOT=ROOT + "/no such root")
    check("opt-in: a lock root that is no folder passes", out == "")

    reset(); ticket("spdp-g1c")
    t = write("a.jsonl", rec(type="user", message={"content": "hi"}))
    code, out, _ = run(payload(t))
    check("idle with a queued ticket blocks and names its tag", blocked(out) and "spdp-g1c" in out, out[:120])
    code, out, _ = run(payload(t, active=True))
    check("stop_hook_active passes, so the guard refuses once in a row", code == 0 and out == "")
    code, out, _ = run(payload(t), CLAUDE_ROLE="analyst")
    check("another role passes", out == "")
    sub = json.loads(payload(t)); sub["agent_id"] = "a123"
    code, out, _ = run(json.dumps(sub))
    check("a subagent's stop passes", out == "")
    os.remove(ROOT + "/lock/queue/1-1790342712-2670885-spdp-g1c.ticket")
    code, out, _ = run(payload(t))
    check("an empty queue passes", out == "")
    os.makedirs(ROOT + "/lock/gradle.lock.d")
    code, out, _ = run(payload(t))
    check("the mutex held with no ticket blocks", blocked(out) and "is held" in out, out[:80])
    shutil.rmtree(ROOT + "/lock/gradle.lock.d")
    os.makedirs(ROOT + "/lock/build.lock")
    code, out, _ = run(payload(t), IDLE_GUARD_HELD="build.lock")
    check("IDLE_GUARD_HELD names the held folder", blocked(out) and "is held" in out, out[:80])

    for kind, launch, tid in (("agent", agent("a1"), "a1"),
                              ("monitor", monitor("bmon1", ts=time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(T0))), "bmon1"),
                              ("shell", shell("bsh1"), "bsh1")):
        reset(); ticket("x-g1")
        t = write(kind + ".jsonl", launch)
        code, out, _ = run(payload(t, sid="s-" + kind))
        check("a live %s passes" % kind, out == "", out[:60])
        append(t, note(tid))
        code, out, _ = run(payload(t, sid="s-" + kind))
        check("the %s ended, then idle, blocks" % kind, blocked(out), out[:60])

    reset(); ticket("x-g1")
    code, out, _ = run(payload(ROOT + "/t/missing.jsonl"))
    check("H2 a missing transcript passes", code == 0 and out == "")
    t = write("empty.jsonl", "")
    code, out, _ = run(payload(t))
    check("H2 an empty transcript blocks (nothing in flight)", blocked(out))
    with open(ROOT + "/state/sess0001.json", "w") as h:
        h.write("{garbage")
    with open(ROOT + "/state/sess0001.json.tmp", "w") as h:
        h.write("leftover")
    code, out, _ = run(payload(t))
    check("H2 garbage state and a leftover tmp are rebuilt", code == 0 and blocked(out) and state()["offset"] == 0)
    os.remove(ROOT + "/state/sess0001.json")
    os.makedirs(ROOT + "/state/sess0001.json")
    code, out, err = run(payload(t))
    check("H2 a state path that is a folder fails open", code == 0 and out == "", err[:60])
    glog = open(ROOT + "/guard.log", encoding="utf-8").read() if os.path.exists(ROOT + "/guard.log") else ""
    check("a failure is logged with its class", " ERROR PermissionError" in glog or " ERROR IsADirectoryError" in glog,
          glog.strip()[-90:])

    reset(); ticket("x-g1")
    t = write("h3.jsonl", agent("a1") + note("a1"))
    procs = [subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env()) for _ in range(2)]
    outs = [p.communicate(payload(t).encode(), timeout=120) for p in procs]
    check("H3 two at once: both block and the state is whole",
          all(p.returncode == 0 for p in procs) and state()["offset"] == os.path.getsize(t)
          and all(blocked(o[0].decode()) for o in outs))

    reset(); ticket("x-g1"); os.makedirs(ROOT + "/guard.log")
    t = write("h4.jsonl", "")
    code, out, err = run(payload(t))
    check("H4 an unwritable log still blocks, silently", code == 0 and blocked(out) and err == "")
    shutil.rmtree(ROOT + "/guard.log")
    with open(ROOT + "/guard.log", "w") as h:
        h.write("x" * (1024 * 1024 + 10))
    run(payload(t, sid="rot"))
    check("H4 the log rotates past 1 MB", os.path.isfile(ROOT + "/guard.log.1") and os.path.getsize(ROOT + "/guard.log") < 1000)

    reset(); ticket("x-g1")
    t = write("h5 spaced.jsonl", "")
    code, out, _ = run(payload(t.replace("/", "\\")))
    check("H5 a backslash path with a space", blocked(out))

    reset(); ticket("x-g1")
    t = write("h6.jsonl", agent("a1") + agent("a1"))
    run(payload(t))
    code, out, _ = run(payload(t))
    check("H6 a second run with no new lines keeps the agent live", out == "")
    append(t, note("a1"))
    code, out, _ = run(payload(t))
    check("H6 a repeated launch of one id ends with one notice", blocked(out))

    reset(); ticket("x-g1")
    t = write("pend.jsonl", agent("a1") + note("a1", as_kind="enqueue"))
    code, out, _ = run(payload(t))
    check("an end enqueued and not yet taken passes", out == "")
    append(t, note("a1", as_kind="remove"))
    code, out, _ = run(payload(t))
    check("taken by a remove record, then idle, blocks", blocked(out))
    append(t, rec(type="queue-operation", operation="enqueue",
                  content="<task-notification>\n<task-id>bm</task-id>\n<summary>Monitor event</summary>\n</task-notification>"))
    code, out, _ = run(payload(t))
    check("a monitor event enqueued (no status) passes", out == "")
    append(t, rec(type="queue-operation", operation="dequeue"))
    code, out, _ = run(payload(t))
    check("taken by a dequeue record, then idle, blocks", blocked(out))
    append(t, rec(type="queue-operation", operation="enqueue", content="owner text")
           + rec(type="queue-operation", operation="popAll", content="owner text"))
    code, out, _ = run(payload(t))
    check("popAll empties the queue", blocked(out))

    reset(); ticket("x-g1")
    t = write("stale.jsonl", rec(type="queue-operation", operation="enqueue", timestamp="2026-09-25T06:40:00.000Z",
                                 content="<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n</task-notification>"))
    code, out, _ = run(payload(t))
    check("a queue entry 25 min old is stale and holds nothing", blocked(out))
    t = write("fresh.jsonl", rec(type="queue-operation", operation="enqueue", timestamp="2026-09-25T07:00:00.000Z", content="x"))
    code, out, _ = run(payload(t, sid="fresh"))
    check("a queue entry 5 min old still passes", out == "")
    # H7 replay of 09-25: one pass over a whole transcript met a leftover enqueue the harness no longer held, and
    # the dequeue that took a fresh end popped the leftover instead, so the fresh end stayed queued and passed
    t = write("leftover.jsonl", rec(type="queue-operation", operation="enqueue", timestamp="2026-09-25T06:30:00.000Z",
                                    content="lost")
              + rec(type="queue-operation", operation="enqueue",
                    content="<task-notification>\n<task-id>a9</task-id>\n<status>completed</status>\n</task-notification>")
              + rec(type="queue-operation", operation="dequeue"))
    code, out, _ = run(payload(t, sid="leftover"))
    check("a dequeue takes the fresh end, never a stale leftover, then idle blocks", blocked(out))

    reset(); ticket("x-g1")
    t = write("age.jsonl", rec(type="user", timestamp="2026-09-25T04:59:00.000Z",
                               toolUseResult={"status": "async_launched", "agentId": "aold", "description": "old"})
              + rec(type="user", timestamp="2026-09-25T04:59:00.000Z", toolUseResult={"stdout": "", "backgroundTaskId": "bold"}))
    code, out, _ = run(payload(t))
    check("an agent and a shell older than IDLE_GUARD_MAX_AGE do not count", blocked(out))
    code, out, _ = run(payload(t, sid="age2"), IDLE_GUARD_MAX_AGE="9000")
    check("IDLE_GUARD_MAX_AGE is the bound", out == "")

    reset(); ticket("x-g1")
    multi = ("<task-notification>\n<task-id>a1</task-id>\n<task-id>a2</task-id>\n<task-id>b3</task-id>\n"
             "<status>stopped</status>\n</task-notification>")
    t = write("multi.jsonl", agent("a1") + agent("a2") + shell("b3") + rec(type="user", message={"role": "user", "content": multi}))
    code, out, _ = run(payload(t))
    check("one notice naming three ids ends all three", blocked(out) and state()["live"] == {})

    reset(); ticket("x-g1")
    t = write("log.jsonl", agent("a7", "Lane z"))
    code, out, _ = run(payload(t))
    logged = open(ROOT + "/guard.log", encoding="utf-8").read() if os.path.exists(ROOT + "/guard.log") else ""
    check("a pass with a busy mutex is logged with its live ids",
          out == "" and " PASS tickets=1 held=0 queued=0 agent=a7" in logged, logged.strip()[-80:])
    os.remove(ROOT + "/lock/queue/1-1790342712-2670885-x-g1.ticket")
    run(payload(t, sid="quiet"))
    check("an idle mutex logs nothing", open(ROOT + "/guard.log", encoding="utf-8").read() == logged)

    reset(); ticket("x-g1")
    grep_out = rec(type="user", message={"role": "user", "content": [{"type": "tool_result", "content": multi.replace("a1", "a9")}]})
    t = write("grep.jsonl", agent("a9") + grep_out)
    code, out, _ = run(payload(t))
    check("notice text inside a tool result ends no agent", out == "")

    reset(); ticket("x-g1")
    t = write("h8a.jsonl", monitor("bold", ts="2026-09-25T00:00:00.000Z", ms=60000))
    code, out, _ = run(payload(t))
    check("H8 a monitor past its timeout does not count", blocked(out))
    t = write("h8b.jsonl", monitor("bbare", ts="2026-09-25", ms=60000))
    code, out, _ = run(payload(t, sid="bare"))
    check("H8 a bare-date stamp counts from the clock", out == "")
    t = write("h8c.jsonl", monitor("bpers", ts="2026-09-25T06:00:00.000Z", ms=0, persistent=True))
    code, out, _ = run(payload(t, sid="pers"))
    check("H8 a persistent monitor 65 min old counts", out == "")
    t = write("h8c2.jsonl", monitor("bpold", ts="2026-09-20T00:00:00.000Z", ms=0, persistent=True))
    code, out, _ = run(payload(t, sid="pers2"))
    check("H8 a persistent monitor older than MAX_AGE does not count", blocked(out))
    t = write("h8d.jsonl", agent("a1") + rec(type="system", subtype="agents_killed"))
    code, out, _ = run(payload(t, sid="kill"))
    check("H8 a restart (agents_killed) empties the live set", blocked(out))
    t = write("h8e.jsonl", agent("a1") + rec(type="attachment", attachment={"type": "hook_success", "hookName": "SessionStart:resume"}))
    code, out, _ = run(payload(t, sid="resume"))
    check("H8 a restart (SessionStart:resume) empties the live set", blocked(out))

    failed = [r for r in results if not r[1]]
    for label, ok, detail in results:
        print("%s %s%s" % ("OK  " if ok else "FAIL", label, (" | " + detail) if detail and not ok else ""))
    print("%d of %d OK" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
