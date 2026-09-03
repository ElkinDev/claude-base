# Context economics: what a turn costs, what compaction costs, and when to compact

This document answers a question that comes up as soon as someone runs Claude Code all day
with a large context window: *why compact at all, and why at a fixed level?* It gives the cost
model, the numbers measured on real transcripts, the compaction mechanics of Claude Code as
verified in version 2.1.248, and the design of the small toolkit in this repo that compacts at
a sensible moment and loses nothing when it does. Everything here costs zero model tokens to
run: the hooks and the watcher are plain Python reading local files and the Herdr CLI. The numbers below
were measured on one working day (2026-08-27) with Claude Code 2.1.248. Versions change; re-run
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

So the weighted cost of one turn is about `0.1 x context_re_read + 1.25 x new_context +
1 x uncached + 5 x output`. Two consequences drive everything else:

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
largest waste a heavy user can have, ahead of everything a memory tool could save. The
cumulative content of a session is not what you pay for; the content re-sent per turn is. Six
compactions in a day do not add up to 1M of paid context, they keep each turn near 100k.

## 3. What compaction costs and what it saves

Measured on the orchestration session (seven compactions in one day):

- Trigger at 156k to 167k tokens (78 to 83 percent of the 200k window).
- Floor right after: 56k to 77k (28 to 38 percent), climbing 10k to 15k in the first turns and
  83k to 130k by turn twelve, because the harness re-attaches the five files most recently touched
  with the Read, Write or Edit tools when each is under about 12 KB ("Called the Read tool with the
  following input", not configurable) and the recovery hook asks for re-reads.
- Dropped per compaction: 80k to 110k tokens. Summary: 14k to 23k characters.
- Overhead of one compaction, weighted: about 130k (the old context read once at 0.1, the
  summary written at 5, the floor re-cached at 1.25). That is six ordinary turns.

A cycle of 40 to 60 turns at an average of 118k costs about 20k to 25k weighted per turn.
Without compaction the same turns would ride at 300k to 500k and cost three to four times more.
The seven compactions together were roughly 7 percent of the day's spend and avoided about
70 percent of it. Compaction is not the waste; the level it happens at is a second-order detail
(section 6), and the *moment* it happens at is what the toolkit fixes.

Why the working band looks small: a trigger at 83 percent and a floor at 38 percent leave about
45 percent of the window to work in. "More than half the window is lost to compaction" is true
as a share and irrelevant as a cost: what every turn pays for is the floor (section 5).

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
| PreCompact hook | receives `session_id`, `transcript_path`, `cwd`, `trigger` (`manual` or `auto`) and `custom_instructions`. Its **stdout is joined into the custom instructions of the summarizer**, for manual and automatic compactions alike, so a debug line printed there steers every summary. Exit code 2 cancels the compaction. A subagent's compaction runs the hook too, with `agent_id` and `agent_type` added and the parent's `session_id` and `transcript_path`, but its summarizer ignores the instructions. |
| PostCompact hook | receives the same fields plus `compact_summary`. Its stdout is only a display message, never context. |
| SessionStart hook, matcher `compact` | runs right after compaction; its stdout becomes context. This is where recovery pointers belong. |

Both window variables are still there in 2.1.258, verified by reading the installed binary:

```
if(process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW){let B=kte("CLAUDE_CODE_AUTO_COMPACT_WINDOW",process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW,yCe,KUe);
CLAUDE_CODE_AUTO_COMPACT_WINDOW is set and takes precedence. Unset it to change this setting.
function zN(){return a.CLAUDE_CODE_DISABLE_1M_CONTEXT}
```

So the variable wins over the `autoCompactWindow` setting, and the setting is the same knob
under another name. The account launcher wires both (`docs/ACCOUNTS.md`): `orchestrator` and
`lane` get `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` and lose any inherited
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` with it, on the pane command and on the in-window path alike,
so a pane opened from a capped pane does not keep a window the cap contradicts; `research` is
left uncapped, inherited window included. `cc <account> -Window 260000` is the opt-in exception:
it drops the cap, which would hold the window at 200k, and sets the variable to 260000. The name
is `-Window` and not `-CompactWindow` because PowerShell binds by unambiguous prefix and an
unbound flag is forwarded to claude, so a name starting with `c` swallows claude's own `-c`.
`cc -ShowEnv` prints what a launch would set and forward without opening a session, which is how
`scripts/tests/test-launcher-env.ps1` asserts all of it.

Raising the window buys fewer compactions, not cheaper turns: at 260k a cycle drops roughly a
third of the compactions of a 200k session, so a day of nine cycles at about 21600 tokens of
overhead each, 194400 in total, loses three of those cycles and saves around 65000 tokens not
spent on summaries and re-orientation. It is paid for with a larger floor on every turn of the
cycle and with more expensive cache breaks (section 2), which is why it is off unless you ask
for it, and why section 9 keeps "ride the 1M window" refuted: this is a step, not the ceiling.

## 5. Where the floor comes from

The context right after compaction, on this setup, was about 65k to 77k tokens:

| part | size | yours to change? |
|---|---|---|
| harness system prompt and tool schemas | about 30k, cached | no |
| the summary | 4k to 6k | length, through the PreCompact instructions |
| the preserved tail: recent messages the harness keeps verbatim, plus re-injected recently read files | 8k to 13k (`postTokens` in the boundary row, minus the summary) | only by reading fewer large files before compaction |
| user CLAUDE.md, memory index, skill descriptions, MCP tool names | 5k to 8k | yes, and it is the smallest part |
| recovery hook output | 2000 characters at most, which is roughly 500 tokens | yes |

The floor is paid on every turn of every cycle, so it is a first-order lever, but the part you
own is a few thousand tokens: trimming a memory index from 3k to 2k is worth doing once, and it
is not where the day goes.

Skills are the other habit in that table. Every skill a pane has loaded is restored in full at
each compaction and stays restored for the life of the session: six artifact skills measured at
8704 tokens per compaction (commit-message 1980, pr-description 1965, adversarial-review 1438,
evidence-report 1169, story 956, devops-work-item 900), on a pane that had delegated the writing
to lanes. So an orchestrator does not invoke artifact skills at all; the lane that writes the
artifact invokes the one it needs, pays for it inside its own window, and ends.

The re-attached files are the one line of that table a habit can move. After every compaction the
harness re-attaches the five files most recently touched with the Read, Write or Edit tools, whole,
when each is under about 12 KB (the largest seen attached was 11,755 characters; a 13,278-byte file
came back as a path reference). Over one day of the orchestration session that was 8.2k tokens per
cycle, and all of it was the throwaway scripts the pane had just written to record a round, 4 to
9 KB each; the briefs and reports are larger and come back as references. The same bytes read with
`sed -n` or `cat` arrive as tool output, which the summary replaces instead of carrying. So an
orchestrator, which reads a lot and edits almost nothing, reads with the shell, records with
`tools/record.py` (payload on stdin, nothing written through the Write tool), and uses the Read
tool only on a file over 12 KB or as the one-line read that lets Edit work on it; a lane, which
opens a file to change it, keeps the Read tool. `claude/hooks/guard-read.py` is
wired to both routes for that reason: with the `Read` matcher alone, moving the reads to the
shell would move them past the image and large-file guard too. Which files a command prints, and
whether a pipe, a redirect or a substitution keeps those bytes out of the window, is parsed in
`claude/hooks/shell_read.py`.

## 6. The right moment: an optimum band, and why the level is second order

Let `F` be the floor, `g` the average context growth per turn, `O` the weighted overhead of one
compaction and `cr` the cache-read price (0.1). A cycle that runs from `F` to `F + B` lasts
`B / g` turns. Averaged over the cycle, each turn pays the carry of the band, about
`cr x B / 2`, plus its share of the next compaction, `O x g / B`. Minimizing the sum gives
`B* = sqrt(2 x O x g / cr)`, which is `sqrt(20 x O x g)` with `cr = 0.1`.

With the measured day (402 turns, eight compactions): `O = 126k`, `g = 1.6k` per turn and
`F = 68k`, so `B* = 63k` and the optimum trigger is about 131k tokens, 65 percent of a 200k
window. The report prints this figure from your own numbers.

The curve is flat around the optimum: the variable part is 6.4k weighted per turn at `B*`,
and 8k at half or twice `B*`, a 7 percent difference on a 22.3k turn, under 3 percent between
`0.85 B*` and `1.6 B*`. The trigger level is second order; the first-order levers, in the
measured order of size, are:

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
boundary* instead of mid-task saves the rework of a summary that cut a task in half, which is
not measurable in tokens because it shows up as wrong work. The compaction is dead time too:
the measured day spent 1,025 seconds inside its eight compactions, about two minutes each.

## 7. The toolkit

Five pieces, all zero-token, all in this repo:

| piece | event | what it does |
|---|---|---|
| `claude/hooks/precompact-checkpoint.py` | PreCompact | writes a checkpoint file with git truth (branch, tip, uncommitted files, worktrees) for the current repository or every repository directly under `cwd`, the subagents of the session with their last words, and the last text of the dialogue; then prints the summarization instructions (keep hashes, ids, paths and the next step verbatim; point at the checkpoint for git state). Never blocks. Compactions of a subagent (payload with `agent_id`) are skipped unless `CLAUDE_CHECKPOINT_SUBAGENTS=1`. |
| `claude/hooks/postcompact-persist.py` | PostCompact | saves the summary the harness produced next to the checkpoint. Prints nothing. |
| `claude/hooks/compact-recover.py` | SessionStart (compact) | after compaction, injects the checkpoint path and its disk-truth section (capped), plus the pointers to notes, brief and landings. Facts only, no instruction to re-read anything, since that instruction is paid for on every compaction and is obeyed even when the summary already has the answer. Under 2k characters. |
| `scripts/compact-at-boundary.py` | a process, not a hook | watches the Claude sessions Herdr knows; when one is above a threshold of its window *and* has been waiting for input for a while (Herdr `idle`, or `done`, which is what Herdr reports right after a compaction), submits `/compact` to that pane, once, then holds that session until its transcript grows a new turn, so a submission that produced nothing is not repeated at the same number. Auto-compaction stays armed as the ceiling. |
| `scripts/compaction-report.py` | on demand | the measurement behind this document, for your transcripts. |

Data flow of one compaction with the toolkit on:

```
watcher sees idle + 65%  ->  /compact  ->  PreCompact: autosave (dialogue tail), worklog snapshot,
checkpoint (git truth + subagents + last words) and its instructions  ->  the summarizer runs
with those instructions  ->  PostCompact: summary saved next to the checkpoint  ->  SessionStart
(compact): recovery text points at the checkpoint  ->  the session continues at the floor
```

Where the files land: checkpoints and summaries go to `~/.claude/checkpoints/<project>/`
(`CLAUDE_CHECKPOINT_DIR` overrides), outside every repository, so they need no ignore rule. The
autosave is the exception. It goes to `NOTES.autosave.md` in the session's `cwd`, usually a
repository, and it is a dump of the last messages, not something to commit, so the hook makes
git ignore it before writing it: when `git check-ignore` does not already cover the name, it is
appended to `.git/info/exclude`, the repository's local list shared by every worktree, never to
the team's `.gitignore`. Nothing to remember before the first compaction, and `git add -A` does
not pick the file up. The old habit of a hand-kept `NOTES.md` is gone: no hook reads or writes
one.

Nothing in the flow asks the model to write anything before compaction: the checkpoint is
computed from disk, which is cheaper and more reliable, for the reasons in section 9.

## 8. Install and use

The hooks install with the rest of the toolkit (`install.ps1`); `claude/settings.json` wires
them, per config directory: with account profiles (`docs/ACCOUNTS.md`) the default directory is
the only one that has them until each profile is synced with `cc <profile> -p -s`, and a session
on an unsynced profile compacts with no checkpoint and no warning. To wire them by hand, copy
the blocks from `claude/settings.json` rather than retyping them: PreCompact runs
`precompact-autosave.py` and then `precompact-checkpoint.py` in that order, PostCompact runs
`postcompact-persist.py`, and SessionStart with the matcher `compact` runs `compact-recover.py`,
each as `python "%USERPROFILE%/.claude/hooks/<name>.py"` with a timeout of 15 to 20 seconds.

A change reaches sessions that are already running: on 2.1.248, two processes started at 18:45
and 18:56 ran hooks wired into `settings.json` at 20:29, at their compactions of 20:34 and
20:40, without a restart. If a session does not pick them up, restart it.

A subagent's compaction fires the same hooks with `agent_id` and `agent_type` in the payload
while `transcript_path` still names the parent's transcript, and its summarizer ignores the
hook's instructions, so both hooks skip subagents by default; with
`CLAUDE_CHECKPOINT_SUBAGENTS=1` they write files tagged `-agent-<id>` that the session's own
recovery never mistakes for its own.

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
each pane) and reads the session transcripts under `~/.claude/projects`. Its switches are in
`--help` and in the module docstring; the ones in daily use: `--status` for one pass and the
decision table, `--dry-run` to loop and log without submitting, `--titles` to choose the panes,
`--threshold`, `--idle`, `--cooldown` and `--interval` for the numbers (defaults: window 200000,
threshold 0.65, idle 90 seconds, cooldown 900, interval 30), `--idle-states idle` to count only
Herdr's idle and not `done`, and `--stop` to ask a running watcher to exit. Set `--window 1000000`
only if the session really runs with the 1M window; the threshold is a fraction of that number.

**Select by name, not by pane.** Herdr renumbers workspaces when it restarts (`w3:p1` became
`w4:p1` overnight and the watcher sat waiting on a pane that no longer existed), and a pane id
means nothing on another machine. A session's stable identity is its name: `claude --name
orchestrator` at launch, or `/rename orchestrator` inside the session, writes a `custom-title`
row to the transcript and shows the name in the prompt box, the resume picker and the terminal
title; the launcher passes `--name <role>` by itself (`cc work -o orchestrator`, and
`-- --name x` overrides). `--titles` is a regex matched, in this order, against that transcript
name, Herdr's agent name (`herdr agent rename`), the tab label of a `-Tab` launch
(`cc-<account>-<role>`) and the terminal title; `herdr pane rename` sets a label `agent list`
does not return, so it is not matched. Convention: the launcher's roles, plus `-- --name
lane-<feature>` when several lanes run at once. A name that matches nothing is reported once and
then polled for until it appears.

One trap when submitting by hand: Git Bash rewrites a leading slash, so `herdr agent prompt
w1:p5 /compact` arrives as `C:/Program Files/Git/compact` and the session answers it as a
question. The watcher submits through Python; from a shell, PowerShell or `MSYS_NO_PATHCONV=1`.

Run it in a spare pane where its log is visible, or hidden with `Start-Process -WindowStyle
Hidden python -ArgumentList '"C:\path\to\claude-base\scripts\compact-at-boundary.py"','--titles','"orques|orchestr"'`.

It keeps one instance through a lock file (a pid checked against a live process, so a stale
lock from a reboot does not block the next start), logs to `~/.claude/compact-at-boundary.log`,
and survives the death of any Claude session because it is not one, and Herdr going away: each
pass logs `herdr agent list failed` and polls until Herdr is back. It does not survive a logoff
or a reboot; register it as a logon task once:

```powershell
$action  = New-ScheduledTaskAction -Execute 'pythonw.exe' -Argument '"C:\path\to\claude-base\scripts\compact-at-boundary.py" --titles "orques|orchestr"'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask -TaskName 'compact-at-boundary' -Action $action -Trigger $trigger -Force
```

`pythonw.exe` runs without a console window; give its full path if it is not on PATH, and
starting before Herdr is up is fine for the reason above. `Unregister-ScheduledTask -TaskName
'compact-at-boundary' -Confirm:$false` removes it.

**One submission per position.** After a submission the watcher holds that session until its
transcript grows a new usage row, so a session that answers `Not enough messages to compact.` is
not asked again at the same number, and a prompt lost on the way or a pane that was busy looks
the same from outside and is held the same way: a failed submission is not retried, that session
is picked up after its next turn, and auto-compaction stays armed underneath. The cooldown is
the wait between two submissions to a session that did move on, and it does not grow. That is
what ends the 2026-09-02 pattern of five submissions at one number, four of them refused.

**Replacing a session.** `claude -c` or `-r` reopens a session with the context it had, so it
costs what that session cost and continues the same trajectory, while a new session handed the
newest checkpoint and summary starts a few thousand tokens in. When a session has to go (a
version upgrade, a window that died, a context that drifted), cut over at a committed
checkpoint: commit or list the uncommitted work, close, open new, and give it the newest files
under `~/.claude/checkpoints/<project>/` as its first prompt. `compact-recover.py` only runs
after a compaction, so a fresh session gets that pointer from you.

## 9. What was refuted, and why

- **"Let the model write a brief before compaction."** It would be written mid-task, at 5x per
  token, and the model cannot know what the summary will lose. A checkpoint computed from disk
  costs nothing and cannot be wrong about git state, and the model's own last words are taken
  from the transcript for free.
- **"Let the orchestrating session watch its own context."** A session polling itself pays a turn
  per poll and dies with the process it is trying to protect. The watcher is a process; a session
  may *launch* it into a pane once, and then it lives on its own.
- **"A shell script would be simpler."** The inputs are JSON (Herdr, transcripts, hook payloads)
  and the environment is Windows, where PowerShell 5.1 JSON handling and shell quoting have
  already cost real damage. The hooks were Python already.
- **"Disable auto-compaction and compact by hand."** A session that never goes idle would hit
  the hard limit mid-task. The watcher picks a better moment; auto-compaction stays the ceiling.
- **"Ride the 1M window and compact less."** Section 2. The tax is per turn, and cache breaks
  at 500k are thirty turns each.
- **"Raise the trigger so more of the window is usable."** `CLAUDE_CODE_AUTO_COMPACT_WINDOW`
  can do it, and the report will show it costs a few percent more per turn, because the last
  third of a cycle is its most expensive part. The usable share of the window is not the cost.

## 10. Validation status and limits

- Offline: `claude/hooks/tests/test-compaction-hooks.py` (18 tests, the hooks as subprocesses
  over a throwaway repository), `scripts/tests/test-compaction-decide.py` (9, the watcher's
  decision) with `test-compaction-tools.py` beside it (13, synthetic transcripts and a fake
  Herdr), `scripts/tests/test-guard-read.py` (45, the guard as a subprocess over both the Read
  and the shell route) and `scripts/tests/test-launcher-env.ps1` (the launcher's dry run,
  default and opt-in window).
- Offline: `claude/tools/tests/run-tests.py` (the record CLI as a subprocess over temporary files:
  line endings, BOM, idempotence, anchors, all-or-nothing rounds, config resolution).
- Live, end to end, on 2026-08-27 with Claude Code 2.1.250 in a throwaway session under Herdr:
  the watcher submitted `/compact` through `herdr agent prompt` at 27 percent of the window
  (53,091 tokens, idle), the pane went `working`, the PreCompact hook wrote a 1,483-byte
  checkpoint, the summarizer followed the seven-item structure of the instructions (2,310
  characters, checkpoint path cited), the PostCompact hook saved the summary 11 seconds later,
  and Herdr reported the session as `done`, which is why `done` counts as a boundary state.
- Live, the hooks alone: two 2.1.248 sessions already running when the hooks were wired ran them
  at their next compactions (a subagent's at 20:34, a session's own at 20:40), and the recovery
  text after compaction carried the checkpoint path and its disk-truth section.
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

## 11. The other meter: quota, and the pane that stops for two hours

Everything above is about the context window. A subscription account has a second meter that
stops work just as hard: a five hour usage window and a seven day one, each with a utilization
and the time it resets. When the five hour window fills, the session stops mid queue and stays
stopped until a person notices, even though the endpoint that reports the meter also announces,
to the minute, when it comes back. An overnight queue that hits the ceiling at 23:10 and reopens
at 01:00 loses the night to nobody being awake to press enter.

`scripts/quota-wake.py` is the answer, and it has the shape of the compaction watcher: a
resident process, zero model cost, that reads the meters through `scripts/usage-probe.py`, waits
for the announced reset plus a grace, confirms the meter recovered, and submits one resume prompt
to the stopped pane through `herdr agent prompt`. It wakes each pane at most once per reset,
keyed by the reset time itself, so a restart inside the same window never wakes a pane twice.

The rule that matters is the one that refuses. With the seven day meter at or above the cap the
operator set (`--cap`, default 80 percent), the account is left alone however dry the five hour
window is: a wake there does not buy work, it converts the rest of the week into unfinished work,
and crossing that line is the operator's decision, never the script's. A pane Herdr reports as
`working` is skipped for the same reason: it is still producing output, whatever the meter said a
moment ago.

Costs and limits: one HTTPS request per account per pass (default every 300 seconds, and the
wait to a known reset is exact rather than aligned to that grid), no model tokens, no request
while an account waits for a reset that has not arrived. The meters are read, never
extrapolated: the toolkit does not guess how many tokens are left before the window fills.
`scripts/usage-probe.py --csv` is the same row the nightly ledger has always appended, byte for
byte. The design, the states and the decisions behind the knobs are in
`docs/03-features/F14-quota-wake.md`.
