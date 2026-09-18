"""PreToolUse hook for the Agent tool: two rules, and nothing else is ever denied.

Rule 1: a read-only agent delegates only to the bulk reader. Neither definition is meant to change a
file; this hook closes the one path this branch opens, delegation. Bash and PowerShell in the
reviewer's allowlist and the analyst's inherited tools are a separate open item. Listing the bulk
reader as a skill for the reviewer means putting Agent on its allowlist, and an allowlist has no
shape: with Agent on it, a reviewer can launch an implementer, which writes. The sentence in the
definition asking it not to is prose, and prose is not a boundary; this hook is. When the caller is
one of READ_ONLY_CALLERS, which the harness names in the payload as agent_type, and the requested
subagent_type is anything other than ALLOWED_TARGET, the call is denied with a reason naming both.
The rule is closed on the field it reads: for such a caller an absent or empty subagent_type is
denied too, because the harness resolves an omitted type to the general-purpose agent, which carries
every tool, so reading a missing field as "nothing to check" would hand a read-only agent the widest
agent there is. A main session carries no agent_type and is never touched by rule 1: a person driving
a session is not a read-only agent, and stopping them from delegating would break the kit for
everyone. Every other agent type passes untouched too.

Rule 2: every launch of an implementer from a main session is logged, one line in brief-launches.log
beside this file (time, type, the brief path found in the prompt, the three tier verdicts of that
brief, the deny tier in force, PASS or DENY). The tiers are the ones scripts/brief-check.py measures
(tier 1 a Report or Deliverable section, tier 2 a test or a golden named anywhere, tier 3 a test
class under a Pins, Rule or Law heading), duplicated here so that the hook imports nothing outside
its own folder. DENY_TIER is the highest tier a named brief must pass, and it is measured, not
chosen: 0 while the log is the only output, which is what the kit ships, then raised to the tier the
log supports (at least 80 percent of logged launches passing it, read with brief-check.py --from-log).
CLAUDE_BRIEF_DENY_TIER raises it without editing the file.

The brief graded is the first path under briefs/ that is not the template, so a prompt that cites a
TEMPLATE file before its brief is graded on the brief; paths are found in linear time (each "briefs/"
token extended left over path characters and right to the first .md, a quoted span allowed to carry
spaces, any /x/ drive mapped), never by a backtracking pattern over the prompt. A relative path is
resolved against CLAUDE_BRIEFS_ROOT, or the working directory when it is unset. A launch that names
no brief is logged NONE, one that mentions briefs without a parseable path is logged UNPARSED, an
unreadable brief UNREADABLE; none of the three is ever denied, which is the coverage gap the log
makes visible.

Any internal failure exits 0 without output, so the hook can never block a call by accident.
"""
import json
import os
import re
import sys
import time

# Rule 1: the two definitions that promise to change nothing, by the names their files carry in
# claude/agents/, and the single agent they may reach.
READ_ONLY_CALLERS = ("reviewer", "analyst")
ALLOWED_TARGET = "bulk-reader"

# Rule 2: the launches that are logged, the deny tier in force, the log, and the tier readers.
LOGGED_TARGETS = ("implementer", "implementer-light")
DENY_TIER = 0  # log only until the log says which tier the briefs of this machine already pass
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brief-launches.log")  # GUARD_LOG moves it
BRIEFS_AT = re.compile(r"briefs[/\\]")
PATH_CHAR = re.compile(r"[^\s`'\"()<>]")
RIGHT = re.compile(r"[^\s`'\"()<>]{0,240}?\.md\b")
QUOTES = "`'\""
TEST_ID = re.compile(r"\b[A-Z][A-Za-z0-9]*Test\b")
GOLDEN = re.compile(r"\bgolden", re.I)
HEADING = re.compile(r"^#{1,4}\s*(.+?)\s*$")
MISSING = {
    1: "no Report or Deliverable section",
    2: "no test identifier (ending in Test) and no golden named anywhere",
    3: "no test class named in a Pins, Rule or Law section or its heading",
}


def deny(reason):
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


def verdict(caller, target):
    """Rule 1: the denial reason for one delegation, or None when it may proceed."""
    if caller not in READ_ONLY_CALLERS or target == ALLOWED_TARGET:
        return None
    if not target:
        # An absent subagent_type is not an absent delegation: the harness resolves it to the
        # general-purpose agent, which carries every tool. For a caller on the list the field is
        # required, so its absence is denied like any other target that is not the reader.
        return (
            "A read-only agent delegates only to %s; name it as subagent_type." % ALLOWED_TARGET
        )
    return (
        "A read-only agent delegates only to %s; %s is not allowed from %s. "
        "Read what you need yourself, or ask the orchestrator to run that agent."
        % (ALLOWED_TARGET, target, caller)
    )


