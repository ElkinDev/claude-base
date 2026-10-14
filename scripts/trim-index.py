"""Drop the link titles of an agent-memory index, keeping every file name and every hook byte.

'- [Title](file.md) - hook' becomes '- file.md - hook'. A title is dropped only when it is proved redundant, except that
in an ASCII title words under three characters and digit runs under three are not checked, so 'Round 2' against a
hook holding 'round' goes; any other title stays whole, in its own bytes, in parentheses after the name
('- file.md (Title) - hook'), so a search for it still finds the line. Both sides are compared NFC normalized, lowered
for a title made only of ASCII and case folded for any other, with each run of white space read as one space:
- a title made only of ASCII is read as words, runs of ASCII letters and digits; words under three characters are
  ignored, and the title is dropped when every remaining word is found in the file name or the hook;
- a title that holds any character outside ASCII is never split into words; it is dropped only when the whole title
  is found in the file name or in the hook. A title that is not UTF-8 always stays.
Lines of any other shape are kept as they are, so a second run over the output changes nothing.

Dry run by default: prints sizes and writes nothing. --apply refuses when no line changes, writes the backup with
exclusive create (never over an existing backup of the same name), then writes the index through a temp file and
os.replace; the backup always exists before the index changes. The backup folder is --bak-dir, default a folder named
memory-bak in the parent of the index's folder, never inside the folder that holds the index: that folder is often
tracked by git (a project-scoped agent memory), and a backup there would be committed by a blanket add and found by
any search of the folder. An index whose folder has no parent (a drive root, a share root) has no such default:
without --bak-dir --apply is refused (exit 1), and so it is when the default would be the index folder itself or inside
it (an index folder named memory-bak). A --bak-dir that is a file, or cannot be made, is a refusal (exit 1)
with the index unchanged. Each line keeps its own ending (LF or CRLF) and a byte order mark at the head is kept. Run
--apply only when no agent that writes to this index is live, since it may add an entry between the read and the
write; the script re-reads the index before the swap and refuses when it changed.

The checks parse the input and the output with patterns of their own, apart from the rewrite: the list of file
names and each hook must match byte for byte, or the script exits 1 and writes nothing.

    python -I trim-index.py <path to the MEMORY.md index> [--apply] [--bak-dir DIR]

Exit 0 on a dry run or an applied rewrite, 1 when a check fails, when nothing would change under --apply, or when the
backup or the swap is refused, 2 on a usage error.
"""
import argparse
import os
import re
import sys
import time
import unicodedata

REWRITE = re.compile(rb'^(\s*-\s*)\[(.*?)\]\((.+?\.md)\)(.*)$')
CHECK_IN = re.compile(rb'^\s*-\s*\[.*?\]\((.+?\.md)\)(.*)$')
CHECK_OUT = re.compile(rb'^\s*-\s*(\S+?\.md)(?: \(.*?\))?((?: |\t|$).*)$')
WORD = re.compile(r'[A-Za-z0-9]+')  # the words of an ASCII title; a title outside ASCII is never split
SPACES = re.compile(r'\s+')
BOM = b'\xef\xbb\xbf'
BAK_NAME = 'memory-bak'


def fold(text, ascii_title):
    """Each run of white space read as one space, and lowered when the title is made only of ASCII, case folded when
    it is not. Both sides are NFC normalized before this."""
    return SPACES.sub(' ', text.lower() if ascii_title else text.casefold())


def title_stays(title, name, hook):
    """False only when the title is proved redundant. Both sides are compared NFC normalized, with each run of white
    space read as one space: lowered for a title made only of ASCII, case folded for any other. A title made only of
    ASCII goes when each of its words of three characters or more is in the file name or the hook. A title holding any
    character outside ASCII is never split: it goes only when the whole title is in the file name or in the hook. A
    title that is not UTF-8 stays, and a byte of the name or the hook that is not UTF-8 matches no character of a
    title."""
    try:
        text = title.decode('utf-8')
    except UnicodeDecodeError:
        return True
    ascii_title = text.isascii()
    want = fold(unicodedata.normalize('NFC', text), ascii_title)
    have = [fold(unicodedata.normalize('NFC', part.decode('utf-8', 'surrogateescape')), ascii_title)
            for part in (name, hook)]
    if ascii_title:
        return any(len(w) >= 3 and not any(w in h for h in have) for w in WORD.findall(want))
    return not any(want in h for h in have)


