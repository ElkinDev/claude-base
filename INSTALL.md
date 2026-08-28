# Install

## 1. User-level engine
Installs the skills, hooks, and status line into `%USERPROFILE%\.claude`, and sets up
`settings.json` (an existing one is backed up, never overwritten).
```
powershell -ExecutionPolicy Bypass -File .\install.ps1
```
If you already had a `settings.json`, the installer writes `settings.json.new` next to it and backs
up the original. Merge the two by hand: keep your own keys and add the `statusLine` block and the
`worklog` SessionStart / PreCompact / SessionEnd / Stop hooks.

Requirements: Windows PowerShell 5.1+, `git` on PATH, and a terminal font with emoji for the status
line glyphs.

## 2. A project
Scaffold a project so it gets the working rules, a profile to fill, and the branch hooks:
```
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Project C:\Repo\my-app
```
Then pick a profile: copy the closest `project-template\profiles\*.md` over `my-app\CLAUDE.project.md`
and fill in the blanks (tracker, gate commands, integration branch, evidence path, git discipline).

For a spec-driven project (a new build governed by a `docs/` spec structure), add `-Sdd` to also
scaffold `.claude/agents/` and the `docs/` spec structure, then drive it with `/sdd`:
```
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Project C:\Repo\my-app -Sdd
```

The installer copies the agents folder by wildcard, so the project gets all five templates:

- `analyst` decision-shaping analysis with file:line citations, read-only toward the repo
- `designer` UI and design deliverables from the design brief and the feature specs
- `implementer` implements a feature or sprint slice strictly from the written specs
- `implementer-light` the same at lower effort, for mechanical slices (tests, strings, docs, renames)
- `reviewer` adversarial review of a branch diff before merge; a disposition, never a fix

If you keep the project rules local (not committed), add to the project's `.git/info/exclude`:
```
CLAUDE.project.md
.claude/
```

## 3. Herdr and the global hotkey (optional)
See `herdr/README.md`. In short:
1. Install Herdr; copy `herdr/config.toml` to `%APPDATA%\herdr\config.toml`
   (`~/.config/herdr/config.toml` on macOS/Linux).
2. Run Herdr's Claude integration so it tracks Claude Code sessions.
3. Install the Ctrl+Alt+N hotkey:
   ```
   powershell -ExecutionPolicy Bypass -File .\herdr\hotkey\setup-hotkey.ps1
   ```

## 4. Another device (clone and go)
1. Clone this repo.
2. Run `install.ps1` (user scope).
3. Copy `herdr/config.toml` into place and run the Herdr Claude integration.
4. Run `herdr/hotkey/setup-hotkey.ps1`.
5. Review the defaults in `settings.json` (permission mode, model, effort) and adjust.

## What the installer does not touch
- It never deletes anything under `~/.claude`; it merges skills and hooks and backs up settings.
- It does not write the Herdr hook (`herdr-agent-state.ps1`); Herdr's own integration owns that file.
- Project scaffolding skips files that already exist unless you pass `-Force`.
