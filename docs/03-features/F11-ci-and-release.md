# F11: CI and release engineering

Satisfies: FR-043, FR-044, NFR-010. Stories: US-007, US-011. Depends on: ADR-001, ADR-004, ADR-005.

## Summary

The GitHub Actions matrix that proves every promise (lint, guard, install smoke, hook parity) on Windows, macOS, and Linux, plus the release process: semver tags, changelog, and a publish checklist.

## Behavior

- `ci.yml` on push and PR, matrix `windows-latest`, `macos-latest`, `ubuntu-latest`:
  - Lint: shellcheck on `*.sh`, PSScriptAnalyzer while any ps1 remains, a markdown linter tuned to the house style (long lines are correct, so no line-length rule), JSON validity for settings and manifest.
  - Guard: `python scripts/sanitize-check.py --all` plus the seeded-leak fixtures (F10).
  - Install smoke: user-scope install into a temp HOME (including one with spaces), verify phase green, re-run no-op check, `--dry-run` writes-nothing check; project scaffold with each optional flag into a temp dir.
  - Hook parity: the F02 fixture suite.
  - Optional-module smoke: evidence harness headless scenario (F08) and cross-agent hook fixtures (F09) on the matrix, allowed to be a separate job for speed.
- House-style check: a small lint asserting no em-dash characters in shipped markdown and no AI-attribution phrases; part of the lint job.
- Release: tags `v0.x.y`; `CHANGELOG.md` (keep-a-changelog format, human-written entries); `RELEASING.md` checklist gating publish: matrix green, guard green with the maintainer's local denylist run, capability matrix current, LICENSE holder filled, README quickstart re-tested on one fresh machine per OS.
- CONTRIBUTING.md: house style (English, no em-dashes, continuous-line paragraphs, no AI attribution, no person names), the guard workflow for contributors, the adapter contract pointer, and the file-size limit.

## Edge cases

- Matrix runners lacking optional deps: jobs install Node/Python explicitly rather than assuming runner images.
- Fork PRs cannot access secrets: the pipeline needs none; everything runs from repo content.
- Windows runner Git Bash: resolved the same way the installer does, which doubles as a test of that resolution.

## Out of scope

Package-manager distribution, artifact signing (backlog), docs website (backlog), issue templates (nice-to-have, backlog).

## Acceptance

- A PR breaking any promise (a hook behavior change, a leak fixture, an installer regression) turns the matrix red.
- The release checklist exists and v0.1.0 is cut with it (Sprint 05).
- CONTRIBUTING.md present and linked from the README.
