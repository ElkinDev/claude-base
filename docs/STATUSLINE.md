# Status line

A custom Claude Code status line for Windows / PowerShell. It renders one line:

```
🐺 Opus 5  max  ⭐ alex  📁 my-app  main*  +142 -37  ctx [====......] 41%  7d 68% left
```

| Segment | Meaning |
|---|---|
| 🐺 `Opus 5` | Model, one animal per family: 🐺 Opus, 🦁 Fable, 🐰 Sonnet, 🐭 Haiku. Anything unrecognised falls back to the wolf |
| `max` | Effort level: low, medium, high, xhigh, max (cyan) |
| ⭐ `alex` | The account this session runs as. Green when it points at a local model |
| 📁 `my-app` | Current folder (blue) |
| `main*` `+142 -37` | Branch (magenta), a `*` when the tree is dirty (yellow), and lines added / removed against HEAD (green / red) |
| `ctx [====......] 41%` | Context window used. Green under 70%, yellow to 89%, red above |
| `7d 68% left` | Weekly quota left, not spent. The word is spelled out because `7d 100%` alone reads as an exhausted quota when it means an untouched one. Green above 40%, yellow to 15%, red below. Adds the reset time once it drops under 40% |

## How it is wired

`~/.claude/settings.json` points the status line at the script. Claude Code needs a literal path here, it does not expand `~` or `%USERPROFILE%`:

```json
"statusLine": {
  "type": "command",
  "command": "powershell -NoProfile -ExecutionPolicy Bypass -File C:/Users/<YOUR-USER>/.claude/statusline.ps1",
  "padding": 0
}
```

The installer fills that path in for you. Claude Code sends a JSON payload on stdin, the script reads it, runs `git` in the current directory for the branch and diff stats, and writes the line to stdout as raw UTF-8 so the glyphs render even when stdout is redirected.

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

Four constants near the top of the script (`$AccountsDir`, `$AccountsFile`, `$KeyDefault`, `$KeyIcons`) are the only thing to change if your switcher writes a different layout.

## The quota segment

It comes from `rate_limits.seven_day` in the payload, and shows what is **left** rather than what you have burned, because that is the number you act on.

That field only appears on claude.ai subscriptions, and only after the first API response of a session. When it is missing the segment is omitted entirely rather than showing a misleading zero.

## Why a regex instead of ConvertFrom-Json

For the e-mail lookup the script deliberately uses a regex on the raw text. `~/.claude.json` reaches 60 KB and accumulates keys that differ only in case, such as `c:/code` and `C:/code`, which JSON allows and PowerShell 5.1 refuses to parse. It is also cheaper: about 1.5 ms, against roughly 27 ms for a single one of the git calls the line already makes.

## Privacy

Nothing personal is baked into the script. Every path is built from `$env:USERPROFILE` at run time, so the file carries no user name, no account and no e-mail. At run time it reads `~/.claude.json` and the optional companion file above, both on your own machine, makes no network calls and writes nothing.

If you publish a screenshot, note that the account segment shows the local part of your e-mail.

## Requirements

- Windows PowerShell 5.1, which ships with Windows.
- `git` on PATH. Outside a repo, or without git, that segment is simply skipped.
- A terminal font with emoji support.

## Customize

- Model icons: the code points in `Get-ModelIcon`.
- Folder icon: `$IDir` near the top.
- Colours: the `C '<ansi>' <text>` helper. 91 red, 92 green, 93 yellow, 94 blue, 95 magenta, 96 cyan, 90 grey.
- Context bar width: `$w = 10`.
- Quota thresholds: the `15` and `40` in the `$colLeft` line.
