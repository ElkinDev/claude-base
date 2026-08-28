# Backlog

Not scheduled. An item moves into a sprint file when it is chosen; it never gets picked up silently.

## Decisions pending (maintainer)

- F10 sanitization guard moved up: the repository went public on 2026-08-28 before the guard existed, and the first scan afterwards found private project names in tests and fixtures, fixed by a history rewrite and a forced push the same morning. F10's "history starts clean" assumption no longer holds; the pre-push check is the next thing to build, and the commits that existed before the rewrite stay reachable by hash on the host until it garbage-collects them or support is asked to purge them.

- Repo name before going public: keep `claude-base` (honest, searchable) or rename to something agent-neutral. Decide before Sprint 05 executes F12.
- herdr folder depth: keep the minimal integration note (F12 baseline) or absorb the fuller private kit's extras (settings reference, launchers) after sanitization.
- Statusline default glyphs: keep current cosmetic defaults or switch to plain ASCII defaults with glyphs as documented opt-in.

## Adapters and agents

- Gemini CLI adapter (Tier 3 candidate; rules and skills mapping similar to Codex).
- GitHub Copilot / VS Code adapter (instructions file plus skills location).
- Windsurf adapter.
- Worklog automation for OpenCode via its plugin system (TypeScript), bringing Tier 2 closer to Tier 1 for continuity.
- Per-phase model assignment guidance per agent (document native mechanisms; never manage them).

## Content

- Port the language and framework instruction references from the private VS Code AI setup as generic stack addenda (like `tsql-rules.md`), one per stack, profile-linked.
- Relocate `docs/tsql-rules.md` under a `project-template/addenda/` convention once more addenda exist.
- A Spanish companion doc for the README (addition, not replacement; shipped content stays English).
- Chatmode-style role documents (researcher, planner) as optional project agents.

## Tooling and distribution

- Package-manager convenience distribution (brew tap, scoop bucket) once content stabilizes; ADR-001 still holds (thin bootstrap only).
- Artifact or tag signing for releases.
- Hosted secret scanning as a complement to the guard.
- Issue and PR templates.
- Docs website when the markdown outgrows GitHub rendering.
- MCP server recommendations doc (which servers pair well with the kit; config examples per agent).

## Known small items

- `.gitignore` entry for `.sanitize/` ships with F10; verify nothing else personal-adjacent needs ignoring after Sprint 03 ports.
- Evidence root on Linux distros without `~/Documents`: create-on-demand covers it; revisit only if users report friction.
