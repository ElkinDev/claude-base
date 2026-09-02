#!/usr/bin/env python3
"""Compact Claude Code sessions at an idle boundary instead of at the ceiling.

Claude Code compacts on its own when the context reaches a fixed fraction of the model
window (about 80 percent on a 200k window). That moment is arbitrary: it can land in
the middle of a task, and it lets the context ride at its most expensive size for the
last third of every cycle. This watcher picks a better moment with zero model tokens:

  - it reads each session's live context size from the last usage row of its transcript,
  - it asks the Herdr multiplexer whether the session is idle (waiting for input),
  - when the context is above a threshold AND the session has been idle for a while,
    it submits `/compact` to that pane, once, then waits for a cooldown.

The compaction itself runs through the ordinary path, so the PreCompact hooks (checkpoint
plus summary instructions) and the PostCompact hook apply. The built-in auto-compaction
stays armed as the ceiling for sessions that never go idle. Nothing here calls a model.

Usage:
  python compact-at-boundary.py                       watch every Claude pane Herdr knows
  python compact-at-boundary.py --titles orques       watch the panes whose title matches (regex)
  python compact-at-boundary.py --sessions f2109a6d   watch these session ids (prefixes)
  python compact-at-boundary.py --panes w3:p1,w3:p3   watch these pane ids (Herdr renumbers them)
  python compact-at-boundary.py --status              one pass, print the table, exit
  python compact-at-boundary.py --dry-run             decide and log, never submit
  python compact-at-boundary.py --once                one pass with real submissions
  python compact-at-boundary.py --stop                ask the running watcher to exit

Defaults: --window 200000 tokens, --threshold 0.65 of the window, --idle 90 seconds of
continuous idleness, --cooldown 900 seconds between submissions to the same session,
--interval 30 seconds between passes. See docs/CONTEXT-ECONOMICS.md for where the
threshold comes from and why the exact value is second order.
"""
import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".claude")
LOCK_FILE = os.path.join(STATE_DIR, "compact-at-boundary.lock")
STOP_FILE = os.path.join(STATE_DIR, "compact-at-boundary.stop")
LOG_FILE = os.path.join(STATE_DIR, "compact-at-boundary.log")
TAIL_BYTES = 512 * 1024
COMPACTION_DROP = 0.6      # a context that falls below 60 percent of its last value was compacted
MAX_BACKOFF_DOUBLINGS = 4  # a cooldown that has already grown 16 times is long enough

# Where the context number came from: `offset` is the byte offset of that row in the
# transcript, `kind` is "usage" for an assistant turn and "boundary" for a compaction row.
# Two passes that read the same mark read the same row, which is how a number that has not
# moved is told from a session that answered something in between.
Mark = collections.namedtuple("Mark", "offset kind")


def log(message, quiet=False):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}"
    if not quiet:
        print(line, flush=True)
    try:
        if os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > 1024 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- Herdr

