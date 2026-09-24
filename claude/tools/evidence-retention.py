#!/usr/bin/env python3
"""List, and with --apply remove, what an evidence root no longer keeps.

The rule it serves: record work older than a cutoff (two calendar months by default) goes, and a
release artifact goes when its release is more than a number of releases behind the newest. What
"record work" covers comes from a JSON scope file (--scope; evidence-retention.example.json beside
this tool is the shape), so a change of scope is a config change, never a code change:

- `round_folders`: globs of capture round folders, relative to the root. A folder that matches is
  one unit and goes whole when its newest file is older than the cutoff, except its release
  artifacts and their companions, which wait for the release rule below, so a past round can
  stand holding only its artifact until that release is far enough behind.
- `record_files`: globs of files that go when older than the cutoff. Anything below a third-level
  folder (top/x/y/...) goes with that folder, subfolders included, once the newest file under it
  is past the cutoff, so a set such as lanes/gates/<lane>/ is never torn; a file directly in a
  second-level folder goes with that folder when the folder holds only files, and on its own when
  it holds subfolders too (a bucket of independent files); a file in a top folder and any `.bak`
  go on their own. A second-level folder holding both an old file and a fresh subfolder can lose
  the file first.
- `keep`: globs that are never removed, whatever the other rules say. Keep wins over everything.
  Name here every file a tool reads at run time.
- `apk_rule` and `release_kinds`: the release rule, for an Android project whose builds carry a
  `vc<N>` version code in their names. With apk_rule true, a release artifact (a file whose
  extension is in release_kinds, such as `.apk`, and `.aab` if bundles should go the same way) is
  removed, anywhere under the root, when its release is more than `--releases` behind the newest;
  its companions go with it: `<artifact>.<anything>` (x.apk.sha256, x.apk.txt) and the short
  `<stem>.sha256` one build writes for its .apk and .aab together, the latter only once every .apk
  and .aab of that stem beside it goes, by either rule, whatever release_kinds holds. Artifacts
  and companions are never taken by the age rules, so the record rule cannot take a recent
  release's APK before the release rule judges it. A project with no such builds sets apk_rule
  false.
- `trash_days`: 0 deletes; more moves each file into `<trash>/<YYYY-MM-DD>/<its path>` and removes
  a trash day folder once it is older than that many days. An evidence root is usually not under
  git, so the trash is the only road back from a wrong scope or a wrong clock.

A file's age is the latest of its modified time, its creation time (st_birthtime, Windows), and a
`YYYYMMDD[-HHMM[SS]]` stamp in its name. A backup made by a copy keeps the mtime of the file it
protects, and one made by a rename keeps its creation time too; the stamp or the creation time
dates the backup itself. The cutoff is the same day `--months` calendar months back.

A release is a `vc<N>` number; the newest is the highest N of an `.aab` whose name carries it. An
APK takes its release from a `vc<N>` in its name or in a folder of its path, else from its
modified time: the first release whose bundle is not older than the APK. An APK newer than every
bundle belongs to the next release. A `vc1` is not a release: a debug build carries versionCode 1
and may name itself `-vc1.apk`, so that label is skipped and the next part of the path, or the
date, decides.

Dry run by default: one line per file it would remove, one per trash day folder it would purge,
and a summary line; it changes nothing. With --apply it purges the old trash day folders first
(a refused plan never stops the purge), then moves (or deletes) the files one at a time, removes
the folders its own removals left empty, and appends every path with its bytes, reason and trash
path to `ledger/deleted/<date>.txt` under the root. Links and junctions are never followed, moved
or deleted. A run whose candidates pass `--max-share` of the root's files is refused whole, exit 5,
since a wrong clock or a wrong scope file would otherwise empty the root in one morning. An
--apply holds an exclusive lock on `ledger/deleted/.retention.lock` from its purge to its last
removal; a second --apply meanwhile refuses, exit 4, since two runs removing the same files at
once each read the other's removals as errors and leave manifest lines doubled or missing.

--root is required and must hold the register file (rulings.md), so a typo or a wrong working
directory refuses instead of sweeping. The default trash is `<root>-trash` beside the root.

Exit codes: 0 done (or nothing to do), 2 a removal, a purge or a manifest line failed (the rest
still ran), 4 refused before removing anything (a usage error, an unusable root, scope file,
--now, trash or manifest), 5 refused by the share bound. Standard library only.
"""

import argparse
import calendar
import datetime as dt
import fnmatch
import json
import os
import re
import stat
import sys

