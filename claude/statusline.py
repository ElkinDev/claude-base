"""Claude Code status line, the Python twin of statusline.ps1 for macOS and Linux (it runs on Windows as well, where
the installer still wires the PowerShell one).
role - model - effort - account - folder - git (branch + lines +/-) - context - weekly quota - 5-hour quota

Drop this file in the kit home and wire it up in ~/.claude/settings.json. Claude Code needs a literal path there:
  "statusLine": { "type": "command", "command": "python3 \"/Users/<you>/.claude/statusline.py\"" }

The line is the same one statusline.ps1 renders, segment for segment and colour for colour, so a window reads the
same on every machine. Claude Code runs it on every refresh, so it starts the two git calls first and does the rest
while they run, and imports nothing heavier than json and subprocess. Python 3.8 or later, standard library only.

It never fails the line: a payload that is not JSON prints nothing, any other error prints the segments built so far,
and the exit code is always 0.

Privacy: nothing personal is baked into this file. Every path is resolved at run time from the home folder. It reads
two files on your own machine and prints to your own terminal: ~/.claude.json, only to pull the local part of the
account e-mail for the account label, and the optional companion file described below.
"""
import json
import math
import os
import re
import subprocess
import sys
import time

# ---- which account this session is running as ----
# The display name and icon can come from an OPTIONAL companion file written by a multi-account switcher. These
# four constants are the only thing to touch if your switcher writes a different layout; everything below is generic.
ACCOUNTS_DIR = ".claude-accounts"   # folder under the home folder holding the profiles
ACCOUNTS_FILE = ".names.json"       # companion file inside it
KEY_DEFAULT = "default"             # key holding the display name of ~/.claude
KEY_ICONS = "icons"                 # key holding "<profile name>": "<hex code point>"
USAGE_LOG_DIR = ""                  # set a folder to append quota changes to usage-log.csv there
#
# Expected shape:
#     { "<KEY_DEFAULT>": "<display name for ~/.claude>",
#       "<KEY_ICONS>":   { "<profile name>": "<hex code point>" } }
# Without that file the script still works: it shows the profile directory name, or for the default directory the
# local part of the account e-mail.

# ---- role of this session ----
# The account launcher passes the seat in CLAUDE_ROLE (orchestrator, analyst, lane, research), and the variable
# reaches this script because Claude Code starts it as a child process. A session opened without the launcher has no
# role and no badge. ROLE_BY_FOLDER relabels a lane or role-less session by the folder its transcript is kept in,
# which is named after the launch folder and does not move with a later cd. Use it when a project's orchestrator is
# launched with another role. The value is the label shown; its first word picks the colour.
ROLE_BY_FOLDER = {}   # e.g. {"-Users-me-work-my-app": "orchestrator my-app"}

ROLE_COLOURS = {
    "orchestrator": "38;5;183",   # pastel lavender
    "analyst": "38;5;153",        # pastel sky blue
    "research": "38;5;151",       # pastel mint
    "lane": "38;5;250",           # soft grey
}
ROLE_COLOUR_OTHER = "38;5;223"    # pastel peach

# Icons by code point, so the file encoding never breaks them.
ICON_DIR = "\U0001F4C1"           # folder
SEPS = os.sep + (os.altsep or "")
# Case-insensitive like PowerShell's -match, so both scripts read the same files the same way.
EMAIL_RE = re.compile(rb'"emailAddress"\s*:\s*"([^"@]+)', re.IGNORECASE)
NON_PRINTABLE = re.compile(r"[^ -~]")


def c(code, text):
    return "\x1b[" + code + "m" + text + "\x1b[0m"


def field(obj, *keys):
    """The value under a key path of the payload, or None when any step is missing or not an object."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def text(value):
    """PowerShell's [string] cast: null reads as an empty string."""
    return "" if value is None else str(value)


def lookup(table, key):
    """A key read the way PowerShell reads a hashtable or a JSON property: exact first, then ignoring case."""
    if not isinstance(table, dict):
        return None
    if key in table:
        return table[key]
    low = key.lower()
    for k, v in table.items():
        if isinstance(k, str) and k.lower() == low:
            return v
    return None


