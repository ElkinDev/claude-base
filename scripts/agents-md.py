"""agents-md.py: keep the kit's marked sections of an AGENTS.md (or any rules file) in step with their source.

    python scripts/agents-md.py render <source> <target>             rewrite the target's marked sections
    python scripts/agents-md.py render <source> <target> --dry-run   say what would change, write nothing
    python scripts/agents-md.py check <source> <target>              exit 1 when a section differs

F03 (docs/03-features/F03-agents-interchange.md) and the data contract of docs/02-architecture/data-contracts.md:
a managed section is the text between a line `<!-- cb:<id> -->` and a line `<!-- /cb:<id> -->`, each marker alone
on its line. Only the text inside markers is rewritten; everything outside them belongs to the target's owner and is
kept byte for byte, each line with its own ending, with one exception: when a section is appended, the blank lines
at the very end of the file give way to one blank line before it, and a last line with no line feed gets one (and its CR when the target's lines end in CRLF and it has none). A marker
quoted inside other text (in backticks, say) is prose, not a marker.

render copies every section of the source into the target: a section the target has is replaced in place, one it
lacks is appended at its end, and a target that does not exist is created from the source's sections alone. The
`learnings` section is the target's own record (append-only, written by the agents that work there), so render
never replaces it: it only adds the source's `learnings` section when the target has none. check exits 1 exactly
when render would write, so a `learnings` section that differs is in step and a missing one is not.

The lines render writes (the sections it replaces or adds) take the ending most of the target's lines have, and the
target keeps its byte order mark. A file whose markers do not pair (an opener with no closer, a closer with no
opener, a section inside another, an id twice) is refused before anything is written. Exit codes: 0 done or in
step, 1 check found a difference, 2 a usage error or a malformed or unreadable file.
"""
import argparse
import os
import re
import sys
import tempfile

OPEN = re.compile(r"^<!-- cb:([a-z][a-z0-9-]*) -->$")
CLOSE = re.compile(r"^<!-- /cb:([a-z][a-z0-9-]*) -->$")
OWN = ("learnings",)  # sections the target owns: added when missing, never replaced


class Malformed(Exception):
    pass


class Unpaired(Malformed):
    def __str__(self):
        return "the markers do not pair: " + super().__str__()


