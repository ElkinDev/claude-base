"""Add the kit's new hooks to a settings.json the user has made their own, and nothing else.

install.ps1 never overwrites a settings.json that differs from what it wrote: the kit version goes
beside it as settings.json.new. Hooks are the part of that file the kit keeps adding to, so without
this every update meant opening the .new and copying a block by hand. This does that one job.

Rules:
  1. A handler is known by the script it runs: the last path-like token of its command that ends
     in .py, .ps1, .sh or .cmd, by its file name, case folded (a command with no such token is
     known by the whole command). So `py -3 ".../landing.py"` and `python ".../landing.py"` are one
     handler, and so are a bare `powershell -File ".../b.ps1"` and the full-path form a newer kit
     renders: a command whose form changed is never wired twice.
  2. Only additions. A kit handler is added, under its event and matcher, when
       a. its (event, matcher, script) is new since the previous kit render, when that render is
          given (--previous: the settings.json.new on disk before this run refreshed it), and
       b. the user's file runs no handler of that script under that event.
     With no previous render, a handler is added only when the user's file runs its script
     nowhere. A hook the user moved or deleted therefore stays as they left it whenever the
     previous render can tell. Nothing of the user's is removed, reordered or changed.
  3. Only into a file that already runs at least one of the kit's scripts, which is the sign the
     user took the kit's hooks. A file with none of them is left alone, with one line saying so.
  4. A script the user took out on purpose stays out when its name is a line of
     hook-merge-skip.txt beside the settings file (one name per line, # for comments).
  5. The file is written only when re-serialising it as parsed, with its own indent of spaces,
     gives back its own bytes (line endings and a BOM aside) and it holds no duplicate key: then
     the write changes nothing but the added handlers. Any other layout (tabs, entries kept on one
     line, a compact file, a key written twice) is left alone, with one line naming the .new.
  6. A file that does not parse, or whose hooks are not an object of lists of entries that each
     carry a hooks list of objects, is never touched: one line, exit 1, in a dry run too. A file
     that is a link is left alone: a rollback would replace the link with a plain file.
  7. The write is in place: the same file opened for update, then truncated, UTF-8 without BOM,
     the file's own line ending.

Usage:
  python merge-hooks.py (--kit <file> | --kit-b64 <base64>) [--previous <file> | --previous-b64 <base64>]
                        <user settings.json> [--dry-run]

One line per handler added (`added`, or `would add` under --dry-run), or one line saying why
nothing was added. A dry run that would write also prints `after-sha256 <hex>`, the hash the file
will have, which the installer records so kit-restore.py can tell whether the file changed since.
Exit 0 when merged, up to date or left alone; 1 when nothing could be done safely; 2 on a bad
argument.
"""
import argparse
import base64
import copy
import hashlib
import json
import os
import re
import stat
import sys

SKIP_FILE = "hook-merge-skip.txt"
SCRIPT_EXTENSIONS = (".py", ".ps1", ".sh", ".cmd")
TOKEN = re.compile(r'"([^"]*)"|\'([^\']*)\'|(\S+)')
REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: a symlink or a junction on Windows


def norm(command):
    text = str(command or "").replace("\\", "/").lower()
    text = re.sub(r"/+", "/", text)
    return re.sub(r"\s+", " ", text).strip()


def script_of(command):
    """The file name of the script a command runs, case folded, or the whole command when none."""
    last = None
    for match in TOKEN.finditer(str(command or "")):
        token = next(group for group in match.groups() if group is not None).strip()
        if token.lower().endswith(SCRIPT_EXTENSIONS):
            last = token
    if last is None:
        return "command:" + norm(command)
    return re.split(r"[\\/]", last)[-1].lower()


def matcher_of(entry):
    return str(entry.get("matcher") or "")


