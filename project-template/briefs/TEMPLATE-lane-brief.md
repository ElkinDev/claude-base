# Lane brief template

Copy this file into the briefs folder of the evidence root (the folder `CLAUDE_BRIEFS_ROOT` resolves a relative `briefs/` path against) and write each lane brief to its shape. The launch hook `claude/hooks/guard-delegate.py` reads three tiers and nothing else: tier 1 a Report or Deliverable section, tier 2 a test or a golden named anywhere, tier 3 a test class under Pins, Rule or Law. It denies below the deny tier it is set to, and that tier is measured, not chosen: `python scripts/brief-check.py --from-log <hook log>` grades the logged launches, and the deny tier is the highest one at least 80 percent of them pass. `scripts/brief-check.py` also reports tier 4 (a budget with a number and a unit) and tier 5 (the end-your-turn line of a brief that names a detached run) as would-deny. `scripts/brief-gen.py` writes the review, fix and notes briefs of a lane in this shape.

First lines, always: "Your reader is a session, never a person. Write in English. The deliverable is the report file named here; your last message is one line naming the report path, no summary. No attribution lines." Then the lane line: lane token, worktree, branch, base tip (a sha, never a branch name).

## Purpose
Which of the four this lane serves (quality, optimization, automation, token reduction), the number it moves with its baseline and the command that reads it, and what it does not serve. A lane that serves none is not launched.

## Context
What exists on disk today, with file:line, and why the lane runs. State claims (a tip, a file, a count) are verified against the disk when the brief is written; a brief that starts from a false state is a rework round paid in advance.

## Change
What the lane changes, one paragraph or one list. A structural feature or an animation carries its design here or a link to the reviewed design; a lane never designs while implementing.

## Pins
Red first: the first commit is the failing test. Each pin is one line: the test class (and method when it matters) and the sentence of the change it proves. A pin written as prose without a class name is a missing pin, and tier 3 reads it as one.

## Checks
- A long build or test run is launched detached, as a FOREGROUND tool call that returns at once, never with run_in_background, whose exit re-invokes the lane and re-sends its context. Then END YOUR TURN with the run's .done or .exit path as your last line: no watcher, no polling, no sleep. The orchestrator reads the result and resumes you with the verdict.
- Before hand-back: one filtered run of the lane's OWN test classes (the pins and every test class the lane wrote or edited), never a whole suite; the gate runs the suites. The report names the run tag and its exit. Read the exit file for the result and never hold the run log open (no tail -f, no editor).
- After any record commit (golden images included), the static pre-check runs again on the new tip; a pre-check stamp belongs to one tip.
- No commit on a tip while a gate or a review runs on it; fixes wait for the verdict, or the gate restarts on the new tip.

## Report
Path: `<evidence root>/lanes/<lane>-<date>.md`, a size cap in lines, what it must contain (the tip, the gate run name, the pins with their verdict, what was not done), and always an Open items section: what the lane saw wrong, fragile, slow or badly built in the code it touched or read, inside or outside the brief, with file:line and a one-line suggestion each. Forbidden means do not change, never do not report.

If the lane writes or changes a script (a file under scripts/, or a .py, .ps1, .sh or .ts file), the report also carries a hostile-input table, one row per probe: `H<n> / entry point / scratch path / command / exit code / one output line`, or `H<n> / n/a / reason`. The probes: H1: an empty, whitespace-only, missing or malformed argument. H2: a missing, empty, stale-leftover or locked file. H3: two instances at once. H4: every child exit code and every throw, including one while a lock is held and one inside a finally; and each bound actually fires. H5: path spellings: forward slash, a junction or symlink, spaces. H6: no-op input: already done, nothing to do, a repeated member. H7: one run on the real target, or a faithful stub of it, at real size, timed, with the output counted against its source. H8: clock and session edges: a bare date, a run at each scheduled hour of the day, a session restarted mid-window. Caps and n/a rules: every probe runs under `timeout 120`, well inside any bounded-wait rule, and H3 starts both instances under one timeout. No probe touches a shared build lock, a device lock, the register or a device: a failing probe must never reach the real lock. For a script whose real target is a machine-wide lock or a lock root, H3 and H4 run against a scratch copy of the lock root and H7 is n/a by rule. A script that writes the register runs H7 on a copy of it. A script whose target is a device marks H7 n/a for the lane and leaves it to a delegated device run. A stub stands in where one exists, such as a throwaway batch file in place of a launcher.

## Budget
Budget N tool uses, with the unit written out (tier 4 reads the number and the unit). A brief that spends more than 20 on reading is wrong. The round cap: two fix rounds, then a diagnosis.

## Forbidden
What the lane must not touch or do; the lanes that share a file; the accepted drafts it must not contradict. A new UI variant outside the design system unless this brief names the catalog entry it extends and why.
