# Sprint 01: Safety net and portable core

Goal: make leaking private content impossible before any porting begins, and make the core hooks run on Windows, macOS, and Linux. Features: F10 (whole), F02 (whole), F11 (CI skeleton only).

## Scope

1. Initial commit: the currently staged tree becomes the first commit on `main` before any new work, so history starts from the audited clean state.
2. F10 sanitization guard: `tools/sanitize-check.sh`, generic rules and allowlist files, `.sanitize/` gitignore entry, pre-commit helper doc, seeded-leak fixtures. The maintainer creates the local private denylist (outside the repo's history) as part of this sprint.
3. F11 skeleton: `ci.yml` with the three-OS matrix running lint (shellcheck, PSScriptAnalyzer, markdown, JSON validity, house-style check) and the guard job with fixtures. Install smoke and parity jobs are added as their subjects land.
4. F02 hook port: finalize the behavior contracts from the ps1 sources, implement the six sh scripts plus `statusline.sh`, add the parity fixture suite to CI, wire the base-branch git config fallback, write `docs/HOOKS.md`. The ps1 files stay in place and remain the wired implementation on Windows during this sprint.
5. `.gitattributes` forcing LF on `*.sh`.

## Out of scope

Installer changes (Sprint 02 wires the sh hooks), any porting from private setups (Sprint 03, guard must exist first), adapters.

## Definition of done

- CI matrix green on all jobs, including parity fixtures proving each sh hook matches its behavior contract on the three OSes and matches the ps1 output on Windows.
- Guard: full-repo run green in CI; seeded-leak fixtures each fail with the right rule id; local run with the private denylist green; a tracked-denylist sandbox test fails hard.
- `docs/HOOKS.md` lists every hook in the repo; the CI check for unregistered hooks passes.
- Statusline sh renders correctly from recorded payloads on the three OSes within budget.
- Specs updated where implementation taught something; guard and house-style checks green over the spec tree itself.
