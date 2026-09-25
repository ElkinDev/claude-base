"""idle-guard.py: Stop hook for the orchestrator seat. It refuses one turn end when nothing can wake the session
again while a work mutex still holds work: no Agent-tool subagent, no Monitor and no background shell in flight,
nothing queued for delivery, and the mutex held or a ticket in its queue.

Why: an orchestrator that ends its turn with no agent and no monitor while runs sit in the mutex queue is woken only
by whatever delivers their endings. When that waker stalls, every ending goes unread and the mutex stands idle until
a person looks, hours later. This hook makes that turn end ask for a waker first.

Opt-in: the guard reads a mutex only when IDLE_GUARD_LOCK_ROOT names its folder. With the variable unset, or naming
no folder, every turn end passes and nothing is written.

Rules. Only a session whose CLAUDE_ROLE is orchestrator is guarded (IDLE_GUARD_ROLE names another role, for tests).
A Stop that is a refusal's own continuation (stop_hook_active) or a subagent's (agent_id) always passes, so the guard
refuses at most once in a row and never loops. Every error passes (exit 0, nothing printed): the guard fails open.

What is in flight, read incrementally from the transcript (state per session in hooks/idle-guard-state/<sid>.json,
offset plus a digest of the file head, reset when the file is rewritten):
  - a subagent: a tool result with status async_launched and an agentId (same rule as inflight.py);
  - a Monitor: a tool result with a taskId and timeoutMs; it counts until its timeout passes, with no end when
    persistent is true;
  - a background shell: a tool result with a backgroundTaskId;
  - an end: a <task-notification> block with a terminal status ends EVERY task id the block names (an orphan notice
    lists several ids under one status), or a TaskStop result naming the id;
  - age: an agent or a shell older than IDLE_GUARD_MAX_AGE seconds (default 7200) no longer counts. A shell stopped
    by the UI, a Monitor timeout or an agent teardown leaves no end record, and one stale entry would keep the guard
    silent for the rest of the session; past the bound the cheaper error is one refusal the model answers in a line;
  - the delivery queue, modelled from the queue-operation records: enqueue adds its content, dequeue takes the
    oldest entry not yet stale at the dequeue's own time, remove takes the entry with that content, popAll empties
    it. An entry still queued at the Stop is a wake already coming, so the turn end passes; an entry older than 10
    minutes is dropped as stale (the harness drains the queue as a turn ends, so an entry that old was lost, not
    waiting);
  - a process restart kills everything: the agents_killed record and a SessionStart resume or startup hook row
    empty the live set and the queue.
The mutex: <lock root>/<IDLE_GUARD_HELD, default gradle.lock.d> exists (the mutex is held) or
<lock root>/queue/*.ticket exists (runs waiting for it); the lock root is IDLE_GUARD_LOCK_ROOT. A ticket's name is
<priority>-<epoch>-<pid>-<tag>.ticket, and the tag is what the refusal names.

Log (idle-guard.log beside this hook, or IDLE_GUARD_LOG): one line for every Stop that meets a busy mutex, a BLOCK
or a PASS with the live ids by kind and the queued count, so a false PASS is as readable as a false BLOCK. State
lives in idle-guard-state/ beside this hook, or IDLE_GUARD_STATE.
Number to read it by: stretches of 45 minutes or more in the orchestrator's main thread with a run ending inside.
"""
import glob
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

