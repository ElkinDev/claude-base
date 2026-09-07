"""PreToolUse hook for the Agent tool: a read-only agent delegates only to the bulk reader.

The reviewer and the analyst return a verdict or an analysis and never change a file. That
property used to be structural, because neither could reach the Agent tool. Listing the bulk
reader as a skill for them means putting Agent on the reviewer's allowlist, and an allowlist has
no shape: with Agent on it, a reviewer can launch an implementer, which writes. The sentence in
the definition asking it not to is prose, and prose is not a boundary; this hook is. It is the
guard, not the sentence, that holds the line.

One rule, and nothing else is ever denied: when the caller is one of READ_ONLY_CALLERS, which the
harness names in the payload as agent_type, and the requested subagent_type is anything other than
ALLOWED_TARGET, the call is denied with a reason naming both. A main session carries no agent_type
and is never touched: a person driving a session is not a read-only agent, and stopping them from
delegating would break the kit for everyone. Every other agent type passes untouched too, because
this rule is about the two definitions that promise to change nothing, not about fan-out in
general.

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
    if caller not in READ_ONLY_CALLERS or not target or target == ALLOWED_TARGET:
        return None
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
