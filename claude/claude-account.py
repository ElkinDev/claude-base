#!/usr/bin/env python3
"""cca: switch between several Claude Code accounts without logging out, on macOS and Linux.

The POSIX twin of claude-account.ps1, with the same commands, flags and files. docs/ACCOUNTS.md
explains every design decision; this header only says how the mechanism lands on a POSIX machine.

How it works:
  - Each account lives in its own folder under ~/.claude-accounts/<name>. Pointing
    CLAUDE_CONFIG_DIR at one folder or another swaps the whole account. The variable is set only
    for the claude process this script starts (or for the tab it opens), never in a shell rc file:
    that would switch every new session at once.
  - Where the sign-in lives differs by system. On Linux the OAuth token is <config dir>/.credentials.json,
    as on Windows, so it moves with the folder. On macOS Claude Code keeps it in the login Keychain,
    in an item whose name carries a hash of the CLAUDE_CONFIG_DIR string ("Claude Code-credentials"
    for ~/.claude, "Claude Code-credentials-<8 hex>" for any other folder). Each profile therefore
    signs in once and keeps its own token. This script never reads, copies or deletes a Keychain
    item; it only names the item in the notes it prints.
  - Shared assets (skills, plugins, hooks, tools, agents, commands, and the projects folder that holds
    session transcripts and agent memory) are symbolic links back to ~/.claude, so there are not N
    copies of them. Deleting a profile unlinks them and never descends through one.
  - CLAUDE.md and settings.json are brought over from ~/.claude, never overwriting a change made
    inside a profile. -s (-Sync) forces it.

'default' is a reserved name meaning ~/.claude itself, the directory plain `claude` uses when
CLAUDE_CONFIG_DIR is not set. It cannot be moved, only relabelled.

The command is `cca`, not `cc` as on Windows: on macOS and Linux `cc` is the C compiler, and a kit
folder early on PATH would shadow it for every build.

Quick start:  cca -h           full help
              cca              list the accounts
              cca work         open that account here

Python 3.8 or later, standard library only.
"""
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime

ROLES = ("orchestrator", "analyst", "lane", "research")
# Only these two have a chair: a seat file appended to the system prompt, a generated name and a
# start line. lane and research are handed nothing.
SEATED = ("orchestrator", "analyst")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
START_LINE = ("Session start: read the newest resume brief of your seat, then the state sheet, "
              "then continue with its first actions.")
# The auto-compact window every role but research runs with. Setting it to 0 brings back the 200k
# cap for those roles (docs/ACCOUNTS.md, Roles); -Window replaces it for one launch.
DEFAULT_WINDOW = 300000
MAX_WINDOW = 2000000
# Folders shared with the default directory. Missing ones are skipped.
LINKED = ("skills", "plugins", "hooks", "tools", "agents", "commands", "projects")
# Files brought over from the default directory.
COPIED = ("CLAUDE.md", "settings.json")
NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
NEGATIVE_RE = re.compile(r"^-\d+$")
HERE = os.path.dirname(os.path.abspath(__file__))

# Each option: key, whether it takes a value, PowerShell names, PowerShell aliases, GNU long names.
# The PowerShell spellings match claude-account.ps1, so a line copied from the docs works here too.
OPTIONS = (
    ("account", True, ("Account",), (), ()),
    ("folder", True, ("Folder",), ("Dir",), ("folder", "dir")),
    ("list", False, ("List",), ("l",), ("list",)),
    ("delete", False, ("Delete",), ("d",), ("delete",)),
    ("rename", True, ("Rename",), ("r",), ("rename",)),
    ("sync", False, ("Sync",), ("s",), ("sync",)),
    ("prepare", False, ("Prepare",), ("p",), ("prepare",)),
    ("tab", False, ("Tab",), ("w",), ("tab",)),
    ("noshare", False, ("NoShare",), ("n",), ("no-share", "noshare")),
    ("alias", True, ("Alias",), ("a",), ("alias",)),
    ("icon", True, ("Icon",), ("i",), ("icon",)),
    ("help", False, ("Help",), ("h",), ("help",)),
    ("role", True, ("Role",), ("o",), ("role",)),
    ("window", True, ("Window",), (), ("window",)),
    ("showenv", False, ("ShowEnv",), (), ("show-env", "showenv")),
    ("workspace", True, ("Workspace",), ("x",), ("workspace",)),
)


def _tables():
    exact, prefix, long_names = {}, [], {}
    for key, takes, names, aliases, longs in OPTIONS:
        for word in names + aliases:
            exact[word.lower()] = (key, takes)
        for word in names + tuple(a for a in aliases if len(a) > 1):
            prefix.append((word.lower(), key, takes))
        for word in longs:
            long_names[word] = (key, takes)
    return exact, prefix, long_names


PS_EXACT, PS_PREFIX, GNU_LONG = _tables()

COLORS = {"red": "31", "green": "32", "yellow": "33", "cyan": "36", "gray": "90"}


# --- output -----------------------------------------------------------------------

def _paint(text, color, stream):
    if not color or os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return text
    try:
        if not stream.isatty():
            return text
    except (AttributeError, ValueError):
        return text
    return "\033[" + COLORS[color] + "m" + text + "\033[0m"


def say(text="", color=None, err=False):
    stream = sys.stderr if err else sys.stdout
    print(_paint(text, color, stream), file=stream)


def ok(text):
    say("  " + text, "gray")


def fail(text):
    sys.stdout.flush()
    say("  FAILED: " + text, "red", err=True)
    raise SystemExit(1)


# --- arguments --------------------------------------------------------------------

class UsageError(Exception):
    pass


