"""MsysTreeTest: msys_tree.py on captured ps.exe -W and Win32_Process tables, never the live ones.

MSYS_TREE_TEST is set before the module loads (the live readers refuse under it) and the module's subprocess is
replaced by a tripwire that fails the test on any use. The one case that starts a process, the MSYS_PS_EXE case,
restores it for a fake ps.cmd in a temporary folder and puts the tripwire back.

    python claude/tools/tests/test-msys-tree.py

Fixtures: two live captures of 2026-11-26 (raw ps.exe bytes, LF, no BOM, and the Win32_Process table), trimmed to the
rows each case needs. Four long command lines are cut where marked [cut]; user names, folders and project names in
command lines are replaced by generic ones; pids, parents, creation times and images are as captured. Every expected
value below was read by hand from those captures.

- CAP1 (06:40:42, ps read started at FILETIME 134401668426948992):
  * A gate.sh run with an exec chain under it. The Git launcher 31624 starts usr/bin/bash 17072 (MSYS 778123),
    which forks 23844 (MSYS 778131). That fork ran `timeout 2400 python mut.py`: the forked Windows process 29136
    exec'd timeout.exe 2736 (MSYS 778133) and ended, so 2736's Windows parent is gone. timeout forked again; that
    child (10876, a timeout.exe Windows process that stays alive) exec'd the native python 31068 (MSYS 778136), which
    runs node 28168. taskkill /T 31624 reaches 31624, 17072, 23844 and 17556 only.
  * The capturing session's own Bash tool shell: claude.exe 27048, launcher 28904, usr/bin/bash 10188 (MSYS 789015),
    then `timeout 150 python -I capture.py` as above (timeout 27076 with its Windows parent 16920 gone, stub 13308,
    python 21824 = MSYS 789040), and the CIM reader powershell 19768 under python.
  * An MSYS sleep 3980 (MSYS 787041) whose MSYS parent 787037 is in neither table.
- CAP0 (06:36): a Claude Code PowerShell tool shell as the harness starts it: claude.exe 27048, cmd.exe 25808 (chcp
  and the launcher command, output redirected to a task file), powershell.exe 23908; and a Bash tool shell 14696.
"""
import dataclasses
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

os.environ['MSYS_TREE_TEST'] = '1'
HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), 'msys_tree.py')
_spec = importlib.util.spec_from_file_location('msys_tree', TOOL)
mt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mt)


class Tripwire:
    def __getattr__(self, name):
        raise AssertionError(f'the test reached subprocess.{name}: a live read')


TRIPWIRE = Tripwire()
mt.subprocess = TRIPWIRE

PS_HEADER = '      PID    PPID    PGID     WINPID   TTY         UID    STIME COMMAND'
PS_CAP1 = '\n'.join([
    PS_HEADER,
    '  4194308       0       0          4  ?              0   Nov 19 System',
    '  4194540       0       0        236  ?              0   Nov 19 *** unknown ***',
    '   778123       1  778123      17072  cons0     197611 06:20:29 /usr/bin/bash',
    '   778131  778123  778123      23844  cons0     197611 06:20:29 /usr/bin/bash',
    '   778133  778131  778133       2736  cons0     197611 06:20:29 /usr/bin/timeout',
    '   778136  778133  778133      31068  cons0     197611 06:20:29 /c/Users/user/AppData/Local/Programs/Python/Python312/python',
    '  4205180       0       0      10876  ?              0 06:20:29 C:\\Program Files\\Git\\usr\\bin\\timeout.exe',
    '  4221352       0       0      27048  ?              0 06:06:52 C:\\Users\\user\\AppData\\Roaming\\fnm\\node-versions\\v24.22.0\\installation\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe',
    '  4223208       0       0      28904  ?              0 06:40:42 C:\\Program Files\\Git\\bin\\bash.exe',
    '  4225928       0       0      31624  ?              0 06:20:29 C:\\Program Files\\Git\\bin\\bash.exe',
    '   787041  787037  787037       3980  ?         197611 06:37:01 /usr/bin/sleep',
    '   789015       1  789015      10188  ?         197611 06:40:42 /usr/bin/bash',
    '   789035  789015  789035      27076  ?         197611 06:40:42 /usr/bin/timeout',
    '   789040  789035  789035      21824  ?         197611 06:40:42 /c/Users/user/AppData/Local/Programs/Python/Python312/python',
    '   789048       1  789048       9844  ?         197611 06:40:42 /usr/bin/ps',
]) + '\n'
PS_CAP1_READ_FT = 134401668426948992

