#!/usr/bin/env python3
"""Put back what the installer replaced, from one of its backups.

Every run of install.ps1 that writes anything leaves a folder under `<kit home>/backups/<stamp>/`
holding a copy of each file it replaced, plus a `manifest.txt` recording what it did: which files
it overwrote, which ones it created (they did not exist before, so a rollback removes them), which
files of yours it kept untouched, and which `.new` proposals it left beside them.

    python scripts/kit-restore.py --list
    python scripts/kit-restore.py --stamp 20260828-120000 --dry-run
    python scripts/kit-restore.py --stamp 20260828-120000

The kit home is `~/.claude`, or `$KIT_HOME` when that variable is set. The installer reads the
same variable, so the two always agree on which tree they are talking about.

A file the record says you changed after the backup was taken is never touched: the restore prints
why it skipped it and exits 1, and `--force` is the way to say you meant it anyway. So is a file
something else is holding open, which is named and stepped over instead of stopping the rollback.
A file the record never heard of is put back from the copy in the backup, because the line in that
backup is then the only record of what happened, and a file it created is removed only after a copy
of it goes into the backup folder. Nothing outside that manifest's paths is read or written.

No file you have is ever opened for writing. Every restore fills a temp file beside the target and
renames it over the target, so a write that cannot finish costs the temp file and nothing else: a
skipped file still holds every byte it held before the run.

Exit codes: 0 when every entry was handled, 1 when something was skipped or missing, 2 on a bad
argument (unknown stamp, missing or unreadable manifest).
"""
import argparse
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("kit_restore_report",
                                               os.path.join(HERE, "kit_restore_report.py"))
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)
MANIFEST_NAME, VERBS, LOST = report.MANIFEST_NAME, report.VERBS, report.LOST
sha256, mirror_path, read_backup = report.sha256, report.mirror_path, report.read_backup
list_backups, off_record_note = report.list_backups, report.off_record_note

KIT_MANIFEST = ".kit-manifest.json"


def kit_home():
    """The tree the installer manages. KIT_HOME wins so a test can sandbox the whole thing."""
    from_env = os.environ.get("KIT_HOME", "").strip()
    if from_env:
        return Path(from_env)
    return Path(os.path.expanduser("~")) / ".claude"


def write_bytes(path, data):
    with open(str(path), "wb") as handle:
        handle.write(data)


def swap_in(target, fill):
    """Put new bytes at `target` without ever opening `target` itself for writing: `fill` writes
    them into a temp file beside it, and that file is renamed over the target. A rename either
    happens or does not, so a target something else is holding keeps every byte it had and the
    caller reports the entry skipped, where opening it for writing would have emptied it first."""
    parent = os.path.dirname(os.path.abspath(str(target))) or "."
    if not os.path.isdir(parent):
        os.makedirs(parent)
    handle, temp = tempfile.mkstemp(prefix=".kit-restore-", dir=parent)
    os.close(handle)
    try:
        fill(temp)
        try:
            shutil.copymode(str(target), temp)
        except (OSError, IOError):
            pass  # nothing there to copy a mode from, so the temp keeps its own
        os.replace(temp, str(target))
    except (OSError, IOError):
        try:
            # The mode was copied from the target, so a read-only target left the temp read-only
            # and Windows refuses to remove it. The temp is ours, and it goes whatever its mode.
            os.chmod(temp, stat.S_IREAD | stat.S_IWRITE)
            os.remove(temp)
        except OSError:
            pass
        raise


