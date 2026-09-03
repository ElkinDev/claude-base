#!/usr/bin/env python3
"""The pane plumbing every resident script of this kit shares: what Herdr knows, which of those
panes a selector means, how a prompt is submitted, and the one-instance discipline.

Used by `scripts/compact-at-boundary.py`, which compacts a session at an idle boundary, and by
`scripts/quota-wake.py`, which resumes a pane when its five hour window reopens. Neither calls a
model. Both keep their own log, lock and stop file, so the two processes never collide; this
module holds only the behavior, never the paths.
"""
import json
import os
import re
import subprocess

# A parent without a console (pythonw, a logon task) would let every herdr child open its own
# console window for the length of the call: a black flash every poll. This flag keeps the
# child windowless; its output is captured either way. Zero off Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
from datetime import datetime

# where a session's name can come from, most deliberate first: the name given with
# `claude --name` or `/rename` (lives in the transcript, so it travels with the session),
# Herdr's own agent name, the launcher's tab label, the terminal title
NAME_KEYS = ("session_name", "name", "tab_label", "title")


def stamp(now=None):
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def rotate_and_append(log_file, line, limit=1024 * 1024):
    """One line into a log capped at `limit` bytes with a single rotation. Never raises: a log
    that cannot be written must not stop the watcher that writes it."""
    try:
        if os.path.isfile(log_file) and os.path.getsize(log_file) > limit:
            os.replace(log_file, log_file + ".1")
        with open(log_file, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- Herdr

def herdr_agents(herdr):
    """Claude agents Herdr knows: [{pane, session, status, seq, cwd, title}]. Empty list on any failure."""
    try:
        proc = subprocess.run([herdr, "agent", "list"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, creationflags=NO_WINDOW)
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
        proc = subprocess.run([herdr, "tab", "list"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, creationflags=NO_WINDOW)
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
    proc = subprocess.run([herdr, "agent", "prompt", pane, prompt], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=NO_WINDOW)
    detail = (proc.stdout or proc.stderr or "").strip().replace("\n", " ")
    return proc.returncode == 0, detail[:300]


# the launcher writes `cc-<account>-<role>` on the tab it creates, which is the only place a
# pane's account and role are visible from outside the process
ROLE_SUFFIXES = ("orchestrator", "lane", "research")
# a pane named on purpose answers for itself; the tab label speaks only for a pane with no name
# of its own, because one tab can hold panes of several roles. The terminal title is not a name:
# Claude Code rewrites it with a summary of the current task, so a pane judged on its title is
# judged on whatever it happens to be doing, and every working pane carries one.
PANE_NAME_KEYS = ("session_name", "name")


def pane_account(agent, fallback=""):
    """The account a pane runs under. The launcher labels its tab `cc-<account>` plus the role,
    which is the only place the pane's CLAUDE_CONFIG_DIR is visible from outside the process;
    a pane opened by hand falls back to the caller's default."""
    for key in ("tab_label", "name", "title"):
        label = (agent.get(key) or "").strip()
        if not label.startswith("cc-"):
            continue
        name = label[3:]
        for suffix in ROLE_SUFFIXES:
            if name.endswith("-" + suffix):
                name = name[: -len(suffix) - 1]
        if name:
            return name
    return fallback


def list_panes(herdr):
    """Every Claude pane Herdr knows, with its tab label resolved, and the error when it has none."""
    agents, error = herdr_agents(herdr)
    if agents is None:
        return [], error
    labels = tab_labels(herdr)
    for agent in agents:
        agent["tab_label"] = labels.get(agent.get("tab", ""), "")
    return agents, ""


def pane_names(agent):
    """The names a pane carries of its own, in the order a reader would trust them."""
    return [agent[key] for key in PANE_NAME_KEYS if agent.get(key)]


def has_role(agent, pattern):
    """Whether a pane carries the role, by its own name first and by its tab label only when it
    has no name: an orchestrator tab can hold a lane pane, and that pane is not the orchestrator."""
    own = pane_names(agent)
    if own:
        return any(pattern.search(value) for value in own)
    return bool(pattern.search(agent.get("tab_label") or ""))


def pane_order(pane):
    """Sort key for a Herdr pane id, so `w1:p2` comes before `w1:p10` and a pane id that does not
    end in a number sorts after the ones that do instead of raising."""
    workspace, _, tail = str(pane or "").rpartition(":")
    number = tail[1:] if tail[:1].lower() == "p" else tail
    return (workspace, 0, int(number), "") if number.isdigit() else (workspace, 1, 0, str(pane or ""))


def role_candidates(agents, pattern):
    """(candidates, ambiguous) for a role match with no selector.

    A pane with a name of its own answers for itself. A pane with no name is judged by its tab
    label, and when a tab carrying the role holds several such panes there is nothing in the
    payload to tell them apart, so only the lowest numbered one is a candidate and the rest come
    back as ambiguous for the caller to log. The cure on the operator's side is a name or a
    selector, never a guess on this side.
    """
    matched = [agent for agent in agents if has_role(agent, pattern)]
    by_tab = {}
    for agent in matched:
        if not pane_names(agent):
            by_tab.setdefault(agent.get("tab", ""), []).append(agent)
    ambiguous = []
    for panes in by_tab.values():
        ambiguous.extend(sorted(panes, key=lambda agent: pane_order(agent.get("pane")))[1:])
    skipped = {agent.get("pane") for agent in ambiguous}
    return [agent for agent in matched if agent.get("pane") not in skipped], ambiguous


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


def take_lock(lock_file):
    """0 when this process owns the lock, the pid of the live owner when another has it."""
    os.makedirs(os.path.dirname(lock_file) or ".", exist_ok=True)  # a bare filename has no dirname
    if os.path.isfile(lock_file):
        try:
            with open(lock_file, encoding="utf-8") as handle:
                other = int(handle.read().strip() or 0)
        except Exception:
            other = 0
        if other and other != os.getpid() and pid_alive(other):
            return other
    with open(lock_file, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    return 0


def release_lock(lock_file):
    try:
        os.remove(lock_file)
    except Exception:
        pass


def stop_requested(stop_file):
    if os.path.isfile(stop_file):
        try:
            os.remove(stop_file)
        except Exception:
            pass
        return True
    return False