def floor_int(value):
    return int(math.floor(float(value)))


def glyph(code_point):
    """The character for a code point, or '' for one outside Unicode scalar values (what ConvertFromUtf32 refuses)."""
    if 0 <= code_point <= 0x10FFFF and not 0xD800 <= code_point <= 0xDFFF:
        return chr(code_point)
    return ""


def leaf(path):
    return os.path.basename(path.rstrip(SEPS)) if path else ""


def model_icon(name):
    # One animal per model family, to tell them apart at a glance. Matched against display_name, which arrives as
    # "Opus 5", "Sonnet 5", "Haiku 4.5", "Fable 5". Anything unrecognised falls back to the wolf.
    low = name.lower()
    if "fable" in low:
        return "\U0001F981"   # lion
    if "sonnet" in low:
        return "\U0001F430"   # hare
    if "haiku" in low:
        return "\U0001F42D"   # mouse
    return "\U0001F43A"       # wolf, Opus included


def account_info():
    """Derived from CLAUDE_CONFIG_DIR, which the process inherits from Claude Code. Without that variable we are in
    ~/.claude, the default config directory."""
    home = os.path.expanduser("~")
    cd = os.environ.get("CLAUDE_CONFIG_DIR") or ""
    if not cd.strip():
        target = KEY_DEFAULT
    else:
        trimmed = cd.rstrip(SEPS)
        if os.path.normcase(trimmed) == os.path.normcase(os.path.join(home, ".claude-local")):
            target = "local"
        else:
            target = os.path.basename(trimmed)
    is_default = target.lower() == KEY_DEFAULT.lower()

    cfg = None
    try:
        with open(os.path.join(home, ACCOUNTS_DIR, ACCOUNTS_FILE), "rb") as f:
            cfg = json.loads(f.read().decode("utf-8-sig"))
    except Exception:
        pass
    if not isinstance(cfg, dict):
        cfg = None

    # Full name, never a one letter shortcut. This line is read out of the corner of your eye, and a single letter
    # says nothing there.
    name = ""
    if is_default:
        configured = lookup(cfg, KEY_DEFAULT)
        if configured:
            name = str(configured)
        else:
            # With no name of its own, the default directory does not say which account it is. Pull the e-mail out of
            # .claude.json with a regex, not a JSON parse: it is the largest file the line touches, it keeps growing
            # with every project, and the one field we want does not need the whole tree built on every refresh.
            # (statusline.ps1 has a second reason, PowerShell 5.1 refusing keys that differ only in case.)
            try:
                with open(os.path.join(home, ".claude.json"), "rb") as f:
                    m = EMAIL_RE.search(f.read())
                if m:
                    name = m.group(1).decode("utf-8", "replace")
            except Exception:
                pass
    if not name:
        name = target

    # Icon stored as a hex code point, so it does not depend on the JSON file encoding. With no companion file the
    # default account gets the star, so a fresh install looks deliberate instead of half configured. Any other
    # profile falls back to a bust.
    code = lookup(lookup(cfg, KEY_ICONS), target)
    code = str(code) if code else ("2B50" if is_default else "1F464")
    icon = ""
    try:
        icon = glyph(int(code, 16))
    except Exception:
        pass
    return name, icon, target.lower() == "local"


def role_label(payload):
    role = (os.environ.get("CLAUDE_ROLE") or "").strip().lower()
    role = NON_PRINTABLE.sub("", role)   # printable ASCII only, so no escape code reaches the line
    if role in ("", "lane") and ROLE_BY_FOLDER:
        tp = text(field(payload, "transcript_path"))
        if tp:
            mapped = lookup(ROLE_BY_FOLDER, os.path.basename(os.path.dirname(tp)))
            if mapped is not None:
                role = str(mapped)
    return role[:32]


