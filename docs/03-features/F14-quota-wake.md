# F14: Quota wake

Status: proposed 2026-09-02. Design only; no code until the maintainer approves the knobs listed under "Decisions pending".

Satisfies: FR-049 (proposed below). Stories: US-013 (proposed below). Depends on: ADR-002, ADR-006, ADR-007. Builds on the watcher of `docs/CONTEXT-ECONOMICS.md` section 7 (`scripts/compact-at-boundary.py`) and on the meters `scripts/ledger-nightly.ps1` already expects from `scripts/usage-probe.py`.

## Summary

A subscription account has two meters: a five hour window and a seven day window, each with a utilization and a reset time. An orchestrator that hits the five hour ceiling stops in the middle of its queue and stays stopped until a person notices, even when the seven day meter still has room and the window reopens two hours later. The quota wake is a resident script with zero model cost that reads both meters, notes when the account is dry, waits for the reset time the meter itself announces, confirms the meter came back, and submits one resume prompt to the stopped pane. It never wakes a pane when the weekly cap the operator configured is already reached, because a wake at that point only converts weekly quota into unfinished work.

Two files: `scripts/usage-probe.py`, the one shot reader of the meters that the nightly ledger already looks for beside itself and that today lives only in a private tree, and `scripts/quota-wake.py`, the resident loop. Both run on Windows, macOS and Linux with the standard library only.

## Behavior

- Data source, in this order. First the usage endpoint through `usage-probe.py --json`: the token is read from `.credentials.json` under `CLAUDE_CONFIG_DIR` (or `~/.claude`), sent once as a bearer header, and never printed or logged. Second, as the free fallback, the CSV the statusline writes when `$UsageLogDir` is set (`usage-log.csv`: `time,account,five_hour_used,seven_day_used,context_pct`), which costs no request but carries no reset time and goes stale when every session on the account is stopped, which is exactly the state this feature cares about. The probe is therefore the primary source and the CSV only refines the polling cadence.
- The probe grows a `--json` mode that prints the meters as a map of `name` to `utilization` and `resets_at`, in ISO 8601 with offset, plus the account name resolved the way `--csv` resolves it. `--csv` and the human lines stay byte for byte as the nightly ledger consumes them today.
- States per account, decided from the probe only: `ok` (five hour below 100 and seven day below the cap), `dry` (five hour at 100, seven day below the cap), `capped` (seven day at or above the cap, whatever the five hour meter says). The cap is `--cap <percent>`, default 80, the maintainer's own rule; `100` disables it.
- Pane selection: `--panes`, `--titles <regex>` or `--sessions <prefix>` exactly as the compaction watcher takes them, resolved through `herdr agent list` on every pass because Herdr renumbers panes. A pane is a wake candidate only when it belongs to the watched account, which is read from the pane's `CLAUDE_CONFIG_DIR` (the `-o`/`--name` launch of `claude/claude-account.ps1` exposes it) and, failing that, from `--account <name>` on the command line.
- The loop. Every `--interval` seconds (default 300) it probes once per watched account. On `ok` it records the reading and does nothing. On the first `dry` reading it logs the reset time announced by the meter and switches that account to a single wait: it sleeps until `resets_at` plus `--grace` seconds (default 120), probes again, and only if the meter now reads below `--resume-below` (default 50) and the state is not `capped` does it wake. If the meter is still at 100 after the reset, which happens when the reset time was rounded or the endpoint lags, it retries every `--interval` for at most `--retry-for` seconds (default 1800) and then goes back to plain watching and logs why.
- The wake is one `herdr agent prompt <pane> <text>` per candidate pane whose Herdr status is `idle`, `done` or `stalled`. A pane that is `working` is skipped and logged: it is not out of tokens, whatever the meter said a moment ago. The text is fixed and short, in English, and carries the facts the pane cannot know: the account, the reading before and after the reset, the seven day reading against the cap, the time, and the sentence "resume from your brief; measure before launching anything". `--prompt-file <path>` replaces the text for operators who keep a brief per pane.
- One wake per reset per pane, recorded in the state file with the reset time it answered, so a restart of the script inside the same window never wakes the same pane twice. The cooldown is the meter's own reset cycle, not a timer.
- `capped` never wakes and is logged once per pass with the reading, so the log shows the operator that the account was dry, came back, and was left alone because the weekly cap said so. Crossing the cap is the operator's decision, never the script's, which is the rule behind `daily-cap` in the private setups this generalizes.
- Process discipline copied from the compaction watcher: one instance per machine through a lock file under `~/.claude`, `--status` for one pass that prints the table (account, five hour, seven day, state, reset time, panes, last wake) and exits, `--dry-run` that decides and logs without submitting, `--once`, `--stop` through a stop file, a log capped at one megabyte with one rotation. Nothing here calls a model.
- The probe and the loop share the account resolution and the endpoint handling in one module, so a change of endpoint or header touches one file.

