# F17: Vendored skills

Status: proposal. The tree carries the third plugin, the vendored skills and the installer change; nothing is reviewed or merged yet, and no FR covers this material: the requirement is proposed with the feature.

Stories: US-001, US-007. Depends on: F15 (the plugin channel and its manifests), ADR-002 (two-layer model), ADR-003 (the Claude-native SKILL.md is the canonical source). Neither ADR changes: a vendored skill is a SKILL.md like any other, and it reaches a machine through a channel that already exists.

## Summary

Take three skills from a third-party library published under the MIT licence, rewrite them for this kit, and ship them: `decision-rounds` through the installer, `/groundwork:codebase-design` and `/groundwork:slice-plan` through a third plugin. `groundwork` is the work before the code, and it is the first plugin whose skills the installer deliberately does not copy, so each of them exists on a machine in exactly one form.

The upstream text is not vendored as it stands. Each file is rewritten in the kit's voice against this repository's docs tree, its tracker rules and its house format for a question put to the owner. What carries over is the method and the vocabulary, which is what was worth taking.

## Behavior

- `decision-rounds` cannot live in a plugin. It is preloaded by `claude/agents/analyst.md`, an agent definition names a skill bare, and the harness resolves that name at the kit home (F15, "Edge cases"). It therefore sits under `claude/skills/` and reaches a machine through the installer only.
- `groundwork` declares itself single-channel with the keyword `plugin-channel-only` in its own manifest, and `install.ps1` skips any plugin carrying it. The declaration is a keyword and not a field of its own because `claude plugin validate --strict` fails a manifest that carries a field the format does not know.
- A scaffolded project enables `groundwork` for itself. `project-template/.claude/settings.json` carries `extraKnownMarketplaces` and `enabledPlugins`, with the owner, the repository and the commit as placeholders. `install.ps1 -Project` fills them from the clone it is running out of, pinning the source to the commit the clone sits on. The tracked template keeps the placeholders, because a real account name in a shared file is what the push guard exists to catch.
- The invariant becomes stronger, not weaker. It used to read "one channel per machine"; it now reads "one channel per skill", which is true on every machine whatever its owner installed. `delivery` and `orchestration` are unchanged and stay out of `enabledPlugins`, since the installer still copies them.
- No skill names a tracker, a triage label or a scratch folder. Where the upstream text published to a tracker, the kit's version writes a file under the docs tree and stops; anything that has to reach a tracker goes through `work-item`, which reads the profile.
- The licence travels with both channels. `THIRD-PARTY-NOTICES.md` at the repository root covers everything, and `plugins/groundwork/NOTICE.md` covers the plugin for a machine that only ever sees the plugin. Each carries the MIT text in full and names the upstream commit and the origin of every file. Neither the manifests nor the bodies of the skills carry it: a manifest fails the person guard on a copyright line, and a skill body would ride in the agent's context on every invocation for nothing.

## Edge cases

- `install.ps1` running from a clone with no readable origin leaves the placeholders in the scaffolded settings, and the project simply has no plugin source until someone fills it in. It is not an error: the rest of the scaffold is unaffected.
- The person guard in `scripts/tests/test-marketplace.py` matches two capitalised words in a row anywhere in a manifest value and its waiver set is empty, so the new manifest and the new entry are written to carry none.
- A refresh from upstream is a comparison, not a re-copy. The notices pin the commit the files were taken at, so a later change upstream can be read as a diff against it and applied, or declined, on purpose.

## Out of scope

The rest of the upstream library. Its remaining skills either assume a tracker the kit reads but never writes, duplicate what an agent definition or an existing skill already says, or would more than double the skill descriptions injected into every session for material the kit does not need. Anything taken later is taken one skill at a time, by the same rule: rewritten, its tracker assumptions stripped, and its licence recorded.

## Acceptance

- The root and all three plugins pass `claude plugin validate` and `claude plugin validate --strict` with no warning.
- `python scripts/tests/test-marketplace.py` is green, including the person guard over the new manifest values and the set intersection that proves no skill name exists under both `claude/skills` and a plugin.
- A fresh `install.ps1` run lands `skills/decision-rounds/SKILL.md` at the kit home and lands neither skill of the single-channel plugin, asserted by `scripts/tests/test-install-smoke.ps1` against the manifests rather than a list of names.
- `install.ps1 -Project` writes a `.claude/settings.json` carrying a real owner, repository and commit, while the tracked template still carries its placeholders, asserted by `scripts/tests/test-install-project.ps1`.
- `claude/agents/analyst.md` plus the bodies of the skills it preloads stays under 8000 bytes, and no other agent's total grows.
- Both notice files carry the MIT text and the upstream commit, and each vendored file is named by the notice of the channel it ships on.
