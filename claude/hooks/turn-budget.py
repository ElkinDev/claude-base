"""PreToolUse hook for every tool: keep a subagent's turn budget in sight.

An agent with `maxTurns` in its definition stops dead at that count and returns only what it
already wrote. The definitions ask for a checkpoint report at 70 percent, but an agent sees no
counter, so on a busy project it read until the limit and handed back nothing.

Inside a subagent (the harness passes `agent_id` and `agent_type`) every tool call the agent
starts adds one empty file to `$CLAUDE_LEDGER_DIR/turn-budget/<agent_id>/`, so the file count is
the call count even when calls run in parallel (Windows has no atomic append). PreToolUse sees
every call that passes the harness's input validation: a call that then fails, is refused by
another hook or is denied a permission fires no PostToolUse, yet it spends a turn. A call
rejected before that (an unknown tool, a missing parameter) fires no hook at all and is not
counted. When the count first reaches 70
and then 90 percent of the definition's `maxTurns` (found as `<cwd>/.claude/agents/<type>.md`,
then `~/.claude/agents/<type>.md`), the hook adds one factual line to the agent's context through
`additionalContext`. A turn holds one or more calls, so unless calls are rejected for bad input
the count is never behind the turns and the note comes at or before the turn it names. In the main session, and for an agent whose
definition has no `maxTurns` or cannot be found, it does nothing and writes nothing.

A run that a message resumes gets a fresh `maxTurns`, so the count starts again with it. The run is
found in the agent's own transcript (`agent_transcript_path`, else
`<folder of transcript_path>/<session_id>/subagents/agent-<agent_id>.jsonl`, else the first
`agent-<agent_id>*.jsonl` there); only its last 256 KB are read, with no JSON parse except for a
prompt. A prompt is a user record that is not a tool result and not a harness meta record (an
image, a skill text, a retry); a coordinator message or a task notification counts as one. A
prompt starts a new run when it comes right after an assistant record (the agent had ended its
turn), or when the run so far has had `maxTurns` turns (the agent was cut off, and its last tool
result sits before the prompt). Turns are the changes of assistant message id since the run's
start, as the harness counts them; calls are not, since parallel calls pass the limit before the
turns do. A prompt that comes while the agent works starts nothing, and a `seen-<uuid>` file keeps
it that way. The first run
counts in `<agent_id>/` itself; a later run counts in `<agent_id>/run-<time>-<uuid>/`, the newest
of which is the run counted so far. Each run gets its own 70 and 90 percent notes, once each. When
the transcript cannot be found or read, the count goes on in the run counted so far, which for an
agent never seen with a transcript is `<agent_id>/`, as before. A first turn that writes more than
256 KB before its first call pushes its prompt out of reach, and that run is counted with the one
before it.

It never blocks and never decides: every failure exits 0 with no output.
"""
import json
import math
import os
import re
import shutil
import sys
import time

BUDGET_DIR = "turn-budget"
MARKS = (70, 90)  # percent of maxTurns at which the agent gets one note each
KEEP_SECONDS = 2 * 86400  # call folders idle this long are pruned when a new agent starts
TAIL_BYTES = 256 * 1024  # the end of the transcript searched for the prompt that started the run
USER_MARKS = (b'"type":"user"', b'"type": "user"')
ASSISTANT_MARKS = (b'"type":"assistant"', b'"type": "assistant"')
RESULT_MARKS = (b'"type":"tool_result"', b'"type": "tool_result"')
META_MARKS = (b'"isMeta":true', b'"isMeta": true')
ORIGIN_MARKS = (b'"origin":',)  # a coordinator message or a task notification, both meta records
MESSAGE_ID = re.compile(rb'"id":\s*"([^"]+)"')  # the first id after "message": is the message id
MAX_TURNS_LINE = re.compile(r"^maxTurns:\s*(\d+)\s*$")
SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]")  # no dots, so no name can be "." or ".."


