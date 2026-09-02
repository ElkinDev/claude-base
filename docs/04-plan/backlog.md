# Backlog

Not scheduled. An item moves into a sprint file when it is chosen; it never gets picked up silently.

## Decisions pending (maintainer)

- The sanitization guard is in `scripts/` as of 2026-08-28 (`python scripts/sanitize-check.py`, with a pre-push hook and an optional pre-commit one); the CI job that would run it on every push is F11 and is not built yet. Separately, the commits that existed before that morning's history rewrite stay reachable by hash on the host until it garbage-collects them or support is asked to purge them.

- F14 (quota wake): which sprint takes it, and whether it ships inside v0.1.0 or waits for v0.2.x. The code and its tests are in the tree on a branch, unmerged, and the roadmap lists it out of sprint until this is answered.
- NFR-003 line budget: keep the flat 300 line rule or amend it with the exemption the repo already lives by. Main carries twelve files over the budget, `scripts/ledger-day.py` at 1397 lines the largest and the two largest test suites at 585 and 521, and F14 adds four more, listed under "Known deviations" in its feature doc. The rule stays as written until this is decided.
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

- Sanitization guard (from the F10 review): a stdout closed outright (not a broken pipe) raises AttributeError on the final flush, unreachable from a git hook; a clean report cut short by a pager exits 1; under `ls-tree -t` a directory row and the file beneath it each yield a `private-path` finding; overlapping alternations in a private `regex:` line (`(a|a)+`) are documented, not detected.
- Safe adoption (from the F13 review): `kit-restore.py` does not yet apply the broken-record rule that `install/lib.ps1` and the data contract state; a restore through rename does not carry an explicit ACE or the hidden attribute of the replaced file (`copymode` carries the read-only bit only).
- `.gitignore` entry for `.sanitize/` ships with F10; verify nothing else personal-adjacent needs ignoring after Sprint 03 ports.
- Evidence root on Linux distros without `~/Documents`: create-on-demand covers it; revisit only if users report friction.
