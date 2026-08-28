# Architecture overview

## Layers

1. Content (canonical source): `claude/` (engine: rules, settings, hooks, skills, statusline) and `project-template/` (project kit: CLAUDE.md, CLAUDE.project.md, AGENTS.md, `.claude/` wiring, agents, profiles, optional modules). Authored once, in Claude-native formats (ADR-003).
2. Tooling: `install/` (the two thin bootstrap installers plus the shared install manifest) and `tools/` (render helpers, parity test runner). Logic lives in the manifest and in single-source POSIX sh scripts (ADR-004, ADR-005). The sanitization guard shipped before this layout existed and lives in `scripts/` with the rest of the Python tooling (ADR-007, amended).
3. Adapters: `adapters/<agent>/` holds one mapping spec per supported agent (what canonical content lands where, in which format, merged how) plus any agent-specific shim files. Adapters never own content (ADR-003, ADR-008).
4. Quality: `.github/workflows/` CI matrix (lint, guard, install smoke, hook parity) and the release checklist (F11).

## Target repo layout (after implementation)

```
claude-base/
  claude/                  engine content (canonical)
    CLAUDE.md              user-scope rules, marked sections
    settings.json          template, rendered per OS at install
    statusline.sh          single-source statusline
    hooks/                 single-source sh hooks + optional markitdown-read.py
    skills/                the skill pack
  project-template/        project kit (canonical)
    AGENTS.md              canonical project instructions (template)
    CLAUDE.md              thin import of AGENTS.md + Claude extras
    CLAUDE.project.md      profile skeleton
    .claude/               project hook wiring, agents/
    profiles/              example profiles
    docs/                  SDD tree doc + coordination templates (optional module)
    testing/               evidence harness (optional module)
    .agents/hooks/         cross-agent Node hooks (optional module)
  adapters/
    claude-code/           reference mapping (identity)
    codex/  opencode/  cursor/
  install/
    install.sh  install.ps1  manifest.json
  tools/
    parity/  render/
  scripts/                 zero-token python tooling, including the sanitization guard
  docs/                    reference docs + this spec tree
  herdr/                   optional third-party integration notes
```

Existing files move toward this layout during the sprints; nothing moves without its sprint saying so.

## Install flow

1. Bootstrap: the user clones the repo and runs `install/install.sh` (macOS/Linux) or `install/install.ps1` (Windows). Both are thin: parse flags, detect OS and shells, then execute the shared manifest.
2. Plan: the installer reads `install/manifest.json`, filters operations by OS and flags, and prints the plan (`--dry-run` stops here).
3. Execute: operations are `copy` (merge-copy, backup on overwrite), `render` (token substitution: home dir, Git Bash absolute path on Windows, base-branch defaults), and `register` (write or update marked sections and settings entries). Existing user files get `.bak` and `.new`, never silent overwrite, never deletion.
4. Adapt: for each selected agent (default: every detected agent, flag-selectable), the installer applies that adapter's mapping. Rendered artifacts live only on the user's machine, never in this repo.
5. Verify: the installer ends with a self-check (hooks executable, settings valid JSON, statusline runs once) and prints what was skipped and why (for example markitdown without Python).

## Runtime model

- Hooks are event-bound sh scripts invoked by the agent harness; on Windows they run under Git Bash via the absolute path resolved at install time. They read JSON from stdin where the event provides it and write additional context or exit codes per the hook contract in `data-contracts.md`.
- The worklog subsystem: session-start hook injects the branch's task block; snapshot hook appends mechanical git state on stop/compact/end; the worklog skill owns semantic updates. Storage layout in `data-contracts.md`.
- Branch safety: an advisory session-start check against the active story marker, and an upstream fixer that repoints feature branches so a plain `git push` cannot hit a protected integration branch.
- The statusline is a stateless script fed the agent's JSON on stdin.

## Multi-agent model

- Project scope: `AGENTS.md` at the repo root is canonical (ADR-010). `CLAUDE.md` is a thin file that imports it and adds Claude-only notes. Codex and OpenCode read AGENTS.md natively; Cursor gets a rendered rules file where native AGENTS.md support is absent. A marked persistent-learnings section inside AGENTS.md is shared by all agents.
- User scope: `claude/CLAUDE.md` is authored with marked sections; the installer renders the shared sections into each agent's user file (`~/.codex/AGENTS.md`, Cursor and OpenCode equivalents), preserving anything the user keeps outside the markers.
- Skills: the SKILL.md folder convention is copied as-is where the agent supports it, and exposed as prompts/commands where it supports only those. The mapping per agent lives in the adapter spec; the outcome lives in the capability matrix.
- Hooks and statusline are Claude Code capabilities; adapters document equivalents or mark the gap in the matrix rather than emulating badly.

## Decision index

ADR-001 content as data. ADR-002 two-layer model. ADR-003 Claude-native canonical source. ADR-004 thin dual installers over one manifest. ADR-005 single-source POSIX sh runtime scripts. ADR-006 portable path policy. ADR-007 sanitization guard with untracked denylist. ADR-008 agent tiers. ADR-009 safe permission defaults. ADR-010 AGENTS.md as project canonical.
