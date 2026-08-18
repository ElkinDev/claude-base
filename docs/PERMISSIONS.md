# Permission modes

Claude Code asks before it does things that change your machine: writing files, running shell commands, making network calls. How much it asks is a setting, and this base ships the least cautious option on purpose. Read this before adopting it.

## The two modes

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1                       # bypass, the default here
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Permissions ask      # keep the prompts
```

**`ask`** is Claude Code's normal behaviour. It asks before editing a file it has not been allowed to edit, before running a command, and before anything with side effects. You approve each one, or approve a pattern for the session.

**`bypass`** turns those prompts off. Claude Code edits, runs commands and installs things without stopping. It is genuinely faster, and it is what the delivery skills in this base assume: a pipeline that stops for approval on every step is not a pipeline.

In `settings.json` those are two keys moving together:

```json
{
  "permissions": { "defaultMode": "bypassPermissions" },
  "skipDangerousModePermissionPrompt": true
}
```

The first is the mode. The second suppresses the warning Claude Code shows when a session starts in that mode, so without it you would confirm the same thing on every start.

## What you are actually trading

With bypass on, an agent that misreads your intent does not stop to check. It can delete files, force-push, run an installer, or make a change across a repo in one go. There is no undo prompt between the decision and the action.

That risk is manageable when the work is in a git repo with a clean tree, when you review diffs before committing, and when the folder is not shared with anything you cannot lose. It is not manageable when you are working directly on production configuration, in a folder without version control, or with credentials lying around.

Judge it per folder, not once and forever.

## Changing it later

Nothing is baked in. Edit `~/.claude/settings.json` and restart the session:

```json
{ "permissions": { "defaultMode": "default" }, "skipDangerousModePermissionPrompt": false }
```

`defaultMode` accepts `default` (ask), `acceptEdits` (file edits go through, everything else asks), `plan` (read and plan only, no changes), and `bypassPermissions`.

`acceptEdits` is a reasonable middle ground: the agent writes code freely but still asks before running commands.

You can also switch mode inside a session with `/permissions`, and press `shift+tab` to cycle modes on the fly, which is often enough: work in `ask`, flip to bypass for a stretch of mechanical work, flip back.

## Per project

Project settings override user settings. To keep bypass globally but stay careful in one repo, put this in that repo's `.claude/settings.local.json`:

```json
{ "permissions": { "defaultMode": "default" } }
```

Permission **rules** behave differently from other settings: they merge across scopes rather than replace each other, so an allow or deny list in a project adds to the user-level one instead of overriding it.

## If you run several accounts

The account switcher copies `settings.json` from the default config directory into each profile, so the permission mode follows. If you want one account to be more careful, edit that profile's `settings.json`. The switcher notices the file changed and stops overwriting it. See `ACCOUNTS.md`.
