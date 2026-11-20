#!/bin/sh
# release.sh - publish the Herdr key app that other Macs install, so Login Items names its developer there too.
#
#   sh herdr/hotkey/release.sh               build, sign, notarize, publish, and write release.txt
#   sh herdr/hotkey/release.sh --no-publish  build and sign only, to check the signing; nothing leaves the Mac
#
# What a release is: HerdrHotkey.app built universal (Apple silicon and Intel) by setup-hotkey.sh --build --release,
# signed with the maintainer's Developer ID with the hardened runtime and a secure timestamp, notarized by Apple and
# stapled, zipped, uploaded as the asset of a GitHub release tagged herdr-hotkey-<source>, and named in
# herdr/hotkey/release.txt by its source, URL, sha256 and team. setup-hotkey.sh installs that build on any Mac whose
# clone has the same source, checking the sha256 and the team first, and builds its own otherwise. Run it after a
# change to HerdrHotkey.swift, launch-herdr.sh or HerdrHotkey.icns, then commit and push release.txt.
#
# It needs, on the maintainer's Mac only:
#   - a "Developer ID Application" identity in the keychain (KIT_CODESIGN_IDENTITY names another);
#   - notary credentials stored once, which prompts for an app-specific password from appleid.apple.com:
#       xcrun notarytool store-credentials notary --apple-id <apple id> --team-id <team id>
#     (NOTARY_PROFILE names another profile);
#   - gh signed in, and the current commit pushed, since the release tag points at it.
set -eu

here=$(cd "$(dirname "$0")" && pwd -P)
root=$(cd "$here/../.." && pwd -P)
profile=${NOTARY_PROFILE:-notary}
publish=1
[ "${1:-}" = --no-publish ] && publish=0

identity=${KIT_CODESIGN_IDENTITY:-$(security find-identity -v -p codesigning 2>/dev/null \
    | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -n 1)}
case $identity in
    "Developer ID Application:"*) ;;
    *) echo "release.sh: no Developer ID Application identity in the keychain; a release has to carry one." >&2
       exit 1 ;;
esac

work=$(mktemp -d "${TMPDIR:-/tmp}/herdr-hotkey-release.XXXXXX")
app=$work/HerdrHotkey.app
echo "Building and signing with $identity"
KIT_CODESIGN_IDENTITY=$identity sh "$here/setup-hotkey.sh" --build "$app" --release
codesign --verify --deep --strict "$app"
team=$(codesign -dv "$app" 2>&1 | sed -n 's/^TeamIdentifier=//p')
flags=$(codesign -dv "$app" 2>&1 | sed -n 's/^CodeDirectory.*flags=\([^ ]*\).*/\1/p')
echo "Signed: team $team, flags $flags, $(lipo -archs "$app/Contents/MacOS/HerdrHotkey")"

if [ "$publish" = 0 ]; then
    echo "Not published (--no-publish). The app is at $app"
    exit 0
fi

source_stamp=$(cat "$here/HerdrHotkey.swift" "$here/launch-herdr.sh" "$here/HerdrHotkey.icns" | shasum -a 256 | cut -c1-64)
short=$(printf '%s' "$source_stamp" | cut -c1-12)
tag=herdr-hotkey-$short
commit=$(git -C "$root" rev-parse HEAD)
if [ -z "$(git -C "$root" branch -r --contains "$commit" 2>/dev/null)" ]; then
    echo "release.sh: push $commit first; the release tag points at it." >&2
    exit 1
fi
repo=$(cd "$root" && gh repo view --json nameWithOwner -q .nameWithOwner)

echo "Notarizing with the keychain profile '$profile' (a few minutes)"
ditto -c -k --keepParent "$app" "$work/submit.zip"
if ! xcrun notarytool submit "$work/submit.zip" --keychain-profile "$profile" --wait > "$work/notary.log" 2>&1 \
        || ! grep -q "status: Accepted" "$work/notary.log"; then
    cat "$work/notary.log" >&2
    echo "release.sh: Apple did not accept the app; the log above says why (xcrun notarytool log <id> has the detail)." >&2
    exit 1
fi
xcrun stapler staple "$app" >/dev/null
xcrun stapler validate "$app" >/dev/null

zip=$work/HerdrHotkey.zip
ditto -c -k --keepParent "$app" "$zip"
sum=$(shasum -a 256 "$zip" | cut -c1-64)
notes="Ctrl+Option+Cmd+N opens Herdr on macOS. Built from source $short, signed with a Developer ID and notarized by Apple. install.py --herdr installs it; there is no need to download it by hand."
if (cd "$root" && gh release view "$tag" >/dev/null 2>&1); then
    (cd "$root" && gh release upload "$tag" "$zip" --clobber)
else
    (cd "$root" && gh release create "$tag" "$zip" --target "$commit" --latest=false \
        --title "Herdr hotkey for macOS ($short)" --notes "$notes")
fi

cat > "$here/release.txt" <<EOF
# Written by herdr/hotkey/release.sh. setup-hotkey.sh installs this build instead of compiling one when its source
# matches HerdrHotkey.swift, launch-herdr.sh and HerdrHotkey.icns, after checking the sha256 and the team.
source=$source_stamp
url=https://github.com/$repo/releases/download/$tag/HerdrHotkey.zip
sha256=$sum
team=$team
EOF
rm -rf "${work:?}"
echo "Published $tag. Now commit and push herdr/hotkey/release.txt, so installs pick it up."
