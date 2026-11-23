# Status line

A custom Claude Code status line in two twins that render the same line: `claude/statusline.ps1` for Windows (PowerShell 5.1) and `claude/statusline.py` for macOS and Linux (Python 3.8 or later, standard library only). It renders one line:

```
analyst  🐺 Opus 5  max  ⭐ alex  📁 my-app  main*  +142 -37  ctx [====......] 41%/200k  7d 68% left  5h 90% left
```

| Segment | Meaning |
|---|---|
| `analyst` | The session's role, from `CLAUDE_ROLE`, as plain text in a soft colour: orchestrator lavender, analyst sky blue, research mint, lane grey, anything else peach (256-colour codes). Nothing when the session has no role |
| 🐺 `Opus 5` | Model, one animal per family: 🐺 Opus, 🦁 Fable, 🐰 Sonnet, 🐭 Haiku. Anything unrecognised falls back to the wolf |
| `max` | Effort level: low, medium, high, xhigh, max (cyan) |
| ⭐ `alex` | The account this session runs as. Green when it points at a local model |
| 📁 `my-app` | Current folder (blue) |
| `main*` `+142 -37` | Branch (magenta), a `*` when the tree is dirty (yellow), and lines added / removed against HEAD (green / red) |
| `ctx [====......] 41%/200k` | Context window used. Green under 70%, yellow to 89%, red above. The number after the slash is the window size Claude Code reports for the session (200k or 1M) |
| `7d 68% left` | Weekly quota left, not spent. The word is spelled out because `7d 100%` alone reads as an exhausted quota when it means an untouched one. Green above 40%, yellow to 15%, red below. Adds the reset time once it drops to 40% or below |
| `5h 90% left` | The 5-hour session window, same convention and colours, without the reset time |

## How it is wired

`~/.claude/settings.json` points the status line at the script. Claude Code needs a literal path here, it does not expand `~` or `%USERPROFILE%`. On Windows:

```json
"statusLine": {
  "type": "command",
  "command": "powershell -NoProfile -ExecutionPolicy Bypass -File C:/Users/<YOUR-USER>/.claude/statusline.ps1",
  "padding": 0
}
```

On macOS and Linux, the Python twin in the kit home:

```json
"statusLine": {
  "type": "command",
  "command": "python3 \"<kit home>/statusline.py\"",
  "padding": 0
}
```

The installer fills that path in for you on each system. Claude Code sends a JSON payload on stdin, the script reads it, runs `git` in the current directory for the branch and diff stats, and writes the line to stdout as raw UTF-8 so the glyphs render even when stdout is redirected.

## The account segment

With no extra configuration it shows the local part of the account e-mail, read from `~/.claude.json`, with a star.

If you run several accounts by pointing `CLAUDE_CONFIG_DIR` at different directories, the segment follows that variable and shows the directory name instead, so a window always tells you which account it is on. See `ACCOUNTS.md`.

The label and icon can be overridden through the optional companion file the account switcher writes, at `~/.claude-accounts/.names.json`:

```json
{
  "default": "work",
  "icons": {
    "default": "2B50",
    "personal": "1F3E0"
  }
}
```

`default` is the display name for `~/.claude`. Under `icons`, each key is either `default` or the name of a config directory under `~/.claude-accounts`, and each value is a Unicode code point in hex. Code points rather than the emoji itself, because PowerShell 5.1 and JSON files disagree about surrogate pairs.

Four constants near the top of the script (`$AccountsDir`, `$AccountsFile`, `$KeyDefault`, `$KeyIcons` in the .ps1, `ACCOUNTS_DIR`, `ACCOUNTS_FILE`, `KEY_DEFAULT`, `KEY_ICONS` in the .py) are the only thing to change if your switcher writes a different layout.

## The role segment

When many windows are open, the role says which chair each one is. The account launcher passes the role in `CLAUDE_ROLE` (`-Role orchestrator`, `analyst`, `lane` or `research`, see `ACCOUNTS.md`), and Claude Code starts the status line as a child process, so the script reads it from its environment. A session opened without the launcher has no role and shows none.

If a project's orchestrator is launched with another role, `$RoleByFolder` near the top of the script relabels it by the folder its transcript is kept in, which Claude Code names after the launch folder and which a later `cd` does not move. It applies only to a session whose role is `lane` or empty; a session launched with any other role keeps it even in a mapped folder. Its value is the label shown:

```powershell
$RoleByFolder = @{ 'C--work-my-app' = 'orchestrator my-app' }
```

In the Python twin the same map is `ROLE_BY_FOLDER`, and on macOS the folder name is the launch path with each `/` turned into `-`:

```python
ROLE_BY_FOLDER = {"-Users-me-work-my-app": "orchestrator my-app"}
```

The pins are in `scripts/tests/test-statusline.py`. The .ps1 cases run on Windows only; the .py cases run anywhere, and also pin the context bar, the quota text, the git segment and the account label.

## The quota segment

It comes from `rate_limits.seven_day` (and `rate_limits.five_hour` for the 5h segment) in the payload, and shows what is **left** rather than what you have burned, because that is the number you act on.

That field only appears on claude.ai subscriptions, and only after the first API response of a session. When it is missing the segment is omitted entirely rather than showing a misleading zero.

## Why a regex instead of a JSON parse

For the e-mail lookup both scripts deliberately use a regex on the raw text. `~/.claude.json` reaches 60 KB and accumulates keys that differ only in case, such as `c:/code` and `C:/code`, which JSON allows and PowerShell 5.1 refuses to parse. It is also cheaper: about 1.5 ms, against roughly 27 ms for a single one of the git calls the line already makes. Python's `json` would parse the file, but it is the largest one the line touches and keeps growing, and one field does not need the whole tree built on every refresh.

## Speed

Claude Code runs the script on every refresh. The Python twin starts its two git calls side by side before it builds anything else: `git status --porcelain=v2 --branch` gives the branch and the dirty marker in one call, and `git diff HEAD --numstat` gives the line counts. Both run with `GIT_OPTIONAL_LOCKS=0`, so a refresh never takes the index lock from a git command you are running. Measured on a Mac inside a 500-file repository, a refresh takes about 35 ms with `/usr/bin/python3`, about 15 ms of which is the interpreter starting; running the git calls one after the other measured 43 ms.

## Privacy

Nothing personal is baked into the script. Every path is built from the home folder (`$env:USERPROFILE`, or `~` in the Python twin) at run time, so the file carries no user name, no account and no e-mail. At run time it reads `~/.claude.json` and the optional companion file above, both on your own machine, makes no network calls and writes nothing unless you switch on the usage log (`$UsageLogDir` / `USAGE_LOG_DIR`, empty by default).

If you publish a screenshot, note that the account segment shows the local part of your e-mail.

## Requirements

- Windows: PowerShell 5.1, which ships with Windows.
- macOS and Linux: `python3`, version 3.8 or later. Standard library only.
- `git` on PATH. Outside a repo, or without git, that segment is simply skipped.
- A terminal font with emoji support.

## Customize

Names in the .ps1 first, then the .py.

- Model icons: the code points in `Get-ModelIcon` / `model_icon`.
- Folder icon: `$IDir` / `ICON_DIR` near the top.
- Colours: the `C '<ansi>' <text>` / `c("<ansi>", text)` helper. 91 red, 92 green, 93 yellow, 94 blue, 95 magenta, 96 cyan, 90 grey.
- Context bar width: `$w = 10` / `width = 10`.
- Quota thresholds: the `15` and `40` in the `$colLeft` line / in `left_colour`.
