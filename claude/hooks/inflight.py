"""inflight.py: Stop hook. At every turn end it logs the live Agent-tool subagents of the session into
~/.claude/hooks/inflight.log, with no tokens and without ever blocking (exit 0 on every path).

The session that runs the agents writes no count and no status row: a hook logs what is in flight, and
whoever reads the fleet reads the log. A number typed from memory is the one number nobody can check.

State per session in hooks/inflight-state/<session_id>.json: the byte offset already read, a digest of the file's
head (a rewrite in place resets the state) and the live set, so a turn end reads only the new bytes of its transcript.
Same rules as scripts/agents-in-flight.py: a spawn is a tool result with status async_launched (agentId,
description); an end is a task-notification with a terminal status, or a TaskStop result naming the id; an end
already queued for delivery counts as an end at the turn end. State files untouched for seven days are pruned.

CLAUDE_INFLIGHT_LOG and CLAUDE_INFLIGHT_STATE_DIR move both, which is what a test run sets so that it never
writes into the log the live sessions read. Read the log with:
    python scripts/agents-in-flight.py --log --session <prefix> --since "<local>"
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime

HOOKS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "hooks")
LOG = os.path.join(HOOKS_DIR, "inflight.log")
STATE_DIR = os.path.join(HOOKS_DIR, "inflight-state")
TASK_RE = re.compile(r"<task-id>([0-9a-f]+)</task-id>.*?<status>(\w+)</status>", re.S)
STOP_RE = re.compile(r"Successfully stopped task: ([0-9a-f]{12,})")
END_STATES = ("completed", "failed", "killed", "cancelled", "error", "stopped")
HEAD_BYTES = 4096
PRUNE_DAYS = 7
ROTATE_BYTES = 2 * 1024 * 1024  # the log is renamed to inflight.log.1 past this size (one generation kept)


def log_path():
    return os.environ.get("CLAUDE_INFLIGHT_LOG") or LOG


def state_dir():
    return os.environ.get("CLAUDE_INFLIGHT_STATE_DIR") or STATE_DIR


def rotate_log():
    try:
        path = log_path()
        if os.path.isfile(path) and os.path.getsize(path) > ROTATE_BYTES:
            old = path + ".1"
            if os.path.isfile(old):
                os.remove(old)
            os.replace(path, old)
    except Exception:
        pass


def head_digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha1(handle.read(HEAD_BYTES)).hexdigest()


def read_new(path, offset):
    """New complete lines since offset; a partial last line waits for the next run."""
    with open(path, "rb") as handle:
        handle.seek(offset)
        blob = handle.read()
    last_nl = blob.rfind(b"\n")
    if last_nl < 0:
        return offset, ""
    return offset + last_nl + 1, blob[:last_nl].decode("utf-8", "replace")


def prune_state():
    try:
        folder = state_dir()
        limit = time.time() - PRUNE_DAYS * 86400
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if name.endswith(".json") and os.path.getmtime(full) < limit:
                os.remove(full)
    except Exception:
        pass


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        if not isinstance(data, dict) or data.get("stop_hook_active"):
            return 0
        sid = data.get("session_id") or ""
        path = data.get("transcript_path") or ""
        if not sid or not os.path.isfile(path):
            return 0
        folder = state_dir()
        os.makedirs(folder, exist_ok=True)
        state_path = os.path.join(folder, sid + ".json")
        state = {}
        if os.path.isfile(state_path):
            try:
                with open(state_path, encoding="utf-8") as handle:
                    state = json.load(handle)
            except Exception:
                state = {}
        live = dict(state.get("live") or {})
        pending = set(state.get("pending") or [])
        offset = int(state.get("offset") or 0)
        head = head_digest(path)
        if os.path.getsize(path) < offset or state.get("head") != head:
            live, pending, offset = {}, set(), 0  # truncated or rewritten in place
        offset, text = read_new(path, offset)
        for line in text.split("\n"):
            if '"async_launched"' in line:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                result = record.get("toolUseResult") or {}
                if isinstance(result, dict) and result.get("status") == "async_launched" and result.get("agentId"):
                    live[result["agentId"]] = " ".join(str(result.get("description") or "?").split())
                continue
            ends = {tid for tid, st in TASK_RE.findall(line) if st in END_STATES} if "<task-notification>" in line else set()
            if '"type":"user"' in line:
                try:
                    record = json.loads(line)
                except ValueError:
                    record = None
                if record and record.get("type") == "user":
                    content = (record.get("message") or {}).get("content")
                    is_text = isinstance(content, str) or (
                        isinstance(content, list) and bool(content)
                        and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content))
                    if is_text:
                        for tid in pending | ends:
                            live.pop(tid, None)
                        pending = set()
                        continue
            if ends:
                pending |= ends
            if "Successfully stopped task" in line:
                for tid in STOP_RE.findall(line):
                    live.pop(tid, None)
        # An end already queued for delivery is an end: the session wakes for it right after this Stop, so the
        # count at the turn end excludes it (agents-in-flight.py applies the same flush at the end of a transcript).
        for tid in pending:
            live.pop(tid, None)
        with open(state_path, "w", encoding="utf-8") as handle:
            json.dump({"offset": offset, "head": head, "live": live, "pending": []}, handle)
        rotate_log()
        with open(log_path(), "a", encoding="utf-8", newline="\n") as handle:
            handle.write("%s %s %d %s\n" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sid[:8], len(live), "; ".join(live.values())[:300]))
        prune_state()
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