SCOPE_FILE = "evidence-retention.json"
VC = re.compile(r"(?<![a-z0-9])vc(\d{1,4})(?![0-9])", re.IGNORECASE)
# A backup's own moment in its name: 20260924, 20260924-1150, 20260924-095901.
STAMP = re.compile(r"(?<!\d)(20\d\d)(\d\d)(\d\d)(?:-(\d\d)(\d\d)(\d\d)?)?(?!\d)")
# A root is the evidence root only if it holds this file; a typo in --root refuses instead of sweeping.
ROOT_MARK = "rulings.md"
# Every debug build carries this versionCode, so a vc label with it names no release.
DEBUG_VERSION_CODE = 1
# Under ledger/deleted; two --apply runs at once would race on the same files, so the second refuses.
LOCK_NAME = ".retention.lock"
# A trash folder holds this file; a purge never runs in a folder without it.
TRASH_MARK = ".evidence-trash"
BUILD_ARTIFACTS = (".apk", ".aab")  # what a short <stem>.sha256 names
# Names tried for a path trashed twice on one day: <name>, <name>.2 ... <name>.99.
MAX_TRASH_NAMES = 99


def months_back(now, months):
    """The same moment `months` calendar months earlier; the day is clamped to the month's length."""
    month = now.month - months
    year = now.year
    while month < 1:
        month += 12
        year -= 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day)


def name_stamp(name):
    """The latest valid YYYYMMDD[-HHMM[SS]] stamp in a file name as a timestamp, or 0. A time that
    is not a time (-9999) falls back to the day."""
    best = 0
    for hit in STAMP.finditer(name):
        year, month, day, hour, minute, second = hit.groups()
        for parts in ((year, month, day, hour or 0, minute or 0, second or 0), (year, month, day, 0, 0, 0)):
            try:
                best = max(best, dt.datetime(*map(int, parts)).timestamp())
                break
            except (ValueError, OverflowError, OSError):
                continue
    return best


def age_of(name, info):
    """The moment a file counts from: the latest of its mtime, its creation time and its name stamp."""
    return max(info.st_mtime, getattr(info, "st_birthtime", 0) or 0, name_stamp(name))


def is_link(path):
    """True for a symlink or a junction; an entry that cannot be stat'ed counts as one."""
    if os.path.islink(path):
        return True
    try:
        info = os.lstat(path)
    except OSError:
        return True
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def matches(rel, globs):
    return any(fnmatch.fnmatchcase(rel, g) for g in globs)


def load_scope(path):
    with open(path, "r", encoding="utf-8") as handle:
        scope = json.load(handle)
    if not isinstance(scope, dict):
        raise ValueError("the scope must be a JSON object")
    for key in ("round_folders", "record_files", "keep"):
        value = scope.get(key)
        if not isinstance(value, list) or not all(isinstance(g, str) and g.strip() for g in value):
            raise ValueError("{} must be a list of non-empty globs".format(key))
    if not isinstance(scope.get("apk_rule"), bool):
        raise ValueError("apk_rule must be true or false")
    kinds = scope.get("release_kinds")
    if not isinstance(kinds, list) or not all(isinstance(k, str) and re.fullmatch(r"\.[A-Za-z0-9]+", k) for k in kinds):
        raise ValueError("release_kinds must be a list of extensions such as .apk")
    days = scope.get("trash_days")
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise ValueError("trash_days must be a whole number, 0 or more")
    return scope


def walk(root):
    """Every regular file as (rel, full, mtime, bytes, age), links never entered and never listed."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d != ".git" and not is_link(os.path.join(dirpath, d))]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if is_link(full):
                continue
            try:
                info = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel == "ledger/deleted/" + LOCK_NAME:
                continue  # the run's own lock, not evidence
            files.append((rel, full, info.st_mtime, info.st_size, age_of(name, info)))
    return files


def release_map(files):
    """{release number: newest bundle mtime} from every .aab whose name carries vc<N>."""
    found = {}
    for rel, _, mtime, _, _ in files:
        name = rel.rsplit("/", 1)[-1]
        if not name.lower().endswith(".aab"):
            continue
        hit = VC.search(name)
        if hit:
            number = int(hit.group(1))
            found[number] = max(found.get(number, 0), mtime)
    return found


def apk_release(rel, mtime, releases):
    """The release an APK belongs to: a vc<N> in its name, else in its folders, else by date; a
    debug build's vc1 is skipped."""
    parts = rel.split("/")
    for part in [parts[-1]] + parts[-2::-1]:
        for hit in VC.finditer(part):
            if int(hit.group(1)) != DEBUG_VERSION_CODE:
                return int(hit.group(1))
    for number in sorted(releases):
        if releases[number] >= mtime:
            return number
    return (max(releases) + 1) if releases else None


