"""agents-in-flight.py: how many Agent-tool subagents a session has in flight, read from its transcript
or from the Stop hook's log, never from memory. The session running the agents writes no count; the hook
claude/hooks/inflight.py logs and this script reads.

Default: one line from the transcript, agents in flight: N (description since HH:MM; ...). A spawn is a tool result
with status async_launched (toolUseResult.agentId and .description); an end is a task-notification naming that id
with a terminal status, or a TaskStop result naming it; an end already queued at the end of the file counts as an
end. A partial last line (still being written) is ignored, as the hook ignores it.
--exits --since "<local>": the live count at every wake of the session and the wakes under the floor.
--spawns --since "<local>": launches per local day, by kind of the launch description (word match).
--log --session <prefix> --since "<local>": the day's number from hooks/inflight.log (written by the Stop hook):
the share of the logged time with the floor met and every gap under the floor of --gap minutes or more; intervals
longer than 30 minutes (the session away, the machine off) are neither counted nor read as gaps; the last entry is
not charged. --log-file points at another log (tests).

CLAUDE_PROJECTS_DIRS (a semicolon list) and CLAUDE_INFLIGHT_LOG move the transcript folders and the log; without
them the transcripts are read from ~/.claude/projects and the log from ~/.claude/hooks/inflight.log.
"""
import argparse
import datetime
import glob
import json
import os
import re

HOME = os.path.expanduser("~")
DEFAULT_DIRS = [os.path.join(HOME, ".claude", "projects")]
DEFAULT_LOG = os.path.join(HOME, ".claude", "hooks", "inflight.log")
TASK_RE = re.compile(r"<task-id>([0-9a-f]+)</task-id>.*?<status>(\w+)</status>", re.S)
STOP_RE = re.compile(r"Successfully stopped task: ([0-9a-f]{12,})")
END_STATES = ("completed", "failed", "killed", "cancelled", "error", "stopped")
CAP_MINUTES = 30
STALE_MINUTES = 60


def project_dirs():
    raw = os.environ.get("CLAUDE_PROJECTS_DIRS")
    dirs = [d.strip() for d in raw.split(";") if d.strip()] if raw else list(DEFAULT_DIRS)
    return dirs


def find(prefix):
    """The newest transcript whose name starts with the prefix, in any project folder."""
    for folder in project_dirs():
        matches = sorted(glob.glob(os.path.join(folder, "*", prefix + "*.jsonl")), key=os.path.getmtime)
        matches += sorted(glob.glob(os.path.join(folder, prefix + "*.jsonl")), key=os.path.getmtime)
        if matches:
            return matches[-1]
    raise SystemExit("no transcript for " + (prefix or "<any session>"))


def local(ts):
    dt = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone()


def parse_since(text, aware):
    if not text:
        return None
    try:
        dt = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        raise SystemExit('--since must be local "YYYY-MM-DD HH:MM", got %r' % text)
    return dt.astimezone() if aware else dt


