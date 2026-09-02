#!/usr/bin/env python3
"""Resume a pane when its five hour window reopens, and never when the week is spent.

A subscription account carries two meters, a five hour window and a seven day window, each with a
utilization and a reset time. A session that hits the five hour ceiling stops in the middle of its
queue and stays stopped until a person notices, even though the meter itself announces when it
comes back. This watcher reads both meters with zero model cost, notes when the account is dry,
waits for the announced reset, confirms the meter recovered, and submits one resume prompt to the
stopped pane. It refuses to wake when the seven day meter is at or above the cap the operator set,
because a wake at that point only turns weekly quota into unfinished work.

Usage:
  python quota-wake.py [--account a,b] [--titles regex | --panes w3:p1,w3:p3 | --sessions abc123]
                       [--cap 80] [--interval 300] [--grace 120] [--resume-below 50]
                       [--retry-for 1800] [--prompt-file brief.txt] [--status|--dry-run|--once|--stop]

With no selector the candidate is the orchestrator pane only. `--status` prints one row per
account and exits, `--dry-run` decides and logs without submitting, `--stop` asks the running
watcher to exit. See docs/03-features/F14-quota-wake.md for the states and the decisions.
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import herdr_panes  # noqa: E402  (the path above is what makes it importable)
import usage_meters as meters  # noqa: E402
from quota_states import advance, classify, clock, prompt_text, read_template  # noqa: E402
from herdr_panes import (  # noqa: E402
    describe_selectors, label_of, list_panes, pane_account, role_candidates, rotate_and_append, select_agents, submit,
)

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".claude")
LOCK_FILE = os.path.join(STATE_DIR, "quota-wake.lock")
STOP_FILE = os.path.join(STATE_DIR, "quota-wake.stop")
LOG_FILE = os.path.join(STATE_DIR, "quota-wake.log")
STATE_FILE = os.path.join(STATE_DIR, "quota-wake.state.json")

# Herdr states that mean the pane is waiting for input. `done` is what Herdr reports right after
# a compaction, `stalled` after five seconds without output, which is what a pane out of tokens
# looks like. A `working` pane is not out of tokens, whatever the meter said a moment ago.
WAKE_STATES = ("idle", "done", "stalled")
# with no selector, the wake goes to the orchestrator pane only: it owns the queue, and waking
# every idle pane of an account would start work nobody is coordinating
ROLE_PATTERN = re.compile("orchestrator", re.IGNORECASE)
WAKE_HISTORY = 200         # wakes kept in the state file, newest first


def log(message, quiet=False):
    line = f"{herdr_panes.stamp()} {message}"
    if not quiet:
        print(line, flush=True)
    rotate_and_append(LOG_FILE, line)


# ---------------------------------------------------------------- lock, stop and state
# One instance per machine, and a stop file instead of a signal, so `--stop` works from any shell
# on Windows too. The behavior is in herdr_panes; the paths are this script's own.

def take_lock():
    return herdr_panes.take_lock(LOCK_FILE)


def release_lock():
    herdr_panes.release_lock(LOCK_FILE)


def stop_requested():
    return herdr_panes.stop_requested(STOP_FILE)


def new_store():
    return {"accounts": {}, "wakes": {}}


def load_state():
    """The state from disk, pruned to the newest wakes. An unreadable file is a fresh start:
    the worst it costs is one extra wake, and refusing to run would cost the whole queue."""
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            store = json.load(handle)
    except Exception:
        return new_store()
    store.setdefault("accounts", {})
    wakes = store.setdefault("wakes", {})
    if len(wakes) > WAKE_HISTORY:
        newest = sorted(wakes.items(), key=lambda item: item[1].get("at", 0), reverse=True)[:WAKE_HISTORY]
        store["wakes"] = dict(newest)
    return store


def save_state(store, dry_run=False):
    if dry_run:
        return
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(store, handle, indent=1)
    except Exception as error:
        log(f"state file not written: {error}", quiet=True)


# ---------------------------------------------------------------- panes and accounts

def candidates(args, agents, account):
    """(panes, ambiguous) for one account: the panes a wake may target, which are the panes the
    selectors name or the orchestrator by default, and the panes a role-labelled tab could not
    tell apart, which the caller logs so the operator knows a name or a selector is missing."""
    if args.panes or args.sessions or args.titles:
        selected, ambiguous = select_agents(agents, args.panes, args.sessions, args.titles), []
    else:
        selected, ambiguous = role_candidates(agents, ROLE_PATTERN)
    mine = [a for a in selected if a["session"] and pane_account(a, account) == account]
    return mine, [a for a in ambiguous if pane_account(a, account) == account]


def watched_accounts(args, agents):
    """The accounts to probe: the names given on the command line, narrowed to the ones the panes
    carry when the panes say; the account of this process when nothing else names one."""
    named = [name.strip() for name in (args.account or "").split(",") if name.strip()]
    from_panes = []
    for agent in agents:
        name = pane_account(agent)
        if name and name not in from_panes:
            from_panes.append(name)
    if from_panes:
        narrowed = [name for name in from_panes if not named or name in named]
        if narrowed:
            return narrowed
    return named or [meters.account_name()]


def config_dir_of(account, args):
    """Where the account's credentials live. The profile this process runs under keeps its own
    directory, whatever it is called; every other account is a folder of the switcher's layout."""
    if account == meters.account_name():
        return meters.default_config_dir()
    return meters.config_dir_for(account, args.accounts_dir)