def release_sets(files, kinds):
    """{rel: the release artifacts it belongs to} for every artifact whose extension is in kinds and
    every companion beside it: <artifact>.<anything> (x.apk.sha256, x.apk.txt) and the short
    <stem>.sha256 that one build writes for its .apk and .aab together."""
    by_folder = {}
    for rel, *_ in files:
        folder, _, name = rel.rpartition("/")
        by_folder.setdefault(folder, []).append(name)
    owners = {}
    for folder, names in by_folder.items():
        for art in names:
            if not art.lower().endswith(kinds):
                continue
            stem = art[:art.rfind(".")]
            for name in names:
                if name == art or name.startswith(art + ".") or name == stem + ".sha256":
                    owners.setdefault((folder + "/" if folder else "") + name, set()).add(
                        (folder + "/" if folder else "") + art)
    return owners


def plan(root, scope, cutoff, releases_back):
    """Every file to remove as (rel, full, bytes, reason), and the root's file count. The
    two rules are about kinds of file: a release artifact and its companions go by the release rule
    only, wherever they lie, and the age rules (rounds, records) never take them."""
    files = walk(root)
    cut = cutoff.timestamp()
    keep = scope["keep"]
    kinds = tuple(k.lower() for k in scope["release_kinds"])
    in_set = release_sets(files, kinds) if kinds else {}
    newest_under = {}  # folder -> the newest age of any file below it
    holds_folders = set()  # folders that hold a subfolder
    for rel, _, _, _, age in files:
        parts = rel.split("/")
        for depth in range(1, len(parts)):
            folder = "/".join(parts[:depth])
            newest_under[folder] = max(newest_under.get(folder, 0), age)
            if depth < len(parts) - 1:
                holds_folders.add(folder)
    rounds = {}
    for item in files:
        parts = item[0].split("/")
        for depth in range(1, len(parts)):
            folder = "/".join(parts[:depth])
            if matches(folder, scope["round_folders"]):
                rounds.setdefault(folder, []).append(item)
                break
    chosen = {}
    for folder, members in rounds.items():
        if matches(folder, keep) or newest_under[folder] >= cut:
            continue
        for rel, full, _, size, _ in members:
            if not matches(rel, keep) and rel not in in_set:
                chosen[rel] = (full, size, "round " + folder)
    in_round = {item[0] for members in rounds.values() for item in members}
    for rel, full, _, size, age in files:
        if (rel in chosen or rel in in_round or rel in in_set or matches(rel, keep)
                or not matches(rel, scope["record_files"])):
            continue
        parts = rel.split("/")
        # below a third-level folder: that folder is the unit, subfolders included; directly in a
        # second-level folder of files: that folder; beside subfolders, in a top folder, or a
        # backup: the file alone
        unit = "/".join(parts[:3]) if len(parts) > 3 else "/".join(parts[:-1])
        whole = len(parts) > 3 or (len(parts) == 3 and unit not in holds_folders)
        if whole and not rel.lower().endswith(".bak"):
            if newest_under[unit] < cut:
                chosen[rel] = (full, size, "record " + unit)
        elif age < cut:
            chosen[rel] = (full, size, "record")
    releases = release_map(files)
    if scope["apk_rule"] and releases and kinds:
        newest = max(releases)
        by_rel = {rel: (full, size, mtime) for rel, full, mtime, size, _ in files}
        short_names = {}  # <stem>.sha256 -> every .apk and .aab of that stem beside it, any case
        by_lower = {rel.lower(): rel for rel in by_rel}
        for rel in in_set:
            folder, _, name = rel.rpartition("/")
            if name.lower().endswith(".sha256") and not name.lower().endswith(BUILD_ARTIFACTS + tuple(e + ".sha256" for e in BUILD_ARTIFACTS)):
                stem = (folder + "/" if folder else "") + name[:-len(".sha256")]
                short_names[rel] = {by_lower[(stem + e).lower()] for e in BUILD_ARTIFACTS if (stem + e).lower() in by_lower}
        doomed_artifacts = {}
        for rel, (full, size, mtime) in by_rel.items():
            if not rel.lower().endswith(kinds) or matches(rel, keep):
                continue
            number = apk_release(rel, mtime, releases)
            if number is not None and number < newest - releases_back:
                doomed_artifacts[rel] = "release {} vc{} of newest vc{}".format(
                    rel.rsplit(".", 1)[-1].lower(), number, newest)
        for rel, owners in in_set.items():
            if rel in chosen or matches(rel, keep) or not owners <= set(doomed_artifacts):
                continue
            named = short_names.get(rel)
            if named and not named <= set(doomed_artifacts) | set(chosen):
                continue
            reason = doomed_artifacts.get(rel) or (doomed_artifacts[sorted(owners)[0]] + " sidecar")
            chosen[rel] = (by_rel[rel][0], by_rel[rel][1], reason)
    return sorted((rel,) + chosen[rel] for rel in chosen), len(files)


