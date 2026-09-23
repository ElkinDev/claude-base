"""Token shape of an orchestrator session and its subagents over a UTC window.

One script for the probes of a token investigation of a long orchestrating session.
Reads the session transcript and every
subagent transcript under <session>/subagents/, deduplicates assistant turns by
message id (largest output wins), and prints:

  1. window sums for the main session and the subagents, with the API price proxy
     (input 1, cache write 1.25, cache read 0.1, output 5) and the share of each bucket;
  2. cold resumes: subagent turns whose cache write exceeds 50k that are not a first
     turn (a finished agent resumed after the 5-minute cache died, or a tool-list change);
  3. orchestrator shell calls by shape: register-only rows (a call made only of
     scripts/row.sh rows, or of an older printf append to rulings.md), register greps,
     gate polls (an until or while loop that sleeps on a gate, lock, .exit or .log file);
  4. orchestrator context segments between compactions;
  5. per agent type: agents, turns, cache read, first-turn context p50, tools used;
  6. cost by turn-count bucket.

Usage:
  python token-shape.py <session.jsonl> --since 2026-09-09T13:06:00 --until 2026-09-09T21:44:00
  python token-shape.py <session.jsonl> --since ... --until ... --row      # one register line

Timestamps are UTC as written in the transcripts.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys
from datetime import datetime

PROXY = {"in": 1.0, "cc": 1.25, "cr": 0.1, "out": 5.0}


def parse_ts(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def load_turns(path):
    """Deduplicated assistant turns in file order, plus user entries (ts, is_compaction, kind)."""
    rows, order, tools = {}, [], collections.defaultdict(list)
    users = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("type")
            m = d.get("message") or {}
            if t == "assistant":
                mid = m.get("id") or d.get("uuid")
                for b in m.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        tools[mid].append((b.get("name"), inp.get("command") if isinstance(inp, dict) else None))
                u = m.get("usage") or {}
                if not u:
                    continue
                o = u.get("output_tokens", 0) or 0
                prev = rows.get(mid)
                if prev is None:
                    order.append(mid)
                if prev is None or o > prev["out"]:
                    rows[mid] = {
                        "ts": d.get("timestamp") or "",
                        "out": o,
                        "in": u.get("input_tokens", 0) or 0,
                        "cr": u.get("cache_read_input_tokens", 0) or 0,
                        "cc": u.get("cache_creation_input_tokens", 0) or 0,
                        "has_tool": False,
                    }
            elif t == "user":
                c = m.get("content")
                kind = "msg"
                if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                    kind = "tool_result"
                users.append((d.get("timestamp") or "", bool(d.get("isCompactSummary")), kind))
    out = []
    for mid in order:
        r = rows[mid]
        r["tools"] = tools.get(mid, [])
        r["ctx"] = r["in"] + r["cr"] + r["cc"]
        out.append(r)
    return out, users


def proxy(r):
    return r["in"] * PROXY["in"] + r["cc"] * PROXY["cc"] + r["cr"] * PROXY["cr"] + r["out"] * PROXY["out"]


def sums(turns, since, until):
    a = collections.Counter()
    for r in turns:
        if since <= r["ts"] <= until:
            a["turns"] += 1
            for k in ("in", "cr", "cc", "out"):
                a[k] += r[k]
            a["proxy"] += proxy(r)
    return a


def _drop_heredocs(c):
    """The command without its heredoc bodies: the lines after a <<DELIM opener up to the DELIM line are text,
    not commands, and a stray apostrophe in them flipped the quote mask (review round 2: three real rows hidden,
    one invented, over 77 calls)."""
    lines, out, i = c.split("\n"), [], 0
    while i < len(lines):
        out.append(lines[i])
        m = re.search(r"(?<!<)<<-?\s*[\'\"]?([A-Za-z_][A-Za-z0-9_]*)[\'\"]?", lines[i])
        i += 1
        if m:
            while i < len(lines) and lines[i].strip() != m.group(1):
                i += 1
            i += 1
    return "\n".join(out)


def _mask_quotes(c):
    """The command with the inside of every quoted run blanked, same length, so a split on the mask never
    cuts inside quoted text. A backslash escapes the next character inside double quotes; an unclosed quote
    masks to the end. Heredoc bodies are dropped before this runs (_drop_heredocs)."""
    out, q, esc = [], None, False
    for ch in c:
        if q:
            if esc:
                esc = False
                out.append(" ")
            elif q == '"' and ch == "\\":
                esc = True
                out.append(" ")
            elif ch == q:
                q = None
                out.append(ch)
            else:
                out.append(" ")
        else:
            if ch in "\"'":
                q = ch
            out.append(ch)
    return "".join(out)


def row_shape(c):
    """"register-row-only" when every segment of the call is a scripts/row.sh call, "register-row-with-action"
    when a row rides with the action it records, None when the call writes no row through row.sh."""
    c = _drop_heredocs(c)
    mask = _mask_quotes(c)
    segs, start = [], 0
    for m in re.finditer(r"&&|;|\n", mask):
        segs.append(c[start:m.start()])
        start = m.end()
    segs.append(c[start:])
    segs = [s.strip() for s in segs if s.strip()]
    rows = [s for s in segs if re.match(r"^(?:bash\s+)?\S*scripts/row\.sh\s", s)]
    if not rows:
        return None
    return "register-row-only" if len(rows) == len(segs) else "register-row-with-action"


def shape_of(cmd):
    c = (cmd or "").strip()
    if not c:
        return "other"
    shape = row_shape(c)  # a call made only of rows is a turn spent on bookkeeping alone
    if shape:
        return shape
    if re.match(r"^(T|H)=\$\(date", c) and ">>" in c and "rulings.md" in c and "printf" in c:
        return "register-row-only" if c.count(";") <= 2 else "register-row-with-action"
    if re.match(r"^printf '%s\\n' \"- 20", c) and "rulings.md" in c:
        return "register-row-only"
    if re.match(r"^R=\S*rulings\.md", c) or re.match(r"^(grep|sed|tail)\b[^|;&]*rulings\.md", c):
        return "register-grep"
    if re.match(r"^(until|while)\b", c) and "sleep" in c and re.search(r"gate|lock|\.exit|\.log", c):
        return "gate-poll-or-read"
    if c.startswith("cat "):
        return "cat"
    return "other"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session")
    ap.add_argument("--since", help="UTC ISO start of the window (required unless --agent)")
    ap.add_argument("--until", help="UTC ISO end of the window (required unless --agent)")
    ap.add_argument("--row", action="store_true", help="print one register line instead of the report")
    ap.add_argument("--agent", metavar="TASK_ID", help="print the last-turn context and turn count of agent-<TASK_ID>.jsonl and exit")
    args = ap.parse_args()
    subdir = os.path.join(os.path.dirname(args.session), os.path.basename(args.session)[:-6], "subagents")

    if args.agent:
        fp = os.path.join(subdir, f"agent-{args.agent}.jsonl")
        if not os.path.exists(fp):
            print(f"agent {args.agent}: no transcript at {fp}")
            raise SystemExit(2)
        tr, _ = load_turns(fp)
        if not tr:
            print(f"agent {args.agent}: no usage rows")
            raise SystemExit(2)
        last = tr[-1]
        print(f"agent {args.agent}: last ctx {last['ctx']:,} turns {len(tr)} last turn {last['ts'][:19]}Z {'RESUME' if last['ctx'] < 100000 else 'FRESH'} (resume threshold 100k)")
        return

    if not args.since or not args.until:
        ap.error("--since and --until are required unless --agent is given")

    def stamp(value, flag):
        # the window is compared as text against the transcript stamps, so a malformed one would read an empty window
        try:
            return datetime.fromisoformat(value.strip().rstrip("Z")).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            ap.error(f"{flag} takes a UTC ISO stamp such as 2026-09-09T13:06:00")

    since, until = stamp(args.since, "--since"), stamp(args.until, "--until")
    if until <= since:
        ap.error("--until is not after --since")
    if not os.path.isfile(args.session):
        ap.error(f"no session transcript at {args.session!r}")

    try:
        main_turns, main_users = load_turns(args.session)
    except OSError as e:
        raise SystemExit(f"token-shape: cannot read {args.session}: {e.strerror or e}")
    subs = {}
    for fp in sorted(glob.glob(os.path.join(subdir, "*.jsonl"))):
        try:
            tr, _ = load_turns(fp)
        except OSError as e:
            print(f"token-shape: skipped {fp}: {e.strerror or e}; the subagent sums are floors", file=sys.stderr)
            continue
        if not tr:
            continue
        meta = {}
        mp = fp[:-6] + ".meta.json"
        if os.path.exists(mp):
            try:
                meta = json.load(open(mp, encoding="utf-8"))
            except Exception:
                pass
        subs[os.path.basename(fp)[6:14]] = (meta.get("agentType") or "?", (meta.get("description") or "")[:34], tr)

    mw = sums(main_turns, since, until)
    sw = collections.Counter()
    for _, _, tr in subs.values():
        sw.update(sums(tr, since, until))
    total = mw["proxy"] + sw["proxy"]

    # cold resumes
    misses, miss_tok, gaps = 0, 0, []
    notes_n, notes_tok = 0, 0  # resumes of an agent whose description carries "notes" (a notes round sent to the same agent) apart
    for _, desc, tr in subs.values():
        prev = None
        for i, r in enumerate(tr):
            if i > 0 and since <= r["ts"] <= until and r["cc"] > 50000:
                misses += 1
                miss_tok += r["cc"]
                if re.search(r"\bnotes\b", desc, re.I):
                    notes_n += 1
                    notes_tok += r["cc"]
                a, b = parse_ts(prev), parse_ts(r["ts"])
                if a and b:
                    gaps.append((b - a).total_seconds())
            prev = r["ts"]
    gaps.sort()

    # shell shapes
    shapes = collections.Counter()
    ncalls = 0
    for r in main_turns:
        if since <= r["ts"] <= until:
            for n, cmd in r["tools"]:
                if n in ("Bash", "PowerShell"):
                    ncalls += 1
                    shapes[shape_of(cmd)] += 1

    # per type
    per = collections.defaultdict(lambda: {"agents": 0, "turns": 0, "cr": 0, "first": [], "tools": collections.Counter(), "over80": 0, "proxy": 0.0})
    buckets = collections.defaultdict(list)
    for key, (atype, desc, tr) in subs.items():
        if not any(since <= r["ts"] <= until for r in tr):
            continue
        p = per[atype]
        p["agents"] += 1
        p["turns"] += len(tr)
        p["cr"] += sum(r["cr"] for r in tr)
        p["first"].append(tr[0]["ctx"])
        p["proxy"] += sum(proxy(r) for r in tr)
        if len(tr) > 80:
            p["over80"] += 1
        for r in tr:
            for n, _ in r["tools"]:
                p["tools"][n] += 1
        n = len(tr)
        b = "<=40" if n <= 40 else "41-80" if n <= 80 else "81-120" if n <= 120 else ">120"
        buckets[b].append((n, sum(proxy(r) for r in tr), max(r["ctx"] for r in tr)))

    def p50(v):
        v = sorted(v)
        return v[len(v) // 2] if v else 0

    if args.row:
        imp = per.get("implementer", {"first": [0]})
        over80 = sum(p["over80"] for p in per.values())
        print(f"token-shape {since[:16]}Z..{until[:16]}Z: total {total/1e6:.1f}M proxy (main {mw['proxy']/1e6:.1f}M in {mw['turns']} turns, mean ctx {(mw['in']+mw['cr']+mw['cc'])//max(mw['turns'],1):,}; subagents {sw['proxy']/1e6:.1f}M in {sw['turns']} turns); cold resumes {misses} ({miss_tok*PROXY['cc']/1e6:.1f}M); implementer first-turn ctx p50 {p50(imp['first']):,}; register-row-only shell calls {shapes['register-row-only']} of {ncalls}; agents over 80 turns {over80}")
        return

    print(f"=== 1. WINDOW {since} .. {until} (proxy: in 1, cache write 1.25, cache read 0.1, out 5)")
    for label, a in (("main", mw), ("subagents", sw)):
        if a["turns"]:
            print(f"  {label:10s} turns={a['turns']:5d} in={a['in']:,} cc={a['cc']:,} cr={a['cr']:,} out={a['out']:,} proxy={a['proxy']/1e6:.1f}M"
                  f"  [cr {a['cr']*0.1/a['proxy']*100:.0f}% cc {a['cc']*1.25/a['proxy']*100:.0f}% out {a['out']*5/a['proxy']*100:.0f}%]"
                  + (f" mean ctx={(a['in']+a['cr']+a['cc'])//a['turns']:,}" if label == "main" else ""))
    print(f"  total proxy={total/1e6:.1f}M")
    print(f"=== 2. COLD RESUMES (subagent turns with cache write > 50k, not first): n={misses} tokens={miss_tok:,} proxy={miss_tok*1.25/1e6:.1f}M"
          + (f" gap p50={gaps[len(gaps)//2]:.0f}s p90={gaps[int(len(gaps)*0.9)]:.0f}s" if gaps else ""))
    print(f"    notes-by-resume: n={notes_n} tokens={notes_tok:,} proxy={notes_tok*1.25/1e6:.1f}M; other resumes: n={misses-notes_n} "
          f"tokens={miss_tok-notes_tok:,} proxy={(miss_tok-notes_tok)*1.25/1e6:.1f}M (agent description carries 'notes')")
    print(f"=== 3. ORCHESTRATOR SHELL CALLS in window: {ncalls}")
    for k, v in shapes.most_common():
        print(f"    {v:5d}  {k}")
    print("=== 4. ORCHESTRATOR CONTEXT SEGMENTS between compactions")
    comp = sorted(ts for ts, is_c, _ in main_users if is_c)
    seg, cur, ci = [], [], 0
    for r in main_turns:
        if not (since <= r["ts"] <= until):
            continue
        while ci < len(comp) and comp[ci] <= r["ts"]:
            if cur:
                seg.append(cur)
            cur = []
            ci += 1
        cur.append(r)
    if cur:
        seg.append(cur)
    for s in seg:
        ctxs = [r["ctx"] for r in s]
        print(f"  {s[0]['ts'][11:16]}Z..{s[-1]['ts'][11:16]}Z turns={len(s):4d} ctx first={ctxs[0]:,} last={ctxs[-1]:,} mean={sum(ctxs)//len(ctxs):,} proxy={sum(proxy(r) for r in s)/1e6:.1f}M")
    print("=== 5. SUBAGENTS BY TYPE (agents with a turn in the window; turns and reads are whole-agent)")
    for t, p in sorted(per.items(), key=lambda kv: -kv[1]["turns"]):
        print(f"  {t:18s} agents={p['agents']:3d} turns={p['turns']:5d} cache_read={p['cr']/1e6:7.1f}M proxy={p['proxy']/1e6:6.1f}M first_ctx_p50={p50(p['first']):,} over80={p['over80']} tools={dict(p['tools'].most_common(8))}")
    print("=== 6. COST BY TURN COUNT")
    for b in ("<=40", "41-80", "81-120", ">120"):
        v = buckets.get(b, [])
        if v:
            print(f"  turns {b:7s} agents={len(v):3d} mean turns={sum(x[0] for x in v)/len(v):5.0f} mean proxy={sum(x[1] for x in v)/len(v)/1e6:5.2f}M per turn={sum(x[1] for x in v)/sum(x[0] for x in v)/1e3:5.1f}k mean ctx max={sum(x[2] for x in v)/len(v):,.0f}")


if __name__ == "__main__":
    main()