def ledger_dir():
    return os.environ.get("CLAUDE_LEDGER_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "ledger")


def max_turns(agent_type, cwd):
    """maxTurns from the agent's definition, project folder first, then the user's; None when absent.

    Only the frontmatter is read (between the first two `---` lines), so a body line that happens
    to start with `maxTurns:` never counts.
    """
    name = SAFE_NAME.sub("", str(agent_type or ""))
    if not name:
        return None, ""
    places = []
    if cwd:
        places.append(os.path.join(cwd, ".claude", "agents", name + ".md"))
    places.append(os.path.join(os.path.expanduser("~"), ".claude", "agents", name + ".md"))
    for path in places:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            continue
        if not lines or lines[0].strip() != "---":
            continue
        for line in lines[1:]:
            if line.strip() == "---":
                break
            match = MAX_TURNS_LINE.match(line.strip())
            if match:
                value = int(match.group(1))
                return (value if value > 0 else None), path
        return None, path
    return None, ""


def is_junction(path):
    check = getattr(os.path, "isjunction", None)  # Python 3.12 and later
    return bool(check and check(path))


def prune(folder, keep):
    """Remove the call folders of agents idle for two days; never a link, never the current one."""
    now = time.time()
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for entry in entries:
        try:
            if os.path.normcase(entry.path) == os.path.normcase(keep) or entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False) and not is_junction(entry.path) \
                    and now - entry.stat().st_mtime > KEEP_SECONDS:
                shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            continue


def find_transcript(data, agent_id):
    """The subagent's own transcript, or "" when it cannot be found."""
    given = data.get("agent_transcript_path")
    if isinstance(given, str) and given and os.path.isfile(given):
        return given
    main = data.get("transcript_path")
    session = SAFE_NAME.sub("", str(data.get("session_id") or ""))
    if not isinstance(main, str) or not main or not session:
        return ""
    subagents = os.path.join(os.path.dirname(main), session, "subagents")
    derived = os.path.join(subagents, "agent-%s.jsonl" % agent_id)
    if os.path.isfile(derived):
        return derived
    import glob
    hits = sorted(glob.glob(os.path.join(glob.escape(subagents), "agent-%s*.jsonl" % agent_id)))
    return hits[0] if hits else ""


