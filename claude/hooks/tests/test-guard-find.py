"""Tests for claude/hooks/guard-find.py, the PreToolUse hook that denies a walk over a whole drive or the root.

    python claude/hooks/tests/test-guard-find.py

The hook runs the way the harness runs it: a subprocess with the PreToolUse payload on stdin. GUARD_FIND_LOG points
into a temporary folder whose name holds a space, so no real log is touched. Exit 0 when every case passes, 1
otherwise.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(HERE), "guard-find.py")
TMP = tempfile.mkdtemp(prefix="guard find ")
LOG = os.path.join(TMP, "guard-find.log")
results = []


def run(command, tool="Bash", raw=None):
    env = dict(os.environ, GUARD_FIND_LOG=LOG)
    data = raw if raw is not None else json.dumps({"tool_name": tool, "tool_input": {"command": command}})
    p = subprocess.run([sys.executable, HOOK], input=data, capture_output=True, text=True, env=env, timeout=30)
    return p.returncode, p.stdout.strip()


def denied(out):
    return bool(out) and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def case(name, ok, detail=""):
    results.append(ok)
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + detail))


DENY_BASH = [
    "find / -name three.core.js 2>/dev/null | head -3",
    "find / -maxdepth 6 -name three.module.js",
    "find /c -name '*.glb'",
    "find /c/ -type f -name x",
    "find C:/ -name x",
    "find 'C:\\' -name x",
    "find /mnt/c -name x",
    "find /cygdrive/d -name x",
    "find /* -name x",
    "cd /c/Repo && find / -name x",
    "timeout 60 find / -name x",
    "FOO=1 find /d -iname x",
    "du -sh /c",
    "ls -R /",
    "ls -laR /d",
    "grep -rn three.core / ",
    "grep -r --include=*.js three /c",
    "rg three.core /",
    "rg -l three C:/",
    "/usr/bin/find / -name x",
    "find.exe / -name x",
    "find -L / -name x",
    "find -H -O2 /c -name x",
    "find -D tree / -name x",
    "(find / -name x)",
    "{ find / -name x; }",
    "echo $(find / -name x)",
    "bash -c \"find / -name x\"",
    "sh -c 'cd /tmp && find /c -name x'",
    "pwsh -c \"Get-ChildItem C:\\ -Recurse\"",
    "cat <<'EOF' > notes.txt\nhello\nEOF\nfind / -name x",
]
ALLOW_BASH = [
    "find /c/Repo/Odin -name three.core.js",
    "find . -name x",
    "find / -maxdepth 1 -name x",
    "find / -maxdepth 2 -type d",
    "find /c -maxdepth 2 -name x",
    "find",
    "ls /",
    "ls -la /c",
    "ls -r /c",
    "du -sh /c/Repo/build-out",
    "grep -n / file.txt",
    "grep -rn / src",
    "grep -r -e foo src /c/Repo",
    "rg / src",
    "rg -d 2 foo /",
    "echo \"find / -name x\"",
    "git commit -m 'guard: deny find / -name'",
    "python -c \"print('find / x')\"",
    "cat <<'EOF'\nnot a heredoc with an apostrophe's quote\nEOF",
    "cat <<EOF > brief.md\nfind / -name x\nEOF",
    "cat <<-'END'\n\tgrep -r x /\n\tEND",
    "find -L /c/Repo -name x",
    "bash -c \"find . -name x\"",
    "find /c/Repo -name x -exec ls {} \\;",
    "awk '{print $1}' file | sort",
]
DENY_PS = [
    "Get-ChildItem -Path C:\\ -Recurse -Filter three.core.js",
    "Get-ChildItem C:/ -Recurse",
    "gci -r D:\\ -Include *.glb",
    "ls -Recurse -LiteralPath 'C:\\'",
    "dir C:\\ -rec",
]
DENY_PS += [
    "(Get-ChildItem C:\\ -Recurse).Count",
    "1..2 | ForEach-Object { Get-ChildItem -Path D:\\ -Recurse }",
    "Get-ChildItem C:\\ -Recurse:$true",
]
ALLOW_PS = [
    "Get-ChildItem -Path C:\\Repo\\Odin -Recurse -Filter three.core.js",
    "(Get-ChildItem C:\\Repo -Recurse).Count",
    "Get-ChildItem C:\\",
    "gci -r C:\\ -Depth 2",
    "Get-ChildItem -Recurse -Filter C:",
    "ls -r .",
]

for c in DENY_BASH:
    code, out = run(c)
    case("deny bash: " + c.replace("\n", " "), code == 0 and denied(out), out)
for c in ALLOW_BASH:
    code, out = run(c)
    case("allow bash: " + c.replace("\n", " "), code == 0 and out == "", out)
for c in DENY_PS:
    code, out = run(c, tool="PowerShell")
    case("deny ps: " + c, code == 0 and denied(out), out)
for c in ALLOW_PS:
    code, out = run(c, tool="PowerShell")
    case("allow ps: " + c, code == 0 and out == "", out)

code, out = run("find / -name x", tool="Read")
case("other tool untouched", code == 0 and out == "", out)
code, out = run(None, raw="not json")
case("bad payload exits 0 silent", code == 0 and out == "", out)
code, out = run("find / -name 'unclosed")
case("unclosed quote exits 0 silent", code == 0 and out == "", out)
code, out = run("find / -name x")
case("reason names the walk and the cap", "find over /" in out and "2 or less" in out, out)
with open(LOG, encoding="utf-8") as h:
    lines = h.read().splitlines()
case("each denial is one log line", len(lines) == len(DENY_BASH) + len(DENY_PS) + 1, str(len(lines)))
case("log line names the tool and the walk", "Bash find over /" in lines[0], lines[0] if lines else "")

shutil.rmtree(TMP, ignore_errors=True)
print("%d of %d passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
