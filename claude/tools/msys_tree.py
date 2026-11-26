"""msys_tree: every Windows pid of a process tree, including what MSYS fork and exec cut off the Windows parent chain.

shell-reaper.py beside this file judges a tool shell on the tree this module joins and ends it through kill_roots.

Why. taskkill /T follows Win32_Process.ParentProcessId. Git's MSYS runtime makes a fork a new Windows process and an
exec of an MSYS program another one, after which the forked process ends; the exec'd process keeps its MSYS pid and
MSYS parent but its Windows parent pid names a process that is gone (read live 2026-11-26 06:40: timeout.exe 2736 =
MSYS 778133, MSYS parent 778131 = WINPID 23844 alive, Windows parent 29136 absent). An exec of a native program
leaves the forked process alive as a stub with the native process as its Windows child, so that link survives.

How. Git's ps.exe -W prints, read live 2026-11-26 (cygwin ps 3.6.7):
      PID    PPID    PGID     WINPID   TTY         UID    STIME COMMAND
  4194308       0       0          4  ?              0   Nov 19 System
   778133  778131  778133       2736  cons0     197611 06:20:29 /usr/bin/timeout
MSYS rows carry their MSYS pid, MSYS parent pid and WINPID; a Windows-only process shows PID = 4194304 + WINPID
(System: 4194308 for WINPID 4) and is dropped here, the CIM table holds those. ps -ef prints no WINPID; plain ps and
ps -l print the same columns without the Windows rows. COMMAND in this format is the program path only.

The tree joins two edge kinds, from the root down:
- Windows edge p -> c: c.ppid == p.pid and p was created no later than c (an older c names a reused pid).
- MSYS edge: rows r and m of one ps read with r.ppid == m.pid link m's WINPID to r's WINPID, when both WINPIDs pass
  the identity check: present in the CIM table, created no later than the moment ps started (a later creation means
  the pid was reused after ps listed it), and the same image (basename of COMMAND against the CIM Name, without .exe).
Read ps first, then the CIM table, and pass the time taken just before ps started as ps_read_ft.

PowerShell tool shells: the harness starts a Claude Code PowerShell call as
claude.exe -> cmd.exe /d /s /c "chcp 65001 >nul & powershell.exe ... $env:CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT ..." ->
powershell.exe (read live 2026-11-26 06:36, cmd 25808 and powershell 23908 under claude 27048). Both command lines
hold the marker, the command itself lives in the environment variable. powershell_tool_shell also accepts a
powershell.exe that is a direct child of claude.exe.

ps.exe is read from Git for Windows' default path, or from MSYS_PS_EXE when it is set (Git installed elsewhere).

CLI: python msys_tree.py PID | --own-claude. Read-only: it starts ps.exe and one powershell.exe, never kills.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

PS_EXE = 'C:/Program Files/Git/usr/bin/ps.exe'  # Git for Windows' default install; MSYS_PS_EXE names another
PS_ENV = 'MSYS_PS_EXE'
MAX_PID = 4194304          # ps -W: a Windows-only process shows PID = MAX_PID + WINPID
LAUNCHER_MARK = 'CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT'
CREATE_NO_WINDOW = 0x08000000
TEST_ENV = 'MSYS_TREE_TEST'  # set by the tests: the live readers refuse under it
FILETIME_EPOCH_OFFSET = 11644473600
TICKS_PER_MINUTE = 10_000_000 * 60
CMD_CHARS = 160

PS_HEAD = ['PID', 'PPID', 'PGID', 'WINPID']
# optional status flag, PID PPID PGID WINPID TTY UID, STIME as HH:MM:SS (started within a day) or "Mon DD", COMMAND
PS_ROW_RE = re.compile(r'^\s*(?:([A-Z])\s+)?(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s+'
                       r'(\d{2}:\d{2}:\d{2}|[A-Z][a-z]{2}\s+\d{1,2})(?:\s(.*))?$')


@dataclass(frozen=True)
class WinProc:
    """A Win32_Process row; field names match shell-reaper's Proc, so its table can be passed as it is."""
    pid: int
    ppid: int
    name: str
    created: int  # creation time as a UTC FILETIME
    cmd: str


