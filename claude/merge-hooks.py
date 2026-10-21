"""Add the kit's new hooks to a settings.json the user has made their own, and nothing else.

install.ps1 never overwrites a settings.json that differs from what it wrote: the kit version goes
beside it as settings.json.new. Hooks are the part of that file the kit keeps adding to, so without
this every update meant opening the .new and copying a block by hand. This does that one job.

Rules:
  1. Only additions. A kit hook handler is added when its command appears nowhere in the user's
     hooks; it goes under the same event and matcher, appended to the user's entry with that
     matcher or as a new entry. Nothing of the user's is removed, reordered or changed.
  2. Only into a file that already carries at least one of the kit's hook commands, which is the
     sign the user took the kit's hooks. A file with none of them is left alone, with one line
     saying so: its hooks are the user's to merge by hand from the .new.
  3. A command the user has taken out on purpose stays out when its script name is a line of
     hook-merge-skip.txt beside the settings file (one name per line, # for comments).
  4. Commands are compared after folding backslashes to slashes, case and runs of spaces, so the
     same handler written with either slash is one handler.
  5. A file that does not parse, or whose top level or hooks block is not an object, is never
     touched: one line, exit 1.
  6. The write is in place (the same file opened for update, then truncated), never a rename: an
     in-place write is what reaches Claude Code sessions already running. UTF-8 without BOM, the
     file's own line ending, its own indent width, its trailing newline if it had one.

Usage:
  python merge-hooks.py (--kit <settings.json> | --kit-b64 <base64 of its UTF-8 text>) <user settings.json> [--dry-run]

One line per handler added (`added`, or `would add` under --dry-run), or one line saying why
nothing was added. Exit 0 when merged, up to date or left alone; 1 when nothing could be done
safely; 2 on a bad argument.
"""
import argparse
import base64
import copy
import json
import os
import re
import sys

SKIP_FILE = "hook-merge-skip.txt"


def norm(command):
    text = str(command or "").replace("\\", "/").lower()
    text = re.sub(r"/+", "/", text)
    return re.sub(r"\s+", " ", text).strip()


def matcher_of(entry):
    return entry.get("matcher") or ""


def commands(hooks):
    """Every normalised command in a hooks block, whatever event or matcher it sits under."""
    found = set()
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                continue
            for handler in entry["hooks"]:
                if isinstance(handler, dict) and handler.get("command"):
                    found.add(norm(handler["command"]))
    return found


def skip_names(settings_path):
    path = os.path.join(os.path.dirname(os.path.abspath(settings_path)), SKIP_FILE)
    try:
        with open(path, encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    return [norm(line) for line in lines if line.strip() and not line.strip().startswith("#")]


def indent_of(text):
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" ")
        if stripped and len(stripped) < len(line):
            return len(line) - len(stripped)
    return 2


def plan(kit, user, skips):
    """The handlers to add, as (event, matcher, handler, entry_had_matcher), and the skipped ones."""
    have = commands(user.get("hooks") or {})
    adds, skipped = [], []
    for event, entries in (kit.get("hooks") or {}).items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                continue
            for handler in entry["hooks"]:
                if not isinstance(handler, dict) or not handler.get("command"):
                    continue
                key = norm(handler["command"])
                if key in have:
                    continue
                if any(name and name in key for name in skips):
                    skipped.append((event, matcher_of(entry), handler))
                    continue
                adds.append((event, matcher_of(entry), handler, "matcher" in entry))
    return adds, skipped


def apply(user, adds):
    hooks = user.setdefault("hooks", {})
    for event, matcher, handler, had_matcher in adds:
        entries = hooks.setdefault(event, [])
        target = next((e for e in entries if isinstance(e, dict) and isinstance(e.get("hooks"), list)
                       and matcher_of(e) == matcher), None)
        if target is None:
            target = {"matcher": matcher, "hooks": []} if had_matcher else {"hooks": []}
            entries.append(target)
        target["hooks"].append(copy.deepcopy(handler))


def label(event, matcher, handler):
    return "%s[%s] %s" % (event, matcher or "*", handler.get("command"))


def read_kit(args):
    if args.kit_b64 is not None:
        return base64.b64decode(args.kit_b64).decode("utf-8-sig")
    with open(args.kit, encoding="utf-8-sig") as handle:
        return handle.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Add the kit's new hooks to a settings.json of the user's.")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--kit", help="the kit's rendered settings.json")
    source.add_argument("--kit-b64", help="the same text, UTF-8, base64")
    ap.add_argument("settings", help="the user's settings.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out = sys.stdout

    try:
        kit = json.loads(read_kit(args))
    except Exception as exc:
        out.write("NOTHING TOUCHED: the kit settings do not parse (%s)\n" % type(exc).__name__)
        return 1
    try:
        with open(args.settings, "rb") as handle:
            raw = handle.read()
        text = raw.decode("utf-8-sig")
        user = json.loads(text)
    except Exception as exc:
        out.write("NOTHING TOUCHED: %s cannot be read or does not parse (%s)\n" % (args.settings, type(exc).__name__))
        return 1
    if not isinstance(user, dict) or not isinstance(kit, dict) \
            or not isinstance(user.get("hooks", {}), dict) or not isinstance(kit.get("hooks", {}), dict):
        out.write("NOTHING TOUCHED: %s has no object at its top level or in its hooks block\n" % args.settings)
        return 1

    if not (commands(user.get("hooks") or {}) & commands(kit.get("hooks") or {})):
        out.write("left alone   %s (it carries none of the kit's hook commands, so its hooks are yours; "
                  "take any you want from %s.new by hand)\n" % (args.settings, args.settings))
        return 0
    adds, skipped = plan(kit, user, skip_names(args.settings))
    for event, matcher, handler in skipped:
        out.write("skipped      %s (named in %s)\n" % (label(event, matcher, handler), SKIP_FILE))
    if not adds:
        out.write("up to date   %s (every kit hook command is already there)\n" % args.settings)
        return 0
    for event, matcher, handler, _ in adds:
        out.write("%s %s\n" % ("would add   " if args.dry_run else "added       ", label(event, matcher, handler)))
    if args.dry_run:
        return 0

    apply(user, adds)
    body = json.dumps(user, indent=indent_of(text), ensure_ascii=False)
    if text.endswith("\n"):
        body += "\n"
    eol = "\r\n" if b"\r\n" in raw else "\n"
    data = body.replace("\n", eol).encode("utf-8")
    try:
        with open(args.settings, "r+b") as handle:
            handle.seek(0)
            handle.write(data)
            handle.truncate()
    except OSError as exc:
        out.write("NOTHING TOUCHED: %s cannot be written (%s)\n" % (args.settings, type(exc).__name__))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
