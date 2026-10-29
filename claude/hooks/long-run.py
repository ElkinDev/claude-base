"""PostToolUse hook for every tool: tell a subagent when its context has grown past the point where a fresh agent
is cheaper. With --wall, a PreToolUse hook for every tool that stops one that ran on past the last notice.

The long-run rule: every API call of an agent re-reads its whole context. An agent that keeps working for hours
grows from a few tens of thousands of tokens a turn to a few hundred thousand, and its late calls each cost
several times what the same call costs a fresh agent started from a short checkpoint report (about 50k a turn).
So at 200k and again at 300k context tokens the agent is told to write its report as a checkpoint at the next
clean point and end, and the parent continues with a fresh agent. Before it ends, a review or single-answer task
gives its verdict or answer in full, any lock is released, and no staged change, rebase or merge is left half
made. A periodic waste check, if the project has one, finds long-running agents afterwards; this hook says it in
the moment.

It acts only on hook input that carries agent_id. A main session's payload has no "agent_id" key, so main() returns
on a byte test before any parse or file read; json, glob and time are imported only after that test. The
subagent's own transcript is agent_transcript_path if given, else
<folder of transcript_path>/<session_id>/subagents/agent-<agent_id>.jsonl, else a glob for agent-<agent_id>*.jsonl
inside that same subagents folder only, never across the project. Only the last TAIL_BYTES are read; the newest
assistant record with usage gives the context of the last turn: input_tokens + cache_read_input_tokens +
cache_creation_input_tokens.

One notice at 200k and one at 300k, each once per agent, as additionalContext. State per agent in
long-run-state/<agent_id>.json. Two parallel calls in the same turn can both see a level as due; only the call that
creates long-run-state/<agent_id>.<level>.mark (O_CREAT | O_EXCL) prints. State and markers are pruned at 7 days;
one log line per notice in long-run.log. LONG_RUN_DIR moves all of them (tests and probes); by default they sit
next to this file.

The PostToolUse mode never blocks: every path exits 0 and prints nothing except a notice. Only the --wall mode
below denies a call, and only at the wall.

The wall: a notice is advice, and an agent busy with a long task can read it and keep going. Agents that got the
300k notice and ran on were seen re-reading tens of millions of context tokens at 300k or more before they ended.
`long-run.py --wall` is a PreToolUse hook for every tool. With the same payload parsing and the same context read,
a subagent whose last turn carried WALL tokens or more, and whose 300k marker exists, is denied every tool except
Write and Edit (WALL_ALLOWED), with WALL_REASON, so the one thing left to it is writing its checkpoint report. An
agent without the marker is allowed: the PostToolUse notice comes first. Each denial writes one long-run.log line
with the level "wall <n>". Any error path exits 0 with no output, so the call is allowed.

The turn budget (70 and 90 percent of a definition's maxTurns) is not this hook's: turn-budget.py says it.
"""
import os
import sys

TAIL_BYTES = 256 * 1024
LEVELS = (200000, 300000)
ASSISTANT_MARKS = (b'"type":"assistant"', b'"type": "assistant"')
SAFE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
NOTICE = ("Long-run rule: your last turn carried %d k tokens of context, and every call re-reads it; past 200k a "
          "fresh agent started from a checkpoint report is cheaper. "
          "At the next clean point (a commit, a finished step, a detached job with an exit file), write your report "
          "as a checkpoint: what is done, the state, the next step, any exit file. Then end. Before you end: if your "
          "task is a review or a single answer, give that verdict or answer first, in full; release any lock you "
          "hold; leave no staged change, rebase or merge in progress. The parent continues with a fresh agent from "
          "that report. Go on only if fewer than 10 calls remain.")
WALL = 330_000
WALL_AFTER = 300000             # the notice level whose marker must exist before the wall acts
WALL_ALLOWED = ("Write", "Edit")
WALL_REASON = ("Context wall (330k). Stop working now. Write your checkpoint report (what is done, what is left, the "
               "exit files or logs you wait on, any lock you hold and any rebase, merge or staged change in progress, "
               "the next step) as a new file or as your final message, then end your turn. Your parent continues "
               "with a fresh agent.")


def base_dir():
    return os.environ.get("LONG_RUN_DIR") or os.path.dirname(os.path.abspath(__file__))


def safe_id(text):
    return bool(text) and set(text) <= SAFE_CHARS


