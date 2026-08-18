# Sprint 05: Cursor, docs, release

Goal: complete the v0.1.0 surface and publish. Features: F06, F11 (completion), F12.

## Scope

1. F06 Cursor adapter: mapping spec with the native-AGENTS.md check, user-scope rules render, skills and subagents where supported, generated-file discipline for the `.mdc` case, matrix column, uninstall entries, smoke job.
2. F11 completion: CHANGELOG.md started, RELEASING.md checklist, CONTRIBUTING.md (house style, guard workflow, adapter contract pointer, file-size limit), link-check and UNINSTALL-drift checks confirmed in CI.
3. F12 docs: README rewrite (positioning, per-OS quickstart, support table linking the matrix, philosophy, power overlay section, scaffold walkthrough, prior-art credit), INSTALL.md final pass, `docs/AGENTS-SUPPORT.md` adapter-contract section, herdr folder reconciled into the portable integration note.
4. Maintainer decisions executed: repo name (backlog decision, sweep self-references if renamed), herdr depth, statusline default glyphs.
5. LICENSE copyright holder filled by the maintainer.
6. Release: run RELEASING.md end to end, tag v0.1.0, make the repo public.

## Out of scope

Anything in the backlog (more adapters, packaging, website, Spanish companion doc). New features discovered during release prep go to the backlog, not into this sprint.

## Definition of done

- Cursor column verified and filled; all Sprint 04 guarantees still green (regression matrix run).
- README plus INSTALL walked through by the maintainer on one fresh machine per OS family, going clone-to-working-engine with no undocumented step (recorded in sprint notes).
- Link check green; house-style lint green; guard green in CI and locally with the private denylist; UNINSTALL matches the manifest.
- The support table in the README matches `docs/AGENTS-SUPPORT.md` exactly; no capability is promised anywhere above its verified tier.
- RELEASING.md checklist fully checked; v0.1.0 tagged; repo public.
