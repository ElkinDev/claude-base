"""ShellReaperTest: shell-reaper.py on a fake process table with a fake kill, and its install step.

Never the live table, never a real kill: SHELL_REAPER_TEST is set before the module loads (the live reader, the
re-read and taskkill refuse under it) and the module's subprocess is replaced by a tripwire that fails the test.
KIT_HOME names a temporary folder for the whole run, and each case keeps its log, stop file and config
(shell-reaper.json) in a temporary folder of its own, laid out as a kit home.

    python claude/tools/tests/test-shell-reaper.py

Two kinds of case start a process, never a live read or a kill: the CLI under python -I with the test flags set and a
stop file or a bad config in its kit home, and the install cases, which run install/shell-reaper.ps1 in a fresh
Windows PowerShell with KIT_SCHEDULER naming a stand-in JSON file and each scheduler cmdlet shadowed by a function
that records its name and throws.

Command lines are generic: user names, profile folders and project paths are replaced by placeholder ones; pids,
creation times and the shapes of the prefix and the PowerShell tool shell are as read live on 2026-11-26.
"""
import atexit
import dataclasses
import datetime
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

os.environ['SHELL_REAPER_TEST'] = '1'
os.environ['MSYS_TREE_TEST'] = '1'  # msys_tree's live readers refuse under it too
# A Deps default or a live run reads the kit home: KIT_HOME names a temporary folder, so none reaches the real one.
KIT_HOME_DIR = tempfile.mkdtemp(prefix='shell-reaper-kit-home-')
atexit.register(shutil.rmtree, KIT_HOME_DIR, True)
os.environ['KIT_HOME'] = KIT_HOME_DIR
HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), 'shell-reaper.py')
EXAMPLE = os.path.join(os.path.dirname(HERE), 'shell-reaper.example.json')
INSTALL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), 'install', 'shell-reaper.ps1')
_spec = importlib.util.spec_from_file_location('shell_reaper', TOOL)
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)


class Tripwire:
    def __getattr__(self, name):
        raise AssertionError(f'the test reached subprocess.{name}: a live table read or a real kill')


sr.subprocess = Tripwire()
if sr.MT is not None:
    sr.MT.subprocess = Tripwire()

DECOY = 'kit-shell-reaper-deadbeef'  # another kit home's task in the scheduler stand-in: no case may touch it
# Each scheduler cmdlet the install step could reach, shadowed by a function that records its name and throws: a
# function defined in powershell -Command shadows the cmdlet inside the script that command runs.
SCHEDULER_TRIPWIRES = ''.join(
    f"function {name} {{ Add-Content -LiteralPath $env:TRIPWIRE_LOG -Value '{name}'; throw 'tripwire: {name}' }}; "
    for name in ('Register-ScheduledTask', 'Unregister-ScheduledTask', 'Get-ScheduledTask'))


def ps_quote(text):
    """text as one PowerShell single-quoted string."""
    return "'" + text.replace("'", "''") + "'"


def console(data):
    """What powershell.exe printed: decoded with the console's OEM code page on Windows, LF line ends."""
    return data.decode('oem' if os.name == 'nt' else 'utf-8', 'replace').replace('\r\n', '\n')


def task_name(kit):
    """The task name install/shell-reaper.ps1 gives a kit home: the full path with backslashes, no trailing one, case
    folded, hashed as install/schedule.ps1 hashes it (Get-KitHomeKey)."""
    key = os.path.abspath(kit).replace('/', '\\').rstrip('\\').lower()
    return 'kit-shell-reaper-' + hashlib.sha256(key.encode('utf-8')).hexdigest()[:8]


NOW_DT = datetime.datetime(2026, 11, 26, 11, 0, 0, tzinfo=datetime.timezone.utc)
NOW = int((NOW_DT.timestamp() + sr.FILETIME_EPOCH_OFFSET) * 10_000_000)
TS = sr.filetime_local(NOW)

# The prefix a Claude Code Bash tool shell carries, as read live on 2026-11-26 06:15 (pid 31952), around a command.
# The user name and the profile folder are replaced by generic ones.
PREFIX = ('"C:\\Program Files\\Git\\bin\\bash.exe" -c "source /c/Users/user/.claude-profiles/two/shell-snapshots/'
          'snapshot-bash-1795665338163-nfuz08.sh 2>/dev/null || true && export TEMP=\'C:\\Users\\user\\AppData\\Local'
          '\\Temp\' TMP=\'C:\\Users\\user\\AppData\\Local\\Temp\' && \\builtin shopt -u extglob 2>/dev/null || true && '
          '{ \\builtin unalias -- \'which\'; \\builtin unset -f -- \'which\'; } >/dev/null 2>&1 || true && '
          '{ \\builtin unalias -- \'unsetenv\'; \\builtin unset -f -- \'unsetenv\'; } >/dev/null 2>&1 || true && '
          '{ eval \'')
SUFFIX = '\'; } && pwd -P >| /c/Users/user/AppData/Local/Temp/claude-7c81-cwd"'
INNER = '"C:\\Program Files\\Git\\bin\\..\\usr\\bin\\bash.exe" -c "source /c/Users/user/.claude/shell-snapshots/snapshot-bash-1-a.sh 2>/dev/null || true && { eval \''


def tool_cmd(command):
    return PREFIX + command + SUFFIX


def ago(minutes):
    return NOW - int(minutes * sr.TICKS_PER_MINUTE)


CLAUDE = sr.Proc(100, 50, 'claude.exe', ago(900), '"C:\\Users\\user\\...\\claude.exe" -r')
PANE = sr.Proc(50, 40, 'powershell.exe', ago(10000), 'powershell.exe -NoExit')
SELF = sr.Proc(9000, 8000, 'pythonw.exe', ago(0.1), 'pythonw.exe -I C:/Users/user/.claude/tools/shell-reaper.py')
BASE = [PANE, CLAUDE, SELF]
ROOTS = {'repo_roots': ['D:/work']}  # a config with one repo root, for class C


def shell(pid, minutes, command, ppid=100, name='bash.exe'):
    return sr.Proc(pid, ppid, name, ago(minutes), tool_cmd(command))


class Fakes:
    def __init__(self, table, reread=None, kill_result=None):
        self.table = list(table)
        self.reread = reread  # pid -> Proc or None, overriding the table on the re-read
        self.kill_result = kill_result  # (rc, text) the kill returns instead of one SUCCESS line
        self.killed, self.reads, self.rereads, self.printed, self.resume_reads = [], 0, [], [], 0
        self.msys, self.reread_calls, self.kill_calls, self.ps_reads = [], [], [], 0

    def read_table(self):
        self.reads += 1
        return list(self.table)

    def read_one(self, pid):
        self.rereads.append(pid)
        if self.reread is not None and pid in self.reread:
            return self.reread[pid]
        return next((p for p in self.table if p.pid == pid), None)

    def read_pids(self, pids):
        # one re-read of every root of a kill, as the live reader does with one CIM query
        self.reread_calls.append(list(pids))
        found = {pid: self.read_one(pid) for pid in pids}
        return {pid: p for pid, p in found.items() if p is not None}

    def kill_trees(self, pids):
        self.kill_calls.append(list(pids))
        self.killed.extend(pids)
        if self.kill_result is not None:
            return self.kill_result
        return 0, f'SUCCESS: The process with PID {pids[0]} (child process of PID 100) has been terminated.'

    def read_ps(self):
        self.ps_reads += 1
        return list(self.msys), NOW


class ShellReaperTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='shell-reaper-test-')
        # laid out as a kit home: logs/shell-reaper.log, shell-reaper.stop and shell-reaper.json
        self.log = os.path.join(self.dir, 'logs', 'shell-reaper.log')
        self.stop = os.path.join(self.dir, 'shell-reaper.stop')
        self.config = os.path.join(self.dir, 'shell-reaper.json')

    def tearDown(self):
        shutil.rmtree(self.dir)

    def go(self, table, argv=(), reread=None, resume=None, resume_error=None, msys=(), ps_error=None, kill=None):
        """One run on BASE plus table, with the config at self.config. resume: FILETIME the resume reader returns
        (None: no resume event); resume_error: an exception the resume reader raises instead. msys: the ps.exe -W
        rows (msys_tree.MsysRow); ps_error: an exception the ps reader raises instead. kill: (rc, text) taskkill
        returns instead of one SUCCESS line."""
        f = Fakes(BASE + list(table), reread, kill)
        f.msys = list(msys)

        def read_ps():
            f.ps_reads += 1
            if ps_error is not None:
                raise ps_error
            return list(f.msys), NOW

        def read_resume():
            f.resume_reads += 1
            if resume_error is not None:
                raise resume_error
            return resume
        deps = sr.Deps(read_table=f.read_table, read_pids=f.read_pids, kill_trees=f.kill_trees, now_ft=lambda: NOW,
                       read_resume=read_resume, read_ps=read_ps, own_pid=SELF.pid, log_path=self.log,
                       stop_path=self.stop, config_path=self.config, out=f.printed.append)
        f.rc = sr.main(list(argv), deps)
        return f

    def log_lines(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding='utf-8') as fh:
            return fh.read().splitlines()

    def write_config(self, data):
        """shell-reaper.json in this case's kit home: a str as it is, anything else as JSON."""
        with open(self.config, 'w', encoding='utf-8') as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))

    # ---- class A ----
    def test_class_a_plain_command_killed_only_past_45_minutes(self):
        f = self.go([shell(201, 44, 'ls -la'), shell(202, 45, 'ls -la'), shell(203, 46, 'ls -la')])
        self.assertEqual(f.killed, [203])
        self.assertEqual(f.rereads, [203])
        lines = self.log_lines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith(f'{TS} A KILL pid=203 age=46 parent=claude.exe:100 live rc=0 '), lines[0])

    def test_class_a_each_wait_token_holds_the_shell_to_150_minutes(self):
        for token in ('sleep', 'until', 'while', 'wait', 'cca-run'):
            with self.subTest(token=token):
                command = f'echo a {token} b'
                f = self.go([shell(301, 46, command), shell(302, 149, command), shell(303, 150, command),
                             shell(304, 151, command)])
                self.assertEqual(f.killed, [304])
                os.remove(self.log)

    def test_wait_token_matches_in_any_case(self):
        f = self.go([shell(311, 100, 'powershell -Command "Start-Sleep 30"'), shell(312, 100, 'gate-WAIT x')])
        self.assertEqual(f.killed, [])

    def test_wait_shell_with_sleep_at_100_minutes_is_kept(self):
        f = self.go([shell(401, 100, 'sleep 140; tail -3 D:/work/build/x/gate.log')])
        self.assertEqual(f.killed, [])
        self.assertEqual(self.log_lines(), [])

    def test_pipeline_call_at_46_minutes_kills_the_whole_call_once(self):
        command = 'cd D:/work/app/sketches && node shots.mjs --all --out D:/work/build/rs | head -2'
        top = shell(501, 46, command)
        inner = sr.Proc(502, 501, 'bash.exe', ago(46), INNER + command + SUFFIX)  # Git launcher's usr\bin child
        sub = sr.Proc(503, 502, 'bash.exe', ago(46), INNER + command + SUFFIX)    # the pipeline subshell
        node = sr.Proc(504, 503, 'node.exe', ago(46), 'node shots.mjs --all --out D:/work/build/rs')
        head = sr.Proc(505, 502, 'head.exe', ago(46), 'head -2')
        f = self.go([top, inner, sub, node, head])
        self.assertEqual(f.killed, [501])
        lines = self.log_lines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith('cmd=' + command), lines[0])

    def test_shell_whose_live_parent_is_not_claude_is_not_class_a(self):
        f = self.go([shell(511, 300, 'ls', ppid=50), sr.Proc(512, 100, 'bash.exe', ago(300), 'bash -c "ls"')])
        self.assertEqual(f.killed, [])

    # ---- class B ----
    def test_class_b_parent_gone_killed_past_45_minutes_never_at_10(self):
        f = self.go([shell(600, 11, 'ls', ppid=776), shell(601, 44, 'ls', ppid=777), shell(602, 46, 'ls', ppid=778)])
        self.assertEqual(f.killed, [602])
        lines = self.log_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn(' B KILL pid=602 age=46 parent=gone:778 rc=0 ', lines[0])

    def test_class_b_parent_pid_reused_by_a_newer_process(self):
        newer = sr.Proc(779, 40, 'svchost.exe', ago(5), 'svchost.exe -k x')
        f = self.go([newer, shell(603, 50, 'ls', ppid=779)])
        self.assertEqual(f.killed, [603])
        self.assertIn(' B KILL pid=603 age=50 parent=reused:779 ', self.log_lines()[0])

    def test_class_b_inner_shell_left_by_a_stopped_launcher_is_killed_once(self):
        command = 'npm test 2>&1 | tail -5'
        inner = sr.Proc(611, 31952, 'bash.exe', ago(50), INNER + command + SUFFIX)
        sub = sr.Proc(612, 611, 'bash.exe', ago(50), INNER + command + SUFFIX)
        f = self.go([inner, sub])
        self.assertEqual(f.killed, [611])

    # ---- class C ----
    # An orphan tree of a test run whose launcher ended: its top names the repo root D:/work.
    ORPHAN = '"C:\\Program Files\\Git\\bin\\bash.exe" D:/work/build/app-v3/tests/run-tests.sh'

    def test_class_c_orphan_reported_at_13_hours_and_never_killed(self):
        self.write_config(ROOTS)
        top = sr.Proc(27668, 15400, 'bash.exe', ago(13 * 60), self.ORPHAN)
        child = sr.Proc(27670, 27668, 'bash.exe', ago(13 * 60), '"C:\\Program Files\\Git\\usr\\bin\\bash.exe" D:/work/build/app-v3/tests/run-tests.sh')
        f = self.go([top, child])
        self.assertEqual(f.killed, [])
        self.assertEqual(f.rereads, [])
        self.assertEqual(self.log_lines(), [f'{TS} C WOULD-KILL pid=27668 age=780 parent=gone:15400 cmd={self.ORPHAN}'])

    def test_class_c_not_reported_at_11_hours(self):
        self.write_config(ROOTS)
        f = self.go([sr.Proc(27668, 15400, 'bash.exe', ago(11 * 60), self.ORPHAN)])
        self.assertEqual(self.log_lines(), [])
        self.assertEqual(f.killed, [])

    def test_class_c_each_name_and_each_root_reported(self):
        self.write_config({'repo_roots': ['D:/work', 'D:\\src']})
        roots = ('D:/work/x', 'D:\\work\\x', 'D:/src/x', 'D:\\src\\x', 'd:/WORK/x')
        for i, name in enumerate(('bash.exe', 'node.exe', 'python.exe', 'timeout.exe')):
            for j, root in enumerate(roots):
                with self.subTest(name=name, root=root):
                    pid = 700 + i * 10 + j
                    f = self.go([sr.Proc(pid, 1, name, ago(800), f'{name} {root}/run.sh')])
                    self.assertEqual([x.split(' pid=')[1].split(' ')[0] for x in self.log_lines()], [str(pid)])
                    self.assertEqual(f.killed, [])
                    os.remove(self.log)

    def test_class_c_root_must_end_at_a_separator_or_the_end_of_the_token(self):
        self.write_config({'repo_roots': ['D:/work', 'D:\\src\\']})
        rules = sr.Rules(roots=('D:/work', 'D:\\src\\'))
        for cmd, want in (('node D:/workshop/a.js', False), ('node D:\\workshop\\a.js', False),
                          ('node D:/src2/a.js', False), ('node D:/work', True), ('node "D:/work"', True),
                          ('node D:\\work\\a.js', True), ('node D:/src/a.js', True), ('node d:/WORK/a.js', True)):
            with self.subTest(cmd=cmd):
                self.assertEqual(sr.names_repo_root(cmd, rules), want)
        f = self.go([sr.Proc(791, 1, 'node.exe', ago(800), 'node D:/workshop/a.js'),
                     sr.Proc(792, 1, 'node.exe', ago(800), 'node D:/work/a.js')])
        self.assertEqual([x.split(' pid=')[1].split(' ')[0] for x in self.log_lines()], ['792'])
        self.assertEqual(f.killed, [])

    def test_class_c_exclusions(self):
        self.write_config(ROOTS)
        tokens = ('gradle', 'gradlew', 'java', 'lockrun', 'gate-detach')
        self.assertEqual(sr.EXEMPT_TOKENS, tokens)
        for token in tokens:
            with self.subTest(top_token=token):
                self.go([sr.Proc(801, 1, 'python.exe', ago(800), f'python D:/work/x/{token} --go')])
                self.assertEqual(self.log_lines(), [])
            with self.subTest(child_token=token):
                self.go([sr.Proc(802, 1, 'bash.exe', ago(800), 'bash D:/work/x/run.sh'),
                         sr.Proc(803, 802, 'cmd.exe', ago(800), f'cmd /c {token} build')])
                self.assertEqual(self.log_lines(), [])
        cases = {
            'pythonw in tree': [sr.Proc(811, 1, 'bash.exe', ago(800), 'bash D:/work/x/run.sh'),
                                sr.Proc(812, 811, 'pythonw.exe', ago(800), 'pythonw.exe C:/Users/user/a.py')],
            'pythonw top': [sr.Proc(813, 1, 'pythonw.exe', ago(800), 'pythonw.exe D:/work/x/a.py')],
            'no repo root': [sr.Proc(814, 1, 'node.exe', ago(800), 'node C:/Users/user/x.mjs')],
            'parent live': [sr.Proc(815, 50, 'node.exe', ago(800), 'node D:/work/x.mjs')],
            'other name': [sr.Proc(816, 1, 'cmd.exe', ago(800), 'cmd /c D:/work/x.cmd')],
        }
        for label, table in cases.items():
            with self.subTest(label=label):
                f = self.go(table)
                self.assertEqual(self.log_lines(), [])
                self.assertEqual(f.killed, [])
        self.go([sr.Proc(817, 1, 'python.exe', ago(800), 'python D:/work/x/other.py --go')])  # the control
        self.assertEqual([x.split(' ')[2:5] for x in self.log_lines()], [['C', 'WOULD-KILL', 'pid=817']])

    # ---- the kill itself ----
    def test_pid_reuse_refused_before_taskkill(self):
        old = shell(901, 60, 'ls')
        reused = sr.Proc(901, 4, 'notepad.exe', ago(1), 'notepad.exe')
        f = self.go([old], reread={901: reused})
        self.assertEqual(f.rereads, [901])
        self.assertEqual(f.killed, [])
        self.assertEqual(self.log_lines(),
                         [f'{TS} A REFUSED pid=901 age=60 parent=claude.exe:100 live pid reuse: created {old.created} '
                          f'now {reused.created} cmd=ls'])

    def test_process_gone_before_the_kill_is_logged_not_killed(self):
        f = self.go([shell(902, 60, 'ls')], reread={902: None})
        self.assertEqual(f.killed, [])
        self.assertIn(' A GONE pid=902 ', self.log_lines()[0])

    def test_failed_reread_is_logged_the_next_kill_still_runs_and_the_run_fails(self):
        f = Fakes(BASE + [shell(961, 60, 'ls'), shell(962, 60, 'pwd')])
        real_read_pids = f.read_pids

        def flaky(pids):
            if 961 in pids:
                raise RuntimeError('powershell exit 1: access denied')
            return real_read_pids(pids)
        deps = sr.Deps(read_table=f.read_table, read_pids=flaky, kill_trees=f.kill_trees, now_ft=lambda: NOW,
                       read_resume=lambda: None, read_ps=f.read_ps, own_pid=SELF.pid, log_path=self.log,
                       stop_path=self.stop, config_path=self.config, out=f.printed.append)
        self.assertEqual(sr.main([], deps), 1)
        self.assertEqual(f.killed, [962])
        lines = self.log_lines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], f'{TS} A ERROR pid=961 age=60 parent=claude.exe:100 live RuntimeError: powershell exit 1: '
                                   f'access denied cmd=ls')
        self.assertIn(' A KILL pid=962 ', lines[1])

    def test_log_line_carries_time_class_pid_age_parent_and_160_characters_after_the_prefix(self):
        command = 'cd D:/work/app && L=D:/work/build/x\n' + 'node run.mjs --long ' * 20
        f = self.go([shell(951, 47, command)])
        self.assertEqual(f.killed, [951])
        shown = command.replace('\n', '\\n')[:160]
        self.assertEqual(self.log_lines(),
                         [f'{TS} A KILL pid=951 age=47 parent=claude.exe:100 live rc=0 taskkill=SUCCESS: The process '
                          f'with PID 951 (child process of PID 100) has been terminated. cmd={shown}'])
        self.assertNotIn('snapshot', self.log_lines()[0])

    # ---- stop file, dry, own tree, failures ----
    def test_stop_file_writes_one_line_and_touches_nothing(self):
        os.makedirs(os.path.dirname(self.stop), exist_ok=True)
        open(self.stop, 'w').close()
        f = self.go([shell(1001, 60, 'ls'), shell(1002, 30, 'ls', ppid=777)])
        self.assertEqual(f.rc, 0)
        self.assertEqual(f.reads, 0)
        self.assertEqual(f.killed, [])
        self.assertEqual(self.log_lines(), [f'{TS} - STOP stop file {self.stop} present, nothing read or killed'])

    def test_dry_prints_every_action_kills_nothing_logs_nothing(self):
        self.write_config(ROOTS)
        f = self.go([shell(1101, 60, 'ls'), shell(1102, 60, 'ls', ppid=777),
                     sr.Proc(1103, 1, 'bash.exe', ago(800), self.ORPHAN)], argv=['--dry'])
        self.assertEqual(f.rc, 0)
        self.assertEqual(f.killed, [])
        self.assertEqual(f.rereads, [])
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual([x.split(' pid=')[0] for x in f.printed],
                         [f'DRY {TS} A WOULD-KILL', f'DRY {TS} B WOULD-KILL', f'DRY {TS} C WOULD-KILL'])

    def test_dry_with_stop_file_prints_the_stop_line_only(self):
        os.makedirs(os.path.dirname(self.stop), exist_ok=True)
        open(self.stop, 'w').close()
        f = self.go([shell(1201, 60, 'ls')], argv=['--dry'])
        self.assertEqual(f.printed, [f'DRY {TS} - STOP stop file {self.stop} present, nothing read or killed'])
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual(f.killed, [])

    def test_own_shell_and_its_ancestors_are_never_candidates(self):
        mine = shell(1301, 60, 'python shell-reaper.py --dry')
        me = sr.Proc(9000, 1301, 'python.exe', ago(0.1), 'python shell-reaper.py --dry')
        f = Fakes([PANE, CLAUDE, mine, me, shell(1302, 60, 'ls')])
        deps = sr.Deps(read_table=f.read_table, read_pids=f.read_pids, kill_trees=f.kill_trees, now_ft=lambda: NOW,
                       read_resume=lambda: None, read_ps=f.read_ps, own_pid=9000, log_path=self.log,
                       stop_path=self.stop, config_path=self.config, out=f.printed.append)
        self.assertEqual(sr.main([], deps), 0)
        self.assertEqual(f.killed, [1302])

    def test_own_pid_missing_from_the_table_fails_loudly(self):
        f = Fakes([PANE, CLAUDE, shell(1401, 60, 'ls')])
        deps = sr.Deps(read_table=f.read_table, read_pids=f.read_pids, kill_trees=f.kill_trees, now_ft=lambda: NOW,
                       read_resume=lambda: None, read_ps=f.read_ps, own_pid=9000, log_path=self.log,
                       stop_path=self.stop, config_path=self.config, out=f.printed.append)
        self.assertEqual(sr.main([], deps), 1)
        self.assertEqual(f.killed, [])
        self.assertEqual(self.log_lines(), [f'{TS} - ERROR RuntimeError: own pid 9000 is not in the process table; '
                                            f'refusing to judge it'])

    def test_table_read_failure_is_logged_and_exits_1(self):
        def broken():
            raise RuntimeError('powershell exit 1: Get-CimInstance failed')
        deps = sr.Deps(read_table=broken, read_pids=None, kill_trees=None, now_ft=lambda: NOW, read_resume=lambda: None,
                       read_ps=lambda: ([], NOW), own_pid=9000, log_path=self.log, stop_path=self.stop,
                       config_path=self.config, out=lambda s: None)
        self.assertEqual(sr.main([], deps), 1)
        self.assertEqual(self.log_lines(), [f'{TS} - ERROR RuntimeError: powershell exit 1: Get-CimInstance failed'])

    def test_live_reader_and_real_kill_refuse_under_the_test_flag(self):
        calls = (('read_table_live', sr.read_table_live), ('read_pids_live', lambda: sr.read_pids_live([1])),
                 ('kill_trees_live', lambda: sr.kill_trees_live([1])), ('read_resume_live', sr.read_resume_live),
                 ('read_ps_live', sr.read_ps_live), ('powershell', lambda: sr.powershell('Get-Date', 5)))
        for name, call in calls:
            with self.subTest(call=name):
                with self.assertRaisesRegex(RuntimeError, f'^{name} refused: SHELL_REAPER_TEST is set'):
                    call()

    def test_parse_table_reads_the_powershell_shapes(self):
        one = '\ufeff[{"p":31952,"q":32960,"n":"bash.exe","c":134401656216024540,"l":"bash -c x"}]\r\n'
        self.assertEqual(sr.parse_table(one), [sr.Proc(31952, 32960, 'bash.exe', 134401656216024540, 'bash -c x')])
        single = '{"p":4,"q":0,"n":"System","c":0,"l":""}'
        self.assertEqual(sr.parse_table(single), [sr.Proc(4, 0, 'System', 0, '')])
        self.assertEqual(sr.parse_table('[]'), [])
        with self.assertRaisesRegex(RuntimeError, 'empty process table output'):
            sr.parse_table('  ')
        with self.assertRaisesRegex(RuntimeError, 'not a list'):
            sr.parse_table('"text"')

    def test_default_out_is_safe_without_a_console(self):
        # pythonw has no stdout: the default out (print) must return quietly when sys.stdout is None
        out = sr.Deps(read_table=None, read_pids=None, kill_trees=None, now_ft=None, read_resume=None, read_ps=None,
                      own_pid=0).out
        buf = io.StringIO()
        with redirect_stdout(buf):
            out('x')
        self.assertEqual(buf.getvalue(), 'x\n')
        saved = sys.stdout
        sys.stdout = None
        try:
            out('no console')
        finally:
            sys.stdout = saved

    def test_parse_resume_reads_the_powershell_shapes(self):
        # 134394791940506738 is the newest resume event read live on 2026-11-26 06:40 (2026-11-18 07:39:54 local)
        self.assertEqual(sr.parse_resume('\ufeff134394791940506738\r\n'), 134394791940506738)
        self.assertIsNone(sr.parse_resume('none\r\n'))
        for bad in ('', '  ', 'Get-WinEvent : access denied', '-5', '0', '1.5'):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(RuntimeError, 'not a FILETIME'):
                    sr.parse_resume(bad)

    # ---- rules R1 to R6 ----
    def seed_log(self, lines):
        os.makedirs(os.path.dirname(self.log), exist_ok=True)
        with open(self.log, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(''.join(x + '\n' for x in lines))

    def test_r1_exempt_gradle_shell_at_500_minutes_kept_and_watched_once(self):
        command = 'cd D:/work/app && ./gradlew :app:testDebugUnitTest --tests *SyncTest 2>&1 | tail -20'
        table = [shell(1501, 500, command)]
        expected = f'{TS} A WATCH pid=1501 age=500 parent=claude.exe:100 live created={ago(500)} cmd={command}'
        f = self.go(table, argv=['--dry'])
        self.assertEqual(f.printed, ['DRY ' + expected])
        self.assertFalse(os.path.exists(self.log))
        f = self.go(table)
        self.assertEqual((f.rc, f.killed, f.rereads), (0, [], []))
        self.assertEqual(self.log_lines(), [expected])
        f = self.go(table)  # the next tick, same day: kept, no second line
        self.assertEqual(f.killed, [])
        self.assertEqual(self.log_lines(), [expected])
        f = self.go(table, argv=['--dry'])
        self.assertEqual(f.printed, [f'DRY {TS} - nothing to do (4 processes read)'])

    def test_r1_watch_starts_past_390_minutes_in_class_a_and_b(self):
        command = 'java -jar D:/work/x/render-server.jar --job 3'
        f = self.go([shell(1503, 390, command), shell(1504, 391, command), shell(1505, 389, command, ppid=777),
                     shell(1506, 391, command, ppid=778)])
        self.assertEqual(f.killed, [])
        self.assertEqual(self.log_lines(),
                         [f'{TS} A WATCH pid=1504 age=391 parent=claude.exe:100 live created={ago(391)} cmd={command}',
                          f'{TS} B WATCH pid=1506 age=391 parent=gone:778 created={ago(391)} cmd={command}'])

    def test_r1_each_exempt_token_word_bounded_in_any_case_keeps_a_and_b_shells(self):
        tokens = ('gradle', 'gradlew', 'java', 'lockrun', 'gate-detach')
        for token in tokens + ('GRADLEW', 'Java', 'GATE-DETACH'):
            with self.subTest(token=token):
                command = f'cd D:/work/x && {token} --go'
                f = self.go([shell(1511, 46, command), shell(1512, 389, command), shell(1513, 46, command, ppid=777),
                             shell(1514, 389, command, ppid=777)])
                self.assertEqual(f.killed, [])
                self.assertEqual(self.log_lines(), [])

    def test_r1_token_inside_a_longer_word_does_not_exempt(self):
        for command in ('node D:/work/x/javascript-lint.mjs', 'cat D:/work/x/my_gradlew.bak', 'ls gradle2',
                        'type D:/work/x/Java_backup', 'cat D:/work/x/gradlew2.log'):
            with self.subTest(command=command):
                f = self.go([shell(1521, 46, command)])
                self.assertEqual(f.killed, [1521])
                os.remove(self.log)

    def test_r1_watch_once_per_pid_and_creation_time_per_day(self):
        command = 'java -jar D:/work/x/queue.jar run'
        old = shell(1531, 600, command)
        yesterday = sr.filetime_local(NOW - 24 * 60 * sr.TICKS_PER_MINUTE)
        seed = [f'{yesterday} A WATCH pid=1531 age=400 parent=claude.exe:100 live created={old.created} cmd={command}',
                f'{TS} A WATCH pid=1531 age=400 parent=claude.exe:100 live created={old.created - 1} cmd=echo '
                f'created={old.created} x',
                f'{TS} A WATCH pid=1599 age=400 parent=claude.exe:100 live created=5 cmd=echo pid=1531 '
                f'created={old.created} ',
                f'{TS} A KILL pid=1531 age=46 parent=claude.exe:100 live rc=0 created={old.created} cmd=ls']
        self.seed_log(seed)
        new_line = f'{TS} A WATCH pid=1531 age=600 parent=claude.exe:100 live created={old.created} cmd={command}'
        self.go([old])
        self.assertEqual(self.log_lines(), seed + [new_line])
        self.go([old])
        self.assertEqual(self.log_lines(), seed + [new_line])

    def test_r2_class_b_forked_wait_subshell_at_30_minutes_kept(self):
        # a forked subshell carries its top's whole command line, marker included; its top has returned
        command = '( until [ -f D:/work/build/x/done ]; do sleep 30; done; echo ok ) > D:/work/x/w.log 2>&1 &'
        f = self.go([shell(1541, 30, command, ppid=31999)])
        self.assertEqual((f.killed, self.log_lines()), ([], []))
        f = self.go([shell(1542, 149, command, ppid=31998), shell(1543, 151, command, ppid=31997)])
        self.assertEqual(f.killed, [1543])
        self.assertIn(' B KILL pid=1543 age=151 parent=gone:31997 rc=0 ', self.log_lines()[0])

    def test_r3_lock_released_holds_to_390_minutes_in_class_a_and_b(self):
        for command in ('grep -c LOCK_RELEASED D:/work/build/x/gate.log',
                        'until grep -q LOCK_RELEASED D:/work/build/x/gate.log; do sleep 60; done',
                        'bash D:/work/x/mylockrun.sh', 'bash D:/work/x/my-gate-detacher.sh'):
            for ppid, cls in ((100, 'A'), (777, 'B')):
                with self.subTest(command=command, cls=cls):
                    f = self.go([shell(1551, 200, command, ppid), shell(1552, 390, command, ppid),
                                 shell(1553, 391, command, ppid), shell(1554, 400, command, ppid)])
                    self.assertEqual(f.killed, [1553, 1554])
                    lines = self.log_lines()
                    self.assertEqual(len(lines), 2)
                    self.assertIn(f' {cls} KILL pid=1554 age=400 ', lines[1])
                    os.remove(self.log)

    def test_r4_creation_zero_or_null_skipped_with_one_line(self):
        self.write_config(ROOTS)
        self.assertEqual(sr.parse_table('[{"p":5,"q":1,"n":"bash.exe","c":null,"l":"x"}]')[0].created, 0)
        zero = sr.Proc(1561, 100, 'bash.exe', 0, tool_cmd('ls -la'))
        orphan_zero = sr.Proc(1562, 1, 'node.exe', 0, 'node D:/work/x/run.mjs')
        system = sr.Proc(4, 0, 'System', 0, '')
        f = self.go([system, zero, orphan_zero])
        self.assertEqual((f.rc, f.killed, f.rereads), (0, [], []))
        self.assertEqual(self.log_lines(), [f'{TS} - SKIP pid=1561 ppid=100 creation time reads 0 or null cmd=ls -la',
                                            f'{TS} - SKIP pid=1562 ppid=1 creation time reads 0 or null '
                                            f'cmd=node D:/work/x/run.mjs'])

    def test_r5_shell_created_before_a_resume_20_minutes_ago_is_20_minutes_old(self):
        self.write_config(ROOTS)
        old = shell(1571, 300, 'ls -la')
        f = self.go([old], resume=ago(20))
        self.assertEqual((f.killed, self.log_lines(), f.resume_reads), ([], [], 1))
        f = self.go([old], resume=ago(46))
        self.assertEqual(f.killed, [1571])
        self.assertIn(' A KILL pid=1571 age=46 ', self.log_lines()[0])
        os.remove(self.log)
        f = self.go([shell(1572, 50, 'ls -la')], resume=ago(300))  # created after the resume: its own age counts
        self.assertEqual(f.killed, [1572])
        self.assertIn(' A KILL pid=1572 age=50 ', self.log_lines()[0])
        os.remove(self.log)
        f = self.go([old])  # no resume event in the log: the creation time counts
        self.assertEqual(f.killed, [1571])
        self.assertIn(' A KILL pid=1571 age=300 ', self.log_lines()[0])
        os.remove(self.log)
        orphan = sr.Proc(27668, 15400, 'bash.exe', ago(13 * 60), self.ORPHAN)
        self.go([orphan], resume=ago(60))  # class C ages from the resume too
        self.assertEqual(self.log_lines(), [])
        self.go([orphan], resume=ago(721))
        self.assertEqual(self.log_lines(), [f'{TS} C WOULD-KILL pid=27668 age=721 parent=gone:15400 cmd={self.ORPHAN}'])

    def test_r5_failed_resume_read_logs_one_line_uses_wall_age_and_kills_nothing(self):
        self.write_config(ROOTS)
        exempt_cmd = 'java -jar D:/work/x/queue.jar run'
        table = [shell(1581, 60, 'ls'), shell(1582, 60, 'pwd', ppid=777), shell(1583, 500, exempt_cmd),
                 sr.Proc(27668, 15400, 'bash.exe', ago(13 * 60), self.ORPHAN)]
        error = RuntimeError('powershell exit 1: Get-WinEvent failed')
        f = self.go(table, resume_error=error)
        self.assertEqual((f.rc, f.killed, f.rereads, f.resume_reads), (1, [], [], 1))
        self.assertEqual(self.log_lines(),
                         [f'{TS} - RESUME-ERROR RuntimeError: powershell exit 1: Get-WinEvent failed; wall age used, '
                          f'no kill this run (2 held)',
                          f'{TS} A WATCH pid=1583 age=500 parent=claude.exe:100 live created={ago(500)} '
                          f'cmd={exempt_cmd}',
                          f'{TS} C WOULD-KILL pid=27668 age=780 parent=gone:15400 cmd={self.ORPHAN}'])
        os.remove(self.log)
        f = self.go([shell(1584, 60, 'ls')], argv=['--dry'], resume_error=error)
        self.assertEqual(f.printed, [f'DRY {TS} - RESUME-ERROR RuntimeError: powershell exit 1: Get-WinEvent failed; '
                                     f'wall age used, no kill this run (1 held)'])
        self.assertFalse(os.path.exists(self.log))

    def test_r5_failed_resume_read_still_logged_when_the_run_cannot_judge(self):
        f = Fakes([PANE, CLAUDE, shell(1591, 60, 'ls')])

        def broken_resume():
            raise RuntimeError('powershell exit 1: Get-WinEvent failed')
        deps = sr.Deps(read_table=f.read_table, read_pids=f.read_pids, kill_trees=f.kill_trees, now_ft=lambda: NOW,
                       read_resume=broken_resume, read_ps=f.read_ps, own_pid=9000, log_path=self.log,
                       stop_path=self.stop, config_path=self.config, out=f.printed.append)
        self.assertEqual(sr.main([], deps), 1)
        self.assertEqual(f.killed, [])
        self.assertEqual(self.log_lines(),
                         [f'{TS} - RESUME-ERROR RuntimeError: powershell exit 1: Get-WinEvent failed; wall age used, '
                          f'no kill this run',
                          f'{TS} - ERROR RuntimeError: own pid 9000 is not in the process table; refusing to judge it'])

    def test_r6_a_run_with_7_candidates_kills_5(self):
        table = [shell(1600 + i, 60, 'ls') for i in range(7)]
        f = self.go(table)
        self.assertEqual(f.killed, [1600, 1601, 1602, 1603, 1604])
        self.assertEqual(f.rereads, [1600, 1601, 1602, 1603, 1604])
        lines = self.log_lines()
        self.assertEqual([x.split(' pid=')[0] for x in lines], [f'{TS} A KILL'] * 5 + [f'{TS} A DEFER'] * 2)
        defer = '{} A DEFER pid={} age=60 parent=claude.exe:100 live cap 5 kills a run cmd=ls'
        self.assertEqual(lines[5:], [defer.format(TS, 1605), defer.format(TS, 1606)])
        os.remove(self.log)
        f = self.go(table, argv=['--dry'])
        self.assertEqual([x.split(' pid=')[0] for x in f.printed],
                         [f'DRY {TS} A WOULD-KILL'] * 5 + [f'DRY {TS} A DEFER'] * 2)
        self.assertEqual((f.killed, f.rereads), ([], []))

    def test_r6_a_candidate_gone_at_the_reread_counts_against_the_cap(self):
        table = [shell(1610 + i, 60, 'ls') for i in range(7)]
        f = self.go(table, reread={1610: None})
        self.assertEqual(f.rereads, [1610, 1611, 1612, 1613, 1614])
        self.assertEqual(f.killed, [1611, 1612, 1613, 1614])

    # ---- the MSYS join, M1 to M4 ----
    PS_MARK = ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$__claudeCodeScript = '
               '$env:CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT; $env:CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT = $null; '
               'Invoke-Expression -Command $__claudeCodeScript"')

    def ps_tool_shell(self, minutes, wrapped=True):
        """A Claude Code PowerShell tool shell as the harness starts it (cap0, 06:36): claude.exe -> cmd.exe -> ps."""
        psh_cmd = '"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"  ' + self.PS_MARK
        if not wrapped:
            return [sr.Proc(602, 100, 'powershell.exe', ago(minutes), psh_cmd)]
        wrapper = sr.Proc(601, 100, 'cmd.exe', ago(minutes) - 1,
                          'C:\\WINDOWS\\System32\\cmd.exe /d /s /c ""C:\\WINDOWS\\System32\\chcp.com" 65001 >nul & '
                          '"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" ' + self.PS_MARK + '"')
        return [wrapper, sr.Proc(602, 601, 'powershell.exe', ago(minutes), psh_cmd)]

    def test_m1_powershell_tool_shell_at_500_minutes_watched_never_killed(self):
        table = self.ps_tool_shell(500)
        expected = (f'{TS} A WATCH pid=602 age=500 parent=claude.exe:100 live via cmd.exe:601 created={ago(500)} '
                    f'cmd={sr.PS_SHOWN}')
        f = self.go(table)
        self.assertEqual((f.rc, f.killed, f.rereads), (0, [], []))
        self.assertEqual(self.log_lines(), [expected])
        f = self.go(table)  # the next tick, same day: still kept, no second line
        self.assertEqual((f.killed, self.log_lines()), ([], [expected]))
        os.remove(self.log)
        f = self.go(self.ps_tool_shell(899))  # no age kills it (CLAUDE, its parent, is 900 minutes old)
        self.assertEqual((f.killed, f.rereads, len(self.log_lines())), ([], [], 1))
        self.assertIn(' A WATCH pid=602 age=899 ', self.log_lines()[0])

    def test_m1_powershell_watch_starts_past_390_minutes_in_both_shapes(self):
        for wrapped, parent in ((True, 'claude.exe:100 live via cmd.exe:601'), (False, 'claude.exe:100 live')):
            with self.subTest(wrapped=wrapped):
                for minutes in (46, 151, 389, 390):
                    f = self.go(self.ps_tool_shell(minutes, wrapped))
                    self.assertEqual((f.killed, self.log_lines()), ([], []), minutes)
                f = self.go(self.ps_tool_shell(391, wrapped), argv=['--dry'])
                self.assertEqual(f.printed, [f'DRY {TS} A WATCH pid=602 age=391 parent={parent} created={ago(391)} '
                                             f'cmd={sr.PS_SHOWN}'])
                self.assertEqual(f.killed, [])

    def exec_chain(self, minutes, command='ls', exec_cmd='"C:\\Program Files\\Git\\usr\\bin\\timeout.exe" 900 node t.mjs',
                   exec_name='timeout.exe', ppid=100, msys_image='/usr/bin/timeout'):
        """An exec chain: tool shell 501, its usr/bin bash 502 (MSYS 7001), and 503, which MSYS exec'd from
        a fork of 502: its Windows parent 9999 is gone, only the MSYS parent links it. Then 503's Windows child 504."""
        top = shell(501, minutes, command, ppid)
        inner = sr.Proc(502, 501, 'bash.exe', ago(minutes), INNER + command + SUFFIX)
        execd = sr.Proc(503, 9999, exec_name, ago(minutes) + 5, exec_cmd)
        child = sr.Proc(504, 503, 'node.exe', ago(minutes) + 6, 'node t.mjs')
        rows = [sr.MT.MsysRow(7001, 1, 7001, 502, '/usr/bin/bash'), sr.MT.MsysRow(7002, 7001, 7001, 503, msys_image)]
        return [top, inner, execd, child], rows

    def test_m2_non_exempt_shell_whose_msys_descendant_runs_gradle_not_killed(self):
        command = 'cd D:/work/app && bash D:/work/x/run-tests.sh 2>&1 | tail -5'
        procs, rows = self.exec_chain(60, command, '"C:\\Program Files\\Git\\usr\\bin\\timeout.exe" 3000 ./gradlew '
                                                   ':app:testDebugUnitTest')
        procs[3] = sr.Proc(504, 503, 'java.exe', ago(60) + 6, '"C:\\Program Files\\Java\\jdk-21\\bin\\java.exe" '
                           '-classpath gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain :app:testDebugUnitTest')
        expected = (f'{TS} A WATCH pid=501 age=60 parent=claude.exe:100 live created={ago(60)} exempt=503:timeout.exe '
                    f'cmd={command}')
        f = self.go(procs, msys=rows)
        self.assertEqual((f.rc, f.killed, f.rereads), (0, [], []))
        self.assertEqual(self.log_lines(), [expected])
        f = self.go(procs, msys=rows)  # one WATCH line per pid and creation time per day
        self.assertEqual((f.killed, self.log_lines()), ([], [expected]))
        os.remove(self.log)
        # the same table without the MSYS rows: 503 joins no tree, so only the MSYS join kept the shell
        f = self.go(procs[:3], msys=())
        self.assertEqual(f.killed, [501])

    def test_m2_exempt_windows_child_keeps_the_tree(self):
        command = 'bash D:/work/x/build.sh'

        def tree(minutes, ppid=100):
            inner = sr.Proc(502, 501, 'bash.exe', ago(minutes), INNER + command + SUFFIX)
            java = sr.Proc(505, 502, 'java.exe', ago(minutes),
                           '"C:\\Program Files\\Java\\jdk-21\\bin\\java.exe" -jar x.jar')
            return [shell(501, minutes, command, ppid), inner, java]
        f = self.go(tree(50))
        self.assertEqual(f.killed, [])
        self.assertEqual(self.log_lines(), [f'{TS} A WATCH pid=501 age=50 parent=claude.exe:100 live created={ago(50)} '
                                            f'exempt=505:java.exe cmd={command}'])
        os.remove(self.log)
        f = self.go(tree(44))  # under its limit: no tree read, no line
        self.assertEqual((f.killed, self.log_lines()), ([], []))
        f = self.go(tree(50, ppid=777))  # class B too
        self.assertEqual(f.killed, [])
        self.assertIn(' B WATCH pid=501 age=50 parent=gone:777 ', self.log_lines()[0])

    def test_m2_descendant_gate_token_holds_the_tree_to_390_minutes(self):
        command = 'bash D:/work/x/run-gate.sh'
        for exec_cmd, name, image in (('"C:\\Program Files\\Git\\usr\\bin\\bash.exe" D:/work/x/mylockrun.sh', 'bash.exe',
                                       '/usr/bin/bash'),
                                      ('"C:\\Program Files\\Git\\usr\\bin\\grep.exe" -q LOCK_RELEASED gate.log',
                                       'grep.exe', '/usr/bin/grep')):
            for link in ('msys', 'win'):
                with self.subTest(exec_cmd=exec_cmd, link=link):
                    def table(minutes):
                        procs, rows = self.exec_chain(minutes, command, exec_cmd, name, msys_image=image)
                        if link == 'win':  # the same member as a Windows child of 502, no MSYS row needed
                            procs[2] = dataclasses.replace(procs[2], ppid=502)
                            rows = rows[:1]
                        return procs, rows
                    for minutes in (200, 390):
                        procs, rows = table(minutes)
                        f = self.go(procs, msys=rows)
                        self.assertEqual((f.killed, self.log_lines()), ([], []), minutes)
                    procs, rows = table(400)
                    f = self.go(procs, msys=rows)
                    roots = [501, 503] if link == 'msys' else [501]
                    self.assertEqual((f.kill_calls, f.reread_calls), ([roots], [roots]))
                    self.assertIn(f' A KILL pid=501 age=400 parent=claude.exe:100 live rc=0 ', self.log_lines()[0])
                    os.remove(self.log)

    def test_m2_word_bounded_lockrun_in_a_descendant_is_exempt_not_a_gate(self):
        exec_cmd = '"C:\\Program Files\\Git\\usr\\bin\\bash.exe" D:/work/scripts/lockrun.sh gate'
        procs, rows = self.exec_chain(400, 'bash D:/work/x/run-gate.sh', exec_cmd, 'bash.exe', msys_image='/usr/bin/bash')
        f = self.go(procs, msys=rows)
        self.assertEqual(f.killed, [])
        self.assertEqual(self.log_lines(), [f'{TS} A WATCH pid=501 age=400 parent=claude.exe:100 live created={ago(400)} '
                                            f'exempt=503:bash.exe cmd=bash D:/work/x/run-gate.sh'])

    def test_m2_a_candidate_whose_joined_tree_holds_this_process_is_never_killed(self):
        top = shell(701, 60, 'timeout 150 python shell-reaper.py --dry')
        me = sr.Proc(702, 9998, 'python.exe', ago(0.1), 'python shell-reaper.py --dry')  # exec'd: Windows parent gone
        rows = [sr.MT.MsysRow(7101, 1, 7101, 701, '/usr/bin/bash'),
                sr.MT.MsysRow(7102, 7101, 7101, 702, '/c/Users/user/AppData/Local/Programs/Python/Python312/python')]
        f = Fakes([PANE, CLAUDE, top, me])
        f.msys = rows
        deps = sr.Deps(read_table=f.read_table, read_pids=f.read_pids, kill_trees=f.kill_trees, now_ft=lambda: NOW,
                       read_resume=lambda: None, read_ps=f.read_ps, own_pid=702, log_path=self.log,
                       stop_path=self.stop, config_path=self.config, out=f.printed.append)
        self.assertEqual(sr.main([], deps), 0)
        self.assertEqual((f.killed, f.rereads, self.log_lines()), ([], [], []))
        f = self.go([top, me], msys=rows)  # the same shell judged by another process: killed with its exec'd root
        self.assertEqual(f.kill_calls, [[701, 702]])

    def test_m2_failed_ps_read_logs_one_line_and_kills_nothing(self):
        procs, rows = self.exec_chain(60)
        exempt = shell(1583, 500, 'java -jar D:/work/x/queue.jar run')
        error = RuntimeError('ps.exe exit 1: boom')
        f = self.go(procs + [exempt], msys=rows, ps_error=error)
        self.assertEqual((f.rc, f.killed, f.rereads, f.ps_reads, f.reads), (1, [], [], 1, 1))
        self.assertEqual(self.log_lines(),
                         [f'{TS} - MSYS-ERROR RuntimeError: ps.exe exit 1: boom; the joined tree cannot be read, '
                          f'no kill this run (1 held)',
                          f'{TS} A WATCH pid=1583 age=500 parent=claude.exe:100 live created={ago(500)} '
                          f'cmd=java -jar D:/work/x/queue.jar run'])
        os.remove(self.log)
        f = self.go(procs, msys=rows, ps_error=error, argv=['--dry'])
        self.assertEqual(f.printed, [f'DRY {TS} - MSYS-ERROR RuntimeError: ps.exe exit 1: boom; the joined tree cannot '
                                     f'be read, no kill this run (1 held)'])
        self.assertFalse(os.path.exists(self.log))
        f = self.go(procs, msys=rows, ps_error=error, resume_error=RuntimeError('Get-WinEvent failed'))
        self.assertEqual([x.split(' ')[3] for x in self.log_lines()], ['MSYS-ERROR', 'RESUME-ERROR'])
        self.assertEqual((f.rc, f.killed), (1, []))

    def test_m2_ps_is_read_before_the_table(self):
        order = []
        f = Fakes(BASE)
        deps = sr.Deps(read_table=lambda: order.append('table') or f.read_table(), read_pids=f.read_pids,
                       kill_trees=f.kill_trees, now_ft=lambda: NOW, read_resume=lambda: None,
                       read_ps=lambda: order.append('ps') or ([], NOW), own_pid=SELF.pid, log_path=self.log,
                       stop_path=self.stop, config_path=self.config, out=f.printed.append)
        self.assertEqual(sr.main([], deps), 0)
        self.assertEqual(order, ['ps', 'table'])

    def test_m3_the_kill_names_every_root_in_one_reread_and_one_taskkill(self):
        procs, rows = self.exec_chain(60)
        f = self.go(procs, msys=rows)
        self.assertEqual((f.reread_calls, f.kill_calls), ([[501, 503]], [[501, 503]]))
        self.assertEqual(self.log_lines(),
                         [f'{TS} A KILL pid=501 age=60 parent=claude.exe:100 live rc=0 roots=501,503 taskkill=SUCCESS: '
                          f'The process with PID 501 (child process of PID 100) has been terminated. cmd=ls'])
        f = self.go(procs, msys=rows, argv=['--dry'])
        self.assertEqual(f.printed, [f'DRY {TS} A WOULD-KILL pid=501 age=60 parent=claude.exe:100 live roots=501,503 '
                                     f'cmd=ls'])
        self.assertEqual((f.reread_calls, f.kill_calls), ([], []))

    def test_m3_a_root_that_vanished_between_read_and_kill_refused(self):
        procs, rows = self.exec_chain(60)
        head = f'{TS} A {{}} pid=501 age=60 parent=claude.exe:100 live roots=501,503 {{}} cmd=ls'
        f = self.go(procs, msys=rows, reread={503: None})
        self.assertEqual((f.reread_calls, f.kill_calls, f.rc), ([[501, 503]], [], 0))
        self.assertEqual(self.log_lines(), [head.format('REFUSED', 'root 503 gone before the kill')])
        os.remove(self.log)
        reused = sr.Proc(503, 4, 'timeout.exe', NOW, 'timeout.exe 5 other')
        f = self.go(procs, msys=rows, reread={503: reused})
        self.assertEqual(f.kill_calls, [])
        self.assertEqual(self.log_lines(), [head.format('REFUSED', f'root 503 pid reuse: created {procs[2].created} '
                                                                   f'now {NOW}')])
        os.remove(self.log)
        f = self.go(procs, msys=rows, reread={501: None})  # the shell itself ended: nothing is killed
        self.assertEqual(f.kill_calls, [])
        self.assertEqual(self.log_lines(), [head.format('GONE', 'ended before the kill')])

    def test_m4_missing_or_broken_msys_tree_logs_one_error_line_and_reads_nothing(self):
        self.write_config('[]')  # a config that refuses the run: msys_tree is checked first, so its ERROR shows
        for label, content in (('missing', None), ('broken', 'def broken(:\n')):
            with self.subTest(label):
                folder = tempfile.mkdtemp(prefix='shell-reaper-alone-', dir=self.dir)
                shutil.copy2(TOOL, os.path.join(folder, 'shell-reaper.py'))
                if content is not None:
                    with open(os.path.join(folder, 'msys_tree.py'), 'w', encoding='utf-8') as fh:
                        fh.write(content)
                spec = importlib.util.spec_from_file_location(f'shell_reaper_{label}',
                                                              os.path.join(folder, 'shell-reaper.py'))
                alone = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(alone)  # loading never raises: the failure waits for run()
                alone.subprocess = Tripwire()
                self.assertIsNone(alone.MT)
                f = Fakes(BASE + [shell(1701, 60, 'ls')])
                deps = alone.Deps(read_table=f.read_table, read_pids=f.read_pids, kill_trees=f.kill_trees,
                                  now_ft=lambda: NOW, read_resume=lambda: None, read_ps=f.read_ps, own_pid=SELF.pid,
                                  log_path=self.log, stop_path=self.stop, config_path=self.config,
                                  out=f.printed.append)
                self.assertEqual(alone.main([], deps), 1)
                self.assertEqual((f.reads, f.ps_reads, f.killed), (0, 0, []))
                lines = self.log_lines()
                self.assertEqual(len(lines), 1)
                kind = 'FileNotFoundError' if content is None else 'SyntaxError'
                self.assertTrue(lines[0].startswith(f'{TS} - ERROR msys_tree.py not loaded ({kind}: '), lines[0])
                self.assertTrue(lines[0].endswith('); shell-reaper.py and msys_tree.py are copied together, nothing '
                                                  'read or killed'), lines[0])
                os.remove(self.log)
                f = Fakes(BASE)
                deps.read_table, deps.out = f.read_table, f.printed.append
                self.assertEqual(alone.main(['--dry'], deps), 1)
                self.assertEqual((len(f.printed), f.reads, os.path.exists(self.log)), (1, 0, False))
                self.assertTrue(f.printed[0].startswith(f'DRY {TS} - ERROR msys_tree.py not loaded ({kind}: '))

    # ---- the config (shell-reaper.json in the kit home) ----
    def test_config_error_logs_one_line_reads_and_kills_nothing_and_exits_1(self):
        table = [shell(1801, 60, 'ls'), sr.Proc(1802, 1, 'node.exe', ago(800), 'node D:/work/x/run.mjs')]
        lists = '"{}" must be a list of non-empty strings'
        cases = (('[]', 'ValueError', 'a JSON object is expected, not a list'),
                 ('"D:/work"', 'ValueError', 'a JSON object is expected, not a str'),
                 ('{"exempt": ["x"], "roots": ["D:/work"]}', 'ValueError',
                  'unknown key roots (known: exempt, repo_roots)'),
                 ('{"exempt": "queue-runner.py"}', 'ValueError', lists.format('exempt')),
                 ('{"exempt": ["queue-runner.py", " "]}', 'ValueError', lists.format('exempt')),
                 ('{"repo_roots": [7]}', 'ValueError', lists.format('repo_roots')),
                 ('{"repo_roots": null}', 'ValueError', lists.format('repo_roots')),
                 ('{"repo_roots": ["D:/work",', 'JSONDecodeError', None))
        for text, kind, message in cases:
            with self.subTest(config=text):
                self.write_config(text)
                f = self.go(table)
                self.assertEqual((f.rc, f.reads, f.ps_reads, f.resume_reads, f.killed), (1, 0, 0, 0, []))
                lines = self.log_lines()
                self.assertEqual(len(lines), 1, lines)
                if message is None:
                    self.assertTrue(lines[0].startswith(f'{TS} - CONFIG-ERROR {kind}: '), lines[0])
                    self.assertTrue(lines[0].endswith('; nothing read or killed'), lines[0])
                else:
                    detail = f'{self.config}: {message}'[:160]
                    self.assertEqual(lines[0], f'{TS} - CONFIG-ERROR {kind}: {detail}; nothing read or killed')
                os.remove(self.log)
        self.write_config('[]')
        f = self.go(table, argv=['--dry'])
        detail = f'{self.config}: a JSON object is expected, not a list'[:160]
        self.assertEqual(f.printed, [f'DRY {TS} - CONFIG-ERROR ValueError: {detail}; nothing read or killed'])
        self.assertEqual((f.rc, f.reads, os.path.exists(self.log)), (1, 0, False))

    def test_config_notes_are_ignored_and_the_stop_file_comes_before_the_config(self):
        self.write_config({'_help': 'a note', '_why': ['any', 'value', 3]})
        f = self.go([shell(1811, 46, 'ls')])
        self.assertEqual((f.rc, f.killed), (0, [1811]))
        os.remove(self.log)
        os.makedirs(os.path.dirname(self.stop), exist_ok=True)
        open(self.stop, 'w').close()
        self.write_config('[]')
        f = self.go([shell(1812, 60, 'ls')])
        self.assertEqual((f.rc, f.reads, f.killed), (0, 0, []))
        self.assertEqual(self.log_lines(), [f'{TS} - STOP stop file {self.stop} present, nothing read or killed'])

    def test_the_example_config_loads_as_the_shape_it_documents(self):
        with open(EXAMPLE, encoding='utf-8') as fh:
            data = json.load(fh)
        self.assertEqual(sorted(data), ['_help', 'exempt', 'repo_roots'])
        rules = sr.load_rules(EXAMPLE)
        self.assertEqual((rules.exempt, rules.roots),
                         (('gradle', 'gradlew', 'java', 'lockrun', 'gate-detach', 'queue-runner.py', 'render-server'),
                          ('D:/work', 'D:\\src')))
        # copied into the kit home with a BOM, as Windows PowerShell 5.1 writes UTF-8: the run reads it as it is
        with open(self.config, 'w', encoding='utf-8-sig') as fh:
            json.dump(data, fh)
        f = self.go([sr.Proc(1821, 1, 'node.exe', ago(800), 'node D:\\src\\x\\run.mjs'),
                     shell(1822, 46, 'python D:/work/x/queue-runner.py run')])
        self.assertEqual((f.rc, f.killed), (0, []))
        self.assertEqual([x.split(' ')[2:5] for x in self.log_lines()], [['C', 'WOULD-KILL', 'pid=1821']])
        self.write_config({'exempt': ['java', 'queue-runner.py', 'java']})  # a kit token named again is kept once
        self.assertEqual(sr.load_rules(self.config).exempt,
                         ('gradle', 'gradlew', 'java', 'lockrun', 'gate-detach', 'queue-runner.py'))

    def test_class_c_reports_nothing_without_a_repo_root_the_config_names(self):
        table = [sr.Proc(1831, 1, 'node.exe', ago(800), 'node D:/work/x/run.mjs'),
                 sr.Proc(1832, 1, 'bash.exe', ago(800), 'bash D:\\work\\x\\run.sh')]
        f = self.go(table)  # no config: the kit lists no repo root
        self.assertEqual((f.rc, f.killed, self.log_lines()), (0, [], []))
        for config in ({}, {'repo_roots': []}, {'repo_roots': ['D:/other']}, {'exempt': ['queue-runner.py']}):
            with self.subTest(config=config):
                self.write_config(config)
                f = self.go(table)
                self.assertEqual((f.rc, f.killed, self.log_lines()), (0, [], []))
        self.write_config(ROOTS)  # the control: the same table under a root the config names
        f = self.go(table)
        self.assertEqual((f.rc, f.killed), (0, []))
        self.assertEqual([x.split(' ')[2:5] for x in self.log_lines()],
                         [['C', 'WOULD-KILL', 'pid=1831'], ['C', 'WOULD-KILL', 'pid=1832']])

    def test_config_exempt_tokens_are_word_bounded_in_any_case_and_substrings_in_class_c(self):
        self.assertEqual(sr.EXEMPT_TOKENS, ('gradle', 'gradlew', 'java', 'lockrun', 'gate-detach'))
        self.write_config({'exempt': ['queue-runner.py', 'render-server'], 'repo_roots': ['D:/work']})
        for command in ('python D:/work/x/queue-runner.py run', 'QUEUE-RUNNER.PY --once',
                        'D:/work/x/Render-Server -p 9'):
            with self.subTest(kept=command):
                f = self.go([shell(1841, 46, command), shell(1842, 389, command, ppid=777)])
                self.assertEqual((f.killed, self.log_lines()), ([], []))
        for command in ('python D:/work/x/myqueue-runner.py run', 'cat D:/work/x/render-server2.log'):
            with self.subTest(killed=command):
                f = self.go([shell(1843, 46, command)])
                self.assertEqual(f.killed, [1843])
                os.remove(self.log)
        # class C takes a config token as a substring in any case, on any process of the tree
        for command in ('python D:/work/x/queue-runner.py run', 'python D:/work/x/MyQueue-Runner.pyc'):
            with self.subTest(orphan=command):
                self.go([sr.Proc(1844, 1, 'python.exe', ago(800), command)])
                self.assertEqual(self.log_lines(), [])
        self.go([sr.Proc(1845, 1, 'python.exe', ago(800), 'python D:/work/x/other.py')])
        self.assertEqual([x.split(' ')[2:5] for x in self.log_lines()], [['C', 'WOULD-KILL', 'pid=1845']])

    # ---- the kit home ----
    def test_kit_home_is_kit_home_else_dot_claude_in_the_profile(self):
        saved = {name: os.environ.get(name) for name in ('KIT_HOME', 'USERPROFILE', 'HOME')}
        try:
            os.environ['KIT_HOME'] = 'D:\\kits\\one'
            self.assertEqual(sr.kit_home(), 'D:\\kits\\one')
            del os.environ['KIT_HOME']
            os.environ['USERPROFILE'] = os.environ['HOME'] = self.dir
            self.assertEqual(sr.kit_home(), os.path.join(self.dir, '.claude'))
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_home_names_the_log_stop_file_and_config_of_a_live_run(self):
        home = os.path.join(self.dir, 'kit home')
        os.makedirs(home)
        log, stop, config = (os.path.join(home, 'logs', 'shell-reaper.log'), os.path.join(home, 'shell-reaper.stop'),
                             os.path.join(home, 'shell-reaper.json'))
        live = sr.live_deps(home)
        self.assertEqual((live.log_path, live.stop_path, live.config_path), (log, stop, config))
        with open(config, 'w', encoding='utf-8') as fh:
            fh.write('[]')
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = sr.main(['--home', home])  # the live deps: the config refuses the run before any live read
        self.assertEqual(rc, 1)
        with open(log, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        detail = f'{config}: a JSON object is expected, not a list'[:160]
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].endswith(f' - CONFIG-ERROR ValueError: {detail}; nothing read or killed'), lines[0])
        self.assertEqual(buf.getvalue().splitlines(), lines)

    def test_the_cli_in_isolated_mode_loads_msys_tree_and_takes_home_over_kit_home(self):
        # python -I, as the task runs it (pythonw -I): this folder is off sys.path, so msys_tree loads by path or not
        # at all. A bad config is reported only after msys_tree loaded (M4 comes first), so CONFIG-ERROR proves it.
        homes = [os.path.join(self.dir, name) for name in ('by-env', 'by-flag')]
        for home in homes:
            os.makedirs(home)
            open(os.path.join(home, 'shell-reaper.stop'), 'w').close()
        env = dict(os.environ, KIT_HOME=homes[0], SHELL_REAPER_TEST='1', MSYS_TREE_TEST='1')
        for argv, home, other in (([], homes[0], homes[1]), (['--home', homes[1]], homes[1], homes[0])):
            with self.subTest(argv=argv):
                done = subprocess.run([sys.executable, '-I', TOOL] + argv, env=env, capture_output=True, timeout=60)
                self.assertEqual(done.returncode, 0, done.stderr)
                stop, log = os.path.join(home, 'shell-reaper.stop'), os.path.join(home, 'logs', 'shell-reaper.log')
                with open(log, encoding='utf-8') as fh:
                    lines = fh.read().splitlines()
                self.assertEqual(len(lines), 1, lines)
                self.assertTrue(lines[0].endswith(f' - STOP stop file {stop} present, nothing read or killed'), lines[0])
                self.assertFalse(os.path.exists(os.path.join(other, 'logs', 'shell-reaper.log')))
                os.remove(log)
        os.remove(os.path.join(homes[1], 'shell-reaper.stop'))
        with open(os.path.join(homes[1], 'shell-reaper.json'), 'w', encoding='utf-8') as fh:
            fh.write('{"exempt": "x"}')
        done = subprocess.run([sys.executable, '-I', TOOL, '--home', homes[1]], env=env, capture_output=True,
                              timeout=60)
        self.assertEqual(done.returncode, 1, done.stderr)
        with open(os.path.join(homes[1], 'logs', 'shell-reaper.log'), encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn(' - CONFIG-ERROR ValueError: ', lines[0])

    # ---- what taskkill printed ----
    def test_kill_line_carries_the_last_taskkill_line_when_rc_is_not_0(self):
        procs, rows = self.exec_chain(60)
        ok = 'SUCCESS: The process with PID 501 (child process of PID 100) has been terminated.'
        gone = 'ERROR: The process "503" not found.'
        head = f'{TS} A KILL pid=501 age=60 parent=claude.exe:100 live rc={{}} roots=501,503 taskkill={{}} cmd=ls'
        for rc, text, shown in ((128, f'{ok}\r\n{gone}', f'{ok} last={gone}'),  # some ended, one did not
                                (0, f'{ok}\r\n{ok}', ok),                      # rc 0: the first line only
                                (1, 'ERROR: Access is denied.', 'ERROR: Access is denied.'),  # one line: no last=
                                (128, '', '(no output)')):
            with self.subTest(rc=rc, text=text):
                f = self.go(procs, msys=rows, kill=(rc, text))
                self.assertEqual(f.kill_calls, [[501, 503]])
                self.assertEqual(self.log_lines(), [head.format(rc, shown)])
                self.assertEqual(f.rc, 0 if rc == 0 else 1)  # a failed taskkill fails the run
                os.remove(self.log)

    def test_duplicate_exempt_tokens_are_kept_once(self):
        self.write_config({'exempt': ['runner', 'runner', 'gradle']})
        rules = sr.load_rules(self.config)
        self.assertEqual(rules.exempt, sr.EXEMPT_TOKENS + ('runner',))

    def test_console_text_decodes_with_the_oem_code_page(self):
        try:
            import ctypes
            oemcp = ctypes.windll.kernel32.GetOEMCP()
        except (ImportError, AttributeError):
            self.skipTest('GetOEMCP is Windows only')
        expected = b'\x82'.decode(f'cp{oemcp}', 'replace')
        if oemcp == 65001 or expected == '\ufffd':
            self.skipTest(f'0x82 alone is no character in code page {oemcp}, so no decode can be told apart')
        self.assertEqual(sr.console_text(b'\x82'), expected)
        self.assertEqual(sr.console_text(b'ERROR: \x82 x'), f'ERROR: {expected} x')

    # ---- the install step (install/shell-reaper.ps1) ----
    def make_kit(self):
        """A kit home with both tools, a scheduler stand-in that holds another kit home's task, and a fake pythonw."""
        if os.name != 'nt':
            self.skipTest('the install step is Windows PowerShell')
        self.kit = os.path.join(self.dir, 'kit')
        self.tools = os.path.join(self.kit, 'tools')
        os.makedirs(self.tools)
        for name in ('shell-reaper.py', 'msys_tree.py'):
            shutil.copy2(os.path.join(os.path.dirname(TOOL), name), self.tools)
        self.scheduler = os.path.join(self.dir, 'scheduler.json')
        with open(self.scheduler, 'w', encoding='utf-8') as fh:
            json.dump({DECOY: {'Execute': 'another kit home'}}, fh)
        self.pythonw = os.path.join(self.dir, 'py', 'pythonw.exe')
        os.makedirs(os.path.dirname(self.pythonw))
        open(self.pythonw, 'wb').close()
        self.tripwire = os.path.join(self.dir, 'tripwire.log')

    def install(self, *args, scheduler=True, shadow='', config=True):
        """(exit code, stdout, stderr) of install/shell-reaper.ps1 with args, run in a fresh powershell under the
        scheduler tripwires; scheduler=False leaves KIT_SCHEDULER unset, so the script reaches for the scheduler."""
        env = dict(os.environ, KIT_HOME=self.kit, TRIPWIRE_LOG=self.tripwire)
        if config:  # the install step wants a config in the kit home unless -Force
            open(os.path.join(self.kit, 'shell-reaper.json'), 'a', encoding='utf-8').close()
        elif os.path.exists(os.path.join(self.kit, 'shell-reaper.json')):
            os.remove(os.path.join(self.kit, 'shell-reaper.json'))
        env.pop('KIT_SCHEDULER', None)
        if scheduler:
            env['KIT_SCHEDULER'] = self.scheduler
        call = ' '.join(a if a.startswith('-') else ps_quote(a) for a in (INSTALL,) + args)
        done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                               '-Command', SCHEDULER_TRIPWIRES + shadow + '& ' + call + '; exit $LASTEXITCODE'],
                              env=env, capture_output=True, timeout=120)
        return done.returncode, console(done.stdout), console(done.stderr)

    def read_scheduler(self):
        with open(self.scheduler, encoding='utf-8-sig') as fh:
            return json.load(fh)

    def tripped(self):
        if not os.path.exists(self.tripwire):
            return []
        with open(self.tripwire, encoding='utf-8-sig') as fh:
            return fh.read().split()

    def test_install_shadows_reach_the_script_and_the_tripwires_answer_for_the_scheduler(self):
        self.make_kit()
        # a harmless Get-Date shadow defined in -Command shows up in the task's start: a function there shadows the
        # cmdlet inside the script, after it dot-sourced lib.ps1 and schedule.ps1
        rc, out, err = self.install('-Python', self.pythonw, shadow="function Get-Date { [datetime]'2030-01-02 00:00' }; ")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_scheduler()[task_name(self.kit)]['StartBoundary'], '2030-01-02T00:00:00')
        self.assertEqual(self.tripped(), [])
        # with no stand-in the removal asks the scheduler for the task: the tripwire answers and the run fails
        rc, out, err = self.install('-Remove', scheduler=False)
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.tripped(), ['Get-ScheduledTask'])

    def test_install_without_a_config_refuses_with_the_warning_and_force_registers(self):
        self.make_kit()
        before = self.read_scheduler()
        rc, out, err = self.install('-Python', self.pythonw, config=False)
        self.assertEqual(rc, 2, out)
        for word in ('shell-reaper.json', 'exempt', '45', '150', '390', '-Force', 'Nothing was registered'):
            self.assertIn(word, err)
        self.assertEqual((self.read_scheduler(), self.tripped()), (before, []))
        rc, out, err = self.install('-Python', self.pythonw, '-Force', config=False)
        self.assertEqual(rc, 0, err)
        self.assertIn(task_name(self.kit), self.read_scheduler())

    def test_install_registers_one_task_per_kit_home_with_a_15_minute_cap(self):
        self.make_kit()
        rc, out, err = self.install('-Python', self.pythonw)
        self.assertEqual(rc, 0, err)
        name = task_name(self.kit)
        tasks = self.read_scheduler()
        self.assertEqual(list(tasks), [DECOY, name])
        self.assertEqual(tasks[DECOY], {'Execute': 'another kit home'})
        arguments = f'-I "{os.path.join(self.tools, "shell-reaper.py")}" --home "{self.kit}"'
        expected = {'Execute': self.pythonw, 'Arguments': arguments, 'WorkingDirectory': self.tools, 'DaysInterval': 1,
                    'RepetitionInterval': 'PT10M', 'RepetitionDuration': 'P1D', 'StartWhenAvailable': False,
                    'ExecutionTimeLimit': 'PT15M', 'MultipleInstances': 'IgnoreNew', 'LogonType': 'Interactive',
                    'RunLevel': 'Limited'}
        self.assertEqual({k: tasks[name][k] for k in expected}, expected)
        self.assertRegex(tasks[name]['StartBoundary'], r'^\d{4}-\d{2}-\d{2}T00:00:00$')
        self.assertEqual(out.splitlines()[:2],
                         [f'shell-reaper  registered the task {name}: every 10 minutes, as this user with no '
                          f'elevation, it runs', f'              {self.pythonw} {arguments}'])
        self.assertEqual(self.tripped(), [])

    def test_install_refuses_without_both_tools_or_with_a_python_that_is_not_a_file(self):
        self.make_kit()
        before = self.read_scheduler()
        for missing in ('msys_tree.py', 'shell-reaper.py'):
            with self.subTest(missing=missing):
                path = os.path.join(self.tools, missing)
                os.rename(path, path + '.away')
                try:
                    rc, out, err = self.install('-Python', self.pythonw)
                finally:
                    os.rename(path + '.away', path)
                self.assertEqual(rc, 2, out)
                self.assertEqual(err.strip(), f'refused: no {path}. shell-reaper.py and msys_tree.py are copied '
                                              f'together into {self.tools} by install.ps1; run it first. Nothing was '
                                              f'registered.')
                self.assertEqual(self.read_scheduler(), before)
        nowhere = os.path.join(self.dir, 'py', 'no-pythonw.exe')
        rc, out, err = self.install('-Python', nowhere)
        self.assertEqual((rc, err.strip()), (2, f'refused: -Python names {nowhere}, which is not a file. Nothing was '
                                                f'registered.'))
        self.assertEqual((self.read_scheduler(), self.tripped()), (before, []))

    def test_install_dry_run_changes_nothing_and_remove_takes_only_its_own_task(self):
        self.make_kit()
        before = self.read_scheduler()
        name = task_name(self.kit)
        rc, out, err = self.install('-Python', self.pythonw, '-DryRun')
        self.assertEqual((rc, self.read_scheduler()), (0, before), err)
        script = os.path.join(self.tools, 'shell-reaper.py')
        self.assertEqual(out.splitlines(), [f'shell-reaper  would register the task {name}, every 10 minutes, as this '
                                            f'user with no elevation:',
                                            f'              {self.pythonw} -I "{script}" --home "{self.kit}"'])
        rc, out, err = self.install('-Remove')
        self.assertEqual((rc, out.strip()), (0, f'shell-reaper  no task {name} for {self.kit} to remove'), err)
        self.assertEqual(self.install('-Python', self.pythonw)[0], 0)
        rc, out, err = self.install('-Remove', '-DryRun')
        self.assertEqual((rc, out.strip()), (0, f'shell-reaper  would remove the task {name}'), err)
        self.assertEqual(list(self.read_scheduler()), [DECOY, name])
        rc, out, err = self.install('-Remove', '-Python', self.pythonw)
        self.assertEqual((rc, err.strip()), (2, 'refused: -Remove takes no -Python. Nothing was changed.'))
        rc, out, err = self.install('-Remove')
        self.assertEqual((rc, out.strip()), (0, f'shell-reaper  removed the task {name}; its log and any stop file or '
                                                f'config stay in {self.kit}'), err)
        self.assertEqual((self.read_scheduler(), self.tripped()), (before, []))


if __name__ == '__main__':
    unittest.main()
