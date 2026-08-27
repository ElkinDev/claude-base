---
name: investigate-issue
description: Investigate test failures, crashes, timeouts, or bugs using facts from logs and evidence only. Use when tests fail or behavior is unexpected.
---

# Issue Investigation Protocol

Use when investigating test failures, crashes, timeouts, or unexpected behavior.

## CRITICAL: No Guessing - EVIDENCE FIRST

### FORBIDDEN responses (path-of-least-resistance guesses)
- "It's non-deterministic"
- "We're out of resources / memory"
- "Let's lower the threshold"
- "Let's try random changes"
- "Max iterations reached" - unless PROVED with logs
- "Network timeout" - unless an actual timeout is in the error trace
- "Let's increase the limit" / "Let's retry more times" - treating symptoms
- "Timing issue" - timing is rarely the cause; it's usually a CODE BUG
- "Data is stale/expired" - lazy excuse; query the actual source to verify

### FORBIDDEN fix behaviors (trial-and-error is not debugging)
- "Quick fix for now, investigate later"
- "Just try changing X and see if it works"
- Changing more than one variable at once, then running the tests
- "I don't fully understand but this might work"
- "One more fix attempt" after 2+ have failed, especially when each fix just moves the
  failure somewhere else
- 3-STRIKES RULE: if 3+ fixes have failed, STOP and question the architecture or the
  diagnosis. Do not attempt fix #4 the same way.

### REQUIRED before ANY fix proposal
1. Find the **ACTUAL error trace** - not assumptions
2. Identify the **EXACT line/message** that fails
3. Show the **ACTUAL data** that caused the failure
4. Explain **WHY** that data is wrong
5. Trace back to the **ROOT CAUSE** - not the symptom

### The test
Before proposing a fix, ask: do I have PROOF this is the cause? Can I show the exact
log/trace? Am I treating the root cause or a symptom? If you can't answer YES to all
three, KEEP INVESTIGATING.

## Step 1: Gather Facts
Find and read the real logs, saved request/response files, and output artifacts for
the failing run. Read them to see what actually happened.

## Step 2: Red Flags Checklist

| Pattern | Meaning |
|---------|---------|
| "cannot locate" / "not found" | Missing data or misconfigured paths |
| Empty results `{}` or `[]` | Index/cache not built or not passed |
| Same operation repeated 3+ times | Code or agent looping |
| Size/count growing each iteration | Payload accumulation bug |
| `TimeoutError` / `ConnectionError` | Network/API config issue |
| Truncated output | Response size limit hit |

## Step 3: Verify Infrastructure
Before blaming anything external:
1. **Data initialized?** Required structures are built.
2. **Data passed correctly?** Check parameters and state flow.
3. **Config correct?** Timeouts, retries, authentication.
4. **Instructions clear?** Check for ambiguity.

## Step 4: Create a Test First
Reproduce the issue in a test before fixing, documenting the evidence and root cause
in the test's docstring/comment.

## Step 5: Implement the Fix
State the hypothesis in writing first: "I think X is the root cause because Y." Then:
- Pattern analysis: find similar code that WORKS, read it completely, and list every
  difference between the working and the broken path. The difference is usually the bug.
- Make the SMALLEST change that tests the hypothesis, ONE variable at a time, so a pass
  or fail actually tells you something.
- Fix the ROOT CAUSE (the original trigger traced up the call chain), not the symptom.
  After the fix, add validation at the layer where the bad data first appeared so the
  same bug cannot recur silently.

## Step 6: Verify (user runs externally)
Provide the exact command for the user to run the relevant test; do NOT run the full
suite automatically unless asked.

## Flaky or timeout tests: wait for a condition, not a duration

Symptom: a test that "sometimes passes", or a `TimeoutError` on something async. The
usual cause is an ARBITRARY wait (`setTimeout(50)`, `Task.Delay`, `Thread.Sleep`) that
guesses how long an async operation takes. Too short and it is flaky; too long and the
suite is slow. A fixed sleep is never the right fix; raising it just hides the problem.

Fix: poll the real condition until it holds, with a timeout. Three rules:
- Read the value FRESH inside the loop each iteration. Never cache it before the loop,
  or you re-check a stale snapshot forever.
- Poll on a sane interval (~10ms), not every 1ms (that burns CPU for nothing).
- Always include a timeout guard that throws a CLEAR error stating what was expected,
  so a never-met condition fails loud instead of hanging.

Stack notes:
- Frontend (JS): use the framework's waiter, not a manual sleep. Testing Library
  `waitFor` / `findBy*`, Playwright auto-waiting and `expect.poll` / `toPass`.
- Backend (.NET): poll the condition with a timeout (or a retry policy, or an assertion
  that already waits). Never a fixed `Thread.Sleep` to "give it time".
