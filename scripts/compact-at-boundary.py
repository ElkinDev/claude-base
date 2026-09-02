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
import glob
import json
import os
import re
import subprocess  # noqa: F401  (the tests reach subprocess.run through this module)
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import herdr_panes  # noqa: E402  (the path above is what makes it importable)
from herdr_panes import (  # noqa: E402
    NAME_KEYS, describe_selectors, herdr_agents, label_of, rotate_and_append, select_agents,
    stamp, submit, tab_labels,
)

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".claude")
LOCK_FILE = os.path.join(STATE_DIR, "compact-at-boundary.lock")
STOP_FILE = os.path.join(STATE_DIR, "compact-at-boundary.stop")
LOG_FILE = os.path.join(STATE_DIR, "compact-at-boundary.log")
TAIL_BYTES = 512 * 1024
COMPACTION_DROP = 0.6      # a context that falls below 60 percent of its last value was compacted


def log(message, quiet=False):
    line = f"{stamp()} {message}"
    if not quiet:
        print(line, flush=True)
    rotate_and_append(LOG_FILE, line)


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


def last_context(path):
    """Context size of the last assistant turn: input + cache write + cache read tokens."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > TAIL_BYTES:
                handle.seek(size - TAIL_BYTES)
                handle.readline()
            lines = handle.read().splitlines()
    except Exception:
        return None
    for raw in reversed(lines):
        try:
            row = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        if row.get("type") != "assistant":
            continue
        usage = ((row.get("message") or {}).get("usage")) or {}
        if not usage:
            continue
        return (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
    return None


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

def decide(state, status, seq, ctx, now, cfg):
    """Pure decision for one session. Mutates `state` (idle_since, last_ctx, last_compaction)
    and returns (action, reason) with action in hold | wait | fire | skip."""
    if ctx is None:
        return "skip", "no usage row yet"
    last_ctx = state.get("last_ctx")
    if last_ctx and last_ctx > 0.3 * cfg["window"] and ctx < last_ctx * COMPACTION_DROP:
        state["last_compaction"] = now
        state["idle_since"] = None
    state["last_ctx"] = ctx
    pct = ctx / cfg["window"]
    if status == "idle":
        if state.get("idle_since") is None or state.get("idle_seq") != seq:
            state["idle_since"] = now
            state["idle_seq"] = seq
    else:
        state["idle_since"] = None
    if pct < cfg["threshold"]:
        return "hold", f"{pct:.0%} below {cfg['threshold']:.0%}"
    if status != "idle":
        return "hold", f"{pct:.0%} but {status}"
    idle_for = now - state["idle_since"]
    if idle_for < cfg["idle"]:
        return "wait", f"{pct:.0%}, idle {idle_for:.0f}s of {cfg['idle']}s"
    since_fire = now - state.get("last_fire", 0)
    if since_fire < cfg["cooldown"]:
        return "hold", f"{pct:.0%}, cooldown {cfg['cooldown'] - since_fire:.0f}s left"
    since_compaction = now - state.get("last_compaction", 0)
    if since_compaction < cfg["cooldown"]:
        return "hold", f"{pct:.0%}, compacted {since_compaction:.0f}s ago"
    return "fire", f"{pct:.0%} ({ctx} tokens), idle {idle_for:.0f}s"


# ---------------------------------------------------------------- lock and stop
# One instance per machine, and a stop file instead of a signal, so `--stop` works from any
# shell on Windows too. The behavior is in herdr_panes; the paths are this script's own.

def take_lock():
    return herdr_panes.take_lock(LOCK_FILE)


def release_lock():
    herdr_panes.release_lock(LOCK_FILE)


def stop_requested():
    return herdr_panes.stop_requested(STOP_FILE)


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
        ctx = last_context(path) if path else None
        # Herdr reports `idle` after an ordinary turn and `done` after a compaction; both mean
        # the session is waiting for input, which is the boundary this watcher looks for
        status = "idle" if agent["status"] in cfg["idle_states"] else agent["status"]
        action, reason = decide(state, status, agent["seq"], ctx, now, cfg)
        rows.append((agent["pane"], session[:8], agent["status"], ctx, action, reason, label_of(agent)))
        if action == "fire":
            if args.dry_run:
                log(f"{agent['pane']} {session[:8]} would submit {args.prompt!r}: {reason} (dry run)", quiet)
                state["last_fire"] = now
                continue
            ok, detail = submit(args.herdr, agent["pane"], args.prompt)
            state["last_fire"] = now
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
