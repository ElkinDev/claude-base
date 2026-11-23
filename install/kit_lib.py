"""kit_lib.py - the safe-adoption engine install.py runs on, for macOS and Linux (F13).

The Python twin of install/lib.ps1 and install/backup.ps1: the same contract, the same files on
disk, so scripts/kit-restore.py rolls back a run of either installer.

The contract in one paragraph: the installer knows which files it wrote, because it records a hash
per file in the managed-file manifest at <kit home>/.kit-manifest.json. On every later run it
compares what is on disk with that record. A file it wrote and nobody touched is refreshed (after a
backup). A file that differs from the record, or that the manifest never heard of, is somebody
else's file: it is backed up, kept exactly as it is, and the kit version lands beside it as
<name>.new, which is itself governed by the same rule. Nothing is ever deleted, and every overwrite
leaves a copy under <kit home>/backups/<stamp>/ that scripts/kit-restore.py can put back.

The backup record: one run, one folder, <kit home>/backups/<stamp>/. Inside it, a copy of every
file the run replaced or kept, filed under the absolute path it came from, and manifest.txt listing
what happened in the order it happened. Each line is appended and flushed before the write it
describes, so a run that dies halfway still leaves a folder kit-restore.py can read and put back.
The folder is created on the first line, so a run that changes nothing leaves nothing behind.
"""
import datetime
import hashlib
import json
import os
import shutil
import stat

MANIFEST_VERSION = 1


# ---------------------------------------------------------------- paths and hashing

def kit_home():
    """KIT_HOME lets a test (or a second engine) point the whole install somewhere else.
    scripts/kit-restore.py reads the same variable, so the two always agree on the home."""
    from_env = os.environ.get("KIT_HOME", "").strip()
    if from_env:
        return os.path.abspath(os.path.expanduser(from_env))
    return os.path.join(os.path.expanduser("~"), ".claude")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    """The hash of a file, or None when it is not a readable regular file."""
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def sha256_text(text):
    # The bytes a rendered text is written as: UTF-8 with no BOM, line endings as given.
    return sha256_bytes(text.encode("utf-8"))


def read_text(path):
    """A text file as written, line endings kept and a BOM dropped, the way Get-Content -Raw reads it."""
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return handle.read()


def write_text_no_bom(path, text):
    """UTF-8 with no BOM: a BOM in front of JSON is rejected by Node's parser."""
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def is_script(path):
    """A file that starts with a shebang runs as a command, so its copy has to be executable. git
    keeps most of this repository's scripts at mode 100644 (they were committed from Windows), so
    the mode in the clone says nothing; the first two bytes do."""
    try:
        with open(path, "rb") as handle:
            return handle.read(2) == b"#!"
    except OSError:
        return False


def make_executable(path):
    mode = os.stat(path).st_mode
    os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ---------------------------------------------------------------- the backup record

class Backup(object):
    """The record of one run. Lines go to manifest.txt before the write they describe."""

    def __init__(self):
        self.root = None
        self.trigger = ""
        self.open = False

    def start(self, root, trigger):
        self.root = root
        self.trigger = trigger
        self.open = False

    def mirror_path(self, path):
        # /Users/you/.claude/x.md becomes <backup root>/Users/you/.claude/x.md, the layout
        # scripts/kit_restore_report.py mirror_path() reads back.
        full = os.path.abspath(path)
        return os.path.join(self.root, full.lstrip("/\\"))

    def line(self, text):
        manifest = os.path.join(self.root, "manifest.txt")
        if not self.open:
            os.makedirs(self.root, exist_ok=True)
            with open(manifest, "a", encoding="utf-8", newline="") as handle:
                handle.write("trigger %s\n" % self.trigger)
                handle.flush()
                os.fsync(handle.fileno())
            self.open = True
        with open(manifest, "a", encoding="utf-8", newline="") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def copy(self, path):
        """The copy is of the bytes only: a restore gives the file back with default permissions."""
        dest = self.mirror_path(path)
        folder = os.path.dirname(dest)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        shutil.copyfile(path, dest)

    def folder(self):
        return self.root if self.open else None


