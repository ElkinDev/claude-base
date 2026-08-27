---
name: subagent-delegation
description: Delegate heavy or parallelizable work to subagents to keep the main context lean. Use for broad searches or multi-file reads.
---

# Subagent Delegation

Keep the main thread lean by sending token-heavy work to subagents. A subagent runs in
its OWN context window and returns only its conclusion, so large reads and searches
never accumulate in the working context. You keep the answer, not the file dumps.

## When to delegate
- Broad search across many files or directories where you only need the conclusion.
- Reading several large files to answer one question.
- Independent investigations that can run in parallel (one area each).
- Research that produces a short verdict from a lot of input.

## When NOT to delegate (do it inline)
- A single known file, symbol, or value. Just read it.
- Small edits where you already have the context.
- Anything that needs the live conversation nuance to get right.
- Trivial work. A subagent re-pays its own setup, so TOTAL tokens go up. Delegate to
  protect the context WINDOW, not to save total spend. Do not delegate one-liners.

## How to scope a subagent
- Self-contained prompt: it does not see this chat. State the goal, the paths, and the
  exact deliverable.
- Ask for the CONCLUSION, not a transcript: "return the file:line and a one-line why",
  not "read these and tell me everything".
- One clear job per subagent. Split unrelated jobs into separate agents.

## Parallel fan-out
For independent jobs, launch them in ONE message (multiple tool calls) so they run
concurrently. Wait for all, then synthesize. Use this for "search these 4 areas" or
"check each app for X".

## Rules still apply
Delegated work follows the same hard rules: no commit/push/PR, no AI attribution,
verify before claiming. A subagent's findings are evidence to check, not gospel.