@dataclass(frozen=True)
class MsysRow:
    pid: int
    ppid: int
    pgid: int
    winpid: int
    command: str


@dataclass(frozen=True)
class Node:
    pid: int
    name: str
    depth: int
    parent: object    # tree parent pid, None for the root
    via: str          # 'root', 'win' (Windows parent), 'msys' (MSYS parent), 'win+msys' (both name the tree parent)
    msys_pid: object  # MSYS pid when an MSYS row names this pid and passes the identity check, else None
    win_parent: str   # '<live|gone|reused>:<ParentProcessId>'
    created: int
    cmd: str


@dataclass(frozen=True)
class PsToolShell:
    pid: int          # the powershell.exe
    top: int          # the process a kill starts from: the cmd.exe wrapper when there is one, else the powershell.exe
    claude: int       # the claude.exe that started it
    age_min: float    # minutes since the powershell.exe was created


@dataclass
class Deps:
    read_ps: object       # () -> (list[MsysRow], ps_read_ft)
    read_table: object    # () -> list[WinProc]
    now_ft: object        # () -> FILETIME
    own_pid: int
    out: object = field(default=print)


def now_filetime():
    return int((time.time() + FILETIME_EPOCH_OFFSET) * 10_000_000)


# ---- parsing ----

def parse_ps(text):
    """MSYS rows of `ps.exe -W` output; Windows-only rows are dropped. Fails loudly on any other format."""
    lines = [ln for ln in (text or '').lstrip('\ufeff').splitlines() if ln.strip()]
    if not lines:
        raise ValueError('empty ps output')
    head = lines[0].split()
    if head[:4] != PS_HEAD:
        raise ValueError(f'ps header {lines[0].strip()!r} does not start with PID PPID PGID WINPID (ps -W format)')
    rows = []
    for ln in lines[1:]:
        m = PS_ROW_RE.match(ln)
        if not m:
            raise ValueError(f'ps row not in the -W format: {ln!r}')
        pid, ppid, pgid, winpid = (int(m.group(i)) for i in (2, 3, 4, 5))
        if pid >= MAX_PID:
            continue
        rows.append(MsysRow(pid, ppid, pgid, winpid, (m.group(9) or '').strip()))
    if not rows:
        raise ValueError('ps output holds no MSYS row (ps lists itself, so a live read always has one)')
    return rows


def parse_cim(text):
    """Process rows from the PowerShell JSON (a list of {p, q, n, c, l}); fails loudly on anything else."""
    text = (text or '').lstrip('\ufeff').strip()
    if not text:
        raise ValueError('empty process table output')
    rows = json.loads(text)
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise ValueError(f'process table is a {type(rows).__name__}, not a list')
    return [WinProc(int(r['p']), int(r['q']), str(r.get('n') or ''), int(r.get('c') or 0), str(r.get('l') or ''))
            for r in rows]


# ---- the join ----

def _image(name):
    base = re.split(r'[/\\]', (name or '').strip())[-1].lower()
    return base[:-4] if base.endswith('.exe') else base


def _win_parent(p, by_pid):
    """('live', parent) when the parent pid names a process created no later than p, ('reused', None) when it names a
    newer one, ('gone', None) when no process holds it."""
    par = by_pid.get(p.ppid)
    if par is None or par.pid == p.pid:
        return 'gone', None
    if par.created > p.created:
        return 'reused', None
    return 'live', par


