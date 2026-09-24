# Agent support

What the kit gives each coding agent, and the only place in this repository that claims support
(F03, FR-025; the contract is in `docs/02-architecture/data-contracts.md`). A claim anywhere else
links here. A column lands with the change that makes it true, and every such change updates this
table.

| Capability | Claude Code | pi |
|---|---|---|
| rules | full | full [1] |
| skills | full | partial [2] |
| workflows | full | none [3] |
| hooks | full | none [4] |
| subagents | full | none [5] |
| worklog | full | none [4] |
| evidence | full | partial [6] |
| statusline | full | none [4] |

[1] The project's `AGENTS.md` is the canonical rules file (ADR-010). Claude Code reads it through
the `@AGENTS.md` import in `CLAUDE.md` and skips an `AGENTS.md` it has already loaded, so the import
never reads it twice. pi loads `AGENTS.md` natively from `~/.pi/agent/AGENTS.md`, from the parent
directories and from the working directory, and concatenates them (pi README, "Context Files").
`scripts/agents-md.py` keeps the managed sections of a rules file in step with their source.

[2] pi reads skills in the Agent Skills format (`SKILL.md`) from `~/.pi/agent/skills/`,
`~/.agents/skills/`, `.pi/skills/` and `.agents/skills/`. The kit's skills are that format, but the
installer does not place them there, and some of them name Claude Code tools.

[3] The pipeline entry points (`/story`, `/sdd`) are Claude Code skills. pi has prompt templates
(`/name`), and the kit ships none for it. The pipeline itself is written agent-neutrally in
`AGENTS.md` (`cb:pipeline`), so a pi session can follow it by hand.

[4] These run as Claude Code hooks and a Claude Code status line. pi does the same through
TypeScript extensions, and the kit ships none.

[5] pi has no subagents by design; it suggests separate pi instances instead. The roles-by-model
pattern of `cb:pipeline` still applies, with one role at a time.

[6] `scripts/evidence-path.py` and the evidence layout are plain files and Python, usable from any
agent. The evidence-report skill that writes the pack is a Claude Code skill.

Codex, OpenCode and Cursor are Tier 2 in ADR-008. Their columns land with their adapters (F04 to
F06). None is claimed until then.
