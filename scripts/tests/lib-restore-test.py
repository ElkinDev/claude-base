"""Shared fixture for the scripts/kit-restore.py suites.

Nothing real is touched: every case builds a throwaway kit home under TEMP, points KIT_HOME at it,
and writes the backup folders by hand in the layout install.ps1 produces (a manifest.txt of verbs
plus the replaced files mirrored under their absolute path). The everyday paths are in
test-kit-restore.py, what a rollback does when it meets a file it cannot touch is in
test-restore-failures.py, and the cases that drive the real installer are in
test-install-rollback.py."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "kit-restore.py")
spec = importlib.util.spec_from_file_location("kit_restore", SCRIPT)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def run_restore(argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = restore.main(argv)
    return code, buffer.getvalue()


class RestoreFixture(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="kit-restore-test-")
        self.home = os.path.join(self.base, "kit-home")
        self.backups = os.path.join(self.home, "backups")
        os.makedirs(self.backups)
        self.saved = os.environ.copy()
        os.environ["KIT_HOME"] = self.home
        self.managed = {}

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)
        shutil.rmtree(self.base, ignore_errors=True)

    def write(self, path, text):
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path

    def installed(self, relative, text):
        """A file the installer wrote, recorded in the managed-file manifest."""
        path = self.write(os.path.join(self.home, relative), text)
        self.managed[path] = sha256_text(text)
        return path

    def save_managed(self):
        self.write(os.path.join(self.home, ".kit-manifest.json"), json.dumps(
            {"version": 1, "installed": "2026-08-28T10:00:00", "files": self.managed}, indent=2))

    def backup(self, stamp, trigger, entries, copies=None):
        """One backup folder: `entries` are (verb, path), `copies` are path -> previous content."""
        stamp_dir = os.path.join(self.backups, stamp)
        os.makedirs(stamp_dir, exist_ok=True)
        for path, text in (copies or {}).items():
            self.write(str(restore.mirror_path(stamp_dir, path)), text)
        lines = ["trigger " + trigger] + ["%s %s" % (verb, path) for verb, path in entries]
        self.write(os.path.join(stamp_dir, "manifest.txt"), "\n".join(lines) + "\n")
        self.save_managed()
        return stamp_dir

    def tree(self, root):
        """Relative path plus content hash for every file under `root`, order independent."""
        out = {}
        for folder, _, files in os.walk(root):
            for name in files:
                full = os.path.join(folder, name)
                out[os.path.relpath(full, root)] = restore.sha256(full)
        return out

    def managed_now(self):
        return json.loads(read_text(os.path.join(self.home, ".kit-manifest.json")))["files"]