# ---------------------------------------------------------------- one pass

def wake_panes(args, cfg, account, state, panes, store, now, quiet):
    """One wake per candidate pane, at most once per pane per reset."""
    if not panes:
        log(f"{account} recovered but no pane matches {describe_selectors(args) or 'the orchestrator role'}", quiet)
        return
    template, note = read_template(args.prompt_file)
    if note:
        log(note, quiet=True)
    text = prompt_text(template, cfg, account, state, now)
    for agent in panes:
        session, pane = agent["session"], agent["pane"]
        if agent["status"] not in WAKE_STATES:
            log(f"{pane} {session[:8]} is {agent['status']}, skipped: a pane still working is not out of tokens", quiet)
            continue
        key = f"{session}|{state.get('resets_at') or 'none'}"
        if key in store["wakes"]:
            log(f"{pane} {session[:8]} already woken for the window that reset at {state.get('resets_at') or 'an unannounced time'}", quiet)
            continue
        if args.dry_run:
            log(f"{pane} {session[:8]} would wake {account}: {text!r} (dry run)", quiet)
            continue
        ok, detail = submit(args.herdr, pane, text)
        store["wakes"][key] = {"pane": pane, "account": account, "at": now}
        log(f"{pane} {session[:8]} woken for {account} '{label_of(agent)}'; herdr {'ok' if ok else 'FAILED'} {detail}", quiet)


def one_pass(args, cfg, store, probe, now=None, quiet=False):
    """One decision pass over every watched account. Returns (rows, herdr_error): one row per
    account for --status, and the reason Herdr could not be reached when it could not."""
    now = time.time() if now is None else now
    agents, herdr_error = list_panes(args.herdr)
    if herdr_error:
        # never quiet: an operator reading --status has to see why the panes column is empty
        log(f"{herdr_error}; the meters are still read, no pane can be woken")
    rows = []
    for account in watched_accounts(args, agents):
        state = store["accounts"].setdefault(account, {"state": "unknown"})
        panes, ambiguous = candidates(args, agents, account)
        if state.get("due_at") and now < state["due_at"]:
            log(f"{account} dry, next probe at {clock(state['due_at'])}", quiet)
        else:
            try:
                snapshot = probe(account)
            except Exception as error:  # belt and braces: a probe that raises must not end the night
                snapshot = meters.empty_reading(account, f"probe raised: {error}")
            verdict, message = advance(state, snapshot, now, cfg)
            if verdict in ("capped", "unknown", "unknown-warn", "wait", "give-up", "retry", "wake") or not quiet:
                log(f"{account} {message}", quiet)
            if verdict == "wake":
                if ambiguous:
                    log(f"{','.join(a['pane'] for a in ambiguous)} skipped, ambiguous: no name of their own under a "
                        f"tab that carries the role; name the pane or pass a selector", quiet)
                wake_panes(args, cfg, account, state, panes, store, now, quiet)
        rows.append({"account": account, "five_hour": state.get("five_hour"), "seven_day": state.get("seven_day"),
                     "state": state.get("state", "unknown"), "resets_at": state.get("resets_at", ""),
                     "panes": [a["pane"] for a in panes], "due_at": state.get("due_at", 0)})
    return rows, herdr_error