CIM_CAP1 = '[' + ','.join(r'''
{"p": 0, "q": 0, "n": "System Idle Process", "c": 134395680008127210, "l": ""}
{"p": 4, "q": 0, "n": "System", "c": 134395680008127210, "l": ""}
{"p": 31624, "q": 30004, "n": "bash.exe", "c": 134401656293009260, "l": "\"C:\\Program Files\\Git\\bin\\bash.exe\" D:/work/build/app-notes/fix5/gate.sh "}
{"p": 17072, "q": 31624, "n": "bash.exe", "c": 134401656293838390, "l": "\"C:\\Program Files\\Git\\bin\\..\\usr\\bin\\bash.exe\" D:/work/build/app-notes/fix5/gate.sh "}
{"p": 23844, "q": 17072, "n": "bash.exe", "c": 134401656294493800, "l": "\"C:\\Program Files\\Git\\bin\\..\\usr\\bin\\bash.exe\" D:/work/build/app-notes/fix5/gate.sh "}
{"p": 17556, "q": 31624, "n": "conhost.exe", "c": 134401656293078560, "l": "\\??\\C:\\WINDOWS\\system32\\conhost.exe 0x4"}
{"p": 2736, "q": 29136, "n": "timeout.exe", "c": 134401656294705980, "l": "\"C:\\Program Files\\Git\\usr\\bin\\timeout.exe\" 2400 python mut.py"}
{"p": 10876, "q": 2736, "n": "timeout.exe", "c": 134401656294831320, "l": "\"C:\\Program Files\\Git\\usr\\bin\\timeout.exe\" 2400 python mut.py"}
{"p": 31068, "q": 10876, "n": "python.exe", "c": 134401656295187280, "l": "C:\\Users\\user\\AppData\\Local\\Programs\\Python\\Python312\\python.exe mut.py"}
{"p": 28168, "q": 31068, "n": "node.exe", "c": 134401667515072290, "l": "node tests/identity.test.mjs"}
{"p": 27048, "q": 14952, "n": "claude.exe", "c": 134401648121195690, "l": "\"C:\\Users\\user\\AppData\\Local\\fnm_multishells\\19368_1795094559072\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe\" --model opus -r"}
{"p": 28904, "q": 27048, "n": "bash.exe", "c": 134401668419735200, "l": "\"C:\\Program Files\\Git\\bin\\bash.exe\" -c \"source /c/Users/user/.claude/shell-snapshots/snapshot-bash-1795691411641-3ukg9v.sh 2>/dev/null || true && [cut]"}
{"p": 10188, "q": 28904, "n": "bash.exe", "c": 134401668419909600, "l": "\"C:\\Program Files\\Git\\bin\\..\\usr\\bin\\bash.exe\" -c \"source /c/Users/user/.claude/shell-snapshots/snapshot-bash-1795691411641-3ukg9v.sh 2>/dev/null || true && [cut]"}
{"p": 31228, "q": 10188, "n": "conhost.exe", "c": 134401668420113300, "l": "\\??\\C:\\WINDOWS\\system32\\conhost.exe 0x4"}
{"p": 27076, "q": 16920, "n": "timeout.exe", "c": 134401668426260960, "l": "\"C:\\Program Files\\Git\\usr\\bin\\timeout.exe\" 150 python -I capture.py cap1"}
{"p": 13308, "q": 27076, "n": "timeout.exe", "c": 134401668426375070, "l": "\"C:\\Program Files\\Git\\usr\\bin\\timeout.exe\" 150 python -I capture.py cap1"}
{"p": 21824, "q": 13308, "n": "python.exe", "c": 134401668426574750, "l": "C:\\Users\\user\\AppData\\Local\\Programs\\Python\\Python312\\python.exe -I capture.py cap1"}
{"p": 19768, "q": 21824, "n": "powershell.exe", "c": 134401668430331730, "l": "powershell.exe -NoProfile -NonInteractive -EncodedCommand CgAkAEUAcgByAG8AcgBBAGMAdABpAG8AbgBQAHIAZQ [cut]"}
{"p": 29224, "q": 19768, "n": "conhost.exe", "c": 134401668433920670, "l": "\\??\\C:\\WINDOWS\\system32\\conhost.exe 0x4"}
{"p": 3980, "q": 18680, "n": "sleep.exe", "c": 134401666216173720, "l": "\"C:\\Program Files\\Git\\usr\\bin\\sleep.exe\" 600"}
'''.strip().splitlines()) + ']'