HOOKS = os.path.dirname(os.path.abspath(__file__))
LOG = os.environ.get("IDLE_GUARD_LOG") or os.path.join(HOOKS, "idle-guard.log")
STATE_DIR = os.environ.get("IDLE_GUARD_STATE") or os.path.join(HOOKS, "idle-guard-state")
LOCK_ROOT = (os.environ.get("IDLE_GUARD_LOCK_ROOT") or "").strip()
HELD = (os.environ.get("IDLE_GUARD_HELD") or "gradle.lock.d").strip()
ROLE = os.environ.get("IDLE_GUARD_ROLE") or "orchestrator"
MAX_AGE = float(os.environ.get("IDLE_GUARD_MAX_AGE") or 7200)
QUEUE_STALE = 600.0
NOW = os.environ.get("IDLE_GUARD_NOW")  # epoch seconds standing in for the clock, for replays in tests
BLOCK_RE = re.compile(r"<task-notification>(.*?)(?:</task-notification>|$)", re.S)
ID_RE = re.compile(r"<task-id>([A-Za-z0-9_]+)</task-id>")
STATUS_RE = re.compile(r"<status>(\w+)</status>")
STOP_RE = re.compile(r"Successfully stopped task: ([A-Za-z0-9_]+)")
END_STATES = ("completed", "failed", "killed", "cancelled", "error", "stopped")
HEAD_BYTES = 4096
PRUNE_DAYS = 7
ROTATE_BYTES = 1024 * 1024
MARKERS = (b'"async_launched"', b'"timeoutMs"', b'"backgroundTaskId"', b"<task-notification>", b"Successfully stopped task",
           b'"agents_killed"', b"SessionStart:resume", b"SessionStart:startup", b'"queue-operation"', b'"queued_command"')


def clock():
    return float(NOW) if NOW else time.time()


def log(line):
    try:
        if os.path.isfile(LOG) and os.path.getsize(LOG) > ROTATE_BYTES:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + line + "\n")
    except Exception:
        pass


def epoch(stamp):
    """Epoch seconds of a transcript timestamp such as 2026-09-25T07:15:03.811Z, or None."""
    try:
        return datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def head_digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha1(handle.read(HEAD_BYTES)).hexdigest()


def read_new(path, offset):
    """The new complete lines since offset that carry a marker, decoded, streamed (a 250 MB transcript is never held
    whole), and the offset after the last complete line; a partial last line waits for the next run."""
    lines = []
    with open(path, "rb") as handle:
        handle.seek(offset)
        for raw in handle:
            if not raw.endswith(b"\n"):
                break
            offset += len(raw)
            if any(marker in raw for marker in MARKERS):
                lines.append(raw.decode("utf-8", "replace"))
    return offset, lines


def ended_ids(text):
    """Every task id named by a <task-notification> block whose status is terminal, however many ids the block lists."""
    out = set()
    for block in BLOCK_RE.findall(text):
        status = STATUS_RE.search(block)
        if status and status.group(1) in END_STATES:
            out.update(ID_RE.findall(block))
    return out


def scan(lines, live, queue):
    """Apply the new transcript lines to live {id: [kind, label, deadline, start]} and queue [[content, epoch]]."""
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("isSidechain"):
            continue
        kind = record.get("type")
        when = epoch(record.get("timestamp")) or clock()
        if (kind == "system" and record.get("subtype") == "agents_killed") or (
                kind == "attachment" and str((record.get("attachment") or {}).get("hookName") or "")
                in ("SessionStart:resume", "SessionStart:startup")):
            live.clear()
            del queue[:]
            continue
        if kind == "queue-operation":
            op, content = record.get("operation"), str(record.get("content") or "")
            if op == "enqueue":
                queue.append([content[:2000], when])
            elif op == "dequeue":
                # the stale rule is applied at the record's time before the pop: a leftover the harness no longer
                # holds would otherwise take the pop, and one pass over a whole transcript would keep the entry
                # the harness really dequeued, where a turn-by-turn reading had already dropped the leftover
                queue[:] = [entry for entry in queue if when - float(entry[1] or 0) <= QUEUE_STALE]
                if queue:
                    queue.pop(0)
            elif op == "remove":
                for i, entry in enumerate(queue):
                    if entry[0] == content[:2000]:
                        del queue[i]
                        break
            elif op == "popAll":
                del queue[:]
        result = record.get("toolUseResult")
        if isinstance(result, dict):
            if result.get("status") == "async_launched" and result.get("agentId"):
                label = " ".join(str(result.get("description") or "?").split())
                live[str(result["agentId"])] = ["agent", label, 0, when]
            elif result.get("taskId") and "timeoutMs" in result:
                end = 0 if result.get("persistent") else when + float(result.get("timeoutMs") or 0) / 1000.0
                live[str(result["taskId"])] = ["monitor", "", end, when]
            elif result.get("backgroundTaskId"):
                live[str(result["backgroundTaskId"])] = ["shell", "", 0, when]
        for tid in STOP_RE.findall(line):
            live.pop(tid, None)
        # an end notice counts only where the harness puts one: a queue record, a user row whose content is the
        # notice itself (never a tool result that prints notice text, such as a grep of a transcript), or a
        # queued_command attachment
        notice = ""
        if kind == "queue-operation":
            notice = str(record.get("content") or "")
        elif kind == "user":
            content = (record.get("message") or {}).get("content")
            if isinstance(content, str):
                notice = content
            elif isinstance(content, list) and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                notice = " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
        elif kind == "attachment" and (record.get("attachment") or {}).get("type") == "queued_command":
            notice = str((record.get("attachment") or {}).get("prompt") or "")
        if "<task-notification>" in notice:
            for tid in ended_ids(notice):
                live.pop(tid, None)


