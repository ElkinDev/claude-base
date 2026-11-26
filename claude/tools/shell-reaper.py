"""shell-reaper: ends Claude Code Bash tool shells that outlived their call, with no agent.

Why. The harness keeps an agent alive while a shell it started runs, so a tool call stuck on a wait that never ends
keeps its agent, and whoever waits on that agent, stuck with it. This tool ends such shells.

Run by a scheduled task of the user every 10 minutes through pythonw.exe -I (install/shell-reaper.ps1 in the kit
registers it, INSTALL.md says how); every child starts under CREATE_NO_WINDOW. Its files are under the kit home
(--home, else KIT_HOME, else ~/.claude): logs/shell-reaper.log, shell-reaper.stop and shell-reaper.json (Config below).
Git's ps.exe is read from its default path, or from MSYS_PS_EXE when it is set (msys_tree.py).

A Claude Code Bash tool shell is a bash.exe whose command line holds
`-c "source <profile>/shell-snapshots/snapshot-bash-...`. The rules are labelled R1 to R7 and M1 to M4 below; the
tests carry the same labels.

Class A, kill: a tool shell whose parent is a live claude.exe. Class B, kill: a tool shell whose parent is gone (absent,
  or its pid now names a newer process). Both take the same limit from the command after the snapshot prefix (R2, R3):
  older than 390 minutes when it holds a gate token (LOCK_RELEASED, lockrun, gate-detach; a gate wait cap of 6 hours
  plus margin), else older than 150 minutes when it holds a wait token (sleep, until, while, wait, cca-run), else older
  than 45 minutes. The whole tree goes (taskkill /T /F). A shell whose live parent is another tool shell is an inner
  shell of that call: its top is judged instead.
Exempt (R1): a class A or B shell whose command after the prefix names an exempt token (EXEMPT_TOKENS, plus the ones
  the config adds), word-bounded and in any case, is never killed; past 390 minutes it gets one WATCH line per pid and
  creation time per local day.
Class C, report only: an orphan tree of bash.exe, node.exe, python.exe or timeout.exe older than 12 hours whose top
  command names one of the repo roots the config lists, and where no process of the tree is pythonw.exe or names an
  exempt token (substring, any case). It logs WOULD-KILL lines and never kills. The kit lists no repo root, so class C
  reports nothing until the config names one.

Config. shell-reaper.json in the kit home, optional: a JSON object with "exempt", a list of tokens added to
EXEMPT_TOKENS (your own long jobs, such as a queue runner or a render server, so they are watched instead of killed),
and "repo_roots", the folders class C reports under, each matched with either slash. Keys that start with an
underscore are notes and are ignored. A file that is not such an object, holds another key, or a value that is not a
list of non-empty strings makes the run log one CONFIG-ERROR line, read and kill nothing, and exit 1; no file means the
kit's tokens and no repo root. shell-reaper.example.json beside this file is the shape.

Age (R5) is now minus the later of the creation time and the newest resume from sleep, read once per run from the
System log (Microsoft-Windows-Power-Troubleshooter, event id 1). No such event means no resume: the creation time
counts. A failed read logs one RESUME-ERROR line; that run then uses wall age, kills nothing, and exits 1.
A candidate whose creation time reads 0 or null is skipped with one SKIP line (R4).
A run handles at most 5 kill candidates, each a re-read plus at most one taskkill, so it kills at most 5 trees; the rest
get a DEFER line and are judged again next run (R6). Worst case 60 s ps + 120 s table + 60 s resume + 5 x (60 s + 60 s)
= 14 min, under the 15-minute task cap.

A shell that is this process or one of its ancestors is never a candidate: a scan by command line skips its own tree.
That walk follows live Windows parents only, so it stops at a process MSYS exec'd (its Windows parent is gone), such as
python run by `timeout 150 python shell-reaper.py` in a tool shell. That lineage is covered by judge_tree instead: a
candidate whose joined tree holds this process or one of its ancestors gets the verdict 'own' and is skipped, with no
line.

What a class A kill frees (R7, probe 2026-11-26 06:37): a Bash tool call running `timeout.exe 900 sleep 600` was
killed by `taskkill /T /F` on its top marker shell; the harness ended the call at once (its background task was
notified failed within seconds), while the exec'd timeout.exe, whose Windows parent was gone, survived until it was
killed by hand. So a class A kill frees the agent; the MSYS join below ends such survivors with it.

The MSYS join: each run reads Git's ps.exe -W before the table (msys_tree's identity check needs that order), and a
  class A or B candidate is judged on its joined tree: msys_tree.tree, its Windows children plus the processes MSYS
  fork and exec cut off the Windows parent chain.
M1 PowerShell tool shells (msys_tree.powershell_tool_shell: a powershell.exe whose command line holds
  CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT under a live claude.exe, directly or through the harness's cmd.exe wrapper) are
  never killed: the command lives in an environment variable. Past 390 minutes each gets one WATCH line per pid and
  creation time per local day, like the exempt class.
M2 Before a kill, every process of the joined tree goes through the R1 test (the exempt pattern on its command line,
  read after the snapshot prefix when it carries one) and the gate tokens. An exempt member keeps the tree: one WATCH
  line per pid and creation time per day instead of the kill, naming the member (exempt=<pid>:<name>). A member
  holding a gate token keeps the tree up to 390 minutes. A tree that holds this process or one of its ancestors is
  never killed. A failed ps read logs one MSYS-ERROR line; the tree cannot then be read whole, so that run kills
  nothing and exits 1.
M3 The kill names every root msys_tree.kill_roots returns (the shell, then each member no earlier root's Windows tree
  reaches) in one taskkill /T /F, after one re-read of all of them by pid. A root that is gone or whose creation time
  changed refuses the whole kill: GONE when it is the shell, else REFUSED naming the root (pid reuse never reaches
  taskkill). The KILL line carries taskkill's first output line, and its last one as last= when the exit code is not
  0 (see the probe below: rc 128 can follow SUCCESS lines).
M4 Install: shell-reaper.py and msys_tree.py are copied together into <kit home>/tools/ (the installer copies the
  kit's claude/tools folder whole; msys_tree.py is loaded by path from beside this file, since pythonw -I keeps this
  folder off sys.path; install/shell-reaper.ps1 refuses without it). When it cannot be loaded, a run logs one ERROR
  line, reads and kills nothing, and exits 1.

What taskkill does (probe of 2026-11-26 07:2x, on throwaway pings only):
  Several /PID with one gone (a terminated ping whose handle was held, or 99999996, which no process can hold; named
  in the middle and first): every live pid named is ended, the gone one gives `ERROR: The process "<pid>" not found.`
  after the SUCCESS lines, and the exit code is 128. So rc=128 alone does not mean nothing was ended.
  /T and a reused root: with R holding the pid that S, created before R, names as its parent, `taskkill /T /F /PID R`
  ended R alone and S survived (two runs); the control, S2 created by a P2 that stays alive, ended both. taskkill /T
  thus walks only children created after their parent, the guard msys_tree.windows_subtree applies, so kill_roots
  covers what taskkill reaches.
  taskkill writes in the console's OEM code page, so its output is decoded with it (console_text).

Every action is one line in the log. The stop file makes a run write one line and touch nothing. --dry prints what a
run would do, kills nothing, and writes nothing to the log.
"""
import argparse
import base64
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

