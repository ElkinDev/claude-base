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

`claude-account.ps1` knows `orchestrator` and `analyst` as seated roles. It appends the seat file, refuses the continue flag (the line it prints, and why, are two sections down), names a fresh session and hands it a start line, so a chair costs one flag and nothing typed:

```powershell
cc work -Role orchestrator          a fresh seated session
cc work -Role orchestrator -- -r    resume one, then pick it from the list
cc work -Role analyst               the other seat
cc work                             no role, no seat: a lane
```

Write `-Role` in full. A wrapper that forwards flags it does not know straight to `claude` can swallow the short `-o` alias, and then the role is gone with no error to read.

The seat file is `<seats dir>\<role>.md`, where the seats directory is `CLAUDE_SEATS_DIR` when that variable is set and `%USERPROFILE%\.claude\seats` otherwise. The launcher tests the file before it adds the flag, because claude refuses to start when an append file is missing; when it is absent the launch costs one line, never the session:

```
Seat file missing: <seats dir>\<role>.md; the session opens without a seat.
```

A role with no seat file, `lane` and `research`, keeps launching exactly as it did before the seats existed.

On a fresh launch only, the session is named `<role>-MMdd-HHmm`, so the resume picker separates today's chair from yesterday's, and the start line goes last in the command, where claude reads a positional argument as the first prompt:

```
Session start: read the newest resume brief of your seat, then the state sheet, then continue with its first actions.
```

A launch that reopens a conversation is neither renamed nor handed a start line: the chair it reopens already carries both. `-r`, `--resume`, `--resume=<id>` and the attached `-r<id>`, which claude parses the same way, all read as a resume.

A name of your own, `-- --name x`, wins over the generated one, and a prompt of your own wins over the start line. When the last argument of a seated launch does not begin with a dash, the launcher reads it as the prompt you typed, adds no start line, and says so on one line:

```
Start line not added: the last argument reads as your prompt; type the start of day yourself.
```

A trailing option value, `--model opus` or `--add-dir <path>`, is that last argument by the same rule, since a launcher cannot know which options take a value, so the line appears there too and the start of day is yours to type.

`--no-chrome` joins the argument array before the seat block, for every role but `research` and unless a chrome flag was passed by hand, so the command that runs reads `claude --name <role>-MMdd-HHmm --append-system-prompt-file <seat> --no-chrome '<start line>'`, the start line stays the last token, and the in-window path receives `--no-chrome` as well.

A seated session also carries `CLAUDE_BRIEFS_DIR`, default `%USERPROFILE%\.claude\briefs`, and `CLAUDE_CLOSING_HOUR`, default `22:00`. That is where the hooks below read the resume brief and the closing round from.

`cc work -Role orchestrator -ShowEnv` prints the plan and exits without opening a session, which is how a pane is checked against this page. Beside the context variables it prints `COMMAND=`, the whole command line; `SEAT=`, the seat file or `none`; `FRESH=`, `true` or `false`; `START=`, the start line, `none`, or `none (positional given)` when a prompt was typed; and `CLAUDE_BRIEFS_DIR=` and `CLAUDE_CLOSING_HOUR=`, both `(unset)` on a role with no chair.

## The line without the launcher

The launcher is PowerShell and it is not the mechanism, only the convenience. On a machine without it, or in any other shell, a seat is two things typed by hand, the variable and the append flag:

```sh
CLAUDE_ROLE=orchestrator claude --append-system-prompt-file ~/.claude/seats/orchestrator.md
```

```powershell
$env:CLAUDE_ROLE='orchestrator'; claude --append-system-prompt-file "$env:USERPROFILE\.claude\seats\orchestrator.md"
```

Neither line names the session or hands it a start line, so a pane opened this way reads its brief because a person said so. A pane opened with neither the launcher nor these lines is a lane, and the first block line says so.

## Why the continue flag is refused on a seat

Profiles share the projects directory, so `-c` and `--continue` load the most recent conversation of that folder, which may belong to the other seat. Appending the right seat text to the wrong conversation does not save it: the loaded session keeps answering as the chair it already held, with the new seat text sitting unread above it. That is why the launcher refuses `-c`, `--continue` and `--continue=<id>` on a seated role, printing one line and exiting without opening a session:

```
A seated role never continues the most recent conversation of a folder (the profiles share it); resume with -r and the picker, or -r <id>.
```

A resume is allowed and is what a chair wants: `-r` and the picker, `-r <session id>`, `-r<session id>` attached or `--resume=<session id>`, never the continue flag.

## What the hooks print

`compact-recover.py` opens its block with the seat, on startup, resume, clear and fork, and again after a compaction:

```
Seat: orchestrator
Resume brief: <briefs dir>\orchestrator-resume-2026-09-07.md (read it first)
```

A brief is named `<seat>-resume-<YYYY-MM-DD>.md`, with `-HHMM` appended when a seat writes a second brief of the same day, and the newest by name wins, which is why the hook sorts the stem and not the whole filename: over filenames the dash of the hour would sort before the dot of `.md` and the morning brief would win.

A pane with no seat says `Seat: none (lane)`. A pane launched by nothing at all, a bare restore after a logon or a session started by hand, adds the loud line `Not launched through the account launcher: no seat, no window, no --no-chrome. Relaunch through it before working.`, which is the standing rule made visible instead of silently lost. A subagent prints none of this: it inherits the environment of the session that launched it, so the payload, not the variable, is what tells them apart, and an agent holds no chair.

`prompt-log.py` adds one line to a seated session once the closing round has opened: the day ends, write the resume brief, land or stop every agent. The round runs from 45 minutes before the closing hour to two hours after it, wrapped around midnight so a closing hour after it behaves like any other, and the line is added only to a prompt a person typed, never to a slash command or a harness tag.

| Variable | What it does |
|---|---|
| `CLAUDE_ROLE` | names the chair: `orchestrator`, `analyst`, anything else is a lane |
| `CLAUDE_SEATS_DIR` | where the launcher looks for `<role>.md`; default `%USERPROFILE%\.claude\seats` |
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
