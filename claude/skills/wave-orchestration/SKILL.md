---
name: wave-orchestration
description: Law sheet for running a band of implementer agents: dispatch, lane conduct, gates under one build mutex, union merges, evidence and token economy.
---

# Wave orchestration

Every line below is a law. Cite laws by their words in briefs; never paraphrase one.

## Dispatch and briefs

- The orchestrator owns dispatch, sequencing, merging, pushing and every conversation with the owner.
- Every brief opens with the token law: never wait alive, never narrate, report raw data.
- Briefs point at ticket ids, file paths, law names and the canonical gate script; they never paraphrase a law and never paste what the lane can read itself.
- Every brief carries the root cause the orchestrator already knows, plus the worktree, the branch, the acceptance list and the exit-file path.
- Every brief names the test task of every module the lane may touch, and the lane adds a phase for any module it ends up touching.
- After any compaction, restart or account switch the orchestrator re-reads this sheet before dispatching or resuming.
- Continuation is a message to the same agent; the orchestrator's context is never forked, and a fresh agent that re-reads the repo never continues live work.
- Stopped agents never resume: recover with a fresh lane per worktree briefed on disk truth, never relaunching over uncommitted work, and order every lane to commit and write a partial report before a planned cutover.
- One feature implementation lane at a time; the rest of the band takes work that does not need the build mutex, such as specs, mockups, analysis, reviews, docs and scripts.
- The band is three to five lanes on the primary account when the approved queue feeds them; a secondary account runs one lane on simple tasks and stays well inside its quotas.
- Zero lanes on an empty queue is the correct state; idle-filling is banned, and the band is called full only after enumerating the mutex-free work classes.
- Orchestrator and lanes run with a capped context and only a research role runs uncapped; agent panes start one at a time and never steal the owner's focus or split the owner's pane.

## Lane conduct

- Agents never wait alive: launch long work from your own session and end the turn.
- Waiting for a mutex or any slow condition is one blocking foreground call, repeated silently; turn-polling a wait is banned.
- While a gate runs, bank the next scope item it does not compile, and end the turn only when nothing remains doable without the verdict.
- Never pause with an uncommitted tree: commit the checkpoint first, then wait.
- Every lane keeps a notes file in its worktree with goal, acceptance list, done and next, and checkpoints it well before its turn budget runs out.
- No narration: no status pings, no per-phase messages, no waiting messages.
- A lane ends its turn at most twice per gate, one interim naming the exit file and one final report; interims are at most five lines and only for a decision, a flag or error, or a checkpoint before sleeping on a queued gate.
- The final report is at most ten lines plus pointers to the exit file, the test results and the diff stat; forensics go to the exit file or the notes file, never into a message.
- Red before green: prove the defect against the pre-change tree before the fix lands.
- Prove the red by snapshot and checkout, never by a stash, whose ref is shared across worktrees.
- Restore the tree before releasing the lock on every exit path, and abort the gate when the restore fingerprint does not match.
- Style pass before any gate: run the touched modules' formatter and linter checks in one acquisition and fix the whole report set in one pass.
- Read every report file in one pass rather than iterating gate runs, and count a report only when its writing task ran in this gate and its mtime falls inside this run's window.
- Every scripted edit asserts its match count, that the file hash moved, and that the intended new text is literally present.
- Never pass a replacement carrying a dollar sign or backslashes through a shell into a regex engine; write the edit to a file or use the editing tool, and detect each file's newline style first on a mixed-ending tree.
- Scripts and analysis run from a private per-lane scratch directory, never writing a bare standard-library module name where an interpreter runs.
- A lane verifies another lane's finding against code and version control before building on it; helpers coordinate through worktree files, and a helper that refuses a peer's git order because its brief reserves git to its parent is correct.

## Gates

