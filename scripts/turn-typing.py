"""turn-typing.py: what an orchestrator's main thread typed in a day, by step kind, and an automation number.

Reads only a session jsonl's main thread (assistant records that are not sidechains; a tool_use block is
counted once by (message id, tool_use id); never an agent .jsonl or .output) and prints: tool uses and typed
chars per tool, Write calls by target (brief, script, draft, report), shell calls by category (gate launch,
gate or log read, report or brief read, git, script, device or pane, other, and three register ones:
register-row-only, a call made only of scripts/row.sh rows, a turn spent on bookkeeping alone;
register-row-with-action, a row that rides with its action, or an append to rulings.md; register-read, a
grep or cat of the register), and Agent prompts.

--ledger prints the automation number, one line per session:
  automation <session> <day>: main-thread tool uses N, lanes delivered L, per lane X; shell lines written S in F files
where lanes delivered is the count of <evidence>/lanes/*<day>*.md minus the files that are a part or a
rework round of another lane, or a device session (TURN_TYPING_ROUND_RE, default the -partN, -rN, -rNa and
-roundN suffixes and a -session- word), and shell lines is the line count of .sh and .ps1 files whose mtime
falls on the day under <evidence>/scratch-*/ and the session's own scratchpad (its subagents write there too).
Fewer main-thread tool uses per delivered lane means more of the day ran without the orchestrator typing it.

Usage:
  python turn-typing.py --session <id prefix> --day YYYY-MM-DD [--profile <config dir>] [--ledger]
  python turn-typing.py --jsonl <transcript path> --day YYYY-MM-DD [--ledger]
  python turn-typing.py --pick-from <projects/<project> dir> --day YYYY-MM-DD --ledger
      the ledger's form: every jsonl touched on or after the day's start, largest six, and one line for
      each that holds at least MIN_RECORDS assistant records on the day (a pane restarted in the evening
      gives two lines for that day and the reader sums them). Exit 3 when none qualifies.
The session file is <profile>/projects/<project>/<session id>.jsonl; --project narrows the search to one
project folder, else every project is searched. The profile defaults to CLAUDE_CONFIG_DIR, else ~/.claude;
the evidence root to EVIDENCE_ROOT, else the working directory; the scratchpad root to <temp>/claude.
Timestamps are UTC in the transcripts and are read in the machine's local time, the offset taken at each stamp.
"""
import argparse, collections, datetime, glob, json, os, re, sys, tempfile

DEFAULT_PROFILE = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
LANE_ROUND_RE = re.compile(os.environ.get("TURN_TYPING_ROUND_RE") or r"-session-|-part\d|-r\d+[a-z]?(-|$)|-round\d")
MIN_RECORDS = 50


def wcat(fp):
    f = fp.replace("\\", "/")
    if "/briefs/" in f: return "brief"
    if f.endswith((".sh", ".ps1", ".py")): return "script"
    if "/drafts/" in f: return "draft"
    if "/reviews/" in f or "/lanes/" in f: return "report-or-review"
    return "other"


def _drop_heredocs(c):
    """The command without its heredoc bodies: the lines after a <<DELIM opener up to the DELIM line are text,
    not commands, and a stray apostrophe in them would flip the quote mask."""
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


def bcat(cmd):
    shape = row_shape(cmd)  # a row.sh call never names rulings.md itself
    if shape: return shape
    if re.search(r">>\s*\S*rulings\.md", cmd): return "register-row-with-action"  # the append shape before row.sh
    if "rulings.md" in cmd: return "register-read"  # a grep or a cat of the register writes no row
    if re.search(r"gate-detach\.ps1\s+-Command|gradle-lockrun\.ps1\s+-|lane-gate\.sh\s+\S|Start-Process|gate\.py launch", cmd): return "gate-launch"
    if re.search(r"lockrun|gate-detach|\.exit|\.done|\.log\b|gate\.py (wait|status)", cmd): return "gate-or-log-read"
    if re.search(r"\bgit\b", cmd): return "git"
    if re.search(r"reviews/|lanes/|briefs/", cmd): return "report-or-brief-read"
    if re.search(r"\badb\b|\bherdr\b", cmd): return "device-or-pane"
    if re.search(r"python|\.py\b", cmd): return "script"
    return "other"


