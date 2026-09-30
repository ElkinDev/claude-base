"""radar-flags.py: a daily sweep that turns the readings a machine already runs into ranked proposals, with no model.

    python radar-flags.py [--out FILE] [--now YYYY-MM-DDTHH:MM]

A failure an owner has to flag by hand (an account resetting its week with quota unused, an agent pinned to a model a
newer and cheaper one replaced, kit twins owed for days) is often already visible to a reader the machine runs. This
script runs those readers, turns what they flag into one ranked table and writes it to
<evidence root>/ledger/radar-<YYYY-MM-DD>.md (--out overrides, --now fixes the clock for tests). Serves quality and
automation. The number: failures the owner raises before the radar lists them, per week, target 0. Not served:
tokens (it spends none).

Detectors. Each one tolerates a missing input with one "input missing" line; a reader that fails or times out is a
"reading missing" line, never a flag. A detector its setting leaves off prints one "off" line and is not missing.
1 model fit    `model:` and `effort:` of every agent definition against radar-prices.json beside this script: a pin
               whose family has a newer model priced lower in every bucket. An alias (haiku, opus) floats, never flags.
2 capacity     usage-probe.py per profile: weekly_all projected at the seven_day reset as used / share of the week
               elapsed (the pace of cca-pace.py); under 70 pct is a flag, over 100 pct a wall risk, and under 10 pct
               of the week is too early to project. For the profile RADAR_PACE_PROFILE names, cca-pace.py `behind` is
               a flag too, fed the same probe output through its --probe-output. A 401 means the stored token expired.
3 owed twins   kit-twin-drift.py --row: owed over 0 is a flag (owed is older than 24 h by the reader's definition).
               The row carries no ages for no-twin files, so when its count is over 0 the plain table is read and only
               files it dates older than 24 h count.
4 pulse        pulse.py: every line carrying a [pulse:...] key. And every register line whose review date is before
               the run's date, or that pulse.py prints as DUE, as a verdict due naming its keep line.
5 versions     claude --version against npm view @anthropic-ai/claude-code version (30 s bound each).
6 stale waits  house-only, so off unless RADAR_DECISIONS names a folder. It reads one decision-file format: the
               owner-decisions-<date>.md files of the run's date and the two before it, where a top-level row of AUTO
               class carries its deadline as "antes de HH:MM" and is closed by DECIDED, APPLIED, LAPSED, CLOSED, DONE
               or RULED. A row whose deadline has passed and that is not closed is a flag. A deadline before the row's
               own leading HH:Mx stamp is the next morning's. Another format needs its own detector.

Ranking: principle weight x age, quality and tokens 2, optimization and automation 1; 15 rows at most. The age is the
runs since first seen, kept in radar-state.json beside the output. A key missing from a clean run leaves the state;
a detector with a missing input or reading keeps its keys' ages; a repeated run at the same --now ages nothing.
A "## Discover" section that radar-discover.py wrote into the same file is kept when the file is rewritten.

Settings, all environment variables, each default relative to this script or to the standard folders:
  EVIDENCE_ROOT          the evidence root the default output goes under; default the folder above this script
  RADAR_AGENTS           the agent definitions; default <CLAUDE_CONFIG_DIR, else ~/.claude>/agents
  RADAR_PRICES           the price table; default <EV>/ledger/radar/radar-prices.json, else the seed beside this script
  RADAR_SCRIPTS          the folder of the readers below; default the folder of this script
  RADAR_PROBE_PY         usage-probe.py; default in RADAR_SCRIPTS
  RADAR_PACE_PY          cca-pace.py; default in RADAR_SCRIPTS
  RADAR_DRIFT_PY         kit-twin-drift.py; default in RADAR_SCRIPTS
  RADAR_PULSE_PY         pulse.py; default in RADAR_SCRIPTS, else, when RADAR_SCRIPTS is unset, claude/tools/pulse.py
                         of the kit this script sits in
  RADAR_PULSE_REGISTER   the register pulse.py reads; default PULSE_REGISTER, else pulse.md under PULSE_ROOT, else
                         under ~/.claude (pulse.py's own default)
  RADAR_PROFILES         name=dir;name=dir, the profiles capacity probes (an empty dir is the default profile);
                         default "default=", the current profile only
  RADAR_PACE_PROFILE     the profile whose cca-pace.py `behind` is a flag; default none, the pace check is off
  RADAR_DECISIONS        the folder of the owner-decisions files; default none, stale waits is off
  RADAR_CLAUDE, RADAR_NPM   the two version commands; default claude and npm (a .py runs under Python)
Exit 0 when every input was read, 1 when the file was written with an input or reading missing, 2 on a usage error or
an output that cannot be written.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EV = os.environ.get("EVIDENCE_ROOT") or os.path.dirname(HERE)
CONFIG = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
AGENTS = os.environ.get("RADAR_AGENTS") or os.path.join(CONFIG, "agents")
PRICES = os.environ.get("RADAR_PRICES") or next(  # the table radar-discover refreshes, else the seed
    (p for p in (os.path.join(EV, "ledger", "radar", "radar-prices.json"), os.path.join(HERE, "radar-prices.json"))
     if os.path.isfile(p)), os.path.join(HERE, "radar-prices.json"))
SCRIPTS = os.environ.get("RADAR_SCRIPTS") or HERE
PROBE_PY = os.environ.get("RADAR_PROBE_PY") or os.path.join(SCRIPTS, "usage-probe.py")
PACE_PY = os.environ.get("RADAR_PACE_PY") or os.path.join(SCRIPTS, "cca-pace.py")
DRIFT_PY = os.environ.get("RADAR_DRIFT_PY") or os.path.join(SCRIPTS, "kit-twin-drift.py")
KIT_PULSE = os.path.join(os.path.dirname(HERE), "claude", "tools", "pulse.py")  # the kit layout, scripts/ beside claude/
PULSE_PY = os.environ.get("RADAR_PULSE_PY") or next(  # an explicit RADAR_SCRIPTS never falls back to the kit's copy
    (p for p in [os.path.join(SCRIPTS, "pulse.py")] + ([] if os.environ.get("RADAR_SCRIPTS") else [KIT_PULSE])
     if os.path.isfile(p)), os.path.join(SCRIPTS, "pulse.py"))
REGISTER = os.environ.get("RADAR_PULSE_REGISTER") or os.environ.get("PULSE_REGISTER") or os.path.join(
    os.environ.get("PULSE_ROOT") or os.path.join(os.path.expanduser("~"), ".claude"), "pulse.md")
PROFILES = os.environ.get("RADAR_PROFILES") or "default="
PACE_PROFILE = (os.environ.get("RADAR_PACE_PROFILE") or "").strip()
DECISIONS = (os.environ.get("RADAR_DECISIONS") or "").strip()
CLAUDE = os.environ.get("RADAR_CLAUDE") or "claude"
NPM = os.environ.get("RADAR_NPM") or "npm"
WEIGHT = {"quality": 2, "tokens": 2, "optimization": 1, "automation": 1}
ORDER = ("model fit", "capacity", "owed twins", "pulse", "versions", "stale waits")
BUCKETS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
CAP, FLOOR, MIN_SHARE, WALL_RISK, PER_DETECTOR = 15, 70.0, 0.1, 100.0, 2
MODEL_RE = re.compile(r"^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$")
SEVEN_RE = re.compile(r"^seven_day: ([\d.]+)% used, resets (\S+)", re.M)
WEEKLY_RE = re.compile(r"^limit weekly_all: ([\d.]+)% used", re.M)
ROW_RE = re.compile(r"twins trailing (\d+) \(owed, live change older than 24 h: (\d+)\), "
                    r"live tools with no twin since (\S+ \S+): (\d+)")
NOTWIN_RE = re.compile(r"^- (.+) \((\d{4}-\d{2}-\d{2} \d{2}:\d{2})\)\s*$", re.M)
KEY_RE = re.compile(r"\s*\[(pulse:[^\]]+)\]\s*$")
DEADLINE_RE = re.compile(r"antes de (?:las )?(\d{1,2}):(\d{2})")
ROW_TIME_RE = re.compile(r"\b(\d{1,2}):(\d)[0-9x]\b")
CLOSED_RE = re.compile(r"\b(DECIDED|APPLIED|LAPSED|CLOSED|DONE|RULED)\b")
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def run(cmd, bound, env=None):
    """(exit, text); exit None when the command did not start or its bound fired."""
    exe = cmd[0]
    if exe.lower().endswith(".py"):
        cmd = [sys.executable] + cmd
    else:
        cmd = [shutil.which(exe) or exe] + cmd[1:]
    env = dict(os.environ if env is None else env, PYTHONIOENCODING="utf-8")
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                              timeout=bound)
    except subprocess.TimeoutExpired:
        return None, "the %d s bound fired" % bound
    except OSError as e:
        return None, "did not start: %s" % e
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def first_line(text):
    return (text.strip().splitlines() or ["empty output"])[0][:140]


def row(key, principle, finding, number, proposal):
    return {"key": key, "principle": principle, "finding": finding, "number": number, "proposal": proposal}


def principle_of(text, default="quality"):
    for part in re.split(r"[,;/]| and ", (text or "").lower()):
        p = part.strip()
        for name in ("quality", "optimization", "automation", "tokens"):
            if p.startswith(name[:5]):
                return name
    return default


def model_of(mid):
    m = MODEL_RE.match(mid.strip().lower().replace("[1m]", ""))
    return (m.group(1), (int(m.group(2)), int(m.group(3) or 0))) if m else None


def model_fit(now):
    try:
        with open(PRICES, encoding="utf-8") as h:
            table = {model_of(r["id"]): r for r in json.load(h)["models"]
                     if model_of(r["id"]) and all(isinstance(r.get(b), (int, float)) for b in BUCKETS)}
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [], ["model fit: input missing: price table %s (%s)" % (PRICES, e.__class__.__name__)], True
    defs = sorted(glob.glob(os.path.join(AGENTS, "*.md")))
    if not defs:
        return [], ["model fit: input missing: no agent definition under %s" % AGENTS], True
    out, notes, missing, pinned = [], [], False, 0
    for path in defs:
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as h:
                lines = h.read(8000).splitlines()
        except OSError as e:
            notes.append("model fit: input missing: %s (%s)" % (path, e.__class__.__name__))
            missing = True
            continue
        head = {}
        if lines and lines[0].strip() == "---":
            for ln in lines[1:]:
                if ln.strip() == "---":
                    break
                k, _, v = ln.partition(":")
                head.setdefault(k.strip(), v.strip().strip("\"'"))
        model, effort = head.get("model", ""), head.get("effort", "unset")
        pin = model_of(model) if model else None
        if not pin:
            continue
        pinned += 1
        cur = table.get(pin)
        if cur is None:
            notes.append("model fit: %s pins %s, which radar-prices.json does not hold" % (os.path.basename(path), model))
            continue
        better = [(v, r) for (f, v), r in table.items() if f == pin[0] and v > pin[1]
                  and all(r[b] < cur[b] for b in BUCKETS)]
        if better:
            r = max(better, key=lambda x: x[0])[1]
            name = os.path.basename(path)[:-3]
            out.append(row("model-fit:%s:%s" % (name, model), "tokens",
                           "agent %s pins %s (effort %s); %s is newer and cheaper in every bucket" % (
                               name, model, effort, r["id"]),
                           "input $%g vs $%g, output $%g vs $%g per MTok (`grep -H ^model: %s`, radar-prices.json)" % (
                               cur["input"], r["input"], cur["output"], r["output"], path.replace("\\", "/")),
                           "Repin %s to %s; proof: its next agent records %s at %.0f pct of today's output price." % (
                               name, r["id"], r["id"], 100.0 * r["output"] / cur["output"])))
    notes.append("model fit: %d definitions read, %d pinned to an id, %d flagged" % (len(defs), pinned, len(out)))
    return out, notes, missing


def profiles():
    for part in PROFILES.split(";"):
        if "=" in part:
            name, d = part.split("=", 1)
            yield name.strip(), d.strip()


def probe(prof, path):
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if prof[1]:
        env["CLAUDE_CONFIG_DIR"] = prof[1]
    return run([path], 30, env)


def pace_line(name, text, now):
    path = PACE_PY
    if not os.path.isfile(path):
        return None, "capacity %s pace: input missing: %s" % (name, path)
    fd, tmp = tempfile.mkstemp(prefix="radar-probe-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as h:
            h.write(text)
        code, out = run([path, "--probe-output", tmp, "--now", now.isoformat(timespec="minutes"), "--label", name],
                        30)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if code not in (0, 3):
        return None, "capacity %s pace: reading missing (cca-pace exit %s): %s" % (name, code, first_line(out))
    return first_line(out), None


def capacity(now):
    path = PROBE_PY
    if not os.path.isfile(path):
        return [], ["capacity: input missing: %s" % path], True
    profs = list(profiles())
    if not profs:
        return [], ["capacity: input missing: no profile in RADAR_PROFILES"], True
    lost = [] if not PACE_PROFILE or PACE_PROFILE in [n for n, _ in profs] else [
        "capacity %s pace: input missing: RADAR_PACE_PROFILE names %s, which RADAR_PROFILES does not list" % (
            PACE_PROFILE, PACE_PROFILE)]
    with cf.ThreadPoolExecutor(len(profs)) as ex:
        reads = list(ex.map(lambda p: probe(p, path), profs))
    out, notes, missing = [], lost, bool(lost)
    for (name, d), (code, text) in zip(profs, reads):
        cmd = ("CLAUDE_CONFIG_DIR=%s python scripts/usage-probe.py" % d) if d else \
            "python scripts/usage-probe.py, CLAUDE_CONFIG_DIR unset"
        if code != 0:
            why = "401, the stored token expired" if "HTTP 401" in text else "probe exit %s" % code
            notes.append("capacity %s: reading missing (%s): %s" % (name, why, first_line(text)))
            missing = True
            continue
        sd, wa = SEVEN_RE.search(text), WEEKLY_RE.search(text)
        try:
            reset = dt.datetime.fromisoformat(sd.group(2)).astimezone() if sd else None
        except ValueError:
            reset = None
        if not (reset and wa):
            notes.append("capacity %s: reading missing: no seven_day reset or weekly_all line: %s" % (
                name, first_line(text)))
            missing = True
            continue
        used = float(wa.group(1))
        share = min(1.0, max(0.0, (now - reset + dt.timedelta(days=7)).total_seconds() / (7 * 86400)))
        when = reset.strftime("%Y-%m-%d %H:%M")
        if share < MIN_SHARE:
            notes.append("capacity %s: weekly_all %.0f%% at %.0f%% of the week, too early to project (reset %s)" % (
                name, used, 100 * share, when))
        else:
            proj = used / share
            notes.append("capacity %s: weekly_all %.0f%% at %.0f%% of the week, projected %.0f%% at the reset %s" % (
                name, used, 100 * share, proj, when))
            if proj < FLOOR:
                out.append(row("capacity:%s" % name, "optimization",
                               "profile %s is on course to end its week at %.0f pct of weekly_all, under %d" % (
                                   name, proj, FLOOR),
                               "weekly_all %.0f%% at %.0f%% of the week, reset %s (`%s`)" % (
                                   used, 100 * share, when, cmd),
                               "Route the next pane-suitable units to %s until it projects %d pct; proof: the next "
                               "radar projects %s at %d pct or more." % (name, FLOOR, name, FLOOR)))
            elif proj > WALL_RISK:
                out.append(row("capacity-wall:%s" % name, "optimization",
                               "profile %s is on course to reach its weekly wall before the reset (%.0f pct projected)" % (
                                   name, proj),
                               "weekly_all %.0f%% at %.0f%% of the week, reset %s (`%s`)" % (
                                   used, 100 * share, when, cmd),
                               "Move pane-suitable units off %s to a profile that projects under %d pct; proof: the "
                               "next radar projects %s at %d pct or less." % (name, FLOOR, name, WALL_RISK)))
        if PACE_PROFILE and name == PACE_PROFILE:
            line, err = pace_line(name, text, now)
            if err:
                notes.append(err)
                missing = True
            else:
                notes.append("capacity %s pace: %s" % (name, line))
                if line.rstrip().endswith("-> behind"):
                    out.append(row("capacity:%s-behind" % name, "optimization", "profile %s is behind its pace" % name,
                                   "%s (`python scripts/cca-pace.py`)" % line,
                                   "Give the next pane-suitable unit to %s; proof: cca-pace.py reads a gap above "
                                   "-5 at the next run." % name))
    return out, notes, missing


def owed_twins(now):
    path = DRIFT_PY
    if not os.path.isfile(path):
        return [], ["owed twins: input missing: %s" % path], True
    code, text = run([path, "--row"], 45)
    m = ROW_RE.search(text)
    if code != 0 or not m:
        return [], ["owed twins: reading missing (exit %s): %s" % (code, first_line(text))], True
    trailing, owed, since, notwin = int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4))
    out, notes, missing, old = [], [], False, 0
    if notwin:
        code, table = run([path], 45)
        stamps = NOTWIN_RE.findall(table) if code == 0 else []
        cut = (now - dt.timedelta(hours=24)).replace(tzinfo=None)
        old = sum(1 for _, s in stamps if dt.datetime.strptime(s, "%Y-%m-%d %H:%M") < cut)
        if len(stamps) != notwin:
            notes.append("owed twins: reading missing: the table lists %d no-twin files, the row counts %d" % (
                len(stamps), notwin))
            missing = True
    notes.append("owed twins: trailing %d, owed %d, no twin %d (older than 24 h: %d)" % (trailing, owed, notwin, old))
    if owed:
        out.append(row("twins:owed", "quality", "%d kit twins owed, their live change older than 24 h" % owed,
                       "owed %d of %d trailing (`python scripts/kit-twin-drift.py --row`)" % (owed, trailing),
                       "Carry the owed twins to the kit in one slice, or carry them by verdict; proof: "
                       "kit-twin-drift.py --row reads owed 0."))
    if old:
        out.append(row("twins:no-twin", "quality", "%d live tools changed since %s have no twin and no waiver, older "
                                                   "than 24 h" % (old, since),
                       "no twin %d, %d older than 24 h (`python scripts/kit-twin-drift.py`)" % (notwin, old),
                       "Give each a kit twin or a kit-twin-waivers.txt line; proof: kit-twin-drift.py --row "
                       "reads no twin 0."))
    return out, notes, missing


def register():
    try:
        with open(REGISTER, encoding="utf-8-sig") as h:
            lines = h.read().splitlines()
    except OSError as e:
        return None, "pulse: input missing: register %s (%s)" % (REGISTER, e.__class__.__name__)
    rows = {}
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#") or ln.startswith("id | "):
            continue
        f = [x.strip() for x in ln.split(" | ")]
        if len(f) >= 8:
            rows.setdefault(f[0], {"principle": f[1], "number": " | ".join(f[5:-2]).strip("`"), "keep": f[-2],
                                   "review": f[-1]})
    return rows, None


PULSE_FIX = {
    "SILENT": "Find why %s's trigger fired with no use, then wire it or retire it in a decision row citing [%s]",
    "DARK": "Name in the register a use %s leaves on disk, or retire it in a decision row citing [%s]",
    "NOMATCH": "Fix the trigger or use glob of %s in the register, or cite [%s] in a decision row",
}


def pulse(now):
    out, notes, missing = [], [], False
    regs, err = register()
    if err:
        notes.append(err)
        missing, regs = True, {}
    path = PULSE_PY
    due = {}
    if not os.path.isfile(path):
        notes.append("pulse: input missing: %s" % path)
        missing = True
    else:
        code, text = run([path], 45)
        lines = text.splitlines()
        if code is None or not lines or not lines[0].startswith("pulse "):
            notes.append("pulse: reading missing (exit %s): %s" % (code, first_line(text)))
            missing, lines = True, []
        else:
            notes.append("pulse: %s" % lines[0][:200])
        for ln in lines[1:]:
            m = KEY_RE.search(ln)
            if not m:
                continue
            key, status, text_ = m.group(1), ln.split(" ", 1)[0], ln[:m.start()]
            mid = key[len("pulse:"):]
            if status == "DUE":
                due[mid] = text_
                continue
            fix = (PULSE_FIX[status] % (mid, key) if status in PULSE_FIX
                   else "Fix the cause of the line, or write a decision row citing [%s]" % key)
            out.append(row(key, principle_of((regs.get(mid) or {}).get("principle")), text_[:200],
                           "%s (`python scripts/pulse.py`)" % status,
                           "%s; proof: pulse.py prints no line for [%s] at the next ledger run." % (fix, key)))
    for mid, r in regs.items():
        try:
            if dt.date.fromisoformat(r["review"]) < now.date():
                due.setdefault(mid, "")
        except ValueError:
            continue
    for mid in sorted(due):
        r = regs.get(mid) or {}
        out.append(row("due:pulse:%s" % mid, principle_of(r.get("principle")),
                       "verdict due on %s since %s; keep line: %s" % (mid, r.get("review", "?"),
                                                                     (r.get("keep") or due[mid])[:160]),
                       "review date %s (`grep ^%s %s`)" % (r.get("review", "?"), mid, os.path.basename(REGISTER)),
                       "Run `%s` and write the keep-or-revert row citing [pulse:%s]; proof: that row exists and "
                       "the register carries a later review date or no line for it." % (
                           (r.get("number") or "the number command")[:120], mid)))
    notes.append("pulse: %d keyed lines, %d verdicts due" % (len(out) - len(due), len(due)))
    return out, notes, missing


def versions(now):
    code, local = run([CLAUDE, "--version"], 30)
    lv = VERSION_RE.search(local or "") if code == 0 else None
    if not lv:
        return [], ["versions: reading missing: claude --version (exit %s): %s" % (code, first_line(local))], True
    code, latest = run([NPM, "view", "@anthropic-ai/claude-code", "version"], 30)
    nv = VERSION_RE.search(latest or "") if code == 0 else None
    if not nv:
        return [], ["versions: reading missing: npm view, offline or failed (exit %s): %s" % (
            code, first_line(latest))], True
    a, b = tuple(map(int, lv.groups())), tuple(map(int, nv.groups()))
    notes = ["versions: claude %s, npm latest %s" % (lv.group(0), nv.group(0))]
    if b <= a:
        return [], notes, False
    return [row("version:claude-code", "optimization", "Claude Code %s is installed and %s is released" % (
        lv.group(0), nv.group(0)), "%s vs %s (`claude --version`; `npm view @anthropic-ai/claude-code version`)" % (
        lv.group(0), nv.group(0)), "Read the %s release notes and update through the kit's install step; proof: "
                                   "claude --version reads %s." % (nv.group(0), nv.group(0)))], notes, False


def stale_waits(now):
    if not DECISIONS:  # house-only: one decision-file format (module docstring, detector 6)
        return [], ["stale waits: off (RADAR_DECISIONS is unset; it reads one house's owner-decisions format)"], False
    days = {(now.date() - dt.timedelta(days=i)).isoformat() for i in range(3)}
    files = []
    for p in sorted(glob.glob(os.path.join(DECISIONS, "owner-decisions-*.md"))):
        m = re.search(r"owner-decisions-(\d{4}-\d{2}-\d{2})\.md$", p.replace("\\", "/"))
        if m and m.group(1) in days:
            files.append((p, dt.date.fromisoformat(m.group(1))))
    if not files:
        return [], ["stale waits: input missing: no owner-decisions file of the last 3 days under %s" % DECISIONS], True
    out, notes, missing, auto = [], [], False, 0
    for p, day in files:
        try:
            with open(p, encoding="utf-8-sig", errors="replace") as h:
                lines = h.read().splitlines()
        except OSError as e:
            notes.append("stale waits: input missing: %s (%s)" % (p, e.__class__.__name__))
            missing = True
            continue
        for no, ln in enumerate(lines, 1):
            if not ln.startswith("- ") or not re.search(r"\bauto class\b", ln, re.I):
                continue
            auto += 1
            m = DEADLINE_RE.search(ln)
            if not m or CLOSED_RE.search(ln):
                continue
            h_, m_ = int(m.group(1)), int(m.group(2))
            if h_ > 23 or m_ > 59:
                continue
            deadline = dt.datetime.combine(day, dt.time(h_, m_)).astimezone()
            said = ROW_TIME_RE.match(ln[2:])  # the row's leading stamp is the ask's own time; a cited time is not
            if said and int(said.group(1)) * 60 + int(said.group(2)) * 10 > h_ * 60 + m_:
                deadline += dt.timedelta(days=1)
            if now <= deadline:
                continue
            where = "%s/%s:%d" % (os.path.basename(os.path.normpath(DECISIONS)), os.path.basename(p), no)
            out.append(row("stale:%s:%s" % (day, hashlib.sha1(ln[:120].encode("utf-8")).hexdigest()[:8]), "automation",
                           "AUTO row past its %02d:%02d deadline and not closed: %s" % (h_, m_, ln[2:150]),
                           "past by %.0f h (%s)" % ((now - deadline).total_seconds() / 3600, where),
                           "Run the row's recommended option and mark it APPLIED, or mark it LAPSED; proof: the next "
                           "radar lists no stale wait at %s." % where))
    notes.append("stale waits: %d files, %d AUTO rows, %d past their deadline and open" % (len(files), auto, len(out)))
    return out, notes, missing


DETECTORS = dict(zip(ORDER, (model_fit, capacity, owed_twins, pulse, versions, stale_waits)))


def age(found, missing_dets, state_path, now):
    """Sets f["age"] on each finding and rewrites the state; found is [(detector, finding, missing)], and the keys of
    a detector in missing_dets that this run did not see keep their age."""
    try:
        with open(state_path, encoding="utf-8") as h:
            state = json.load(h)
        keys = state["keys"] if isinstance(state.get("keys"), dict) else {}
    except (OSError, ValueError, AttributeError, KeyError):
        state, keys = {}, {}
    stamp = now.isoformat(timespec="minutes")
    repeat = state.get("last_run") == stamp
    new = {}
    for det, f, _ in found:
        old = keys.get(f["key"]) if isinstance(keys.get(f["key"]), dict) else {}
        prev = old["runs"] if isinstance(old.get("runs"), int) and old["runs"] > 0 else 0
        runs = prev if (repeat and prev) else prev + 1
        new[f["key"]] = {"det": det, "first": old.get("first") if prev and old.get("first") else stamp, "runs": runs}
        f["age"] = runs
    for k, v in keys.items():
        if k not in new and isinstance(v, dict) and v.get("det") in missing_dets:
            new[k] = v
    put(state_path, json.dumps({"last_run": stamp, "keys": new}, indent=1, sort_keys=True) + "\n")


def put(path, text):
    """Writes through a temp file and one os.replace; the temp file never outlives a failed replace."""
    fd, tmp = tempfile.mkstemp(prefix="radar-", suffix=".tmp", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as h:
            h.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def cell(s):
    return re.sub(r"\s*\|\s*", " / ", str(s).replace("\r", " ").replace("\n", " "))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="the radar file (default ledger/radar-<date>.md under the evidence root)")
    ap.add_argument("--now", help="fix the clock, YYYY-MM-DD[THH:MM] (tests)")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        now = (dt.datetime.fromisoformat(a.now.strip()) if a.now is not None else dt.datetime.now()).astimezone()
    except ValueError as e:
        print("radar-flags: bad --now: %s" % e, file=sys.stderr)
        return 2
    if a.out is not None and not a.out.strip():
        print("radar-flags: --out is empty", file=sys.stderr)
        return 2
    out = os.path.abspath(a.out.strip() if a.out else "%s/ledger/radar-%s.md" % (EV, now.strftime("%Y-%m-%d")))
    sys.stdout.reconfigure(errors="backslashreplace")
    with cf.ThreadPoolExecutor(len(ORDER)) as ex:
        futs = {d: ex.submit(DETECTORS[d], now) for d in ORDER}
    found, notes, missing_dets, seen = [], [], set(), set()
    for d in ORDER:
        try:
            fs, ns, miss = futs[d].result()
        except Exception as e:  # a detector's own defect is a reading missing, never a crash of the sweep
            fs, ns, miss = [], ["%s: reading missing: %s: %s" % (d, e.__class__.__name__, e)], True
        for f in fs:  # one row per key, also when a reader prints the same key twice
            if f["key"] not in seen:
                seen.add(f["key"])
                found.append((d, f, miss))
        notes += ns
        if miss:
            missing_dets.add(d)
    missing_any = bool(missing_dets)
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        age(found, missing_dets, os.path.join(os.path.dirname(out), "radar-state.json"), now)
    except OSError as e:
        print("radar-flags: cannot write beside %s: %s" % (out, e), file=sys.stderr)
        return 2
    ranked = sorted(found, key=lambda x: (-WEIGHT[x[1]["principle"]] * x[1]["age"], -WEIGHT[x[1]["principle"]],
                                          ORDER.index(x[0]), x[1]["key"]))
    keep, per, kinds = set(), {}, set()  # a floor of PER_DETECTOR rows per detector, so one noisy reader never
    for distinct in (True, False):       # buries the rest; the first pass takes one row per kind (the key's prefix),
        for d, f, _ in ranked:           # so two wall rows never crowd out a waste row of the same detector
            kind = (d, f["key"].split(":")[0])
            if f["key"] in keep or per.get(d, 0) >= PER_DETECTOR or (distinct and kind in kinds):
                continue
            per[d] = per.get(d, 0) + 1
            kinds.add(kind)
            keep.add(f["key"])
    for d, f, _ in ranked:
        if len(keep) >= CAP:
            break
        keep.add(f["key"])
    shown = [x for x in ranked if x[1]["key"] in keep][:CAP]
    cut = [x for x in ranked if x[1]["key"] not in {y[1]["key"] for y in shown}]
    text = ["# Radar %s" % now.strftime("%Y-%m-%d %H:%M"), "",
            "`python %s` at %s, no model. Findings %d, listed %d (cap %d); inputs or readings missing: %s. Rank is "
            "principle weight x age in runs: quality and tokens 2, optimization and automation 1." % (
                os.path.abspath(__file__).replace("\\", "/"), now.strftime("%Y-%m-%d %H:%M"), len(found), len(shown),
                CAP, "yes" if missing_any else "none"), "",
            "| rank | principle | finding | number (source) | proposal | age |", "|---|---|---|---|---|---|"]
    for i, (_, f, _) in enumerate(shown, 1):
        text.append("| %d | %s | %s | %s | %s | %d |" % (i, f["principle"], cell(f["finding"]), cell(f["number"]),
                                                         cell(f["proposal"]), f["age"]))
    if cut:
        text.append("\nBeyond the cap (%d): %s" % (len(cut), ", ".join(f["key"] for _, f, _ in cut)))
    text += ["", "## Readings", ""] + ["- %s" % n for n in notes]
    keep = ""  # radar-discover.py writes a "## Discover" section into the same file; a rerun keeps it
    try:
        with open(out, encoding="utf-8", errors="replace") as h:
            old = h.read()
        at = 0 if old.startswith("## Discover") else old.find("\n## Discover")
        keep = "\n" + old[at:].strip("\n") + "\n" if at >= 0 else ""
    except OSError:
        pass
    try:
        put(out, "\n".join(text) + "\n" + keep)
    except OSError as e:
        print("radar-flags: cannot write %s: %s" % (out, e), file=sys.stderr)
        return 2
    for n in notes:
        if "missing" in n:
            print(n)
    print("radar %s: findings %d, listed %d -> %s" % (now.strftime("%Y-%m-%d %H:%M"), len(found), len(shown),
                                                     out.replace("\\", "/")))
    return 1 if missing_any else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
