"""Merge one settings.json into another without dropping what the destination has.

Used by claude-account.ps1 when it brings settings from the default Claude Code config
directory into an account profile.

Replacing the file wholesale drops keys Claude Code writes on its own, such as
skipDangerousModePermissionPrompt, and then it asks you to confirm bypass mode on every
start. The same class of bug bites .claude.json harder: rewriting that one from scratch
drops oauthAccount and signs the account out.

Rules:
  1. If the destination exists and does NOT parse, nothing is touched. Exit 1.
  2. On a conflict the source wins, because this only runs when a sync was requested.
  3. Keys that exist only in the destination are always kept.
  4. Dictionaries merge deeply. Lists are replaced whole, because half a hooks list is
     not a valid configuration.
  5. Output is UTF-8 without BOM, written atomically, and only when something changed.
     PowerShell 5.1 writes a BOM with Set-Content -Encoding utf8, and Node's JSON.parse
     rejects a leading BOM, which is why this is not done in PowerShell.

Usage:  python merge-settings.py <source.json> <destination.json>
Exit:   0 when merged or already up to date, 1 when nothing could be done safely.
"""
import io
import json
import os
import sys


def read_json(path, required):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        if required:
            print("NOTHING TOUCHED: missing or empty {}".format(path))
            return None, False
        return {}, True
    try:
        with io.open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception as e:
        print("NOTHING TOUCHED: {} does not parse ({}: {})".format(
            os.path.basename(path), type(e).__name__, e))
        return None, False
    if not isinstance(data, dict):
        print("NOTHING TOUCHED: {} is not a JSON object".format(os.path.basename(path)))
        return None, False
    return data, True


def merge(source, dest):
    """Return (result, changes). Neither argument is mutated."""
    out = dict(dest)
    changes = 0
    for key, value in source.items():
        current = out.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            sub, n = merge(value, current)
            if n:
                out[key] = sub
                changes += n
        elif key not in out or current != value:
            out[key] = value
            changes += 1
    return out, changes


def main():
    if len(sys.argv) < 3:
        print("usage: merge-settings.py <source.json> <destination.json>")
        return 1
    source_path, dest_path = sys.argv[1], sys.argv[2]

    source, ok = read_json(source_path, True)
    if not ok:
        return 1
    dest, ok = read_json(dest_path, False)
    if not ok:
        return 1

    result, changes = merge(source, dest)
    dest_only = [k for k in dest if k not in source]

    if not changes:
        print("settings: already up to date, {} keys".format(len(result)))
        return 0

    tmp = dest_path + ".tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, dest_path)
    except OSError as e:
        print("could not write: {}".format(e))
        try:
            os.remove(tmp)
        except OSError:
            pass
        return 1

    print("settings: {} changes, {} profile-owned keys kept{}".format(
        changes, len(dest_only),
        " (" + ", ".join(sorted(dest_only)) + ")" if dest_only else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
