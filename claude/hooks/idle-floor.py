"""idle-floor.py: Stop hook for the orchestrator seat. It refuses a turn end when fewer than IDLE_FLOOR (default 3)
Agent-tool subagents are in flight, inside the working hours, at most once per 10 minutes per session.

Why: an orchestrator can end its turn with no subagents in flight while work is still owed, and idle-guard.py only
guards a busy work mutex. This hook asks for the next lanes first. Number to read it by: minutes per working day with
fewer than IDLE_FLOOR live subagents in the orchestrator's main thread, from the log below.

Rules. Only CLAUDE_ROLE == orchestrator is acted on (IDLE_FLOOR_ROLE names another role, for tests). A Stop with
stop_hook_active true or with agent_id passes. Every error passes (exit 0, nothing on stdout): fail open.
Live subagents are counted with idle-guard.py's own parser (imported, not copied, so one rule set serves both): an
async_launched result with an agentId starts one, a terminal task-notification ends every id it names, TaskStop ends
it, one older than 2 h no longer counts, a restart or SessionStart resume empties the set. Incremental state per
session in idle-floor-state/<sid>.json beside this hook (offset, digest of the file head, live set, queue, last refusal time).
Monitors and shells are not subagents and never count here.
A refusal prints {"decision": "block", "reason": ...} when live < floor, local time is in [IDLE_FLOOR_OPEN,
IDLE_FLOOR_CLOSE) (HH:MM, default 08:00 and 21:15) and no refusal was printed for the session in the last 10 minutes. Log: idle-floor.log beside this hook (IDLE_FLOOR_LOG), one line per Stop
evaluated: time, session, BLOCK or PASS, why, live ids.
Settings: IDLE_FLOOR (floor, default 3), IDLE_FLOOR_OPEN, IDLE_FLOOR_CLOSE. Test hooks: IDLE_FLOOR_NOW (epoch seconds for the clock), IDLE_FLOOR_STATE, IDLE_FLOOR_LOG, IDLE_FLOOR_FLOOR.
"""
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime

HOOKS = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
GUARD_PATH = HOOKS + "/idle-guard.py"
LOG = os.environ.get("IDLE_FLOOR_LOG") or HOOKS + "/idle-floor.log"
STATE_DIR = os.environ.get("IDLE_FLOOR_STATE") or HOOKS + "/idle-floor-state"
ROLE = os.environ.get("IDLE_FLOOR_ROLE") or "orchestrator"
FLOOR = int(os.environ.get("IDLE_FLOOR_FLOOR") or os.environ.get("IDLE_FLOOR") or 3)
NOW = os.environ.get("IDLE_FLOOR_NOW")
MAX_AGE = 7200.0
REFUSE_GAP = 600.0


def minutes(text, default):
    try:
        hour, _, minute = (text or "").strip().partition(":")
        return int(hour) * 60 + int(minute)
    except ValueError:
        return default


OPEN_MIN = minutes(os.environ.get("IDLE_FLOOR_OPEN"), 8 * 60)
CLOSE_MIN = minutes(os.environ.get("IDLE_FLOOR_CLOSE"), 21 * 60 + 15)
ROTATE_BYTES = 1024 * 1024
PRUNE_DAYS = 7
SID_RE = re.compile(r"^[0-9A-Fa-f-]{8,64}$")


def clock():
    return float(NOW) if NOW else time.time()


def log(line):
    try:
        if os.path.isfile(LOG) and os.path.getsize(LOG) > ROTATE_BYTES:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(datetime.fromtimestamp(clock()).strftime("%Y-%m-%d %H:%M:%S") + " " + line + "\n")
    except Exception:
        pass


def load_guard():
    spec = importlib.util.spec_from_file_location("idle_guard_parser", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def first_line_is_json(path):
    with open(path, "rb") as handle:
        for raw in handle:
            if raw.strip():
                return isinstance(json.loads(raw.decode("utf-8", "replace")), dict)
    return False


def prune_state():
    try:
        limit = time.time() - PRUNE_DAYS * 86400
        for name in os.listdir(STATE_DIR):
            full = os.path.join(STATE_DIR, name)
            if name.endswith(".json") and os.path.getmtime(full) < limit:
                os.remove(full)
    except Exception:
        pass


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        if not isinstance(data, dict) or data.get("stop_hook_active") or data.get("agent_id"):
            return 0
        if (os.environ.get("CLAUDE_ROLE") or "") != ROLE:
            return 0
        sid = str(data.get("session_id") or "")
        path = str(data.get("transcript_path") or "")
        if not SID_RE.match(sid) or not os.path.isfile(path):
            return 0
        if not first_line_is_json(path):
            log("%s PASS malformed-transcript" % sid[:8])
            return 0
        guard = load_guard()
        os.makedirs(STATE_DIR, exist_ok=True)
        state_path = os.path.join(STATE_DIR, sid + ".json")
        state = {}
        if os.path.isfile(state_path):
            try:
                with open(state_path, encoding="utf-8") as handle:
                    state = json.load(handle)
            except Exception:
                state = {}
        live = dict(state.get("live") or {})
        queue = list(state.get("queue") or [])
        offset = int(state.get("offset") or 0)
        last_block = float(state.get("last_block") or 0)
        head = guard.head_digest(path)
        if os.path.getsize(path) < offset or state.get("head") != head:
            live, queue, offset = {}, [], 0
        offset, lines = guard.read_new(path, offset)
        good = []
        for line in lines:
            try:
                json.loads(line)
                good.append(line)
            except ValueError:
                # a damaged marker line is skipped and the offset still advances past it, so one bad row never
                # freezes the hook for the rest of the session; it is logged once, when first met
                log("ERROR damaged-marker-line skipped %s" % sid[:8])
        guard.scan(good, live, queue)
        now = clock()
        for tid in list(live):
            kind, _, deadline, start = (live[tid] + [0, 0, 0, 0])[:4]
            if kind != "agent" or (start and now - start > MAX_AGE):
                live.pop(tid)
        local = datetime.fromtimestamp(now)
        minute = local.hour * 60 + local.minute
        count = len(live)
        ids = ",".join(sorted(live)) or "-"
        if count >= FLOOR:
            verdict = "PASS floor-met"
        elif not OPEN_MIN <= minute < CLOSE_MIN:
            verdict = "PASS outside-hours"
        elif now - last_block < REFUSE_GAP:
            verdict = "PASS refused-within-10min"
        else:
            verdict = "BLOCK"

        def save(block_time):
            tmp = state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"offset": offset, "head": head, "live": live, "queue": queue, "last_block": block_time},
                          handle)
            os.replace(tmp, state_path)

        save(last_block)
        prune_state()
        log("%s %s live=%d %s" % (sid[:8], verdict, count, ids))
        if verdict == "BLOCK":
            reason = ("%d subagents in flight, under the floor of %d: launch the next owed lanes now (unread finished "
                      "jobs first), or answer in one line why none can run." % (count, FLOOR))
            sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
            sys.stdout.flush()
            save(now)  # the 10-minute gap is spent only once the refusal was delivered
        return 0
    except Exception as exc:
        log("ERROR %s %s" % (type(exc).__name__, str(exc)[:160].replace("\n", " ")))
        return 0


if __name__ == "__main__":
    sys.exit(main())
