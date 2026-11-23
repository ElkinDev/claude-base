#!/bin/bash
# get.sh - install claude-base on a macOS (or Linux) machine with one line, pasted into a terminal:
#
#   curl -fsSL https://raw.githubusercontent.com/ElkinDev/claude-base/main/install/get.sh | bash
#
# Options go after `bash -s --`, for example:
#   curl -fsSL https://raw.githubusercontent.com/ElkinDev/claude-base/main/install/get.sh | bash -s -- --no-herdr
#
# The twin of install/get.ps1. What it does, in order:
#   1. Checks git, Python 3.8+ and Claude Code, and offers to install a missing one: on macOS git and
#      Python come with the Command Line Tools (xcode-select --install, which opens Apple's own
#      dialog); Claude Code through npm when npm is on PATH, else through its official installer
#      (https://claude.ai/install.sh), saved to a temp file and run as a file. A Claude Code already
#      installed, either way, is left as it is. A tool you decline stops the run before anything of
#      the kit is written.
#   2. Clones the kit into --dir (default $CLAUDE_BASE_DIR, else ~/claude-base), or updates a clone of
#      this repository that is already there. A folder there that is not such a clone is refused.
#   3. Runs install.py at user scope. It never overwrites a file of yours: the kit version lands beside
#      it as <name>.new. It asks here whether to add Herdr, and Enter is yes.
#   4. Runs scripts/doctor.py and says what is left to do by hand (signing in to Claude Code).
#
# Questions are read from the terminal (/dev/tty), so they work when this file arrives through a pipe.
# With no terminal and no --yes, every answer is no.
#
# The tests replace the outside world through the environment: CLAUDE_BASE_REPO (the repository to
# clone), CLAUDE_BASE_ANSWER (the answer to every question), CLAUDE_BASE_MISSING (a comma list of
# tools to treat as missing), CLAUDE_BASE_CLAUDE_INSTALLER (a file or URL run instead of the Claude
# Code installer) and CLAUDE_BASE_NPM (an npm instead of the one on PATH, or `none`).
# Written for the bash 3.2 macOS ships: no associative arrays, no ${var,,}, no mapfile.

DIR="${CLAUDE_BASE_DIR:-$HOME/claude-base}"
REPO="${CLAUDE_BASE_REPO:-https://github.com/ElkinDev/claude-base.git}"
HERDR=""
YES=0
DRY_RUN=0
NO_HOOK_MERGE=0
AUTO_UPDATE=0
NO_AUTO_UPDATE=0
PERMISSIONS="bypass"

while [ $# -gt 0 ]; do
    case "$1" in
        --dir|-Dir) DIR="$2"; shift ;;
        --repo|-Repo) REPO="$2"; shift ;;
        --herdr|-Herdr) [ "$HERDR" = "no" ] && { echo "--herdr and --no-herdr cannot both be given."; exit 1; }; HERDR="yes" ;;
        --no-herdr|-NoHerdr) [ "$HERDR" = "yes" ] && { echo "--herdr and --no-herdr cannot both be given."; exit 1; }; HERDR="no" ;;
        --yes|-y|-Yes) YES=1 ;;
        --dry-run|-DryRun) DRY_RUN=1 ;;
        --no-hook-merge|-NoHookMerge) NO_HOOK_MERGE=1 ;;
        --auto-update|-AutoUpdate) AUTO_UPDATE=1 ;;
        --no-auto-update|-NoAutoUpdate) NO_AUTO_UPDATE=1 ;;
        --permissions|-Permissions)
            case "$2" in bypass|ask) PERMISSIONS="$2" ;; *) echo "--permissions takes bypass or ask"; exit 1 ;; esac
            shift ;;
        -h|--help) sed -n '2,32p' "$0" 2>/dev/null || echo "see install/get.sh"; exit 0 ;;
        *) echo "unknown option: $1"; exit 1 ;;
    esac
    shift
