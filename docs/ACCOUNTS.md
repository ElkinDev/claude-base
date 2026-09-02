# Multiple Claude Code accounts

Claude Code has no native profile or multi-account support. `/login` overwrites the session, so switching between a personal account, a work account and a client account means logging out and back in every time.

This document explains the mechanism, the design decisions behind `claude/claude-account.ps1`, and the traps that cost real damage on the way. You can use the script or build your own from this.

## The mechanism

Claude Code reads `CLAUDE_CONFIG_DIR`. When set, it uses that directory instead of `~/.claude` for everything, including authentication.

On Windows the OAuth token lives in `<config dir>/.credentials.json`, not in Credential Manager, and the account identity is cached in `<config dir>/.claude.json` under `oauthAccount`. Both move with the variable, so pointing it at a different folder swaps the entire account.

```powershell
$env:CLAUDE_CONFIG_DIR = "$env:USERPROFILE\.claude-accounts\work"
claude
```

That is the whole trick. Everything else is ergonomics and damage control.

Two properties matter. The variable is set with `$env:`, so it lives only in that shell, which means several accounts can be signed in at the same time in different windows. And `~/.claude` stays the fallback for plain `claude`, so it cannot be moved or deleted, only relabelled.

**Never use `setx` or the Windows environment variable UI for this.** That makes it global and every session switches at once, including ones already running.

## What to isolate and what to share

Isolate only what must be isolated:

| Path | Isolated | Why |
|---|---|---|
| `.credentials.json` | yes | the OAuth token, this is the account |
| `.claude.json` | yes | account identity, per-project trust, MCP config, caches |
| `skills/`, `plugins/`, `hooks/`, `agents/`, `commands/` | shared | you want the same tooling on every account |
| `projects/` | shared | session transcripts and agent memory |

Sharing `projects/` is the one worth thinking about. It means every account sees the same session history, so `claude -r` from any account lists sessions started under another one. For one human with several accounts that is what you want. If you are sharing a machine, it is not.

The script shares by creating **directory junctions** with `mklink /J`, which needs no administrator rights, unlike symbolic links.

`CLAUDE.md` and `settings.json` are copied rather than linked, because a profile may legitimately want a different model or effort level.

## Creating an account

```powershell
cc work -p          create the profile without opening a session
cc work             open it, which triggers the sign-in the first time
cc                  list the accounts and confirm which e-mail landed where
```

### The trap that ruins this

The sign-in opens a browser. If you are already signed in to claude.ai there, the OAuth flow accepts that session without asking and you end up with the same account in two profiles.

Sign out of claude.ai first, or use a private window, or a separate browser profile per account. Then check with `cc` that the e-mail is the one you expected before creating the next account.

## Shortcuts and icons

Typing full account names all day is friction, so each account can carry a one letter shortcut and an icon.

```powershell
cc work -a w        shortcut: cc w
cc work -i 1F4BC    icon: briefcase
cc personal -a p -i 1F3E0
```

One shortcut per account, on purpose. Setting a second one removes the first, because two names for the same thing is exactly the confusion shortcuts are meant to remove.

Pick shortcuts that do **not** share a prefix. If your accounts are `nick`, `nick23` and `nicka`, then `nick` is a prefix of the other two, the discriminator is the last character, and a typo silently opens the wrong account: same skills, same memory, same folder, only a different word in the status line. In a work repo that means committing under the wrong identity. One distinct letter each is safer than a shared stem.

Icons are stored as hex code points rather than the emoji itself, because PowerShell 5.1 and JSON files disagree about surrogate pairs and the emoji corrupts on a rewrite.

Both show up in the list and in the status line, so the window always tells you which account it is on. See `STATUSLINE.md`.

## Working with them

The account opens in the window you called it from, and that window stays on that account until you close it, including after you exit `claude`. The window becomes that account's window.

```powershell
cd C:\code\my-app
cc work                 open here
cc work -w              open in a separate tab or window instead
cc work -Dir C:\other   open somewhere else
```

To pass flags to `claude` itself, put them after `--`:

```powershell
cc work -- -c           continue that account's last session
cc work -- -r           pick which session to resume
```

The separator is not optional. Without it PowerShell tries to bind those flags to the script: `-c` collides with `-Dir`, and `-r` binds to `-Rename` and waits for a value.