def local_day(stamp):
    """The local calendar day of a transcript stamp (UTC, Z), or None."""
    try:
        return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def near_days(day):
    """The UTC dates a record of the local day can carry: the day and the one on each side of it."""
    d = datetime.datetime.strptime(day, "%Y-%m-%d")
    return [(d + datetime.timedelta(days=k)).strftime("%Y-%m-%d") for k in (-1, 0, 1)]


def lanes_delivered(day, evidence):
    names = [os.path.basename(p)[:-3] for p in glob.glob(os.path.join(evidence, "lanes", "*%s*.md" % day))]
    return sorted(n for n in names if not LANE_ROUND_RE.search(n))


def shell_written(day, session_id, scratch_root, evidence):
    d0 = datetime.datetime.strptime(day, "%Y-%m-%d")
    d1 = d0 + datetime.timedelta(days=1)
    roots = glob.glob(os.path.join(evidence, "scratch-*"))
    if session_id:
        roots += glob.glob(os.path.join(scratch_root, "*", session_id + "*", "scratchpad"))
    files, lines = 0, 0
    for root in roots:
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if not fn.endswith((".sh", ".ps1")):
                    continue
                fp = os.path.join(dp, fn)
                try:
                    mt = datetime.datetime.fromtimestamp(os.path.getmtime(fp))
                    if d0 <= mt < d1:
                        files += 1
                        with open(fp, encoding="utf-8", errors="replace") as f:
                            lines += sum(1 for _ in f)
                except OSError:
                    continue
    return files, lines


def main_thread_records(path, day):
    """The main-thread assistant records of one transcript whose local day is `day`."""
    days = near_days(day)
    for line in open(path, encoding="utf-8", errors="replace"):
        if '"assistant"' not in line or not any(d in line for d in days):
            continue  # cheap pre-filter: a local day spans up to three UTC dates on the stamps
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not isinstance(r, dict) or r.get("type") != "assistant" or not r.get("timestamp") or r.get("isSidechain"):
            continue
        if local_day(r["timestamp"]) == day:
            yield r


def analyze(path, day):
    """The day's main-thread records of one transcript: counts per tool, Write target, shell category."""
    r_ = {"records": 0, "tool_n": collections.Counter(), "tool_chars": collections.Counter(),
          "wn": collections.Counter(), "wc": collections.Counter(), "bn": collections.Counter(),
          "bc": collections.Counter(), "prompts": []}
    seen = set()
    for r in main_thread_records(path, day):
        msg = r.get("message") or {}
        mid = msg.get("id") or r.get("uuid") or ""
        r_["records"] += 1
        for b in msg.get("content") or []:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            key = (mid, b.get("id"))
            if key in seen:
                continue
            seen.add(key)
            n = b.get("name"); i = b.get("input") or {}
            r_["tool_n"][n] += 1; r_["tool_chars"][n] += len(json.dumps(i, ensure_ascii=False))
            if n == "Agent":
                r_["prompts"].append(len(i.get("prompt", "")))
            elif n == "Write":
                c = wcat(i.get("file_path", "")); r_["wn"][c] += 1; r_["wc"][c] += len(i.get("content", ""))
            elif n in ("Bash", "PowerShell"):
                cmd = i.get("command", ""); c = bcat(cmd); r_["bn"][c] += 1; r_["bc"][c] += len(cmd)
    return r_


def day_has_records(path, day):
    """Count of main-thread assistant records whose local day matches, for --pick-from."""
    return sum(1 for _ in main_thread_records(path, day))


