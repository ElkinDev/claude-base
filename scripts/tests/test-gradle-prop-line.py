"""Pins for scripts/gradle-prop-line.py (a daemon tuning change: the Kotlin daemon on G1). Every case runs on a stub
gradle.properties in a temp folder whose name has a space; the stub carries fake secret lines that must never reach the
output. No real gradle file is read.

    python test-gradle-prop-line.py
    GRADLE_PROP_LINE=<path> python test-gradle-prop-line.py
"""
import contextlib
import glob
import importlib.machinery
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

SCRIPT = os.environ.get("GRADLE_PROP_LINE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gradle-prop-line.py")
SECRET = "FAKE-SECRET-7731"
OLD = "-Xmx4g -Dfile.encoding=UTF-8"
NEW = "-Xmx4g -Dfile.encoding=UTF-8 -XX:+UseG1GC -XX:G1PeriodicGCInterval=60000 -XX:MinHeapFreeRatio=10 -XX:MaxHeapFreeRatio=30"
KEY = "kotlin.daemon.jvmargs"


def stub(eol, trailing=True):
    lines = ["# user gradle file", "RELEASE_STORE_PASSWORD=" + SECRET, "org.gradle.caching=true",
             KEY + "=" + OLD, "kotlin.daemon.jvmargs.extra=not-this-one", "RELEASE_KEY_PASSWORD=" + SECRET]
    return (eol.join(lines) + (eol if trailing else "")).encode("utf-8")


class GradlePropLineTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="prop line ")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.file = os.path.join(self.root, "gradle.properties")

    def write(self, data):
        with open(self.file, "wb") as f:
            f.write(data)

    def run_it(self, *extra, expect=OLD, value=NEW, key=KEY):
        r = subprocess.run([sys.executable, SCRIPT, "--file", self.file, "--key", key, "--expect=" + expect, "--value=" + value] + list(extra),
                           capture_output=True, text=True, timeout=60)
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        return r

    def backups(self):
        return glob.glob(self.file + ".*.bak")

    def test_one_line_changes_and_every_other_byte_stays(self):
        for eol, trailing in (("\r\n", True), ("\n", True), ("\n", False)):
            self.write(stub(eol, trailing))
            before = stub(eol, trailing)
            r = self.run_it("--keep-backup")
            self.assertEqual(r.returncode, 0, r.stdout)
            after = open(self.file, "rb").read()
            self.assertEqual(after, before.replace((KEY + "=" + OLD).encode(), (KEY + "=" + NEW).encode()))
            self.assertEqual(len(self.backups()), 1)
            self.assertEqual(open(self.backups()[0], "rb").read(), before)
            self.assertIn("new %s=%s" % (KEY, NEW), r.stdout)
            for b in self.backups():
                os.remove(b)

    def test_the_swapped_call_reverts(self):
        self.write(stub("\r\n"))
        self.assertEqual(self.run_it().returncode, 0)
        r = self.run_it(expect=NEW, value=OLD)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(open(self.file, "rb").read(), stub("\r\n"))

    def test_an_unexpected_current_value_is_refused_and_nothing_written(self):
        self.write(stub("\n"))
        r = self.run_it(expect="-Xmx2g")
        self.assertEqual(r.returncode, 3)
        self.assertIn("not the expected value", r.stdout)
        self.assertEqual((open(self.file, "rb").read(), self.backups()), (stub("\n"), []))

    def test_a_missing_or_doubled_key_is_refused(self):
        self.write(stub("\n").replace(KEY.encode() + b"=", b"#" + KEY.encode() + b"="))
        self.assertEqual(self.run_it().returncode, 3)
        self.write(stub("\n") + (KEY + "=" + OLD + "\n").encode())
        r = self.run_it()
        self.assertEqual(r.returncode, 3)
        self.assertIn("2 lines start with", r.stdout)
        self.assertEqual(self.backups(), [])

    def test_already_at_the_value_and_a_dry_run_write_nothing(self):
        self.write(stub("\n").replace(OLD.encode(), NEW.encode(), 1))
        r = self.run_it()
        self.assertEqual((r.returncode, "already" in r.stdout, self.backups()), (0, True, []))
        self.write(stub("\n"))
        r = self.run_it("--dry-run")
        self.assertEqual((r.returncode, "dry run" in r.stdout, self.backups()), (0, True, []))
        self.assertEqual(open(self.file, "rb").read(), stub("\n"))

    def test_hostile_arguments_are_refused(self):
        self.write(stub("\n"))
        for kw, code in ((dict(value="a\nRELEASE=x"), 3), (dict(expect=OLD + "\r"), 3), (dict(value="   "), 2),
                         (dict(key="  "), 2), (dict(key="a=b"), 2), (dict(key="RELEASE_STORE_PASSWORD"), 2),
                         (dict(key="release_key_password"), 2), (dict(key="SIGNING_KEY_PW"), 2),
                         (dict(key="GITHUB_PAT"), 2), (dict(key="MAPS_API_KEY"), 2), (dict(key="RELEASE_KEY_ALIAS"), 2),
                         (dict(key="jalapeño"), 2)):
            r = self.run_it(**kw)
            self.assertEqual(r.returncode, code, (kw, r.stdout, r.stderr))
        self.assertEqual((open(self.file, "rb").read(), self.backups()), (stub("\n"), []))

    @unittest.skipIf(getattr(os, "geteuid", lambda: 1)() == 0, "root writes a read-only file")
    def test_a_read_only_file_is_reported_not_a_traceback(self):
        self.write(stub("\n"))
        os.chmod(self.file, 0o444)
        self.addCleanup(os.chmod, self.file, 0o666)
        r = self.run_it()
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("WRITE FAILED", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("nothing changed", r.stdout)
        self.assertEqual(open(self.file, "rb").read(), stub("\n"))  # a refused open writes no byte
        self.assertEqual(self.backups(), [])  # nothing written, so the read-only copy of the file goes too

    def test_an_apply_and_its_revert_in_one_second_keep_both_backups(self):
        self.write(stub("\n"))
        self.assertEqual(self.run_it("--keep-backup").returncode, 0)
        self.assertEqual(self.run_it("--keep-backup", expect=NEW, value=OLD).returncode, 0)
        self.assertEqual(len(self.backups()), 2)

    def test_a_passed_read_back_removes_the_backup_by_default(self):
        # a copy of a password file is not left behind
        self.write(stub("\r\n"))
        r = self.run_it()
        self.assertEqual((r.returncode, "backup removed" in r.stdout, self.backups()), (0, True, []))

    def test_a_continued_line_or_value_is_refused(self):
        # a trailing backslash continues a properties line onto the next
        self.write(stub("\n").replace(OLD.encode(), OLD.encode() + b" \\"))
        self.assertEqual(self.run_it(expect=OLD + " \\").returncode, 3)
        self.write(stub("\n"))
        for kw in (dict(value=NEW + " \\"), dict(expect=OLD + "\\")):
            self.assertEqual(self.run_it(**kw).returncode, 3, kw)
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.run_it(value=NEW + " \\\\").returncode, 0)  # an escaped backslash ends the line

    def test_iso_8859_1_bytes_are_read_and_a_value_outside_it_refused(self):
        # gradle.properties is ISO-8859-1; a 0xE9 byte once crashed the utf-8 decode
        self.write(stub("\n").replace(OLD.encode(), b"-Xmx4g -Dx=caf\xe9"))
        r = self.run_it()
        self.assertEqual(r.returncode, 3)
        self.assertNotIn("Traceback", r.stderr)
        self.write(stub("\n"))
        self.assertEqual(self.run_it(value=NEW + " €").returncode, 3)

    def test_an_empty_file_is_refused(self):
        self.write(b"")
        self.assertEqual(self.run_it().returncode, 3)

    def test_a_missing_file_is_a_usage_error(self):
        r = self.run_it()
        self.assertEqual(r.returncode, 2)
        self.assertIn("cannot read", r.stdout)

    def in_process(self, patches):
        """main() in this process with functions patched, for the cases a subprocess cannot reach; a target "mod" is the
        loaded script itself."""
        loader = importlib.machinery.SourceFileLoader("gradle_prop_line", SCRIPT)
        mod = types.ModuleType(loader.name)
        loader.exec_module(mod)
        argv = [SCRIPT, "--file", self.file, "--key", KEY, "--expect=" + OLD, "--value=" + NEW]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            with contextlib.ExitStack() as stack:
                for target, name, fn in patches:
                    own = target == "mod"
                    stack.enter_context(mock.patch.object(mod if own else target, name, fn, create=own))
                code = mod.main()
        self.assertNotIn(SECRET, out.getvalue())
        return code, out.getvalue()

    def test_a_write_by_someone_else_after_the_read_is_never_overwritten(self):
        # a line appended between the read and the write was once lost with its backup
        self.write(stub("\n"))
        real_copy = shutil.copy2

        def copy_then_append(src, dst):
            real_copy(src, dst)
            with open(self.file, "ab") as f:
                f.write(b"added.by.another=1\n")
        code, out = self.in_process([(shutil, "copy2", copy_then_append)])
        self.assertEqual(code, 3, out)
        self.assertIn("changed since it was read", out)
        self.assertEqual(open(self.file, "rb").read(), stub("\n") + b"added.by.another=1\n")
        self.assertEqual(self.backups(), [])

    def test_a_backup_that_cannot_be_removed_is_named_with_exit_5(self):
        # an os.remove failure after a passed read-back was once a traceback and exit 1
        self.write(stub("\r\n"))

        def held(path):
            raise PermissionError(13, "held by another process")
        code, out = self.in_process([(os, "remove", held)])
        self.assertEqual(code, 5, out)
        self.assertIn("BACKUP WAS NOT REMOVED", out)
        self.assertEqual(len(self.backups()), 1)
        self.assertIn(os.path.basename(self.backups()[0]), out)
        self.assertEqual(open(self.file, "rb").read(), stub("\r\n").replace(OLD.encode(), NEW.encode()))

    def test_a_refusal_whose_backup_cannot_be_removed_names_it(self):
        self.write(stub("\n"))
        real_copy = shutil.copy2

        def copy_then_append(src, dst):
            real_copy(src, dst)
            with open(self.file, "ab") as f:
                f.write(b"added.by.another=1\n")

        def held(path):
            raise PermissionError(13, "held by another process")
        code, out = self.in_process([(shutil, "copy2", copy_then_append), (os, "remove", held)])
        self.assertEqual(code, 3, out)
        self.assertEqual(len(self.backups()), 1)
        self.assertIn("delete " + self.backups()[0].replace("\\", "/"), out)

    def test_a_write_that_fails_midway_keeps_the_backup(self):
        # an OSError after the first byte may have reached the file is never reported as nothing changed
        self.write(stub("\n"))
        real_open = open

        class Breaks:
            def __init__(self, f):
                self.f = f

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.f.close()
                return False

            def read(self, *a):
                return self.f.read(*a)

            def seek(self, *a):
                return self.f.seek(*a)

            def truncate(self, *a):
                return self.f.truncate(*a)

            def write(self, data):
                self.f.write(data[:10])
                raise OSError(28, "No space left on device")

        def breaking(path, mode="r", *rest, **kw):
            h = real_open(path, mode, *rest, **kw)
            return Breaks(h) if path == self.file and mode == "r+b" else h
        code, out = self.in_process([("mod", "open", breaking)])
        self.assertEqual(code, 4, out)
        self.assertIn("may be partly written", out)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(open(self.backups()[0], "rb").read(), stub("\n"))

    def test_a_read_back_that_cannot_open_keeps_and_names_the_backup(self):
        self.write(stub("\n"))
        real_open, reads = open, []

        def flaky(path, mode="r", *rest, **kw):
            if path == self.file and mode == "rb":
                reads.append(path)
                if len(reads) == 2:  # the first read, then the read-back
                    raise PermissionError(13, "locked by another process")
            return real_open(path, mode, *rest, **kw)
        code, out = self.in_process([("mod", "open", flaky)])
        self.assertEqual(code, 4, out)
        self.assertIn("READ-BACK FAILED", out)
        self.assertEqual(len(self.backups()), 1)
        self.assertIn(self.backups()[0].replace("\\", "/"), out)
        self.assertEqual(open(self.backups()[0], "rb").read(), stub("\n"))


if __name__ == "__main__":
    unittest.main()
