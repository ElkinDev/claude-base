"""Add the kit's new hooks to a settings.json the user has made their own, and nothing else.

install.ps1 never overwrites a settings.json that differs from what it wrote: the kit version goes
beside it as settings.json.new. Hooks are the part of that file the kit keeps adding to, so without
this every update meant opening the .new and copying a block by hand. This does that one job.

Rules:
  1. A handler is known by its key: event, matcher, the file name of the script its command runs
     (the last token ending in .py, .ps1, .sh or .cmd, case folded) and the arguments after that
     script (quotes stripped, whitespace collapsed, backslashes as slashes, the kit home written as
     <kit>). The interpreter and its flags before the script are not part of it, so `py -3 x.py`
     and `python x.py`, or a bare `powershell -File b.ps1` and the full-path form, are one handler,
     while `compact-recover.py` and `compact-recover.py --rulings` are two. A command that runs no
     script is known by its whole text.
  2. What the kit has already offered is a record this tool owns, `.kit-hooks-merged.json` in the
     kit home: the keys the user's file has received or was found to have. Only kit keys outside
     it are offered. A key in it that the user later removed or moved is never offered again,
     which is how a deliberate removal is respected; a script named in hook-merge-skip.txt beside
     the settings file is never offered either.
  3. The record advances only when this tool writes the file successfully, or finds every offered
     key already there. A dry run, a refusal, a file left alone or a failure never touches it, so a
     hook the user did not get is offered again on the next run.
  4. With no record (the first run, or one that cannot be read), the record is seeded from the kit
     keys the user's file already runs, never from the settings.json.new, which may hold hooks
     that were never merged. So a hook removed before the first merge is offered once, on a line
     of its own; hook-merge-skip.txt keeps it out for good. One line says the record was seeded.
     Its absence is never an error.
  5. Only additions, under the key's event and matcher, appended to the user's entry with that
     matcher or as a new entry. Nothing of the user's is removed, reordered or changed, and only
     a file that already runs at least one kit handler is merged into.
  6. The file is written only when re-serialising it as parsed, with its own indent of spaces,
     gives back its own bytes (line endings and a BOM aside) and it holds no duplicate key. Any
     other layout is left alone, with one line naming the .new. A file that does not parse, or
     whose hooks are not an object of lists of entries that each carry a hooks list of objects, is
     never touched (exit 1, in a dry run too). A file that is a link is left alone.
  7. The write is in place, UTF-8 without BOM, the file's own line ending. The record is written
     after it, through a temp file renamed over it.

Usage:
  python merge-hooks.py (--kit <file> | --kit-b64 <base64>) [--record <file>] [--kit-home <dir>]
                        <user settings.json> [--dry-run]

--record defaults to .kit-hooks-merged.json and --kit-home to the folder of the settings file. One
line per handler added (`added`, or `would add` under --dry-run), or one line saying why nothing
was. A dry run also prints `after-sha256 <hex>` when it would write the file and `record-changes`
when it would write the record, so the installer can back both up first. Exit 0 when merged, up to
date or left alone; 1 when nothing could be done safely; 2 on a bad argument.
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
import tempfile

SKIP_FILE = "hook-merge-skip.txt"
RECORD_FILE = ".kit-hooks-merged.json"
SCRIPT_EXTENSIONS = (".py", ".ps1", ".sh", ".cmd")
TOKEN = re.compile(r'"([^"]*)"|\'([^\']*)\'|(\S+)')
REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: a symlink or a junction on Windows


def slashes(text):
    return re.sub(r"/+", "/", str(text).replace("\\", "/"))


def tokens(command):
    return [next(group for group in m.groups() if group is not None) for m in TOKEN.finditer(str(command or ""))]


def handler_id(command, kit_home):
    """(script, arguments) of a command, by rule 1."""
    parts = tokens(command)
    at = None
    for index, token in enumerate(parts):
        if token.strip().lower().endswith(SCRIPT_EXTENSIONS):
            at = index
    if at is None:
        return "command:" + re.sub(r"\s+", " ", slashes(command).lower()).strip(), ""
    script = slashes(parts[at].strip()).rsplit("/", 1)[-1].lower()
    home = slashes(kit_home).rstrip("/")
    args = []
    for token in parts[at + 1:]:
        token = slashes(token.strip())
        if home:
            token = re.sub(re.escape(home), "<kit>", token, flags=re.IGNORECASE)
        args.append(token)
    return script, re.sub(r"\s+", " ", " ".join(args)).strip()


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


def handlers(hooks, kit_home):
    """(key, handler, entry_has_matcher) for every handler with a command, key by rule 1."""
    for event, entries in hooks.items():
        for entry in entries:
            for handler in entry["hooks"]:
                if handler.get("command"):
                    script, args = handler_id(handler["command"], kit_home)
                    yield (event, matcher_of(entry), script, args), handler, "matcher" in entry


def skip_names(settings_path):
    path = os.path.join(os.path.dirname(os.path.abspath(settings_path)), SKIP_FILE)
    try:
        with open(path, encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return set()
    return set(line.strip().lower() for line in lines if line.strip() and not line.strip().startswith("#"))


def read_record(path):
    """The keys of the record, or None when it is absent or cannot be read."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        keys = data["keys"]
        return set(tuple(str(part) for part in key) for key in keys if isinstance(key, list) and len(key) == 4)
    except Exception:
        return None


