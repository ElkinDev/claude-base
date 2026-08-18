---
name: wave-orchestration
description: Run a project as an orchestrator conducting a band of implementer agents (waves) under a token-economy regime, with red-first gates, machine-readable evidence, a regression ledger, and an optional zero-cost local model for night QA. Use when coordinating multi-agent autonomous work on a codebase, when agent spend must stay proportional to progress, or when the user asks for the wave/band way of working.
---

# Wave orchestration

One orchestrator conducts; implementer agents (waves, or lanes) build; scripts and gates verify; a local model collects for free at night. The system exists to convert tokens into merged, verified work at the highest possible ratio, and every rule below was paid for by a failure that taught it.

## Roles

- **Orchestrator** (the strongest model available): decides, briefs, verifies, merges, pushes, talks to the owner. Never implements at length while lanes are free to do it, never believes a lane's prose without checking the artifacts.
- **Implementer lanes** (strong-tier subagents): one ticket cluster each, own git worktree, own branch, red-first, gate before reporting. Route models by VERIFICATION COST, not task size: a cheaper model is only cheaper when the gate can prove its work mechanically; if correctness needs the orchestrator reading code, the supervision cost eats the saving.
- **Local model** (optional, zero quota): collectors and low-stakes judgment over FILES that scripts gathered. Its findings NEVER become tickets without a cheap verification pass (grep, bench, source read); expect a high noise rate and design for it.
- **Scripts** (zero tokens, deterministic): everything scriptable is scripted: gates, collectors, cleanups, guards. An agent doing what a script can do is waste.

## The band

3 to 5 lanes WHEN the approved queue feeds them and three waste conditions hold: no output nobody reads, no reprocessed bugs, no relaunched contexts. Zero lanes on an empty queue is the correct state, never idle-filling. On every lane-completion notification the orchestrator launches the next approved item before anything else. No polling or clock wake-ups; wake on terminal signals only.

## Token economy (the sealed contract)

1. **Report contract**: a lane's final message is at most 10 lines plus pointers (exit file, test-results XML, diff stat). Long prose is reserved for FLAG, ERROR, or a decision the orchestrator must take. The verification standard does not drop; the MEDIUM changes: the orchestrator verifies from machine-readable artifacts, not narrative.
2. **Briefs point, never paste**: ticket ids, file paths, law names. The lane reads what it needs. Delegate broad reading to a search subagent that returns conclusions, keeping file dumps out of every long-lived context.
3. **Measure**: record each lane's token usage (the harness reports it per completed agent) in a worklog; set budgets; review the burn against progress. Optimization claims without measurement are guesses. Note the account's remaining quota at session open and close.
4. **Continue, do not respawn**: message a live or completed lane to extend it (it keeps its context); a fresh spawn re-pays the whole fixed context. Never fork the orchestrator's own context into a lane.
5. **One session per work block**: long orchestrator sessions pay repeated compaction; resume from memory/worklog files instead. One canonical record per audience (an owner board, a repo intake); other documents link rather than repeat.
6. **Artifacts once**: build release artifacts once per cut, AFTER device or end-to-end verification reports zero holds. A discarded upload can still consume a version number.

## Gates and worktrees

- Every lane works in its own worktree from the current main tip; the orchestrator merges. **Merge closes the worktree in the same step**: remove worktree, delete branch, VERIFY the directory left the disk (on Windows, long build paths defeat rmdir; retry with `rm -rf`, and a daemon-locked file waits for the daemon, not for `--force`). A cleanup script that only touches clean, merged trees runs as a backstop. A dirty worktree is never cleaned by automation; it is triaged by name.
- Gates run detached (`nohup ... &`) under a single machine-wide build mutex (mkdir-atomic directory lock with an owner record refreshed on a beat); phases run under timeouts with `--continue`; every exit line names its run; the final gate verdict folds the worst phase. Verify from the exit file and the test XML, never from the build's last word.
- **Red-first**: every fix proves the defect on the shipped bytes before the fix makes the test pass. A guard that cannot fail is decoration; prove the red by running the new pin against the pre-change tree (snapshot and restore, never stash on a shared repo).
- Lanes run the formatter and linter over THEIR touched files before requesting the gate; one over-long line costs a whole extra gate acquisition.
- When CI is unavailable or rationed, a local union gate (full mirror over the touched modules) is the arbiter, and every push carries a tip that suppresses CI (e.g. `[skip ci]`) until it returns; a pre-push hook enforcing identity and the tip rule prevents the two expensive mistakes.

## Verification (anti-hallucination law)

The orchestrator verifies every load-bearing claim before it shapes a decision: cites are opened, greps are re-run, arithmetic is reproduced. A lane asserting something "from memory" about an external system (an OS constant, a store policy, an API) verifies it against the source, not recollection. Findings from any model, local or premium, are a MAP, never evidence.

## Regression ledger

A defect seen TWICE gets a ledger row (pattern, canonical case, standing guard) and a guard ticket BEFORE new feature work lands in its area. Wave briefs cite the rows of the area they touch; reintroducing a rowed pattern is a FLAG, not a report line. A row with no standing guard is a ticket, not a record.

## Owner interaction

Decide everything decidable (precedent, house law, reversible default) and log it for veto; only genuine product-taste calls reach the owner, numbered, each with a recommendation. When the owner reports a field round, triage EVERY item into a numbered document (bug lanes, small queue, design queue, answers) before implementing any of it. Design-first for structural features: analysis and an approved spec or mockup before code; small curated fixes stay on the fast lane.

## Device bench (if the project has one)

One physical test device, one mkdir-atomic lock every session must take before any command reaches it; the device is always addressed by serial; personal devices are never touched. Restore every setting changed; leave the device in a known state; screenshots and dumps are the evidence. Before diagnosing any anomaly, verify WHICH build is installed (update time plus a hash of the pulled binary).

## Local-model night QA (optional)

A scripted harness drives the device or the checkout and SAVES files; the local model judges the files against a fixed rubric with structured, greppable verdict lines, uncertainty marked explicitly; a run window (for example 20:00-07:00) protects daytime machine performance. The morning pass is one orchestrator read: verify the survivors cheaply, discard the noise, lane what remains. Two hard-won calibrations: have the harness read the device's REAL resolution before computing taps, and dedupe repeated findings across missions before the report.

## Evolution log

When a failure teaches a law, append it to the project's protocol skill the same day, dated, with the failure named. The protocol is alive; this file is its portable seed.
