"""Tests for scripts/kit-restore.py when the rollback cannot do what the record asks:
python scripts/tests/test-restore-failures.py

Two kinds of trouble live here. A file another program is holding, which the rollback must report
and step over instead of crashing or lying about it, and a run that died before it finished
recording, where the backup line is the only record there is. The everyday paths are in
test-kit-restore.py and the fixture both suites build on is in lib-restore-test.py."""
import contextlib
import ctypes
import importlib.util
import os
import stat
import unittest

try:
    import msvcrt
except ImportError:  # not Windows, and the lock cases below say so and skip
    msvcrt = None

MINE = "MY OWN VERSION, an afternoon of edits on top of what the kit installed."
BEFORE = "the version that was there before the kit ran"

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("lib_restore_test",
                                              os.path.join(HERE, "lib-restore-test.py"))
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)
restore, run_restore, read_text = lib.restore, lib.run_restore, lib.read_text
on_windows = unittest.skipUnless(os.name == "nt", "a real file lock, so Windows only")


@contextlib.contextmanager
def locked(path):
    """Hold `path` the way an editor or a running program holds it: the file stays exactly where
    it is, and for the length of the block nobody else can read it, write it or remove it."""
    span = max(os.path.getsize(path), 1)
    handle = open(path, "r+b")
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, span)
    try:
        yield path
    finally:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, span)
        handle.close()


