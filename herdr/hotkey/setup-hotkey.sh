#!/bin/sh
# setup-hotkey.sh - make Ctrl+Option+Cmd+N open Herdr on macOS, from any app. The twin of setup-hotkey.ps1.
#
#   setup-hotkey.sh [launcher]          install or refresh the key, and start it now and at every sign-in
#   setup-hotkey.sh --remove            stop it and delete what this script made
#   setup-hotkey.sh --build <app> [--release]
#                                       only build the app at <app> (release.sh uses it; --release builds it the way
#                                       a published app has to be: universal, hardened runtime, secure timestamp)
#
# What it makes, all in your user account, with no administrator rights:
#   <kit home>/herdr-hotkey/HerdrHotkey.app   a small accessory app (no Dock icon, no window) that registers the key,
#                                             with launch-herdr.sh inside it
#   ~/Library/LaunchAgents/local.herdr-hotkey.plist   starts it at every sign-in, and again if it crashes
#   <kit home>/logs/herdr-hotkey.log          one line per start and per key press
# The first press may ask whether Herdr Hotkey can control Terminal (or iTerm): allow it once, since that is how the
# window opens.
#
# Where the app comes from. herdr/hotkey/release.txt, written by release.sh, names a build of this same source that the
# kit's maintainer signed with a Developer ID and Apple notarized. When its source matches HerdrHotkey.swift and the
# launcher here, that build is downloaded, checked against the sha256 and the team in release.txt, and installed, so
# Login Items names its developer on any Mac. Otherwise, or when the download or a check fails, the app is compiled
# here with swiftc (Apple's Command Line Tools: xcode-select --install) and signed with the Developer ID of this Mac's
# keychain when it has one, else ad hoc (Login Items then says "unidentified developer"; it works the same).
#
# The app is installed again only when its source or its origin changed, so the Terminal permission is not asked for
# again on every run. KIT_CODESIGN_IDENTITY names another signing identity, or - for ad hoc; KIT_HOTKEY_RELEASE_FILE
# another release record (a missing file means none). KIT_HOME, KIT_LAUNCHAGENTS_DIR, KIT_LAUNCHCTL and KIT_SWIFTC
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
record=$home_dir/installed.txt
label=local.herdr-hotkey
agents=${KIT_LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}
plist=$agents/$label.plist
launchctl=${KIT_LAUNCHCTL:-/bin/launchctl}
domain=gui/$(id -u)
log=$kit_home/logs/herdr-hotkey.log
release_file=${KIT_HOTKEY_RELEASE_FILE:-$here/release.txt}
key="Ctrl+Option+Cmd+N"
min_macos=11.0

if [ "${1:-}" = --remove ]; then
    "$launchctl" bootout "$domain/$label" >/dev/null 2>&1 || true
    rm -f "$plist"
    rm -rf "${home_dir:?}"
    echo "Removed: $key no longer opens Herdr ($plist and $home_dir are gone)."
    exit 0
fi

build_to=
release_build=0
launcher_src=$here/launch-herdr.sh
if [ "${1:-}" = --build ]; then
    build_to=${2:?--build needs the path of the app to make}
    [ "${3:-}" = --release ] && release_build=1
elif [ -n "${1:-}" ]; then
    launcher_src=$1
fi
source_swift=$here/HerdrHotkey.swift
icon=$here/HerdrHotkey.icns
for needed in "$launcher_src" "$source_swift" "$icon"; do
    [ -f "$needed" ] || { echo "setup-hotkey.sh: missing $needed" >&2; exit 1; }
done

# What the app is built from: a release whose source differs from this is a build of other code.
source_stamp=$(cat "$source_swift" "$launcher_src" "$icon" | shasum -a 256 | cut -c1-64)

identity=${KIT_CODESIGN_IDENTITY:-$(security find-identity -v -p codesigning 2>/dev/null \
    | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -n 1)}
[ -n "$identity" ] || identity=-

# XML text for the plists: a path with & or < must not break them.
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
# One value of release.txt, which holds key=value lines.
release_value() { sed -n "s/^$1=//p" "$release_file" 2>/dev/null | head -n 1; }

