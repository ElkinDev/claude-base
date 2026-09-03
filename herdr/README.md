# Herdr setup

Herdr is a terminal multiplexer for AI agents. You run `claude` inside a Herdr pane so Herdr can
track agent state, show a status rail, and play a sound or a desktop toast when an agent finishes or
needs input in a background workspace. This folder records the personal setup so it can be
reproduced on another machine.

## What is in this folder
- `config.toml` - the personal Herdr config (keymap, sounds, toasts). Copy it to the config path.
- `hotkey/launch-herdr.cmd` - launcher that opens Herdr in its own Windows Terminal window.
- `hotkey/setup-hotkey.ps1` - creates the global Ctrl+Alt+N shortcut that runs the launcher.
- `verified-version.txt` - the Herdr build this kit was verified against.
- `cli-surface.txt` - the subcommands the kit drives, one per line, probed by
  `python scripts/doctor.py`.

How to drive a pane from a session (naming, prompting, waiting, trust dialogs, slash commands): see
the `herdr-driving` skill in `plugins/orchestration/skills/herdr-driving/`. It reaches a machine
either through the installer, under its bare name, or from the marketplace as
`/orchestration:herdr-driving`.

## Install on a new machine
1. Install Herdr (Windows preview build). After install, `herdr.exe` lives under
   `%LOCALAPPDATA%\Programs\Herdr\bin\` and is on PATH, so `herdr` works from any terminal.
2. Copy the config into place:
   - Windows: `%APPDATA%\herdr\config.toml`
   - macOS / Linux: `~/.config/herdr/config.toml`
   Herdr live-reloads it; a running server also reloads with `herdr server reload-config`.
3. Install the Claude integration so Herdr sees Claude Code sessions:
   ```
   herdr integration install claude
   ```
   Herdr writes its own SessionStart hook at `%USERPROFILE%\.claude\hooks\herdr-agent-state.ps1`
   and wires it in your Claude settings. That file is managed by Herdr (reinstalling or updating
   overwrites it), so do not hand-edit it; add custom hooks beside it instead. `herdr integration
   status` lists every integration and where its file went.
4. Set up the global hotkey (optional, see below).
5. Run `python scripts/doctor.py` and check the herdr lines are all `ok`.

## The verified version and the surface file
`verified-version.txt` holds one line, the exact `herdr --version` output this kit was checked
against. `cli-surface.txt` holds the subcommands the kit calls, one per line. The doctor compares
the installed version with the first and probes `herdr <subcommand> --help` for every line of the
second.

When Herdr updates:
1. `python scripts/doctor.py`.
2. A `warn herdr` line only says the build moved. A `FAIL herdr <subcommand>` says the surface
   moved: read the new `--help` and fix the code that drives it, which is one of
   `claude/claude-account.ps1`, `scripts/compact-at-boundary.py`, `claude/hooks/landing.py`,
   `claude/hooks/alarm-big-result.py`.
3. When every line is `ok` again, update `cli-surface.txt` and `verified-version.txt` in the same
   commit as the fix, so the two files always describe a build that was actually driven.

## Keymap (from config.toml)
The prefix is remapped to `Ctrl+A` (Herdr default is `Ctrl+B`). Press the prefix, then the action
key:
- `Ctrl+A ?` - list every configured keybinding
- `Ctrl+A s` - settings menu (sounds, toasts, integrations)
- `Ctrl+A w` - workspace picker; `Ctrl+A g` - session browser
- `Ctrl+A c` - new tab; `Ctrl+A 1..9` - jump to tab N
- `Ctrl+A h` / `Ctrl+A l` - focus the pane left / right
- `Ctrl+A b` - minimalist mode (collapsed status rail)
- `Alt+1..9` - jump straight to agent row N (no prefix). Terminals do not reliably deliver
  `Ctrl+<digit>`, so agent jumps use `Alt+<digit>`.

Toasts are delivered as OS desktop notifications (`[ui.toast] delivery = "system"`) and sounds are on
for agent state changes in background workspaces (`[ui.sound] enabled = true`).

## Detach and reattach
`Ctrl+A q` detaches; run `herdr` again to reattach to the running session.

## Global hotkey: Ctrl+Alt+N opens Herdr
On Windows a `.lnk` shortcut placed in the Start Menu, with a Hotkey property set, makes a key combo
global. The setup here is a launcher plus that shortcut:
- `launch-herdr.cmd` runs `wt -w herdr new-tab --title herdr -d "<dir>" cmd /k herdr`, opening Herdr
  in a dedicated Windows Terminal window. With no argument it opens in your user profile folder; edit
  the default (or pass a directory) to point at your main repo.
- `setup-hotkey.ps1` creates `herdr (Ctrl+Alt+N).lnk` in the Start Menu, pointing at the launcher,
  with Hotkey `Ctrl+Alt+N`.

Install the hotkey:
```
powershell -NoProfile -ExecutionPolicy Bypass -File .\hotkey\setup-hotkey.ps1
```
Pass a different key or launcher if you want: `-Hotkey "Ctrl+Alt+H" -Target "C:\path\launch-herdr.cmd"`.
The `.lnk` must stay in the Start Menu for the global hotkey to keep working. Delete it to disable
the hotkey.

## Driving a pane from a script
The Claude integration gives every pane a state (`idle`, `working`, `blocked`, `done`, `unknown`)
that scripts can read and wait on. Verified on the Windows preview build:
- `herdr agent list` prints each pane with its session id and state. `herdr agent wait <pane>
  [--until <state>]... [--timeout <ms>]` blocks until one matches (`idle`, `done` or `blocked`
  when no `--until` is given) and answers `timeout` or `agent_not_found` while the session has
  not registered yet.
- `herdr agent prompt <pane> <text> [--wait] [--until <state>]... [--timeout <ms>]` types the
  text and presses enter. With `--wait`, a pane that is not working must show a state change
  within 5000 ms or the call returns `agent_prompt_stalled`; a pane that is already blocked is
  refused with `agent_blocked` before anything is typed. It does not track turns: on a working
  pane the wait may match the end of the turn already running.
- After a compaction with no turn behind it Herdr reports `done`, not `idle`. A script that
  waits for "ready for input" must accept both; the watcher in `scripts/compact-at-boundary.py`
  does (`--idle-states idle,done`).
- A session opened in a folder it has not seen stops at the trust dialog, whose default choice
  is "No, exit". `herdr agent send-keys <pane> down` and then `herdr agent send-keys <pane> enter`
  accept it; wait for `idle` before the first prompt. `esc` is the Escape key name.
- A leading-slash argument sent through Git Bash (MSYS) becomes a Windows path:
  `herdr agent prompt w1:p5 /exit` delivers `C:/Program Files/Git/exit` and the session answers
  it as a question, one turn wasted and the command not run. Send slash commands from
  PowerShell, cmd or Python, or prefix the command with `MSYS_NO_PATHCONV=1`.
- Select panes by name, never by pane id: Herdr renumbers workspaces when it restarts
  (`w3:p1` became `w4:p1` overnight). The name that travels with the session is the one Claude
  Code stores in the transcript (`claude --name <role>` at launch or `/rename <role>` inside;
  the account launcher passes `--name <role>` on its own); Herdr reports it as
  `terminal_title_stripped` (otherwise that field is a summary of the current task). Herdr's
  own agent name, `herdr agent rename <pane> <name>` (`--clear` removes it), comes back as
  `name` in `herdr agent list`; tab labels come from `herdr tab list` (default labels are bare
  numbers); a `herdr pane rename` label is only visible in `pane get`, so scripts do not see
  it. Inside a pane, `HERDR_PANE_ID`, `HERDR_TAB_ID` and `HERDR_WORKSPACE_ID` are in the
  environment.
