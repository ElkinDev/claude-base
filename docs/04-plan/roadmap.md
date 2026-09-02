# Roadmap

Sprints are scope units, not calendar units: a sprint is done when its definition of done is green, and sprints run strictly in order because each builds on the previous one's guarantees.

| Sprint | Theme | Features | Exit guarantee |
|---|---|---|---|
| 01 | Safety net and portable core | F10, F02, CI skeleton from F11 | The repo can never regress into leaking private content, and the core hooks run on three OSes |
| 02 | Cross-platform install | F01, F13, settings render, safe defaults (ADR-009), ps1 retirement | A fresh machine on any OS installs the engine with one command, and an existing repository keeps everything it already had |
| 03 | Unification | F07, F08, F09, docs/WORKLOG.md | The best of the private setups lives here, generic and guarded |
| 04 | Multi-agent interchange | F03, F04, F05 | One AGENTS.md drives Claude Code, Codex, and OpenCode |
| 05 | Cursor, docs, release | F06, F11 completion, F12 | v0.1.0 public, matrix honest, docs complete |

## Out of sprint

- F14 (quota wake, `03-features/F14-quota-wake.md`): built after the table above was written, so it belongs to no sprint yet. It adds two commands and three modules beside the compaction watcher, runs only when a person starts it, and nothing in the v0.1.0 surface depends on it. It stays a proposal until it is reviewed and merged, and it enters a sprint the day the maintainer schedules it, never silently.

## Releases

- v0.1.0: after Sprint 05, the first public release, scope per `00-product/vision.md`.
- v0.2.x: backlog-driven (candidate themes: more adapters, worklog for OpenCode via plugin, MCP recommendations, packaging).

## Standing rules for every sprint

- The sanitization guard is green locally (with the private denylist) before every commit, from Sprint 01 onward.
- The specs are updated in the same change when reality diverges from them.
- No sprint widens its scope without recording the change here and in the sprint file.
