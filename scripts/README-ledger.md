# Ledger, twice a day

`ledger-day.py` reads a time window of the Claude Code transcripts, the quota log written by the status line, and the merge commits of one repository, then appends one row per project to `<ledger>/ledger.md` and a full breakdown to `<ledger>/daily/`. The row answers two questions: quota points per merged feature, and how much of the window went to waste (forks, status readers, narration turns, probes, cache breaks). It builds nothing else: no brake, no alert, no notification.

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

The page never invents a measurement: a KPI whose instrument did not exist in one of the two windows reads `n/m` with a footnote saying why, and quota points fall back to a labelled estimate from raw tokens when the window has no meter samples. `scripts/tests/test-ledger-compare.py` covers the four verdict states, the insufficient-sample path, a weekly meter that resets inside the window, and the pre-cap row.