## Roles

A session can be opened with a role, `cc work -o orchestrator`, `-o lane` (the default) or `-o research`. The role does three things and nothing else.

It sets the context cap. `orchestrator` and `lane` run with `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`, so the window stays at 200k and compacts on the schedule the kit's hooks expect; `research` is the only uncapped role, for a session that reads far more than it writes. The variable is set with `$env:` in the window the script opens, never globally.

It labels the process. `CLAUDE_ROLE` travels to the session so hooks can tell which pane they are in; the kit's read guard, for example, denies image reads only to the orchestrator. The Herdr tab label carries the role too (`cc-work-orchestrator`), and `lane` is left off the label because it is the default.

It names the session. When `-o` is given explicitly, the script passes `claude --name <role>` so the prompt box, the resume picker and the transcript all say `orchestrator` or `research` whatever pane number the multiplexer hands out. It is applied only when the role was typed on purpose: a plain `cc work -- -c` must not rename the orchestrator it resumes into `lane`. Pass `-- --name x` to choose another name.

The cap has one opt-in exception. `cc work -CompactWindow 260000` drops `CLAUDE_CODE_DISABLE_1M_CONTEXT` for that session and sets `CLAUDE_CODE_AUTO_COMPACT_WINDOW` to the number you give, so auto-compaction fires higher and the session compacts less often. Both variables are read by claude.exe, verified against 2.1.258, and the variable takes precedence over the `autoCompactWindow` setting. It is off unless you pass it, and it is a tradeoff, not a free win: a bigger window means a bigger floor on every turn and more expensive cache breaks (`docs/CONTEXT-ECONOMICS.md`, sections 2 and 4). `cc work -ShowEnv` prints the variables a launch would set and exits without opening a session, which is the way to check the wiring, and the way the tests do it.

Two side notes. Every role except `research` adds `--no-chrome`, so a session that needs the browser either takes `research` or passes a `chrome` flag itself. And `-Workspace <n>` (`-x`), with `-Tab`, picks the Herdr workspace for the new tab by number or id; it is ignored without Herdr.

## Keeping settings in sync

`CLAUDE.md` and `settings.json` come from the default directory, which acts as the master. Two rules keep that from destroying anything.

**JSON is merged, not replaced.** Replacing `settings.json` drops keys Claude Code writes on its own, such as `skipDangerousModePermissionPrompt`, and then it asks you to confirm bypass mode on every start. `claude/merge-settings.py` merges deeply, keeps keys the profile owns, and refuses to write when the destination does not parse. Without Python the script falls back to a copy and says so.

**A profile that changed is never overwritten silently.** The script stores the hash of both sides of the last copy in `.sync-source.json`. If the profile file no longer matches, it is left alone and you are told. `-Sync` forces it and saves a `.bak` first.

Dates do not work for this. A modification date only says which file is newer, not whether the profile drifted from what it was given, and Claude Code rewrites `settings.json` on its own whenever you touch `/config`.