def _lookup_ps(word):
    """A single-dash word: an exact name or alias, any case, else an unambiguous prefix of a name of
    two letters or more, which is how PowerShell binds -Sh to -ShowEnv. A single letter that is not
    an alias (-c, -v) never matches, so claude's own one-letter flags reach claude."""
    lowered = word.lower()
    if lowered in PS_EXACT:
        return PS_EXACT[lowered]
    if len(lowered) < 2:
        return None
    hits = sorted({(key, takes) for name, key, takes in PS_PREFIX if name.startswith(lowered)})
    if len(hits) > 1:
        raise UsageError("-{} is ambiguous ({}).".format(word, ", ".join("-" + k for k, _ in hits)))
    return hits[0] if hits else None


def parse_args(argv):
    """Returns (options, extra, given). Everything after `--` lands in extra untouched. Before it, a
    flag this script does not know is forwarded to claude too, as the PowerShell launcher does with
    an unbound flag, and so is a second positional word."""
    opts = {"account": None, "folder": None, "list": False, "delete": False, "rename": None,
            "sync": False, "prepare": False, "tab": False, "noshare": False, "alias": None,
            "icon": None, "help": False, "role": "lane", "window": "0", "showenv": False,
            "workspace": None}
    given = set()
    extra = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        i += 1
        if tok == "--":
            extra.extend(argv[i:])
            break
        spec, inline = None, None
        if tok.startswith("--") and len(tok) > 2:
            name, eq, value = tok[2:].partition("=")
            spec = GNU_LONG.get(name)
            if eq:
                inline = value
        elif tok.startswith("-") and len(tok) > 1 and not NEGATIVE_RE.match(tok):
            name, colon, value = tok[1:].partition(":")
            spec = _lookup_ps(name)
            if colon:
                inline = value
        if spec is None:
            if tok.startswith("-") and len(tok) > 1:
                extra.append(tok)
            elif opts["account"] is None:
                opts["account"] = tok
            else:
                extra.append(tok)
            continue
        key, takes = spec
        if takes:
            if inline is not None:
                value = inline
            elif i < len(argv) and (not argv[i].startswith("-") or argv[i] == "-"
                                    or (key == "window" and NEGATIVE_RE.match(argv[i]))):
                value = argv[i]
                i += 1
            else:
                raise UsageError("missing a value for {}.".format(tok))
            opts[key] = value
        else:
            opts[key] = inline is None or inline.lower() not in ("$false", "false", "0")
        given.add(key)
    return opts, extra, given


# --- the launch plan: seat, name, model, effort, window ---------------------------

def build_plan(opts, given, extra, home, environ):
    """Everything a launch hands claude, worked out before any account is touched, so -ShowEnv can
    print it and the in-terminal, Herdr and new-window paths all read the same answer."""
    role = opts["role"]
    plan = {"role": role, "seat": "none", "start": "none"}
    is_seat = role in SEATED
    # The seats are installed into the kit home; CLAUDE_SEATS_DIR moves them for a test or a second
    # install. The briefs are the seats' own output and stay under ~/.claude, where the hooks look.
    kit_home = environ.get("KIT_HOME") or os.path.join(home, ".claude")
    seats_dir = environ.get("CLAUDE_SEATS_DIR") or os.path.join(kit_home, "seats")
    seat_file = os.path.join(seats_dir, role + ".md")
    briefs_dir = environ.get("CLAUDE_BRIEFS_DIR") or os.path.join(home, ".claude", "briefs")
    closing = environ.get("CLAUDE_CLOSING_HOUR") or "22:00"

    # Fresh is read off the tokens as typed, before anything is added. claude spells a resume
    # several ways and three of them carry the id in the same token (-r<id> parses as well as
    # --resume=<id>). A launch that reopens a conversation is neither renamed nor handed a start line.
    def is_resume(t):
        return (t in ("-r", "--resume", "-c", "--continue") or t.startswith("--resume=")
                or t.startswith("--continue=") or (t.startswith("-r") and len(t) > 2))

    fresh = not any(is_resume(t) for t in extra)
    named = any(t in ("--name", "-n") or t.startswith("--name=") for t in extra)
    # A last token that does not begin with a dash is the prompt the owner typed, or an option
    # value: a launcher cannot know which options take one. Either way the start line stands aside.
    positional = bool(extra) and not extra[-1].startswith("-")

    # Agents never use the browser: every role but research starts without the Chrome integration.
    # It joins before the seat block, so the start line added after it stays the last token.
    if role != "research" and not any(t in ("--chrome", "--no-chrome") for t in extra):
        extra = extra + ["--no-chrome"]

    if is_seat:
        # Two seated panes in one folder share the projects link, so -c can load the other seat's
        # conversation. That was proven live, so the flag is refused here, not documented.
        if any(t in ("-c", "--continue") or t.startswith("--continue=") for t in extra):
            sys.stdout.flush()
            say("A seated role never continues the most recent conversation of a folder (the profiles "
                "share it); resume with -r and the picker, or -r <id>.", "red", err=True)
            raise SystemExit(1)
        # claude refuses to start when the append file is missing, so its absence costs one line here.
        if os.path.isfile(seat_file):
            plan["seat"] = seat_file
            extra = ["--append-system-prompt-file", seat_file] + extra
        else:
            say("Seat file missing: {}; the session opens without a seat.".format(seat_file), "yellow")

    # The role names the session (`claude --name`), so the prompt box, the /resume picker and the
    # transcript say which chair it is, whatever pane number the multiplexer hands out. Only when the
    # role was given: a plain `cca work -- -c` must not rename the orchestrator it resumes.
    if is_seat:
        if fresh and not named:
            extra = ["--name", role + "-" + datetime.now().strftime("%m%d-%H%M")] + extra
        if fresh and not positional:
            plan["start"] = START_LINE
            extra = extra + [START_LINE]
        elif fresh:
            plan["start"] = "none (positional given)"
            say("Start line not added: the last argument reads as your prompt; type the start of day "
                "yourself.", "yellow")
    elif "role" in given and not named:
        extra = ["--name", role] + extra

    # A default model per role: a --model on the line wins, then CLAUDE_MODEL_<ROLE>, then the
    # profile default. Put at the front so the start line stays last.
    plan["model"] = "profile default"
    at = next((k for k, t in enumerate(extra) if t == "--model" or t.startswith("--model=")), -1)
    if at >= 0:
        if extra[at].startswith("--model="):
            shown = extra[at][len("--model="):]
        elif at + 1 < len(extra):
            shown = extra[at + 1]
        else:
            shown = "(no value)"
        plan["model"] = shown + " (command line)"
    else:
        var = "CLAUDE_MODEL_" + role.upper()
        value = (environ.get(var) or "").strip()
        if value:
            extra = ["--model", value] + extra
            plan["model"] = "{} ({})".format(value, var)

    # Effort per role, the same way, from CLAUDE_EFFORT_<ROLE>. A value claude does not know adds
    # nothing and says so.
    plan["effort"] = "session default"
    if any(t == "--effort" or t.startswith("--effort=") for t in extra):
        plan["effort"] = "command line"
    else:
        var = "CLAUDE_EFFORT_" + role.upper()
        value = (environ.get(var) or "").strip().lower()
        if value:
            if value in EFFORTS:
                extra = ["--effort", value] + extra
                plan["effort"] = "{} ({})".format(value, var)
            else:
                plan["effort"] = "session default ({}='{}' refused)".format(var, value)
                say("{}='{}' is not low, medium, high, xhigh or max; no --effort passed.".format(var, value), "yellow")

    # The context window. Every role but research compacts against DEFAULT_WINDOW without the 200k
    # cap; research keeps the model's own window. CLAUDE_ROLE travels so the hooks know the pane.
    window = opts["window_n"]
    if window <= 0 and role != "research":
        window = DEFAULT_WINDOW
    cap = role != "research" and window <= 0
    ops = [("set", "CLAUDE_ROLE", role)]
    if cap:
        ops += [("set", "CLAUDE_CODE_DISABLE_1M_CONTEXT", "1"), ("unset", "CLAUDE_CODE_AUTO_COMPACT_WINDOW")]
    else:
        ops.append(("unset", "CLAUDE_CODE_DISABLE_1M_CONTEXT"))
    if window > 0:
        ops.append(("set", "CLAUDE_CODE_AUTO_COMPACT_WINDOW", str(window)))
    # A seated role also carries where its briefs live and when its day closes.
    if is_seat:
        ops += [("set", "CLAUDE_BRIEFS_DIR", briefs_dir), ("set", "CLAUDE_CLOSING_HOUR", closing)]

    plan.update({"extra": extra, "fresh": fresh, "window": window, "cap": cap, "role_ops": ops,
                 "is_seat": is_seat, "briefs": briefs_dir, "closing": closing})
    return plan


