---
name: herdr-driving
description: How the kit drives Herdr panes: names, prompting, waiting, trust dialogs and what to do when Herdr updates. Use when launching, prompting or watching a session in a Herdr pane.
---

# Driving Herdr

Herdr is the terminal multiplexer the kit runs sessions inside. It is optional, and every caller
treats it that way. Everything below is verified against the build recorded in
`herdr/verified-version.txt`; when the installed build differs, re-verify before trusting a line
(see the last section).

## Gate every call

Two conditions, both required, checked before anything else:

- `HERDR_ENV` is `1` (the process is running inside a Herdr pane).
- `herdr` is on PATH.

Without either, skip silently: no error, no warning, no fallback. That is what the launcher
(`claude/claude-account.ps1`) and the two hooks (`claude/hooks/landing.py`,
`claude/hooks/alarm-big-result.py`) do, and it is why a machine with no Herdr installs and runs the
kit unchanged.

## Identify a pane by name, never by a remembered id

Herdr renumbers workspaces when it restarts: a pane that was `w3:p1` came back as `w4:p1` overnight.
A pane id is therefore valid only for the call you resolved it in.

The commands take a pane id as their target. The Claude Code session name is not a target:
`herdr agent get <session name>` answers `agent_not_found`. So the shape of every call is: read
`herdr agent list`, find the pane by name, use the `pane_id` from that same read.

`herdr agent list` returns one object per pane, with the fields that matter here:

- `pane_id`: the target for every other command. Never cache it across a Herdr restart.
- `name`: Herdr's own agent name, set with `herdr agent rename <pane> <name>` and removed with
  `herdr agent rename <pane> --clear`. Null until something sets it.
- `terminal_title_stripped`: the Claude Code session name, from `claude --name <name>` at launch or
  `/rename` inside the session. With no name it is an automatic summary of the current task, so it
  is a reliable selector only when the session was named on purpose.
- `agent` (`claude` for a Claude Code pane), `agent_status`, `cwd`, `tab_id`, `workspace_id`.

Tab labels come from `herdr tab list`, and Herdr's default labels are bare numbers, so a label worth
matching is one the launcher set. A `herdr pane rename` label is visible only in `herdr pane get`,
so scripts do not see it. `scripts/compact-at-boundary.py` matches on the session name, then Herdr's
name, then the tab label, then the terminal title, in that order.

Inside a pane, `HERDR_PANE_ID`, `HERDR_TAB_ID` and `HERDR_WORKSPACE_ID` are in the environment, which
is how a session learns its own pane without a lookup.

## The naming convention

Roles the launcher uses: `orchestrator`, `research`, and `lane` (`lane-<feature>` when several run at
once). The launcher passes `claude --name <role>` only when a role was given, because `--name` also
renames a session resumed with `-c`: naming every launch would rewrite the name of a session that
already had a good one.

## Prompting and waiting

```
herdr agent prompt <pane> <text> [--wait] [--until idle|working|blocked|done|unknown] [--timeout MS]
herdr agent wait   <pane>        [--until <state>]... [--timeout MS]
```

`prompt` types the text into the pane and presses enter. Facts that decide how a script is written:

- With `--wait`, a submission that starts from a non-working state must show an observed state change
  within 5000 ms, otherwise the call returns `agent_prompt_stalled`. A shorter `--timeout` returns
  `timeout` instead.
- A pane that is already blocked is refused with `agent_blocked` before anything is typed.
- Neither call tracks turns. On a session that is already working, the wait may match the end of the
  turn that was already running, and Claude Code queues the typed text as its next message. So a
  prompt to a busy session is delivered, but the wait proves nothing about it.
- Without `--until`, both match `idle`, `done` or `blocked`. Without `--timeout`, the wait is
  indefinite.
- `herdr agent wait` answers `agent_not_found` while the session has not registered yet, and
  `timeout` when the window expires. Both are normal on a pane that is still starting.
- After a compaction with no turn behind it the state reads `done`, not `idle`. A script waiting for
  "ready for input" must accept both; the boundary watcher passes `--idle-states idle,done`.

From PowerShell, a double quote inside the text splits the argument. Use single quotes inside the
text, or a here-string with no double quotes in it.

## Slash commands

Never send `/exit` or any `/command` through Git Bash. MSYS rewrites a leading slash into a Windows
path, so `herdr agent prompt w1:p5 /exit` delivers `C:/Program Files/Git/exit`, the session answers it
as a question, and the command never runs. Send slash commands from PowerShell, cmd or Python, or
prefix the call with `MSYS_NO_PATHCONV=1`.

## The trust dialog on a new folder

A session opened in a folder Claude Code has not seen stops at the trust dialog, whose default choice
is "No, exit". Accepting it takes key presses, not a prompt:

```
herdr agent send-keys <pane> down
herdr agent send-keys <pane> enter
```

`send-keys` accepts more than one key per call; the two-call form above is the one verified in use.
`esc` is the canonical Escape key name. Wait for `idle` after the dialog, then send the first prompt.
A prompt sent before the dialog is answered is typed into the dialog.

## Launching a session in a new pane

```
herdr tab create --cwd <folder> --label <label> --no-focus
herdr pane run <pane> "<command>"
```

`tab create` returns JSON carrying the new pane id, which `pane run` then takes as its target. Pick
the workspace with `herdr workspace list` and pass `--workspace <id>`; `--no-focus` keeps the new tab
out of the way of whoever is watching. The launcher labels its tabs so the role is visible in
`herdr tab list`.

## Notifications

```
herdr notification show <title> [--body <text>] [--sound none|done|request]
```

`landing.py` raises one with `--sound done` when a landing is recorded, and `alarm-big-result.py`
raises one when a tool result crosses the alarm threshold. Both check the gate first and both are
silent when Herdr is absent.

## When Herdr updates

Herdr is a preview build and its CLI moves. The routine, in order:

1. `python scripts/doctor.py`.
2. A `warn herdr` line means the installed version differs from `herdr/verified-version.txt`. That
   alone is not a problem, it is a reason to read the next lines carefully.
3. A `FAIL herdr <subcommand>` means that subcommand is gone or renamed. Read its new `--help` and
   re-verify the code that drives it: `claude/claude-account.ps1`, `scripts/compact-at-boundary.py`,
   `claude/hooks/landing.py`, `claude/hooks/alarm-big-result.py`.
4. When the doctor is clean again, update `herdr/cli-surface.txt` and `herdr/verified-version.txt` in
   the same commit as the fix, so the two files never describe a build nobody drove.

Never adjust a claim in this file from a release note. Run the command, read what it answers, then
write it down.