def rewrite(line):
    """The line rewritten, whether it was an entry, and whether its title stays."""
    m = REWRITE.match(line)
    if not m:
        return line, False, False
    prefix, title, name, hook = m.groups()
    stays = title_stays(title, name, hook)
    return prefix + name + (b' (' + title + b')' if stays else b'') + hook, True, stays


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('index', help='the MEMORY.md index to trim')
    ap.add_argument('--apply', action='store_true', help='write the backup and the index (default: a dry run)')
    ap.add_argument('--bak-dir', help='backup folder (default: %s in the parent of the index folder; required when '
                                      'that folder has no parent)' % BAK_NAME)
    a = ap.parse_args(argv)
    path = a.index
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError as e:
        print('cannot read %s: %s' % (path, e))
        return 2
    bom = raw.startswith(BOM)  # a byte order mark is kept and never part of the first line
    lines = (raw[len(BOM):] if bom else raw).split(b'\n')
    ends = [b'\r' if ln.endswith(b'\r') else b'' for ln in lines]  # each line keeps its own ending
    lines = [ln[:len(ln) - len(e)] for ln, e in zip(lines, ends)]
    out, changed, kept_titles = [], 0, 0
    for ln in lines:
        new, did, stays = rewrite(ln)
        out.append(new)
        changed += did
        kept_titles += stays
    new_raw = (BOM if bom else b'') + b'\n'.join(o + e for o, e in zip(out, ends))
    names_ok = hooks_ok = others_ok = len(lines) == len(out)
    for x, y in zip(lines, out):
        m = CHECK_IN.match(x)
        if not m:
            others_ok = others_ok and x == y
            continue
        name, hook = m.groups()
        lead = x[:len(x) - len(x.lstrip())] + b'- '
        names_ok = names_ok and y.startswith(lead + name) and CHECK_OUT.match(y) is not None
        hooks_ok = hooks_ok and y.endswith(hook) and len(y) <= len(x)
    print('%s: %d bytes -> %d bytes (%.0f pct), %d lines rewritten (%d keep their title), names ok %s, hooks ok %s, '
          'other lines ok %s'
          % (path, len(raw), len(new_raw), 100.0 * (len(raw) - len(new_raw)) / max(1, len(raw)), changed, kept_titles,
             names_ok, hooks_ok, others_ok))
    if not (names_ok and hooks_ok and others_ok):
        return 1
    if not a.apply:
        return 0
    if changed == 0:
        print('nothing to change, refused')
        return 1
    folder = os.path.dirname(os.path.abspath(path))
    bak_dir = a.bak_dir
    if not bak_dir:
        parent = os.path.dirname(folder)
        if parent == folder:  # a drive root, a share root: the default would sit inside the index folder
            print('refused, index unchanged: %s has no parent folder for the default backup, pass --bak-dir' % folder)
            return 1
        bak_dir = os.path.join(parent, BAK_NAME)
        here, there = (os.path.normcase(os.path.realpath(x)) for x in (folder, bak_dir))
        if there == here or there.startswith(here + os.sep):  # an index folder named memory-bak
            print('refused, index unchanged: the default backup folder %s is the index folder or inside it, pass '
                  '--bak-dir' % bak_dir)
            return 1
    try:
        os.makedirs(bak_dir, exist_ok=True)
    except OSError as e:  # a path that is a file, a folder that cannot be made
        print('refused, index unchanged: the backup folder %s is unusable: %s' % (bak_dir, e))
        return 1
    bak = os.path.join(bak_dir, '%s-%s.%s.bak' % (os.path.basename(folder), os.path.basename(path),
                                                  time.strftime('%Y%m%d-%H%M%S')))
    try:
        with open(bak, 'xb') as f:  # exclusive: an existing backup of this name is never overwritten
            f.write(raw)
    except FileExistsError:
        print('refused, index unchanged: the backup %s exists' % bak)
        return 1
    except OSError as e:
        print('refused, index unchanged: the backup %s cannot be written: %s' % (bak, e))
        return 1
    tmp = path + '.trim-tmp'
    try:
        with open(tmp, 'xb') as f:
            f.write(new_raw)
        with open(path, 'rb') as f:
            if f.read() != raw:
                raise RuntimeError('the index changed since it was read (a live agent wrote to it)')
        os.replace(tmp, path)
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        print('refused, index unchanged: %s' % e)
        return 1
    print('applied, backup %s' % bak)
    return 0


if __name__ == '__main__':
    sys.exit(main())
