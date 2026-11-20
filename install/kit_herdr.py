"""kit_herdr.py - the optional Herdr step of the user-scope install on macOS and Linux, the port of install/herdr.ps1,
imported by install.py by file path.

    get_choice(yes, no, dry_run) -> 'yes' | 'no' | 'ask' | 'unattended'
    install_herdr(kit_root, kit_home, choice, dry_run=False, out=print) -> None

On yes it installs Herdr with its official installer (https://herdr.dev/install.sh), unless a herdr is already on
PATH, which keeps its own channel (the run says when it is not preview), puts herdr/config.toml in place, wires the
Claude Code integration and points at the Ctrl+Option+N hotkey. A config.toml you already have and that differs is
never touched: the kit version lands beside it as config.toml.new, the same rule as every other file the installer
writes.

The channel. install.sh takes no channel: it installs the stable build named by https://herdr.dev/latest.json, and
HERDR_INSTALL_DIR (default ~/.local/bin) is its only setting (read 2026-11-20). On macOS and Linux the channel is a
line of config.toml, [update] channel, and herdr/config.toml names preview. So a fresh install is followed, once the
kit config.toml is in place, by herdr channel set preview, which installs the latest preview build and leaves that
config.toml byte for byte as it was (herdr 0.9.5 in a throwaway HOME, 2026-11-20). A config.toml of the user's own is
never edited for it: the run says which channel it names. For the same reason an installed Herdr with no config.toml
moves to preview at its next update once the kit config.toml is written, and the run says so.

The config path is the one herdr --help prints: HERDR_CONFIG_PATH when set, else $XDG_CONFIG_HOME/herdr/config.toml,
else ~/.config/herdr/config.toml, on macOS too (herdr 0.8.0 and 0.9.5, 2026-11-20).

The hotkey. macOS gives no file a global key the way a Windows .lnk carries one, and every way to bind one (a
Shortcuts shortcut, an Automator Quick Action with a pbs key equivalent) needs a step in an app, or writes system
preferences no test can check without changing the machine. So the run points at herdr/hotkey/launch-herdr.sh and at
the one manual step in herdr/README.md, and binds nothing itself.

Herdr is optional, so nothing here can fail the install: a step that fails prints a FAIL line and the next steps that
depend on it are skipped.

The external commands can be replaced through the environment, which is what the tests do:
  KIT_HERDR_INSTALLER     an installer (a path or a URL) saved and run instead of the official one
  KIT_HERDR_EXE           the herdr to call instead of the one found on PATH
  KIT_HERDR_CONFIG_DIR    the folder of config.toml instead of the one herdr reads
  KIT_HERDR_HOTKEY        a sh script run for the hotkey step instead of the manual-step note
  KIT_HERDR_ANSWER        the answer to the question, instead of reading the console
  KIT_ASSUME_INTERACTIVE  1 or 0, instead of asking the console whether a person is there
"""
import filecmp
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile

INSTALLER_URL = "https://herdr.dev/install.sh"
STEP_TIMEOUT = 600  # seconds; a download of the preview build included
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
QUESTION = ("Add Herdr, the terminal workspace for agents, on its preview channel and set up the way this kit uses "
            "it? [Y/n] ")


def interactive():
    if os.environ.get("KIT_ASSUME_INTERACTIVE") == "1":
        return True
    if os.environ.get("KIT_ASSUME_INTERACTIVE") == "0":
        return False
    # A captured run (a test, a pipeline, the daily job) has no person to answer, and a question there would hang it,
    # so any redirection counts as unattended.
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def get_choice(yes, no, dry_run):
    """yes, no, ask (a dry run that would ask), or unattended (no console and no switch)."""
    if yes and no:
        raise ValueError("--herdr and --no-herdr cannot both be given.")
    if yes:
        return "yes"
    if no:
        return "no"
    if dry_run:
        return "ask"
    answer = os.environ.get("KIT_HERDR_ANSWER")
    if answer is None:
        if not interactive():
            return "unattended"
        try:
            answer = input(QUESTION)
        except EOFError:
            return "no"
    if re.match(r"^\s*(n|no)\s*$", answer, re.IGNORECASE):
        return "no"
    return "yes"


def find_herdr():
    if os.environ.get("KIT_HERDR_EXE"):
        return os.environ["KIT_HERDR_EXE"]
    found = shutil.which("herdr")
    if found:
        return found
    # install.sh puts herdr in HERDR_INSTALL_DIR, else ~/.local/bin, which this process's PATH may not have: a shell
    # whose profile never added it, or one started before the install.
    for folder in (os.environ.get("HERDR_INSTALL_DIR"), os.path.join(os.path.expanduser("~"), ".local", "bin")):
        if folder:
            candidate = os.path.join(folder, "herdr")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def config_path():
    if os.environ.get("KIT_HERDR_CONFIG_DIR"):
        return os.path.join(os.environ["KIT_HERDR_CONFIG_DIR"], "config.toml")
    if os.environ.get("HERDR_CONFIG_PATH"):
        return os.path.abspath(os.environ["HERDR_CONFIG_PATH"])
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(os.path.abspath(base), "herdr", "config.toml")


