# claude-base

A portable base for working with Claude Code: a set of skills, hooks, a status line, settings, and a
per-project template with swappable profiles. Install it once at the user level and it works in every
project; drop the template into a project to give it a delivery pipeline and a profile for its stack
and tracker.

The design is two layers:
- A **user-level engine** (`claude/`) that applies everywhere: the skills, the hooks (worklog,
  branch helpers, document-to-markdown), the status line, and the base settings.
- A **per-project profile** (`project-template/`) that carries everything stack- or tracker-specific
  (gate commands, integration branch, evidence path, git discipline). The skills read the profile,
  so the same skills serve Azure DevOps, Jira, or plain git without edits.

## What is inside
```
claude/                     -> installs into ~/.claude
  CLAUDE.md                 global always-on rules (language, voice, persona, principles)
  settings.json             base settings (status line + worklog hooks); paths filled at install
  statusline.ps1            model / effort / account / folder / git / context / quota status line
  claude-account.ps1        optional: switch between several Claude Code accounts (see docs/ACCOUNTS.md)
  merge-settings.py         helper the switcher uses to merge settings.json without dropping keys
  skills/                   18 skills (story + sdd pipelines, TDD, quality gates, evidence, wave orchestration, ...)
  hooks/                    worklog, branch helpers, markitdown, read guard, big-result alarm, landings,
                            session audit, compaction checkpoint + summary persistence + recovery
project-template/           -> copy into each project
  CLAUDE.md                 portable working rules (voice, persona, git, TDD, evidence, pipeline)
  CLAUDE.project.md         the active profile to fill per project
  profiles/                 examples: azure-devops-dotnet, jira-git, plain-git, personal-notes
  .claude/settings.local.json   project hook wiring (branch hooks)
  .claude/agents/           five agent templates for /sdd; the installer copies the folder whole
    analyst.md              decision-shaping analysis with file:line citations, read-only toward the repo
    designer.md             UI and design deliverables from the design brief and the feature specs
    implementer.md          implements a feature or sprint slice strictly from the written specs
    implementer-light.md    the same at lower effort, for mechanical slices (tests, strings, docs, renames)
    reviewer.md             adversarial review of a branch diff before merge; a disposition, never a fix
  docs/                     spec structure for spec-driven projects (for /sdd)
herdr/                      Herdr multiplexer config + the Ctrl+Alt+N global hotkey
scripts/                    zero-token tooling: usage ledger and board, compaction watcher and report
docs/                       status line, accounts, permissions, memory notes, context economics
install.ps1                 installer (user scope, and -Project to scaffold a project)
```

## Quickstart
```
# user-level engine
powershell -ExecutionPolicy Bypass -File .\install.ps1

# same, but keep Claude Code's permission prompts instead of bypassing them
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Permissions ask

# scaffold a project
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Project C:\Repo\my-app
# then copy the closest project-template\profiles\*.md over my-app\CLAUDE.project.md and fill it
```
See `INSTALL.md` for a full setup, including another machine, Herdr, and the hotkey.

## Several accounts
Claude Code has no native multi-account support, so switching between a personal, work and client
account means logging out and back in. The optional `claude-account.ps1` gives each account its own
config directory and swaps them with an environment variable, so they can all be signed in at once,
one per window. Shared skills, plugins and agent memory stay in one place.
```
cc work -p        create the profile
cc work           open it here (signs in the first time)
cc                list the accounts and which one this window is on
```
`docs/ACCOUNTS.md` covers the wiring, the shortcuts, and the traps that cost real damage: deleting a
profile through a junction, and the PowerShell 5.1 JSON parse that silently signs an account out.

## Documentation
| Document | What it covers |
|---|---|
| `INSTALL.md` | full setup, another machine, Herdr, the global hotkey |
| `docs/ACCOUNTS.md` | several Claude Code accounts, shortcuts, and the traps |
| `docs/PERMISSIONS.md` | what bypass mode trades, and how to change it |
| `docs/STATUSLINE.md` | status line segments, wiring, and customization |
| `docs/MEMORY.md` | conventions for the agent's persistent memory |
| `docs/CONTEXT-ECONOMICS.md` | what a turn costs, what compaction costs and saves, the threshold knobs, and the boundary watcher |
| `docs/tsql-rules.md` | optional stack addendum with portable SQL Server rules |

## The pipeline
One entry point drives a task end to end, keeping a STOP gate at each step:
`/story <id>` -> work-item (branch + evidence) -> explore-and-plan -> tdd-workflow -> quality-gates
-> e2e + automation -> definition-of-done -> evidence-report. Trigger any sub-skill directly for a
partial run.

## Notes on the defaults
The base ships opinionated personal defaults you should review before adopting:
- `settings.json` sets `permissions.defaultMode: bypassPermissions` and
  `skipDangerousModePermissionPrompt: true`. That trades safety prompts for speed, and it is what
  the delivery skills assume. Install with `-Permissions ask` to keep the prompts instead, or change
  it later per project. `docs/PERMISSIONS.md` explains what you are trading and how to switch.
- `model`, `effortLevel`, `theme`, and `tui` are personal preferences; adjust to taste.
- The house style is English, Conventional Commits, no em-dashes, and no AI attribution. It is a
  default, not a law; relax it in your project profile if you disagree.

## License
MIT. See `LICENSE`.
