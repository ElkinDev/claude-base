# docs/ - the spec structure (spec-driven development)

This is the source of truth for a spec-driven project. The `/sdd` orchestrator and the
implementer/designer agents read these files before writing anything. Fill each one for your project,
keep it in the repo, and version it. This README documents the layout so it is self-contained on any
machine, with no dependency on any other project.

## Layout
- `00-product/`
  - `01-vision-and-scope.md` what the product is, who it is for, what is in and out of scope
  - `02-domain-model.md` the core entities and their relationships
- `01-requirements/`
  - `01-functional-requirements.md` FR-xxx, each a testable capability
  - `02-non-functional-requirements.md` performance, security, accessibility, limits
  - `03-user-stories.md` US-xxx, each with explicit acceptance criteria
- `02-architecture/`
  - `01-architecture-overview.md` the ADRs (numbered decisions). ADRs are fixed; change only by
    discussion with the user.
  - `02-data-model.md` entities, fields, storage
  - `03-security-and-privacy.md` data-handling rules
- `03-features/`
  - `F01-<name>.md` one spec per feature: the FR/US it satisfies, behavior, states, edge cases
- `04-plan/`
  - `00-roadmap-and-releases.md` the release plan
  - `backlog.md` the ordered backlog
  - `sprints/sprint-00.md`, `sprint-01.md`, ... one per sprint, each with its scope and a definition
    of done
- `05-design/` (when there is a UI)
  - `01-design-brief.md` the visual and interaction direction
  - `02-screen-inventory.md` the screens and their states

## How it is used
`/sdd` reads `04-plan` (roadmap + the active sprint), then the feature spec in `03-features`, then the
ADRs in `02-architecture`, establishes the acceptance criteria and the sprint definition of done,
implements strictly to them through the implementer/designer agents, and validates each deliverable
against the spec and the DoD before committing. If a section does not apply to your project, say so in
that file rather than deleting the structure.