def config_plan(kit_config, config):
    if not os.path.exists(config):
        return "write " + config
    if filecmp.cmp(config, kit_config, shallow=False):
        return "skip same " + config
    return "keep yours %s, the kit version lands beside it as %s.new" % (config, os.path.basename(config))


def launcher_path(kit_root):
    return os.path.join(kit_root, "herdr", "hotkey", "launch-herdr.sh")


def hotkey_plan(kit_root):
    if os.environ.get("KIT_HERDR_HOTKEY"):
        return "run " + os.environ["KIT_HERDR_HOTKEY"]
    if sys.platform == "darwin":
        return ("one step by hand: a Shortcuts shortcut that runs /bin/sh %s, key Ctrl+Option+N "
                "(herdr/README.md, macOS)" % shlex.quote(launcher_path(kit_root)))
    return "none on Linux: bind herdr to a key in your desktop's keyboard settings"


def run(argv, env=None, timeout=STEP_TIMEOUT, merge=True):
    """Runs one command with no input. Returns its exit code (None when it ran past timeout and was stopped, with every
    process it started) and its output lines."""
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT if merge else subprocess.DEVNULL, env=env,
                                   start_new_session=True)
    except OSError as error:
        return 127, [str(error)]
    try:
        output, _ = process.communicate(timeout=timeout)
        code = process.returncode
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
                process.wait(timeout=5)
                break
            except OSError:
                break
            except subprocess.TimeoutExpired:
                continue
        try:
            output, _ = process.communicate(timeout=5)
        except (subprocess.TimeoutExpired, ValueError):
            output = b""
        code = None
    return code, (output or b"").decode("utf-8", "replace").splitlines()


def last_line(lines):
    said = [ANSI.sub("", line).strip() for line in lines]
    said = [line for line in said if line]
    return said[-1] if said else ""


def step(label, argv, out, env=None):
    """Runs one external step and prints one ok or FAIL line; True when it exited 0."""
    code, lines = run(argv, env=env)
    last = last_line(lines)
    tail = (": " + last) if last else ""
    if code == 0:
        out("  ok    %s%s" % (label, tail))
        return True
    if code is None:
        out("  FAIL  %s (stopped after %d s)%s" % (label, STEP_TIMEOUT, tail))
    else:
        out("  FAIL  %s (exit %d)%s" % (label, code, tail))
    return False


def read_channel(exe):
    """herdr channel show, or '' when it fails: a failure here never stops the install."""
    code, lines = run([exe, "channel", "show"], timeout=60, merge=False)
    if code != 0:
        return ""
    return "\n".join(lines).strip()


def fetch(source, target):
    """Saves the installer to target: a path is copied, a URL is downloaded with curl, which install.sh needs too.
    (herdr.dev answers 403 to Python's own HTTP client, 2026-11-20.)"""
    if "://" not in source:
        shutil.copyfile(source, target)
        return
    curl = shutil.which("curl")
    if not curl:
        raise OSError("curl is not on PATH, and the download needs it")
    code, lines = run([curl, "-fsSL", "--retry", "3", "--connect-timeout", "10", "--max-time", "120", "-o", target,
                       source], timeout=180)
    if code != 0:
        raise OSError("curl %s: %s" % ("exit %d" % code if code is not None else "stopped", last_line(lines)))


