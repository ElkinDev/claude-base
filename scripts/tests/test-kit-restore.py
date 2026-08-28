"""Tests for scripts/kit-restore.py, the everyday paths:
python scripts/tests/test-kit-restore.py

What a rollback does when it meets a file it cannot read or write, and what it does with a run that
died before it finished recording, are in test-restore-failures.py. The fixture both suites build
on is in lib-restore-test.py, and the cases that drive the real installer are in
test-install-rollback.py; the installer side is covered by test-install-smoke.ps1 and
test-install-project.ps1."""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("lib_restore_test",
                                              os.path.join(HERE, "lib-restore-test.py"))
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)
restore, run_restore, read_text = lib.restore, lib.run_restore, lib.read_text


class KitRestoreTest(lib.RestoreFixture):
    def test_list_names_every_backup_with_its_count_and_trigger(self):
        skill = self.installed("skills/audit/SKILL.md", "kit version")
        self.backup("20260828-090000", "user scope install",
                    [("created", skill)])
        self.backup("20260828-100000", "project scope install at C:/repo/app",
                    [("overwritten", skill)], {skill: "yours"})
        code, output = run_restore(["--list"])
        self.assertEqual(code, 0, output)
        self.assertIn("20260828-090000", output)
        self.assertIn("20260828-100000", output)
        self.assertIn("user scope install", output)
        self.assertIn("project scope install at C:/repo/app", output)
        self.assertIn("--stamp", output)

    def test_list_says_so_when_there_is_nothing_to_roll_back(self):
        code, output = run_restore(["--list"])
        self.assertEqual(code, 0, output)
        self.assertIn("no backups under", output)

    def test_an_overwritten_file_comes_back_from_the_backup(self):
        skill = self.installed("skills/audit/SKILL.md", "kit version")
        self.backup("20260828-100000", "user scope install",
                    [("overwritten", skill)], {skill: "the version I wrote myself"})
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 0, output)
        self.assertIn("restored", output)
        self.assertEqual(read_text(skill), "the version I wrote myself")
        self.assertNotIn(skill, self.managed_now())

    def test_a_created_file_is_removed_and_leaves_no_empty_folders(self):
        hook = self.installed("hooks/deep/nested/landing.py", "print('hi')")
        self.backup("20260828-100000", "user scope install", [("created", hook)])
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 0, output)
        self.assertIn("removed", output)
        self.assertFalse(os.path.exists(hook))
        self.assertFalse(os.path.isdir(os.path.join(self.home, "hooks")))
        self.assertEqual(self.managed_now(), {})

    def test_a_created_file_already_gone_is_reported_not_an_error(self):
        hook = self.installed("hooks/landing.py", "print('hi')")
        self.backup("20260828-100000", "user scope install", [("created", hook)])
        os.remove(hook)
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 0, output)
        self.assertIn("gone", output)

    def test_a_file_changed_after_the_backup_is_refused(self):
        skill = self.installed("skills/audit/SKILL.md", "kit version")
        self.backup("20260828-100000", "user scope install",
                    [("overwritten", skill)], {skill: "yours"})
        self.write(skill, "kit version, then my own edit on top")
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 1, output)
        self.assertIn("skipped", output)
        self.assertIn("changed after the backup", output)
        self.assertIn("--force", output)
        self.assertEqual(read_text(skill), "kit version, then my own edit on top")

    def test_force_restores_over_a_file_changed_after_the_backup(self):
        skill = self.installed("skills/audit/SKILL.md", "kit version")
        created = self.installed("statusline.ps1", "kit statusline")
        self.backup("20260828-100000", "user scope install",
                    [("overwritten", skill), ("created", created)], {skill: "yours"})
        self.write(skill, "my own edit")
        self.write(created, "my own edit too")
        code, output = run_restore(["--stamp", "20260828-100000", "--force"])
        self.assertEqual(code, 0, output)
        self.assertEqual(read_text(skill), "yours")
        self.assertFalse(os.path.exists(created))

    def test_force_never_touches_a_file_the_run_kept(self):
        """--force is about the files the run wrote, and a kept file is not one of them: it was
        there before, the run left it alone, and no flag turns it into something to roll back."""
        mine = self.write(os.path.join(self.home, "CLAUDE.md"), "my rules")
        self.backup("20260828-100000", "user scope install", [("kept", mine)])
        self.write(mine, "my rules, plus a paragraph I added after the install")
        code, output = run_restore(["--stamp", "20260828-100000", "--force"])
        self.assertEqual(code, 0, output)
        self.assertIn("kept         %s (yours, the run never wrote it)" % mine, output)
        self.assertEqual(read_text(mine), "my rules, plus a paragraph I added after the install")

    def test_a_file_that_is_already_the_backed_up_version_is_left_alone(self):
        skill = self.installed("skills/audit/SKILL.md", "same on both sides")
        self.backup("20260828-100000", "user scope install",
                    [("overwritten", skill)], {skill: "same on both sides"})
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 0, output)
        self.assertIn("unchanged", output)

    def test_a_new_proposal_is_named_and_never_deleted(self):
        mine = self.write(os.path.join(self.home, "CLAUDE.md"), "my rules")
        proposal = self.write(mine + ".new", "the kit rules")
        self.backup("20260828-100000", "user scope install",
                    [("overwritten", mine), ("new", proposal)], {mine: "my rules"})
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 0, output)
        self.assertIn("left", output)
        self.assertTrue(os.path.exists(proposal))
        self.assertEqual(read_text(mine), "my rules")

    def test_a_kept_file_is_named_and_never_touched(self):
        mine = self.write(os.path.join(self.home, "CLAUDE.md"), "my rules")
        proposal = self.write(mine + ".new", "the kit rules")
        self.backup("20260828-100000", "user scope install",
                    [("kept", mine), ("new", proposal)], {mine: "my rules"})
        code, output = run_restore(["--stamp", "20260828-100000"])
        self.assertEqual(code, 0, output)
        self.assertIn("kept         %s (yours, the run never wrote it)" % mine, output)
        self.assertIn("0 skipped", output)
        self.assertEqual(read_text(mine), "my rules")
        self.assertTrue(os.path.exists(proposal))

    def test_dry_run_writes_nothing(self):
        skill = self.installed("skills/audit/SKILL.md", "kit version")
        created = self.installed("statusline.ps1", "kit statusline")
        self.backup("20260828-100000", "user scope install",
                    [("overwritten", skill), ("created", created)], {skill: "yours"})
        before = self.tree(self.home)
        code, output = run_restore(["--stamp", "20260828-100000", "--dry-run"])
        self.assertEqual(code, 0, output)
        self.assertIn("would restore", output)
        self.assertIn("would remove", output)
        self.assertEqual(before, self.tree(self.home))

    def test_an_unknown_stamp_is_an_argument_error(self):
        code, output = run_restore(["--stamp", "20260101-000000"])
        self.assertEqual(code, 2, output)
        self.assertIn("no backup called", output)

    def test_kit_home_follows_the_environment_variable(self):
        self.assertEqual(str(restore.kit_home()), self.home)
        os.environ.pop("KIT_HOME")
        self.assertTrue(str(restore.kit_home()).endswith(".claude"))

    def test_the_backup_layout_mirrors_the_absolute_path(self):
        mirrored = str(restore.mirror_path(os.path.join(self.backups, "s"), self.home))
        self.assertTrue(mirrored.startswith(os.path.join(self.backups, "s")))
        self.assertNotIn(":", mirrored[len(self.backups) + 2:])


if __name__ == "__main__":
    unittest.main(verbosity=1)