def shell_line(ops):
    """The environment part of a command typed into a new shell: one list of operations feeds both
    this string and the in-terminal environment, because the two drifted apart once on Windows."""
    parts = []
    for op in ops:
        if op[0] == "set":
            parts.append("export {}={}".format(op[1], shlex.quote(op[2])))
        else:
            parts.append("unset " + op[1])
    return "; ".join(parts)


def apply_ops(env, ops):
    for op in ops:
        if op[0] == "set":
            env[op[1]] = op[2]
        else:
            env.pop(op[1], None)
    return env


def claude_line(extra):
    return "claude" + "".join(" " + shlex.quote(t) for t in extra)


def show_env(plan, environ):
    print("CLAUDE_ROLE=" + plan["role"])
    print("CLAUDE_CODE_DISABLE_1M_CONTEXT=" + ("1" if plan["cap"] else "(unset)"))
    inherited = environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW")
    if plan["window"] > 0:
        window = str(plan["window"])
    elif plan["cap"]:
        window = "(removed, inherited {})".format(inherited) if inherited else "(removed)"
    else:
        window = "(unchanged, inherited {})".format(inherited) if inherited else "(unchanged)"
    print("CLAUDE_CODE_AUTO_COMPACT_WINDOW=" + window)
    print("PANE_COMMAND=" + shell_line(plan["role_ops"]))
    print("EXTRA=" + " ".join(plan["extra"]))
    print("COMMAND=" + claude_line(plan["extra"]))
    print("MODEL=" + plan["model"])
    print("EFFORT=" + plan["effort"])
    print("SEAT=" + plan["seat"])
    print("FRESH=" + ("true" if plan["fresh"] else "false"))
    print("START=" + plan["start"])
    print("CLAUDE_BRIEFS_DIR=" + (plan["briefs"] if plan["is_seat"] else "(unset)"))
    print("CLAUDE_CLOSING_HOUR=" + (plan["closing"] if plan["is_seat"] else "(unset)"))


# --- names, shortcuts and icons ---------------------------------------------------
# ~/.claude cannot move, so its display name is stored in ~/.claude-accounts/.names.json, the file
# the status line reads: {"default": "<name>", "shortcuts": {"<short>": "<target>"},
# "icons": {"<target>": "<hex code point>"}}. A target is a profile name or 'default'.

def read_names(path):
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path, data, mode=None):
    """UTF-8 without BOM, written atomically. A symlinked file is written where it points."""
    real = os.path.realpath(path)
    tmp = real + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, real)


def default_name(names):
    if names and names.get("default"):
        return str(names["default"])
    return "default"


def resolve_target(name, names):
    """Follows the shortcut chain. The cap stops two shortcuts that point at each other from hanging."""
    x = name
    shortcuts = (names or {}).get("shortcuts") or {}
    for _ in range(5):
        if isinstance(shortcuts, dict) and x in shortcuts:
            x = str(shortcuts[x])
        else:
            break
    return x


def shortcut_of(target, names):
    for short, value in ((names or {}).get("shortcuts") or {}).items():
        if str(value) == target:
            return short
    return None


def icon_of(target, names):
    hex_value = ((names or {}).get("icons") or {}).get(target)
    if not hex_value:
        hex_value = "2B50" if target == "default" else "1F464"
    try:
        return chr(int(str(hex_value), 16))
    except (ValueError, OverflowError):
        return ""