def floor_of(root, rel, full, reason):
    """The folder an emptied-folder cleanup stops at, never removed itself: a round's parent (so
    Findings/bench stays when its last round goes), a record's top folder (lanes/ stays), and for
    an APK its own folder, since removing one file of a round must not take the round's folders."""
    if reason.startswith("round "):
        folder = reason[len("round "):]
        parent = folder.rsplit("/", 1)[0] if "/" in folder else ""
        return os.path.abspath(os.path.join(root, *parent.split("/"))) if parent else os.path.abspath(root)
    if reason.startswith("release "):
        return os.path.abspath(os.path.dirname(full))
    top = rel.split("/", 1)[0] if "/" in rel else ""
    return os.path.abspath(os.path.join(root, top)) if top else os.path.abspath(root)


def inside(path, other):
    """True when path is other or lies below it, compared case-blind on Windows."""
    path, other = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(other))
    return path == other or path.startswith(other.rstrip(os.sep) + os.sep)


def check_trash(trash):
    """Refuse a trash that is a link or a file, or a folder without the mark that holds anything:
    it is not ours to fill or purge. Runs before the purge, so no purge runs through a trash the
    same run then refuses."""
    if os.path.lexists(trash) and (is_link(trash) or not os.path.isdir(trash)):
        raise OSError("{} exists and is not a plain folder".format(trash))
    if os.path.isdir(trash) and not os.path.isfile(os.path.join(trash, TRASH_MARK)) and os.listdir(trash):
        raise OSError("{} holds files and has no {}".format(trash, TRASH_MARK))


def prepare_trash(trash):
    """Create the trash with its mark, or accept one that has it, after check_trash."""
    check_trash(trash)
    os.makedirs(trash, exist_ok=True)
    with open(os.path.join(trash, TRASH_MARK), "a", encoding="utf-8"):
        pass


def stale_trash_days(trash, now, days):
    """The trash day folders older than `days`, oldest first: only YYYY-MM-DD names, never a link."""
    if days <= 0 or not os.path.isfile(os.path.join(trash, TRASH_MARK)):
        return []
    limit = now.date() - dt.timedelta(days=days)
    stale = []
    for name in sorted(os.listdir(trash)):
        try:
            day = dt.datetime.strptime(name, "%Y-%m-%d").date()
        except ValueError:
            continue
        folder = os.path.join(trash, name)
        if day < limit and not is_link(folder) and os.path.isdir(folder):
            stale.append(folder)
    return stale


def remove_tree(folder):
    """Remove a trash day folder without following links: walked top down, so a link to a folder
    (a junction or a symlink) is removed as a link before the walk could enter it, its target
    untouched; files next, folders last, deepest first. Returns the entries that could not go."""
    errors = 0
    folders = []
    for dirpath, dirnames, filenames in os.walk(folder, topdown=True, followlinks=False):
        folders.append(dirpath)
        plain = []
        for name in dirnames:
            path = os.path.join(dirpath, name)
            if not is_link(path):
                plain.append(name)
                continue
            try:
                os.rmdir(path)  # a junction or a folder symlink goes as a link
            except OSError:
                try:
                    os.unlink(path)
                except OSError:
                    errors += 1
        dirnames[:] = plain
        for name in filenames:
            try:
                os.remove(os.path.join(dirpath, name))  # a file symlink goes as a link
            except OSError:
                errors += 1
    for dirpath in reversed(folders):
        try:
            os.rmdir(dirpath)
        except OSError:
            errors += 1
    return errors


