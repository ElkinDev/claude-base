# ADR-004: Thin dual installers driven by one manifest

Status: Proposed (2026-07-11)

## Context

The installer must run on a fresh Windows box (no Node, no Python, no pwsh guaranteed) and on fresh macOS/Linux (no PowerShell). Today's installer is PowerShell-only. Two installers written independently would drift, which is the same disease as duplicated agent content.

## Decision

Two thin bootstrappers, `install/install.sh` (POSIX sh) and `install/install.ps1` (Windows PowerShell 5.1+), each at most flag parsing, OS detection, and an executor for the shared declarative `install/manifest.json`. The manifest is the single place that says what gets installed where (operations: copy, render, register, with os/flag/agent filters, per `data-contracts.md`). Any logic beyond those three operations lives in single-source sh tools under `tools/` that both installers invoke.

## Alternatives considered

- Single sh installer run via Git Bash on Windows too: possible since Git for Windows is required anyway, but first-run UX on Windows (execution from PowerShell, PATH questions) is worse, and a native ps1 entry point is cheap once it is manifest-driven. Rejected as sole entry point; the ps1 stays thin.
- Node or Python installer: single language, but adds a hard runtime dependency the core otherwise avoids (NFR-001). Rejected.
- Compiled binary: rejected per ADR-001.

## Consequences

Drift between installers is structurally limited to the tiny executor layer, which CI smoke-tests on all three OSes (FR-043). The manifest becomes a reviewable, diffable description of everything the toolkit touches, which also feeds `--dry-run` (FR-006) and the uninstall doc (FR-007).