def icon_hex(value):
    """Accepts the pasted emoji or a hand-written code point and always stores hex, as the
    PowerShell switcher does, so both write the same file."""
    if re.match(r"^[0-9a-fA-F]{4,6}$", value):
        return value.upper()
    if not value:
        return None
    return format(ord(value[0]), "X")


def account_email(json_path, cred_path):
    if os.path.isfile(json_path):
        try:
            with io.open(json_path, encoding="utf-8-sig") as fh:
                text = fh.read()
        except OSError:
            text = ""
        try:
            data = json.loads(text)
            email = ((data or {}).get("oauthAccount") or {}).get("emailAddress")
            if email:
                return email
        except ValueError:
            # A file that does not parse is never rewritten; a regex is enough to show a label.
            m = re.search(r'"emailAddress"\s*:\s*"([^"]+)"', text)
            if m:
                return m.group(1)
    # .credentials.json is where Linux keeps the token, and where macOS falls back to when the
    # Keychain refuses a write. On macOS a signed-in profile shows its e-mail from .claude.json.
    if os.path.isfile(cred_path):
        return "signed in, e-mail not cached"
    return "not signed in yet"


def keychain_item(config_dir):
    """The name Claude Code gives the Keychain item of a config dir on macOS: nothing extra for
    ~/.claude, else the first 8 hex digits of the SHA-256 of the CLAUDE_CONFIG_DIR string in NFC.
    Read from the claude 2.1.329 binary. Used only to name the item in a note, never to touch it."""
    if config_dir is None:
        return "Claude Code-credentials"
    digest = hashlib.sha256(unicodedata.normalize("NFC", config_dir).encode("utf-8")).hexdigest()
    return "Claude Code-credentials-" + digest[:8]


# --- files ------------------------------------------------------------------------

def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest().upper()


def remove_tree_no_follow(path):
    """Deletes a profile folder without ever descending a symbolic link: a link is unlinked, which
    removes the link only. On Windows a recursive delete went through a junction and took the shared
    skills and memory with it (docs/ACCOUNTS.md, The traps); this is the guard against that here."""
    if os.path.islink(path):
        os.unlink(path)
        return
    with os.scandir(path) as it:
        entries = list(it)
    for entry in entries:
        if entry.is_symlink():
            os.unlink(entry.path)
        elif entry.is_dir(follow_symlinks=False):
            remove_tree_no_follow(entry.path)
        else:
            os.unlink(entry.path)
    os.rmdir(path)


# --- tabs and windows -------------------------------------------------------------

TERMINAL_SCRIPT = (
    "on run argv",
    'tell application "Terminal"',
    "activate",
    "do script (item 1 of argv)",
    "end tell",
    "end run",
)
# iTerm2 can open a tab in the window in front; Terminal.app can only open a window without UI
# scripting, which needs an accessibility grant.
ITERM_SCRIPT = (
    "on run argv",
    'tell application "iTerm"',
    "activate",
    "if (count of windows) is 0 then",
    "create window with default profile",
    "else",
    "tell current window to create tab with default profile",
    "end if",
    "tell current session of current window to write text (item 1 of argv)",
    "end tell",
    "end run",
)


def open_terminal(command):
    """Opens a new Terminal.app window, or an iTerm2 tab when iTerm2 is the terminal in use, running
    the command in the user's shell. Returns the application name, or None when it could not, in
    which case the caller prints the command to run by hand."""
    if sys.platform != "darwin":
        return None
    osascript = shutil.which("osascript")
    if not osascript:
        return None
    iterm = os.environ.get("TERM_PROGRAM") == "iTerm.app"
    script = ITERM_SCRIPT if iterm else TERMINAL_SCRIPT
    args = [osascript]
    for line in script:
        args += ["-e", line]
    # The command travels as an argument of the run handler, never inside the script text, so no
    # quote in a path or a prompt can break or extend the AppleScript.
    args.append(command)
    try:
        proc = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        if detail:
            say("  note: osascript said: " + detail.splitlines()[-1], "yellow")
        return None
    return "iTerm2" if iterm else "Terminal"


def herdr_run(herdr, args):
    proc = subprocess.run([herdr] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip() or "exit {}".format(proc.returncode))
    return proc.stdout.decode("utf-8", "replace")


def herdr_workspace_args(herdr, workspace):
    """-x takes the id (w8) or the number the UI shows (5); the number is resolved here."""
    if not workspace:
        return []
    ws_id = workspace
    if re.match(r"^\d+$", workspace):
        try:
            payload = json.loads(herdr_run(herdr, ["workspace", "list"]))
            spaces = (payload.get("result") or {}).get("workspaces") or []
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, AttributeError):
            fail("could not read the Herdr workspaces.")
        match = [w for w in spaces if str(w.get("number")) == workspace]
        if not match:
            fail("there is no Herdr workspace number {}.".format(workspace))
        ws_id = str(match[0].get("workspace_id"))
    return ["--workspace", ws_id]