def trash_name(trash, stamp, rel):
    """A free path for rel in today's trash folder; a second removal the same day takes .2 on."""
    base = os.path.join(trash, stamp.strftime("%Y-%m-%d"), *rel.split("/"))
    for n in range(1, MAX_TRASH_NAMES + 1):
        dest = base if n == 1 else "{}.{}".format(base, n)
        if not os.path.lexists(dest):
            return dest
    raise OSError("no free trash name for {}".format(rel))


def take_lock(root):
    """An exclusive lock on ledger/deleted/.retention.lock, held from the purge to the last removal,
    or None when another run holds it. The OS drops the lock when its holder exits, so a killed run
    leaves no stale lock, only an empty file that the next run locks again."""
    folder = os.path.join(root, "ledger", "deleted")
    os.makedirs(folder, exist_ok=True)
    handle = open(os.path.join(folder, LOCK_NAME), "a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def open_manifest(root, stamp):
    """The day's manifest, opened for append before any removal, so a run that cannot record what
    it removes removes nothing."""
    manifest_dir = os.path.join(root, "ledger", "deleted")
    os.makedirs(manifest_dir, exist_ok=True)
    return open(os.path.join(manifest_dir, stamp.strftime("%Y-%m-%d") + ".txt"), "a", encoding="utf-8")


def delete(root, doomed, stamp, log, trash=None):
    """Move each file into the trash (or delete it when trash is None), then remove the folders
    these removals emptied; append the manifest."""
    deleted, freed, errors, gone = 0, 0, 0, 0
    touched = set()
    with log:
        for rel, full, size, reason in doomed:
            dest = "deleted"
            try:
                if trash is None:
                    os.remove(full)
                else:
                    if not os.path.lexists(full):
                        raise FileNotFoundError(full)
                    dest = trash_name(trash, stamp, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    os.replace(full, dest)
            except FileNotFoundError:
                gone += 1  # another run or a hand got there first: the file is gone either way
                continue
            except OSError as exc:
                errors += 1
                print("ERROR {}: {}".format(rel, exc))
                continue
            deleted += 1
            freed += size
            touched.add((os.path.dirname(full), floor_of(root, rel, full, reason)))
            try:
                log.write("{}\t{}\t{}\t{}\t{}\n".format(stamp.strftime("%Y-%m-%d %H:%M"), rel, size, reason, dest))
                log.flush()
            except OSError as exc:
                errors += 1  # the file is gone but unrecorded: say so on stdout, which the ledger keeps
                print("ERROR manifest line for {} {} ({}) -> {}: {}".format(rel, size, reason, dest, exc))
    for folder, floor in sorted(touched, key=lambda item: len(item[0]), reverse=True):
        current = os.path.abspath(folder)
        while current.startswith(floor + os.sep):
            try:
                os.rmdir(current)  # refuses a folder that still holds anything
            except OSError:
                break
            current = os.path.dirname(current)
    return deleted, freed, errors, gone


def main(argv):
    parser = argparse.ArgumentParser(description="Remove what the evidence root no longer keeps.")
    parser.add_argument("--root", required=True, help="the evidence root; it must hold %s" % ROOT_MARK)
    parser.add_argument("--scope", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), SCOPE_FILE))
    parser.add_argument("--trash", help="the trash folder; default <root>-trash beside the root")
    parser.add_argument("--months", type=int, default=2)
    parser.add_argument("--releases", type=int, default=10)
    parser.add_argument("--max-share", type=float, default=0.25)
    parser.add_argument("--now", help="ISO date and time to read as now; tests only")
    parser.add_argument("--apply", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 4 if exc.code else 0  # a usage error is a refusal, never exit 2 (a failed removal)
    if not args.root.strip() or not args.scope.strip() or (args.trash is not None and not args.trash.strip()):
        print("REFUSED --root, --scope and --trash must name a path")
        return 4
    if args.months < 1 or args.releases < 1 or not 0 < args.max_share <= 1:
        print("REFUSED --months and --releases must be 1 or more, --max-share in (0, 1]")
        return 4
    try:
        now = dt.datetime.fromisoformat(args.now) if args.now is not None else dt.datetime.now()
    except ValueError as exc:
        print("REFUSED --now {!r}: {}".format(args.now, exc))
        return 4
    if now.tzinfo is not None:
        print("REFUSED --now {} carries an offset; give local time, as the file dates are".format(args.now))
        return 4
    if args.apply and now > dt.datetime.now() + dt.timedelta(days=1):
        # a dry run may look ahead; a removal never runs on a clock that is ahead of the machine
        print("REFUSED --now {} is in the future and --apply was given".format(now))
        return 4
    root = os.path.abspath(args.root)
    if not os.path.isfile(os.path.join(root, ROOT_MARK)):
        print("REFUSED {} is not the evidence root (no {})".format(root, ROOT_MARK))
        return 4
    try:
        scope = load_scope(args.scope)
    except (OSError, ValueError) as exc:
        print("REFUSED scope file {}: {}".format(args.scope, exc))
        return 4
    trash = None
    if scope["trash_days"] > 0:
        trash = os.path.abspath(args.trash) if args.trash is not None else root.rstrip(os.sep) + "-trash"
        if inside(trash, root) or inside(root, trash):
            print("REFUSED the trash {} and the root {} must not hold each other".format(trash, root))
            return 4
    lock = None
    if args.apply:
        try:
            lock = take_lock(root)
        except OSError as exc:
            print("REFUSED the folder {} cannot hold the lock, nothing removed: {}".format(
                os.path.join(root, "ledger", "deleted"), exc))
            return 4
        if lock is None:
            print("REFUSED another run holds {}, nothing removed".format(
                os.path.join(root, "ledger", "deleted", LOCK_NAME)))
            return 4
    try:
        return settle(root, scope, now, args, trash)
    finally:
        if lock is not None:
            lock.close()


def settle(root, scope, now, args, trash):
    """Purge the old trash first, so a refused plan never stops it; then plan, apply the share
    bound, and list or remove. main holds the lock around an --apply. A run with nothing to remove
    opens no manifest and makes no trash."""
    days = scope["trash_days"]
    if trash:
        try:
            check_trash(trash)
        except OSError as exc:
            print("REFUSED the trash cannot be used, nothing removed or purged: {}".format(exc))
            return 4
    stale = stale_trash_days(trash, now, days) if trash else []
    purge_errors, purged = 0, 0
    if args.apply:
        for folder in stale:
            failed = remove_tree(folder)
            purge_errors += failed
            if failed:
                print("ERROR purge {}: {} entries left".format(folder, failed))
            else:
                purged += 1
        purge_line = "purged {} of {} day folders, purge errors {}".format(purged, len(stale), purge_errors)
    else:
        purge_line = "purge {} day folders".format(len(stale))
    cutoff = months_back(now, args.months)
    doomed, total = plan(root, scope, cutoff, args.releases)
    size = sum(item[2] for item in doomed)
    reasons = {}
    for item in doomed:
        kind = item[3].split(" ", 1)[0]
        reasons[kind] = reasons.get(kind, 0) + 1
    detail = " ".join("{}={}".format(k, reasons[k]) for k in sorted(reasons)) or "none"
    where = "trash {} for {} days".format(trash, days) if trash else "no trash, deleted"
    if doomed and len(doomed) > args.max_share * total:
        print("REFUSED {} of {} files is past the share bound {}; nothing removed from the root ({}; {})".format(
            len(doomed), total, args.max_share, detail, purge_line))
        return 5
    if not args.apply:
        for folder in stale:
            print("WOULD-PURGE {}".format(folder))
        verb = "WOULD-TRASH" if trash else "WOULD-DELETE"
        for rel, _, nbytes, reason in doomed:
            print("{} {} {} ({})".format(verb, rel, nbytes, reason))
        print("RETENTION dry: files={} bytes={} of {} files, cutoff {:%Y-%m-%d %H:%M}, {}; {}; {}"
              .format(len(doomed), size, total, cutoff, detail, purge_line, where))
        return 0
    deleted, freed, errors, gone = 0, 0, 0, 0
    if doomed:
        try:
            if trash:
                prepare_trash(trash)
        except OSError as exc:
            print("REFUSED the trash cannot be used, nothing removed: {}".format(exc))
            return 4
        try:
            log = open_manifest(root, now)
        except OSError as exc:
            print("REFUSED the manifest under {} cannot be opened, nothing removed: {}".format(
                os.path.join(root, "ledger", "deleted"), exc))
            return 4
        deleted, freed, errors, gone = delete(root, doomed, now, log, trash)
    print("RETENTION applied: deleted={} bytes={} errors={} already-gone={} of {} files, cutoff {:%Y-%m-%d %H:%M}, {}; {}; {}"
          .format(deleted, freed, errors, gone, total, cutoff, detail, purge_line, where))
    return 2 if errors or purge_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
