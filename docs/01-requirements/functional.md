# Functional requirements

Each FR is a testable capability. Feature specs in `03-features/` reference these ids; do not renumber.

## Install and update

- FR-001: A user installs the engine (user scope) on Windows, macOS, and Linux with one documented command per OS, from a local clone of this repo.
- FR-002: A user scaffolds a project (project scope) with the template, a chosen profile, project hook wiring, and optionally the SDD spec tree and the agents, via installer flags.
- FR-003: The installer is idempotent. Re-running refreshes managed files, writes `.bak` backups and `.new` proposals for user-modified files, and never deletes user content.
- FR-004: The installer renders settings per OS: it resolves the shell used for hook commands and the statusline (including the absolute Git Bash path on Windows), and writes home paths in the form the host agent expects.
- FR-005: Updating is `git pull` on the clone followed by re-running the installer; the result equals a fresh install on a clean machine.
- FR-006: The installer supports `--dry-run` (print the plan, write nothing) and prints every write it performs.
- FR-007: An uninstall document lists every managed file per scope so a user can remove the toolkit completely by hand.

## Portability

- FR-010: The core hooks (worklog context, worklog snapshot, worklog resolve, worklog lib, branch check, branch upstream fix) behave identically on the three OSes, verified by parity tests against one behavior contract.
- FR-011: The statusline renders the same fields on the three OSes (model, effort, folder, branch, dirty marker, added/removed, context bar).
- FR-012: The markitdown read hook is optional: an installer flag wires it only when Python and the markitdown package are present, and the installer says clearly when it skipped it.
- FR-013: A single hook registry document lists every hook, its event, its scope (user or project), the file that wires it, and its OS support. No hook exists outside the registry.
- FR-014: The base-branch list used by hooks (default `main,master,devel,HEAD`) is configurable per repo without editing the hook scripts.
- FR-015: All user-facing paths use portable tokens (`~`, `$CLAUDE_PROJECT_DIR`, profile variables). The evidence root is a profile line carrying a portable spec, resolved per machine by `scripts/evidence-path.py` and defaulting to `{repo_parent}/evidence`, so evidence sits beside the repository and never inside it (`docs/EVIDENCE.md`).

## Multi-agent

- FR-020: In a scaffolded project, `AGENTS.md` at the repo root is the canonical cross-agent instruction file. `CLAUDE.md` imports it and adds only Claude-specific notes. Agents that read AGENTS.md natively need no extra file.
- FR-021: The user-scope rules render from one canonical source into the per-agent user files (Claude `~/.claude/CLAUDE.md`, Codex `~/.codex/AGENTS.md`, and equivalents), managed via marked sections that preserve user content outside the markers.
- FR-022: The Codex adapter installs rules, the skill pack, and workflow prompts into Codex locations, and documents what does not carry over (hooks, statusline, permission modes).
- FR-023: The OpenCode adapter installs rules, the skill pack, and workflow commands into OpenCode locations with the same documentation duty.
- FR-024: The Cursor adapter installs rules (and skills and subagents where Cursor supports them) into Cursor locations with the same documentation duty.
- FR-025: The capability matrix document states, per agent and per capability (rules, skills, workflows, hooks, subagents, worklog, evidence, statusline), the support level and the workaround when unsupported. The README links it. No other place claims agent support.
- FR-026: The project kit ships a persistent-learnings convention: one marked section in AGENTS.md where any agent appends durable lessons, and the rules tell agents to read it first.

## Content unification

- FR-030: The generic skills mined from the private setups (plan interrogation, branch-diff correctness/completeness/security review) are part of the engine skill pack, sanitized.
- FR-031: The live improvements of the private tracker skill are back-ported into the generic `work-item` skill; org and project always come from the profile, never from the skill.
- FR-032: The parallel-agents coordination templates (agent prompts, tracking board, model selection guide, coordination example) ship in the project template as an optional module.
- FR-033: The worklog design spec is published as `docs/WORKLOG.md`, sanitized.
- FR-034: The project template documents the roles-by-model orchestration pattern (orchestrator session plans, validates, owns git; implementation runs in subagents).
- FR-035: Third-party or person-attributed skills are not vendored. `docs/OPTIONAL-SKILLS.md` lists them with source links and install pointers.
- FR-036: The evidence harness template (Playwright config and reporter, demo recorder with H.264 conversion, preflight, screenshot, PDF validation, recording guide) ships in the project template as an optional module, fully parameterized, with no personal values.
- FR-037: The cross-agent project hooks (edit-time validate, pre-push gate) ship in the project template as optional Node templates, with the watched paths and the gate command parameterized, and payload normalization for Claude, Copilot, and generic agents.

## Safety and repo quality

- FR-040: The repo contains no personal identifiers, private project names, real org names, real emails, or absolute personal paths. Enforced by the sanitization guard locally and in CI.
- FR-041: The guard's committed rules are generic patterns only. The real private names live in an untracked local denylist; the guard merges it when present. The private names never appear in any committed file, including the guard's own config and this spec tree.
- FR-042: Shipped settings use safe permission defaults and a secrets deny list (SSH keys, key files, env files, cloud credentials). The aggressive personal mode (bypass permissions, dangerous-mode skip) is documented as an explicit local opt-in, never shipped as default.
- FR-043: CI runs on a Windows, macOS, and Linux matrix: lint (shellcheck, markdown lint, PSScriptAnalyzer while any PowerShell remains), the sanitization guard, installer smoke tests, and hook parity tests.
- FR-044: Releases are semver tags with a changelog. A release checklist gates publishing (license holder filled, guard green, matrix green, capability matrix current).