def shape_error(hooks):
    """Why a hooks block is not the shape Claude Code reads, or None."""
    if not isinstance(hooks, dict):
        return "its hooks block is not an object"
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            return "its %s hooks are not a list" % event
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                return "an entry under %s has no hooks list" % event
            if any(not isinstance(handler, dict) for handler in entry["hooks"]):
                return "a handler under %s is not an object" % event
    return None


def handlers(hooks):
    """(event, matcher, script, handler, entry_has_matcher) for every handler with a command."""
    for event, entries in hooks.items():
        for entry in entries:
            for handler in entry["hooks"]:
                if handler.get("command"):
                    yield event, matcher_of(entry), script_of(handler["command"]), handler, "matcher" in entry


def skip_names(settings_path):
    path = os.path.join(os.path.dirname(os.path.abspath(settings_path)), SKIP_FILE)
    try:
        with open(path, encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return set()
    return set(line.strip().lower() for line in lines if line.strip() and not line.strip().startswith("#"))


def plan(kit_hooks, user_hooks, previous_hooks, skips):
    """The handlers to add and the ones the skip file keeps out, by rule 2."""
    mine = list(handlers(user_hooks))
    runs_by_event = set((event, script) for event, _, script, _, _ in mine)
    runs_anywhere = set(script for _, _, script, _, _ in mine)
    before = None
    if previous_hooks is not None:
        before = set((event, matcher, script) for event, matcher, script, _, _ in handlers(previous_hooks))
    adds, skipped, seen = [], [], set()
    for event, matcher, script, handler, has_matcher in handlers(kit_hooks):
        key = (event, matcher, script)
        if key in seen:
            continue
        seen.add(key)
        if before is not None:
            if key in before or (event, script) in runs_by_event:
                continue
        elif script in runs_anywhere:
            continue
        if script in skips:
            skipped.append((event, matcher, handler))
            continue
        adds.append((event, matcher, handler, has_matcher))
    return adds, skipped


def apply(user, adds):
    hooks = user.setdefault("hooks", {})
    for event, matcher, handler, had_matcher in adds:
        entries = hooks.setdefault(event, [])
        target = next((entry for entry in entries if matcher_of(entry) == matcher), None)
        if target is None:
            target = {"matcher": matcher, "hooks": []} if had_matcher else {"hooks": []}
            entries.append(target)
        target["hooks"].append(copy.deepcopy(handler))


def label(event, matcher, handler):
    return "%s[%s] %s" % (event, matcher or "*", handler.get("command"))


def loads_strict(text):
    """Parse JSON and say which keys were written twice; a plain load would keep only the last."""
    twice = []

    def pairs(items):
        found = {}
        for key, value in items:
            if key in found:
                twice.append(key)
            found[key] = value
        return found

    return json.loads(text, object_pairs_hook=pairs), twice


def indent_of(body):
    """The width of the first indented line when it is made of spaces; None for a one-line file."""
    for line in body.split("\n")[1:]:
        stripped = line.lstrip(" \t")
        if stripped and len(stripped) < len(line):
            lead = line[:len(line) - len(stripped)]
            return len(lead) if set(lead) == {" "} else "tabs"
    return None


def layout(body, data):
    """How to write `data` so the file changes only where data changed, or None when no such way."""
    width = indent_of(body)
    if width == "tabs":
        return None
    tail = "\n" if body.endswith("\n") else ""
    for ascii_only in (False, True):
        if json.dumps(data, indent=width, ensure_ascii=ascii_only) + tail == body:
            return width, ascii_only, tail
    return None


def is_link(path):
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT)