CREATE_NO_WINDOW = 0x08000000
TEST_ENV = 'SHELL_REAPER_TEST'  # set by the tests: the live table reader and the real kill refuse under it

MT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'msys_tree.py')


def load_msys_tree(path):
    """msys_tree loaded by path (M4); raises when the file is missing or does not load."""
    spec = importlib.util.spec_from_file_location('msys_tree', path)
    if spec is None or spec.loader is None:
        raise ImportError(f'no loader for {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    MT, MT_ERROR = load_msys_tree(MT_PATH), None
except Exception as _exc:  # M4: run() logs it as one ERROR line and returns 1; never silent
    MT, MT_ERROR = None, _exc

PS_SHOWN = '(PowerShell tool shell: the command is in an environment variable, not visible)'
PLAIN_MIN = 45
WAIT_MIN = 150
GATE_MIN = 390
WATCH_MIN = 390
C_MIN = 12 * 60
KILL_CAP = 5
CMD_CHARS = 160


def kit_home():
    """The kit home, as the installer reads it: KIT_HOME, else ~/.claude."""
    return os.environ.get('KIT_HOME') or os.path.join(os.path.expanduser('~'), '.claude')


def home_paths(home):
    """(log, stop file, config) of a kit home."""
    return (os.path.join(home, 'logs', 'shell-reaper.log'), os.path.join(home, 'shell-reaper.stop'),
            os.path.join(home, 'shell-reaper.json'))


# Matched case-insensitively, as substrings, against the command after the snapshot prefix (classes A and B, R3).
# A gate token gives 390 minutes, else a wait token 150, else the limit is 45.
GATE_TOKENS = ('LOCK_RELEASED', 'lockrun', 'gate-detach')
WAIT_TOKENS = ('sleep', 'until', 'while', 'wait', 'cca-run')
# R1: never killed in class A or B (word-bounded on the command after the prefix, any case); class C exclusions
# (substring, any case, on every process of the tree). It is checked before the tokens above, so a word-bounded
# lockrun or gate-detach exempts the shell and the 390 limit reaches only their other forms (mylockrun.sh).
# The config's "exempt" list adds to these (Rules, load_rules).
EXEMPT_TOKENS = ('gradle', 'gradlew', 'java', 'lockrun', 'gate-detach')
C_NAMES = ('bash.exe', 'node.exe', 'python.exe', 'timeout.exe')
C_EXCLUDE_NAMES = ('pythonw.exe',)
CONFIG_KEYS = ('exempt', 'repo_roots')


def exempt_pattern(tokens):
    return re.compile(r'\b(?:' + '|'.join(re.escape(t) for t in tokens) + r')\b', re.IGNORECASE)


@dataclass(frozen=True)
class Rules:
    """What the config can change: the exempt tokens (the kit's first) and the class C repo roots."""
    exempt: tuple = EXEMPT_TOKENS
    roots: tuple = ()
    exempt_re: object = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, 'exempt_re', exempt_pattern(self.exempt))


