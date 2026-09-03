"""record.py: append, amend and swap records in text files, with the guards built in.

One command replaces the throwaway script a session writes for every round of work.
The payload arrives on stdin as UTF-8 text, usually from a quoted heredoc, and the
file's own bytes decide how it is written back.

    python record.py add   <target> [--key TEXT] [--require TEXT]...  < payload
    python record.py amend <target>                                   < ANCHOR => SUFFIX lines
    python record.py swap  <target>                                   < an @old block and an @new block
    python record.py round [--config PATH]                            < blocks, all or nothing
    python record.py show  [target] [--tail N]

A target is a name from the config or a path (absolute, or relative to the current
directory, with ~ expanded and either slash). Anything carrying a slash, a drive letter
or a leading ~ is a path. A bare word is a name when the config defines it, a path when
a file of that name sits in the current directory, and an error otherwise. The config is
JSON:

    {"targets": {"landings": "board/landings.md", "plan": "~/plan/log.md"}}

A relative path in it resolves against the config file's own directory. It is looked up
as --config PATH, then the RECORD_CONFIG environment variable, then record.json in the
current directory or the nearest ancestor, and it is read lazily, on the first name: a
run that only names paths never opens it, so a --config that points nowhere costs
nothing until a name needs it.

Writing rules, every command. Bytes in, bytes out, UTF-8, and the file's own BOM is kept.
A file that is not UTF-8 is refused, and so is a payload that is not UTF-8 or that carries
a NUL byte, which is how UTF-16 arrives. One leading BOM on the payload is dropped and
never becomes content, so a row sent from a heredoc and the same row sent from a file that
PowerShell 5.1 Out-File -Encoding utf8 wrote are one row; a BOM anywhere else in the
payload is content. The end of line is the majority of the file's own line endings, where
a tie or a file without newlines means LF, and every line written uses it. A file missing
its final newline gets one before an append, and appended text ends with exactly one
newline. Nothing else in the file changes, byte for byte. A target is never created: a
missing file is refused.

Writing is all or nothing. Every block is computed first, then every target it touches is
proved writable, then each one is written through a temporary file in its own directory
and renamed over, so a file holds either its old bytes or its new bytes and never half of
either. If a write still fails, the targets already written go back to the bytes they
were read with, and any file that could not be put back is named on stderr.

Guards. U+2014 anywhere in the payload is refused. An append whose key is already in the
file is refused, where the key is --key, the key: directive, or by default the first non
blank payload line, stripped. An amend whose line already ends with the suffix is refused,
and so is a swap whose new lines are in place while the old lines are gone.
--require TEXT, and the require: directive, refuse the write unless TEXT is already in
the file, which keeps a record from landing before the record it cites. An anchor has to
match exactly one line, and the old text of a swap exactly one occurrence. A --key or a
--require with no text in it is a refusal, never a way to turn a guard off, and so is a
key: or require: directive with nothing after the colon. add reads those directives at
the head of its payload exactly as a round block does, so they are taken and never
appended, --key together with a key: directive is a usage error, and require: directives
and --require values add up.

amend reads one edit per non blank payload line, as ANCHOR => SUFFIX. Leading whitespace
in ANCHOR counts, so an indented line can be anchored. swap reads a line @old, the old
lines, a line @new and the new lines; blank lines around either block are dropped and
neither block may be empty, because deleting is not recording. The old block matches a
run of whole file lines, never a piece of one, and it has to match exactly one run.

round reads a sequence of blocks, each opened by a header line: @<target> appends,
@amend <target> amends, @swap <target> swaps, and only the @old and @new of a swap may
start with @ inside a block. key: and require: directives may follow an append header,
before its content. Every block is validated and its result computed before anything is
written, so a failure anywhere writes nothing anywhere, and several blocks may share one
target because each one sees the result of the block before it. One file is one target
however it is spelled: two spellings of one path, or a symbolic link or a directory
junction and the file behind it, meet in the same target and it sees every one of their
blocks, in order. A hard link is another file to this command, and the write breaks it.

Output is one line per block on stdout, and errors are one line on stderr as
ERROR <target>: <reason>, with the target named the way the caller wrote it.

Exit codes: 0 written, 1 usage or config, 2 guard (missing file, em-dash, empty payload,
malformed block), 3 already recorded, 4 anchor or require.
"""
import argparse
import json
import os
import sys
import tempfile

