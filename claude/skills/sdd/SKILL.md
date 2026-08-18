---
name: sdd
description: Spec-driven orchestrator for spec-governed work, greenfield projects, whole features, and autonomous multi-step builds driven by a written docs/ spec structure. It reads the specs (product, requirements, architecture/ADRs, features, plan/sprints), implements strictly to them via implementer/designer agents, and validates every deliverable against the feature spec and the sprint definition of done. Use for spec-driven, new-project, or autonomous work. Triggers, EN "spec-driven / SDD mode / new project / autonomous build". In Spanish, also trigger on "modo sdd / spec-driven / arranquemos el proyecto / construccion autonoma". Also invocable as /sdd. For a single task tied to a story or ticket, use /story instead.
---

# SDD (spec-driven development)

Orchestrate spec-governed work: a greenfield project, a whole feature, or an autonomous run through a
sprint, where the written specs are the source of truth. Read and obey the docs/ spec structure; do
not invent behavior. Everything in English, no AI attribution, no em-dashes.

Use `/sdd` for spec-driven, new-project, or autonomous work. Use `/story` for a single task tied to a
story or ticket.

## The docs/ spec structure (source of truth)
The specs live in the repo under `docs/`. This is the expected layout. If it does not exist yet,
offer to scaffold it from the base `project-template/docs/` before implementing anything. The layout
is documented here so it is self-contained, with no dependency on any specific project.

- `00-product/` product vision and scope, and the domain model (core entities and relationships).
- `01-requirements/` functional requirements (FR-xxx, each a testable capability), non-functional
  requirements, and user stories (US-xxx, each with explicit acceptance criteria).
- `02-architecture/` an architecture overview with the ADRs (numbered, fixed decisions), a data model,
  and security/privacy.
- `03-features/` one spec per feature (F01-...), mapping the FR/US it satisfies, with behavior,
  states, and edge cases.
- `04-plan/` a roadmap and releases, a `sprints/` folder with one file per sprint (each carrying its
  scope and a definition of done), and a backlog.
- `05-design/` a design brief and a screen inventory, when the project has a UI.

## Working model (roles)
- An orchestrator session plans, sequences, validates, and owns git (branches and commits per the
  project's git discipline). It does not write implementation code inline.
- Implementation and design run in subagents: the `.claude/agents/implementer.md` and `designer.md`
  templates, or general-purpose subagents at high effort. Give each agent the exact spec files to
  read. Agents read the specs first and never improvise; if a spec is ambiguous or contradictory they
  STOP and report the conflict rather than inventing.
- Agents never run git commands. The orchestrator owns history.

## The cycle (per feature or sprint slice)
0. Load context: read `04-plan` (roadmap + the active sprint), then the feature spec in `03-features`,
   then the ADRs in `02-architecture`. Establish the acceptance criteria and the sprint definition of
   done.
1. Guardrails: never change an ADR or a fixed convention (stack, naming, string keys) without
   discussing it with the user first. If the specs conflict, STOP and report it; do not pick one
   silently.
2. Plan the slice against the spec. For a sprint, take its scope and DoD as the boundary.
3. Implement strictly to the spec. Delegate to the implementer/designer agents; each builds only what
   its spec files say.
4. Validate every deliverable against the feature spec and the sprint DoD before committing. Findings
   go back to the same agent (continue it) rather than being patched inline.
5. Commit or hand off per the project's git discipline (see `CLAUDE.project.md`). Keep the specs and
   the sprint status current as work lands.

## Autonomous runs
For an autonomous build, the specs and the sprint DoD are the guardrails. Proceed through the sprint
tasks in order, stopping only to (a) report a spec conflict or ambiguity, or (b) ask before changing
an ADR or a fixed convention. Do not widen scope beyond the active sprint without saying so.

## When NOT to use
- A single task tied to a story or ticket. Use `/story`.
- A trivial or mechanical edit. Just do it.
- Debugging an existing failure. Use investigate-issue or spec-first-debug.
