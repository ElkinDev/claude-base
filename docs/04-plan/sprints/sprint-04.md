# Sprint 04: Multi-agent interchange

Goal: one AGENTS.md drives Claude Code, Codex, and OpenCode; support claims become a verified matrix. Features: F03, F04, F05.

## Scope

1. F03 project restructure: `project-template/AGENTS.md` with the three managed sections (rules, learnings, pipeline, including the roles-by-model text from Sprint 03), `CLAUDE.md` reduced to the `@AGENTS.md` import plus Claude notes, profile referenced from AGENTS.md, scaffold behavior for repos that already have an AGENTS.md.
2. F03 user scope: marker sections in `claude/CLAUDE.md`; the render tooling under `tools/render/` that adapters use; marker round-trip tests.
3. F03 matrix: `docs/AGENTS-SUPPORT.md` seeded with the Claude Code column at `full`.
4. Skill portability tagging: every shipped skill's frontmatter gains the portable/Claude-only tag that F04/F05/F06 consume.
5. F04 Codex adapter: `adapters/codex/mapping.md` verified against the current Codex release, rules render into `~/.codex/AGENTS.md`, portable skills copied, workflow prompts installed, matrix column filled, uninstall entries added.
6. F05 OpenCode adapter: `adapters/opencode/mapping.md` verified against the current OpenCode release, rules and commands and skills installed per the chosen mechanism, additive `opencode.json` merge with round-trip test, matrix column filled, uninstall entries added.
7. Demo project walkthrough (doc plus CI-checked script where feasible): scaffold, edit a rule in AGENTS.md once, show all three agents honoring it, append a learning under one agent and read it from another.

## Out of scope

Cursor (Sprint 05), MCP configuration, worklog automation for non-Claude agents, per-phase model management.

## Definition of done

- The demo walkthrough passes end to end on at least one machine per OS family with real Codex and OpenCode installs (versions recorded in the mapping specs).
- Marker round-trip tests green for the project AGENTS.md, `~/.codex/AGENTS.md`, and the OpenCode rules file; `opencode.json` install/uninstall round-trip equals the original.
- Matrix columns for Claude Code, Codex, and OpenCode filled with verified values and honest gaps; no support claim exists outside the matrix.
- Adapter install smoke jobs green on the CI matrix (pinned agent versions); UNINSTALL.md covers adapter writes.
- Every shipped skill carries its portability tag; Claude-only skills are listed as gaps with workarounds in the matrix.