class _Join:
    def __init__(self, table, msys_rows, ps_read_ft):
        self.by_pid = {p.pid: p for p in table}
        self.win_kids = {}
        for p in table:
            state, par = _win_parent(p, self.by_pid)
            if state == 'live':
                self.win_kids.setdefault(par.pid, []).append(p)
        self.msys_by_win = {}   # WINPID -> MSYS row, identity-checked
        for r in msys_rows:
            proc = self.by_pid.get(r.winpid)
            if proc is not None and proc.created <= ps_read_ft and _image(proc.name) == _image(r.command):
                self.msys_by_win[r.winpid] = r
        by_msys = {r.pid: r for r in self.msys_by_win.values()}
        self.msys_parent_win = {}  # WINPID -> WINPID of its MSYS parent
        self.msys_kids = {}        # WINPID -> [child procs by MSYS parent]
        for r in self.msys_by_win.values():
            m = by_msys.get(r.ppid)
            if m is not None and m.pid != r.pid:
                self.msys_parent_win[r.winpid] = m.winpid
                self.msys_kids.setdefault(m.winpid, []).append(self.by_pid[r.winpid])

    def node(self, p, depth, parent):
        state, par = _win_parent(p, self.by_pid)
        if parent is None:
            via = 'root'
        else:
            win = par is not None and par.pid == parent
            msys = self.msys_parent_win.get(p.pid) == parent
            via = 'win+msys' if win and msys else 'win' if win else 'msys'
        r = self.msys_by_win.get(p.pid)
        return Node(p.pid, p.name, depth, parent, via, r.pid if r else None, f'{state}:{p.ppid}', p.created, p.cmd)


def tree(root_pid, table, msys_rows, ps_read_ft):
    """The root and every descendant through Windows and MSYS edges, depth first, parents before children; at each
    process its Windows children come first (by pid), then its MSYS children. Raises LookupError for an absent root."""
    j = _Join(table, msys_rows, ps_read_ft)
    root = j.by_pid.get(root_pid)
    if root is None:
        raise LookupError(f'pid {root_pid} is not in the process table')
    out, seen = [], set()
    stack = [(root, 0, None)]
    while stack:
        p, depth, parent = stack.pop()
        if p.pid in seen:
            continue
        seen.add(p.pid)
        out.append(j.node(p, depth, parent))
        nxt = sorted(j.win_kids.get(p.pid, []), key=lambda x: x.pid)
        nxt += sorted(j.msys_kids.get(p.pid, []), key=lambda x: x.pid)
        stack.extend((c, depth + 1, p.pid) for c in reversed(nxt) if c.pid not in seen)
    return out


def windows_subtree(root_pid, table):
    """The pids taskkill /T would reach from root_pid: Windows edges only (with the creation-order guard)."""
    by_pid = {p.pid: p for p in table}
    kids = {}
    for p in table:
        state, par = _win_parent(p, by_pid)
        if state == 'live':
            kids.setdefault(par.pid, []).append(p.pid)
    out, stack = set(), [root_pid]
    while stack:
        pid = stack.pop()
        if pid in out:
            continue
        out.add(pid)
        stack.extend(kids.get(pid, []))
    return out


def kill_roots(nodes, table):
    """The pids to end with taskkill /T, in tree order, so that together they cover every node: the root, then each
    node no earlier root's Windows subtree reaches."""
    covered, roots = set(), []
    for n in nodes:
        if n.pid not in covered:
            roots.append(n.pid)
            covered |= windows_subtree(n.pid, table)
    return roots


def nearest_ancestor(pid, name, table, msys_rows, ps_read_ft):
    """The nearest ancestor of pid whose image is `name`, walking the live Windows parent and, where that chain is
    broken, the MSYS parent. None when the walk ends without one."""
    j = _Join(table, msys_rows, ps_read_ft)
    want = _image(name)
    p, seen = j.by_pid.get(pid), set()
    while p is not None and p.pid not in seen:
        seen.add(p.pid)
        state, par = _win_parent(p, j.by_pid)
        if state != 'live':
            par = j.by_pid.get(j.msys_parent_win.get(p.pid))
        if par is not None and _image(par.name) == want:
            return par.pid
        p = par
    return None


def one_line(text):
    return (text or '').replace('\r\n', '\\n').replace('\n', '\\n').replace('\r', '\\n').replace('\t', ' ')[:CMD_CHARS]


def format_tree(nodes, now_ft):
    lines = []
    for n in nodes:
        age = (now_ft - n.created) / TICKS_PER_MINUTE
        msys = n.msys_pid if n.msys_pid is not None else '-'
        lines.append(f'{"  " * n.depth}{n.pid} {n.name} via={n.via} msys={msys} winparent={n.win_parent} '
                     f'age={age:.0f}m cmd={one_line(n.cmd)}')
    return lines


# ---- PowerShell tool shells ----

