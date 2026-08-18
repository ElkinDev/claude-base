# F01: Cross-platform installer

Satisfies: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007. Stories: US-001, US-002, US-003, US-007. Depends on: ADR-001, ADR-004, ADR-005, ADR-006, ADR-009.

## Summary

Replace the Windows-only `install.ps1` with `install/install.sh` plus `install/install.ps1`, both thin executors of the shared `install/manifest.json` (contract in `data-contracts.md`). One documented command per OS installs the engine; flags scaffold projects and optional modules.

## Behavior

- Entry points: `./install/install.sh [flags]` on macOS/Linux; `./install/install.ps1 [flags]` on Windows (PowerShell 5.1+). Flags, identical on both: `--project <path>` (project scope), `--sdd`, `--testing`, `--coordination`, `--markitdown`, `--agent <id,...>` (default: detected agents), `--force`, `--dry-run`.
- Detection phase: OS, home dir, git presence (hard requirement), Git Bash absolute path on Windows (hard requirement, resolved from the git install, never bare `bash` on PATH), Python plus markitdown (only when `--markitdown`), Node (only when `--testing` or the cross-agent hooks module is selected), and installed agents (presence of `~/.claude`, `~/.codex`, OpenCode and Cursor config dirs).
- Plan phase: read the manifest, filter by OS, flags, and selected agents, print the resulting operation list. `--dry-run` stops here (FR-006).
- Execute phase, per operation type: `copy` merges directories and backs up differing existing files as `.bak` while writing the incoming version as `.new` for manual merge, or overwrites with backup under `--force`; `skip-existing` copies only absent files; `render` substitutes `{HOME}`, `{GITBASH}`, `{BASE_BRANCHES}` and writes the result; `register` updates marked sections in instruction files and hook entries in settings, touching nothing outside markers.
- User scope installs: rules, settings (rendered, safe defaults per ADR-009), hooks, skills, statusline, plus per-agent adapter mappings for every selected agent (F04, F05, F06).
- Project scope installs: AGENTS.md, CLAUDE.md, CLAUDE.project.md, `.claude/` wiring rendered for the OS, and the optional modules by flag.
- Verify phase: settings parse as JSON, hook files exist and are executable, statusline runs once against a sample payload, and a summary lists everything written, backed up, and skipped with reasons (for example markitdown skipped, Python absent).

## Edge cases

- git missing: fail before any write, with per-OS install instructions.
- Git Bash unresolvable on Windows: fail with instructions (ADR-005); never fall back silently.
- Home path with spaces: covered by quoting; a CI smoke test uses a spaced temp home.
- Existing user `settings.json`: never merged automatically; `.bak` plus `.new` with a printed diff hint (FR-003).
- Re-run after `git pull`: refreshed managed files only; equals fresh install (FR-005).
- Interrupted run: operations are individually idempotent; re-running completes the remainder.

## Out of scope

Uninstall automation (a documented manual list only, FR-007), package-manager distribution (backlog), agent binaries installation (non-goal).

## Acceptance

- Fresh-machine smoke test green on the three-OS CI matrix: install, verify phase passes, re-run is a no-op.
- `--dry-run` writes nothing (verified by hashing the home tree before and after).
- Project scaffold plus each optional flag produces exactly the manifest-declared files.
- UNINSTALL.md lists every path the manifest can write.
