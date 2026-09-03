# F15: Plugin marketplace

Status: proposal. The tree carries the manifests, the two plugins and the installer change; nothing is reviewed or merged yet, and no FR covers this channel: the requirement is proposed with the feature.

Stories: US-001, US-007. Depends on: ADR-002 (two-layer model), ADR-003 (the Claude-native SKILL.md is the canonical source). Neither is changed: a plugin is a second way to deliver the same files, not a second format.

## Summary

Publish the skills that only make sense as a set through Claude Code's own plugin channel, so a machine can take them with `claude plugin install` instead of running the installer. `.claude-plugin/marketplace.json` at the root lists two plugins under `plugins/`: `delivery` (story, sdd, work-item) and `orchestration` (wave-orchestration, herdr-driving). Each carries its own `.claude-plugin/plugin.json` and its skills at `skills/<name>/SKILL.md`, byte for byte the files that were under `claude/skills/`.

## Behavior

- One source per skill. A skill lives under `claude/skills` or under exactly one plugin, never both. `scripts/tests/test-marketplace.py` fails the build on a name that appears on both sides, because the two copies would drift.
- One channel per machine. The installer still copies the plugin skills into the kit home under their bare names, reading `plugins/*/skills` through the same `Get-KitPairs` it already used, so an installer machine sees no change at all. A marketplace machine installs `delivery@<marketplace>` and `orchestration@<marketplace>` and invokes them namespaced: `/delivery:story`, `/orchestration:wave-orchestration`. A machine that took both would hold two copies of each.
- The rest of the kit has one channel only: the agents, the hooks, the tools, the status line and the settings reach a machine through the installer.
- The manifests carry no personal attribution. `name`, `owner.name` and `author.name` are all the repository name, `claude-base`, and no address appears anywhere in the three files.

## Edge cases

- `claude plugin validate --strict` warns on a manifest with no `author` and treats that warning as a failure. The field takes the repository name, which is the attribution these files can carry, so strict passes: every one of the three targets returns exit 0 with "Validation passed" and no warning at all. The test asserts the strict exit code as well as the text, so a `claude` that rejected `--strict` could not pass it by saying nothing, and it skips with a stated reason when `claude` is not on PATH.
- A skill named in an agent's `skills:` list cannot move into a plugin: an agent definition names skills bare and the harness resolves them at the kit home. None of the five was named by an agent, which is why all five could move.

## Out of scope

A `core` plugin for the remaining fifteen skills. It needs two things this feature does not have: `project-template/CLAUDE.md` and the five agent definitions name skills bare (story, sdd, work-item, tdd-workflow, quality-gates and the rest), so either those files learn to write both forms, or the namespaced names have to resolve bare on a marketplace machine. Until one of those is true, moving the skills the template and the agents depend on would break every project that took the template.

## Acceptance

- The marketplace root and both plugins pass `claude plugin validate` and `claude plugin validate --strict`, with no warning.
- A fresh `install.ps1` run lands `skills/story/SKILL.md` and `skills/wave-orchestration/SKILL.md` at the kit home, asserted by `scripts/tests/test-install-smoke.ps1`.
- No skill directory name exists under both `claude/skills` and a plugin, asserted by `scripts/tests/test-marketplace.py`.
- Every skill name `project-template/CLAUDE.md` hands a reader resolves under `claude/skills` or under a plugin.
