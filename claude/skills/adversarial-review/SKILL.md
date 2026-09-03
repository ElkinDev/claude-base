---
name: adversarial-review
description: Refute a change before it ships instead of confirming it. The method the reviewer agent preloads (spec compliance first, then code quality); a lane invokes it to spawn one read-only reviewer over a PR, a release, or work touching money, deletion, sync or auth.
---

# Adversarial review

A reviewer asked to check work will find it correct. A reviewer asked to break it will find what is wrong with it. Only the second is worth the tokens. Confirmation is the default failure of review, and the defect it hides is always the same one: a control that reports success it never verified. A review that merely agrees reproduces that defect one level up.

## When

Every branch before merge and every PR before it is opened. Before any release build. Always, without asking, when the change touches money, notifications and alarms, deletion or purging, sync and conflict resolution, or authorisation rules, because those are the places where a quiet mistake reaches a person rather than a log.

After the gate is green, never instead of it. A green gate says the tests that exist pass. This asks whether the right tests exist at all.

## Who runs it

The kit's `reviewer` agent preloads this skill and is the normal route: the orchestrator launches it with the brief, the diff range and the evidence paths, and nothing else. A lane that needs a review outside that flow (a PR it is about to open, a release) spawns ONE agent, `general-purpose` on `opus`, read-only, with the sheet below and the real diff, never a summary written from memory.

Whoever spawns it never says the change is good, never says the gate passed, never says the author is confident. All three prime the reviewer to agree.

The reviewer is read-only: no edits, no git writes, no builds, no locks. It reads, reasons and reports; the caller decides.

## Two stages, in this order

Stage one, spec compliance: does the diff do what the brief and the acceptance list asked, no more and no less? Each acceptance item is attacked one at a time with the input, state or ordering that would break it, and is satisfied only when the attack failed. What the brief asked for and the diff does not touch is a finding; what the diff touches beyond the brief is a finding. Tests must assert the behaviour the item describes, not the shape of the implementation: a test that would still pass with the feature removed, that asserts a mock was called, or that seeds a degenerate case which passes for the wrong reason, is a finding.

Stage two, code quality, only once stage one is done, because a well-built wrong thing is still wrong. The order follows the subagent-driven-development flow of the superpowers plugin (MIT), which runs a spec reviewer before a quality reviewer.

## The sheet

> You are reviewing a change that its author believes is correct and complete. Your job is to refute that, not to confirm it. A review that agrees has told me nothing I did not already have.
>
> Read only. Do not edit anything, do not run git write commands, do not run a build, do not take any lock. Read the code, read the tests, reason, and report.
>
> The claims the author makes, to be disproved rather than taken as context: {THE CLAIMS, verbatim, including the red-before-green evidence and any position the author defended}
>
> The brief and its acceptance list: {PATH}
>
> The diff: {RANGE OR PATH}
>
> The surrounding code and specs worth reading: {PATHS}
>
> Stage one, spec compliance, item by item, before anything else. Then stage two, the questions below, and say plainly when the honest answer is that you found nothing, because a fabricated objection is worse than none.
>
> Where is the claim wrong? Take each load-bearing assertion and construct the case that breaks it: concurrency, ordering, a null the author assumed impossible, an empty collection, a clock or timezone edge, a process that dies mid-operation, a user on the oldest supported version.
>
> What does the test prove, versus what does it look like it proves? A test that passes for the wrong reason is worse than a missing test, because it is a control reporting success it did not earn. Check whether the red would have failed for the stated reason, whether a guard's false branch is reachable at all, whether the fixture invented a condition the system never has.
>
> What did the change not cover? The sibling path, the other caller, the same defect one module over, a twin implementation that now diverges right next to the change, the case the author explicitly deferred and whether deferring it was honest.
>
> Where does the contract lie to its consumer? For each new or changed type on a boundary: are the wire types faithful (a calendar date is a date, never a timestamp; money is a decimal; an enum is not a string), and what does each audience actually receive from each entry point? For each new guard: an invariant, or a first-time gate that goes silent once the state exists?
>
> What does this cost at runtime, per interaction and per row? For a UI component: any derivation that parses, walks or rebuilds a structure on every render without memoization, since a render per keystroke makes per-render cost per-interaction cost. For a list: per-cell work that grows with the row count, handlers recreated per row that defeat memoized children. For a data path: a query inside a loop, a per-row round trip, a collection materialized only to count it. Quote the line and name what triggers the repeated work. That the file already does it this way is not a defense; flag the defect and its sibling sites.
>
> What breaks for somebody who already runs the shipped version? Existing rows, existing preferences, a half-completed migration, a build that has not updated yet, data that syncs from a client on the old version.
>
> Where can this lose or expose data? Anything deleted, purged, cascaded, overwritten or made unreachable. Anything readable by somebody who should not read it. Anything recorded as done that was not verified.
>
> What would you have done better? Not style. Structure, naming that misleads, a comment that asserts something the code does not do, a simpler shape that is also more correct.
>
> Rank what you find by whether it reaches a user. Quote file and line. Where you are uncertain, say uncertain and say what would settle it. End with one disposition: CLEAR, naming the acceptance items you tried to break and how, or BLOCK with numbered findings, each carrying CRITICAL, MAJOR or MINOR, the file:line and the concrete failing scenario.

## Reading the result

Findings are claims, not verdicts. Verify the load-bearing ones before acting, the same way a lane's report is verified, because a reviewer can be confidently wrong too.

Disposition is fix-by-default: a verified finding on correctness, concurrency or runtime cost that fits within the story gets fixed, never "accepted and documented". Acceptance is only for product decisions or cost that is genuinely out of scope, and every accepted gap is stated in the PR text so the human reviewer decides with the cards on the table. "The file already does it this way" is never acceptance grounds: when the surrounding convention is itself the defect, the finding stands, gets fixed, and starts a sweep over that convention's other sites. Every applied fix gets its class swept: grep the touched files for the other members of the pattern and treat them in the same pass, or the next reviewer files the sibling as a fresh finding.

A review that returns nothing is a real outcome and is recorded as one. A review made only of style notes, or a CLEAR that names no attempted scenario, was primed to agree: discard it and run it again with the priming removed.

A finding that is a product or privacy decision goes to the owner. A finding that is a defect goes into the queue with its file and line.
