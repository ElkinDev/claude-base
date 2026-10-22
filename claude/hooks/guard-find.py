"""PreToolUse hook for the shell tools: deny a search that walks a whole drive or the root.

On 2026-10-21 two subagent calls ran `find / -name three.core.js ... | head -3` and `find / -maxdepth 6 ...`. The Bash
calls ended, `head` had its three lines, and the two finds kept walking every drive for hours with no parent
(13,522 and 4,536 CPU-seconds) until they were stopped by pid. A search with a known folder costs seconds; a search of
a drive root on this machine reaches models, builds and caches of hundreds of gigabytes.

One rule, and nothing else is ever denied: a recursive walk whose start is `/`, a drive root (`/c`, `C:/`, `C:\\`,
`/mnt/c`, `/cygdrive/c`) or `/*` is denied unless its depth is capped at MAX_DEPTH or less. The walks read are
`find`, `du`, `ls -R`, `grep -r` and `rg` from either shell, and PowerShell's `Get-ChildItem -Recurse` with its
aliases gci, ls and dir. A pattern or a quoted string that holds `/` is not a start path. Each denial is one line in GUARD_FIND_LOG (default guard-find.log beside this file), which is the
number this hook moves: whole-drive walks a week, baseline 2 on 2026-10-21.

Any internal failure exits 0 without output, so the hook can never block a call.
"""
import datetime as dt
import json
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.environ.get("GUARD_FIND_LOG") or os.path.join(HERE, "guard-find.log")
SHELL_TOOLS = ("Bash", "PowerShell")
SEPARATORS = ";&|(){}"  # a subshell, a group, a substitution or a script block starts a new command
MAX_DEPTH = 2
WRAPPERS = {"sudo", "timeout", "nice", "nohup", "env", "command", "exec", "time", "builtin"}
ROOT = re.compile(r"^(/|/\*|/[a-z]/?\*?|/mnt/[a-z]/?\*?|/cygdrive/[a-z]/?\*?|[a-z]:/?\*?)$")
PS_WALKERS = {"get-childitem", "gci", "ls", "dir"}


def deny(reason):
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                  "permissionDecisionReason": reason}}
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


def is_root(token):
    t = token.strip("'\"").replace("\\", "/").lower()
    while len(t) > 1 and t.endswith("//"):
        t = t[:-1]
    return bool(ROOT.match(t))


def commands(line, posix):
    """Split a command line into simple commands (lists of words), on ; & | ( ) { } and newlines."""
    lex = shlex.shlex(line, posix=posix, punctuation_chars=SEPARATORS)
    lex.whitespace_split = True
    lex.commenters = "#" if posix else ""
    current, out = [], []
    for tok in lex:
        if tok and set(tok) <= set(SEPARATORS):
            if current:
                out.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        out.append(current)
    return out