def open_tab(label, folder, pane_command, window_command, role, workspace):
    """The -w path. Inside Herdr a new tab in the chosen workspace; elsewhere a new terminal window.
    The tab label keeps the cc- prefix on every system: scripts/herdr_panes.py reads the account of
    a pane from it."""
    herdr = shutil.which("herdr")
    if os.environ.get("HERDR_ENV") == "1" and herdr:
        ws_args = herdr_workspace_args(herdr, workspace)
        try:
            payload = json.loads(herdr_run(herdr, ["tab", "create"] + ws_args +
                                           ["--cwd", folder, "--label", label, "--no-focus"]))
            pane = payload["result"]["root_pane"]["pane_id"]
            herdr_run(herdr, ["pane", "run", pane, pane_command])
        except (RuntimeError, ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
            fail("could not open the Herdr tab: {}".format(error))
        say("Opened in Herdr tab {} (pane {}, role {}).".format(label, pane, role), "green")
        return 0
    app = open_terminal(window_command)
    if app:
        return app
    say("Could not open a new terminal from here. Open one and run:", "yellow")
    print("  " + window_command)
    return None


# --- help and list ----------------------------------------------------------------

HELP = """
cca  -  switch Claude Code accounts without logging out

  cca                       list the accounts and which one is active
  cca <account>             open that account here, in this terminal
  cca <account> -w          open it in a separate tab or window
  cca <account> -- <args>   anything after -- goes to claude untouched
  cca -h                    this help (also cca help)

Passing flags to claude
  cca work -- -c            continue that account's last session
  cca work -- -r            pick which session to resume
  Use the --: without it -r is cca's -Rename and -n its -NoShare. A flag cca does not know,
  -c or --model opus for instance, is forwarded to claude either way.

Options (the PowerShell spelling, its short alias and the GNU spelling all work)
  -Folder <path>   -Dir  --folder   where to open (defaults to the current folder)
  -List            -l    --list     list only
  -Tab             -w    --tab      open in a separate tab or window (Herdr, iTerm2 or Terminal)
  -Prepare         -p    --prepare  create the profile without opening a session
  -Sync            -s    --sync     bring CLAUDE.md and settings.json from the default
  -Rename <new>    -r    --rename   rename the account
  -Alias <short>   -a    --alias    set a shortcut for it (one per account)
  -Icon <emoji>    -i    --icon     icon shown in the list and the status line
  -Delete          -d    --delete   delete the profile, removing the links first
  -NoShare         -n    --no-share create it without linking skills or memory
  -Role <role>     -o    --role     orchestrator | analyst | lane (default) | research; every role
                                    but research compacts at a 300k window and starts without the
                                    browser (pass --chrome for it); research keeps the browser
                                    and the model's whole window. When given, the role also
                                    names the session (claude --name), even one reopened with -c.
                                    orchestrator and analyst are seated: the launcher appends
                                    <seats>/<role>.md, refuses -c, and on a fresh launch names the
                                    session with the minute and hands it a start line
  -Window <n>            --window   auto-compact window for this launch instead of the 300k
                                    default (CLAUDE_CODE_AUTO_COMPACT_WINDOW=<n>). Larger means fewer
                                    compactions and a larger floor on every turn; 0 keeps the default
  -ShowEnv               --show-env print the variables the session would get, then exit
  CLAUDE_MODEL_<ROLE>               environment variable: default model of a role (CLAUDE_MODEL_ORCHESTRATOR,
                                    _ANALYST, _LANE, _RESEARCH), used when the line has no --model
  CLAUDE_EFFORT_<ROLE>              environment variable: effort of a role (low, medium, high, xhigh, max),
                                    used when the line has no --effort
  -Workspace <n>   -x    --workspace with -w inside Herdr: workspace (number or id) for the new tab
  -Help            -h    --help     this help

Examples
  cd ~/code/my-app; cca work      open the work account there
  cca work -- -c                  open it continuing the last session
  cca personal -a p               give it the shortcut 'p'
  cca old -d                      delete that profile
"""


def show_list(base, default_dir, home, names_file):
    names = read_names(names_file)
    say()
    say("Claude Code accounts", "cyan")
    say()
    active = os.environ.get("CLAUDE_CONFIG_DIR")

    def row(mark, name, short, text, icon):
        label = "{} ({})".format(name, short) if short else name
        print("  {} {} {:<20} {}".format(mark, icon or " ", label, text))

    row(" " if active else "*", default_name(names), shortcut_of("default", names),
        account_email(os.path.join(home, ".claude.json"), os.path.join(default_dir, ".credentials.json")),
        icon_of("default", names))
    say("  {}   {:<20} {}".format(" ", "", "(~/.claude, the one plain 'claude' uses)"), "gray")
    if os.path.isdir(base):
        active_norm = os.path.normpath(active) if active else None
        for name in sorted((n for n in os.listdir(base) if os.path.isdir(os.path.join(base, n))), key=str.lower):
            path = os.path.join(base, name)
            mark = " "
            if active_norm and (active_norm == os.path.normpath(path)
                                or os.path.realpath(active_norm) == os.path.realpath(path)):
                mark = "*"
            row(mark, name, shortcut_of(name, names),
                account_email(os.path.join(path, ".claude.json"), os.path.join(path, ".credentials.json")),
                icon_of(name, names))
    say()
    say("  The asterisk is the account active in THIS terminal. Shortcut in brackets.", "gray")
    say()


# --- the commands -----------------------------------------------------------------

def sync_files(default_dir, profile_dir, sync, account):
    """Brings CLAUDE.md and settings.json over WITHOUT overwriting what was changed inside the
    profile. The stamp holds the hash of both sides of the last copy; a profile file that no longer
    matches it was edited (by hand, or by Claude Code on /config) and is left alone unless -Sync.
    Modification dates cannot say whether the profile drifted from what it was given."""
    stamp_path = os.path.join(profile_dir, ".sync-source.json")
    stamp = {}
    if os.path.isfile(stamp_path):
        try:
            with io.open(stamp_path, encoding="utf-8-sig") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                stamp = loaded
        except (OSError, ValueError):
            stamp = {}
    changed = False
    merger = os.path.join(HERE, "merge-settings.py")

    def copy_setting(src, dst, name):
        # JSON is MERGED, never replaced: replacing drops keys Claude Code writes on its own, such as
        # skipDangerousModePermissionPrompt. The merger refuses to write a destination that does not
        # parse, and keeps the profile's own model.
        if name.endswith(".json") and os.path.isfile(merger):
            proc = subprocess.run([sys.executable, merger, src, dst], stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
            out = proc.stdout.decode("utf-8", "replace").strip()
            if proc.returncode != 0:
                say("  " + out, "yellow")
                return False
            ok("{} : {}".format(name, out))
            return True
        if name.endswith(".json"):
            say("  note: merge-settings.py is not beside this script, copying {} instead of merging".format(name), "yellow")
        shutil.copyfile(src, dst)
        ok("copied from the default: " + name)
        return True

    for name in COPIED:
        src = os.path.join(default_dir, name)
        dst = os.path.join(profile_dir, name)
        if not os.path.isfile(src):
            continue
        if os.path.islink(dst):
            # Writing through a link would change whatever it points at, possibly the default itself.
            say("  note: {} is a link in this profile, leaving it alone".format(name), "yellow")
            continue
        h_src = file_hash(src)
        if not os.path.exists(dst):
            if copy_setting(src, dst, name):
                stamp[name] = {"source": h_src, "dest": file_hash(dst)}
                changed = True
            continue
        h_dst = file_hash(dst)
        entry = stamp.get(name) if isinstance(stamp.get(name), dict) else {}
        source_changed = entry.get("source") != h_src
        touched_here = entry.get("dest") != h_dst
        if not source_changed and not touched_here:
            continue
        # Only the profile moved: nothing new to bring, unless -Sync asks for a reset.
        if not source_changed and not sync:
            continue
        # The merge keeps keys the profile owns but overwrites a key the default also has, hence the
        # brake: once the profile has been touched, it takes an explicit -Sync.
        if touched_here and not sync:
            say("  {} changed inside this profile, leaving it alone.".format(name), "yellow")
            say("  To take the default's version:  cca {} -s".format(account), "gray")
            continue
        if touched_here:
            shutil.copyfile(dst, dst + ".bak")
            say("  previous version saved as {}.bak".format(name), "yellow")
        if copy_setting(src, dst, name):
            stamp[name] = {"source": h_src, "dest": file_hash(dst)}
            changed = True
    if changed:
        write_json(stamp_path, stamp)


def mark_trusted(profile_dir, folder):
    """Marks the folder trusted in the profile's .claude.json so the dialog does not come back.
    Hard rule: a file that exists and does not parse is never touched, because rewriting it from
    scratch drops oauthAccount and signs the account out."""
    json_path = os.path.join(profile_dir, ".claude.json")
    data, unreadable = None, False
    if os.path.isfile(json_path):
        try:
            with io.open(json_path, encoding="utf-8-sig") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data, unreadable = None, True
        except (OSError, ValueError):
            data, unreadable = None, True
    if unreadable:
        say("  note: cannot read this profile's .claude.json, leaving it alone.", "yellow")
        say("  You will probably be asked to confirm the folder. Confirm and carry on.", "gray")
        return json_path
    # The folder as typed and the folder with links resolved are both written when they differ:
    # claude starts with the physical working directory (on macOS /tmp is /private/tmp), and a key
    # that does not match it brings the dialog back.
    keys = [folder]
    real = os.path.realpath(folder)
    if real != folder:
        keys.append(real)
    if data is None:
        data = {"hasCompletedOnboarding": True, "projects": {}}
    projects = data.get("projects")
    if not isinstance(projects, dict):
        projects = {}
        data["projects"] = projects
    missing = [k for k in keys if not (isinstance(projects.get(k), dict) and projects[k].get("hasTrustDialogAccepted") is True)]
    if not missing:
        return json_path
    for key in missing:
        entry = projects.get(key) if isinstance(projects.get(key), dict) else {}
        entry["hasTrustDialogAccepted"] = True
        entry["hasClaudeMdExternalIncludesApproved"] = True
        entry["hasClaudeMdExternalIncludesWarningShown"] = True
        projects[key] = entry
    # Keep the file's own permissions; a new one is private, like the one Claude Code writes.
    mode = 0o600
    if os.path.exists(json_path):
        mode = os.stat(json_path).st_mode & 0o7777
    write_json(json_path, data, mode)
    ok("folder marked as trusted: " + missing[0])
    return json_path


def main(argv):
    if os.name == "nt":
        print("cca is the macOS and Linux switcher. On Windows use cc, which runs claude-account.ps1.",
              file=sys.stderr)
        return 1
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True, errors="replace")
        except (AttributeError, ValueError):
            pass

    try:
        opts, extra, given = parse_args(argv)
    except UsageError as error:
        fail(str(error))
    role = (opts["role"] or "").lower()
    if role not in ROLES:
        fail("-Role must be one of orchestrator, analyst, lane, research (got '{}').".format(opts["role"]))
    opts["role"] = role
    try:
        opts["window_n"] = int(opts["window"])
    except (TypeError, ValueError):
        fail("-Window takes a number of tokens, from 0 to {} (got '{}').".format(MAX_WINDOW, opts["window"]))
    if not 0 <= opts["window_n"] <= MAX_WINDOW:
        fail("-Window takes a number of tokens, from 0 to {} (got {}).".format(MAX_WINDOW, opts["window_n"]))

    home = os.path.expanduser("~")
    default_dir = os.path.join(home, ".claude")
    base = os.path.join(home, ".claude-accounts")
    names_file = os.path.join(base, ".names.json")
    environ = os.environ

    plan = build_plan(opts, given, extra, home, environ)
    # -ShowEnv prints the plan and exits, so the wiring can be asserted without opening a session.
    if opts["showenv"]:
        show_env(plan, environ)
        return 0

    account = opts["account"]
    if opts["help"] or account in ("?", "help"):
        print(HELP)
        return 0
    if opts["list"] or not account:
        show_list(base, default_dir, home, names_file)
        if not account:
            say("  cca -h for every option.", "gray")
            say()
        return 0

    if not NAME_RE.match(account) or account in (".", ".."):
        fail("invalid name. Use letters, digits, dot, dash or underscore.")
    folder = opts["folder"]
    if folder is None:
        # $PWD keeps the folder as the user sees it (a symlinked path stays symlinked) as long as it
        # still names the directory we are in.
        cwd = os.getcwd()
        logical = environ.get("PWD")
        try:
            folder = logical if logical and os.path.isabs(logical) and os.path.samefile(logical, cwd) else cwd
        except OSError:
            folder = cwd
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        fail("no such folder: " + folder)
    claude = shutil.which("claude")
    if not claude:
        fail("cannot find the claude command")

    # --- resolve which account we are talking about ------------------------------
    # This must stay ABOVE every block that uses is_default; on Windows, when it sat below, a
    # shortcut of the default created a new profile named after the shortcut.
    names = read_names(names_file)
    def_name = default_name(names)
    target = resolve_target(account, names)
    is_default = target.lower() == "default" or target == def_name
    if not is_default and (not NAME_RE.match(target) or target in (".", "..")):
        fail("the shortcut '{}' points at '{}', which is not a valid profile name.".format(account, target))
    canonical = "default" if is_default else target

    # --- shortcut and icon (both may be given in one call) ------------------------
    if opts["alias"] or opts["icon"]:
        if not (is_default or os.path.isdir(os.path.join(base, target))):
            fail("no such account '{}'. Create it first with: cca {} -p".format(target, target))
        if names is None:
            names = {}
        if opts["alias"]:
            short = opts["alias"]
            if not NAME_RE.match(short) or short in (".", ".."):
                fail("invalid characters in the shortcut.")
            if short == target:
                fail("a shortcut cannot be the same as its target.")
            if short.lower() == "default" or short == def_name:
                fail("'{}' already names the default account.".format(short))
            if os.path.exists(os.path.join(base, short)):
                fail("'{}' is already a real profile name.".format(short))
            shortcuts = names.get("shortcuts")
            if not isinstance(shortcuts, dict):
                shortcuts = {}
                names["shortcuts"] = shortcuts
            # One shortcut per account: two names for the same thing is the confusion this prevents.
            for old in [k for k, v in shortcuts.items() if str(v) == canonical and k != short]:
                del shortcuts[old]
                ok("previous shortcut removed: " + old)
            shortcuts[short] = canonical
        hex_value = None
        if opts["icon"]:
            hex_value = icon_hex(opts["icon"])
            if not hex_value:
                fail("cannot read that icon. Pass an emoji or its code point, for example 1F3E0.")
            icons = names.get("icons")
            if not isinstance(icons, dict):
                icons = {}
                names["icons"] = icons
            icons[canonical] = hex_value
        os.makedirs(base, mode=0o700, exist_ok=True)
        write_json(names_file, names)
        say()
        if opts["alias"]:
            say("Shortcut set: cca {}  ->  {}".format(opts["alias"], canonical), "green")
        if hex_value:
            say("Icon for {} : {}  (U+{})".format(canonical, icon_of(canonical, names), hex_value), "green")
        return 0

    # --- rename -------------------------------------------------------------------
    if opts["rename"]:
        new = opts["rename"]
        if not NAME_RE.match(new) or new in (".", ".."):
            fail("invalid characters in the new name.")
        if os.path.exists(os.path.join(base, new)):
            fail("a profile named '{}' already exists.".format(new))
        if is_default:
            os.makedirs(base, mode=0o700, exist_ok=True)
            if names is None:
                names = {}
            if new.lower() == "default":
                names.pop("default", None)
                write_json(names_file, names)
                say("The default account is called 'default' again.", "green")
                return 0
            names["default"] = new
            write_json(names_file, names)
            say()
            say("The default account is now called '{}'.".format(new), "green")
            say("That is only the name you invoke it by. The folder stays ~/.claude,", "gray")
            say("because that is the one plain 'claude' uses and it cannot move.", "gray")
            return 0
        if new.lower() == "default":
            fail("'default' is reserved for ~/.claude.")
        src = os.path.join(base, target)
        if not os.path.isdir(src):
            fail("no such profile '{}'.".format(target))
        dst = os.path.join(base, new)
        signed_in = account_email(os.path.join(src, ".claude.json"),
                                  os.path.join(src, ".credentials.json")) != "not signed in yet"
        os.rename(src, dst)
        moved = False
        if names:
            shortcuts = names.get("shortcuts")
            if isinstance(shortcuts, dict):
                for k, v in list(shortcuts.items()):
                    if str(v) == target:
                        shortcuts[k] = new
                        moved = True
            icons = names.get("icons")
            if isinstance(icons, dict) and target in icons:
                icons[new] = icons.pop(target)
                moved = True
        if moved:
            write_json(names_file, names)
            ok("shortcut and icon now point at '{}'".format(new))
        say()
        say("Profile renamed: {} -> {}".format(target, new), "green")
        say("The links store absolute targets, so they keep pointing at the right place.", "gray")
        if sys.platform == "darwin" and signed_in:
            # The Keychain item is named after the folder path, which just changed.
            say("On macOS the sign-in is kept in the Keychain under a name made from the folder path,", "yellow")
            say("so '{}' asks you to sign in once more. The old entry, '{}',".format(new, keychain_item(src)), "yellow")
            say("stays in the login Keychain; remove it in Keychain Access if you no longer want it.", "yellow")
        return 0

    tab_label = "cc-" + (def_name if is_default else target) + ("" if role == "lane" else "-" + role)

    # --- the default account launches as is, with no isolation --------------------
    if is_default:
        if opts["delete"]:
            fail("the default account cannot be deleted from here.")
        say()
        say("Account '{}' (~/.claude), no CLAUDE_CONFIG_DIR.".format(def_name), "cyan")
        # -Prepare creates and links a profile, and there is no profile to create here.
        if opts["prepare"]:
            say("  Nothing to prepare: ~/.claude already exists and is the default.", "gray")
            return 0
        account_ops = [("unset", "CLAUDE_CONFIG_DIR")]
        if not opts["tab"]:
            return launch_here(claude, plan, account_ops, folder)
        pane_cmd, window_cmd = tab_commands(account_ops, plan, folder, def_name)
        result = open_tab(tab_label, folder, pane_cmd, window_cmd, role, opts["workspace"])
        if result not in (0, None):
            say("Window opened on account {} in {}.".format(def_name, result), "green")
        return 0

    profile_dir = os.path.join(base, target)

    # --- delete -------------------------------------------------------------------
    if opts["delete"]:
        if not os.path.lexists(profile_dir):
            fail("no such profile '{}'".format(target))
        real = os.path.realpath(profile_dir)
        real_default = os.path.realpath(default_dir)
        if not os.path.islink(profile_dir) and (real == real_default or real_default.startswith(real + os.sep)):
            fail("refusing: {} resolves to {}, which holds the default directory.".format(profile_dir, real))
        say()
        say("About to delete profile {} at {}".format(target, profile_dir), "yellow")
        say("Links are removed first, so your shared skills and memory are safe.", "gray")
        signed_in = account_email(os.path.join(profile_dir, ".claude.json"),
                                  os.path.join(profile_dir, ".credentials.json")) != "not signed in yet"
        if sys.platform == "darwin" and signed_in:
            # Deleting the folder does not reach the Keychain, so the token would outlive the profile.
            say("Its sign-in stays in the login Keychain as '{}'.".format(keychain_item(profile_dir)), "gray")
            say("To remove that too, answer N, open the account, run /logout, then delete it.", "gray")
        try:
            answer = input("  Confirm? (y/N) ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer.strip().lower() != "y":
            print("  cancelled")
            return 0
        shared_before = [n for n in LINKED if os.path.isdir(os.path.join(default_dir, n))]
        if os.path.islink(profile_dir):
            os.unlink(profile_dir)
            ok("the profile was itself a link; removed the link only")
        else:
            for name in LINKED:
                link = os.path.join(profile_dir, name)
                if os.path.islink(link):
                    os.unlink(link)
                    ok("link removed: " + name)
            remove_tree_no_follow(profile_dir)
        gone = [n for n in shared_before if not os.path.isdir(os.path.join(default_dir, n))]
        if gone:
            fail("WARNING: {} is gone from {}. Check before continuing.".format(", ".join(gone), default_dir))
        cleaned = False
        if names:
            shortcuts = names.get("shortcuts")
            if isinstance(shortcuts, dict):
                for k in [k for k, v in shortcuts.items() if str(v) == target]:
                    del shortcuts[k]
                    cleaned = True
            icons = names.get("icons")
            if isinstance(icons, dict) and target in icons:
                del icons[target]
                cleaned = True
        if cleaned:
            write_json(names_file, names)
            ok("orphaned shortcut and icon removed")
        ok("profile deleted, the default directory is untouched")
        return 0

    # --- create or refresh the profile --------------------------------------------
    say()
    say("Account " + target, "cyan")
    # -Sync refreshes a profile that exists. A name that resolves to none is a typo or a stale note,
    # and building a profile for it leaves a stray folder of links.
    if opts["sync"] and not os.path.isdir(profile_dir):
        fail("no such profile '{}'. -Sync refreshes an existing one; create it with: cca {} -p".format(target, target))
    is_new = not os.path.isdir(profile_dir)
    # Private: on Linux a profile holds its OAuth token in .credentials.json.
    os.makedirs(profile_dir, mode=0o700, exist_ok=True)
    if is_new:
        ok("profile created at " + profile_dir)

    if not opts["noshare"]:
        for name in LINKED:
            src = os.path.join(default_dir, name)
            link = os.path.join(profile_dir, name)
            if not os.path.isdir(src):
                continue
            if os.path.islink(link):
                continue
            if os.path.exists(link):
                say("  note: {} exists as a real folder, not linking it".format(name), "yellow")
                continue
            try:
                os.symlink(src, link)
                ok("shared: " + name)
            except OSError as error:
                say("  note: could not link {} ({})".format(name, error.strerror or error), "yellow")

    sync_files(default_dir, profile_dir, opts["sync"], account)
    json_path = mark_trusted(profile_dir, folder)

    who = account_email(json_path, os.path.join(profile_dir, ".credentials.json"))
    ok("session: " + who)
    if who == "not signed in yet":
        say("  First time on this account: it will ask you to sign in. Once only.", "yellow")
        say("  Sign out of claude.ai in your browser first, or the OAuth flow will", "yellow")
        say("  silently reuse the account already open there.", "yellow")

    if opts["prepare"]:
        say()
        say("Profile ready. Not opening a session because you passed -Prepare.", "green")
        say("  To use it:  cca " + account, "gray")
        return 0

    account_ops = [("set", "CLAUDE_CONFIG_DIR", profile_dir)]
    say()
    if not opts["tab"]:
        say("Account {} in this terminal until claude exits. Use -w to open it separately.".format(target), "gray")
        return launch_here(claude, plan, account_ops, folder)

    pane_cmd, window_cmd = tab_commands(account_ops, plan, folder, target)
    result = open_tab(tab_label, folder, pane_cmd, window_cmd, role, opts["workspace"])
    if result not in (0, None):
        say("Window opened on account {} in {}. Your other sessions are untouched.".format(target, result), "green")
    return 0


def tab_commands(account_ops, plan, folder, label):
    """The line a Herdr pane runs (Herdr already opens it in the folder) and the line a new terminal
    window runs. Both export the variables into that shell, so the tab stays on the account after
    claude exits, as a Windows tab does. POSIX syntax: zsh, bash and dash all read it."""
    env_part = shell_line(account_ops + plan["role_ops"])
    pane = env_part + "; " + claude_line(plan["extra"])
    window = ("cd " + shlex.quote(folder) + "; " + env_part + "; echo " + shlex.quote("Account: " + label) +
              "; " + claude_line(plan["extra"]))
    return pane, window


def launch_here(claude, plan, account_ops, folder):
    """Replaces this process with claude, so signals and the terminal go straight to it. The parent
    shell keeps its own environment: when claude exits, the terminal is back where it was."""
    env = apply_ops(dict(os.environ), account_ops + plan["role_ops"])
    os.chdir(folder)
    env["PWD"] = folder
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(claude, ["claude"] + plan["extra"], env)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