def find_transcript(data, session, agent):
    """The subagent's own transcript path, or None."""
    given = data.get("agent_transcript_path")
    if isinstance(given, str) and given and os.path.isfile(given):
        return given
    transcript = data.get("transcript_path")
    if not isinstance(transcript, str) or not transcript or not session:
        return None
    folder = os.path.dirname(transcript.replace("\\", "/"))
    if not folder:
        return None
    subagents = os.path.join(folder, session, "subagents")
    derived = os.path.join(subagents, "agent-%s.jsonl" % agent)
    if os.path.isfile(derived):
        return derived
    import glob
    hits = sorted(glob.glob(os.path.join(glob.escape(subagents), "agent-%s*.jsonl" % glob.escape(agent))))
    return hits[0] if hits else None


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


def last_context(lines):
    """Context tokens of the newest assistant record with usage, or None."""
    import json
    for line in reversed(lines):
        if not any(m in line for m in ASSISTANT_MARKS):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        msg = rec.get("message")
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if not isinstance(usage, dict):
            continue
        total = 0
        for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                total += int(value)
        return total
    return None


def load_state(path):
    import json
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def save_state(path, state):
    import json
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except Exception:
        pass


def claim(agent, level):
    """True only for the one call that creates the level's marker file."""
    path = os.path.join(base_dir(), "long-run-state", "%s.%d.mark" % (agent, level))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except Exception:
        return False


def project_name(data, path):
    transcript = data.get("transcript_path")
    if isinstance(transcript, str) and transcript:
        return os.path.basename(os.path.dirname(transcript.replace("\\", "/")))
    # <project>/<session>/subagents/agent-x.jsonl
    return os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path.replace("\\", "/")))))


def log_notice(session, agent, project, n):
    import time
    try:
        line = "%s\t%s\t%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), session[:8], agent[:8], project, n)
        with open(os.path.join(base_dir(), "long-run.log"), "a", encoding="utf-8") as fh:
            fh.write(line)
        state_dir = os.path.join(base_dir(), "long-run-state")    # prune at each notice, 7 days
        for name in os.listdir(state_dir):
            full = os.path.join(state_dir, name)
            if name.endswith((".json", ".mark")) and time.time() - os.path.getmtime(full) > 7 * 86400:
                os.remove(full)
    except Exception:
        pass


def run(data):
    if not isinstance(data, dict):
        return None
    agent = data.get("agent_id")
    if not agent:
        return None             # a main session: the rule is for subagents
    agent = str(agent)
    session = str(data.get("session_id") or "")
    if not safe_id(agent) or (session and not safe_id(session)):
        return None
    path = find_transcript(data, session, agent)
    if not path:
        return None
    try:
        n = last_context(read_tail(path))
    except Exception:
        return None
    if n is None:
        return None
    state_path = os.path.join(base_dir(), "long-run-state", agent + ".json")
    state = load_state(state_path)
    sent = [lv for lv in state.get("sent") or [] if isinstance(lv, int)]
    due = [lv for lv in LEVELS if n >= lv and lv not in sent]
    due = [lv for lv in due if claim(agent, lv)]
    if not due:
        return None
    save_state(state_path, {"sent": sorted(set(sent + due)), "n": n, "session": session[:8]})
    log_notice(session, agent, project_name(data, path), n)
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "additionalContext": NOTICE % int(round(n / 1000.0))}}


def wall(data):
    """PreToolUse: the deny output at the wall, or None (allowed)."""
    if not isinstance(data, dict):
        return None
    agent = data.get("agent_id")
    if not agent:
        return None             # a main session: the rule is for subagents
    agent = str(agent)
    session = str(data.get("session_id") or "")
    if not safe_id(agent) or (session and not safe_id(session)):
        return None
    if data.get("tool_name") in WALL_ALLOWED:
        return None
    marker = os.path.join(base_dir(), "long-run-state", "%s.%d.mark" % (agent, WALL_AFTER))
    if not os.path.isfile(marker):
        return None             # no 300k notice yet: the PostToolUse mode sends it first
    path = find_transcript(data, session, agent)
    if not path:
        return None
    try:
        n = last_context(read_tail(path))
    except Exception:
        return None
    if n is None or n < WALL:
        return None
    log_notice(session, agent, project_name(data, path), "wall %d" % n)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": WALL_REASON}}


def main():
    try:
        raw = sys.stdin.buffer.read()
        if b'"agent_id"' not in raw:
            return 0            # a main session: no parse, no file read
        import json
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return 0
    try:
        out = wall(data) if "--wall" in sys.argv[1:] else run(data)
        if out:
            sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