def queue_state():
    tickets = sorted(glob.glob(os.path.join(LOCK_ROOT, "queue", "*.ticket")))
    held = os.path.isdir(os.path.join(LOCK_ROOT, HELD))
    return tickets, held


def ticket_tag(path):
    """1-1790342712-2670885-spdp-g1c.ticket -> spdp-g1c."""
    parts = os.path.basename(path)[:-len(".ticket")].split("-", 3)
    return parts[3] if len(parts) == 4 else os.path.basename(path)


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
            return 0  # a refusal's own continuation, or a subagent's stop: never guarded
        if (os.environ.get("CLAUDE_ROLE") or "") != ROLE:
            return 0
        if not LOCK_ROOT or not os.path.isdir(LOCK_ROOT):
            return 0  # opt-in: no mutex named, nothing to guard
        sid = str(data.get("session_id") or "")
        path = str(data.get("transcript_path") or "")
        if not sid or not os.path.isfile(path):
            return 0
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
        head = head_digest(path)
        if os.path.getsize(path) < offset or state.get("head") != head:
            live, queue, offset = {}, [], 0
        offset, lines = read_new(path, offset)
        scan(lines, live, queue)
        now = clock()
        for tid in list(live):
            kind, _, deadline, start = (live[tid] + [0, 0, 0, 0])[:4]
            # a monitor ends at its timeout; anything with no deadline (an agent, a shell, a persistent monitor) ends
            # at MAX_AGE
            if (deadline and deadline < now) or (not deadline and start and now - start > MAX_AGE):
                live.pop(tid)
        queue = [entry for entry in queue if now - float(entry[1] or 0) <= QUEUE_STALE]
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"offset": offset, "head": head, "live": live, "queue": queue}, handle)
        os.replace(tmp, state_path)
        prune_state()
        tickets, held = queue_state()
        if not tickets and not held:
            return 0
        tags = [ticket_tag(t) for t in tickets]
        shown = ", ".join(tags[:6]) + (" and %d more" % (len(tags) - 6) if len(tags) > 6 else "")
        if live or queue:
            kinds = {}
            for tid, value in live.items():
                kinds.setdefault(value[0], []).append(tid)
            log("%s PASS tickets=%d held=%d queued=%d %s" % (sid[:8], len(tickets), int(held), len(queue), " ".join(
                "%s=%s" % (k, ",".join(sorted(v))) for k, v in sorted(kinds.items()))))
            return 0
        log("%s BLOCK tickets=%d held=%d %s" % (sid[:8], len(tickets), int(held), shown))
        reason = (
            "Idle guard (Stop hook): this turn is ending with no agent, no Monitor and no background shell in flight, "
            "while the work mutex %s%s. Nothing will wake this session when those runs end if the waker that "
            "delivers their endings stalls. Before ending the turn, arm one bounded Monitor on "
            "their .exit and .done files, or launch the next approved lane. If neither applies, say why in one line "
            "and end the turn; this guard refuses only once in a row."
            % ("is held" if held else "is free", (" and %d run(s) are queued: %s" % (len(tags), shown)) if tags else ""))
        sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
        return 0
    except Exception as exc:  # fail open, but never silently
        log("ERROR %s %s" % (type(exc).__name__, str(exc)[:160].replace("\n", " ")))
        return 0


if __name__ == "__main__":
    sys.exit(main())
