#!/usr/bin/env python3
"""List, and with --apply remove, the worktrees of landed lanes.

Dry run by default: it classifies every worktree of a repository and prints one line each,
changing nothing. With --apply it removes only the REMOVE class, one by one, with a plain
`git worktree remove` and no other git write: no forcing flag, no pruning, no branch delete,
and it stops on the first refusal so a half-removed directory stays for a human to look at.

One belt protects evidence, and it needs no search for citations. Tracked source comes back
from git for ever, because a removal keeps the branch; what a removal really destroys is the
ignored `build/` tree, and the reports cite exactly four kinds of subtree inside it:
`build/lockrun` and `build/precheck` at the worktree root, and every `reports` or
`test-results` directory whose parent is a `build` directory, at any depth. Before a worktree
is removed those subtrees are copied into the evidence root and the copy is VERIFIED, file
count and byte count against the source; only a verified archive lets the removal run. The
archive leaf carries the worktree's tip, `<directory name>--<tip9>`, so a worktree directory
name reused later by another lane gets its own leaf instead of writing over the first one.

The archive is this tool's longest step, up to a minute on a large worktree, so the freshness
belts are read again AFTER it and before the removal: a gate that starts during the copy is
seen, and its worktree is skipped with its archive left beside it.

Defaults: `--repo` is the current directory. The mutex belt reads `--lock-root`, a folder of
`*.lock.d` mutex directories, and is off when it is empty (the default). The archive goes
under `--evidence-root` in `worktree-archive/`; left out, it is EVIDENCE_ROOT, else the kit's
`{repo_parent}/evidence` (pass `--evidence-root "$(python scripts/evidence-path.py)"` to
follow the project's own spec). The run belt and the two root leaves follow the kit's
gradle-lockrun convention: a runner writes `build/lockrun/<tag>.log` when a run starts and
`<tag>.done` when it ends, and `build/precheck` holds the precheck stamps.

Standard library only. Every git call is an argument list; no shell.
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys

CLASS_ORDER = (
    "REMOVE",
    "DIRTY",
    "NOT-LANDED",
    "IN-FLIGHT",
    "KEPT",
    "DETACHED-OR-BARE",
    "MISSING",
)

READ_TIMEOUT = 60
ARCHIVE_LEAF = "worktree-archive"
# Named once at the worktree root, under its `build` directory.
ARCHIVE_ROOT_LEAVES = ("lockrun", "precheck")
# Named under any `build` directory, at any depth.
ARCHIVE_BUILD_LEAVES = ("reports", "test-results")
# Never walked at all.
SKIP_DIR_NAMES = (".git", ".gradle", "node_modules")
# Never walked when their parent is a `build` directory: generated bulk, no evidence.
SKIP_BUILD_CHILDREN = ("intermediates", "tmp", "kotlin", "generated", "outputs")
# The archive names the source it came from, so a second worktree of the same name stops.
SOURCE_MARKER = ".source"
# Windows refuses a path near 260 characters; a destination past this is a stop, not a skip.
MAX_DEST_PATH = 240
# Characters of the tip that name the archive leaf, so a reused directory name keeps two leaves.
TIP_LENGTH = 9


class ArchiveStop(Exception):
    """The archive cannot be made or cannot be proved, so the removal must not run."""


def norm(path):
    """Compare paths the way Windows does: absolute and case-insensitive."""
    return os.path.normcase(os.path.abspath(path))


def git(args, timeout=READ_TIMEOUT):
    """Run one git command as an argument list. Returns (code, stdout, stderr).

    A timeout of None means no deadline at all, which is what a removal gets: a git killed
    in the middle of deleting a worktree leaves a half-deleted directory still registered.
    """
    try:
        done = subprocess.run(
            ["git"] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after {} s".format(timeout)
    except OSError as exc:  # git missing, path unusable
        return 125, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def one_line(*chunks):
    """Fold git's own message into a single line, keeping its words."""
    text = " ".join(chunk.strip() for chunk in chunks if chunk and chunk.strip())
    return " ".join(text.split()) or "no message from git"