DEFAULT_RULES = Rules()

SNAPSHOT_RE = re.compile(r'source\s+[^\s"]*[/\\]shell-snapshots[/\\]snapshot-bash-[^\s"]*', re.IGNORECASE)
EVAL_OPEN = "eval '"
CWD_SUFFIX_RE = re.compile(r"'; \} && pwd -P >\| [^\s\"]+\"?\s*$")

FILETIME_EPOCH_OFFSET = 11644473600  # seconds from 1601-01-01 to 1970-01-01
TICKS_PER_MINUTE = 10_000_000 * 60


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    name: str
    created: int  # creation time as a UTC FILETIME (100 ns ticks since 1601), exact, so pid reuse compares by equality
    cmd: str


@dataclass(frozen=True)
class Action:
    cls: str          # 'A', 'B', 'C', or '-' for a skip
    proc: Proc
    age_min: int
    parent: str       # parent state as logged
    shown: str        # command text as logged (after the snapshot prefix, one line, at most 160 characters)
    kind: str = 'kill'  # 'kill' (A, B), 'watch' (exempt A, B, PowerShell), 'report' (C), 'skip' (creation 0 or null)
    roots: tuple = ()   # kill (M3): (pid, created) of each taskkill /T root, the shell first
    note: str = ''      # watch (M2): 'exempt=<pid>:<name>' when a member of the joined tree made the shell exempt


@dataclass
class Deps:
    read_table: object       # () -> list[Proc]
    read_pids: object        # (pids) -> {pid: Proc} for the pids that still exist, in one read
    kill_trees: object       # (pids) -> (returncode, text): one taskkill /T /F with a /PID per pid
    now_ft: object           # () -> int FILETIME
    read_resume: object      # () -> int FILETIME of the newest resume from sleep, or None when the log holds none
    read_ps: object          # () -> (list[msys_tree.MsysRow], FILETIME taken just before ps.exe started)
    own_pid: int
    log_path: str = field(default_factory=lambda: home_paths(kit_home())[0])
    stop_path: str = field(default_factory=lambda: home_paths(kit_home())[1])
    config_path: str = field(default_factory=lambda: home_paths(kit_home())[2])
    out: object = field(default=print)


def now_filetime():
    return int((time.time() + FILETIME_EPOCH_OFFSET) * 10_000_000)


