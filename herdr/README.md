# Herdr setup

Herdr is a terminal multiplexer for AI agents. You run `claude` inside a Herdr pane so Herdr can
track agent state, show a status rail, and play a sound or a desktop toast when an agent finishes or
needs input in a background workspace. This folder records the personal setup so it can be
reproduced on another machine.

## What is in this folder
- `config.toml` - the personal Herdr config (keymap, sounds, toasts). Copy it to the config path.
- `hotkey/launch-herdr.cmd` - launcher that opens Herdr in its own Windows Terminal window.
- `hotkey/setup-hotkey.ps1` - creates the global Ctrl+Alt+N shortcut that runs the launcher.

## Install on a new machine
1. Install Herdr (Windows preview build). After install, `herdr.exe` lives under
   `%LOCALAPPDATA%\Programs\Herdr\bin\` and is on PATH, so `herdr` works from any terminal.
2. Copy the config into place:
   - Windows: `%APPDATA%\herdr\config.toml`
   - macOS / Linux: `~/.config/herdr/config.toml`
   Herdr live-reloads it; a running server also reloads with `herdr server reload-config`.
3. Install the Claude integration so Herdr sees Claude Code sessions. Herdr writes its own
   SessionStart hook at `%USERPROFILE%\.claude\hooks\herdr-agent-state.ps1` and wires it in your
   Claude settings. That file is managed by Herdr (reinstalling or updating overwrites it), so do not
   hand-edit it; add custom hooks beside it instead.
4. Set up the global hotkey (optional, see below).

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