def new_stamp(home):
    """The name of this run, never one a folder already carries: two runs inside the same second
    would share a backup folder, and the second would copy its own version of a file over the one
    the first had saved there."""
    base = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    stamp, n = base, 1
    while os.path.exists(os.path.join(home, "backups", stamp)):
        n += 1
        stamp = "%s-%d" % (base, n)
    return stamp


# ---------------------------------------------------------------- managed-file manifest

class Manifest(object):
    """The installer's record: absolute path -> sha256 of what it wrote there.

    Broken means anything that is not this engine's map of absolute path to hash: bad syntax, an
    empty file, an array, a missing or unknown version, a "files" that is not an object, a value
    that is not a hash, a key that is not an absolute path. All of them degrade to "nothing is
    managed", the safe reading (every existing file then counts as somebody else's), and the caller
    says so, because a record that says nothing is a record that was lost."""

    def __init__(self, path):
        self.path = path
        self.files = {}
        self.broken = False
        self.files = self._read()

    def _read(self):
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8-sig") as handle:
                text = handle.read().strip()
            if not text:
                raise ValueError("the record is empty")
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("the record is not an object")
            version = data.get("version")
            if isinstance(version, bool) or not isinstance(version, int) or version != MANIFEST_VERSION:
                raise ValueError("not a version we know")
            files = data.get("files")
            if not isinstance(files, dict):
                raise ValueError("the record has no file map")
            out = {}
            for key, value in files.items():
                if not isinstance(value, str):
                    raise ValueError("a file is recorded without a hash")
                # The record keeps whole paths only. A Windows record read here is not one this
                # engine wrote, and none of its keys can name a file on this machine.
                if not key.startswith("/"):
                    raise ValueError("a file has no absolute path")
                out[key] = value
            return out
        except (OSError, ValueError):
            self.broken = True
            return {}

    def get(self, path):
        return self.files.get(path)

    def set(self, path, digest):
        self.files[path] = digest

    def move_broken_aside(self, stamp):
        """Renamed, never deleted: the adopter can still read what was in it."""
        if not os.path.isfile(self.path):
            return None
        dest = "%s.broken-%s" % (self.path, stamp)
        os.replace(self.path, dest)
        return dest

    def changed(self):
        old = Manifest(self.path)
        return old.files != self.files

    def write(self):
        """Only when the file map actually moved: rewriting it on a no-op run would change the
        timestamp and make an idempotent re-run look like a write."""
        if not self.changed():
            return False
        doc = {
            "version": MANIFEST_VERSION,
            "installed": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "files": dict(sorted(self.files.items())),
        }
        write_text_no_bom(self.path, json.dumps(doc, indent=4) + "\n")
        return True


# ---------------------------------------------------------------- the ownership rule

class Pair(object):
    """One file to install: copied from `source`, or rendered from `text` when text is not None."""

    def __init__(self, source, target, text=None):
        self.source = source
        self.target = target
        self.text = text

    def __repr__(self):
        return "Pair(%r -> %r)" % (self.source, self.target)


def plan_line(verb, rest):
    # The verbs are padded to one column, the layout install.ps1 prints, so the two plans read alike.
    return "%-12s %s" % (verb, rest)