def read_source(path, b64):
    if b64 is not None:
        return base64.b64decode(b64).decode("utf-8-sig")
    with open(path, encoding="utf-8-sig") as handle:
        return handle.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Add the kit's new hooks to a settings.json of the user's.")
    kit_source = ap.add_mutually_exclusive_group(required=True)
    kit_source.add_argument("--kit", help="the kit's rendered settings.json")
    kit_source.add_argument("--kit-b64", help="the same text, UTF-8, base64")
    previous_source = ap.add_mutually_exclusive_group()
    previous_source.add_argument("--previous", help="the previous kit render (the .new before this run)")
    previous_source.add_argument("--previous-b64", help="the same text, UTF-8, base64")
    ap.add_argument("settings", help="the user's settings.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out = sys.stdout
    path = args.settings
    by_hand = "take new hooks from %s.new by hand" % path

    if is_link(path):
        out.write("left alone   %s (it is a link; a rollback would replace it with a plain file; %s)\n" % (path, by_hand))
        return 0
    try:
        kit = json.loads(read_source(args.kit, args.kit_b64))
        if not isinstance(kit, dict) or shape_error(kit.get("hooks", {})):
            raise ValueError("shape")
    except Exception as exc:
        out.write("NOTHING TOUCHED: the kit settings do not parse or have no hooks of the expected shape (%s)\n"
                  % type(exc).__name__)
        return 1
    previous_hooks = None
    if args.previous is not None or args.previous_b64 is not None:
        try:
            previous = json.loads(read_source(args.previous, args.previous_b64))
            if isinstance(previous, dict) and not shape_error(previous.get("hooks", {})):
                previous_hooks = previous.get("hooks", {})
        except Exception:
            previous_hooks = None
        if previous_hooks is None:
            out.write("note         the previous kit render cannot be read, so only scripts %s runs nowhere are added\n"
                      % path)
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        text = raw.decode("utf-8-sig")
        user, twice = loads_strict(text)
    except Exception as exc:
        out.write("NOTHING TOUCHED: %s cannot be read or does not parse (%s)\n" % (path, type(exc).__name__))
        return 1
    if not isinstance(user, dict):
        out.write("NOTHING TOUCHED: %s has no object at its top level\n" % path)
        return 1
    problem = shape_error(user.get("hooks", {}))
    if problem:
        out.write("NOTHING TOUCHED: %s: %s\n" % (path, problem))
        return 1

    user_hooks, kit_hooks = user.get("hooks", {}), kit.get("hooks", {})
    kit_scripts = set(script for _, _, script, _, _ in handlers(kit_hooks))
    if not any(script in kit_scripts for _, _, script, _, _ in handlers(user_hooks)):
        out.write("left alone   %s (it runs none of the kit's hook scripts, so its hooks are yours; %s)\n"
                  % (path, by_hand))
        return 0
    adds, skipped = plan(kit_hooks, user_hooks, previous_hooks, skip_names(path))
    for event, matcher, handler in skipped:
        out.write("skipped      %s (named in %s)\n" % (label(event, matcher, handler), SKIP_FILE))
    if not adds:
        out.write("up to date   %s (no kit hook to add)\n" % path)
        return 0
    body = text.replace("\r\n", "\n")
    form = None if twice else layout(body, user)
    if form is None:
        why = ("it writes %s twice" % ", ".join(sorted(set(twice)))) if twice else \
            "its layout is not one this tool writes back byte for byte"
        out.write("left alone   %s (%s, so a write would change more than the hooks; %s)\n" % (path, why, by_hand))
        return 0

    apply(user, adds)
    width, ascii_only, tail = form
    new_body = json.dumps(user, indent=width, ensure_ascii=ascii_only) + tail
    eol = "\r\n" if b"\r\n" in raw else "\n"
    data = new_body.replace("\n", eol).encode("utf-8")
    for event, matcher, handler, _ in adds:
        out.write("%s %s\n" % ("would add   " if args.dry_run else "added       ", label(event, matcher, handler)))
    if args.dry_run:
        out.write("after-sha256 %s\n" % hashlib.sha256(data).hexdigest())
        return 0
    try:
        with open(path, "r+b") as handle:
            handle.seek(0)
            handle.write(data)
            handle.truncate()
    except OSError as exc:
        out.write("NOTHING TOUCHED: %s cannot be written (%s)\n" % (path, type(exc).__name__))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
