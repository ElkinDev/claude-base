# F02: Portable hooks and statusline

Satisfies: FR-010, FR-011, FR-012, FR-013, FR-014, FR-015. Stories: US-001, US-002. Depends on: ADR-005, ADR-006.

## Summary

Port the six core PowerShell scripts (worklog-lib, worklog-context, worklog-snapshot, worklog-resolve, branch-check, branch-upstream-fix) and the statusline to single-source POSIX sh, under the behavior contracts in `data-contracts.md`, with parity tests. Fix the two known wiring gaps: markitdown registration and the split hook registry.

## Behavior

- Each ps1 behavior is first written down as a contract (already summarized in `data-contracts.md`; this feature finalizes any missing detail by reading the ps1 sources), then implemented in sh, then covered by parity fixtures. The ps1 files remain until Sprint 02 removes them after CI proof.
- Repo-id derivation keeps the md5 semantics for continuity with existing worklog data; the md5 command is resolved per platform inside worklog-lib with identical output (FR-010, contract in `data-contracts.md`).
- Base-branch list: hooks read git config `workflow.basebranches` (comma-separated) and fall back to `main,master,devel,HEAD` (FR-014). The worklog skill doc mentions the config key.
- Statusline: `statusline.sh` renders the same line as today (model, effort, folder, branch, dirty marker, added/removed, context bar with the same thresholds); glyphs stay configurable at the top of the script; output is raw UTF-8 (FR-011).
- Windows wiring: settings entries produced by F01's render phase invoke `{GITBASH} <script>`; macOS/Linux entries invoke the script directly.
- markitdown: under `--markitdown`, F01 registers the PreToolUse(Read) hook in the rendered settings; without the flag the file still ships but stays dormant and documented (FR-012).
- Hook registry: `docs/HOOKS.md` lists every hook with event, scope, wiring file, write surface, and OS support; the worklog skill and profiles link to it instead of describing wiring inline (FR-013).

## Edge cases

- Non-git directory or unborn HEAD: hooks stay silent and exit 0.
- Detached HEAD or base branch: worklog hooks skip (contract), branch hooks skip.
- Missing worklog directory: context hook silent; snapshot hook creates it.
- Concurrent sessions appending `sessions.jsonl`: single-line appends only, no read-modify-write.
- Windows paths with spaces and non-ASCII folder names: covered by parity fixtures.
- CRLF: `.gitattributes` forces LF on `*.sh`; shellcheck plus a checkout test in CI on Windows.

## Out of scope

New hook capabilities (nothing beyond today's behavior), worklog for non-Claude agents (backlog), removing ps1 (Sprint 02 task, after parity proof).

## Acceptance

- Parity suite: for each hook, the same fixture repo and event payload produce equivalent observable output (context JSON, jsonl lines, git config state) on Windows, macOS, and Linux, and match the ps1 behavior on Windows during the overlap.
- Statusline renders correct fields from recorded payloads on the three OSes within the 300 ms budget.
- `docs/HOOKS.md` covers exactly the hooks present in the repo; CI fails if a hook file exists without a registry entry.