CIM_CAP0 = '[' + ','.join(r'''
{"p": 27048, "q": 14952, "n": "claude.exe", "c": 134401648121195690, "l": "\"C:\\Users\\user\\AppData\\Local\\fnm_multishells\\19368_1795094559072\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe\" --model opus -r"}
{"p": 25808, "q": 27048, "n": "cmd.exe", "c": 134401666038540000, "l": "C:\\WINDOWS\\System32\\cmd.exe /d /s /c \"\"C:\\WINDOWS\\System32\\chcp.com\" 65001 >nul & \"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe\" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"$__claudeCodeScript = $env:CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT; $env:CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT = $null; Invoke-Expression -Command $__claudeCodeScript\" > \"C:\\Users\\user\\AppData\\Local\\Temp\\claude\\0a1b2c3d\\tasks\\b1.output\" 2>&1\""}
{"p": 23908, "q": 25808, "n": "powershell.exe", "c": 134401666039062900, "l": "\"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe\"  -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"$__claudeCodeScript = $env:CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT; $env:CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT = $null; Invoke-Expression -Command $__claudeCodeScript\" "}
{"p": 14696, "q": 27048, "n": "bash.exe", "c": 134401663649287760, "l": "\"C:\\Program Files\\Git\\bin\\bash.exe\" -c \"source /c/Users/user/.claude/shell-snapshots/snapshot-bash-1795691411641-3ukg9v.sh 2>/dev/null || true && [cut]"}
'''.strip().splitlines()) + ']'

TICKS_PER_MINUTE = 10_000_000 * 60
GATE_ORDER = [31624, 17072, 23844, 2736, 10876, 31068, 28168, 17556]
OWN_SHELL_ORDER = [28904, 10188, 31228, 27076, 13308, 21824, 19768, 29224]


def cap1():
    return mt.parse_cim(CIM_CAP1), mt.parse_ps(PS_CAP1)


def cap0():
    return mt.parse_cim(CIM_CAP0)


def replace_row(table, pid, **changes):
    return [dataclasses.replace(p, **changes) if p.pid == pid else p for p in table]


def without(table, pid):
    return [p for p in table if p.pid != pid]


def created(table, pid):
    return next(p.created for p in table if p.pid == pid)


