# Seats

A seat is a chair, not a task. It says who a session is for its whole life: which board it drives, which laws it works under, what it reads first in the morning and what it owes before the day closes. A lane takes a brief and ends when the work lands. A seat outlives the work.

Two long-lived sessions need a chair without a human typing a paragraph into every fresh pane, and the mechanism has to survive a folder change, a profile switch and another machine. That is what this page describes.

## What ships

Two files, plain markdown, no frontmatter:

```
claude/seats/orchestrator.md
claude/seats/analyst.md
```

The installer copies them to `<kit home>\seats\`, beside `agents\` and `skills\`. They are deliberately not agent definitions: an agent file in `agents\` becomes a delegable subagent type, and a seat must never be something an agent can be asked to become. Nothing lists them, nothing can delegate to them.

The seat reaches a session two ways at once. The text is appended to the default system prompt with `--append-system-prompt-file`, and `CLAUDE_ROLE` names the chair in the environment for the hooks to read. Appended, not substituted: `--agent` replaces the default prompt and takes the environment block, the model identity and the memory instructions with it, which is a high price for one paragraph of text.

## The launch lines

```powershell
cc work -Role orchestrator          a fresh seated session
cc work -Role orchestrator -- -r    resume one, then pick it from the list
cc work -Role analyst               the other seat
cc work                             no role, no seat: a lane
```

Write `-Role` in full. A wrapper that forwards flags it does not know straight to `claude` can swallow the short `-o` alias, and then the role is gone with no error to read.

A seat is appended only when `<kit home>\seats\<role>.md` exists, so a role without a seat file, `lane` and `research` today, launches exactly as it did before.

## Why the continue flag is refused on a seat

Profiles share the projects directory, so `-c` and `--continue` load the most recent conversation of that folder, which may belong to the other seat. Appending the right seat text to the wrong conversation does not save it: the loaded session keeps answering as the chair it already held, with the new seat text sitting unread above it. A launcher that knows about seats refuses `-c` and `--continue` on a seated role and prints one line saying so. Resume a seat with `-- -r` and the picker, or `-- -r <session id>`.

## What the hooks print

`compact-recover.py` opens its block with the seat, on startup, resume, clear and fork, and again after a compaction:

```
Seat: orchestrator
Resume brief: <briefs dir>\orchestrator-resume-2026-09-07.md (read it first)
```

A pane with no seat says `Seat: none (lane)`. A pane launched by nothing at all, a bare restore after a logon or a session started by hand, adds the loud line `Not launched through the account launcher: no seat, no window, no --no-chrome. Relaunch through it before working.`, which is the standing rule made visible instead of silently lost. A subagent prints none of this: it inherits the environment of the session that launched it, so the payload, not the variable, is what tells them apart, and an agent holds no chair.

`prompt-log.py` adds one line to a seated session once the closing round has opened, 45 minutes before the closing hour and after it: the day ends, write the resume brief, land or stop every agent.

| Variable | What it does |
|---|---|
| `CLAUDE_ROLE` | names the chair: `orchestrator`, `analyst`, anything else is a lane |
| `CLAUDE_BRIEFS_DIR` | where `<seat>-resume-<date>.md` lives; default `~/.claude/briefs`, absent means nothing is printed |
| `CLAUDE_CLOSING_HOUR` | `HH:MM`, local; unset means no closing round and no line |
| `CLAUDE_TEST_NOW` | `HH:MM`, replaces the clock for the tests of the closing round |

## Mac and Linux

The seats are two markdown files and nothing else, so they work anywhere Claude Code runs. The installer and the account launcher are PowerShell, so on a POSIX machine the files are copied by hand and the line is typed or aliased:

```sh
seat=~/.claude/seats/orchestrator.md
[ -f "$seat" ] || { echo "no seat file at $seat"; exit 1; }
CLAUDE_ROLE=orchestrator claude --append-system-prompt-file "$seat"
```

The check is not decoration: a missing append file is a hard launch failure, `Error: Append system prompt file not found`, and nothing starts. The POSIX installer and the launcher twin are deferred to their own item. Nothing here fails without them, because a session launched with no seat is a plain session.

## What a seat is not

It is not an agent. Agents launched from a seat keep their own definitions and are never the seat: no chair, no register, no merge right. It is not memory either. A resumed session carries its seat in its own conversation, which is why a resume keeps working when the flag is forgotten, and why the seat block prints what the chair is on every start rather than assuming it.