@contextlib.contextmanager
def held_exclusively(path):
    """The other shape of the same trouble: a program that shares nothing, so opening the file at
    all is refused rather than the read. This is what an installer or a virus scanner holds."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateFileW(str(path), 0xC0000000, 0, None, 3, 0x80, None)
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise OSError("could not take a hold that shares nothing on " + str(path))
    try:
        yield path
    finally:
        kernel32.CloseHandle(handle)


@contextlib.contextmanager
def read_only(path):
    """The third shape: the file reads fine and refuses to be replaced. An adopter marks a file
    read-only on purpose, and a rename onto it is denied on Windows even though nothing holds it."""
    before = stat.S_IMODE(os.stat(path).st_mode)
    os.chmod(path, stat.S_IREAD)
    try:
        yield path
    finally:
        os.chmod(path, before)


class RestoreFailuresTest(lib.RestoreFixture):

    def rolled_back_while_held(self, hold, argv):
        """One rollback of a file the adopter edited after the install, run while `hold` holds it.
        Nothing about that file may be written: the guard could not read it, so it cannot know
        whose bytes are there. Returns the exit code, the output, and the bytes on disk after."""
        skill = self.installed("skills/audit/SKILL.md", "the kit version of this skill file")
        self.backup("20260828-100000", "user scope install",
                    [("overwritten", skill)], {skill: BEFORE})
        self.write(skill, MINE)
        with hold(skill):
            code, output = run_restore(["--stamp", "20260828-100000"] + argv)
        with open(skill, "rb") as handle:
            return code, output, handle.read()

    def assertStillTheirs(self, now, output):
        self.assertEqual(len(now), len(MINE.encode("utf-8")), output)
        self.assertEqual(now.decode("utf-8"), MINE, output)

    # a file the rollback cannot touch ------------------------------------

    @on_windows
    def test_a_file_held_by_a_byte_range_lock_is_never_emptied(self):
        code, output, now = self.rolled_back_while_held(locked, [])
        self.assertEqual(code, 1, output)
        self.assertIn("cannot read it to compare, pass --force to overwrite it", output)
        self.assertStillTheirs(now, output)

    @on_windows
    def test_force_over_a_file_held_by_a_byte_range_lock_never_empties_it(self):
        code, output, now = self.rolled_back_while_held(locked, ["--force"])
        self.assertEqual(code, 1, output)
        self.assertIn("locked or unreadable", output)
        self.assertStillTheirs(now, output)

    @on_windows
    def test_a_file_held_by_something_that_shares_nothing_is_never_emptied(self):
        code, output, now = self.rolled_back_while_held(held_exclusively, [])
        self.assertEqual(code, 1, output)
        self.assertIn("cannot read it to compare, pass --force to overwrite it", output)
        self.assertStillTheirs(now, output)

    @on_windows
    def test_force_over_a_file_that_shares_nothing_never_empties_it(self):
        code, output, now = self.rolled_back_while_held(held_exclusively, ["--force"])
        self.assertEqual(code, 1, output)
        self.assertIn("locked or unreadable", output)
        self.assertStillTheirs(now, output)

    @on_windows
    def test_a_read_only_target_is_skipped_and_leaves_no_temp_file_behind(self):
        """The rename onto a read-only file is refused, so the entry is skipped like any other
        write that cannot finish. The temp file it filled has to go with it: a rollback run twice
        against the same file must not leave two .kit-restore- files in the adopter's folder."""
        skill = self.installed("skills/audit/SKILL.md", "kit version")
        self.backup("20260828-100000", "user scope install",
                    [("overwritten", skill)], {skill: BEFORE})
        folder = os.path.dirname(skill)
        with read_only(skill):
            run_restore(["--stamp", "20260828-100000"])
            code, output = run_restore(["--stamp", "20260828-100000"])
            left = sorted(n for n in os.listdir(folder) if n.startswith(".kit-restore-"))
        self.assertEqual(code, 1, output)
        self.assertIn("skipped      %s (locked or unreadable)" % skill, output)
        self.assertEqual(left, [], "the rollback left its temp file in the adopter's folder")
        self.assertEqual(read_text(skill), "kit version")

    @on_windows
    def test_a_created_file_held_by_another_program_is_skipped_not_called_gone(self):
        hook = self.installed("hooks/landing.py", "print('hi')")
        self.backup("20260828-100000", "user scope install", [("created", hook)])
        with locked(hook):
            code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 1, output)
        self.assertIn("skipped      %s (locked or unreadable)" % hook, output)
        self.assertNotIn("gone", output)
        self.assertTrue(os.path.exists(hook))
        self.assertIn(hook, self.managed_now())

    @on_windows
    def test_a_held_overwritten_file_is_skipped_and_the_rest_still_runs(self):
        skill = self.installed("skills/audit/SKILL.md", "kit version")
        created = self.installed("statusline.ps1", "kit statusline")
        self.backup("20260828-100000", "user scope install",
                    [("overwritten", skill), ("created", created)], {skill: "yours"})
        with locked(skill):
            code, output = run_restore(["--stamp", "20260828-100000", "--force"])
        self.assertEqual(code, 1, output)
        self.assertIn("skipped      %s (locked or unreadable)" % skill, output)
        self.assertIn("1 skipped", output)
        self.assertFalse(os.path.exists(created), "the entries after the held one still ran")
        self.assertIn(skill, self.managed_now())
        # Stepped over means the bytes are still there. Nothing may be opened for writing until
        # the run knows it can finish the write.
        self.assertEqual(read_text(skill), "kit version", "the held file was written to")

    # a run that died before it recorded ----------------------------------

    def test_a_run_killed_before_it_recorded_still_puts_the_file_back(self):
        """A run killed from outside leaves the backup lines it had written and a kit manifest
        that never heard of the file. The line is then the only record that the kit replaced it,
        and it is enough: the copy beside it is the version that was there before."""
        skill = self.write(os.path.join(self.home, "skills/audit/SKILL.md"), "kit version")
        self.backup("20260828-100000", "user scope install",
                    [("overwritten", skill)], {skill: "the version I wrote myself"})
        self.assertNotIn(skill, self.managed_now())
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 0, output)
        self.assertIn("restored", output)
        self.assertEqual(read_text(skill), "the version I wrote myself")

    def test_a_created_file_the_record_never_got_goes_but_a_copy_stays(self):
        """Same half finished run, seen from the other verb: the file was not there before, so
        the rollback takes it away, and because nothing on record proves whose bytes those are
        it copies the file into the backup folder before removing it."""
        stray = self.write(os.path.join(self.home, "statusline.ps1"), "kit statusline")
        stamp_dir = self.backup("20260828-100000", "user scope install", [("created", stray)])
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 0, output)
        self.assertIn("removed      %s (was not on the record; copy kept at %s)"
                      % (stray, restore.mirror_path(stamp_dir, stray)), output)
        self.assertFalse(os.path.exists(stray))
        self.assertEqual(read_text(str(restore.mirror_path(stamp_dir, stray))), "kit statusline")

    def test_the_dry_run_says_the_copy_would_be_kept_and_that_the_bytes_are_unknown(self):
        """Nothing on record and no copy in the backup: the tool cannot know whether these are
        still the bytes the run wrote, and says so rather than implying it checked."""
        stray = self.write(os.path.join(self.home, "statusline.ps1"), "kit statusline")
        self.backup("20260828-100000", "user scope install", [("created", stray)])
        code, output = run_restore(["--stamp", "20260828-100000", "--dry-run"])
        self.assertEqual(code, 0, output)
        self.assertIn("would remove %s (not on the record; a copy would be kept in the backup)"
                      % stray, output)
        self.assertIn("bytes unknown", output)

    def test_an_off_record_file_edited_since_the_run_says_so(self):
        """A rollback that copied the file and then died leaves the copy behind. The next one has
        something to compare against, so it can tell the adopter the file moved on since."""
        stray = self.write(os.path.join(self.home, "statusline.ps1"), "kit statusline, mine now")
        stamp_dir = self.backup("20260828-100000", "user scope install", [("created", stray)],
                                {stray: "kit statusline"})
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 0, output)
        self.assertIn("removed      %s (was not on the record; copy kept at %s), "
                      "edited since the run"
                      % (stray, restore.mirror_path(stamp_dir, stray)), output)
        self.assertEqual(read_text(str(restore.mirror_path(stamp_dir, stray))), "kit statusline")

    # a backup folder that cannot be read ---------------------------------

    def test_a_missing_backup_copy_is_reported_and_counted(self):
        skill = self.installed("skills/audit/SKILL.md", "kit version")
        self.backup("20260828-100000", "user scope install", [("overwritten", skill)])
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 1, output)
        self.assertIn("missing", output)
        self.assertEqual(read_text(skill), "kit version")

    def test_a_stamp_folder_with_no_manifest_answers_in_words(self):
        os.makedirs(os.path.join(self.backups, "20260828-100000"))
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 2, output)
        self.assertIn("no manifest.txt", output)
        self.assertNotIn("Errno", output)

    def test_a_manifest_with_nothing_we_understand_is_an_argument_error(self):
        stamp_dir = os.path.join(self.backups, "20260828-100000")
        os.makedirs(stamp_dir)
        self.write(os.path.join(stamp_dir, "manifest.txt"),
                   "trigger user scope install\nrestored?? C:/kit/skills/audit/SKILL.md\n\n")
        before = self.tree(stamp_dir)
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 2, output)
        self.assertIn("no entry this tool understands", output)
        self.assertEqual(before, self.tree(stamp_dir))


if __name__ == "__main__":
    unittest.main(verbosity=1)