def filetime_local(ft):
    return datetime.datetime.fromtimestamp(ft / 10_000_000 - FILETIME_EPOCH_OFFSET).strftime('%Y-%m-%d %H:%M:%S')


def is_tool_shell(p):
    return p.name.lower() == 'bash.exe' and SNAPSHOT_RE.search(p.cmd or '') is not None


def after_prefix(cmd):
    """The command a tool shell runs: the text after the snapshot prefix, without the harness eval wrapper."""
    m = SNAPSHOT_RE.search(cmd or '')
    if not m:
        return cmd or ''
    rest = cmd[m.end():]
    i = rest.find(EVAL_OPEN)
    if i >= 0:
        rest = CWD_SUFFIX_RE.sub('', rest[i + len(EVAL_OPEN):])
    return rest


def one_line(text):
    return (text or '').replace('\r\n', '\\n').replace('\n', '\\n').replace('\r', '\\n').replace('\t', ' ')[:CMD_CHARS]


def holds_gate_token(command):
    low = command.lower()
    return any(t.lower() in low for t in GATE_TOKENS)


def limit_minutes(command):
    """The age past which a class A or B shell running this command is killed (R3)."""
    low = command.lower()
    if holds_gate_token(command):
        return GATE_MIN
    if any(t.lower() in low for t in WAIT_TOKENS):
        return WAIT_MIN
    return PLAIN_MIN


def is_exempt(command, rules=DEFAULT_RULES):
    """R1: the command names an exempt token as a whole word (gradle in ./gradle, not in gradle2), in any case."""
    return rules.exempt_re.search(command) is not None


def age_minutes(p, now_ft, resume_ft=None):
    """R5: minutes since the later of the creation time and the newest resume from sleep (None: creation time)."""
    start = p.created if resume_ft is None else max(p.created, resume_ft)
    return (now_ft - start) / TICKS_PER_MINUTE


def parent_of(p, by_pid):
    """(state, parent proc or None): 'live' when the parent pid names a process created no later than the child,
    'reused' when that pid now names a newer process, 'gone' when no process holds it."""
    par = by_pid.get(p.ppid)
    if par is None:
        return 'gone', None
    if par.created > p.created:
        return 'reused', None
    return 'live', par


def children_index(table):
    kids = {}
    by_pid = {p.pid: p for p in table}
    for p in table:
        state, _ = parent_of(p, by_pid)
        if state == 'live' and p.ppid != p.pid:
            kids.setdefault(p.ppid, []).append(p)
    return kids


def subtree(root, kids):
    out, stack, seen = [], [root], set()
    while stack:
        p = stack.pop()
        if p.pid in seen:
            continue
        seen.add(p.pid)
        out.append(p)
        stack.extend(kids.get(p.pid, []))
    return out


def own_lineage(table, own_pid):
    """This process and its live ancestors, by pid. Fails loudly when this process is not in the table."""
    by_pid = {p.pid: p for p in table}
    if own_pid not in by_pid:
        raise RuntimeError(f'own pid {own_pid} is not in the process table; refusing to judge it')
    lineage, p = set(), by_pid[own_pid]
    while p is not None and p.pid not in lineage:
        lineage.add(p.pid)
        state, par = parent_of(p, by_pid)
        p = par if state == 'live' else None
    return lineage


def names_repo_root(cmd, rules=DEFAULT_RULES):
    """The command names one of the config's repo roots, with either slash, in any case: the root must be followed
    by a separator, the end of the command or a space or quote, so D:/work does not match D:/workshop."""
    low = (cmd or '').lower().replace('\\', '/')
    for root in rules.roots:
        r = root.lower().replace('\\', '/').rstrip('/')
        if r and re.search(re.escape(r) + r'''(?=[/\s"']|$)''', low):
            return True
    return False


def tree_excluded(members, rules=DEFAULT_RULES):
    for m in members:
        if m.name.lower() in C_EXCLUDE_NAMES:
            return True
        low = (m.cmd or '').lower()
        if any(t.lower() in low for t in rules.exempt):
            return True
    return False


def string_list(data, key, path):
    """data[key] as a tuple of non-empty strings, () when absent; raises ValueError on anything else."""
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f'{path}: "{key}" must be a list of non-empty strings')
    return tuple(value)