def readable_count(path, day):
    """day_has_records, with a transcript its writer holds open counted 0 and said on stderr."""
    try:
        return day_has_records(path, day)
    except OSError as e:
        sys.stderr.write("turn-typing: skipped %s: %s\n" % (path, e.strerror or e))
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", help="session id prefix; the file is <profile>/projects/<project>/<id>*.jsonl")
    ap.add_argument("--project", help="the project folder name under <profile>/projects; default every project")
    ap.add_argument("--jsonl", help="the session transcript path itself")
    ap.add_argument("--pick-from", help="a projects/<project> dir: every transcript with records on the day (ledger form)")
    ap.add_argument("--day", required=True)
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--evidence-root", default=os.environ.get("EVIDENCE_ROOT") or ".")
    ap.add_argument("--scratch-root", default=os.path.join(tempfile.gettempdir(), "claude"))
    ap.add_argument("--ledger", action="store_true", help="one automation line per session instead of the report")
    a = ap.parse_args()
    try:
        d0 = datetime.datetime.strptime(a.day.strip(), "%Y-%m-%d")
    except ValueError:
        ap.error("--day takes YYYY-MM-DD")
    a.day = d0.strftime("%Y-%m-%d")  # 2026-9-3 parses; the stamps are compared as 2026-09-03
    if a.pick_from:
        cands = [p for p in glob.glob(os.path.join(a.pick_from, "*.jsonl"))
                 if datetime.datetime.fromtimestamp(os.path.getmtime(p)) >= d0]
        # the day filter runs over the six largest touched files: a large transcript with no records on the
        # day must not push the day's session out
        cands = sorted(cands, key=os.path.getsize, reverse=True)[:6]
        files = [p for p in cands if readable_count(p, a.day) >= MIN_RECORDS]
        if not files:
            print("automation ? %s: no transcript with %d or more main-thread records on the day (candidates %d)"
                  % (a.day, MIN_RECORDS, len(cands)))
            return 3
    elif a.jsonl:
        files = [a.jsonl]
    elif a.session is not None:
        if not a.session.strip():
            ap.error("--session takes a session id prefix")
        files = glob.glob(os.path.join(a.profile, "projects", a.project or "*", a.session.strip() + "*.jsonl"))
        if len(files) != 1:
            sys.exit("session file %s: %s" % ("not found" if not files else "not unique", files))
    else:
        sys.exit("give --session, --jsonl or --pick-from")
    lanes = lanes_delivered(a.day, a.evidence_root) if a.ledger else []
    code = 0
    for fp in files:
        session_id = os.path.basename(fp)[:8]
        try:
            x = analyze(fp, a.day)
        except OSError as e:
            sys.stderr.write("turn-typing: cannot read %s: %s\n" % (fp, e.strerror or e))
            code = 1
            continue
        total = sum(x["tool_n"].values())
        if a.ledger:
            sf, sl = shell_written(a.day, session_id, a.scratch_root, a.evidence_root)
            per = (float(total) / len(lanes)) if lanes else 0.0
            print("automation %s %s: main-thread tool uses %d, lanes delivered %d, per lane %.1f; shell lines written %d in %d files"
                  % (session_id, a.day, total, len(lanes), per, sl, sf))
            continue
        tn, tc, wn, wc, bn, bc, pr = x["tool_n"], x["tool_chars"], x["wn"], x["wc"], x["bn"], x["bc"], x["prompts"]
        print("turn-typing %s %s: assistant records %d, tool uses %d" % (session_id, a.day, x["records"], total))
        print("  by tool: " + ", ".join("%s %d (%dk chars)" % (k, tn[k], v // 1000) for k, v in tc.most_common()))
        print("  Write by target: " + ", ".join("%s %d (%dk)" % (k, wn[k], v // 1000) for k, v in wc.most_common()))
        print("  shell by category: " + ", ".join("%s %d (%dk)" % (k, bn[k], v // 1000) for k, v in bc.most_common()))
        print("  Agent prompts: %d, %dk chars, mean %d" % (len(pr), sum(pr) // 1000, sum(pr) // max(1, len(pr))))
    return code


if __name__ == "__main__":
    sys.exit(main())