class Engine(object):
    """Decides and executes one file at a time against the manifest and the backup record."""

    def __init__(self, manifest, backup, force=False, dry_run=False):
        self.manifest = manifest
        self.backup = backup
        self.force = force
        self.dry_run = dry_run
        self.owned = []

    def add_owned(self, path):
        """Every path this run manages, which is what --local-only excludes. A file of the
        adopter's is never in here; the proposal beside it is."""
        if path not in self.owned:
            self.owned.append(path)

    def resolve(self, target, source_hash, force=None):
        force = self.force if force is None else force
        if not os.path.isfile(target):
            return "write"
        current = sha256_file(target)
        if current == source_hash:
            return "same"
        recorded = self.manifest.get(target)
        if recorded and recorded == current:
            return "refresh"
        if force:
            return "force"
        return "new"

    @staticmethod
    def write_content(target, pair):
        folder = os.path.dirname(target)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        # An explicit None test, not truthiness: a rendered file that came out empty is still a
        # rendered file.
        if pair.text is not None:
            write_text_no_bom(target, pair.text)
        else:
            # Into a temp file and renamed over the target: a file being replaced is never left
            # half written, and a link at the target is replaced, never written through.
            temp = target + ".kit-tmp"
            shutil.copyfile(pair.source, temp)
            os.replace(temp, target)
        source = pair.source if pair.source else target
        if pair.text is None and is_script(source):
            make_executable(target)

    def source_hash(self, pair):
        if pair.text is not None:
            return sha256_text(pair.text)
        return sha256_file(pair.source)

    def proposal(self, pair, source_hash):
        """The target belongs to the adopter, so it is never written. The kit version goes beside
        it as <name>.new, and that proposal obeys the same ownership rule: one the kit wrote is
        refreshed, one the adopter has been editing is backed up first and named in the output."""
        target = pair.target
        proposal = target + ".new"
        self.add_owned(proposal)
        kept = plan_line("backup+new", "%s (yours is kept, the kit version is at %s)" % (target, proposal))
        action = self.resolve(proposal, source_hash, force=False)
        if action == "same":
            # The proposal already holds the kit's bytes, so it goes back on the record too: a lost
            # record is repaired by the first run after it.
            if not self.dry_run:
                self.manifest.set(proposal, source_hash)
            return [plan_line("kept", "%s (yours, the kit version is already at %s)" % (target, proposal))]
        if not self.dry_run:
            self.backup.copy(target)
            self.backup.line("kept %s" % target)
            if action == "write":
                self.backup.line("new %s" % proposal)
            else:
                self.backup.copy(proposal)
                self.backup.line("overwritten %s" % proposal)
            self.write_content(proposal, pair)
            self.manifest.set(proposal, source_hash)
        if action == "new":
            return [kept, plan_line("draft", "%s (your own draft is in the backup, the kit version replaced it)" % proposal)]
        return [kept]

    def set_file(self, pair):
        """One file, decided and executed. Returns the plan lines, or [] when the source is
        missing. Under dry_run it returns the same lines and writes nothing at all."""
        source_hash = self.source_hash(pair)
        if not source_hash:
            return []
        target = pair.target
        action = self.resolve(target, source_hash)
        if action == "same":
            # Byte for byte the kit's version, so it is the kit's file whatever the record says.
            # When the record lost it, this puts it back, or the rollback of the run that installed
            # it would find nothing it was allowed to remove. The backup is not touched.
            if not self.dry_run:
                self.manifest.set(target, source_hash)
                if pair.text is None and is_script(pair.source) and not os.access(target, os.X_OK):
                    make_executable(target)
            self.add_owned(target)
            return [plan_line("skip same", target)]
        if action == "new":
            return self.proposal(pair, source_hash)

        self.add_owned(target)
        if not self.dry_run:
            if action == "write":
                self.backup.line("created %s" % target)
            else:
                self.backup.copy(target)
                self.backup.line("overwritten %s" % target)
            self.write_content(target, pair)
            self.manifest.set(target, source_hash)
        if action == "force":
            return [plan_line("force", "%s (your version is in the backup)" % target)]
        if action == "refresh":
            return [plan_line("refresh", target)]
        return [plan_line("write", target)]


def missing_dirs(pairs):
    """Read before anything is written, so a dry run can name the folders a real run would create."""
    seen = set()
    for pair in pairs:
        folder = os.path.dirname(pair.target)
        while folder and folder not in seen and not os.path.exists(folder):
            seen.add(folder)
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
    return sorted(seen)