done
case "$DIR" in "~"/*) DIR="$HOME/${DIR#\~/}" ;; esac

say() { printf '%s\n' "$*"; }

has_tty() { [ -r /dev/tty ] && ( : < /dev/tty ) 2>/dev/null; }

# Yes on Enter. With no terminal and no --yes the answer is no.
ask_yes() {
    local answer
    if [ "$YES" = 1 ]; then say "$1 yes (--yes)"; return 0; fi
    if [ -n "${CLAUDE_BASE_ANSWER+set}" ]; then
        answer="$CLAUDE_BASE_ANSWER"; say "$1 '$answer' (CLAUDE_BASE_ANSWER)"
    elif has_tty; then
        printf '%s [Y/n] ' "$1" > /dev/tty
        IFS= read -r answer < /dev/tty
    else
        say "$1 no (no terminal to ask; pass --yes)"; return 1
    fi
    case "$answer" in [nN]|[nN][oO]|" "[nN]*) return 1 ;; *) return 0 ;; esac
}

missing() {
    case ",${CLAUDE_BASE_MISSING:-}," in *",$1,"*) return 0 ;; esac
    return 1
}

on_mac() { [ "$(uname -s)" = "Darwin" ]; }

# On macOS /usr/bin/git and /usr/bin/python3 are stubs that open Apple's install dialog when the
# Command Line Tools are absent, so the tools are only called once xcode-select says they are there.
clt_ready() { ! on_mac || xcode-select -p >/dev/null 2>&1; }

have_git() { ! missing git && clt_ready && command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1; }

find_python() {
    missing python && return 1
    clt_ready || return 1
    local candidate
    for candidate in python3 python; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
            command -v "$candidate"; return 0
        fi
    done
    return 1
}

have_claude() {
    missing claude && return 1
    command -v claude >/dev/null 2>&1 && return 0
    # The official installer puts it in ~/.local/bin, which this shell may not have on PATH yet.
    if [ -x "$HOME/.local/bin/claude" ]; then PATH="$PATH:$HOME/.local/bin"; export PATH; return 0; fi
    return 1
}

find_npm() {
    if [ -n "${CLAUDE_BASE_NPM:-}" ]; then [ "$CLAUDE_BASE_NPM" = none ] && return 1; echo "$CLAUDE_BASE_NPM"; return 0; fi
    command -v npm 2>/dev/null
}

install_clt() {
    if ! on_mac; then
        say "  Install $1 with your package manager (for example: sudo apt install git python3) and run this again."
        return 1
    fi
    ask_yes "$1 is missing: it comes with Apple's Command Line Tools. Open their installer (xcode-select --install)?" || return 1
    xcode-select --install >/dev/null 2>&1
    say "  The Command Line Tools installer is open in its own window. When it has finished,"
    say "  run this same command again."
    return 1
}

install_claude() {
    local npm source installer code
    npm="$(find_npm)"
    if [ -n "$npm" ]; then
        ask_yes "Claude Code is missing. Install it with npm (npm install -g @anthropic-ai/claude-code)?" || return 1
        "$npm" install -g @anthropic-ai/claude-code; code=$?
    else
        source="${CLAUDE_BASE_CLAUDE_INSTALLER:-https://claude.ai/install.sh}"
        ask_yes "Claude Code is missing. Install it with its official installer ($source)?" || return 1
        # Saved and run as a file, never piped into a shell: the same rule get.ps1 keeps, and a
        # download cut short then fails here instead of running half a script.
        installer="$(mktemp "${TMPDIR:-/tmp}/claude-base-claude-install.XXXXXX")" || return 1
        if [ -f "$source" ]; then cp "$source" "$installer"
        elif ! curl -fsSL "$source" -o "$installer"; then
            say "  could not download the Claude Code installer from $source"; rm -f "$installer"; return 1
        fi
        bash "$installer"; code=$?
        rm -f "$installer"
    fi
    if [ "$code" -ne 0 ]; then say "  the Claude Code installer exited $code"; return 1; fi
    if have_claude; then say "  ok    Claude Code"; return 0; fi
    say "  Claude Code installed, but this shell cannot see it yet. Open a new terminal and run the same command again."
    return 1
}

main() {
    local python origin args
    say "claude-base: one-command install into $DIR"

    if [ "$NO_AUTO_UPDATE" = 1 ]; then
        if [ ! -f "$DIR/install.py" ]; then
            say "No clone at $DIR, so there is no install.py to remove the daily update with. Pass --dir."
            return 1
        fi
        python="$(find_python)" || { say "Python 3.8+ is required to remove the daily update."; return 1; }
        args=(--no-auto-update); [ "$DRY_RUN" = 1 ] && args+=(--dry-run)
        "$python" "$DIR/install.py" "${args[@]}"
        return $?
    fi

    # git and Python first: on macOS one installer brings both.
    if have_git; then say "  ok    git"
    elif [ "$DRY_RUN" = 1 ]; then say "  would offer to install git (Command Line Tools on macOS)"
    else install_clt "git" || { say "Stopped: git is required. Nothing of the kit was installed."; return 1; }
    fi
    if python="$(find_python)"; then say "  ok    Python 3 ($python)"
    elif [ "$DRY_RUN" = 1 ]; then say "  would offer to install Python 3 (Command Line Tools on macOS)"
    else install_clt "Python 3.8+" || { say "Stopped: Python 3.8+ is required. Nothing of the kit was installed."; return 1; }
    fi
    if have_claude; then say "  ok    Claude Code"
    elif [ "$DRY_RUN" = 1 ]; then say "  would offer to install Claude Code"
    else install_claude || { say "Stopped: Claude Code is required. Nothing of the kit was installed."; return 1; }
    fi

    if [ -d "$DIR/.git" ]; then
        origin="$(git -C "$DIR" remote get-url origin 2>/dev/null)"
        if [ "$origin" != "$REPO" ]; then
            say "Stopped: $DIR is a clone of ${origin:-nothing}, not of $REPO. Pass --dir to use another folder."
            return 1
        fi
        if [ "$DRY_RUN" = 1 ]; then say "would update $DIR (git pull --ff-only)"
        else
            say "updating $DIR"
            git -C "$DIR" pull --ff-only --quiet || {
                say "Stopped: git pull in $DIR failed; it has changes of its own. Resolve them or pass --dir."; return 1; }
        fi
    elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
        say "Stopped: $DIR exists and is not a claude-base clone. Pass --dir to use another folder."
        return 1
    elif [ "$DRY_RUN" = 1 ]; then
        say "would clone $REPO into $DIR, then run install.py"
        return 0
    else
        say "cloning $REPO into $DIR"
        git clone --quiet "$REPO" "$DIR" || { say "Stopped: git clone of $REPO failed."; return 1; }
    fi

    args=(--permissions "$PERMISSIONS")
    [ "$NO_HOOK_MERGE" = 1 ] && args+=(--no-hook-merge)
    [ "$AUTO_UPDATE" = 1 ] && args+=(--auto-update)
    [ "$DRY_RUN" = 1 ] && args+=(--dry-run)
    # The question is asked here, where the terminal is known, and its answer passed on.
    if [ "$HERDR" = no ]; then args+=(--no-herdr)
    elif [ "$HERDR" = yes ]; then args+=(--herdr)
    elif [ "$DRY_RUN" = 1 ]; then :
    elif ask_yes "Add Herdr, the terminal workspace for agents, on its preview channel and set up the way this kit uses it?"; then
        args+=(--herdr)
    else args+=(--no-herdr)
    fi
    python="$(find_python)" || { say "Stopped: Python 3.8+ is required."; return 1; }
    "$python" "$DIR/install.py" "${args[@]}" || { say "Stopped: install.py exited $?."; return 1; }
    [ "$DRY_RUN" = 1 ] && return 0

    say ""
    "$python" "$DIR/scripts/doctor.py"

    say ""
    say "Left to do by hand:"
    say "  1. Open a new terminal and run claude, then sign in (one sign-in per account)."
    say "  2. Scaffold a project: python3 \"$DIR/install.py\" --project <path>"
    say "  3. Several accounts on one machine: cca in a new terminal (cca ? lists the options), $DIR/docs/ACCOUNTS.md"
    return 0
}

main
code=$?
[ "$code" -ne 0 ] && say "claude-base install did not finish (code $code)."
exit "$code"
