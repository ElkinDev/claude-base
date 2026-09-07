"""PreToolUse hook for the Agent tool: a read-only agent delegates only to the bulk reader.

Neither definition is meant to change a file; this hook closes the one path this branch opens,
delegation. Bash and PowerShell in the reviewer's allowlist and the analyst's inherited tools are
a separate open item. Listing the bulk reader as a skill for the reviewer means putting Agent on
its allowlist, and an allowlist has no shape: with Agent on it, a reviewer can launch an
implementer, which writes. The sentence in the definition asking it not to is prose, and prose is
not a boundary; this hook is. It is the guard, not the sentence, that holds the line.

One rule, and nothing else is ever denied: when the caller is one of READ_ONLY_CALLERS, which the
harness names in the payload as agent_type, and the requested subagent_type is anything other than
ALLOWED_TARGET, the call is denied with a reason naming both. The rule is closed on the field it
reads: for such a caller an absent or empty subagent_type is denied too, because the harness
resolves an omitted type to the general-purpose agent, which carries every tool, so reading a
missing field as "nothing to check" would hand a read-only agent the widest agent there is. A main
session carries no agent_type and is never touched: a person driving a session is not a read-only
agent, and stopping them from delegating would break the kit for everyone. Every other agent type
passes untouched too, because this rule is about the two definitions that promise to change
nothing, not about fan-out in general.

Any internal failure exits 0 without output, so the hook can never block a call.
"""
import json
import sys

# The two definitions that promise to change nothing, by the names their files carry in
# claude/agents/, and the single agent they may reach.
READ_ONLY_CALLERS = ("reviewer", "analyst")
ALLOWED_TARGET = "bulk-reader"


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
    """The denial reason for one delegation, or None when it may proceed."""
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


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return 0
    try:
        if data.get("tool_name") != "Agent":
            return 0
        caller = str(data.get("agent_type") or "").strip().lower()
        target = str((data.get("tool_input") or {}).get("subagent_type") or "").strip().lower()
        reason = verdict(caller, target)
        if reason:
            deny(reason)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