def prompts(path, tail):
    """The prompts in the transcript's last tail bytes, oldest first, and whether that is the whole file.

    Each prompt is (uuid, time, after, first, turns). after: the record before it (attachments and
    API records aside) is an assistant record. first: it is the first record of the file, the prompt
    the agent started with. turns: the assistant turns between the prompt before it (or the start of
    the bytes read) and this one, a turn being a change of the assistant message id, as in the
    harness. Only a line that may be a prompt is parsed; the others are told apart by their bytes.
    """
    with open(path, "rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        start = max(0, size - tail)
        handle.seek(start)
        data = handle.read(size - start)
    lines = data.split(b"\n")[:-1]  # the last piece is empty or a line still being written
    if start:
        lines = lines[1:]  # cut by the tail, so the record before the next one is unknown
    found, before, last, turns = [], None if start else "start", None, 0
    for raw in lines:
        if any(m in raw for m in USER_MARKS):
            if not any(m in raw for m in RESULT_MARKS) and (not any(m in raw for m in META_MARKS)
                                                            or any(m in raw for m in ORIGIN_MARKS)):
                try:
                    record = json.loads(raw)
                except ValueError:
                    record = None
                if isinstance(record, dict) and record.get("type") == "user":
                    uuid = SAFE_NAME.sub("", str(record.get("uuid") or ""))
                    if uuid:
                        found.append((uuid, SAFE_NAME.sub("", str(record.get("timestamp") or "")),
                                      before == "assistant", before == "start", turns))
                        turns = 0
            before = "user"
        elif any(m in raw for m in ASSISTANT_MARKS):
            before = "assistant"
            at = raw.find(b'"message":')
            match = MESSAGE_ID.search(raw, at, at + 2048) if at >= 0 else None
            if match and match.group(1) != last:
                turns, last = turns + 1, match.group(1)
    return found, start == 0


def is_call(name):
    return name[:1].isdigit()  # call files are <pid>-<ns>; marks, runs and seen prompts are not


def judge(agent_dir, names, current, found, whole, limit):
    """The run counted now and the prompts to mark seen, or None when the bytes read are too few.

    The prompts move the run on, oldest first. A prompt with a folder is a run already counted, one
    with a seen- file was judged to start nothing, and any other one starts a run when it follows an
    assistant record, or when the run so far has had limit turns. Those turns are counted only from
    a run start inside the bytes read (or the start of the file); without one, and below the limit,
    the answer needs more of the file.
    """
    seen, turns, known = [], 0, whole
    for uuid, stamp, after, first, between in found:
        turns += between
        name = "run-%s-%s" % (stamp, uuid)
        if first:
            current = agent_dir
        elif name in names:
            current = os.path.join(agent_dir, name)
        elif "seen-" + uuid in names or uuid in seen:
            continue
        elif after or turns >= limit:
            current = os.path.join(agent_dir, name)
        elif known:
            seen.append(uuid)
            continue
        else:
            return None
        turns, known = 0, True
    return current, seen


def run_folder(agent_dir, transcript, limit):
    """The folder that counts this run's calls: agent_dir for the first run, a run-* folder after.

    The run counted so far is the newest run-* folder, or agent_dir; judge() moves it on from the
    transcript. A prompt judged to start nothing is marked seen, so it never becomes a run later.
    When the last TAIL_BYTES hold no start of the run a prompt would end, four times as much is
    read, up to the whole file; that happens once per such prompt, as it is marked seen after.
    """
    try:
        names = set(os.listdir(agent_dir))
    except OSError:
        names = set()
    runs = sorted(name for name in names if name.startswith("run-"))
    current = os.path.join(agent_dir, runs[-1]) if runs else agent_dir
    if not transcript:
        return current
    tail = TAIL_BYTES
    try:
        while True:
            found, whole = prompts(transcript, tail)
            verdict = judge(agent_dir, names, current, found, whole, limit)
            if verdict:
                break
            tail *= 4
    except (OSError, ValueError):
        return current
    current, seen = verdict
    for uuid in seen:
        try:
            open(os.path.join(agent_dir, "seen-" + uuid), "x").close()
        except OSError:
            pass
    return current


def calls_in(folder):
    try:
        return sum(1 for name in os.listdir(folder) if is_call(name))
    except OSError:
        return 0


def budget_note(data):
    """Count this subagent's tool call and return the note due at this count, or None."""
    agent_id = SAFE_NAME.sub("", str(data.get("agent_id") or ""))
    agent_type = data.get("agent_type")
    if not agent_id or not agent_type:
        return None
    limit, source = max_turns(agent_type, data.get("cwd"))
    if not limit:
        return None
    folder = os.path.join(ledger_dir(), BUDGET_DIR)
    agent_dir = os.path.join(folder, agent_id)
    fresh = not os.path.isdir(agent_dir)
    os.makedirs(agent_dir, exist_ok=True)
    if fresh:
        prune(folder, keep=agent_dir)
    calls = run_folder(agent_dir, find_transcript(data, agent_id), limit)
    os.makedirs(calls, exist_ok=True)
    # One empty file per call, named by the process and the clock: a shared counter loses calls
    # that run in parallel, while a new file per call never collides. The count read after this
    # call's file exists is at least this call's number.
    open(os.path.join(calls, "%d-%d" % (os.getpid(), time.time_ns())), "x").close()
    count = calls_in(calls)
    for mark in sorted(MARKS, reverse=True):
        if count < math.ceil(limit * mark / 100.0):
            continue
        try:
            # Created once per run and mark, exclusively, so parallel calls give one note.
            open(os.path.join(calls, "mark-%d" % mark), "x").close()
        except OSError:
            return None
        return ("Turn budget, %d percent: this %s run has started %d tool calls; its limit is %d turns "
                "(maxTurns in %s), and a turn holds one or more calls. At the limit the run ends and "
                "returns only what it has already written or reported."
                % (mark, agent_type, count, limit, source))
    return None


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        note = budget_note(data)
        if note:
            out = {"hookSpecificOutput": {"hookEventName": data.get("hook_event_name") or "PreToolUse",
                                          "additionalContext": note}}
            sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