def herdr_agents(herdr):
    """Claude agents Herdr knows: [{pane, session, status, seq, cwd, title}]. Empty list on any failure."""
    try:
        proc = subprocess.run([herdr, "agent", "list"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        payload = json.loads(proc.stdout or "{}")
    except Exception as error:
        return None, f"herdr agent list failed: {error}"
    agents = (payload.get("result") or {}).get("agents")
    if agents is None:
        agents = payload.get("agents") or []
    out = []
    for item in agents:
        if item.get("agent") != "claude":
            continue
        session = ((item.get("agent_session") or {}).get("value")) or ""
        out.append({
            "pane": item.get("pane_id") or "?",
            "session": session,
            "status": item.get("agent_status") or "unknown",
            "seq": item.get("state_change_seq"),
            "cwd": item.get("cwd") or "",
            # two independent names: Herdr's own (`herdr agent rename`, survives everything the
            # pane survives) and the terminal title Claude Code sets (`/rename` inside the session,
            # otherwise an automatic summary of the current task)
            "name": item.get("name") or "",
            "title": item.get("terminal_title_stripped") or item.get("terminal_title") or "",
            "tab": item.get("tab_id") or "",
            "tab_label": "",
        })
    return out, ""


def tab_labels(herdr):
    """{tab_id: label} for the tabs that carry a real label. Herdr's default labels are bare
    numbers and are ignored. The launcher creates tabs as `cc-<account>-<role>`, so the role
    is on the tab whenever a session was opened with -Tab. Empty dict on any failure."""
    try:
        proc = subprocess.run([herdr, "tab", "list"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        payload = json.loads(proc.stdout or "{}")
    except Exception:
        return {}
    result = payload.get("result")
    tabs = result.get("tabs") if isinstance(result, dict) else result
    out = {}
    for item in tabs or []:
        if not isinstance(item, dict):
            continue
        label = (item.get("label") or "").strip()
        if item.get("tab_id") and label and not label.isdigit():
            out[item["tab_id"]] = label
    return out


def select_agents(agents, panes="", sessions="", titles=""):
    """The agents that match any selector; every agent when no selector is given.

    panes:    comma-separated Herdr pane ids. Herdr renumbers workspaces when it restarts
              (w3:p1 became w4:p1 overnight), so this is for one-off runs.
    sessions: comma-separated Claude session id prefixes. Stable while the session lives.
    titles:   case-insensitive regular expression on the pane's Herdr name and terminal title.
              Survives both a Herdr restart and a session replacement as long as the new pane
              keeps the naming convention (for example every orchestrator pane carrying the word).
    """
    want_panes = {p.strip() for p in panes.split(",") if p.strip()}
    want_sessions = {s.strip().lower() for s in sessions.split(",") if s.strip()}
    pattern = re.compile(titles, re.IGNORECASE) if titles else None
    if not (want_panes or want_sessions or pattern):
        return list(agents)
    out = []
    for agent in agents:
        if agent["pane"] in want_panes:
            out.append(agent)
        elif any(agent["session"].lower().startswith(prefix) for prefix in want_sessions):
            out.append(agent)
        elif pattern and any(pattern.search(agent.get(key) or "") for key in NAME_KEYS):
            out.append(agent)
    return out


# where a session's name can come from, most deliberate first: the name given with
# `claude --name` or `/rename` (lives in the transcript, so it travels with the session),
# Herdr's own agent name, the launcher's tab label, the terminal title
NAME_KEYS = ("session_name", "name", "tab_label", "title")


def label_of(agent):
    return next((agent.get(key) for key in NAME_KEYS if agent.get(key)), "")


def describe_selectors(args):
    parts = []
    if getattr(args, "panes", ""):
        parts.append(f"--panes {args.panes}")
    if getattr(args, "sessions", ""):
        parts.append(f"--sessions {args.sessions}")
    if getattr(args, "titles", ""):
        parts.append(f"--titles {args.titles!r}")
    return " ".join(parts)


def submit(herdr, pane, prompt):
    proc = subprocess.run([herdr, "agent", "prompt", pane, prompt], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    detail = (proc.stdout or proc.stderr or "").strip().replace("\n", " ")
    return proc.returncode == 0, detail[:300]


# ---------------------------------------------------------------- transcripts

def transcript_for(session, cache):
    if session in cache and os.path.isfile(cache[session]):
        return cache[session]
    hits = glob.glob(os.path.join(HOME, ".claude", "projects", "*", f"{session}.jsonl"))
    if not hits:
        return ""
    hits.sort(key=os.path.getmtime, reverse=True)
    cache[session] = hits[0]
    return hits[0]


def tail_rows(path):
    """[(byte offset, raw line)] for the tail of a transcript, oldest first. The first
    partial line after the seek is dropped, so every offset names a real row."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        base = 0
        if size > TAIL_BYTES:
            handle.seek(size - TAIL_BYTES)
            base = size - TAIL_BYTES + len(handle.readline())
        data = handle.read()
    rows, offset = [], base
    for raw in data.split(b"\n"):
        rows.append((offset, raw))
        offset += len(raw) + 1
    return rows


def last_context(path):
    """(tokens, mark) for one session: the context of its last assistant turn, which is
    input + cache write + cache read tokens, and the Mark naming the row it came from.

    A compaction writes a `compact_boundary` row and no assistant record, so a session that
    has not answered since one still has its pre-compaction usage row at the end of the
    transcript. Reading that row as the live context over-states it by the whole cycle: on
    2026-09-02 a session that had compacted to 19259 tokens still read as 151579, and the
    watcher submitted `/compact` four more times against a number that could not move. When
    the newest boundary is newer than the newest assistant turn, its `postTokens` is reported
    instead, as a floor estimate, and the mark says "boundary" so the caller knows to hold.
    """
    try:
        rows = tail_rows(path)
    except Exception:
        return None, None
    context = usage_mark = post = boundary_mark = None
    usage_time = boundary_time = ""
    for offset, raw in reversed(rows):
        if usage_mark is not None and boundary_mark is not None:
            break
        if not raw.strip():
            continue
        try:
            row = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        kind = row.get("type")
        if kind == "assistant" and usage_mark is None:
            usage = ((row.get("message") or {}).get("usage")) or {}
            if not usage:
                continue
            context = (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
            usage_mark = Mark(offset, "usage")
            usage_time = str(row.get("timestamp") or "")
        elif kind == "system" and boundary_mark is None and row.get("compactMetadata"):
            post = (row.get("compactMetadata") or {}).get("postTokens")
            boundary_mark = Mark(offset, "boundary")
            boundary_time = str(row.get("timestamp") or "")
    if boundary_mark is None:
        return context, usage_mark
    if usage_mark is None:
        return post, boundary_mark
    # both timestamps come from the same writer in the same format; the file is append only,
    # so the byte offsets say the same thing and answer when a timestamp is missing
    newer = boundary_time > usage_time if (boundary_time and usage_time) else boundary_mark.offset > usage_mark.offset
    if newer:
        return (post if post is not None else context), boundary_mark
    return context, usage_mark


def session_name(path, names):
    """Name the session was given with `claude --name` or `/rename`: the last custom-title row
    in its transcript. The read is incremental, each call scans only what was appended since the
    previous one, so a rename made later is picked up without rereading the file."""
    if not path:
        return ""
    entry = names.setdefault(path, {"offset": 0, "name": ""})
    try:
        size = os.path.getsize(path)
        if size < entry["offset"]:
            entry["offset"], entry["name"] = 0, ""
        if size == entry["offset"]:
            return entry["name"]
        with open(path, "rb") as handle:
            handle.seek(entry["offset"])
            chunk = handle.read()
    except Exception:
        return entry["name"]
    end = chunk.rfind(b"\n") + 1  # leave a half-written last line for the next pass
    entry["offset"] += end
    for raw in chunk[:end].splitlines():
        if b'"custom-title"' not in raw:
            continue
        try:
            row = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        if row.get("type") == "custom-title" and row.get("customTitle"):
            entry["name"] = str(row["customTitle"])
    return entry["name"]


# ---------------------------------------------------------------- decision

def effective_cooldown(state, cfg):
    """The cooldown, doubled once per submission that produced no new turn, capped at 16x.
    A session that refuses `/compact` is asked again in 15 minutes, then 30, then an hour."""
    return cfg["cooldown"] * 2 ** min(state.get("fires_without_effect", 0), MAX_BACKOFF_DOUBLINGS)


def decide(state, status, seq, ctx, mark, now, cfg):
    """Pure decision for one session. Mutates `state` (idle_since, last_ctx, ctx_at,
    last_compaction, fires_without_effect) and returns (action, reason) with action in
    hold | wait | fire | skip. `mark` is the Mark last_context read the number from."""
    if ctx is None:
        return "skip", "no usage row yet"
    kind = mark.kind if mark else "usage"
    moved = mark is not None and mark != state.get("ctx_at")
    if moved and kind == "usage":
        # a real turn happened, so whatever the last submission did or did not do is settled
        state["fires_without_effect"] = 0
        state["fire_counted"] = False
    if moved and kind == "boundary":
        state["last_compaction"] = now
        state["idle_since"] = None
    last_ctx = state.get("last_ctx")
    if last_ctx and last_ctx > 0.3 * cfg["window"] and ctx < last_ctx * COMPACTION_DROP:
        state["last_compaction"] = now
        state["idle_since"] = None
    state["last_ctx"] = ctx
    state["ctx_at"] = mark
    pct = ctx / cfg["window"]
    if status == "idle":
        if state.get("idle_since") is None or state.get("idle_seq") != seq:
            state["idle_since"] = now
            state["idle_seq"] = seq
    else:
        state["idle_since"] = None
    if kind == "boundary":
        # the number is the floor the compaction left, not a context the session grew to
        return "hold", f"{pct:.0%} ({ctx} postTokens), no turn since the compaction"
    fired = state.get("last_fire")
    if fired and mark is not None and mark == state.get("fire_mark"):
        if now - fired >= cfg["cooldown"] and not state.get("fire_counted"):
            state["fires_without_effect"] = state.get("fires_without_effect", 0) + 1
            state["fire_counted"] = True
        return "hold", "no turn since the last /compact"
    if pct < cfg["threshold"]:
        return "hold", f"{pct:.0%} below {cfg['threshold']:.0%}"
    if status != "idle":
        return "hold", f"{pct:.0%} but {status}"
    idle_for = now - state["idle_since"]
    if idle_for < cfg["idle"]:
        return "wait", f"{pct:.0%}, idle {idle_for:.0f}s of {cfg['idle']}s"
    cooldown = effective_cooldown(state, cfg)
    since_fire = now - state.get("last_fire", 0)
    if since_fire < cooldown:
        return "hold", f"{pct:.0%}, cooldown {cooldown - since_fire:.0f}s left"
    since_compaction = now - state.get("last_compaction", 0)
    if since_compaction < cfg["cooldown"]:
        return "hold", f"{pct:.0%}, compacted {since_compaction:.0f}s ago"
    state["last_fire"] = now
    state["fire_mark"] = mark
    state["fire_counted"] = False
    return "fire", f"{pct:.0%} ({ctx} tokens), idle {idle_for:.0f}s"


# ---------------------------------------------------------------- lock and stop

def pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def take_lock():
    os.makedirs(STATE_DIR, exist_ok=True)
    if os.path.isfile(LOCK_FILE):
        try:
            with open(LOCK_FILE, encoding="utf-8") as handle:
                other = int(handle.read().strip() or 0)
        except Exception:
            other = 0
        if other and other != os.getpid() and pid_alive(other):
            return other
    with open(LOCK_FILE, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    return 0


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except Exception:
        pass


def stop_requested():
    if os.path.isfile(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except Exception:
            pass
        return True
    return False


# ---------------------------------------------------------------- main loop

def one_pass(args, cfg, states, cache, quiet=False):
    agents, error = herdr_agents(args.herdr)
    if agents is None:
        log(error, quiet)
        return []
    labels = tab_labels(args.herdr) if getattr(args, "titles", "") else {}
    names = states.setdefault("_names", {})
    for agent in agents:
        agent["tab_label"] = labels.get(agent.get("tab", ""), "")
        agent["session_name"] = session_name(transcript_for(agent["session"], cache), names) if agent["session"] else ""
    selected = select_agents(agents, getattr(args, "panes", ""), getattr(args, "sessions", ""), getattr(args, "titles", ""))
    selectors = describe_selectors(args)
    if selectors and not selected:
        # say it once, not every 30 seconds: this is what a Herdr renumbering looks like
        if not states.get("_nomatch"):
            known = ", ".join(f"{a['pane']} {a['session'][:8]} '{label_of(a)}'" for a in agents) or "no Claude pane"
            log(f"no Claude pane matches {selectors}; Herdr knows {known}; waiting", quiet)
        states["_nomatch"] = True
    elif states.pop("_nomatch", None):
        log(f"{selectors} matches again: " + ", ".join(a["pane"] for a in selected), quiet)
    rows = []
    now = time.time()
    for agent in selected:
        session = agent["session"]
        if not session:
            continue
        state = states.setdefault(session, {})
        path = transcript_for(session, cache)
        ctx, mark = last_context(path) if path else (None, None)
        # Herdr reports `idle` after an ordinary turn and `done` after a compaction; both mean
        # the session is waiting for input, which is the boundary this watcher looks for
        status = "idle" if agent["status"] in cfg["idle_states"] else agent["status"]
        action, reason = decide(state, status, agent["seq"], ctx, mark, now, cfg)
        rows.append((agent["pane"], session[:8], agent["status"], ctx, action, reason, label_of(agent)))
        if action == "fire":
            # decide() already stamped last_fire and fire_mark, so both paths share one clock
            if args.dry_run:
                log(f"{agent['pane']} {session[:8]} would submit {args.prompt!r}: {reason} (dry run)", quiet)
                continue
            ok, detail = submit(args.herdr, agent["pane"], args.prompt)
            log(f"{agent['pane']} {session[:8]} submitted {args.prompt!r}: {reason}; herdr {'ok' if ok else 'FAILED'} {detail}", quiet)
        elif action == "wait" and not quiet:
            log(f"{agent['pane']} {session[:8]} {reason}", quiet)
    return rows


def print_table(rows, cfg):
    print(f"{'pane':8} {'session':9} {'title':20} {'status':8} {'context':>9} {'pct':>5}  decision")
    for pane, session, status, ctx, action, reason, title in rows:
        pct = f"{ctx / cfg['window']:.0%}" if ctx else "?"
        print(f"{pane:8} {session:9} {title[:20]:20} {status:8} {ctx if ctx is not None else '?':>9} {pct:>5}  {action}: {reason}")
    if not rows:
        print("(no Claude agents reported by Herdr)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--panes", default="", help="comma-separated Herdr pane ids to watch (Herdr renumbers them on restart); default all Claude panes")
    parser.add_argument("--sessions", default="", help="comma-separated Claude session id prefixes to watch")
    parser.add_argument("--titles", default="", help="case-insensitive regular expression on the pane title, e.g. orques|orchestr")
    parser.add_argument("--window", type=int, default=200000, help="context window in tokens (default 200000)")
    parser.add_argument("--threshold", type=float, default=0.65, help="fraction of the window that arms a compaction (default 0.65)")
    parser.add_argument("--idle", type=int, default=90, help="seconds of continuous idleness before submitting (default 90)")
    parser.add_argument("--idle-states", default="idle,done", help="Herdr states that count as waiting for input (default idle,done)")
    parser.add_argument("--cooldown", type=int, default=900, help="seconds between submissions to one session (default 900)")
    parser.add_argument("--interval", type=int, default=30, help="seconds between passes (default 30)")
    parser.add_argument("--prompt", default="/compact", help="text submitted to the pane (default /compact)")
    parser.add_argument("--herdr", default="herdr", help="Herdr CLI executable (default herdr on PATH)")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--status", action="store_true", help="one pass, print the decision table, never submit")
    parser.add_argument("--dry-run", action="store_true", help="never submit, only log what would happen")
    parser.add_argument("--stop", action="store_true", help="ask the running watcher to exit")
    args = parser.parse_args()
    if args.titles:
        try:
            re.compile(args.titles, re.IGNORECASE)
        except re.error as error:
            parser.error(f"--titles is not a valid regular expression: {error}")

    if args.stop:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STOP_FILE, "w", encoding="utf-8") as handle:
            handle.write(str(time.time()))
        print(f"stop requested through {STOP_FILE}")
        return 0

    cfg = {
        "window": args.window,
        "threshold": args.threshold,
        "idle": args.idle,
        "cooldown": args.cooldown,
        "idle_states": {s.strip() for s in args.idle_states.split(",") if s.strip()},
    }
    states, cache = {}, {}

    if args.status:
        args.dry_run = True
        print_table(one_pass(args, cfg, states, cache, quiet=True), cfg)
        return 0

    other = take_lock()
    if other:
        print(f"another watcher is running (pid {other}); use --stop first")
        return 1
    log(f"watcher start pid {os.getpid()} select={describe_selectors(args) or 'all Claude panes'} window={args.window} threshold={args.threshold} idle={args.idle}s cooldown={args.cooldown}s interval={args.interval}s dry_run={args.dry_run}")
    try:
        while True:
            one_pass(args, cfg, states, cache)
            if args.once or stop_requested():
                break
            slept = 0
            while slept < args.interval:
                time.sleep(1)
                slept += 1
                if stop_requested():
                    log("stop file seen, exiting")
                    return 0
    except KeyboardInterrupt:
        log("interrupted, exiting")
    finally:
        release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
