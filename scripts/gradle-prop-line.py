"""gradle-prop-line.py: change ONE property line of a gradle.properties file that must never be printed.

A user gradle file (~/.gradle/gradle.properties) often holds the release keystore passwords, so no tool may print,
copy or quote it beyond the one line it changes. This script replaces the value of one key, and only when the
current line is exactly the one the caller expects (a change made by someone else is refused, never overwritten).
It prints only that key's old and new line, keeps the file's line endings and every other byte, backs the file up
beside itself first (<file>.<YYYYMMDD-HHMMSS>.bak, same folder, so the copy stays under the same protection), writes
in place, reads the result back and checks that exactly one line differs. Written for a daemon tuning change (the
Kotlin daemon moved to G1); the same call with old and new swapped reverts.

    python gradle-prop-line.py --file <gradle.properties> --key kotlin.daemon.jvmargs --expect="<current value>" --value="<new value>" [--dry-run]

Pass the two values with "=": a value that starts with "-" and holds no space (-Xmx2g) is read by argparse as an
option otherwise, which ends as a usage error (exit 2), never as a wrong write.

The backup is deleted once the read-back passes (a copy of a password file is not left behind; the revert is the
swapped call, which needs no backup); --keep-backup keeps it, and any failure keeps it. The file is read as
ISO-8859-1, the properties encoding. A line continued with a trailing backslash, and a value or expected value
ending in one, are refused.

Exit 0 changed, or already at the new value (nothing written); 2 a usage error, or the file cannot be read; 3 refused
(the key missing or present more than once, the current value not the expected one, a line break, a continuation, a
character outside ISO-8859-1, or no backup could be written: nothing written in every case); 4 the write or its
read-back failed (the file may be partly written; the backup is kept and named).
"""
import argparse
import os
import shutil
import sys
import time

SECRET_WORDS = ("PASSWORD", "PASS", "PW", "SECRET", "TOKEN", "KEY", "PAT", "PRIVATE", "SIGN", "CERT", "STORE", "ALIAS",
                "CREDENTIAL", "AUTH")  # wide on purpose: this tool changes one daemon line, never a secret


def continued(text):
    """True when the text ends in an odd number of backslashes: a properties line that continues on the next."""
    return (len(text) - len(text.rstrip("\\"))) % 2 == 1


def lines_of(raw):
    return raw.split(b"\n")  # the \r of a CRLF file stays on each line, so the endings survive untouched


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--expect", required=True, help="the value the line holds now, exactly")
    ap.add_argument("--value", required=True, help="the value it gets")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-backup", action="store_true", help="keep the backup after a passed read-back")
    a = ap.parse_args()
    key = a.key.strip()
    if not key or "=" in key or not key.isascii():
        ap.error("--key is blank, holds '=' or is not ASCII")
    if any(w in key.upper() for w in SECRET_WORDS):  # the refusal lines print the current value: never for a secret
        ap.error("--key %s names a secret; this script prints the values it reads" % key)
    for name, v in (("--expect", a.expect), ("--value", a.value)):
        if "\n" in v or "\r" in v:
            print("gradle-prop-line: refused, %s holds a line break" % name)
            return 3
    if not a.value.strip():
        ap.error("--value is blank")
    try:
        with open(a.file, "rb") as f:
            raw = f.read()
    except OSError as e:
        print("gradle-prop-line: cannot read %s: %s" % (a.file, e.strerror or e))
        return 2
    lines = lines_of(raw)
    prefix = (key + "=").encode("utf-8")
    hits = [i for i, l in enumerate(lines) if l.startswith(prefix)]
    if len(hits) != 1:
        print("gradle-prop-line: refused, %d lines start with %s= (one expected)" % (len(hits), key))
        return 3
    i = hits[0]
    cr = lines[i].endswith(b"\r")
    current = lines[i][len(prefix):].rstrip(b"\r").decode("latin-1")  # the properties encoding; never raises
    if continued(current) or continued(a.value) or continued(a.expect):
        print("gradle-prop-line: refused, a line continued with a trailing backslash (the line, --value or --expect)")
        return 3
    try:
        new_line = prefix + a.value.encode("latin-1") + (b"\r" if cr else b"")
    except UnicodeEncodeError:
        print("gradle-prop-line: refused, --value holds a character outside ISO-8859-1")
        return 3
    if current == a.value:
        print("gradle-prop-line: already %s=%s, nothing written" % (key, a.value))
        return 0
    if current != a.expect:
        print("gradle-prop-line: refused, the line is %s=%s, not the expected value" % (key, current))
        return 3
    print("old %s=%s" % (key, current))
    print("new %s=%s" % (key, a.value))
    if a.dry_run:
        print("gradle-prop-line: dry run, nothing written")
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup, n = "%s.%s.bak" % (a.file, stamp), 1
    while os.path.exists(backup):  # two runs in one second (an apply and its revert) keep both backups
        n += 1
        backup = "%s.%s-%d.bak" % (a.file, stamp, n)
    out = b"\n".join(lines[:i] + [new_line] + lines[i + 1:])
    try:
        shutil.copy2(a.file, backup)
    except OSError as e:
        print("gradle-prop-line: refused, no backup could be written (%s); nothing changed" % (e.strerror or e))
        return 3
    try:
        with open(a.file, "r+b") as f:  # in place: the same file, its owner and its permissions
            f.seek(0)
            f.write(out)
            f.truncate()
    except OSError as e:
        print("gradle-prop-line: WRITE FAILED (%s); the file may be partly written, restore from %s"
              % (e.strerror or e, backup.replace("\\", "/")))
        return 4
    with open(a.file, "rb") as f:
        back = lines_of(f.read())
    diff = [j for j in range(max(len(back), len(lines))) if j >= len(back) or j >= len(lines) or back[j] != lines[j]]
    if diff != [i] or back[i] != new_line:
        print("gradle-prop-line: READ-BACK FAILED, lines differing: %s; restore from %s" % (diff[:5], backup))
        return 4
    if a.keep_backup:
        print("gradle-prop-line: changed line %d, backup %s" % (i + 1, backup.replace("\\", "/")))
    else:
        os.remove(backup)  # the read-back passed; the revert is the swapped call
        print("gradle-prop-line: changed line %d, read back, backup removed" % (i + 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