def heredoc_free(line):
    """The command line without heredoc bodies, so a line of text written into a file is never read as a command."""
    out, end = [], None
    for raw in line.split("\n"):
        if end is not None:
            if raw.strip() == end:
                end = None
            continue
        m = re.search(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", raw)  # not a here-string <<<
        if m:
            end = m.group(2)
        out.append(raw)
    return "\n".join(out)


def strip_wrappers(words):
    i = 0
    while i < len(words):
        w = words[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
            i += 1
        elif w in WRAPPERS:
            i += 1
            while i < len(words) and (words[i].startswith("-") or re.match(r"^[0-9.]+[smhd]?$", words[i])):
                i += 1
        else:
            break
    return words[i:]


def number_after(words, names):
    for i, w in enumerate(words):
        low = w.lower()
        for name in names:
            if low == name and i + 1 < len(words):
                try:
                    return int(words[i + 1].strip("'\""))
                except ValueError:
                    return None
            if low.startswith(name + "="):
                try:
                    return int(low.split("=", 1)[1])
                except ValueError:
                    return None
    return None


def capped(depth):
    return depth is not None and depth <= MAX_DEPTH


def operands(args):
    """grep and rg take the pattern as their first operand unless -e or -f names it; the rest are paths."""
    ops = [a for a in args if not a.startswith("-")]
    named = any(a in ("-e", "-f", "--regexp", "--file") or a.startswith(("--regexp=", "--file=")) for a in args)
    return ops if named else ops[1:]


def walks_root(words, posix=True):
    """The reason a simple command walks a root, or None. posix is False for PowerShell, where ls and dir are
    Get-ChildItem."""
    words = list(words)
    while words:  # a subshell, a group or a command substitution: (find / ...), { find / ...; }, $(find / ...)
        if words[0] in ("(", "{", "$("):
            words.pop(0)
        elif words[0].startswith("$(") or words[0][:1] in ("(", "{"):
            words[0] = words[0][2:] if words[0].startswith("$(") else words[0][1:]
        else:
            break
    words = strip_wrappers(words)
    if not words:
        return None
    name = os.path.basename(words[0].strip("'\"").replace("\\", "/")).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    args = words[1:]
    if name in ("bash", "sh", "zsh", "dash", "pwsh", "powershell"):  # a command handed to another shell as a string
        low = [a.lower() for a in args]
        for flag in ("-c", "-command"):
            if flag in low and low.index(flag) + 1 < len(args):
                inner_posix = name not in ("pwsh", "powershell")
                try:
                    inner = commands(heredoc_free(args[low.index(flag) + 1]), inner_posix)
                except ValueError:
                    return None
                return next((r for r in (walks_root(w, inner_posix) for w in inner) if r), None)
        return None
    if name == "find":
        starts = []
        i = 0  # find's own leading options come before the start paths: -L, -H, -P, -O<level>, -D <debugopts>
        while i < len(args) and (args[i] in ("-L", "-H", "-P", "-D") or re.match(r"^-O\d*$", args[i])):
            i += 2 if args[i] == "-D" else 1
        for a in args[i:]:
            if a.startswith("-") or a in ("(", "!", "\\("):
                break
            starts.append(a)
        if any(is_root(s) for s in starts) and not capped(number_after(args, ("-maxdepth",))):
            return "find over %s" % next(s for s in starts if is_root(s))
        return None
    if name == "du" and any(is_root(a) for a in args if not a.startswith("-")):
        return "du over a root"
    if posix and name in ("ls", "dir"):
        recursive = any(a.startswith("-") and not a.startswith("--") and "R" in a for a in args) or "--recursive" in args
        if recursive and any(is_root(a) for a in args if not a.startswith("-")):
            return "ls -R over a root"
        return None
    if name in ("grep", "egrep", "fgrep"):
        recursive = any(a in ("--recursive", "--dereference-recursive") or
                        (a.startswith("-") and not a.startswith("--") and ("r" in a or "R" in a)) for a in args)
        if recursive and any(is_root(a) for a in operands(args)):
            return "grep -r over a root"
        return None
    if name == "rg":
        if any(is_root(a) for a in operands(args)) and not capped(number_after(args, ("--max-depth", "-d"))):
            return "rg over a root"
        return None
    if not posix and name in PS_WALKERS:
        low = [a.lower() for a in args]
        if not any(a == "-r" or a.startswith("-rec") for a in low) or capped(number_after(args, ("-depth",))):
            return None
        valued = ("-filter", "-include", "-exclude", "-depth", "-path", "-literalpath", "-attributes")
        paths = []
        for i, a in enumerate(args):
            if low[i] in ("-path", "-literalpath") and i + 1 < len(args):
                paths.append(args[i + 1])
            elif not a.startswith("-") and (i == 0 or low[i - 1] not in valued):
                paths.append(a)
        if any(is_root(p) for p in paths):
            return "%s -Recurse over a root" % words[0]
    return None


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError:
        return 0
    if payload.get("tool_name") not in SHELL_TOOLS:
        return 0
    line = (payload.get("tool_input") or {}).get("command") or ""
    posix = payload.get("tool_name") == "Bash"
    try:
        simple = commands((heredoc_free(line) if posix else line).replace("\n", ";"), posix)
    except ValueError:  # an unclosed quote: the shell itself will refuse it
        return 0
    for words in simple:
        why = walks_root(words, posix)
        if why:
            try:
                with open(LOG, "a", encoding="utf-8") as h:
                    h.write("%s %s %s | %s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), payload.get("tool_name"),
                                                 why, " ".join(words)[:200]))
            except OSError:
                pass
            deny("Denied: %s walks a whole drive. On 2026-10-21 two finds over / outlived their Bash calls and ran for "
                 "hours with no parent. Search a folder you know instead (the project folder or the "
                 "tool's install folder), or cap the depth at %d or less (-maxdepth, --max-depth, -Depth)." % (why, MAX_DEPTH))
            return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # never block a call on an internal error
        sys.exit(0)