def install_herdr(kit_root, kit_home, choice, dry_run=False, out=print):
    out("")
    if choice == "no":
        out("herdr        not added. Add it later with: install.py --herdr")
        return
    if choice == "unattended":
        out("herdr        not added: there is no console to ask. Pass --herdr to add it, or --no-herdr to skip the "
            "question.")
        return
    config = config_path()
    kit_config = os.path.join(kit_root, "herdr", "config.toml")
    exe = find_herdr()

    if dry_run:
        if choice == "ask":
            out('herdr        a real run asks "Add Herdr? [Y/n]" (Enter is yes); on yes:')
        else:
            out("herdr        --herdr given; a real run does:")
        if exe:
            out("  install    skip, herdr is already at " + exe)
        else:
            out("  install    the official installer, " + INSTALLER_URL)
        out("  channel    a fresh install moves to preview (herdr channel set preview) once the kit config.toml is in "
            "place; an installed Herdr keeps its channel, and the run says which")
        out("  config     " + config_plan(kit_config, config))
        out("  integrate  herdr integration install claude")
        out("  hotkey     " + hotkey_plan(kit_root))
        return

    out("herdr")
    fresh = not exe
    if exe:
        out("  ok    already installed at " + exe)
    else:
        source = os.environ.get("KIT_HERDR_INSTALLER") or INSTALLER_URL
        label = "the Herdr installer" if os.environ.get("KIT_HERDR_INSTALLER") else "the Herdr installer, " + source
        # Saved to a folder of its own and run as a file, never piped into a shell: what runs is what was saved.
        folder = tempfile.mkdtemp(prefix="kit-herdr-")
        installer = os.path.join(folder, "install.sh")
        try:
            try:
                fetch(source, installer)
            except (OSError, ValueError) as error:
                out("  FAIL  %s (exit 1): %s" % (label, error))
                ok = False
            else:
                # install.sh reads no channel today (it always installs stable); the variable costs nothing and is
                # the one install.ps1 reads, should install.sh learn it.
                ok = step(label, ["/bin/sh", installer], out, env=dict(os.environ, HERDR_CHANNEL="preview"))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        if not ok:
            out("  skip  the rest of the Herdr setup, since Herdr did not install")
            return
        exe = find_herdr()
        if not exe:
            out("  FAIL  Herdr installed, but this run cannot find herdr. Open a new terminal and run: install.py "
                "--herdr")
            return
        if not shutil.which("herdr") and not os.environ.get("KIT_HERDR_EXE"):
            folder = os.path.dirname(exe)
            out('  note  %s is not on your PATH; add it in your shell profile so a new terminal finds herdr: '
                'export PATH="%s:$PATH"' % (folder, folder))

    plan = config_plan(kit_config, config)
    # An installed Herdr keeps the channel its owner chose; the run only says when it is not the preview channel this
    # kit is verified on. The channel lives in config.toml, so it is read before the kit config.toml lands.
    if not fresh:
        channel = read_channel(exe)
        if channel == "preview":
            out("  ok    channel preview")
        elif channel and plan.startswith("write "):
            out("  note  your Herdr follows '%s' and has no config.toml; the kit config.toml written next names "
                "preview, so its next update (herdr update) is a preview build. To stay on '%s': herdr channel set %s"
                % (channel, channel, channel))
        elif channel:
            out("  note  your Herdr follows '%s'; this kit is verified on preview. To follow it: herdr channel set "
                "preview" % channel)
        else:
            out("  note  could not read the channel of your Herdr; this kit is verified on preview")

    try:
        if plan.startswith("write "):
            os.makedirs(os.path.dirname(config), exist_ok=True)
            shutil.copyfile(kit_config, config)
        elif plan.startswith("keep yours "):
            shutil.copyfile(kit_config, config + ".new")
        out("  ok    config: " + plan)
    except OSError as error:
        out("  FAIL  config: %s" % error)

    if fresh:
        kit_config_live = os.path.exists(config) and filecmp.cmp(config, kit_config, shallow=False)
        if kit_config_live:
            step("channel preview", [exe, "channel", "set", "preview"], out)
        else:
            channel = read_channel(exe)
            if channel == "preview":
                out("  ok    channel preview, as your config.toml names it")
            elif channel:
                out("  note  your config.toml keeps Herdr on '%s'; this kit is verified on preview. To follow it: "
                    "herdr channel set preview" % channel)
            else:
                out("  note  could not read the channel of your Herdr; this kit is verified on preview")

    # Herdr writes its hook into the Claude Code profile that CLAUDE_CONFIG_DIR names. It goes to the kit home this
    # run installed, whatever profile the shell happens to point at.
    step("integration install claude", [exe, "integration", "install", "claude"], out,
         env=dict(os.environ, CLAUDE_CONFIG_DIR=kit_home))

    hotkey = os.environ.get("KIT_HERDR_HOTKEY")
    launcher = launcher_path(kit_root)
    if hotkey:
        step("hotkey Ctrl+Option+N", ["/bin/sh", hotkey, launcher], out)
    elif sys.platform != "darwin":
        out("  note  hotkey: none on Linux; bind herdr to a key in your desktop's keyboard settings")
    elif not os.path.isfile(launcher):
        out("  FAIL  hotkey: the launcher %s is missing" % launcher)
    else:
        out("  note  hotkey Ctrl+Option+N is one step by hand on macOS, which gives no file a global key: in the "
            "Shortcuts app,")
        out("        a shortcut that runs /bin/sh %s, with Ctrl+Option+N as its key." % shlex.quote(launcher))
        out("        Steps: %s, section macOS." % os.path.join(kit_root, "herdr", "README.md"))