def load_rules(path):
    """The rules of the config at path: the kit's when no file is there; raises on a file that is not a JSON object
    holding only CONFIG_KEYS (and notes, keys that start with an underscore), each a list of non-empty strings."""
    if not os.path.exists(path):
        return DEFAULT_RULES
    with open(path, encoding='utf-8-sig') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f'{path}: a JSON object is expected, not a {type(data).__name__}')
    unknown = sorted(k for k in data if k not in CONFIG_KEYS and not k.startswith('_'))
    if unknown:
        raise ValueError(f'{path}: unknown key {", ".join(unknown)} (known: {", ".join(CONFIG_KEYS)})')
    extra = tuple(t for t in string_list(data, 'exempt', path) if t not in EXEMPT_TOKENS)
    return Rules(EXEMPT_TOKENS + extra, string_list(data, 'repo_roots', path))


def judge_tree(p, table, msys, ps_ft, mine, rules=DEFAULT_RULES):
    """M2 and M3 on p's joined tree: ('own', None) when it holds this process or one of its ancestors; ('exempt', node)
    for the first member, in tree order, whose command passes the R1 test; else ('gate' when a member holds a gate
    token, or 'plain', roots), roots being the (pid, created) of each taskkill /T root, the shell first."""
    nodes = MT.tree(p.pid, table, msys, ps_ft)
    if any(n.pid in mine for n in nodes):
        return 'own', None
    for n in nodes:
        if is_exempt(after_prefix(n.cmd), rules):
            return 'exempt', n
    created = {n.pid: n.created for n in nodes}
    roots = tuple((pid, created[pid]) for pid in MT.kill_roots(nodes, table))
    gate = any(holds_gate_token(after_prefix(n.cmd)) for n in nodes)
    return ('gate' if gate else 'plain'), roots


def plan(table, now_ft, own_pid, resume_ft=None, watched=frozenset(), msys=(), ps_ft=0, rules=DEFAULT_RULES):
    """Every action a run takes on this table, in pid order.
    watched: the (pid, created) pairs the log already holds a WATCH line for today.
    msys, ps_ft: Git's ps.exe -W rows and the FILETIME taken just before ps started (the MSYS join).
    rules: the exempt tokens and repo roots of the config (load_rules)."""
    by_pid = {p.pid: p for p in table}
    kids = children_index(table)
    mine = own_lineage(table, own_pid)
    actions = []
    for p in sorted(table, key=lambda x: x.pid):
        if p.pid in mine:
            continue
        tool = is_tool_shell(p)
        pst = MT.powershell_tool_shell(p.pid, table, now_ft) if p.name.lower() == 'powershell.exe' else None
        if not tool and pst is None and not (p.name.lower() in C_NAMES and names_repo_root(p.cmd, rules)):
            continue
        shown = PS_SHOWN if pst is not None else one_line(after_prefix(p.cmd) if tool else p.cmd)
        if p.created <= 0:  # R4: no age and no pid-reuse check can be trusted
            actions.append(Action('-', p, 0, str(p.ppid), shown, 'skip'))
            continue
        age = age_minutes(p, now_ft, resume_ft)
        state, par = parent_of(p, by_pid)
        if pst is not None:  # M1: never killed
            if age > WATCH_MIN and (p.pid, p.created) not in watched:
                via = f' via cmd.exe:{pst.top}' if pst.top != p.pid else ''
                actions.append(Action('A', p, int(age), f'claude.exe:{pst.claude} live{via}', shown, 'watch'))
            continue
        if tool:
            if state == 'live' and par.name.lower() == 'claude.exe':
                cls, parent = 'A', f'claude.exe:{par.pid} live'
            elif state in ('gone', 'reused'):
                cls, parent = 'B', f'{state}:{p.ppid}'
            else:
                continue  # a live parent that is a tool shell makes this an inner shell: its top is judged instead
            command = after_prefix(p.cmd)
            if is_exempt(command, rules):
                if age > WATCH_MIN and (p.pid, p.created) not in watched:
                    actions.append(Action(cls, p, int(age), parent, shown, 'watch'))
            elif age > limit_minutes(command):
                verdict, detail = judge_tree(p, table, msys, ps_ft, mine, rules)
                if verdict == 'exempt':
                    if (p.pid, p.created) not in watched:
                        actions.append(Action(cls, p, int(age), parent, shown, 'watch',
                                              note=f'exempt={detail.pid}:{detail.name}'))
                elif verdict == 'plain' or (verdict == 'gate' and age > GATE_MIN):
                    actions.append(Action(cls, p, int(age), parent, shown, roots=detail))
            continue
        if state in ('gone', 'reused') and age > C_MIN and not tree_excluded(subtree(p, kids), rules):
            actions.append(Action('C', p, int(age), f'{state}:{p.ppid}', shown, 'report'))
    return actions


