"""PostToolUse hook for every tool: tell a main session when a wake passes three API turns.

The wake rule: a main session spends at most 3 API turns per wake. A wake is the turn a task notification
starts, because a subagent finished or a background command ended. Such a turn usually needs one read of the
result and one decision, and every further API turn re-reads the session's whole context, so a wake that
drifts into ten turns costs ten context reads for work a batched call does in one. A periodic waste check, if
the project has one, finds these wakes afterwards; this hook says it in the moment instead.

How a wake is recognised: trigger() takes the user record that starts a turn (not a tool_result, not meta,
not a compact summary, not an interrupt or a local command), trigger_kind() says what started it, and the
turn count is one per distinct assistant message id that carries usage, sidechain records skipped. A
relayed message that quotes a notification is a message from another session, never a wake.

Only the last TAIL_BYTES of the transcript are read. If the anchor of the current turn is in the tail,
the turns after it are counted there. If it is not (a wake whose results passed TAIL_BYTES), the wake
of the state file is continued and its stored message ids are joined with those of the tail.

One notice at the 3rd turn and one at the 6th, each once per wake, as additionalContext. Subagents are
skipped: the rule is for the session that receives the notifications. State per session in
wake-turns-state/<session_id>.json, pruned at 7 days; one log line per notice in wake-turns.log.
WAKE_TURNS_DIR moves both (tests and measurements); by default they sit next to this file.

It never blocks: every path exits 0 and prints nothing except a notice.
"""
import json
import os
import re
import sys
import time

TAIL_BYTES = 512 * 1024
LEVELS = (3, 6)
WAKE_KINDS = ("subagent finished", "background command ended")
ID_MEMORY = 50                  # message ids kept in the state file for a wake beyond the tail
USER_MARKS = (b'"type":"user"', b'"type": "user"')
ASSISTANT_MARKS = (b'"type":"assistant"', b'"type": "assistant"')
NOTICE = ("Wake rule: this wake (a turn started by a finished subagent or background command) has used %d API "
          "turns, and a wake takes at most 3, since every turn re-reads the whole context. Put what is left in one "
          "batched call, then end the turn.")


def base_dir():
    return os.environ.get("WAKE_TURNS_DIR") or os.path.dirname(os.path.abspath(__file__))


def trigger_kind(txt):
    """What started a turn. A relayed message that quotes a notification is a message from another session,
    never a wake."""
    if txt.lstrip().startswith("From ") or "<cross-session-message" in txt:
        return "message from another session"
    if "<task-notification>" in txt:
        if re.search(r"<summary>Agent ", txt):
            return "subagent finished"
        if "Background command" in txt:
            return "background command ended"
        if re.search(r"[Mm]onitor", txt):
            return "monitor event"
        return "other notification"
    if "<monitor" in txt or "Monitor event" in txt:
        return "monitor event"
    if "sent a message" in txt or txt.lstrip().startswith("From "):
        return "message from another session"
    return "owner or relayed prompt"


def trigger(rec, msg):
    """The text of a user record that starts a turn, or None."""
    if rec.get("isCompactSummary") or rec.get("isMeta"):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        txt = content
    else:
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content or []):
            return None
        txt = " ".join(x.get("text", "") for x in content or [] if isinstance(x, dict) and x.get("type") == "text")
    if txt.strip() and not txt.lstrip().startswith("[Request interrupted") and "<local-command" not in txt \
            and "<command-name>" not in txt:
        return txt
    return None


def read_tail(path):
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        start = max(0, size - TAIL_BYTES)
        fh.seek(start)
        data = fh.read()
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]       # the first line is cut
    return lines


def scan(lines):
    """(anchor, ids) from the newest line back: anchor is (uuid, kind) of the turn's start or None,
    ids the distinct assistant message ids after it, oldest first."""
    ids = []
    seen = set()
    for line in reversed(lines):
        is_user = any(m in line for m in USER_MARKS)
        if not is_user and not any(m in line for m in ASSISTANT_MARKS):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("isSidechain"):
            continue
        msg = rec.get("message") or {}
        if not isinstance(msg, dict):
            continue
        kind = rec.get("type")
        if kind == "user":
            txt = trigger(rec, msg)
            if txt is not None:
                ids.reverse()
                return (rec.get("uuid") or "", trigger_kind(txt)), ids
        elif kind == "assistant":
            mid = msg.get("id")
            if mid and msg.get("usage") and mid not in seen:
                seen.add(mid)
                ids.append(mid)
    ids.reverse()
    return None, ids


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def save_state(path, state):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())  # parallel hooks never share a temp file
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except Exception:
        pass


def log_notice(session, transcript, n):
    try:
        project = os.path.basename(os.path.dirname(transcript))
        line = "%s\t%s\t%s\t%d\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), session[:8], project, n)
        with open(os.path.join(base_dir(), "wake-turns.log"), "a", encoding="utf-8") as fh:
            fh.write(line)
        state_dir = os.path.join(base_dir(), "wake-turns-state")  # prune at each notice, 7 days
        for name in os.listdir(state_dir):
            full = os.path.join(state_dir, name)
            if name.endswith(".json") and time.time() - os.path.getmtime(full) > 7 * 86400:
                os.remove(full)
    except Exception:
        pass


def run(data):
    if not isinstance(data, dict):
        return None
    if data.get("agent_id") or data.get("agent_type"):
        return None             # a subagent: the rule is for main sessions
    session = str(data.get("session_id") or "")
    transcript = data.get("transcript_path") or ""
    if not session or not transcript or not re.match(r"^[A-Za-z0-9_-]+$", session):
        return None
    try:
        lines = read_tail(transcript)
    except Exception:
        return None
    anchor, ids = scan(lines)
    state_path = os.path.join(base_dir(), "wake-turns-state", session + ".json")
    state = load_state(state_path)
    if anchor is not None:
        uuid, kind = anchor
        if kind not in WAKE_KINDS:
            if state.get("wake"):
                save_state(state_path, {"anchor": uuid, "wake": False})   # end the old wake for the fallback
            return None
        sent = list(state.get("sent") or []) if state.get("anchor") == uuid else []
    else:
        if not state.get("wake") or not state.get("anchor"):
            return None
        uuid = state["anchor"]
        known = list(state.get("ids") or [])
        ids = known + [i for i in ids if i not in set(known)]
        sent = list(state.get("sent") or [])
    n = len(ids)
    due = [lv for lv in LEVELS if n >= lv and lv not in sent]
    new_state = {"anchor": uuid, "wake": True, "sent": sorted(set(sent + due)), "ids": ids[-ID_MEMORY:]}
    if new_state != state:
        save_state(state_path, new_state)
    if not due:
        return None
    log_notice(session, transcript, n)
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": NOTICE % n}}


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return 0
    try:
        out = run(data)
        if out:
            sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
