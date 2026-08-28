# Context economics: what a turn costs, what compaction costs, and when to compact

This document answers a question that comes up as soon as someone runs Claude Code all day
with a large context window: *why compact at all, and why at a fixed level?* It gives the cost
model, the numbers measured on real transcripts, the compaction mechanics of Claude Code as
verified in version 2.1.248, and the design of the small toolkit in this repo that compacts at
a sensible moment and loses nothing when it does. Everything here costs zero model tokens to
run: the hooks and the watcher are plain Python reading local files and the Herdr CLI.

Measured on one working day (2026-08-27) with Claude Code 2.1.248. Versions change; re-run
`scripts/compaction-report.py` on your own transcripts before trusting a number.

## 1. What you pay for: the context is re-sent on every turn

A conversation is not billed once. Every turn sends the whole context again: the system prompt,
the tool schemas, every earlier message, every tool result. The API meters four token classes,
and their relative prices are what matter here (Opus-class list prices, rounded):

| class | what it is | relative price |
|---|---|---|
| cache read | context already in the prompt cache | 0.1 |
| uncached input | new input not served from cache | 1 |
| cache write | new context written into the cache | 1.25 |
| output | what the model writes | 5 |

So the weighted cost of one turn is about

```
cost(turn) = 0.1 x context_re_read + 1.25 x new_context + 1 x uncached + 5 x output
```

Two consequences drive everything else:

- **Context size is a per-turn tax.** A session sitting at 500k tokens pays 50k weighted just
  to be re-read on every turn, before it does anything. At 100k it pays 10k.
- **A cache break is a rewrite of the whole context at 1.25.** The prompt cache has a time to
  live (one hour on this setup, five minutes on others). Let it expire, or change something
  early in the prompt, and the next turn rewrites everything: one break at 511k cost 639k
  weighted, about thirty ordinary turns.

`scripts/compaction-report.py` reads the usage rows of a transcript and prints exactly these
quantities per session, per cycle and per turn. Its weights are a parameter; the conclusions do
not depend on the exact values.

## 2. Why a 1M window is not free

Claude Code compacts when the context reaches a fraction of the model window. With a 1M model
and no explicit window setting, that fraction of 1M is the ceiling, so a session simply rides up
to 700k or 800k tokens. It looks like more memory. It is mostly more tax:

| session | turns | average context | tokens re-sent | weighted per turn |
|---|---|---|---|---|
| analysis session, 1M window | 97 | 514k (peak 769k) | 49.9M | 86k |
| same session, 200k cycles | rest of the day | 90k to 120k | | 24k to 36k |
| orchestration session, 200k window | 365 | 118k (peak 167k) | 43.1M | 22.6k |

The 1M stretch was 42 percent of that session's turns and 69 percent of its cost. The model did
not answer better with 700k of history than with 120k plus a good summary; it answered slower
and every cache break was catastrophic. Rounds of long, uncompacted context are the single
largest waste a heavy user can have, ahead of everything a memory tool could save.

The cumulative content of a session is not what you pay for; the content re-sent per turn is.
Six compactions in a day do not add up to 1M of paid context. They keep each turn near 100k.

## 3. What compaction costs and what it saves

Measured on the orchestration session (seven compactions in one day):

- Trigger at 156k to 167k tokens (78 to 83 percent of the 200k window).
- Floor right after: 56k to 77k (28 to 38 percent). Twelve turns later: 83k to 130k, because the
  harness re-injects the files that were recently read and the recovery hook asks for re-reads.
- Dropped per compaction: 80k to 110k tokens. Summary: 14k to 23k characters.
- Overhead of one compaction, weighted: about 130k (the old context read once at 0.1, the
  summary written at 5, the floor re-cached at 1.25). That is six ordinary turns.

A cycle of 40 to 60 turns at an average of 118k costs about 20k to 25k weighted per turn.
Without compaction the same turns would ride at 300k to 500k and cost three to four times more.
The seven compactions together were roughly 7 percent of the day's spend and avoided about
70 percent of it. Compaction is not the waste; the level it happens at is a second-order detail
(section 6), and the *moment* it happens at is what the toolkit fixes.

Why the working band looks small: with a trigger at 83 percent and a floor at 38 percent, a
cycle has about 45 percent of the window to work with. "More than half the window is lost to
compaction" is true as a share of the window and irrelevant as a cost: the floor is what you
pay for on every turn, and the part of the floor you control is small (section 5).