def start_git(cwd):
    """Starts the two git calls side by side and returns them, or None. One status call gives the branch and the
    dirty marker together; the line counts need their own diff. GIT_OPTIONAL_LOCKS=0 keeps both from taking the
    index lock, so a refresh never collides with a git command the user is running."""
    if not cwd:
        return None
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    opts = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    status = subprocess.Popen(["git", "-C", cwd, "status", "--porcelain=v2", "--branch"], **opts)
    try:
        diff = subprocess.Popen(["git", "-C", cwd, "diff", "HEAD", "--numstat"], **opts)
    except Exception:
        diff = None
    return status, diff


def git_segment(procs):
    status, diff = procs
    out, _ = status.communicate()
    numstat = diff.communicate()[0] if diff else b""
    if status.returncode != 0:
        return None   # not a repository, or git refused: the segment is simply skipped
    branch, unborn, dirty = "", False, False
    for line in out.splitlines():
        if line.startswith(b"# branch.head "):
            branch = line[14:].decode("utf-8", "replace")
        elif line == b"# branch.oid (initial)":
            unborn = True
        elif line and not line.startswith(b"#"):
            dirty = True
    # "HEAD" for a detached head and for a branch with no commit yet, which is what statusline.ps1 shows for both
    # (it reads `git rev-parse --abbrev-ref HEAD`), so the two lines stay the same on every machine.
    if unborn or branch in ("", "(detached)"):
        branch = "HEAD"
    add = removed = 0
    for line in numstat.splitlines():
        cols = line.split(b"\t")
        if len(cols) >= 2:
            # A binary file reports "-", which counts as 0, as [int]::TryParse does in the .ps1.
            try:
                add += int(cols[0])
            except ValueError:
                pass
            try:
                removed += int(cols[1])
            except ValueError:
                pass
    seg = c("95", branch)                         # branch (magenta)
    if dirty:
        seg += c("93", "*")                       # dirty (yellow)
    return seg + "  " + c("92", "+%d" % add) + " " + c("91", "-%d" % removed)   # + green, - red


def left_colour(left):
    return "91" if left <= 15 else ("93" if left <= 40 else "92")


def percent_left(used):
    return max(0, min(100, 100 - floor_int(used)))


