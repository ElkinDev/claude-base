#!/bin/sh
# setup-hotkey.sh - make Ctrl+Option+Cmd+N open Herdr on macOS, from any app. The twin of setup-hotkey.ps1.
#
#   setup-hotkey.sh [launcher]   build or refresh the key, and start it now and at every sign-in
#   setup-hotkey.sh --remove     stop it and delete what this script made
#
# What it makes, all in your user account, with no administrator rights:
#   <kit home>/herdr-hotkey/HerdrHotkey.app   a background app compiled here from HerdrHotkey.swift (no Dock icon,
#                                             no window), with launch-herdr.sh copied inside it
#   ~/Library/LaunchAgents/local.herdr-hotkey.plist   starts it at every sign-in, and again if it crashes
#   <kit home>/logs/herdr-hotkey.log          one line per start and per key press
# It needs swiftc, which Apple's Command Line Tools bring (xcode-select --install). The first press may ask whether
# Herdr Hotkey can control Terminal (or iTerm): allow it once, since that is how the window opens.
#
# The app is rebuilt only when HerdrHotkey.swift or the launcher changed: each build has a new signature, and macOS
# asks for the Terminal permission again after one. KIT_HOME, KIT_LAUNCHAGENTS_DIR, KIT_LAUNCHCTL and KIT_SWIFTC
# point a test somewhere else.
set -eu

if [ "$(uname -s)" != Darwin ]; then
    echo "setup-hotkey.sh: macOS only. On Linux, bind herdr to a key in your desktop's keyboard settings." >&2
    exit 1
fi

here=$(cd "$(dirname "$0")" && pwd -P)
kit_home=${KIT_HOME:-$HOME/.claude}
home_dir=$kit_home/herdr-hotkey
app=$home_dir/HerdrHotkey.app
label=local.herdr-hotkey
agents=${KIT_LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}
plist=$agents/$label.plist
launchctl=${KIT_LAUNCHCTL:-/bin/launchctl}
domain=gui/$(id -u)
log=$kit_home/logs/herdr-hotkey.log
key="Ctrl+Option+Cmd+N"

if [ "${1:-}" = --remove ]; then
    "$launchctl" bootout "$domain/$label" >/dev/null 2>&1 || true
    rm -f "$plist"
    rm -rf "${home_dir:?}"
    echo "Removed: $key no longer opens Herdr ($plist and $home_dir are gone)."
    exit 0
fi

launcher_src=${1:-$here/launch-herdr.sh}
source_swift=$here/HerdrHotkey.swift
for needed in "$launcher_src" "$source_swift"; do
    [ -f "$needed" ] || { echo "setup-hotkey.sh: missing $needed" >&2; exit 1; }
done
# Through xcrun, which also hands the compiler the macOS SDK: swiftc started by its path alone finds no standard
# library. KIT_SWIFTC replaces the whole compiler command for the tests.
if [ -n "${KIT_SWIFTC:-}" ]; then
    set -- "$KIT_SWIFTC"
elif xcrun --find swiftc >/dev/null 2>&1; then
    set -- xcrun --sdk macosx swiftc
else
    echo "setup-hotkey.sh: no swiftc; install Apple's Command Line Tools (xcode-select --install) and run it again." >&2
    exit 1
fi

# XML text for the two plists: a path with & or < must not break them.
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

stamp=$(cat "$source_swift" "$launcher_src" | shasum -a 256 | cut -c1-64)
built=$(cat "$app/Contents/Resources/source.sha256" 2>/dev/null || true)
rebuilt=0
if [ "$stamp" != "$built" ] || [ ! -x "$app/Contents/MacOS/HerdrHotkey" ]; then
    build=$(mktemp -d "${TMPDIR:-/tmp}/herdr-hotkey.XXXXXX")
    bundle=$build/HerdrHotkey.app
    mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources"
    # Swift 5 mode: the file is top-level code, which Swift 6 strict concurrency would refuse for no gain here.
    if ! "$@" -O -swift-version 5 -o "$bundle/Contents/MacOS/HerdrHotkey" "$source_swift" 2>"$build/swiftc.log"; then
        echo "setup-hotkey.sh: the build failed:" >&2
        cat "$build/swiftc.log" >&2
        rm -rf "${build:?}"
        exit 1
    fi
    cp "$launcher_src" "$bundle/Contents/Resources/launch-herdr.sh"
    printf '%s\n' "$stamp" > "$bundle/Contents/Resources/source.sha256"
    cat > "$bundle/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key><string>$label</string>
    <key>CFBundleName</key><string>Herdr Hotkey</string>
    <key>CFBundleExecutable</key><string>HerdrHotkey</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSUIElement</key><true/>
    <key>NSAppleEventsUsageDescription</key><string>Herdr Hotkey opens Herdr in a new Terminal or iTerm window when you press $key.</string>
</dict>
</plist>
EOF
    # Signed as a whole bundle (ad hoc, nothing leaves the machine), so macOS knows it by one identity when it asks
    # about Terminal.
    codesign --force --sign - "$bundle" >/dev/null 2>&1 || true
    mkdir -p "$home_dir"
    rm -rf "${app:?}"
    mv "$bundle" "$app"
    rm -rf "${build:?}"
    rebuilt=1
fi

mkdir -p "$agents" "$(dirname "$log")"
want=$(cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$label</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(xml "$app/Contents/MacOS/HerdrHotkey")</string>
        <string>$(xml "$app/Contents/Resources/launch-herdr.sh")</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><dict><key>Crashed</key><true/></dict>
    <key>LimitLoadToSessionType</key><string>Aqua</string>
    <key>ProcessType</key><string>Interactive</string>
    <key>StandardOutPath</key><string>$(xml "$log")</string>
    <key>StandardErrorPath</key><string>$(xml "$log")</string>
</dict>
</plist>
EOF
)
have=$(cat "$plist" 2>/dev/null || true)
loaded=0
"$launchctl" print "$domain/$label" >/dev/null 2>&1 && loaded=1
echo "Log: $log. Remove it with: sh \"$here/setup-hotkey.sh\" --remove"
# The status line goes last: the installer shows the last line of this step.
if [ "$want" != "$have" ] || [ "$rebuilt" = 1 ] || [ "$loaded" = 0 ]; then
    "$launchctl" bootout "$domain/$label" >/dev/null 2>&1 || true
    # bootout returns before the job is gone, and a bootstrap that lands while it is still going fails with exit 5
    # (Input/output error). Wait for it to go, then load, with one more try after a pause.
    tries=0
    while "$launchctl" print "$domain/$label" >/dev/null 2>&1 && [ "$tries" -lt 50 ]; do
        sleep 0.1
        tries=$((tries + 1))
    done
    printf '%s\n' "$want" > "$plist"
    if ! "$launchctl" bootstrap "$domain" "$plist" 2>/dev/null; then
        sleep 1
        "$launchctl" bootstrap "$domain" "$plist"
    fi
    echo "Created: $key opens Herdr from any app ($app, started now and at every sign-in)."
else
    echo "Up to date: $key opens Herdr ($app)."
fi