class MsysTreeTest(unittest.TestCase):
    # ---- ps.exe -W output ----
    def test_parse_ps_keeps_the_msys_rows_and_drops_the_windows_only_rows(self):
        rows = mt.parse_ps(PS_CAP1)
        self.assertEqual([r.pid for r in rows],
                         [778123, 778131, 778133, 778136, 787041, 789015, 789035, 789040, 789048])
        self.assertEqual(rows[2], mt.MsysRow(778133, 778131, 778133, 2736, '/usr/bin/timeout'))
        self.assertEqual(rows[3].command, '/c/Users/user/AppData/Local/Programs/Python/Python312/python')

    def test_parse_ps_reads_a_status_flag_and_a_start_date_older_than_a_day(self):
        text = PS_HEADER + '\n' + (
            'I   778131  778123  778123      23844  cons0     197611   Nov 24 /usr/bin/bash\n'
            '   778133  778131  778133       2736  cons0     197611 06:20:29 /c/Program Files/Some Tool/tool\n')
        rows = mt.parse_ps(text)
        self.assertEqual(rows, [mt.MsysRow(778131, 778123, 778123, 23844, '/usr/bin/bash'),
                                mt.MsysRow(778133, 778131, 778133, 2736, '/c/Program Files/Some Tool/tool')])

    def test_parse_ps_strips_a_bom_and_crlf(self):
        rows = mt.parse_ps('\ufeff' + PS_CAP1.replace('\n', '\r\n'))
        self.assertEqual(len(rows), 9)
        self.assertEqual(rows[0], mt.MsysRow(778123, 1, 778123, 17072, '/usr/bin/bash'))

    def test_parse_ps_fails_loudly_on_the_wrong_format(self):
        ef = ('     UID     PID    PPID  TTY        STIME COMMAND\n'
              '    user  778131  778123 cons0    06:20:29 /usr/bin/bash D:/work/x/gate.sh\n')
        for name, text in (('ps -ef header, no WINPID', ef), ('empty', ''), ('header only', PS_HEADER + '\n'),
                           ('malformed row', PS_HEADER + '\n   778131  778123  oops\n'),
                           ('columns in another order', PS_HEADER.replace('PGID     WINPID', 'WINPID     PGID') + '\n'
                            + '   778131  778123  778123      23844  cons0     197611 06:20:29 /usr/bin/bash\n')):
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    mt.parse_ps(text)

    def test_parse_cim_fails_loudly_on_empty_or_non_list_output(self):
        for text in ('', '   ', '"x"'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    mt.parse_cim(text)
        one = mt.parse_cim('{"p": 4, "q": 0, "n": "System", "c": 1, "l": null}')
        self.assertEqual(one, [mt.WinProc(4, 0, 'System', 1, '')])

    # ---- the tree ----
    def test_finding_1_exec_chain_with_a_broken_windows_parent_is_in_the_tree(self):
        table, msys = cap1()
        nodes = mt.tree(31624, table, msys, PS_CAP1_READ_FT)
        self.assertEqual([n.pid for n in nodes], GATE_ORDER)
        by = {n.pid: n for n in nodes}
        self.assertEqual((by[31624].via, by[31624].parent, by[31624].depth), ('root', None, 0))
        self.assertEqual((by[17072].via, by[17072].parent, by[17072].msys_pid), ('win', 31624, 778123))
        self.assertEqual((by[23844].via, by[23844].parent), ('win+msys', 17072))
        # 2736's Windows parent 29136 is gone: only the MSYS parent 778131 (WINPID 23844) links it
        self.assertEqual((by[2736].via, by[2736].parent, by[2736].msys_pid, by[2736].win_parent),
                         ('msys', 23844, 778133, 'gone:29136'))
        self.assertEqual((by[10876].via, by[10876].parent, by[10876].msys_pid), ('win', 2736, None))
        self.assertEqual((by[31068].via, by[31068].parent, by[31068].depth), ('win', 10876, 5))
        self.assertEqual((by[28168].via, by[28168].parent, by[28168].depth), ('win', 31068, 6))

    def test_taskkill_t_reach_and_the_kill_roots_that_cover_the_whole_tree(self):
        table, msys = cap1()
        self.assertEqual(sorted(mt.windows_subtree(31624, table)), [17072, 17556, 23844, 31624])
        nodes = mt.tree(31624, table, msys, PS_CAP1_READ_FT)
        self.assertEqual(mt.kill_roots(nodes, table), [31624, 2736])

    def test_own_bash_tool_shell_with_timeout_python_is_whole(self):
        table, msys = cap1()
        nodes = mt.tree(28904, table, msys, PS_CAP1_READ_FT)
        self.assertEqual([n.pid for n in nodes], OWN_SHELL_ORDER)
        by = {n.pid: n for n in nodes}
        self.assertEqual((by[27076].via, by[27076].parent, by[27076].win_parent), ('msys', 10188, 'gone:16920'))
        self.assertEqual((by[21824].via, by[21824].parent, by[21824].msys_pid), ('win', 13308, 789040))
        self.assertEqual(mt.kill_roots(nodes, table), [28904, 27076])

    def test_an_msys_row_whose_msys_parent_is_gone_joins_no_tree(self):
        table, msys = cap1()
        self.assertEqual([n.pid for n in mt.tree(3980, table, msys, PS_CAP1_READ_FT)], [3980])
        for root in (31624, 28904, 27048):
            with self.subTest(root=root):
                self.assertNotIn(3980, [n.pid for n in mt.tree(root, table, msys, PS_CAP1_READ_FT)])

    def test_windows_pid_reuse_a_stranger_older_than_its_named_parent_is_left_out(self):
        table, msys = cap1()
        stranger_win = mt.WinProc(40001, 23844, 'python.exe', created(table, 23844) - TICKS_PER_MINUTE, 'python old.py')
        stranger_msys = mt.WinProc(40002, 2736, 'node.exe', created(table, 2736) - 1, 'node old.mjs')
        nodes = mt.tree(31624, table + [stranger_win, stranger_msys], msys, PS_CAP1_READ_FT)
        self.assertEqual([n.pid for n in nodes], GATE_ORDER)
        self.assertEqual(mt.windows_subtree(23844, table + [stranger_win]), {23844})
        self.assertEqual(mt.windows_subtree(2736, table + [stranger_msys]), {2736, 10876, 31068, 28168})

    def test_winpid_reuse_a_winpid_now_held_by_a_process_newer_than_the_ps_read_is_refused(self):
        table, msys = cap1()
        # 31068 ended after ps listed it and a new python.exe took the pid, under an unrelated parent
        reused = replace_row(table, 31068, ppid=4, created=PS_CAP1_READ_FT + 1, cmd='python other.py')
        reused = without(reused, 10876)  # the stub went with it, so no Windows edge reaches 31068 either
        nodes = mt.tree(31624, reused, msys, PS_CAP1_READ_FT)
        self.assertEqual([n.pid for n in nodes], [31624, 17072, 23844, 2736, 17556])

    def test_winpid_reuse_a_winpid_now_held_by_another_image_is_refused(self):
        table, msys = cap1()
        reused = without(replace_row(table, 31068, ppid=4, name='notepad.exe', cmd='notepad.exe'), 10876)
        nodes = mt.tree(31624, reused, msys, PS_CAP1_READ_FT)
        self.assertEqual([n.pid for n in nodes], [31624, 17072, 23844, 2736, 17556])

    def test_a_process_that_names_itself_as_parent_has_no_parent(self):
        table, msys = cap1()
        nodes = mt.tree(0, table, msys, PS_CAP1_READ_FT)
        self.assertEqual([(n.pid, n.win_parent) for n in nodes], [(0, 'gone:0'), (4, 'live:0')])

    def test_the_tree_of_an_absent_root_fails_loudly(self):
        table, msys = cap1()
        with self.assertRaises(LookupError):
            mt.tree(30004, table, msys, PS_CAP1_READ_FT)

    def test_nearest_claude_ancestor_crosses_the_broken_windows_chain(self):
        table, msys = cap1()
        # 21824 -> 13308 -> 27076 (Windows parent 16920 gone) -> MSYS parent 789015 = 10188 -> 28904 -> 27048
        self.assertEqual(mt.nearest_ancestor(21824, 'claude.exe', table, msys, PS_CAP1_READ_FT), 27048)
        self.assertIsNone(mt.nearest_ancestor(3980, 'claude.exe', table, msys, PS_CAP1_READ_FT))
        self.assertIsNone(mt.nearest_ancestor(31068, 'claude.exe', table, msys, PS_CAP1_READ_FT))

    def test_format_tree_names_every_pid_with_its_link(self):
        table, msys = cap1()
        now = PS_CAP1_READ_FT
        lines = mt.format_tree(mt.tree(31624, table, msys, PS_CAP1_READ_FT), now)
        heads = [' '.join(line.split(' cmd=')[0].split()) for line in lines]
        self.assertEqual(heads, [
            '31624 bash.exe via=root msys=- winparent=gone:30004 age=20m',
            '17072 bash.exe via=win msys=778123 winparent=live:31624 age=20m',
            '23844 bash.exe via=win+msys msys=778131 winparent=live:17072 age=20m',
            '2736 timeout.exe via=msys msys=778133 winparent=gone:29136 age=20m',
            '10876 timeout.exe via=win msys=- winparent=live:2736 age=20m',
            '31068 python.exe via=win msys=778136 winparent=live:10876 age=20m',
            '28168 node.exe via=win msys=- winparent=live:31068 age=2m',
            '17556 conhost.exe via=win msys=- winparent=live:31624 age=20m',
        ])
        self.assertEqual(lines[3], '      2736 timeout.exe via=msys msys=778133 winparent=gone:29136 age=20m '
                                   'cmd="C:\\Program Files\\Git\\usr\\bin\\timeout.exe" 2400 python mut.py')

    def test_main_own_claude_prints_the_tree_of_the_nearest_claude(self):
        table, msys = cap1()
        printed, calls = [], []
        deps = mt.Deps(read_ps=lambda: calls.append('ps') or (msys, PS_CAP1_READ_FT),
                       read_table=lambda: calls.append('table') or table,
                       now_ft=lambda: PS_CAP1_READ_FT, own_pid=21824, out=printed.append)
        self.assertEqual(mt.main(['--own-claude'], deps), 0)
        self.assertEqual(calls, ['ps', 'table'])  # the identity check needs ps read first
        self.assertTrue(printed[0].startswith('tree of 27048 claude.exe (nearest claude.exe ancestor of pid 21824)'),
                        printed[0])
        listed = [int(line.split()[0]) for line in printed[1:-1]]
        self.assertEqual(listed, [27048] + OWN_SHELL_ORDER)
        self.assertEqual(printed[-1], 'summary: 9 processes; taskkill /T 27048 reaches 4; 5 more only through the '
                                      'MSYS join; kill roots 27048 27076')

    def test_main_with_a_pid_prints_that_tree(self):
        table, msys = cap1()
        printed = []
        deps = mt.Deps(read_ps=lambda: (msys, PS_CAP1_READ_FT), read_table=lambda: table,
                       now_ft=lambda: PS_CAP1_READ_FT, own_pid=21824, out=printed.append)
        self.assertEqual(mt.main(['31624'], deps), 0)
        self.assertEqual(printed[0], 'tree of 31624 bash.exe')
        self.assertEqual([int(line.split()[0]) for line in printed[1:-1]], GATE_ORDER)
        self.assertEqual(printed[-1], 'summary: 8 processes; taskkill /T 31624 reaches 4; 4 more only through the '
                                      'MSYS join; kill roots 31624 2736')

    # ---- PowerShell tool shells ----
    def test_powershell_tool_shell_as_the_harness_starts_it_behind_cmd(self):
        table = cap0()
        now = created(table, 23908) + 151 * TICKS_PER_MINUTE
        self.assertEqual(mt.powershell_tool_shell(23908, table, now), mt.PsToolShell(23908, 25808, 27048, 151.0))

    def test_powershell_tool_shell_as_a_direct_child_of_claude(self):
        table = without(replace_row(cap0(), 23908, ppid=27048), 25808)
        now = created(table, 23908) + 30 * TICKS_PER_MINUTE
        self.assertEqual(mt.powershell_tool_shell(23908, table, now), mt.PsToolShell(23908, 23908, 27048, 30.0))

    def test_not_a_powershell_tool_shell(self):
        table = cap0()
        now = created(table, 23908) + 200 * TICKS_PER_MINUTE
        plain = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand CgAk'
        cases = {
            'the cmd.exe wrapper itself': (table, 25808),
            'a Bash tool shell': (table, 14696),
            'absent pid': (table, 99999),
            'no launcher marker': (replace_row(table, 23908, cmd=plain), 23908),
            'claude.exe gone': (without(table, 27048), 23908),
            'cmd.exe gone': (without(table, 25808), 23908),
            'cmd.exe pid reused by a newer cmd.exe': (replace_row(table, 25808, created=created(table, 23908) + 1),
                                                      23908),
            'claude.exe pid reused by a newer claude.exe': (
                replace_row(table, 27048, created=created(table, 25808) + 1), 23908),
            'cmd.exe without the marker': (replace_row(table, 25808, cmd='cmd.exe /c powershell.exe'), 23908),
            'cmd.exe under something other than claude.exe': (replace_row(table, 27048, name='node.exe'), 23908),
            'pwsh.exe is not what the harness starts': (replace_row(table, 23908, name='pwsh.exe'), 23908),
        }
        for name, (t, pid) in cases.items():
            with self.subTest(name):
                self.assertIsNone(mt.powershell_tool_shell(pid, t, now))

    # ---- what shell-reaper's rules M1 to M3 read from this module ----
    def test_m1_a_powershell_tool_shell_past_390_minutes_keeps_its_identity(self):
        # shell-reaper keys its WATCH line on the powershell.exe pid and creation time, never on the cmd.exe top
        table = cap0()
        now = created(table, 23908) + 500 * TICKS_PER_MINUTE
        self.assertEqual(mt.powershell_tool_shell(23908, table, now), mt.PsToolShell(23908, 25808, 27048, 500.0))
        self.assertIsNone(mt.powershell_tool_shell(25808, table, now))

    def test_m2_an_exempt_command_reached_only_through_the_msys_join_is_in_the_tree(self):
        table, msys = cap1()
        table = replace_row(table, 21824, cmd='python.exe D:/work/x/queue-runner.py run')
        nodes = mt.tree(28904, table, msys, PS_CAP1_READ_FT)
        by = {n.pid: n for n in nodes}
        self.assertEqual(by[21824].cmd, 'python.exe D:/work/x/queue-runner.py run')
        self.assertEqual(by[27076].cmd, '"C:\\Program Files\\Git\\usr\\bin\\timeout.exe" 150 python -I capture.py cap1')
        # taskkill /T 28904 alone would end the shell without ever reading those two command lines
        self.assertEqual(sorted(mt.windows_subtree(28904, table)), [10188, 28904, 31228])
        self.assertEqual(sorted(by), sorted(OWN_SHELL_ORDER))

    def test_m3_kill_roots_name_every_execd_branch_of_one_shell(self):
        table, msys = cap1()
        # a second exec'd branch under bash 10188 (MSYS 789015): sleep 40010, its Windows parent 40009 gone
        extra_row = mt.MsysRow(789050, 789015, 789050, 40010, '/usr/bin/sleep')
        extra = mt.WinProc(40010, 40009, 'sleep.exe', PS_CAP1_READ_FT - 10, '"C:\\Program Files\\Git\\usr\\bin\\sleep.exe" 900')
        nodes = mt.tree(28904, table + [extra], msys + [extra_row], PS_CAP1_READ_FT)
        self.assertEqual([n.pid for n in nodes], OWN_SHELL_ORDER + [40010])
        self.assertEqual(mt.kill_roots(nodes, table + [extra]), [28904, 27076, 40010])
        self.assertEqual([(n.pid, n.created) for n in nodes if n.pid in (28904, 27076, 40010)],
                         [(28904, 134401668419735200), (27076, 134401668426260960), (40010, PS_CAP1_READ_FT - 10)])

    # ---- ps.exe from MSYS_PS_EXE ----
    def test_ps_exe_is_msys_ps_exe_when_set_else_the_git_default(self):
        saved = os.environ.pop('MSYS_PS_EXE', None)
        try:
            self.assertEqual(mt.ps_exe(), 'C:/Program Files/Git/usr/bin/ps.exe')
            os.environ['MSYS_PS_EXE'] = ''
            self.assertEqual(mt.ps_exe(), 'C:/Program Files/Git/usr/bin/ps.exe')
            os.environ['MSYS_PS_EXE'] = 'D:\\Tools\\Git\\usr\\bin\\ps.exe'
            self.assertEqual(mt.ps_exe(), 'D:\\Tools\\Git\\usr\\bin\\ps.exe')
        finally:
            os.environ.pop('MSYS_PS_EXE', None)
            if saved is not None:
                os.environ['MSYS_PS_EXE'] = saved

    def test_read_ps_live_starts_the_ps_msys_ps_exe_names_and_never_falls_back(self):
        # A fake ps.cmd that prints the CAP1 capture: the live reader runs it as it would run ps.exe -W and parses its
        # output. A name that is not a file fails loudly instead of reading Git's default ps.exe.
        folder = tempfile.mkdtemp(prefix='msys-tree-test-')
        saved = {name: os.environ.get(name) for name in ('MSYS_PS_EXE', 'MSYS_TREE_TEST')}
        try:
            with open(os.path.join(folder, 'ps-out.txt'), 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(PS_CAP1)
            fake = os.path.join(folder, 'ps.cmd')
            with open(fake, 'w', encoding='ascii', newline='\r\n') as fh:
                fh.write('@type "%~dp0ps-out.txt"\n')
            os.environ.pop('MSYS_TREE_TEST')
            mt.subprocess = subprocess
            os.environ['MSYS_PS_EXE'] = fake
            before = mt.now_filetime()
            rows, started = mt.read_ps_live()
            self.assertEqual(rows, mt.parse_ps(PS_CAP1))
            self.assertEqual(len(rows), 9)
            self.assertTrue(before <= started <= mt.now_filetime(), (before, started))
            os.environ['MSYS_PS_EXE'] = os.path.join(folder, 'no-such-ps.exe')
            with self.assertRaises(FileNotFoundError):
                mt.read_ps_live()
        finally:
            mt.subprocess = TRIPWIRE
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            shutil.rmtree(folder)

    # ---- the live readers never run under the tests ----
    def test_live_readers_refuse_under_the_test_env(self):
        for fn in (mt.read_ps_live, mt.read_table_live):
            with self.subTest(fn.__name__):
                with self.assertRaises(RuntimeError) as cm:
                    fn()
                self.assertIn('MSYS_TREE_TEST', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