**The model stays with the profile.** The merge takes everything from the default except `model`, which the profile keeps when it already has one. The model is the one setting that differs between accounts on purpose (an agents-only account runs a cheaper model than the orchestrator's account), and a sync that promoted it to the default's model went unnoticed until the agents came up on the expensive one.

**Hooks live per config directory, so a sync is part of installing them.** A hook added to `~\.claude\settings.json` exists only for the default account. Every session opened through a profile keeps running without it until that profile is synced, and nothing warns you: the first sign was a session that compacted with no checkpoint. After changing hooks in the default, run `cc <profile> -p -s` for every profile (`-p` prepares without opening a session). The shortcut of the default account itself is not a profile: `cc <default> -p -s` reports that there is nothing to prepare, because that directory is the source. Running sessions pick the new hooks up without a restart; the hook that logs tool results started writing rows for three open sessions within the same minute as the sync.

## The traps

These are the ones that caused real damage, not hypotheticals.

**Deleting a profile can delete your shared data.** `Remove-Item -Recurse` on Windows can descend into a junction and delete what is on the other side, which here is your skills and your agent memory. Always remove the junctions first with `cmd /c rmdir <link>`, which removes the link only, and then delete the folder. That is what `cc <account> -d` does, and it checks afterwards that the default directory's `skills` still exists.

**PowerShell 5.1 cannot parse `.claude.json`.** The file accumulates keys that differ only in case, such as `c:/code` and `C:/code`, which JSON allows and PowerShell's dictionary does not:

```
ConvertFrom-Json : Cannot convert the JSON string because a dictionary that was
converted from the string contains the duplicated keys 'c:/code' and 'C:/code'.
```

The dangerous pattern is `try { $c = ... | ConvertFrom-Json } catch { $c = @{} }` followed by writing the file back. When parsing fails you write a fresh JSON with no `oauthAccount`, and that signs the account out. **If the file exists and does not parse, do not touch it.** To read one value out of it, use a regex on the text instead.

`ConvertFrom-Json -AsHashtable` does not exist in PowerShell 5.1 at all, so a `try` that uses it always lands in the `catch` and the destructive fallback runs on every start rather than in some rare case.

**Do not write JSON with `Set-Content -Encoding utf8`.** PowerShell 5.1 adds a BOM and Node's `JSON.parse` rejects a leading BOM. Use `[System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))`, or do the writing in Python.

**Watch out for `$Profile`.** It is an automatic PowerShell variable holding the path to the shell profile. Using it as a local name for something else breaks things in confusing ways.

## Changing which account is the default

`~/.claude` cannot move, so changing which account lives there means a logout, and that leaves **every** session running without `CLAUDE_CONFIG_DIR` unauthenticated. The order that works:

1. Move those sessions to a profile first. Transcripts are shared, so opening the target account and resuming is enough: `cc other -- -r`.
2. Sign out of claude.ai in the browser, or use a private window.
3. `cc default`, then `/logout` and `/login` with the new account. Verify with `cc` **before** going further.
4. Only then delete whichever profile became redundant, and adjust the name, shortcut and icon with `-r`, `-a` and `-i`.

Step 4 goes last on purpose: until the sign-in in step 3 is confirmed, that profile may be the only place the account is signed in.

## Wiring up the `cc` command

The script takes the current folder by default, so the natural flow is `cd` then `cc <account>`. To call it from anywhere, add this to your PowerShell profile (`$PROFILE`):

```powershell
$script:CcScript = "$env:USERPROFILE\.claude\claude-account.ps1"

function cc {
    param([string]$Target)
    $all = @()
    if ($Target) { $all += $Target }
    if ($args)   { $all += $args }
    & $script:CcScript @all
}
```

Three details there are load-bearing.

The function must be **simple, not advanced**. A single `[Parameter()]` attribute turns it into an advanced function, and then PowerShell rejects any flag it does not declare: `cc work -s` would die with `A parameter cannot be found that matches parameter name 's'`. Left simple, undeclared flags land in `$args` and are forwarded.

The parameter is named `$Target`, not `$Account`. With `$Account`, typing `cc -a w` prefix-matches `-Account` and the flag never reaches the script. Make sure the wrapper's parameter name shares no prefix with any flag you want to pass through.

Arguments are collected into **one flat array** and splatted once. Passing the positional separately and `@args` after it breaks the binding: `cc work -a w` fails with `A positional parameter cannot be found that accepts argument 'work'`.

### Tab completion

Optional, and it removes the need to remember shortcuts at all:

```powershell
Register-ArgumentCompleter -CommandName cc -ParameterName Target -ScriptBlock {
    param($cmd, $param, $word)
    $base = "$env:USERPROFILE\.claude-accounts"
    $names = @('default')
    if (Test-Path $base) { $names += (Get-ChildItem $base -Directory).Name }
    $cfg = Join-Path $base '.names.json'
    if (Test-Path $cfg) {
        try {
            $n = Get-Content $cfg -Raw | ConvertFrom-Json
            if ($n.shortcuts) { $names = @($n.shortcuts.PSObject.Properties.Name) + $names }
        } catch {}
    }
    $names | Where-Object { $_ -like "$word*" } | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }
}
```

A profile is only read when a shell starts, so an already open window will not have `cc` until you run `. $PROFILE` in it.

## Undoing all of this

Nothing here modifies Claude Code itself. To back it out, delete `~/.claude-accounts` (junctions first, see the trap above), remove the `cc` function from your profile, and carry on with `~/.claude` as you did before. Any account signed in inside a profile stays signed in on claude.ai; you are only discarding the local token.