build_app() {
    # build_app <bundle> <identity> <release 0|1>: compile, assemble and sign one app bundle. A release build is
    # universal (Apple silicon and Intel), carries the hardened runtime and a secure timestamp, and declares that it
    # drives Terminal through Apple events: the three things notarization asks of an app it accepts.
    out=$1; sign_as=$2; release=$3
    if [ -n "${KIT_SWIFTC:-}" ]; then
        set -- "$KIT_SWIFTC"
    elif xcrun --find swiftc >/dev/null 2>&1; then
        # Through xcrun, which also hands the compiler the macOS SDK: swiftc started by its path alone finds no
        # standard library.
        set -- xcrun --sdk macosx swiftc
    else
        echo "setup-hotkey.sh: no swiftc; install Apple's Command Line Tools (xcode-select --install) and run it again." >&2
        return 1
    fi
    work=$(mktemp -d "${TMPDIR:-/tmp}/herdr-hotkey.XXXXXX")
    bundle=$work/HerdrHotkey.app
    mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources"
    if [ "$release" = 1 ]; then arches="arm64 x86_64"; else arches=$(uname -m); fi
    slices=
    for arch in $arches; do
        # Swift 5 mode: the file is top-level code, which Swift 6 strict concurrency would refuse for no gain here.
        if ! "$@" -O -swift-version 5 -target "$arch-apple-macos$min_macos" -o "$work/HerdrHotkey-$arch" \
                "$source_swift" 2>"$work/swiftc.log"; then
            echo "setup-hotkey.sh: the build failed:" >&2
            cat "$work/swiftc.log" >&2
            rm -rf "${work:?}"
            return 1
        fi
        slices="$slices $work/HerdrHotkey-$arch"
    done
    if [ "$release" = 1 ]; then
        # shellcheck disable=SC2086
        lipo -create $slices -output "$bundle/Contents/MacOS/HerdrHotkey"
    else
        mv "$work/HerdrHotkey-$arches" "$bundle/Contents/MacOS/HerdrHotkey"
    fi
    cp "$launcher_src" "$bundle/Contents/Resources/launch-herdr.sh"
    cp "$icon" "$bundle/Contents/Resources/HerdrHotkey.icns"
    cat > "$bundle/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key><string>$label</string>
    <key>CFBundleName</key><string>Herdr Hotkey</string>
    <key>CFBundleExecutable</key><string>HerdrHotkey</string>
    <key>CFBundleIconFile</key><string>HerdrHotkey</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$(printf '%s' "$source_stamp" | cut -c1-12)</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>$min_macos</string>
    <key>LSUIElement</key><true/>
    <key>NSAppleEventsUsageDescription</key><string>Herdr Hotkey opens Herdr in a new Terminal or iTerm window when you press $key.</string>
</dict>
</plist>
EOF
    if [ "$release" = 1 ]; then
        cat > "$work/entitlements.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.automation.apple-events</key><true/>
</dict>
</plist>
EOF
        if ! codesign --force --options runtime --timestamp --entitlements "$work/entitlements.plist" \
                --sign "$sign_as" "$bundle"; then
            echo "setup-hotkey.sh: the release signing with $sign_as failed" >&2
            rm -rf "${work:?}"
            return 1
        fi
    else
        # Signed as a whole bundle, so macOS knows it by one identity when it asks about Terminal. No timestamp:
        # nothing leaves the machine. A Developer ID signing that fails (a keychain prompt refused) falls back to ad hoc.
        codesign --force --sign "$sign_as" --timestamp=none "$bundle" >/dev/null 2>&1 \
            || codesign --force --sign - "$bundle" >/dev/null 2>&1 || true
    fi
    rm -rf "${out:?}"
    mkdir -p "$(dirname "$out")"
    mv "$bundle" "$out"
    rm -rf "${work:?}"
}

fetch_release() {
    # fetch_release <bundle>: the published build, checked before it is trusted. Prints why when it is not used.
    out=$1
    url=$(release_value url); sum=$(release_value sha256); team=$(release_value team)
    [ -n "$url" ] && [ -n "$sum" ] || { echo "release.txt names no url and sha256"; return 1; }
    work=$(mktemp -d "${TMPDIR:-/tmp}/herdr-hotkey.XXXXXX")
    if ! curl -fsSL --max-time 120 "$url" -o "$work/HerdrHotkey.zip" 2>/dev/null; then
        rm -rf "${work:?}"; echo "the download of $url failed"; return 1
    fi
    got=$(shasum -a 256 "$work/HerdrHotkey.zip" | cut -c1-64)
    if [ "$got" != "$sum" ]; then
        rm -rf "${work:?}"; echo "the download is not the published build (sha256 $got)"; return 1
    fi
    ditto -x -k "$work/HerdrHotkey.zip" "$work/unpacked" 2>/dev/null \
        && [ -x "$work/unpacked/HerdrHotkey.app/Contents/MacOS/HerdrHotkey" ] \
        || { rm -rf "${work:?}"; echo "the download holds no app"; return 1; }
    if ! codesign --verify --deep --strict "$work/unpacked/HerdrHotkey.app" >/dev/null 2>&1; then
        rm -rf "${work:?}"; echo "the signature of the download does not verify"; return 1
    fi
    if [ -n "$team" ]; then
        signed_by=$(codesign -dv "$work/unpacked/HerdrHotkey.app" 2>&1 | sed -n 's/^TeamIdentifier=//p')
        if [ "$signed_by" != "$team" ]; then
            rm -rf "${work:?}"; echo "the download is signed by team ${signed_by:-none}, not $team"; return 1
        fi
    fi
    rm -rf "${out:?}"
    mkdir -p "$(dirname "$out")"
    mv "$work/unpacked/HerdrHotkey.app" "$out"
    rm -rf "${work:?}"
}

if [ -n "$build_to" ]; then
    build_app "$build_to" "$identity" "$release_build"
    exit $?
fi

# The origin this run wants: the published build when it is of this source, else a build of this Mac's own.
release_source=$(release_value source)
if [ -n "$release_source" ] && [ "$release_source" = "$source_stamp" ]; then
    want_origin="release $(release_value sha256)"
else
    want_origin="local $identity"
fi
have_record=$(cat "$record" 2>/dev/null || true)
want_record=$(printf 'source=%s\norigin=%s' "$source_stamp" "$want_origin")
installed=0
if [ "$have_record" != "$want_record" ] || [ ! -x "$app/Contents/MacOS/HerdrHotkey" ]; then
    origin=$want_origin
    case $want_origin in
        release*)
            if why=$(fetch_release "$app"); then
                echo "Installed the signed, notarized build named in release.txt."
            else
                echo "The published build could not be used ($why); building it here instead."
                origin="local $identity"
                build_app "$app" "$identity" 0
            fi ;;
        *) build_app "$app" "$identity" 0 ;;
    esac
    printf 'source=%s\norigin=%s\n' "$source_stamp" "$origin" > "$record"
    installed=1
    # Known to Launch Services, so Login Items can show the app by its name and icon instead of its developer's name.
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister" \
        -f "$app" >/dev/null 2>&1 || true
fi

mkdir -p "$agents" "$(dirname "$log")"
want=$(cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$label</string>
    <key>AssociatedBundleIdentifiers</key><array><string>$label</string></array>
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
if [ "$want" != "$have" ] || [ "$installed" = 1 ] || [ "$loaded" = 0 ]; then
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