def entry(ts, a, verb, extra=''):
    tail = f' {extra}' if extra else ''
    return f'{ts} {a.cls} {verb} pid={a.proc.pid} age={a.age_min} parent={a.parent}{tail} cmd={a.shown}'


def skip_entry(ts, a):
    return f'{ts} - SKIP pid={a.proc.pid} ppid={a.parent} creation time reads 0 or null cmd={a.shown}'


def watch_entry(ts, a):
    note = f' {a.note}' if a.note else ''
    return entry(ts, a, 'WATCH', f'created={a.proc.created}{note}')


# parent= never holds ' created=', so the lazy match stops at the WATCH field even when the command quotes one
WATCH_LINE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} [AB] WATCH pid=(\d+) age=-?\d+ parent=.*? '
                           r'created=(\d+) ')


def read_watched(path, day):
    """(pid, created) of every WATCH line the log holds for this local day (YYYY-MM-DD); an absent log holds none."""
    if not os.path.exists(path):
        return frozenset()
    seen = set()
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = WATCH_LINE_RE.match(line)
            if m and m.group(1) == day:
                seen.add((int(m.group(2)), int(m.group(3))))
    return frozenset(seen)


def msys_entry(ts, exc, held):
    count = '' if held is None else f' ({held} held)'
    return (f'{ts} - MSYS-ERROR {type(exc).__name__}: {one_line(str(exc))}; the joined tree cannot be read, '
            f'no kill this run{count}')


def resume_entry(ts, exc, held):
    count = '' if held is None else f' ({held} held)'
    return f'{ts} - RESUME-ERROR {type(exc).__name__}: {one_line(str(exc))}; wall age used, no kill this run{count}'


def write_log(path, entries):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, 'a', encoding='utf-8', newline='\n') as f:
        for text in entries:
            f.write(text + '\n')


def emit(deps, dry, text):
    if dry:
        deps.out('DRY ' + text)
    else:
        write_log(deps.log_path, [text])
        deps.out(text)


