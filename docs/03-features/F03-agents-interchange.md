# F03: AGENTS.md interchange and capability matrix

Satisfies: FR-020, FR-021, FR-025, FR-026, FR-034. Stories: US-004. Depends on: ADR-003, ADR-008, ADR-010.

## Summary

Make the project kit multi-agent at the source: AGENTS.md becomes the canonical project instruction file with managed sections, CLAUDE.md becomes a thin import, the user-scope rules gain marked sections so adapters can render them into other agents' user files, and the capability matrix becomes the single place that claims support.

## Behavior

- Project kit restructure: `project-template/AGENTS.md` carries the shared working rules (`cb:rules`), the persistent-learnings section (`cb:learnings`, append-only, with a short convention header telling any agent to read it first and append dated one-line lessons), and the pipeline entry points (`cb:pipeline`). `project-template/CLAUDE.md` becomes `@AGENTS.md` plus Claude-only notes (skills, hooks, subagent roles). `CLAUDE.project.md` (the profile) is referenced from AGENTS.md so every agent reads the profile too.
- Roles-by-model orchestration: the template documents the pattern (an orchestrator session plans, validates, and owns git; implementation and design run in subagents that read the specs; agents never run git) in AGENTS.md's pipeline section, phrased agent-neutrally (FR-034).
- User scope: `claude/CLAUDE.md` gets the same marker treatment; the shared sections (voice, working principles, deploy honesty) are the render source for `~/.codex/AGENTS.md` and the OpenCode and Cursor user files, applied by adapters during install, preserving user content outside markers (FR-021).
- Capability matrix: `docs/AGENTS-SUPPORT.md` per the contract in `data-contracts.md`, seeded with Claude Code (Tier 1) and the Tier 2 columns as adapters land. The README table links here and contains no support claims of its own (FR-025).

## Edge cases

- A project that already has an AGENTS.md: project scaffold refuses to overwrite, writes `.new`, and prints marker-adoption instructions.
- An agent that reads both AGENTS.md and its own file: adapters must not double-inject; the matrix notes which file wins.
- Learnings section growth: the convention limits entries to one line each and directs consolidation into the profile or rules when a lesson becomes policy.

## Out of scope

The adapters themselves (F04, F05, F06), automated learnings dedup, MCP server recommendations (backlog).

## Acceptance

- A scaffolded demo project shows Claude Code (via import), Codex, and OpenCode (native) applying a rule edited only in AGENTS.md.
- A learning appended under one agent is visibly honored by another agent's next session.
- Marker round-trip test: render, hand-edit outside markers, re-render, hand edits intact.
- The matrix exists, is linked from the README, and every cell of the Claude column is `full`.