class KitManifest(object):
    """The installer's record of what it wrote. Paths compare the way the OS compares them."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = {"version": 1, "installed": "", "files": {}}
        self.dirty = False
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("files"), dict):
                self.data = loaded
        except (OSError, IOError, ValueError):
            pass
        self.index = dict((os.path.normcase(k), k) for k in self.data["files"])

    def recorded(self, path):
        key = self.index.get(os.path.normcase(os.path.abspath(path)))
        return self.data["files"].get(key) if key else None

    def forget(self, path):
        key = self.index.pop(os.path.normcase(os.path.abspath(path)), None)
        if key is not None:
            self.data["files"].pop(key, None)
            self.dirty = True

    def save(self):
        if not self.dirty:
            return
        text = json.dumps(self.data, indent=2, sort_keys=True)
        swap_in(self.path, lambda temp: write_bytes(temp, text.encode("utf-8")))


def prune_empty(start, home):
    """Remove the folders a removed file leaves empty, inside the kit home only. A file the
    installer created in a project (a hooks folder, an agents folder) is removed on its own:
    pruning there could climb into the repository, and nothing outside the kit home is ours."""
    home = os.path.abspath(str(home))
    current = Path(os.path.abspath(str(start)))
    while str(current).lower().startswith(home.lower() + os.sep):
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


def restore_overwritten(target, backup_file, manifest, force, dry_run, out):
    """Put one replaced file back. Returns True when the entry was handled."""
    if not backup_file.is_file():
        out("missing      %s (no copy in this backup)" % target)
        return False
    current, kept = sha256(target), sha256(backup_file)
    if kept is None:
        out("missing      %s (the copy in this backup cannot be read)" % target)
        return False
    if current == kept:
        out("unchanged    %s (already the version in the backup)" % target)
        manifest.forget(target)
        return True
    recorded = manifest.recorded(target)
    if current is None and os.path.exists(str(target)) and not force:
        # The file is there and will not be read, so whose bytes those are cannot be checked. That
        # is a reason to leave it alone, not a reason to write over it on the record's word.
        out("skipped      %s (cannot read it to compare, pass --force to overwrite it)" % target)
        return False
    if current is not None and recorded is not None and recorded != current and not force:
        out("skipped      %s (changed after the backup, pass --force to overwrite it)" % target)
        return False
    if dry_run:
        out("would restore %s" % target)
        return True
    swap_in(target, lambda temp: shutil.copyfile(str(backup_file), temp))
    manifest.forget(target)
    out("restored     %s%s" % (target, "" if recorded is not None else " (%s)" % LOST))
    return True


def remove_created(target, backup_file, manifest, home, force, dry_run, out):
    """Remove one file the installer created. Returns True when the entry was handled."""
    current = sha256(target)
    if current is None:
        if os.path.exists(str(target)):
            out("skipped      %s (locked or unreadable)" % target)
            return False
        out("gone         %s (nothing to remove)" % target)
        manifest.forget(target)
        return True
    recorded = manifest.recorded(target)
    if recorded is not None and recorded != current and not force:
        out("skipped      %s (changed after the install, pass --force to remove it)" % target)
        return False
    # Computed before anything is written, so a dry run and the real run say the same thing.
    note = "" if recorded is not None else off_record_note(backup_file, current, dry_run)
    if dry_run:
        out("would remove %s%s" % (target, note))
        return True
    if recorded is None and not backup_file.is_file():
        # The record never got this file, so nothing proves whose bytes these are. The line says
        # the run created it and the rollback takes it away, but only after the folder keeps a
        # copy: this way the rollback of a half finished run never destroys anything.
        swap_in(backup_file, lambda temp: shutil.copyfile(str(target), temp))
    os.remove(str(target))
    prune_empty(os.path.dirname(os.path.abspath(target)), home)
    manifest.forget(target)
    out("removed      %s%s" % (target, note))
    return True


def restore(home, stamp, force, dry_run, out):
    stamp_dir = home / "backups" / stamp
    if not stamp_dir.is_dir():
        out("no backup called %s under %s" % (stamp, home / "backups"))
        return 2
    try:
        trigger, entries = read_backup(stamp_dir)
    except (OSError, IOError):
        out("%s has no manifest.txt this tool can read, so there is nothing to roll back from it."
            % stamp_dir)
        out("A folder like this is what a run leaves when it takes a stamp and then writes nothing.")
        return 2
    if not entries:
        out("the manifest in %s holds no entry this tool understands, so there is nothing to roll"
            % stamp_dir)
        out("back from it. The folder is left exactly as it is; its files are under their own"
            " absolute paths inside it.")
        return 2

    out("backup %s%s" % (stamp, (" (%s)" % trigger) if trigger else ""))
    if dry_run:
        out("dry run: nothing below is written")
    manifest = KitManifest(home / KIT_MANIFEST)
    problems = 0
    for verb, target in entries:
        # One entry nothing will let go of stops that entry, never the rest of the rollback.
        try:
            if verb == "overwritten":
                ok = restore_overwritten(target, mirror_path(stamp_dir, target), manifest, force,
                                         dry_run, out)
            elif verb == "created":
                ok = remove_created(target, mirror_path(stamp_dir, target), manifest, home, force,
                                    dry_run, out)
            elif verb == "kept":
                out("kept         %s (yours, the run never wrote it)" % target)
                ok = True
            else:
                out("left         %s (kit proposal, delete it by hand once you have merged it)"
                    % target)
                ok = True
        except (OSError, IOError):
            out("skipped      %s (locked or unreadable)" % target)
            ok = False
        if not ok:
            problems += 1
    if not dry_run:
        manifest.save()
    out("")
    out("%d %s, %d skipped. The backup folder itself is never deleted: %s"
        % (len(entries), "entry" if len(entries) == 1 else "entries", problems, stamp_dir))
    return 1 if problems else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Restore what install.ps1 replaced. See docs/ADOPTION.md.")
    parser.add_argument("--list", action="store_true", help="list the backups and what wrote them")
    parser.add_argument("--stamp", help="the backup folder to roll back")
    parser.add_argument("--dry-run", action="store_true", help="print the actions, write nothing")
    parser.add_argument("--force", action="store_true",
                        help="act on files that changed after the backup was taken")
    args = parser.parse_args(argv)

    out = lambda text: print(text)
    home = kit_home()
    if args.list == bool(args.stamp):
        parser.error("pass either --list or --stamp <stamp>")
    if args.list:
        return list_backups(home, out)
    return restore(home, args.stamp, args.force, args.dry_run, out)


if __name__ == "__main__":
    sys.exit(main())
