#!/bin/sh
# launch-herdr.sh - open herdr in a new terminal window on macOS: an iTerm2 window when iTerm2 is running, else a
# Terminal window. The macOS twin of launch-herdr.cmd.
# Usage: launch-herdr.sh [working-directory]
#   No arg opens in your home folder. Change the default below to your main repo, or pass a folder.
# The global Ctrl+Option+N key that runs it is one step by hand in the Shortcuts app: herdr/README.md, section macOS.
#
# The command goes to the window's own login shell, so herdr is found with the PATH your shell profile sets; it is
# named by its full path when this script can find it, since a Shortcuts run starts with a bare system PATH.
# KIT_HERDR_TERMINAL (Terminal or iTerm) picks the app instead of looking for a running iTerm2, and KIT_OSASCRIPT
# names the osascript to call instead of /usr/bin/osascript; the tests use both.
set -eu

target=${1:-$HOME}

if [ "$(uname -s)" != Darwin ]; then
    echo "launch-herdr.sh: macOS only. On Linux, bind herdr to a key in your desktop's keyboard settings." >&2
    exit 1
fi
if ! dir=$(cd "$target" 2>/dev/null && pwd); then
    echo "launch-herdr.sh: no such folder: $target" >&2
    exit 1
fi

herdr_cmd=herdr
for candidate in "$(command -v herdr 2>/dev/null || true)" "$HOME/.local/bin/herdr" /opt/homebrew/bin/herdr \
        /usr/local/bin/herdr; do
    if [ -n "$candidate" ] && [ -f "$candidate" ] && [ -x "$candidate" ]; then
        herdr_cmd=$candidate
        break
    fi
done

# Single quotes for the window's shell, each quote inside written as '\''.
quote() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}
command="cd $(quote "$dir") && $(quote "$herdr_cmd")"

app=${KIT_HERDR_TERMINAL:-}
if [ -z "$app" ]; then
    if pgrep -x iTerm2 >/dev/null 2>&1; then app=iTerm; else app=Terminal; fi
fi
osascript=${KIT_OSASCRIPT:-/usr/bin/osascript}

# Each app gets a script of its own: osascript compiles the whole script, and naming an app that is not installed
# opens a "Where is iTerm?" dialog.
case $app in
    iTerm|iTerm2)
        exec "$osascript" - "$command" <<'EOF'
on run argv
    tell application "iTerm"
        activate
        set newWindow to (create window with default profile)
        tell current session of newWindow to write text (item 1 of argv)
    end tell
end run
EOF
        ;;
    Terminal)
        # A Terminal that is not running opens a window of its own as it starts; the command goes there, so one
        # window opens, not two.
        exec "$osascript" - "$command" <<'EOF'
on run argv
    set theCommand to item 1 of argv
    if application "Terminal" is running then
        tell application "Terminal" to set theTab to do script theCommand
    else
        tell application "Terminal"
            activate
            try
                set theTab to do script theCommand in window 1
            on error
                set theTab to do script theCommand
            end try
        end tell
    end if
    tell application "Terminal"
        set custom title of theTab to "herdr"
        activate
    end tell
end run
EOF
        ;;
    *)
        echo "launch-herdr.sh: KIT_HERDR_TERMINAL is '$app'; use Terminal or iTerm." >&2
        exit 1
        ;;
esac