## Edge cases

- Endpoint error or no token: the pass is logged as `unknown`, the previous state is kept, nothing is submitted. Three consecutive unknowns are logged once at warning level; the loop never exits on its own because the outage usually is the rate limit itself.
- `resets_at` in the past on a `dry` reading (clock skew, a stale response): treat it as due now, apply the retry budget, never sleep on a negative interval.
- Reset time earlier than the next pass: the wait is exact to the announced time, not aligned to the polling interval, so a two hour window is not lost to a five minute grid.
- The pane compacts or restarts during the wait: the session id changes but the pane id may not, or the reverse; the wake targets whatever `herdr agent list` reports at wake time under the same selectors, and the state file keys by session id so a fresh session is a fresh candidate.
- No Herdr socket: the script says so in words on `--status` and every pass, exits 2 on `--once`, and keeps polling in the loop, because the multiplexer may simply not be up yet after a logon.
- Two accounts watched, one dry and one capped: independent state machines; the wake text names the account it is about, so a pane launched under a profile never gets another profile's numbers.
- The operator crosses the cap by hand after a `capped` reading: the script keeps refusing until `--cap` is raised, which is the intended friction.

## Out of scope

Choosing which account to launch a session under, rotating accounts, or starting a new Claude session in an empty pane: the wake only resumes a pane that already runs. Reading the meters from inside a session: that is the statusline's job. Any estimate of tokens left before the reset: the meters are read, never extrapolated. Waking on the seven day reset: it is logged, and the operator decides what a fresh week is for.

## Proposed requirement and story

- FR-049: A resident script with zero model cost watches the rate limit meters of one or more accounts, waits for the reset time the meter announces when the five hour window is exhausted, confirms the meter recovered, and submits one resume prompt per stopped pane, never when the seven day meter is at or above the configured cap.
- US-013 Resume an orchestrator after a quota reset without being at the keyboard: as an operator who runs a long queue overnight under a subscription account, I want the orchestrator prompted to continue when its five hour window reopens, only while the week still has room, so the queue finishes and the weekly cap I set is respected.

## Acceptance (to be written with the code)

- `scripts/tests/test-usage-probe.py` against a recorded endpoint payload: `--json` shape, ISO times with offset, account resolution with and without `CLAUDE_CONFIG_DIR`, no token in any output stream on success or failure.
- `scripts/tests/test-quota-wake.py` with a fake probe and a fake Herdr: `ok` never wakes; `dry` waits until the announced time plus grace, wakes once and records it; a second run inside the same window does not wake again; `capped` never wakes; a `working` pane is skipped; a reset time in the past applies the retry budget; endpoint errors keep the previous state; the lock refuses a second instance; `--dry-run` writes only the log.
- Sanitization guard green: no account name, path or email from the private setups in the code, the tests or the docs.

## Decisions pending (maintainer)

1. Cap semantics: refuse the wake at the cap (this design) or wake with a message that says the cap is reached and let the pane run its close.
2. Default polling interval: 300 seconds against the endpoint, or the statusline CSV as the primary source and the endpoint only around the reset time.
3. Scope of the wake target: the orchestrator pane only, or every idle pane on the account.
4. Where the resume text lives: fixed in the script, or a per-pane brief path passed on the command line.
