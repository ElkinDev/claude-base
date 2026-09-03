# F04: Codex CLI adapter

Satisfies: FR-022. Stories: US-005. Depends on: ADR-003, ADR-008, F03.

## Summary

Install the discipline into Codex CLI (OpenAI GPT models): user-scope rules into `~/.codex/AGENTS.md` managed sections, the skill pack into Codex's skill location, and the workflow entry points as Codex prompts, with every gap documented in the matrix.

## Behavior

- Mapping spec `adapters/codex/mapping.md` states, before implementation, the exact destinations verified against the installed Codex version at implementation time: rules to `~/.codex/AGENTS.md` (marked sections, merge-preserving), skills copied where Codex resolves SKILL.md folders, workflows (`story` and `sdd`, `/delivery:story` and `/delivery:sdd` from the marketplace, plus `explore-and-plan`, `tdd`, `quality-gates`, `definition-of-done`, `evidence-report`) exposed as prompt files so `/story`-equivalent invocations exist.
- Skill compatibility pass: each shipped skill is tagged in its frontmatter as portable or Claude-only (skills referencing Claude hooks, subagent tooling, or settings are Claude-only). The adapter installs only portable ones; the matrix lists the rest as gaps with workarounds (FR-022).
- Project scope needs nothing: Codex reads the project AGENTS.md natively (F03).
- Documented gaps expected: hooks (no equivalent event system; the worklog becomes manual via the worklog skill), statusline, permission overlay. Optional note: per-phase model profiles are a Codex-native feature the user may configure; the adapter documents it, does not manage it.
- Uninstall: the adapter's managed sections and copied folders are listed in UNINSTALL.md.

## Edge cases

- Codex absent at install: adapter skipped with a note unless `--agent codex` forces it (then files are laid down for a future install).
- Existing user `~/.codex/AGENTS.md` content: preserved outside markers.
- Codex version drift: the mapping spec records the verified version; CI smoke runs against a pinned version.

## Out of scope

Emulating hooks, managing Codex model profiles, MCP configuration (backlog).

## Acceptance

- On a machine with Codex, install then run: rules visibly applied in a Codex session, one ported skill invoked successfully, one workflow prompt runs its happy path.
- Matrix column filled with verified values, gaps included.
- Marker round-trip test passes on `~/.codex/AGENTS.md`.