def record_bytes(keys):
    body = {"version": 1, "keys": [list(key) for key in sorted(keys)]}
    return (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_record(path, keys):
    folder = os.path.dirname(os.path.abspath(path)) or "."
    handle, temp = tempfile.mkstemp(prefix=".kit-hooks-merged-", dir=folder)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(record_bytes(keys))
        os.replace(temp, path)
    except OSError:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def apply(user, adds):
    hooks = user.setdefault("hooks", {})
    for (event, matcher, _, _), handler, had_matcher in adds:
        entries = hooks.setdefault(event, [])
        target = next((entry for entry in entries if matcher_of(entry) == matcher), None)
        if target is None:
            target = {"matcher": matcher, "hooks": []} if had_matcher else {"hooks": []}
            entries.append(target)
        target["hooks"].append(copy.deepcopy(handler))


def label(key, handler):
    return "%s[%s] %s" % (key[0], key[1] or "*", handler.get("command"))


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
    ap.add_argument("--record", help="the merge record (default: %s beside the settings file)" % RECORD_FILE)
    ap.add_argument("--kit-home", help="the kit home the commands point into (default: the settings file's folder)")
    ap.add_argument("settings", help="the user's settings.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out = sys.stdout
    path = args.settings
    folder = os.path.dirname(os.path.abspath(path))
    record_path = args.record or os.path.join(folder, RECORD_FILE)
    kit_home = args.kit_home or folder
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

    kit_handlers = list(handlers(kit.get("hooks", {}), kit_home))
    kit_keys = set(key for key, _, _ in kit_handlers)
    user_keys = set(key for key, _, _ in handlers(user.get("hooks", {}), kit_home))
    if not (user_keys & kit_keys):
        out.write("left alone   %s (it runs none of the kit's hooks, so its hooks are yours; %s)\n" % (path, by_hand))
        return 0

    baseline = read_record(record_path)
    seeded = baseline is None
    if seeded:
        baseline = user_keys & kit_keys
        out.write("baseline     %s seeded from the kit hooks %s already runs\n" % (record_path, path))

    skips = skip_names(path)
    offered, adds, seen = set(), [], set()
    for key, handler, has_matcher in kit_handlers:
        if key in baseline or key in seen:
            continue
        seen.add(key)
        if key[2] in skips:
            out.write("skipped      %s (named in %s)\n" % (label(key, handler), SKIP_FILE))
            continue
        offered.add(key)
        if key not in user_keys:
            adds.append((key, handler, has_matcher))
    advanced = baseline | offered
    record_changes = seeded or advanced != baseline

    if not adds:
        out.write("up to date   %s (no kit hook to add)\n" % path)
        if args.dry_run:
            if record_changes:
                out.write("record-changes\n")
            return 0
        if record_changes:
            try:
                write_record(record_path, advanced)
            except OSError as exc:
                out.write("NOTHING TOUCHED: %s cannot be written (%s)\n" % (record_path, type(exc).__name__))
                return 1
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
    for key, handler, _ in adds:
        out.write("%s %s\n" % ("would add   " if args.dry_run else "added       ", label(key, handler)))
    if args.dry_run:
        out.write("after-sha256 %s\n" % hashlib.sha256(data).hexdigest())
        out.write("record-changes\n")
        return 0
    try:
        with open(path, "r+b") as handle:
            handle.seek(0)
            handle.write(data)
            handle.truncate()
    except OSError as exc:
        out.write("NOTHING TOUCHED: %s cannot be written (%s)\n" % (path, type(exc).__name__))
        return 1
    try:
        write_record(record_path, advanced)
    except OSError as exc:
        out.write("record       %s cannot be written (%s); the next run offers these hooks again\n"
                  % (record_path, type(exc).__name__))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