def print_table(rows, store):
    print(f"{'account':16} {'5h':>4} {'7d':>4} {'state':8} {'reset':26} {'panes':16} last wake")
    for row in rows:
        last = max((entry.get("at", 0) for entry in store["wakes"].values() if entry.get("account") == row["account"]), default=0)
        five = "?" if row["five_hour"] is None else row["five_hour"]
        seven = "?" if row["seven_day"] is None else row["seven_day"]
        print(f"{row['account'][:16]:16} {five:>4} {seven:>4} {row['state']:8} {(row['resets_at'] or '-')[:26]:26} "
              f"{(','.join(row['panes']) or '-')[:16]:16} {clock(last) if last else '-'}")
    if not rows:
        print("(no account to watch: pass --account or open a pane with the launcher)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--panes", default="", help="comma-separated Herdr pane ids to wake (Herdr renumbers them on restart)")
    parser.add_argument("--sessions", default="", help="comma-separated Claude session id prefixes to wake")
    parser.add_argument("--titles", default="", help="case-insensitive regular expression on the pane name; default the orchestrator role")
    parser.add_argument("--account", default="", help="comma-separated account names to watch; default the accounts the panes name")
    parser.add_argument("--accounts-dir", dest="accounts_dir", default="", help="folder holding the account profiles (default ~/.claude-accounts)")
    parser.add_argument("--cap", type=int, default=80, help="seven day percent at or above which no wake is submitted (default 80, 100 disables)")
    parser.add_argument("--interval", type=int, default=300, help="seconds between passes (default 300)")
    parser.add_argument("--grace", type=int, default=120, help="seconds to wait past the announced reset before probing again (default 120)")
    parser.add_argument("--resume-below", dest="resume_below", type=int, default=50, help="five hour percent that counts as recovered (default 50)")
    parser.add_argument("--retry-for", dest="retry_for", type=int, default=1800, help="seconds of retries when the meter is still at the ceiling (default 1800)")
    parser.add_argument("--prompt-file", dest="prompt_file", default="", help="file whose text replaces the built-in resume prompt")
    parser.add_argument("--herdr", default="herdr", help="Herdr CLI executable (default herdr on PATH)")
    parser.add_argument("--once", action="store_true", help="one pass, then exit; exit code 2 when Herdr never answered")
    parser.add_argument("--status", action="store_true", help="one pass, print the table, never submit")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="never submit, only log what would happen")
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

    cfg = {"cap": args.cap, "grace": args.grace, "resume_below": args.resume_below, "retry_for": args.retry_for}
    store = load_state()

    def probe(account):
        return meters.read_snapshot(config_dir_of(account, args))

    if args.status:
        args.dry_run = True
        rows, _ = one_pass(args, cfg, store, probe, quiet=True)
        print_table(rows, store)
        return 0

    other = take_lock()
    if other:
        print(f"another quota wake is running (pid {other}); use --stop first")
        return 1
    log(f"quota wake start pid {os.getpid()} select={describe_selectors(args) or ROLE_PATTERN.pattern} cap={args.cap}% "
        f"interval={args.interval}s grace={args.grace}s resume_below={args.resume_below}% retry_for={args.retry_for}s dry_run={args.dry_run}")
    try:
        while True:
            rows, herdr_error = one_pass(args, cfg, store, probe)
            save_state(store, args.dry_run)
            if args.once:
                return 2 if herdr_error else 0  # the meters were read, but nothing was wakeable
            if stop_requested():
                break
            for _ in range(next_wait(rows, args.interval)):
                time.sleep(1)
                if stop_requested():
                    log("stop file seen, exiting")
                    return 0
    except KeyboardInterrupt:
        log("interrupted, exiting")
    finally:
        release_lock()
    return 0


def next_wait(rows, interval):
    """Seconds until the next pass: the interval, or the announced reset when it comes first, so
    a two hour window is not lost to a five minute grid."""
    now = time.time()
    due = [row["due_at"] - now for row in rows if row.get("due_at", 0) > now]
    return max(1, int(min([interval] + due)))


if __name__ == "__main__":
    sys.exit(main())