def read(path):
    """(text as it is on disk, the ending of most of its lines, bom) of a file. The text keeps every CR: a line the
    target's owner wrote comes back with the ending it had (review agmd r1 finding 2)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    newline = "\r\n" if 2 * text.count("\r\n") > text.count("\n") else "\n"
    return text, newline, bom


def bare(line):
    return line[:-1] if line.endswith("\r") else line


def sections(text, name):
    """{id: (first body line index, closer line index)} over text split in lines; Malformed on a bad pairing."""
    lines = text.split("\n")
    found, current = {}, None
    for i, line in enumerate(lines):
        opened, closed = OPEN.match(line.rstrip()), CLOSE.match(line.rstrip())
        if opened:
            if current is not None:
                raise Unpaired("%s:%d: section %s opens inside section %s" % (name, i + 1, opened.group(1), current[0]))
            if opened.group(1) in found:
                raise Unpaired("%s:%d: section %s appears twice" % (name, i + 1, opened.group(1)))
            current = (opened.group(1), i)
        elif closed:
            if current is None or closed.group(1) != current[0]:
                raise Unpaired("%s:%d: closer of %s with no open section of that id" % (name, i + 1, closed.group(1)))
            found[current[0]] = (current[1] + 1, i)
            current = None
    if current is not None:
        raise Unpaired("%s:%d: section %s never closes" % (name, current[1] + 1, current[0]))
    return found


def body(lines, span):
    return lines[span[0]:span[1]]


def merge(src_text, dst_text, newline="\n"):
    """(new target text, [(id, what)] changes). The target's lines are split on LF and keep their CR, so a line
    outside the markers is written back as it was read; the lines render writes end in `newline`."""
    src_text = src_text.replace("\r\n", "\n")
    src_lines = src_text.split("\n")
    src = sections(src_text, "source")
    if not src:  # a source with nothing to copy would create an empty target or report a false "in step"
        raise Malformed("source: no <!-- cb:<id> --> section in it")
    if dst_text is None:
        blocks = []
        for sid, span in sorted(src.items(), key=lambda kv: kv[1][0]):
            blocks.append("\n".join(["<!-- cb:%s -->" % sid] + body(src_lines, span) + ["<!-- /cb:%s -->" % sid]))
        return "\n\n".join(blocks) + "\n", [(sid, "created") for sid in sorted(src, key=lambda s: src[s][0])]
    cr = "\r" if newline == "\r\n" else ""
    dst_lines = dst_text.split("\n")
    dst = sections(dst_text, "target")
    changes = []
    # replace from the bottom up, so the spans above stay valid
    for sid, span in sorted(dst.items(), key=lambda kv: -kv[1][0]):
        if sid not in src or sid in OWN:
            continue
        new = body(src_lines, src[sid])
        if [bare(line) for line in body(dst_lines, span)] != new:
            dst_lines[span[0]:span[1]] = [line + cr for line in new]
            changes.append((sid, "replaced"))
    missing = [sid for sid in sorted(src, key=lambda s: src[s][0]) if sid not in dst]
    if missing and dst_lines[-1] not in ("", "\r") and not dst_lines[-1].endswith("\r"):
        dst_lines[-1] += cr  # a last line with no ending is about to have lines after it (a CR alone stays one)
    for sid in missing:
        while dst_lines and dst_lines[-1] in ("", "\r"):
            dst_lines.pop()
        lead = [cr] if dst_lines else []  # an empty target gets no blank line before its first section (r1 finding 4)
        dst_lines += (lead + ["<!-- cb:%s -->%s" % (sid, cr)] + [line + cr for line in body(src_lines, src[sid])]
                      + ["<!-- /cb:%s -->%s" % (sid, cr), ""])
        changes.append((sid, "added"))
    return "\n".join(dst_lines), list(reversed([c for c in changes if c[1] == "replaced"])) + [
        c for c in changes if c[1] == "added"]


def write(path, text, bom):
    folder = os.path.dirname(os.path.abspath(path))
    data = (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".agents-md-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=("render", "check"))
    ap.add_argument("source")
    ap.add_argument("target")
    ap.add_argument("--dry-run", action="store_true", help="render: say what would change, write nothing")
    a = ap.parse_args()
    if not a.source.strip() or not a.target.strip():
        ap.error("source and target must not be blank")
    if a.dry_run and a.action != "render":
        ap.error("--dry-run is render's")
    if os.path.isdir(a.target):
        print("agents-md: the target is a folder: %s" % a.target, file=sys.stderr)
        return 2
    try:
        src_text, _, _ = read(a.source)
        if os.path.exists(a.target):
            dst_text, newline, bom = read(a.target)
        else:
            dst_text, newline, bom = None, "\n", False
        new_text, changes = merge(src_text, dst_text, newline)
    except (OSError, UnicodeDecodeError) as e:
        print("agents-md: cannot read: %s" % e, file=sys.stderr)
        return 2
    except Malformed as e:
        print("agents-md: refused, %s" % e, file=sys.stderr)
        return 2
    if a.action == "check":
        if changes:
            print("agents-md: out of step: %s" % ", ".join("%s %s" % (sid, what.replace("replaced", "differs")
                                                                        .replace("added", "missing").replace("created", "missing"))
                                                         for sid, what in changes))
            return 1
        print("agents-md: in step")
        return 0
    if not changes:
        print("agents-md: nothing to change in %s" % a.target)
        return 0
    summary = ", ".join("%s %s" % c for c in changes)
    if a.dry_run:
        print("agents-md: dry run, %s would get: %s" % (a.target, summary))
        return 0
    try:
        write(a.target, new_text, bom)
    except OSError as e:
        print("agents-md: cannot write %s: %s" % (a.target, e), file=sys.stderr)
        return 2
    print("agents-md: %s: %s" % (a.target, summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