def powershell_tool_shell(pid, table, now_ft):
    """A PsToolShell when pid is a powershell.exe whose command line holds the launcher marker and whose live parent
    is claude.exe, or a cmd.exe holding the marker whose live parent is claude.exe; None otherwise."""
    by_pid = {p.pid: p for p in table}
    p = by_pid.get(pid)
    if p is None or p.name.lower() != 'powershell.exe' or LAUNCHER_MARK not in (p.cmd or ''):
        return None
    state, par = _win_parent(p, by_pid)
    if state != 'live':
        return None
    if par.name.lower() == 'claude.exe':
        top, claude = p, par
    elif par.name.lower() == 'cmd.exe' and LAUNCHER_MARK in (par.cmd or ''):
        state, gp = _win_parent(par, by_pid)
        if state != 'live' or gp.name.lower() != 'claude.exe':
            return None
        top, claude = par, gp
    else:
        return None
    return PsToolShell(p.pid, top.pid, claude.pid, (now_ft - p.created) / TICKS_PER_MINUTE)


# ---- live reads (never reached by the tests) ----

PS_TABLE = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$r = @(Get-CimInstance Win32_Process | ForEach-Object {
  $c = 0; if ($_.CreationDate) { $c = [int64]$_.CreationDate.ToFileTimeUtc() }
  [pscustomobject]@{ p = [int64]$_.ProcessId; q = [int64]$_.ParentProcessId; n = [string]$_.Name; c = $c; l = [string]$_.CommandLine }
})
ConvertTo-Json -InputObject $r -Compress
'''


def ps_exe():
    """Git's ps.exe: MSYS_PS_EXE when it is set, else the default install path."""
    return os.environ.get(PS_ENV) or PS_EXE


def refuse_under_test(what):
    if os.environ.get(TEST_ENV):
        raise RuntimeError(f'{what} refused: {TEST_ENV} is set, a test never reads the live tables')


def read_ps_live():
    """(MSYS rows, FILETIME taken just before ps.exe started)."""
    refuse_under_test('read_ps_live')
    started = now_filetime()
    r = subprocess.run([ps_exe(), '-W'], capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f'ps.exe exit {r.returncode}: {r.stderr.decode("utf-8", "replace")[:300]}')
    return parse_ps(r.stdout.decode('utf-8', 'replace')), started


def read_table_live():
    refuse_under_test('read_table_live')
    encoded = base64.b64encode(PS_TABLE.encode('utf-16-le')).decode('ascii')
    r = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                       capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f'powershell exit {r.returncode}: {r.stderr.decode("utf-8", "replace")[:300]}')
    return parse_cim(r.stdout.decode('utf-8', 'replace'))


def live_deps():
    return Deps(read_ps=read_ps_live, read_table=read_table_live, now_ft=now_filetime, own_pid=os.getpid())


def main(argv=None, deps=None):
    ap = argparse.ArgumentParser(description='Print a process tree joined across MSYS fork and exec. Read-only.')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('pid', nargs='?', type=int, help='Windows pid of the root')
    g.add_argument('--own-claude', action='store_true', help='root at the nearest claude.exe ancestor of this process')
    args = ap.parse_args(argv)
    deps = deps or live_deps()
    msys, ps_read_ft = deps.read_ps()     # ps first, then the table: the identity check needs that order
    table = deps.read_table()
    if args.own_claude:
        root = nearest_ancestor(deps.own_pid, 'claude.exe', table, msys, ps_read_ft)
        if root is None:
            raise LookupError(f'no claude.exe among the ancestors of pid {deps.own_pid}')
        note = f' (nearest claude.exe ancestor of pid {deps.own_pid})'
    else:
        root, note = args.pid, ''
    nodes = tree(root, table, msys, ps_read_ft)
    deps.out(f'tree of {root} {nodes[0].name}{note}')
    for line in format_tree(nodes, deps.now_ft()):
        deps.out(line)
    reach = windows_subtree(root, table)
    roots = kill_roots(nodes, table)
    deps.out(f'summary: {len(nodes)} processes; taskkill /T {root} reaches {len(reach)}; '
             f'{len(nodes) - len(reach)} more only through the MSYS join; kill roots {" ".join(map(str, roots))}')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # command lines may hold any character
    sys.exit(main())
