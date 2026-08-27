# Ledger, twice a day

`ledger-day.py` reads a time window of the Claude Code transcripts, the quota log written by the status line, and the merge commits of one repository, then appends one row per project to `<ledger>/ledger.md` and a full breakdown to `<ledger>/daily/`. The row answers three questions: quota points per merged feature, how much of the window went to waste (forks, status readers, narration turns, probes, cache breaks), and what the orchestrator context cost. The last one is two cells read from the main transcripts only: compactions with their rate per active hour and the peak before and floor after each one, and writing volume, the characters the session itself typed next to the characters it received back from its agents and its tools. The same two are pooled per side by `ledger-compare.py` and printed in the optimize list, whether or not they reach the top of the ranking. It builds nothing else: no brake, no alert, no notification.

Copy `ledger-config.example.json` to `ledger-config.json` and set the project mapping, the repository whose merges count and its branch. Point `CLAUDE_LEDGER_DIR` at the folder that holds `usage-log.csv` and will hold `ledger.md`. Python 3.12 or newer, no packages. The official `session-report` analyzer is used when it is installed under `~/.claude/plugins/marketplaces` and skipped with a message when it is not, in which case the cache-break bucket reports `not measurable` instead of a zero.

Register the two runs on Windows, as the current user and not as SYSTEM:

    schtasks /Create /SC DAILY /ST 08:00 /TN "Ledger\ledger-0800" /F /RL LIMITED /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\path\to\scripts\ledger-nightly.ps1"
    schtasks /Create /SC DAILY /ST 18:00 /TN "Ledger\ledger-1800" /F /RL LIMITED /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\path\to\scripts\ledger-nightly.ps1"

Verify with `schtasks /Query /TN "Ledger\ledger-0800" /V /FO LIST` and read `Next Run Time`.

On Mac and Linux the same two runs are cron entries; the window is computed from the clock the same way, so both lines are identical:

    0 8 * * * /usr/bin/python3 /path/to/scripts/ledger-day.py --since "$(date -d 'yesterday 18:00' '+\%Y-\%m-\%d \%H:\%M')" >> "$CLAUDE_LEDGER_DIR/nightly.log" 2>&1
    0 18 * * * /usr/bin/python3 /path/to/scripts/ledger-day.py --since "$(date '+\%Y-\%m-\%d 08:00')" >> "$CLAUDE_LEDGER_DIR/nightly.log" 2>&1

On macOS `date -d` does not exist; use `date -v-1d '+%Y-%m-%d 18:00'` in the 08:00 line. Percent signs are escaped because cron treats a bare `%` as a newline.

## Before and after

`ledger-compare.py` answers the question the rows alone do not: is the current way of working better or worse than the window before the change, in what and by how much. Freeze that window once with `python ledger-compare.py --freeze-baseline <ledger>/baseline.json` over a `ledger-day.py --json` payload of it, then every run renders `<ledger>/compare.md`: one table per axis (cost, speed, quality) with the columns `KPI | baseline | current | delta | verdict`, a recommendation, and the ranked list of what is left to optimize. Every KPI is a rate or a share, never a raw count, so two windows of different length stay comparable; the absolute totals live only in the header.

The `compare` block of `ledger-config.json` holds the baseline file and its label, the cap start that separates the two sides (sessions that began before it are printed in a `pre-cap` row, never counted), the thresholds a delta must reach to read better or worse, the minimum merged features a per-feature verdict needs, and the four evidence paths (gate exit files, landings, owner reports filed after a handoff, device kit). The nightly script runs the compare after the rows, so the page is never older than the ledger; on cron, add a second line calling `ledger-compare.py` right after `ledger-day.py`.

## Retention

The morning run ends with a retention sweep, after the three steps so nothing the ledger reads is deleted before it reads it, and the run is told apart by the same clock test that picks the window, so neither scheduled task needs an argument. Rule A removes a `<scratch_root>/<project>/<session>/scratchpad` whose newest file at any depth is older than `scratch_days`, dating an empty one by its own folder stamp or the session folder's, whichever is newer, so a session that has written nothing yet survives, and takes the `<session>` folder with it when nothing else is left inside; rule B removes a subagent transcript under `<projects_root>/<project>/<session>/subagents` older than `subagent_days` by its own mtime along with the `agent-*.meta.json` sidecar of the same stem, sweeps a sidecar whose transcript is already gone on its own mtime, and never touches the main `<session>.jsonl` beside that folder or a memory folder. The four values live in the `retention` block of `ledger-config.json`.

Each rule logs one line to `<ledger>/nightly.log` with the counts and the bytes freed, deletes one item at a time so a file held open by a live session costs one error instead of the sweep, and skips a folder that holds a junction or a symlink. Check what a sweep would do with `powershell -NoProfile -File scripts\ledger-nightly.ps1 -RetentionOnly -RetentionDryRun`, drop it for a run with `-SkipRetention`, and cover it with `scripts/tests/test-ledger-retention.ps1`, which builds a fixture of old and new scratchpads and transcripts under TEMP and asserts the exact set of survivors.

The page never invents a measurement: a KPI whose instrument did not exist in one of the two windows reads `n/m` with a footnote saying why, and quota points fall back to a labelled estimate from raw tokens when the window has no meter samples. `scripts/tests/test-ledger-compare.py` covers the four verdict states, the insufficient-sample path, a weekly meter that resets inside the window, and the pre-cap row.
