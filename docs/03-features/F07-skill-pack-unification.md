# F07: Skill pack unification

Satisfies: FR-030, FR-031, FR-032, FR-033, FR-035. Stories: US-008. Depends on: ADR-002, ADR-006, ADR-007.

## Summary

Bring the best of the private setups into the repo, sanitized: two new generic skills, back-ported improvements to `work-item`, the parallel-agents coordination templates, the worklog design spec, and a pointers doc for third-party skills that are deliberately not vendored.

## Behavior

- New skill, plan interrogation (name: `grill-me`): relentlessly question a plan before building (assumptions, failure modes, missing requirements, simpler alternatives), producing a revised plan or a STOP. Ported from the private original with any project wording removed.
- New skill, branch-diff review (name: `check-ccs`): review the current branch diff for correctness, completeness, and security against the story's acceptance criteria, evidence-based, output as findings with file/line references. Sanitized the same way.
- `work-item` back-port: fold in the evolved private tracker logic (assigned-to-me and active-item query patterns, the branch reconcile flow) rewritten tracker-agnostically; org, project, and query defaults come only from the profile (FR-031). The Azure DevOps and Jira specifics live as examples in the matching profiles, not in the skill.
- Coordination templates: `project-template/docs/coordination/` with AGENT_PROMPTS.md, TRACKING_BOARD.md, MODEL_SELECTION_GUIDE.md, COORDINATION_EXAMPLE.md, genericized (placeholder feature names, no model-version lock-in), installed under the `--coordination` flag and explained in the template docs README.
- Worklog spec: publish the private worklog design document as `docs/WORKLOG.md`, with example repo-ids and paths replaced by generic ones.
- `docs/OPTIONAL-SKILLS.md`: lists the recommended third-party or person-attributed skills (frontend design and animation families) with source links, license notes, and install pointers; states the policy that this repo does not vendor them (FR-035).
- Every ported file passes the sanitization guard locally before commit; porting happens by rewriting against the originals, never by bulk copy.

## Edge cases

- Name collisions with skills a user already has: install is merge-copy with backup (F01 semantics).
- The private originals contain baked identifiers mid-sentence: the port rewrites those sentences; the guard catches stragglers.
- Third-party skill licenses that later permit vendoring: revisit via backlog, not ad hoc.

## Out of scope

The evidence harness (F08) and the cross-agent hooks (F09), new skills not mined from the private setups, translating skills to non-SKILL.md formats (adapters handle exposure).

## Acceptance

- Both new skills invoke and complete their happy path in Claude Code on a sample repo.
- `work-item` drives its flow end to end with a profile-only configuration for at least the plain-git profile.
- Guard run over the whole repo: zero findings, including with the maintainer's private denylist locally.
- `--coordination` scaffold produces the four templates; docs README explains when to use them.