BOM = "\ufeff"
EM_DASH = "\u2014"
CONFIG_NAME = "record.json"


class Fail(Exception):
    """A refusal: the exit code, the target to blame when there is one, and the extra
    stderr lines a half finished write has to confess."""

    def __init__(self, code, reason, target=None, notes=()):
        Exception.__init__(self, reason)
        self.code = code
        self.reason = reason
        self.target = target
        self.notes = list(notes)


# ---------------------------------------------------------------- bytes and lines

def read_text(path, shown):
    """The bytes as they are, the text without its BOM, and whether there was one."""
    if not os.path.isfile(path):
        raise Fail(2, "no such file: %s" % path, shown)
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Fail(2, "the file is not UTF-8", shown)
    return (data, text[1:], True) if text.startswith(BOM) else (data, text, False)


def encoded(text, bom):
    return ((BOM + text) if bom else text).encode("utf-8")


def write_bytes(path, data):
    """Through a temporary file in the same directory, then renamed over: a target holds
    either its old bytes or its new bytes, never half of either."""
    handle, temp = tempfile.mkstemp(prefix=".record-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        try:
            os.chmod(temp, os.stat(path).st_mode)
        except OSError:
            pass  # the mode is a courtesy; the bytes are the contract
        os.replace(temp, path)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def eol_of(text):
    """The majority ending. A tie, or a file with no newline at all, means LF."""
    crlf = text.count("\r\n")
    return "\r\n" if crlf > text.count("\n") - crlf else "\n"


def lines_of(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def split_keep(text):
    """(content, ending) per line, so amend can rewrite one line and leave the rest."""
    out, start, i, size = [], 0, 0, len(text)
    while i < size:
        if text[i] == "\n":
            out.append((text[start:i], "\n"))
            i += 1
            start = i
        elif text[i] == "\r" and i + 1 < size and text[i + 1] == "\n":
            out.append((text[start:i], "\r\n"))
            i += 2
            start = i
        else:
            i += 1
    if start < size:
        out.append((text[start:], ""))
    return out


def trimmed(lines):
    block = list(lines)
    while block and not block[0].strip():
        block.pop(0)
    while block and not block[-1].strip():
        block.pop()
    return block


# ---------------------------------------------------------------- targets and config

def looks_like_path(target):
    if target[:1] == "~" or target in (".", "..") or "/" in target or "\\" in target:
        return True
    return len(target) > 1 and target[1] == ":"


def find_config(explicit):
    for value, why in ((explicit, "--config %s" % explicit),
                       (os.environ.get("RECORD_CONFIG"), "RECORD_CONFIG")):
        if value:
            path = os.path.abspath(os.path.expanduser(value))
            if not os.path.isfile(path):
                raise Fail(1, "%s is not a file" % why)
            return path
    here = os.path.abspath(os.getcwd())
    while True:
        candidate = os.path.join(here, CONFIG_NAME)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def load_targets(path):
    try:
        with open(path, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8-sig"))
    except ValueError as error:
        raise Fail(1, "%s is not valid JSON: %s" % (path, error))
    named = data.get("targets") if isinstance(data, dict) else None
    if not isinstance(named, dict):
        raise Fail(1, "%s carries no targets object" % path)
    base = os.path.dirname(path)
    out = {}
    for name, value in named.items():
        where = os.path.expanduser(str(value))
        out[name] = where if os.path.isabs(where) else os.path.abspath(os.path.join(base, where))
    return out


class Resolver:
    """The config is read once, and only when a name actually needs it."""

    def __init__(self, explicit):
        self.explicit = explicit
        self.read = False
        self.path = None
        self.names = {}

    def load(self):
        if self.read:
            return
        self.read = True
        self.path = find_config(self.explicit)
        if self.path:
            self.names = load_targets(self.path)

    def resolve(self, target):
        """A name first, then a file of that name in the current directory, then no."""
        if looks_like_path(target):
            return os.path.abspath(os.path.expanduser(target))
        self.load()
        if target in self.names:
            return self.names[target]
        nearby = os.path.abspath(target)
        if os.path.isfile(nearby):
            return nearby
        where = "not in %s" % self.path if self.path else "no %s found" % CONFIG_NAME
        raise Fail(1, "unknown target: %s, %s" % (target, where))


class Target:
    def __init__(self, shown, path):
        self.shown = shown
        self.path = path
        self.original, self.text, self.bom = read_text(path, shown)
        self.eol = eol_of(self.text)
        self.dirty = False


# ---------------------------------------------------------------- the three operations

def do_add(target, lines, key, require):
    body = list(lines)
    while body and not body[-1].strip():
        body.pop()
    if not body:
        raise Fail(2, "empty payload", target.shown)
    if key is None:
        key = next(line.strip() for line in body if line.strip())
    for needed in require:
        if needed not in target.text:
            raise Fail(4, "required text is not in the file: %s" % needed, target.shown)
    if key in target.text:  # a key always exists by here, and an empty one never gets in
        raise Fail(3, "already recorded", target.shown)
    text = target.text
    if text and not text.endswith("\n"):
        text += target.eol
    target.text = text + target.eol.join(body) + target.eol
    target.dirty = True
    return "%s: +%d lines (%s)" % (target.shown, len(body),
                                   "CRLF" if target.eol == "\r\n" else "LF")


def do_amend(target, lines):
    edits = []
    for line in lines:
        if not line.strip():
            continue
        anchor, sep, suffix = line.partition(" => ")
        if not sep or not anchor.strip() or not suffix.strip():
            raise Fail(2, "an amend line reads ANCHOR => SUFFIX, not %s" % line, target.shown)
        edits.append((anchor, suffix))
    if not edits:
        raise Fail(2, "empty payload", target.shown)
    pairs = split_keep(target.text)
    for anchor, suffix in edits:
        hits = [i for i, pair in enumerate(pairs) if pair[0].startswith(anchor)]
        if not hits:
            raise Fail(4, "not found: %s" % anchor, target.shown)
        if len(hits) > 1:
            raise Fail(4, "ambiguous: %d lines start with %s" % (len(hits), anchor), target.shown)
        content, ending = pairs[hits[0]]
        if content.endswith(suffix):
            raise Fail(3, "already recorded", target.shown)
        pairs[hits[0]] = (content + " " + suffix, ending)
    target.text = "".join(content + ending for content, ending in pairs)
    target.dirty = True
    return "%s: %d lines amended" % (target.shown, len(edits))


def swap_blocks(target, lines):
    old, new, bucket, seen = [], [], None, set()
    for line in lines:
        head = line.strip()
        if head in ("@old", "@new"):
            if head in seen:
                raise Fail(2, "%s appears twice in the swap payload" % head, target.shown)
            seen.add(head)
            bucket = old if head == "@old" else new
            continue
        if bucket is None:
            if head:
                raise Fail(2, "the swap payload starts before its @old line", target.shown)
            continue
        bucket.append(line)
    if "@old" not in seen or "@new" not in seen:
        raise Fail(2, "a swap payload needs an @old line and an @new line", target.shown)
    old, new = trimmed(old), trimmed(new)
    for block, name in ((old, "@old"), (new, "@new")):
        if not block:
            raise Fail(2, "the %s block is empty" % name, target.shown)
    return old, new


def runs_of(body, block):
    """Every index where the block equals a run of consecutive whole lines."""
    return [i for i in range(len(body) - len(block) + 1) if body[i:i + len(block)] == block]


def do_swap(target, lines):
    old, new = swap_blocks(target, lines)
    pairs = split_keep(target.text)
    body = [content for content, _ in pairs]
    hits = runs_of(body, old)
    if len(hits) > 1:
        raise Fail(4, "ambiguous: %d runs of the old lines" % len(hits), target.shown)
    if not hits:
        if runs_of(body, new):
            raise Fail(3, "already recorded", target.shown)
        raise Fail(4, "not found: the old lines are not in the file", target.shown)
    start = hits[0]
    ending = pairs[start + len(old) - 1][1]  # the last replaced line keeps its own ending
    pairs[start:start + len(old)] = [(line, target.eol) for line in new[:-1]] + [(new[-1], ending)]
    target.text = "".join(content + close for content, close in pairs)
    target.dirty = True
    return "%s: swapped 1 occurrence" % target.shown


def roll_back(written):
    """Put back the bytes each target was read with. Whatever resists is confessed."""
    notes = []
    for target in written:
        try:
            write_bytes(target.path, target.original)
        except OSError as error:
            notes.append("ERROR %s: left changed, the roll back failed too: %s"
                         % (target.shown, error.strerror or error))
    return notes


def execute(plan, resolver):
    """Compute every block, prove every target writable, then write. A refusal writes
    nothing, and a write that fails puts back the targets that went before it."""
    opened, order, out = {}, [], []
    for kind, shown, lines, key, require in plan:
        path = resolver.resolve(shown)
        seat = os.path.normcase(os.path.realpath(path))  # one file is one target
        target = opened.get(seat)
        if target is None:
            target = opened[seat] = Target(shown, path)
            order.append(target)
        target.shown = shown  # messages name the target the way this block wrote it
        if kind == "add":
            out.append(do_add(target, lines, key, require))
        elif kind == "amend":
            out.append(do_amend(target, lines))
        else:
            out.append(do_swap(target, lines))
    dirty = [target for target in order if target.dirty]
    for target in dirty:
        try:
            handle = open(target.path, "r+b")
            handle.close()
        except OSError as error:
            raise Fail(2, "cannot write: %s" % (error.strerror or error), target.shown)
    written = []
    for target in dirty:
        try:
            write_bytes(target.path, encoded(target.text, target.bom))
        except OSError as error:
            raise Fail(2, "write failed: %s" % (error.strerror or error), target.shown,
                       roll_back(written))
        written.append(target)
    return out


# ---------------------------------------------------------------- round and show

def take_directives(lines):
    key, require, rest = None, [], list(lines)
    while rest:
        head = rest[0].strip()
        if head.startswith("key:") and key is None:
            key = head[4:].strip()
            if not key:
                raise Fail(2, "a key: directive needs text")
        elif head.startswith("require:"):
            require.append(head[8:].strip())
            if not require[-1]:
                raise Fail(2, "a require: directive needs text")
        else:
            break
        rest.pop(0)
    return key, require, rest


def parse_round(payload):
    blocks, current = [], None
    for line in lines_of(payload):
        head = line.strip()
        inside_swap = current is not None and current[0] == "swap" and head in ("@old", "@new")
        if head.startswith("@") and not inside_swap:
            words = head[1:].split(None, 1)
            if not words:
                raise Fail(2, "a block header needs a target")
            if words[0] in ("amend", "swap"):
                if len(words) < 2 or not words[1].strip():
                    raise Fail(2, "the @%s header needs a target" % words[0])
                current = [words[0], words[1].strip(), []]
            else:
                current = ["add", head[1:].strip(), []]
            blocks.append(current)
            continue
        if current is None:
            if head:
                raise Fail(2, "content before the first block header")
            continue
        current[2].append(line)
    if not blocks:
        raise Fail(2, "empty payload")
    plan = []
    for kind, shown, lines in blocks:
        key, require = None, []
        if kind == "add":
            key, require, lines = take_directives(lines)
        plan.append((kind, shown, lines, key, require))
    return plan


def show(resolver, target, tail):
    if target is not None:
        opened = Target(target, resolver.resolve(target))
        rows = [content for content, _ in split_keep(opened.text)]
        for line in rows[-tail:] if tail > 0 else []:
            print(line)
        return 0
    resolver.load()
    if resolver.path is None:
        raise Fail(1, "no %s found, so there are no named targets" % CONFIG_NAME)
    rows = [("name", "path", "exists", "bytes", "eol", "bom")]
    for name in sorted(resolver.names):
        path = resolver.names[name]
        if os.path.isfile(path):
            data, text, bom = read_text(path, name)
            rows.append((name, path, "yes", str(len(data)),
                         "CRLF" if eol_of(text) == "\r\n" else "LF", "yes" if bom else "no"))
        else:
            rows.append((name, path, "no", "-", "-", "-"))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return 0


# ---------------------------------------------------------------- the command line

class Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write("ERROR: %s\n" % message)
        raise SystemExit(1)


def build_parser():
    parser = Parser(prog="record.py", description="Record lines in text files, with the guards.")
    subs = parser.add_subparsers(dest="command")
    for name in ("add", "amend", "swap"):
        sub = subs.add_parser(name)
        sub.add_argument("target")
        sub.add_argument("--config")
        if name == "add":
            sub.add_argument("--key")
            sub.add_argument("--require", action="append", default=[])
    subs.add_parser("round").add_argument("--config")
    sub = subs.add_parser("show")
    sub.add_argument("target", nargs="?")
    sub.add_argument("--config")
    sub.add_argument("--tail", type=int, default=10)
    return parser


def run(args):
    resolver = Resolver(getattr(args, "config", None))
    if args.command == "show":
        return show(resolver, args.target, args.tail)
    if getattr(args, "key", None) is not None and not args.key.strip():
        raise Fail(1, "--key needs text")
    for needed in getattr(args, "require", []):
        if not needed.strip():
            raise Fail(1, "--require needs text")
    data = sys.stdin.buffer.read()
    if b"\x00" in data:
        raise Fail(2, "the payload carries a NUL byte, is it UTF-16? send UTF-8")
    try:
        payload = data.decode("utf-8-sig")  # one leading BOM is never content
    except UnicodeDecodeError:
        raise Fail(2, "the payload is not UTF-8 (from PowerShell 5.1 pipe a UTF-8 file"
                      " or set [Console]::InputEncoding)")
    if EM_DASH in payload:
        raise Fail(2, "the payload carries an em-dash",
                   getattr(args, "target", None))
    if args.command == "round":
        plan = parse_round(payload)
    else:
        lines = lines_of(payload)
        key, require = getattr(args, "key", None), list(getattr(args, "require", []))
        if args.command == "add":  # the directives mean here what they mean in a round
            said, asked, lines = take_directives(lines)
            if said is not None and key is not None:
                raise Fail(1, "--key and a key: directive together", args.target)
            key = key if said is None else said
            require = require + asked
        plan = [(args.command, args.target, lines, key, require)]
    for line in execute(plan, resolver):
        print(line)
    return 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.error("a command is required: add, amend, swap, round or show")
    try:
        return run(args)
    except Fail as error:
        head = "ERROR %s:" % error.target if error.target else "ERROR:"
        sys.stderr.write("%s %s\n" % (head, error.reason))
        for note in error.notes:
            sys.stderr.write("%s\n" % note)
        return error.code
    except OSError as error:  # nothing reaches the caller as a traceback
        sys.stderr.write("ERROR: %s\n" % (error.strerror or error))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