def deny_tier():
    """The tier in force, from the environment when it is set and readable."""
    raw = (os.environ.get("CLAUDE_BRIEF_DENY_TIER") or "").strip()
    if raw.isdigit():
        return int(raw)
    return DENY_TIER


def log_path():
    return os.environ.get("GUARD_LOG") or LOG_PATH


def briefs_root():
    return os.environ.get("CLAUDE_BRIEFS_ROOT") or os.getcwd()


def sections(text):
    out, head, buf = [], "", []
    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            out.append((head, "\n".join(buf)))
            head, buf = m.group(1), []
        else:
            buf.append(line)
    out.append((head, "\n".join(buf)))
    return out


def tiers(path):
    """(t1, t2, t3) for a brief, or None when it cannot be read. Same rules as brief-check.py."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    secs = sections(text)
    t1 = any(re.search(r"\b(report|deliverable)\b", h, re.I) for h, _ in secs)
    t2 = bool(TEST_ID.search(text) or GOLDEN.search(text))
    pin = [(h, b) for h, b in secs if re.search(r"\b(pins?|rule|law)\b", h, re.I)]
    t3 = any(TEST_ID.search(h) or TEST_ID.search(b) for h, b in pin)
    return t1, t2, t3


def brief_paths(prompt):
    """Every brief path the prompt names, in order, in linear time: each 'briefs/' token is extended
    left over path characters (at most 260) and right to the first .md; a span inside one pair of
    quotes or backticks on the same line may carry spaces."""
    out = []
    for m in BRIEFS_AT.finditer(prompt):
        s = m.start()
        lim = max(0, s - 260)
        i = s
        while i > lim and PATH_CHAR.match(prompt[i - 1]):
            i -= 1
        r = RIGHT.match(prompt, m.end())
        cand = prompt[i:r.end()] if r else None
        q = max(prompt.rfind(c, lim, s) for c in QUOTES)
        if q != -1 and "\n" not in prompt[q:s]:
            close = prompt.find(prompt[q], m.end(), m.end() + 260)
            if close != -1 and "\n" not in prompt[m.end():close] and prompt[q + 1:close].lower().endswith(".md"):
                cand = prompt[q + 1:close]
        if cand:
            out.append(cand)
    return out


def brief_in(prompt):
    """(path, marker): the first non-template brief path the prompt names, resolved against the briefs
    root when relative; (None, 'NONE') when it names none; (None, 'UNPARSED') when it mentions briefs
    without a path the finder reads."""
    prompt = prompt or ""
    paths = brief_paths(prompt)
    real = [p for p in paths if not os.path.basename(p.replace("\\", "/")).upper().startswith("TEMPLATE")]
    chosen = (real or paths or [None])[0]
    if chosen is None:
        return None, ("UNPARSED" if re.search(r"\bbriefs\b", prompt) else "NONE")
    path = chosen
    md = re.match(r"/([A-Za-z])/", path)
    if md:
        path = md.group(1).upper() + ":/" + path[3:]
    if not re.match(r"[A-Za-z]:[/\\]", path):
        path = os.path.join(briefs_root(), path)
    return path.replace("\\", "/"), None


def launch_check(target, prompt):
    """Rule 2: log the launch; return a denial reason only when the named brief fails the deny tier."""
    tier = deny_tier()
    path, marker = brief_in(prompt)
    t = tiers(path) if path else None
    if path is None:
        marks = marker
    elif t is None:
        marks = "UNREADABLE"
    else:
        marks = " ".join("T%d=%s" % (i + 1, "ok" if v else "miss") for i, v in enumerate(t))
    fails = [] if t is None else [i + 1 for i, v in enumerate(t) if not v and i + 1 <= tier]
    line = "%s %s %s %s deny_tier=%d %s\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), target, path or marker, marks, tier, "DENY" if fails else "PASS")
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    if fails:
        return "The brief %s fails the launch check at tier %d: %s. Repair it to the lane brief template and relaunch." % (
            path, tier, "; ".join(MISSING[i] for i in fails))
    return None


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return 0
    try:
        if data.get("tool_name") != "Agent":
            return 0
        caller = str(data.get("agent_type") or "").strip().lower()
        tool_input = data.get("tool_input") or {}
        target = str(tool_input.get("subagent_type") or "").strip().lower()
        reason = verdict(caller, target)
        if reason:
            deny(reason)
            return 0
        if not caller and target in LOGGED_TARGETS:
            reason = launch_check(target, str(tool_input.get("prompt") or ""))
            if reason:
                deny(reason)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
