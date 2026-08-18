# ADR-001: Content as data, updates via git

Status: Proposed (2026-07-11)

## Context

The closest prior art ships all rules, skills, and agent assets embedded inside a compiled binary. Every content tweak requires a release, a self-update, and a re-sync, and users cannot read or pin what gets written to their machine without reading Go source.

## Decision

Every rule, skill, hook, template, and adapter mapping in this project is a plain text file in this git repo. The installers are thin scripts that copy and render those files. Updating is `git pull` plus re-running the installer. There is no compiled engine, no embedded assets, no self-updater, and no rendered artifact committed to the repo.

## Alternatives considered

- Compiled Go/Rust binary with embedded assets: faster TUIs and atomic distribution, but opaque, release-coupled, and heavy to maintain. Rejected.
- Package-manager distribution (npm, brew) of a content package: adds a runtime dependency and a publishing pipeline for little gain over git. Deferred to the backlog as an optional convenience.

## Consequences

Users can audit, fork, and pin content at any commit. Content fixes ship at git speed. The cost: installers stay deliberately dumb (see ADR-004), and anything needing real logic must be a reviewable script under `tools/`.