def parse_worktrees(repo):
    """Parse `git worktree list --porcelain` into records, in git's own order."""
    code, out, err = git(["-C", repo, "worktree", "list", "--porcelain"])
    if code != 0:
        return None, one_line(out, err)
    records = []
    current = None
    for raw in out.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("worktree "):
            current = {
                "path": line[len("worktree "):].strip(),
                "head": "",
                "branch": "",
                "detached": False,
                "bare": False,
            }
            records.append(current)
            continue
        if current is None:
            continue
        if line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            current["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line.strip() == "detached":
            current["detached"] = True
        elif line.strip() == "bare":
            current["bare"] = True
    return records, ""


def is_reparse(path):
    """True for a symlink, a junction or anything else the walk must not follow.

    `os.path.islink` answers False for a Windows junction, so the file attributes are read
    too. An entry that cannot be stat'ed counts as one: the archive never guesses.
    """
    if os.path.islink(path):
        return True
    try:
        info = os.lstat(path)
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def ignore_links(dirpath, names):
    """copytree's ignore callable: never copy through a symlink or a junction."""
    return [name for name in names if is_reparse(os.path.join(dirpath, name))]


def archive_subtrees(worktree):
    """Relative paths of every build subtree of this worktree the archive must hold.

    One walk, which never enters `.git`, `.gradle`, `node_modules`, the generated children of
    a `build` directory, or anything that is a link. A subtree that is collected is not walked
    into either: it is copied whole.
    """
    root = os.path.abspath(worktree)
    build_root = os.path.join(root, "build")
    found = []
    for dirpath, dirnames, _ in os.walk(root, followlinks=False):
        in_build = os.path.basename(dirpath).lower() == "build"
        keep = []
        for name in sorted(dirnames):
            full = os.path.join(dirpath, name)
            if name in SKIP_DIR_NAMES or is_reparse(full):
                continue
            if in_build:
                lowered = name.lower()
                if lowered in SKIP_BUILD_CHILDREN:
                    continue
                collected = lowered in ARCHIVE_BUILD_LEAVES or (
                    norm(dirpath) == norm(build_root) and lowered in ARCHIVE_ROOT_LEAVES
                )
                if collected:
                    found.append(os.path.relpath(full, root))
                    continue
            keep.append(name)
        dirnames[:] = keep
    return sorted(set(found))


def measure_tree(source, dest_prefix=None):
    """(files, bytes) of a tree, links skipped and never followed.

    With a destination prefix, every file's destination path is checked as it is counted, so
    a path Windows would refuse stops the run before anything is copied.
    """
    files = 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        dirnames[:] = [
            name for name in sorted(dirnames) if not is_reparse(os.path.join(dirpath, name))
        ]
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if is_reparse(full):
                continue
            if dest_prefix is not None:
                dest = os.path.join(dest_prefix, os.path.relpath(full, source))
                if len(dest) > MAX_DEST_PATH:
                    raise ArchiveStop(
                        "destination path longer than {} characters: {}".format(
                            MAX_DEST_PATH, dest
                        )
                    )
            files += 1
            total += os.path.getsize(full)
    return files, total


def worktree_tip(path):
    """The worktree's short tip, read before anything is copied. Names its archive leaf."""
    code, out, err = git(["-C", path, "rev-parse", "--short={}".format(TIP_LENGTH), "HEAD"])
    tip = out.strip().splitlines()[0].strip() if code == 0 and out.strip() else ""
    if not tip:
        raise ArchiveStop("cannot read the tip of {}: {}".format(path, one_line(out, err)))
    return tip


def archive_leaf_name(path, tip):
    """`<worktree directory name>--<tip9>`: two lanes of the same name keep two leaves."""
    return "{}--{}".format(os.path.basename(os.path.normpath(path)), tip)


def archive_destination(path, evidence_root, tip):
    """Where this worktree's archive lives: one leaf per worktree directory name and tip."""
    return os.path.join(evidence_root, ARCHIVE_LEAF, archive_leaf_name(path, tip))


def plan_archive(path, evidence_root, tip):
    """(subtrees, files, bytes) that an archive of this worktree would hold. Writes nothing."""
    dest_root = archive_destination(path, evidence_root, tip)
    subtrees = archive_subtrees(path)
    files = 0
    total = 0
    for relative in subtrees:
        try:
            count, size = measure_tree(
                os.path.join(path, relative), os.path.join(dest_root, relative)
            )
        except OSError as exc:
            raise ArchiveStop("cannot read {}: {}".format(relative, one_line(str(exc))))
        files += count
        total += size
    return subtrees, files, total


def read_marker(dest_root):
    """The source path this archive leaf was written for, or empty when it has none."""
    marker = os.path.join(dest_root, SOURCE_MARKER)
    try:
        with open(marker, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def archive_worktree(path, evidence_root):
    """Copy the build subtrees of this worktree into the evidence root and prove the copy.

    Returns (files, bytes) actually archived. Raises ArchiveStop when the archive cannot be
    made or cannot be proved equal to its source, which keeps the worktree on disk.
    """
    tip = worktree_tip(path)
    dest_root = archive_destination(path, evidence_root, tip)
    recorded = read_marker(dest_root)
    if recorded and norm(recorded) != norm(path):
        raise ArchiveStop(
            "{} already holds the archive of {}".format(dest_root, recorded)
        )
    subtrees, planned_files, planned_bytes = plan_archive(path, evidence_root, tip)
    if not subtrees:
        return 0, 0
    try:
        os.makedirs(dest_root, exist_ok=True)
        if not recorded:
            with open(
                os.path.join(dest_root, SOURCE_MARKER), "w", encoding="utf-8"
            ) as handle:
                handle.write(os.path.abspath(path) + "\n")
        for relative in subtrees:
            shutil.copytree(
                os.path.join(path, relative),
                os.path.join(dest_root, relative),
                dirs_exist_ok=True,
                symlinks=True,
                ignore=ignore_links,
            )
    except (OSError, shutil.Error) as exc:
        raise ArchiveStop("copy failed: " + one_line(str(exc)))

    files = 0
    total = 0
    for relative in subtrees:
        source = os.path.join(path, relative)
        dest = os.path.join(dest_root, relative)
        try:
            source_files, source_bytes = measure_tree(source)
            dest_files, dest_bytes = measure_tree(dest)
        except OSError as exc:
            raise ArchiveStop("cannot count {}: {}".format(relative, one_line(str(exc))))
        if (source_files, source_bytes) != (dest_files, dest_bytes):
            raise ArchiveStop(
                "{}: source files={} bytes={}, archive files={} bytes={}".format(
                    relative, source_files, source_bytes, dest_files, dest_bytes
                )
            )
        files += source_files
        total += source_bytes
    if (files, total) != (planned_files, planned_bytes):
        raise ArchiveStop(
            "the source changed during the copy: planned files={} bytes={}, copied"
            " files={} bytes={}".format(planned_files, planned_bytes, files, total)
        )
    return files, total


def run_in_flight(path):
    """Name of the run log of a run still in flight, or empty when nothing is running.

    Only the run's own `<tag>.log` counts. lockrun also writes per-phase logs, `<tag>.launch.log`
    and `<tag>.phase1.log`, which never get a `.done` of their own, so a stem carrying a further
    dot segment is a phase log and is skipped: it would mark a finished lane in flight forever.
    """
    lockrun = os.path.join(path, "build", "lockrun")
    if not os.path.isdir(lockrun):
        return ""
    try:
        names = sorted(os.listdir(lockrun))
    except OSError:
        return ""
    for name in names:
        if not name.endswith(".log"):
            continue
        stem = name[: -len(".log")]
        if not stem or "." in stem:
            continue
        if not os.path.exists(os.path.join(lockrun, stem + ".done")):
            return name
    return ""


def held_locks(lock_root):
    """Every `*.lock.d` mutex directory under the lock root, sorted, not only gradle's.

    An empty lock root is no mutex belt at all, never the current directory.
    """
    if not lock_root:
        return []
    try:
        names = sorted(os.listdir(lock_root))
    except OSError:
        return []
    return [
        name
        for name in names
        if name.endswith(".lock.d") and os.path.isdir(os.path.join(lock_root, name))
    ]


def dirty_count(path):
    """Number of `git status --porcelain` lines: uncommitted or untracked files."""
    code, out, err = git(["-C", path, "status", "--porcelain"])
    if code != 0:
        return -1, one_line(out, err)
    return len([line for line in out.splitlines() if line.strip()]), ""


def commit_date(repo, sha):
    """Committer date as an integer, used only to remove the oldest lanes first."""
    if not sha:
        return 0
    code, out, _ = git(["-C", repo, "show", "-s", "--format=%ct", sha])
    if code != 0:
        return 0
    try:
        return int(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def classify(repo, main_ref, record, kept):
    """Exactly one class per worktree, with the detail its line prints."""
    path = record["path"]
    if not os.path.isdir(path):
        return "MISSING", record["branch"] or "-"
    if norm(path) in kept:
        return "KEPT", record["branch"] or "-"
    if record["bare"] or record["detached"] or not record["branch"]:
        return "DETACHED-OR-BARE", "bare" if record["bare"] else "detached at " + record["head"][:12]
    running = run_in_flight(path)
    if running:
        return "IN-FLIGHT", "{} {}".format(record["branch"], running)
    code, _, _ = git(["-C", repo, "merge-base", "--is-ancestor", record["head"], main_ref])
    if code != 0:
        return "NOT-LANDED", "{} {}".format(record["branch"], record["head"][:12])
    count, problem = dirty_count(path)
    if count != 0:
        detail = "files={}".format(count) if count > 0 else "status failed: " + problem
        return "DIRTY", "{} {}".format(record["branch"], detail)
    return "REMOVE", "{} {}".format(record["branch"], record["head"][:12])


def default_evidence_root(repo):
    """EVIDENCE_ROOT, else `{repo_parent}/evidence`, the kit's default evidence spec."""
    return os.environ.get("EVIDENCE_ROOT") or os.path.join(
        os.path.dirname(os.path.abspath(repo)), "evidence"
    )


def recheck(path, lock_root):
    """What changed between classification and this removal, empty when nothing did.

    Classification of two hundred worktrees takes seconds and the removals that follow take
    minutes, so every belt is read again for this worktree, right before its own removal.
    """
    locks = held_locks(lock_root)
    if locks:
        return "a mutex is held under {}: {}".format(lock_root, ", ".join(locks))
    running = run_in_flight(path)
    if running:
        return "a run is in flight: " + running
    count, problem = dirty_count(path)
    if count > 0:
        return "the worktree is dirty now: files={}".format(count)
    if count < 0:
        return "status failed: " + problem
    return ""


def sweep(args, after_archive=None):
    """Classify, and with --apply remove, every worktree of the repository.

    `after_archive` is a seam, not a feature: a callable given the worktree path right after
    its `ARCHIVED` line, so a test can make the world change inside the exact window the
    second re-check covers. Nothing on the command line can set it.
    """
    repo = args.repo
    if not args.evidence_root:
        args.evidence_root = default_evidence_root(repo)
    code, _, err = git(["-C", repo, "rev-parse", "--verify", args.main])
    if code != 0:
        print("REFUSED {} has no ref {}: {}".format(repo, args.main, one_line(err)))
        return 4
    records, problem = parse_worktrees(repo)
    if records is None:
        print("REFUSED cannot list worktrees of {}: {}".format(repo, problem))
        return 4

    kept = {norm(path) for path in args.keep}
    registered = {norm(record["path"]) for record in records}
    unmatched = [path for path in args.keep if norm(path) not in registered]
    main_checkout = norm(records[0]["path"]) if records else norm(repo)
    buckets = {name: [] for name in CLASS_ORDER}
    removable = []
    for record in records:
        if norm(record["path"]) in (main_checkout, norm(repo)):
            continue
        name, detail = classify(repo, args.main, record, kept)
        buckets[name].append((record["path"], detail))
        if name == "REMOVE":
            removable.append(
                (commit_date(repo, record["head"]), record["path"], detail)
            )
    removable.sort(key=lambda item: (item[0], norm(item[1])))

    if not args.apply:
        for name in CLASS_ORDER:
            for path, detail in buckets[name]:
                print("{} {} {}".format(name, path, detail))
        archive_files = 0
        archive_bytes = 0
        for _, path, _ in removable:
            try:
                tip = worktree_tip(path)
                _, files, total = plan_archive(path, args.evidence_root, tip)
            except ArchiveStop as exc:
                print("WOULD-STOP {}: archive not verified: {}".format(path, exc))
                continue
            archive_files += files
            archive_bytes += total
            print(
                "WOULD-ARCHIVE {} leaf={} files={} bytes={}".format(
                    path, archive_leaf_name(path, tip), files, total
                )
            )
        for path in unmatched:
            print("WARNING keep matched no worktree: {}".format(path))
        print(
            "SWEEP dry: remove={} dirty={} not-landed={} in-flight={} kept={} other={}"
            " unmatched-keeps={} archive-files={} archive-bytes={}".format(
                len(buckets["REMOVE"]),
                len(buckets["DIRTY"]),
                len(buckets["NOT-LANDED"]),
                len(buckets["IN-FLIGHT"]),
                len(buckets["KEPT"]),
                len(buckets["DETACHED-OR-BARE"]) + len(buckets["MISSING"]),
                len(unmatched),
                archive_files,
                archive_bytes,
            )
        )
        return 0

    if unmatched:
        for path in unmatched:
            print("REFUSED keep matched no worktree: {}".format(path))
        print("SWEEP refused: unmatched-keeps={}".format(len(unmatched)))
        return 5

    locks = held_locks(args.lock_root)
    if locks:
        print(
            "REFUSED a mutex is held under {}: {}".format(args.lock_root, ", ".join(locks))
        )
        return 3

    removed = 0
    stopped = 0
    skipped = 0
    for _, path, detail in removable[: max(args.limit, 0)]:
        changed = recheck(path, args.lock_root)
        if changed:
            print("SKIPPED {}: {}".format(path, changed))
            skipped += 1
            continue
        try:
            files, total = archive_worktree(path, args.evidence_root)
        except ArchiveStop as exc:
            print("STOPPED {}: archive not verified: {}".format(path, exc))
            stopped = 1
            break
        print("ARCHIVED {} files={} bytes={}".format(path, files, total))
        if after_archive is not None:
            after_archive(path)
        # The archive is the longest step of the run: a walk, a copy and two count passes,
        # up to a minute on a large worktree. A gate that starts in this worktree during
        # that minute takes its mutex and writes its log after the first re-check has read
        # them, so every belt is read once more here, in the window the copy opened. A
        # change leaves the worktree on disk with its archive beside it, which is harmless.
        changed = recheck(path, args.lock_root)
        if changed:
            print("SKIPPED {}: {}".format(path, changed))
            skipped += 1
            continue
        code, out, err = git(["-C", repo, "worktree", "remove", path], timeout=None)
        if code != 0:
            print("STOPPED {}: {}".format(path, one_line(out, err)))
            stopped = 1
            break
        removed += 1
        print("REMOVED {} {}".format(path, detail))
    print(
        "SWEEP applied: removed={} stopped={} left={} skipped={}".format(
            removed, stopped, len(removable) - removed, skipped
        )
    )
    return 2 if stopped else 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="List, and with --apply remove, the worktrees of landed lanes."
    )
    parser.add_argument("--repo", default=".", help="repository to sweep, the current one by default")
    parser.add_argument("--main", default="main", help="ref a landed branch is an ancestor of")
    parser.add_argument(
        "--keep", action="append", default=[], help="worktree path to keep whatever its state"
    )
    parser.add_argument(
        "--lock-root", default="", help="folder of *.lock.d mutex directories; empty: no mutex belt"
    )
    parser.add_argument(
        "--evidence-root",
        default=None,
        help="root the build subtrees are archived under, in worktree-archive; default"
        " EVIDENCE_ROOT, else {repo_parent}/evidence",
    )
    parser.add_argument("--limit", type=int, default=5, help="remove at most N in one run")
    parser.add_argument("--apply", action="store_true", help="remove instead of only listing")
    return parser


def main(argv):
    args = build_parser().parse_args(argv)
    return sweep(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