def read_log(a):
    """The day's number from the Stop hook's log."""
    since = parse_since(a.since, aware=False)
    path = a.log_file or os.environ.get("CLAUDE_INFLIGHT_LOG") or DEFAULT_LOG
    if not os.path.isfile(path):
        print("no log at %s" % path)
        return
    entries = []
    others = {}  # other session prefixes and their last entry time, to flag a restarted session
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split(" ", 3)  # date, time, session prefix, "N descriptions"
            if len(parts) < 4:
                continue
            try:
                t = datetime.datetime.strptime(parts[0] + " " + parts[1], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if not parts[2].startswith(a.session):
                if not parts[2].startswith("rtest"):
                    others[parts[2]] = t
                continue
            try:
                n_desc = parts[3].split(" ", 1)
                n = int(n_desc[0])
            except ValueError:
                continue
            if since and t < since:
                continue
            entries.append((t, n, n_desc[1] if len(n_desc) > 1 else ""))
    if not entries:
        recent = sorted(((t, p) for p, t in others.items() if since is None or t >= since), reverse=True)
        print("log %s: no entries since %s%s" % (a.session, a.since or "start",
              "; OTHER sessions logging in the window: %s" % ", ".join("%s (last %s)" % (p, t.strftime("%m-%d %H:%M")) for t, p in recent[:4]) if recent else "; no session logged in the window (is the hook armed in this profile?)"))
        return
    newer = sorted((t, p) for p, t in others.items() if t > entries[-1][0])
    newer_note = "; NEWER sessions logging after this one's last entry: %s" % ", ".join("%s (last %s)" % (p, t.strftime("%H:%M")) for t, p in newer) if newer else ""
    total = met = 0.0
    gaps = []
    gap_start = None

    def close_gap(t):
        nonlocal gap_start
        if gap_start is not None and (t - gap_start[0]).total_seconds() / 60.0 >= a.gap:
            gaps.append((gap_start[0], t, gap_start[1]))
        gap_start = None

    for i, (t, n, _) in enumerate(entries):
        # this entry's own count first closes or opens a gap
        if n >= a.floor:
            close_gap(t)
        elif gap_start is None:
            gap_start = (t, n)
        if i + 1 == len(entries):
            break  # the last entry is charged nothing
        minutes = (entries[i + 1][0] - t).total_seconds() / 60.0
        if minutes > CAP_MINUTES:
            # the session was away or the machine off: the interval is not counted and an open gap dies unread
            gap_start = None
            continue
        total += minutes
        if n >= a.floor:
            met += minutes
    last = entries[-1]
    if gap_start is not None:
        close_gap(last[0])  # a gap still open at the last entry is read up to it
    stale = (datetime.datetime.now() - last[0]).total_seconds() / 60.0 > STALE_MINUTES
    print("log %s since %s: %d entries, %.1f h logged, floor %d met %.0f percent of it; gaps under the floor of %d min or more: %d%s; last %s live=%d (%s)%s" % (
        a.session, a.since or "start", len(entries), total / 60.0, a.floor, 100.0 * met / total if total else 0.0, a.gap, len(gaps),
        "".join(" [%s to %s, %d live]" % (g[0].strftime("%H:%M"), g[1].strftime("%H:%M"), g[2]) for g in gaps),
        last[0].strftime("%H:%M"), last[1], last[2][:160],
        ("; STALE: last entry over %d min old, the session may be gone or restarted" % STALE_MINUTES if stale else "") + newer_note))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="", help="session id prefix; empty reads the newest transcript, and with --log every session")
    ap.add_argument("--since", help='local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--exits", action="store_true")
    ap.add_argument("--floor", type=int, default=3)
    ap.add_argument("--spawns", action="store_true", help="launches per local day since --since, by kind of description")
    ap.add_argument("--log", action="store_true", help="read hooks/inflight.log (written by the Stop hook) instead of the transcript")
    ap.add_argument("--log-file", help="another log file, for tests")
    ap.add_argument("--gap", type=int, default=20, help="minutes under the floor that count as a gap, with --log")
    ap.add_argument("--transcript", help="a transcript path instead of --session, for tests")
    a = ap.parse_args()
    if a.log:
        return read_log(a)
    path = a.transcript or find(a.session)
    since = parse_since(a.since, aware=True)
    live = {}
    wakes = []
    spawns = []
    pending = set()  # ends seen in a queue record before the user record that delivers them
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.endswith("\n"):
                break  # a partial last line still being written; the hook waits for it too
            if '"async_launched"' in line:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                r = o.get("toolUseResult") or {}
                if isinstance(r, dict) and r.get("status") == "async_launched" and r.get("agentId"):
                    desc = " ".join(str(r.get("description") or "?").split())
                    live[r["agentId"]] = (desc, o.get("timestamp", ""))
                    spawns.append((o.get("timestamp", ""), desc))
                continue
            ends = {tid for tid, st in TASK_RE.findall(line) if st in END_STATES} if "<task-notification>" in line else set()
            if '"type":"user"' in line:
                try:
                    o = json.loads(line)
                except ValueError:
                    o = None
                if o and o.get("type") == "user":
                    c = (o.get("message") or {}).get("content")
                    text = None
                    if isinstance(c, str):
                        text = c
                    elif isinstance(c, list) and c and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                        text = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
                    if text is not None:
                        kind = "wake" if "<task-notification>" in text[:40] else "peer" if "<cross-session-message" in text[:80] else "system" if text.startswith("<") else "prompt"
                        wakes.append((o.get("timestamp", ""), kind, len(live)))  # the count at the previous turn's exit
                        for tid in pending | ends:
                            live.pop(tid, None)
                        pending = set()
                        continue
            if ends:
                pending |= ends  # a queue record: the end is delivered at the next wake
            if "Successfully stopped task" in line:
                for tid in STOP_RE.findall(line):
                    live.pop(tid, None)
    for tid in pending:
        live.pop(tid, None)
    names = "; ".join("%s since %s" % (d, local(t).strftime("%H:%M") if t else "?") for d, t in live.values())
    print("agents in flight: %d (%s) [%s]" % (len(live), names, os.path.basename(path)[:8]))
    if a.exits:
        sel = [(local(t), k, n) for t, k, n in wakes if t and (since is None or local(t) >= since)]
        hist = {}
        for _, _, n in sel:
            hist[n] = hist.get(n, 0) + 1
        print("wakes since %s: %d; live count at the wake: %s" % (a.since or "start", len(sel), ", ".join("%d x%d" % (k, hist[k]) for k in sorted(hist))))
        under = [(t, k, n) for t, k, n in sel if n < a.floor]
        print("under the floor of %d: %d of %d wakes" % (a.floor, len(under), len(sel)))
        for t, k, n in under:
            print("  %s %s live=%d" % (t.strftime("%H:%M"), k, n))
    if a.spawns:
        # Order matters: a description that opens with "Lane " is an implementation whatever else it says,
        # so its rule is read before the wider ones. A round of notes was read as docs until it was.
        kinds = [("implement", r"^lane\b"), ("review", r"review"), ("diagnosis", r"diagnos|trace|analy|fact check|confirm|census|inventory"),
                 ("bench", r"bench|round|device|emulator|exploration"),
                 ("docs", r"brief|draft|ledger|notes|changelog|terms|docs|checklist|clone|describe|mockup|plan|design"),
                 ("implement", r"lane|fix|split|merge|rebuild|repoint|fold|port|rebase|commit|patch|phase|update")]
        per = {}
        for t, d in spawns:
            if not t:
                continue
            lt = local(t)
            if since and lt < since:
                continue
            day = lt.strftime("%m-%d")
            kind = next((k for k, rx in kinds if re.search(rx, d, re.I)), "other")
            per.setdefault(day, {}).setdefault(kind, 0)
            per[day][kind] += 1
        for day in sorted(per):
            tot = sum(per[day].values())
            print("spawns %s: %d (%s)" % (day, tot, ", ".join("%s %d" % (k, per[day][k]) for k in sorted(per[day], key=lambda k: -per[day][k]))))


if __name__ == "__main__":
    main()