def week_segment(payload, used):
    # How much is LEFT, not how much you have burned. The word "left" is not decoration: without it "7d 100%" reads
    # as an exhausted quota when it means an untouched one, and colour alone is too weak a hint for that.
    left = percent_left(used)
    line = "7d %d%% left" % left
    # The reset time only matters once you are running low. Before that it is noise.
    reset = field(payload, "rate_limits", "seven_day", "resets_at")
    if left <= 40 and reset is not None:
        try:
            hours = int(math.ceil((int(round(float(reset))) - time.time()) / 3600.0))
            if hours > 0:
                line += (" %dd" % (hours // 24)) if hours >= 48 else (" %dh" % hours)
        except Exception:
            pass
    return c(left_colour(left), line)


def five_hour_segment(used):
    left = percent_left(used)
    return c(left_colour(left), "5h %d%% left" % left)


def used_or_zero(value):
    try:
        return floor_int(value) if value is not None else 0
    except Exception:
        return 0


def log_usage(account, used5h, used7d, pct):
    """One CSV line per change of the quota pair, so a nightly ledger can read the quota without spending a request.
    Off unless USAGE_LOG_DIR is set above."""
    if not USAGE_LOG_DIR or (used7d is None and used5h is None):
        return
    if not os.path.isdir(USAGE_LOG_DIR):
        os.makedirs(USAGE_LOG_DIR)
    log_file = os.path.join(USAGE_LOG_DIR, "usage-log.csv")
    pair = "%s,%d,%d" % (account or "default", used_or_zero(used5h), used_or_zero(used7d))
    last = ""
    exists = os.path.exists(log_file)
    if exists:
        with open(log_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4096))
            lines = f.read().decode("utf-8", "replace").splitlines()
        last = lines[-1] if lines else ""
    last_pair = ",".join(last.split(",")[1:4]) if last else ""
    if last_pair != pair:
        # Binary mode, so the file gets "\n" on Windows too, as the .ps1 writes it.
        with open(log_file, "ab") as f:
            if not exists:
                f.write(b"time,account,five_hour_used,seven_day_used,context_pct\n")
            f.write(("%s,%s,%d\n" % (time.strftime("%Y-%m-%d %H:%M"), pair, pct)).encode("utf-8"))


def build(payload, segs):
    """Appends the segments in order. Each one is guarded on its own, so a failure costs that segment, not the line."""
    cwd = text(field(payload, "workspace", "current_dir")) or text(field(payload, "cwd"))
    procs = None
    try:
        procs = start_git(cwd)   # first, so git runs while the rest of the line is built
    except Exception:
        pass

    try:
        label = role_label(payload)
        if label:   # role, first so it is seen first; plain text, no background, as the other segments
            segs.append(c(ROLE_COLOURS.get(label.split(" ")[0].lower(), ROLE_COLOUR_OTHER), label))
    except Exception:
        pass

    model = text(field(payload, "model", "display_name"))
    segs.append(c("95;1", model_icon(model) + " " + model))   # model (magenta, bold)
    effort = text(field(payload, "effort", "level"))
    if effort:
        segs.append(c("96", effort))                           # effort (cyan): low/medium/high/xhigh/max

    # Active account. Green for the local model, which burns no account quota at all.
    account = ""
    try:
        account, icon, is_local = account_info()
        if account:
            segs.append(c("92" if is_local else "33", icon + " " + account))
    except Exception:
        pass

    folder = leaf(cwd)
    if folder:
        segs.append(c("94", ICON_DIR + " " + folder))         # folder (blue)
    if procs:
        try:
            seg = git_segment(procs)
            if seg:
                segs.append(seg)
        except Exception:
            pass

    # ---- context bar (green <70, yellow 70-89, red 90+) ----
    pct = 0
    try:
        raw = field(payload, "context_window", "used_percentage")
        pct = max(0, min(100, floor_int(raw))) if raw is not None else 0
    except Exception:
        pct = 0
    width = 10
    filled = max(0, min(width, pct * width // 100))
    bar_col = "91" if pct >= 90 else ("93" if pct >= 70 else "92")
    # The window size is the one Claude Code reports for this session (200k or 1M), so the number after the slash says
    # which window the session really runs with. round() is banker's rounding, the same as PowerShell's [int] cast.
    size = ""
    try:
        cw = field(payload, "context_window", "context_window_size")
        if cw:
            size = "/%dk" % int(round(float(cw) / 1000))
    except Exception:
        pass
    segs.append("ctx [" + c(bar_col, "=" * filled) + c("90", "." * (width - filled)) + "] %d%%%s" % (pct, size))

    # ---- quota windows ----
    # rate_limits only shows up for claude.ai subscriptions, and only after the first API response of the session, so
    # either window may be missing. With no data, show nothing rather than a misleading zero.
    used7d = field(payload, "rate_limits", "seven_day", "used_percentage")
    used5h = field(payload, "rate_limits", "five_hour", "used_percentage")
    if used7d is not None:
        try:
            segs.append(week_segment(payload, used7d))
        except Exception:
            pass
    if used5h is not None:
        try:
            segs.append(five_hour_segment(used5h))
        except Exception:
            pass

    try:
        log_usage(account, used5h, used7d, pct)
    except Exception:
        pass


def main():
    segs = []
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig", "replace")
        payload = json.loads(raw)
    except Exception:
        payload = None   # not JSON: print nothing, as statusline.ps1 does
    if payload is not None:
        try:
            build(payload if isinstance(payload, dict) else {}, segs)
        except Exception:
            pass
    # Raw UTF-8 bytes, so the glyphs survive whatever encoding stdout would pick when it is redirected.
    try:
        sys.stdout.buffer.write("  ".join(segs).encode("utf-8", "replace"))
        sys.stdout.buffer.flush()
    except Exception:
        pass
    # Straight out: a write that failed on a closed pipe would be retried by the interpreter's own shutdown flush and
    # turn the exit code into 120. Skipping the teardown also saves a few milliseconds on every refresh.
    os._exit(0)


if __name__ == "__main__":
    main()
