# Domain model

## Entities

- Engine: the user-scope content installed once per machine (rules, skills, hooks, statusline, settings). Today it targets `~/.claude`; adapters project parts of it into other agents' user-scope locations.
- Project kit: the per-repo content copied into a user's project (CLAUDE.md, CLAUDE.project.md, AGENTS.md, `.claude/` wiring, optional agents, optional spec tree, optional testing module). Committed to the user's repo for team reproducibility.
- Profile: a fill-in-the-blanks file that carries everything project-specific: tracker kind and org/project fields, stack and quality-gate commands, git discipline, evidence root, environments. Skills read the profile; no skill hardcodes a tracker org, a solution name, or a path.
- Canonical source: the Claude-native content under `claude/` and `project-template/`. It is the single authoring format; nothing agent-specific is authored twice.
- Adapter: a per-agent mapping spec plus install logic that renders or copies canonical content into that agent's expected locations (files, formats, config merges). Each adapter declares its fidelity in the capability matrix.
- Capability matrix: the published table of agent x capability (rules, skills, workflows, hooks, subagents, worklog, evidence, statusline) with support level and notes.
- Rule set: the always-on instructions. User-scope rules live in the engine; project-scope rules live in the project kit with AGENTS.md as the cross-agent canonical file.
- Skill: a folder with `SKILL.md` (and optional assets) implementing one capability or workflow step, invocable by name.
- Hook: an event-bound script (session start, stop, pre-compact, pre/post tool use) with one behavior contract and one implementation that runs on all three OSes.
- Workflow: a composed delivery pipeline (`/story`, `/sdd`) orchestrating skills with STOP gates.
- Worklog: the cross-session, per-repo, per-task state under the user's home, written by hooks and the worklog skill, so any session catches up cheaply.
- Evidence pack: the per-story folder of proof (evidence.md, session.md, pr-comment.md, `01_testings/`, `02_PicturesPDF/`) produced near commit/PR time, with an optional Playwright recording harness.
- Install manifest: the declarative file listing install operations (copy, render, register) with OS filters, consumed by both installers so their logic cannot drift.
- Sanitization guard: the tooling (local script plus CI job) that blocks personal identifiers, private project names, real org names, and absolute personal paths from entering the repo.
- Spec tree: the numbered `docs/00-...04-` structure, used both by this repo for itself and by user projects that opt into SDD.

## Relationships

- The engine is installed by the installer following the install manifest; the installer renders settings per OS and resolves shell paths.
- A project kit instantiates exactly one profile; skills and hooks resolve all project-specific values through it.
- Adapters read the canonical source and never own content; deleting an adapter never loses information.
- The capability matrix is derived from adapters and is the only place allowed to claim agent support.
- Hooks write the worklog; the worklog skill reads and updates it; session-start hooks inject it as context.
- The evidence harness writes into the evidence pack; the evidence-report skill assembles the pack's documents.
- The sanitization guard gates every commit and every CI run of this repo, including content ported from private setups.

## Glossary

- Tier 1: full fidelity (Claude Code). Tier 2: rules, skills, and workflow prompts with documented gaps (Codex, OpenCode, Cursor). Tier 3: community adapters, not maintained here.
- Render: producing an agent-specific artifact from canonical content at install time. Rendered artifacts are never committed to this repo.
- Managed file: a file the installer owns and may refresh on re-run; user-modified copies are backed up, never deleted.
- Placeholder: the only allowed form for anything environment-specific, for example `<your-org>`, `{repo_parent}/evidence`, `<evidence-root>/<id>/`.
