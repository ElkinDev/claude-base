#!/usr/bin/env python3
"""Reading a backup folder and saying what is in it, for scripts/kit-restore.py.

Everything here answers a question without changing anything: where the copy of a file sits inside
a backup, what a manifest recorded, what the list of backups looks like, and what to say about a
file whose fate the record never got. The rollback itself, which is the part that writes, is in
kit-restore.py; it loads this module by path so both work from a checkout with no install step.
"""
import hashlib
import os
from pathlib import Path

MANIFEST_NAME = "manifest.txt"
VERBS = ("overwritten", "created", "new", "kept")
LOST = "the run never got it onto the record, this backup line is the record"


def sha256(path):
    """The hash of a file, or None when it is not there or cannot be read."""
    try:
        digest = hashlib.sha256()
        with open(str(path), "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, IOError):
        return None


def mirror_path(stamp_dir, original):
    """Where the backup copy of `original` sits: the absolute path with the drive as a folder."""
    full = os.path.abspath(original)
    if full.startswith("\\\\"):
        rel = "UNC" + full[1:]
    else:
        drive, rest = os.path.splitdrive(full)
        rel = (drive.rstrip(":") + rest) if drive else full
    return Path(stamp_dir) / rel.lstrip("\\/")


def read_backup(stamp_dir):
    """(trigger, [(verb, path), ...]) for one backup folder."""
    text = (Path(stamp_dir) / MANIFEST_NAME).read_text(encoding="utf-8", errors="replace")
    trigger, entries = "", []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        verb, _, rest = line.partition(" ")
        rest = rest.strip()
        if verb == "trigger":
            trigger = rest
        elif verb in VERBS and rest:
            entries.append((verb, rest))
    return trigger, entries


def list_backups(home, out):
    root = home / "backups"
    stamps = sorted([p for p in root.iterdir() if p.is_dir()]) if root.is_dir() else []
    if not stamps:
        out("no backups under %s" % root)
        return 0
    out("%-18s %6s  %s" % ("stamp", "files", "what wrote it"))
    for stamp in stamps:
        try:
            trigger, entries = read_backup(stamp)
        except (OSError, IOError):
            out("%-18s %6s  %s" % (stamp.name, "-", "no manifest.txt, folder left as it is"))
            continue
        out("%-18s %6d  %s" % (stamp.name, len(entries), trigger or "unrecorded"))
    out("")
    out("Roll one back with: python scripts/kit-restore.py --stamp <stamp>")
    return 0


def off_record_note(backup_file, current, dry_run):
    """What to say about a created file the record never got. The copy in the backup folder is the
    only proof of what the run wrote, so it decides whether the bytes on disk are still those. A
    copy is there to compare against only when an earlier rollback wrote one and then died before
    it removed the file, which is why the ", edited since the run" clause never shows after an
    ordinary install: that run leaves a line for the file and no copy of it."""
    kept = sha256(backup_file)
    state = "" if kept == current else ", edited since the run"
    if kept is None:
        state = ", bytes unknown"
    if dry_run:
        return " (not on the record; a copy would be kept in the backup)%s" % state
    return " (was not on the record; copy kept at %s)%s" % (backup_file, state)