## 4. The knobs in Claude Code 2.1.248

Verified by reading the bundled code of 2.1.248; behaviour may change in later versions.

| knob | effect |
|---|---|
| threshold | `min(window - round(window x bufferFraction), window - 13000)`; the fraction comes from a remote table keyed by window size, not from your settings. On 200k it lands at about 160k (80 percent). |
| `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` | holds every native-1M model, subagents included, to a 200k window. The simplest cap. |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW=<tokens>`, setting `autoCompactWindow`, command `/autocompact <tokens>` | the window the threshold is computed from, clamped to the model window. The only lever that *raises* the trigger above 200k. |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | applied with `Math.min`, so it can only lower the trigger, never raise it. |
| `DISABLE_AUTO_COMPACT=1` | no automatic compaction. Never do this on a session that runs unattended: it will hit the hard limit mid-task. |
| `DISABLE_COMPACT=1` | also disables manual `/compact`. |
| blocking limit | 20000 tokens below the threshold; above it, turns are refused until compaction. |
| precomputed compaction | recent versions compute the summary ahead of the threshold so the switch is fast; it does not change the level. |
| PreCompact hook | receives `session_id`, `transcript_path`, `cwd`, `trigger` (`manual` or `auto`) and `custom_instructions`. Its **stdout is joined into the custom instructions of the summarizer**, for manual and automatic compactions alike. Exit code 2 cancels the compaction. A subagent's compaction runs the hook too, with `agent_id` and `agent_type` added and the parent's `session_id` and `transcript_path`, but its summarizer ignores the instructions. |
| PostCompact hook | receives the same fields plus `compact_summary`. Its stdout is only a display message, never context. |
| SessionStart hook, matcher `compact` | runs right after compaction; its stdout becomes context. This is where recovery pointers belong. |

Two gotchas worth repeating. First, anything a PreCompact hook prints becomes summarization
instructions, so a hook that prints a debug line steers every summary. Second, the harness
re-injects recently read files after compaction ("Called the Read tool with the following
input"), which is why the floor climbs 10k to 15k in the first turns; it is not configurable.

## 5. Where the floor comes from

The context right after compaction, on this setup, was about 65k to 77k tokens:

| part | size | yours to change? |
|---|---|---|
| harness system prompt and tool schemas | about 30k, cached | no |
| the summary | 4k to 6k | length, through the PreCompact instructions |
| the preserved tail: recent messages the harness keeps verbatim, plus re-injected recently read files | 8k to 13k (`postTokens` in the boundary row, minus the summary) | only by reading fewer large files before compaction |
| user CLAUDE.md, memory index, skill descriptions, MCP tool names | 5k to 8k | yes, and it is the smallest part |
| recovery hook output | under 4k | yes |

The floor is paid on every turn of every cycle, so it is a first-order lever, but the part
you own is a few thousand tokens. Trimming a memory index from 3k to 2k is worth doing once;
it is not where the day goes.

## 6. The right moment: an optimum band, and why the level is second order

Let `F` be the floor, `g` the average context growth per turn, `O` the weighted overhead of one
compaction and `cr` the cache-read price (0.1). A cycle that runs from `F` to `F + B` lasts
`B / g` turns. Averaged over the cycle, each turn pays the carry of the band, about
`cr x B / 2`, plus its share of the next compaction, `O x g / B`. Minimizing the sum gives

```
B* = sqrt(2 x O x g / cr) = sqrt(20 x O x g)      with cr = 0.1
```

With the measured day (402 turns, eight compactions): `O = 126k`, `g = 1.6k` per turn and
`F = 68k`, so `B* = 63k` and the optimum trigger is about 131k tokens, 65 percent of a 200k
window. The report prints this figure from your own numbers.

The curve is flat around the optimum: the variable part is 6.4k weighted per turn at `B*`,
and 8k at half or twice `B*`, a 7 percent difference on a 22.3k turn, under 3 percent between
`0.85 B*` and `1.6 B*`. The exact trigger level is therefore second order. The first-order
levers, in the measured order of size, are:

1. **The number of turns.** Every turn re-sends the context. Wake-ups that only narrate,
   notification turns that could be batched, and polling loops are pure tax.
2. **The floor**, paid on every turn (section 5).
3. **Output tokens at 5x.** A model that writes 3k tokens of narration per turn costs more in
   output than in context re-reads at 100k. Reports and briefs written by the model are the
   most expensive thing a session does.
4. **Intake.** Large tool results, images and file dumps become context that is re-read every
   turn until the next compaction. Filter at the source (PreToolUse hooks that deny or trim).
5. **Cache warmth.** A break rewrites everything at 1.25. Do not let a large context idle past
   the cache time to live; compact before a long pause, or accept a small context during it.

Compacting at 65 percent instead of 83 percent saves a few percent per turn. Compacting *at a
boundary* instead of mid-task saves the rework of a summary that cut a task in half, and that
is not measurable in tokens because it shows up as wrong work. The compaction itself is dead
time as well: the measured day spent 1,025 seconds inside its eight compactions, about two
minutes each, during which the session does nothing.

## 7. The toolkit

Five pieces, all zero-token, all in this repo:

| piece | event | what it does |
|---|---|---|
| `claude/hooks/precompact-checkpoint.py` | PreCompact | writes a checkpoint file with git truth (branch, tip, uncommitted files, worktrees) for the current repository or every repository directly under `cwd`, the subagents of the session with their last words, and the last text of the dialogue; then prints the summarization instructions (keep hashes, ids, paths and the next step verbatim; point at the checkpoint for git state). Never blocks. Compactions of a subagent (payload with `agent_id`) are skipped unless `CLAUDE_CHECKPOINT_SUBAGENTS=1`. |
| `claude/hooks/postcompact-persist.py` | PostCompact | saves the summary the harness produced next to the checkpoint. Prints nothing. |
| `claude/hooks/compact-recover.py` | SessionStart (compact) | after compaction, injects the checkpoint path and its disk-truth section (capped), plus the existing pointers (notes, brief, landings, autosave). Under 4k characters. |
| `scripts/compact-at-boundary.py` | a process, not a hook | watches the Claude sessions Herdr knows; when one is above a threshold of its window *and* has been waiting for input for a while (Herdr `idle`, or `done`, which is what Herdr reports right after a compaction), submits `/compact` to that pane, once, then cools down. Auto-compaction stays armed as the ceiling. |
| `scripts/compaction-report.py` | on demand | the measurement behind this document, for your transcripts. |

Data flow of one compaction with the toolkit on:

```
watcher sees idle + 65%  ->  /compact  ->  PreCompact: autosave (dialogue tail), worklog snapshot,
checkpoint (git truth + subagents + last words) and its instructions  ->  the summarizer runs
with those instructions  ->  PostCompact: summary saved next to the checkpoint  ->  SessionStart
(compact): recovery text points at the checkpoint  ->  the session continues at the floor
```

Nothing in the flow asks the model to write anything before compaction. The checkpoint is
computed from disk, which is both cheaper (no output tokens) and more reliable (a model asked
to "write a brief now" mid-task produces a partial brief at 5x the price).

## 8. Install and use

The hooks install with the rest of the toolkit (`install.ps1`); `claude/settings.json` wires
them. They are wired per config directory: with account profiles (`docs/ACCOUNTS.md`), the
default directory is the only one that has them until each profile is synced with
`cc <profile> -p -s`, and a session on an unsynced profile compacts with no checkpoint and no
warning. Running sessions pick the hooks up as soon as the file changes, no restart needed.
If you wire them by hand, the events are:

```json
"PreCompact": [
  { "hooks": [ { "type": "command", "command": "python \"%USERPROFILE%/.claude/hooks/precompact-autosave.py\"", "timeout": 20 } ] },
  { "hooks": [ { "type": "command", "command": "python \"%USERPROFILE%/.claude/hooks/precompact-checkpoint.py\"", "timeout": 20 } ] }
],
"PostCompact": [
  { "hooks": [ { "type": "command", "command": "python \"%USERPROFILE%/.claude/hooks/postcompact-persist.py\"", "timeout": 15 } ] }
],
"SessionStart": [
  { "matcher": "compact", "hooks": [ { "type": "command", "command": "python \"%USERPROFILE%/.claude/hooks/compact-recover.py\"", "timeout": 15 } ] }
]
```

Hook changes reach sessions that are already running: on 2.1.248, two processes started at
18:45 and 18:56 ran hooks wired into `settings.json` at 20:29, at their compactions of 20:34
and 20:40, without a restart. If a session does not pick them up, restart it.

A subagent's compaction fires the same hooks with `agent_id` and `agent_type` in the payload
while `transcript_path` still names the parent's transcript, and the summarizer ignores the
hook's instructions there. Both hooks therefore skip subagents by default; with
`CLAUDE_CHECKPOINT_SUBAGENTS=1` they write files tagged `-agent-<id>` that the session's own
recovery never mistakes for its checkpoint.

Environment knobs (all optional):

| variable | default | meaning |
|---|---|---|
| `CLAUDE_CHECKPOINT_DIR` | `~/.claude/checkpoints/<project>` | where checkpoints and summaries go |
| `CLAUDE_CHECKPOINT_ROOTS` | none | extra repositories to inspect, separated by `;` |
| `CLAUDE_CHECKPOINT_KEEP` | 40 | files kept per folder |
| `CLAUDE_CHECKPOINT_QUIET=1` | off | write the checkpoint, print no instructions |
| `CLAUDE_CHECKPOINT_SUBAGENTS=1` | off | also checkpoint subagent compactions, in files tagged `-agent-<id>` |
| `CLAUDE_CHECKPOINT_INSTRUCTIONS` | built-in text | a file with your own instructions; `{path}` is replaced |

The watcher needs Herdr (it asks `herdr agent list` for the session id and the idle state of
each pane) and reads the session transcripts under `~/.claude/projects`:

```
python scripts/compact-at-boundary.py --status                  one pass, print the decision table
python scripts/compact-at-boundary.py --once                    one pass with real submissions, then exit
python scripts/compact-at-boundary.py --dry-run                 loop, log decisions, never submit
python scripts/compact-at-boundary.py --titles "orques|orchestr"  watch the panes named like that, real submissions
python scripts/compact-at-boundary.py --sessions f2109a6d       watch one session by id prefix
python scripts/compact-at-boundary.py --panes w3:p1             watch one pane id (one-off runs only, see below)
python scripts/compact-at-boundary.py --threshold 0.7 --idle 120 --cooldown 1200
python scripts/compact-at-boundary.py --idle-states idle        count only Herdr's idle, not done
python scripts/compact-at-boundary.py --stop                    ask the running watcher to exit
```

**Select by name, not by pane.** Herdr renumbers workspaces when it restarts (`w3:p1` became
`w4:p1` overnight and the watcher sat waiting on a pane that no longer existed), and a pane id
means nothing on another machine. A session's stable identity is its name: `claude --name
orchestrator` at launch, or `/rename orchestrator` inside the session, writes a `custom-title`
row to the transcript, shows the name in the prompt box, the resume picker and the terminal
title, and the account launcher passes `--name <role>` by itself (`cc work -o orchestrator`
names the session `orchestrator`; `-- --name x` overrides). `--titles` is a regex matched, in
this order, against that transcript name, Herdr's agent name (`herdr agent rename`), the tab
label the launcher sets on `-Tab` launches (`cc-<account>-<role>`) and the terminal title.
`herdr pane rename` sets a label that `agent list` does not return, so it is not matched.
Convention: the launcher's roles, `orchestrator`, `lane` and `research`, with `-- --name
lane-<feature>` when several lanes run at once; an unrelated name matches nothing
and the watcher says so once (`no Claude pane matches --titles ...; Herdr knows ...`) and
keeps polling until a pane with that name appears.

One trap when submitting by hand: Git Bash rewrites a leading slash, so `herdr agent prompt
w1:p5 /compact` arrives as `C:/Program Files/Git/compact` and the session answers it as a
question. The watcher submits through Python and is not affected; from a shell use PowerShell
or set `MSYS_NO_PATHCONV=1`.

Run it in a spare pane where its log is visible, or hidden:

```powershell
Start-Process -WindowStyle Hidden python -ArgumentList '"C:\path\to\claude-base\scripts\compact-at-boundary.py"','--titles','"orques|orchestr"'
```

It keeps one instance through a lock file (the lock records a pid and is checked against a
live process, so a stale lock from a reboot does not block the next start), logs to
`~/.claude/compact-at-boundary.log`, and survives the death of any Claude session because it
is not one. It also survives Herdr going away: each pass logs `herdr agent list failed` and
the loop keeps polling until Herdr is back. What it does not survive is a logoff or a reboot;
register it as a logon task once:

```powershell
$action  = New-ScheduledTaskAction -Execute 'pythonw.exe' -Argument '"C:\path\to\claude-base\scripts\compact-at-boundary.py" --titles "orques|orchestr"'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask -TaskName 'compact-at-boundary' -Action $action -Trigger $trigger -Force
```

`pythonw.exe` runs without a console window; give its full path if it is not on PATH. Starting
before Herdr is up is fine for the reason above. `Unregister-ScheduledTask -TaskName
'compact-at-boundary' -Confirm:$false` removes it.

Defaults: window 200000, threshold 0.65, idle 90 seconds, cooldown 900 seconds, interval 30
seconds. Set `--window 1000000` only if the session really runs with the 1M window; the
threshold is a fraction of that number.

**Replacing a session.** `claude -c` or `-r` reopens a session with the context it had, the
summary and everything after it, so it costs what the session cost at that point and continues
the same trajectory. A new session that is handed the newest checkpoint and summary starts a
few thousand tokens in. When a session has to go (a version upgrade, a window that died, a
context that drifted), cut over at a committed checkpoint: commit or list the uncommitted work,
close, open new, and give it the paths of the newest files under `~/.claude/checkpoints/<project>/`
as its first prompt. `compact-recover.py` only runs after a compaction, so a fresh session gets
that pointer from you. Nothing in the old transcript is needed after that.

## 9. What was refuted, and why

- **"Let the model write a brief before compaction."** The model would write it mid-task, at
  5x per token, and it cannot know what the summary will lose. A checkpoint computed from disk
  costs nothing and cannot be wrong about git state. The model's own last words are captured
  from the transcript for free.
- **"Let the orchestrating session watch its own context."** A session polling itself pays a
  turn per poll and dies with the process it is trying to protect. The watcher is a process; a
  session may *launch* it into a pane once, and then it lives on its own.
- **"A shell script would be simpler."** The inputs are JSON (Herdr, transcripts, hook
  payloads) and the environment is Windows, where PowerShell 5.1 JSON handling and shell
  quoting have already cost real damage. The hooks were Python already.
- **"Disable auto-compaction and compact by hand."** A session that never goes idle would hit
  the hard limit mid-task. The watcher picks a better moment; auto-compaction stays the ceiling.
- **"Ride the 1M window and compact less."** Section 2. The tax is per turn, and cache breaks
  at 500k are thirty turns each.
- **"Raise the trigger so more of the window is usable."** `CLAUDE_CODE_AUTO_COMPACT_WINDOW`
  can do it, and the report will show it costs a few percent more per turn, because the last
  third of a cycle is its most expensive part. The usable share of the window is not the cost.

## 10. Validation status and limits

- Offline: `claude/hooks/tests/test-compaction-hooks.py` (15 tests, the hooks run as
  subprocesses against a throwaway repository) and `scripts/tests/test-compaction-tools.py`
  (14 tests, synthetic transcripts and a fake Herdr).
- Live, end to end, on 2026-08-27 with Claude Code 2.1.250 in a throwaway session under
  Herdr: the watcher submitted `/compact` through `herdr agent prompt` at 27 percent of the
  window (53,091 tokens, idle), the pane went `working`, the PreCompact hook wrote a
  1,483-byte checkpoint, the summarizer followed the seven-item structure of the instructions
  (2,310 characters, checkpoint path cited), the PostCompact hook saved the summary 11 seconds
  after the submission, and Herdr reported the session as `done` afterwards, which is why
  `done` counts as a boundary state.
- Live, the hooks alone: two 2.1.248 sessions that were already running when the hooks were
  wired ran them at their next compactions (a subagent's at 20:34, a session's own at 20:40),
  and the session's recovery text after compaction carried the checkpoint path and its
  disk-truth section.
- The report's exact figures come from the `compactMetadata` rows that 2.1.248 writes at each
  boundary. For the orchestrating session of that day: eight compactions of 108 to 163 seconds
  each, 1,025 seconds in total (median 127), each keeping 11,520 to 16,871 tokens (the summary
  plus a preserved tail of recent messages) out of 163,000 to 175,000.
- The checkpoint hook finishes in about two seconds on a folder with fourteen repositories; it
  stops inspecting after twelve seconds so the harness timeout never hits it.
- Not yet observed: a watcher-driven compaction of a busy session at the 65 percent threshold,
  and the payload shapes on versions after 2.1.250.
- The cost weights are relative list prices; on a subscription the currency is quota, and the
  same weights are what the quota meters are believed to track. Measure your own day.
