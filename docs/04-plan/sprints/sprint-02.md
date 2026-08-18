# Sprint 02: Cross-platform install

Goal: one documented command per OS installs the engine; the sh runtime becomes the only runtime. Features: F01 (whole), the settings and safe-defaults work from ADR-009/FR-042, ps1 retirement from ADR-005.

## Scope

1. `install/manifest.json` describing every current install operation (contract in `data-contracts.md`).
2. `install/install.sh` and `install/install.ps1` as thin manifest executors with the shared flag set, detection phase (git, Git Bash resolution on Windows, optional deps, installed agents), plan phase with `--dry-run`, execute phase with backup semantics, and verify phase.
3. Settings render: `claude/settings.json` becomes the safe-defaults template (standard permissions, secrets deny list, statusline and hooks wiring via tokens); the power overlay is documented in the README section that F12 will later polish. Hook commands point at the sh implementations on every OS.
4. ps1 retirement: after the matrix proves parity (Sprint 01 evidence plus this sprint's install smoke), delete the six hook ps1 files and `statusline.ps1`, and drop PSScriptAnalyzer from CI when the last ps1 goes (the installer ps1 remains, so it stays for that).
5. INSTALL.md rewrite for the three OSes; UNINSTALL.md generated-from-manifest first version with its CI drift check.
6. CI: add the install smoke jobs (temp HOME, spaced HOME, re-run no-op, dry-run writes-nothing, project scaffold per flag).
7. Migration note for existing users of the old installer (paths unchanged, settings now safe-by-default, how to restore their power settings via the overlay).

## Out of scope

Adapters (the manifest's `agent` field lands but only `claude-code` entries exist), porting content, markitdown beyond wiring the flag.

## Definition of done

- Fresh-machine smoke green on the three-OS matrix, including the spaced-home case and the re-run no-op check.
- On Windows, hooks and statusline run through the resolved Git Bash path; no ps1 runtime files remain in `claude/hooks/` or as the statusline.
- Shipped settings contain no bypass or dangerous-skip flags and include the secrets deny list; the overlay doc exists and reproduces the previous behavior when applied.
- `--dry-run` provably writes nothing; UNINSTALL.md matches the manifest per the CI check.
- INSTALL.md walked through live on at least one real machine per OS family by the maintainer (recorded in the sprint notes).
