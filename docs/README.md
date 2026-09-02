# docs/

This folder holds two kinds of documents.

## Reference docs (shipped with the toolkit)

- `ADOPTION.md`: installing into a repository that already has company rules, hooks and lint wiring: what the kit writes, what it never touches, how to keep every kit file out of the team's history, and how to roll a whole run back.
- `EVIDENCE.md`: where evidence lives (beside the repository, never inside it), the pack layout, the portable spec grammar, and the resolver every skill calls.
- `MEMORY.md`: conventions for the agent's persistent file-based memory.
- `STATUSLINE.md`: statusline wiring and customization.
- `ACCOUNTS.md`: running several Claude Code accounts, the shortcuts, and the traps.
- `PERMISSIONS.md`: what bypass mode actually trades, and how to change it.
- `CONTEXT-ECONOMICS.md`: what a turn costs, what compaction costs and saves, the compaction knobs of Claude Code, the checkpoint hooks plus the boundary watcher that compact at a sensible moment, and the quota wake that resumes a pane when its five hour window reopens.
- `tsql-rules.md`: optional stack addendum with portable SQL Server rules.
- `WORKLOG.md`: design spec of the cross-session worklog. Planned for Sprint 03, not published yet.

## Spec tree (development of this repo itself)

This repo dogfoods its own spec-driven workflow. The folders below are the source of truth for building the toolkit, consumed by the `/sdd` skill.

- `00-product/`: vision, scope, and the domain model.
- `01-requirements/`: functional requirements (FR-xxx), non-functional requirements (NFR-xxx), and user stories (US-xxx).
- `02-architecture/`: architecture overview, data contracts, security and privacy, and the ADRs (numbered decisions).
- `03-features/`: one spec per feature (F01 to F14), each mapping the FR/US it satisfies and carrying its own status line. F14 (quota wake) is implemented in the tree and stays a proposal until it is reviewed and merged.
- `04-plan/`: roadmap, backlog, and `sprints/` with one file per sprint carrying its scope and definition of done.

There is no `05-design/` folder because the toolkit has no UI.

Status: all ADRs are Proposed until the maintainer approves them. Implementation must not start on a feature whose ADR dependencies are still open. When specs and reality diverge, fix the spec first, then the code.