- Gates run through the canonical gate script; hand-rolled wrappers are banned.
- A gate that fits inside the foreground tool ceiling runs foreground at the maximum timeout; anything longer launches detached, and the orchestrator owns the wake.
- Harness-tracked background gates, custom watchers and agent-armed monitors are banned as wake mechanisms; a monitor may supplement, never substitute.
- Gate scripts use absolute paths, each run gets its own immutable script and its own exit file truncated at start with every line naming the run, and a script that is executing is never edited.
- The verdict is the exit file plus the phase log, read from the file and never through a pipe; an exit code with no log proves nothing.
- The final exit line folds the worst phase, readers read the phase lines rather than the last line alone, and a disagreement between the exit code and the build's own word resolves red in both directions.
- A harness reporting that nothing failed must first prove the work ran: executed test counts, the build's own executed-task line, or the phase log's evidence.
- A phase that fails in seconds is a spawn, filter or environment error until proved otherwise, and a test filter containing spaces matches nothing.
- A compile red is not the assertions firing; read which task failed before calling a red intended.
- The compile phase builds every test source set the gate will run, compile and lint phases keep going past the first failure, each linter runs as its own phase, and expensive test phases are skipped on a compile red while text-level lint still runs in the same acquisition.
- Every test phase runs under a timeout above its contended duration, and on expiry a thread dump of the worker is taken before the kill.
- Every gate exports its toolchain environment inline, including the runtime's bin directory on PATH when a phase spawns one.
- Lane gates are light pre-gates (compile, lint, the lane's module tests, its new cross-cutting classes by filter) and never the full suite; two consecutive union reds from one lane revoke its light pre-gate for the next wave.
- The application-module phase is never a substitute for the library modules' own tests.
- A gate that mutates the tree runs its tests with the build cache disabled and proves the change in the compiled output; a wave that adds or changes a static-analysis rule runs that phase without a resident daemon and records the loaded rule set as a phase line.
- One machine-wide build mutex, taken by atomic mkdir, polled every one to two seconds, bounded, exiting on its own starvation code, logging one waiting line per poll.
- The holder record is one atomic line naming run, pid, log and epoch, written to a fixed pair of filenames and refreshed by a beater that dies with the script and checks its parent each tick.
- A fresh beat alone is never evidence of work, and every helper or heartbeat pid is killed explicitly before any exec.
- Traps cover normal exit and every catchable stop, kill the gate's own build child before releasing, and a signalled gate exits instead of resuming; release only after confirming the record still names this pid, and a failed ownership match is a loud error, never a silent skip.
- A lock is broken only on positive evidence of absence of work: the newest mtime across record, beat, main log and phase logs older than the longest phase by a wide margin, seen twice a minute apart.
- An unreadable record means live, every guard defaults fail-safe, and no break rests on age alone, on the main log alone, or on any process-id evidence, which crosses shells unreliably.
- A silent log is not a dead process, so signal and probe rather than kill a holder with a build behind it, and after any eviction or gentle stop kill the build daemons and clean the touched module build dirs before relaunching.
- A shared physical test device has its own lock, taken before any command reaches it; the owner preempts, personal devices are never touched, and no agent leaves the device in a state nobody can undo.

## Merge and union

- Merge the batch first, then run one union over it, then resume by message the single lane a real failure belongs to.
- The union is the arbiter: the full suite and the visual baselines run there once per batch, orchestrator-owned with no agent alive behind it, never skipped, and with the build cache disabled after merging any wave that mutated its own tree.
- Never merge into a checkout a union is building from; stop that gate gently, merge, then relaunch one union over the batch.
- Merge conflicts are resolved by the orchestrator by hand, in one pass, keeping both waves' intent.
- A registry collision is not resolved until every list that enumerates the registry carries both sides.
- A schema branch writes its migration as head to head plus one and renumbers it mechanically at rebase.
- Before accepting a green, check the lane's exit-file phases against its diff stat and the diff against the claimed scope; a module in the diff with no phase is an unverified lane, and a delivery claim without diff evidence reopens as work.
- A union red names its test and goes back to the owning lane with the failing result pointer.
- Merge closes the worktree in the same step: remove the worktree, delete the branch, and verify the directory left the disk.
- Remnant cleanup enumerates only directories the worktree list does not show, one by one; globbing the worktree prefix is banned and a worktree with uncommitted work is never cleaned by automation.
- Every worktree is seeded with its ignored local configuration and credential files before its first build.
- Format runs are scoped to the failing module, a repo-wide format is never staged, and real changes are isolated with a diff that ignores end-of-line differences.
- While CI is rationed or unavailable every pushed tip carries the marker that suppresses it, and such a marker never sits above unpushed code.
- A deploy carrying a server-side entitlement change is ordered: precondition data, then the server, then the client, then any default flip, and the order is stated in the batch's deploy note.

## Close and evidence

- Landings and owner-facing artifacts live in one owner-facing location; the agent scratchpad stays internal.
- Pending work lives in one owner-facing backlog file, updated at every band close and every owner decision.
- Release artifacts are built once per cut after verification reports zero holds, staged once with a checksum sidecar, uniquely named and never overwritten.
- Every shippable binary passes its integrity probe before install or handoff.
- Visual baselines mount the real production component, never a scaffold copy of its layout.
- A change confined to a small region is proved by a region frame or a strict comparator, never by a full frame under a percentage budget, and a colour-only change is proved by a palette census.
- A baseline that renders relative time anchors its fixture instant to the run, mid-bucket.
- After any record pass, every pre-existing baseline is compared against its committed bytes in version control and restored unless the change deliberately targets it; movement is judged on decoded pixels, never on file bytes.
- Baselines whose surface animates are restored from committed bytes after any record and shown byte-identical in the gate evidence.
- A re-recorded baseline is eyeballed against the previous one and against the state the test names in its own words.
- An absence assertion reads the unmerged tree and sits beside a positive sibling proving the finder can see the node when present.
- A test hands fresh state per mutation exactly as production does, a scroll or reveal claim asserts the measured offset, and a scenario that depends on an absent or unanswered input asserts that premise inside the test.
- Restore, never rewrite, an assertion that pins an approved design, and read its history before trusting the red.
- Promoting a string to a shared module carries every locale verbatim, and a permission widening lands its rules before the code that needs them.
- Device fixtures are seeded through the data layer with the app stopped, and the app is launched by component, never through the launcher.
- Structural features are design-first with an approved spec before code while small curated fixes stay on the fast lane, and a defect seen twice earns a ledger row and a standing guard before new work lands in its area.

## Economy and context

- The orchestrator verifies every load-bearing claim from machine-readable artifacts before it shapes a decision; lane reports are evidence, not truth.
- The orchestrator never reads images, never opens a browser and never reads raw build output; a hook stores it and returns a digest.
- The orchestrator's heartbeat sweeps disk truth, wakes lanes with facts (branch tip, dirty count, exit-file verdict) and never narrates no-ops.
- Wake on terminal lines only, never on per-phase lines and never on a clock.
- An idle watchdog runs whenever lanes are in flight, the owner is never the one who notices dead air, and lanes from a closed train are stopped so their rows cannot revive on stale monitors.
- Each lane's token figure goes into the worklog; optimization claims without measurement are guesses.
- Everything scriptable is scripted; an agent doing a script's work is waste.
- Third-party harnesses never run on the subscription's quota, and a local model's verdicts never gate a decision without a cheap verification.
- Read the machine clock before scheduling anything.
- Artifacts are written in the project's declared artifact language, and nothing carries em-dashes, tool attribution or person names.

When a failure teaches a law, add the law here and its reasoning to the project's own protocol history; this sheet stays laws only.