def run(deps, dry):
    """One pass. Returns the exit code: 0 done; 1 msys_tree.py did not load, the config did not read, the table could
    not be read or judged, the ps or resume read failed, or a kill step failed."""
    ts = filetime_local(deps.now_ft())
    if os.path.exists(deps.stop_path):
        emit(deps, dry, f'{ts} - STOP stop file {deps.stop_path} present, nothing read or killed')
        return 0
    if MT is None:  # M4
        emit(deps, dry, f'{ts} - ERROR msys_tree.py not loaded ({type(MT_ERROR).__name__}: {one_line(str(MT_ERROR))}); '
                        f'shell-reaper.py and msys_tree.py are copied together, nothing read or killed')
        return 1
    try:
        rules = load_rules(deps.config_path)
    except Exception as exc:  # a config that does not read stops the run before any read: never a silent default
        emit(deps, dry, f'{ts} - CONFIG-ERROR {type(exc).__name__}: {one_line(str(exc))}; nothing read or killed')
        return 1
    resume_exc = msys_exc = None
    msys, ps_ft = (), 0
    try:  # before the table: msys_tree's identity check needs ps read first
        msys, ps_ft = deps.read_ps()
    except Exception as exc:  # M2: one line below, then no kill this run
        msys_exc = exc
    try:
        table = deps.read_table()
        try:
            resume_ft = deps.read_resume()
        except Exception as exc:  # R5: one line below, then wall age and no kill this run
            resume_ft, resume_exc = None, exc
        actions = plan(table, deps.now_ft(), deps.own_pid, resume_ft, read_watched(deps.log_path, ts[:10]),
                       msys, ps_ft, rules)
    except Exception as exc:  # logged and returned as a failure, never swallowed
        if msys_exc is not None:
            emit(deps, dry, msys_entry(ts, msys_exc, None))
        if resume_exc is not None:
            emit(deps, dry, resume_entry(ts, resume_exc, None))
        emit(deps, dry, f'{ts} - ERROR {type(exc).__name__}: {one_line(str(exc))}')
        return 1
    code, lines, kills = 0, 0, 0
    held = sum(a.kind == 'kill' for a in actions)
    if msys_exc is not None:
        emit(deps, dry, msys_entry(ts, msys_exc, held))
        code, lines = 1, lines + 1
    if resume_exc is not None:
        emit(deps, dry, resume_entry(ts, resume_exc, held))
        code, lines = 1, lines + 1
    for a in actions:
        if a.kind == 'kill':
            if msys_exc is not None or resume_exc is not None:
                continue
            if kills >= KILL_CAP:  # R6: judged again next run
                emit(deps, dry, entry(ts, a, 'DEFER', f'cap {KILL_CAP} kills a run'))
                lines += 1
                continue
            kills += 1
            lines += 1
            if dry:
                deps.out('DRY ' + entry(ts, a, 'WOULD-KILL', roots_text(a)))
                continue
            try:
                emit(deps, False, execute(deps, a, ts))
            except Exception as exc:  # a failed re-read or taskkill is logged, the next one still runs, the run fails
                emit(deps, False, entry(ts, a, 'ERROR', f'{type(exc).__name__}: {one_line(str(exc))}'))
                code = 1
            continue
        if a.kind == 'watch':
            text = watch_entry(ts, a)
        elif a.kind == 'skip':
            text = skip_entry(ts, a)
        elif a.kind == 'report':
            text = entry(ts, a, 'WOULD-KILL')
        else:
            raise ValueError(f'unknown action kind {a.kind!r} for pid {a.proc.pid}')
        emit(deps, dry, text)
        lines += 1
    if dry and not lines:
        deps.out(f'DRY {ts} - nothing to do ({len(table)} processes read)')
    return code


def kill_roots_of(a):
    """(pid, created) of each root the kill names (M3), the shell first; the shell alone when the plan named none."""
    return a.roots or ((a.proc.pid, a.proc.created),)


def roots_text(a):
    """'roots=shell,extra,...' when the kill names more than the shell, else ''."""
    roots = kill_roots_of(a)
    return '' if len(roots) == 1 else 'roots=' + ','.join(str(pid) for pid, _ in roots)


def execute(deps, a, ts):
    """Kill one class A or B tree (M3): one re-read of every root, then one taskkill naming them all; a root that is
    gone or whose creation time changed refuses the whole kill. Returns the log entry."""
    roots = kill_roots_of(a)
    pre = f'{roots_text(a)} ' if len(roots) > 1 else ''
    cur = deps.read_pids([pid for pid, _ in roots])
    for i, (pid, created) in enumerate(roots):
        now = cur.get(pid)
        if i == 0 and now is None:
            return entry(ts, a, 'GONE', f'{pre}ended before the kill')
        if i == 0 and now.created != created:
            return entry(ts, a, 'REFUSED', f'{pre}pid reuse: created {created} now {now.created}')
        if now is None:
            return entry(ts, a, 'REFUSED', f'{pre}root {pid} gone before the kill')
        if now.created != created:
            return entry(ts, a, 'REFUSED', f'{pre}root {pid} pid reuse: created {created} now {now.created}')
    rc, text = deps.kill_trees([pid for pid, _ in roots])
    lines = (text or '').strip().splitlines()
    first = one_line(lines[0]) if lines else '(no output)'
    # rc 128 can follow SUCCESS lines, its ERROR line last (probe in the docstring): a failed kill logs both ends
    last = f' last={one_line(lines[-1])}' if rc != 0 and len(lines) > 1 else ''
    return entry(ts, a, 'KILL', f'rc={rc} {pre}taskkill={first}{last}')


# ---- the live process table, the re-read and the kill (never reached by the tests) ----

PS_TABLE = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$r = @(Get-CimInstance Win32_Process FILTER | ForEach-Object {
  $c = 0; if ($_.CreationDate) { $c = [int64]$_.CreationDate.ToFileTimeUtc() }
  [pscustomobject]@{ p = [int64]$_.ProcessId; q = [int64]$_.ParentProcessId; n = [string]$_.Name; c = $c; l = [string]$_.CommandLine }
})
ConvertTo-Json -InputObject $r -Compress
'''


def parse_table(text):
    """Process rows from the PowerShell JSON (a list of {p, q, n, c, l}); fails loudly on anything else."""
    text = (text or '').lstrip('\ufeff').strip()
    if not text:
        raise RuntimeError('empty process table output')
    rows = json.loads(text)
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise RuntimeError(f'process table is a {type(rows).__name__}, not a list')
    return [Proc(int(r['p']), int(r['q']), str(r.get('n') or ''), int(r.get('c') or 0), str(r.get('l') or ''))
            for r in rows]


PS_RESUME = r'''
$ErrorActionPreference = 'Stop'
try {
  $f = @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Power-Troubleshooter'; Id = 1 }
  $e = Get-WinEvent -FilterHashtable $f -MaxEvents 1
} catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { 'none'; exit 0 }
  throw
}
[string]$e.TimeCreated.ToFileTimeUtc()
'''


def parse_resume(text):
    """The resume FILETIME from the PowerShell output, None for 'none' (no such event); else fails loudly."""
    text = (text or '').lstrip('\ufeff').strip()
    if text == 'none':
        return None
    if not text.isdigit() or int(text) <= 0:
        raise RuntimeError(f'resume read gave {one_line(text)[:80]!r}, not a FILETIME')
    return int(text)


def refuse_under_test(what):
    if os.environ.get(TEST_ENV):
        raise RuntimeError(f'{what} refused: {TEST_ENV} is set, a test never reads the live table or kills')


def powershell(script, timeout):
    refuse_under_test('powershell')
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    r = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                       capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f'powershell exit {r.returncode}: {r.stderr.decode("utf-8", "replace")[:300]}')
    return r.stdout.decode('utf-8', 'replace')


def read_table_live():
    refuse_under_test('read_table_live')
    table = parse_table(powershell(PS_TABLE.replace('FILTER', ''), 120))
    if not table:
        raise RuntimeError('process table read back empty')
    return table


def read_pids_live(pids):
    """{pid: Proc} for those of pids that still exist, in one CIM query."""
    refuse_under_test('read_pids_live')
    where = ' OR '.join(f'ProcessId={int(pid)}' for pid in pids)
    return {p.pid: p for p in parse_table(powershell(PS_TABLE.replace('FILTER', f'-Filter "{where}"'), 60))}


def read_ps_live():
    refuse_under_test('read_ps_live')
    return MT.read_ps_live()


def read_resume_live():
    refuse_under_test('read_resume_live')
    return parse_resume(powershell(PS_RESUME, 60))


def console_text(data):
    """Bytes a console program such as taskkill wrote, decoded with the console's OEM code page."""
    return data.decode('oem', 'replace')


def kill_trees_live(pids):
    """One taskkill /T /F with a /PID per pid (M3)."""
    refuse_under_test('kill_trees_live')
    args = ['taskkill', '/T', '/F']
    for pid in pids:
        args += ['/PID', str(int(pid))]
    r = subprocess.run(args, capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    text = console_text(r.stdout + r.stderr).strip()
    return r.returncode, text


def live_deps(home):
    log, stop, config = home_paths(home)
    return Deps(read_table=read_table_live, read_pids=read_pids_live, kill_trees=kill_trees_live,
                now_ft=now_filetime, read_resume=read_resume_live, read_ps=read_ps_live, own_pid=os.getpid(),
                log_path=log, stop_path=stop, config_path=config)


def main(argv=None, deps=None):
    ap = argparse.ArgumentParser(description='End Claude Code Bash tool shells that outlived their call.')
    ap.add_argument('--dry', action='store_true', help='print what a run would do; kill nothing, log nothing')
    ap.add_argument('--home', help='the kit home whose log, stop file and config a run uses (default: KIT_HOME, '
                                   'else ~/.claude)')
    args = ap.parse_args(argv)
    return run(deps or live_deps(args.home or kit_home()), args.dry)


if __name__ == '__main__':
    sys.exit(main())
