#!/usr/bin/env python3
"""Tests for claude/tools/worktree-sweep.py.

Every test builds its own repository under tempfile.mkdtemp() and every path handed to the
script must resolve inside that temporary root. The confinement check runs before each call,
so a test that ever named a real repository would fail instead of touching it. The script's
default repository is the current directory, which the guard refuses by name too.
"""

import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "worktree-sweep.py")
NEVER_NAME = (os.getcwd().lower().rstrip("/\\"), os.getcwd().replace("\\", "/").lower().rstrip("/"), ".")
# Four bytes per fixture file, so a count in an ARCHIVED line is read, not recomputed.
FILE_BODY = "abcd"


def load_script():
    """The script under a module name, so a test can hand in the after-archive seam.

    The file name carries a hyphen, so it cannot be imported by name; it is loaded from its
    path. Every in-process call still goes through the same argv and the same confinement
    guard as the subprocess calls.
    """
    spec = importlib.util.spec_from_file_location("worktree_sweep", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SWEEP = load_script()


def norm(path):
    return os.path.normcase(os.path.abspath(path))


def own_long_path(path):
    """The long-path form, written here and not borrowed from the script, so a fixture past
    260 characters never depends on the code under test."""
    full = os.path.abspath(path)
    return "\\\\?\\" + full if os.name == "nt" else full


def looks_like_path(token):
    return os.path.isabs(token) or "/" in token or "\\" in token


class WorktreeSweepTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="wtsw-")
        self.repo = os.path.join(self.root, "repo")
        self.lock = os.path.join(self.root, "lock")
        self.evidence = os.path.join(self.root, "evidence")
        os.makedirs(self.lock)
        os.makedirs(os.path.join(self.evidence, "lanes"))
        os.makedirs(os.path.join(self.evidence, "reviews"))
        self.commits = self.init_repo()

    def tearDown(self):
        # Only ever the temporary root of this test, never a path the script was pointed at.
        self.assertTrue(norm(self.root).startswith(norm(tempfile.gettempdir())))
        # the long-path form, since a test leaves a file past 260 characters when git cannot
        shutil.rmtree(own_long_path(self.root), ignore_errors=True)

    # -- repository fixtures ------------------------------------------------------------

    def git(self, *args, **kwargs):
        env = dict(os.environ)
        env.update(kwargs.pop("env", {}))
        env.setdefault("GIT_CONFIG_NOSYSTEM", "1")
        done = subprocess.run(
            ["git"] + list(args), capture_output=True, text=True, timeout=120, env=env
        )
        if kwargs.get("check", True):
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        return done

    def commit(self, name, when):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as handle:
            handle.write(name + "\n")
        self.git("-C", self.repo, "add", name)
        self.git(
            "-C",
            self.repo,
            "commit",
            "-m",
            "add " + name,
            env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when},
        )
        return self.git("-C", self.repo, "rev-parse", "HEAD").stdout.strip()

    def init_repo(self):
        os.makedirs(self.repo)
        self.git("init", self.repo)
        self.git("-C", self.repo, "symbolic-ref", "HEAD", "refs/heads/main")
        self.git("-C", self.repo, "config", "user.email", "fixture@example.com")
        self.git("-C", self.repo, "config", "user.name", "Lane")
        self.git("-C", self.repo, "config", "commit.gpgsign", "false")
        first = self.commit("one.txt", "2026-01-01T10:00:00+00:00")
        second = self.commit("two.txt", "2026-01-02T10:00:00+00:00")
        return first, second

    def worktree(self, name, branch, start):
        path = os.path.join(self.root, name)
        self.git("-C", self.repo, "worktree", "add", path, "-b", branch, start)
        return path

    def ahead_worktree(self, name, branch):
        path = self.worktree(name, branch, "main")
        with open(os.path.join(path, "extra.txt"), "w", encoding="utf-8") as handle:
            handle.write("extra\n")
        self.git("-C", path, "add", "extra.txt")
        self.git("-C", path, "config", "user.email", "fixture@example.com")
        self.git("-C", path, "config", "user.name", "Lane")
        self.git(
            "-C",
            path,
            "commit",
            "-m",
            "ahead of main",
            env={
                "GIT_AUTHOR_DATE": "2026-01-03T10:00:00+00:00",
                "GIT_COMMITTER_DATE": "2026-01-03T10:00:00+00:00",
            },
        )
        return path

    def exclude_build(self):
        """Ignore build/ the way the real repository does, so a lockrun tree is not dirt."""
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a", encoding="utf-8") as handle:
            handle.write("build/\n")

    def make_files(self, directory, names):
        """Write four-byte files, so every expected byte count in a test is arithmetic."""
        os.makedirs(directory, exist_ok=True)
        for name in names:
            with open(os.path.join(directory, name), "w", encoding="utf-8", newline="") as handle:
                handle.write(FILE_BODY)
        return directory

    def lockrun_files(self, path, names):
        return self.make_files(os.path.join(path, "build", "lockrun"), names)

    def archive_root(self):
        return os.path.join(self.evidence, "worktree-archive")

    def leaf_name(self, path):
        """The leaf the script writes for this worktree: `<directory name>--<tip9>`.

        Read from git while the worktree still exists, so a test can name it after removal.
        """
        tip = self.git("-C", path, "rev-parse", "--short=9", "HEAD").stdout.strip()
        self.assertTrue(tip, "no tip for " + path)
        return os.path.basename(os.path.normpath(path)) + "--" + tip

    def zip_path(self, leaf):
        """The one zip the script writes for this worktree: `<leaf>.zip`."""
        return os.path.join(self.archive_root(), leaf + ".zip")

    def zip_names(self, leaf):
        with zipfile.ZipFile(self.zip_path(leaf)) as archive:
            return sorted(archive.namelist())

    def zip_has(self, leaf, *parts):
        return "/".join(parts) in self.zip_names(leaf)

    def zip_text(self, leaf, *parts):
        with zipfile.ZipFile(self.zip_path(leaf)) as archive:
            return archive.read("/".join(parts)).decode("utf-8")

    def zip_children(self, leaf, *parts):
        """Names one level under a folder of the zip, the way os.listdir reads a folder."""
        prefix = "/".join(parts) + "/"
        return sorted({n[len(prefix):].split("/")[0] for n in self.zip_names(leaf) if n.startswith(prefix)})

    def write_marker(self, leaf, source, extra=()):
        """A zip left by an earlier run: its `.source` entry and any extra entries named."""
        os.makedirs(self.archive_root(), exist_ok=True)
        with zipfile.ZipFile(self.zip_path(leaf), "w") as archive:
            archive.writestr(".source", source + "\n")
            for name in extra:
                archive.writestr(name, FILE_BODY)

    def write_file(self, path, body):
        """One file with a body of this test's choosing, so two archives can be told apart."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(body)

    def read_file(self, path):
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return handle.read()

    def make_link(self, link, target):
        """A symlink when this account may make one, else a junction. False when neither."""
        try:
            os.symlink(target, link, target_is_directory=True)
            return True
        except (OSError, AttributeError, NotImplementedError):
            pass
        try:
            done = subprocess.run(
                ["cmd", "/c", "mklink", "/J", link, target],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except OSError:
            return False
        return done.returncode == 0 and os.path.isdir(link)

    # -- the confinement guard ----------------------------------------------------------

    def assert_confined(self, argv):
        for index, token in enumerate(argv):
            self.assertNotIn(token.lower().rstrip("/\\"), NEVER_NAME, "names a real path: " + token)
            if not looks_like_path(token):
                continue
            self.assertTrue(
                norm(token).startswith(norm(self.root) + os.sep) or norm(token) == norm(self.root),
                "path outside the temp root: " + token,
            )
            if index and argv[index - 1] == "--repo":
                self.assertTrue(norm(token).startswith(norm(self.root) + os.sep))

    def sweep(self, *extra, **kwargs):
        evidence = kwargs.pop("evidence", self.evidence)
        self.assertFalse(kwargs, kwargs)
        argv = [
            "--repo", self.repo, "--lock-root", self.lock, "--evidence-root", evidence
        ] + list(extra)
        self.assert_confined(argv)
        done = subprocess.run(
            [sys.executable, SCRIPT] + argv, capture_output=True, text=True, timeout=300
        )
        return done.returncode, done.stdout

    def sweep_in_process(self, *extra, **kwargs):
        """The same argv and the same guard, run in this process so a seam can be passed.

        `after_archive` is called with the worktree path right after its ARCHIVED line, which
        is the window the archive itself opens between the two freshness re-checks.
        """
        evidence = kwargs.pop("evidence", self.evidence)
        after_archive = kwargs.pop("after_archive", None)
        self.assertFalse(kwargs, kwargs)
        argv = [
            "--repo", self.repo, "--lock-root", self.lock, "--evidence-root", evidence
        ] + list(extra)
        self.assert_confined(argv)
        args = SWEEP.build_parser().parse_args(argv)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = SWEEP.sweep(args, after_archive=after_archive)
        return code, buffer.getvalue()

    def line_for(self, out, prefix):
        hits = [line for line in out.splitlines() if line.startswith(prefix + " ")]
        self.assertEqual(1, len(hits), "expected one {} line in:\n{}".format(prefix, out))
        return hits[0]

    def assert_names(self, line, path):
        """git prints its own separators, so compare the second field as a normalized path."""
        fields = line.split(" ")
        self.assertGreater(len(fields), 1, line)
        self.assertEqual(norm(path), norm(fields[1].rstrip(":")), line)

    def branch_exists(self, branch):
        out = self.git("-C", self.repo, "branch", "--list", branch).stdout
        return branch in out

    # -- pins ----------------------------------------------------------------------------

    def test_landed_clean_worktree_is_remove_and_the_dry_run_leaves_it_on_disk(self):
        path = self.worktree("wt-landed", "landed", self.commits[0])
        code, out = self.sweep()
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "REMOVE"), path)
        self.assertIn(
            "SWEEP dry: remove=1 dirty=0 not-landed=0 in-flight=0 kept=0 other=0", out
        )
        self.assertTrue(os.path.isdir(path))

    def test_apply_removes_the_landed_worktree_and_leaves_its_branch(self):
        path = self.worktree("wt-landed", "landed", self.commits[0])
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "REMOVED"), path)
        self.assertIn("SWEEP applied: removed=1 stopped=0 left=0", out)
        self.assertFalse(os.path.isdir(path))
        self.assertTrue(self.branch_exists("landed"))

    def test_landed_worktree_with_an_untracked_file_is_dirty_and_survives_apply(self):
        path = self.worktree("wt-dirty", "dirty", self.commits[0])
        with open(os.path.join(path, "scratch.txt"), "w", encoding="utf-8") as handle:
            handle.write("not committed\n")
        code, out = self.sweep()
        self.assertEqual(0, code, out)
        self.assertIn("files=1", self.line_for(out, "DIRTY"))
        applied_code, applied = self.sweep("--apply")
        self.assertEqual(0, applied_code, applied)
        self.assertIn("SWEEP applied: removed=0 stopped=0 left=0", applied)
        self.assertTrue(os.path.isdir(path))

    def test_branch_with_a_commit_main_does_not_hold_is_not_landed_and_survives(self):
        path = self.ahead_worktree("wt-ahead", "ahead")
        code, out = self.sweep()
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "NOT-LANDED"), path)
        applied_code, applied = self.sweep("--apply")
        self.assertEqual(0, applied_code, applied)
        self.assertIn("SWEEP applied: removed=0 stopped=0 left=0", applied)
        self.assertTrue(os.path.isdir(path))

    def test_keep_wins_over_remove(self):
        path = self.worktree("wt-landed", "landed", self.commits[0])
        code, out = self.sweep("--keep", path)
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "KEPT"), path)
        self.assertIn("remove=0", out)
        applied_code, applied = self.sweep("--keep", path, "--apply")
        self.assertEqual(0, applied_code, applied)
        self.assertTrue(os.path.isdir(path))

    def test_a_lockrun_log_without_its_done_is_in_flight_and_survives(self):
        path = self.worktree("wt-running", "running", self.commits[0])
        self.lockrun_files(path, ("x.log",))
        code, out = self.sweep()
        self.assertEqual(0, code, out)
        self.assertIn("x.log", self.line_for(out, "IN-FLIGHT"))
        applied_code, applied = self.sweep("--apply")
        self.assertEqual(0, applied_code, applied)
        self.assertIn("SWEEP applied: removed=0 stopped=0 left=0", applied)
        self.assertTrue(os.path.isdir(path))

    def test_phase_logs_never_hold_a_finished_lane_in_flight(self):
        # lockrun leaves per-phase logs behind, and they never get a .done of their own.
        self.exclude_build()
        finished = self.worktree("wt-finished", "finished", self.commits[0])
        self.lockrun_files(finished, ("x.done", "x.log", "x.launch.log", "x.phase1.log"))
        running = self.worktree("wt-running", "running", self.commits[1])
        self.lockrun_files(running, ("y.launch.log", "y.log"))
        code, out = self.sweep()
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "REMOVE"), finished)
        in_flight = self.line_for(out, "IN-FLIGHT")
        self.assert_names(in_flight, running)
        self.assertTrue(in_flight.endswith(" y.log"), in_flight)
        self.assertIn("remove=1 dirty=0 not-landed=0 in-flight=1", out)

    def test_apply_exits_three_and_removes_nothing_while_the_gradle_mutex_is_held(self):
        path = self.worktree("wt-landed", "landed", self.commits[0])
        os.makedirs(os.path.join(self.lock, "gradle.lock.d"))
        code, out = self.sweep("--apply")
        self.assertEqual(3, code, out)
        self.assertIn("REFUSED", out)
        self.assertNotIn("REMOVED", out)
        self.assertTrue(os.path.isdir(path))

    def test_a_failed_removal_prints_stopped_exits_two_and_leaves_the_next_worktree(self):
        blocked = self.worktree("wt-blocked", "blocked", self.commits[0])
        following = self.worktree("wt-following", "following", self.commits[1])
        self.git("-C", self.repo, "worktree", "lock", blocked)
        code, out = self.sweep("--apply")
        self.assertEqual(2, code, out)
        stopped = self.line_for(out, "STOPPED")
        self.assert_names(stopped, blocked)
        self.assertIn("locked", stopped.lower())
        self.assertIn("SWEEP applied: removed=0 stopped=1 left=2", out)
        self.assertNotIn("REMOVED", out)
        self.assertTrue(os.path.isdir(blocked))
        self.assertTrue(os.path.isdir(following))

    def test_limit_one_removes_one_and_reports_the_rest_as_left(self):
        first = self.worktree("wt-first", "first", self.commits[0])
        second = self.worktree("wt-second", "second", self.commits[1])
        code, out = self.sweep("--apply", "--limit", "1")
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "REMOVED"), first)
        self.assertIn("SWEEP applied: removed=1 stopped=0 left=1", out)
        self.assertFalse(os.path.isdir(first))
        self.assertTrue(os.path.isdir(second))

    def test_keep_wins_in_another_case_with_backslashes_and_with_a_trailing_slash(self):
        path = self.worktree("wt-landed", "landed", self.commits[0])
        spellings = (
            path.upper(),
            path.replace("/", "\\"),
            path.rstrip("/\\") + "/",
        )
        for spelling in spellings:
            code, out = self.sweep("--keep", spelling)
            self.assertEqual(0, code, out)
            self.assert_names(self.line_for(out, "KEPT"), path)
            self.assertIn("remove=0", out)
            self.assertIn("unmatched-keeps=0", out)

    def test_the_script_source_carries_no_force_no_prune_and_one_removal_call_site(self):
        with open(SCRIPT, "r", encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("--force", "prune", "rmtree", "os.remove"):
            self.assertNotIn(forbidden, source, "the sweep must never grow " + forbidden)
        self.assertEqual(1, source.count('"worktree", "remove"'), "one removal call site only")

    def test_an_unmatched_keep_warns_in_the_dry_run_and_refuses_apply_with_exit_five(self):
        path = self.worktree("wt-landed", "landed", self.commits[0])
        typo = os.path.join(self.root, "wt-landed2")
        code, out = self.sweep("--keep", typo)
        self.assertEqual(0, code, out)
        self.assertIn("WARNING keep matched no worktree: " + typo, out)
        self.assertIn("unmatched-keeps=1", out)
        applied_code, applied = self.sweep("--keep", typo, "--apply")
        self.assertEqual(5, applied_code, applied)
        self.assertIn("REFUSED keep matched no worktree: " + typo, applied)
        self.assertNotIn("REMOVED", applied)
        self.assertTrue(os.path.isdir(path))

    def test_a_worktree_dirtied_after_classification_is_skipped_and_stays_on_disk(self):
        # The archive of the first removal writes inside the second worktree, so the second
        # one is clean when it is classified and dirty by the time its own removal comes up.
        self.exclude_build()
        first = self.worktree("wt-first", "first", self.commits[0])
        self.lockrun_files(first, ("x.done", "x.log"))
        second = self.worktree("wt-second", "second", self.commits[1])
        evidence = os.path.join(second, "evidence")
        os.makedirs(evidence)
        code, out = self.sweep("--apply", evidence=evidence)
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "REMOVED"), first)
        skipped = self.line_for(out, "SKIPPED")
        self.assert_names(skipped, second)
        self.assertIn("dirty now: files=1", skipped)
        self.assertIn("SWEEP applied: removed=1 stopped=0 left=1 skipped=1", out)
        self.assertFalse(os.path.isdir(first))
        self.assertTrue(os.path.isdir(second))

    # -- the archive, round 3: four subtrees, copied and verified before any removal -------

    def test_the_four_build_subtrees_are_archived_with_their_relative_paths_and_verified(self):
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.make_files(os.path.join(path, "build", "lockrun"), ("x.done", "x.log"))
        self.make_files(os.path.join(path, "build", "precheck"), ("tip.ok",))
        self.make_files(os.path.join(path, "app", "build", "reports"), ("lint.html",))
        self.make_files(
            os.path.join(path, "app", "build", "test-results", "testDebug"), ("TEST-a.xml",)
        )
        leaf = self.leaf_name(path)
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        archived = self.line_for(out, "ARCHIVED")
        self.assert_names(archived, path)
        self.assertTrue(archived.endswith(" files=5 bytes=20"), archived)
        self.assert_names(self.line_for(out, "REMOVED"), path)
        self.assertFalse(os.path.isdir(path))
        for relative in (
            ("build", "lockrun", "x.done"),
            ("build", "lockrun", "x.log"),
            ("build", "precheck", "tip.ok"),
            ("app", "build", "reports", "lint.html"),
            ("app", "build", "test-results", "testDebug", "TEST-a.xml"),
        ):
            self.assertTrue(self.zip_has(leaf, *relative), "/".join(relative) + " missing:\n" + out)
        self.assertEqual(norm(path), norm(self.zip_text(leaf, ".source").strip()))
        # one zip per worktree and nothing else in the archive folder, no .part left behind
        self.assertEqual([leaf + ".zip"], sorted(os.listdir(self.archive_root())))

    def test_the_archive_holds_the_same_names_after_a_removal(self):
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        names = ("x.done", "x.log", "x.phase1.log")
        self.lockrun_files(path, names)
        leaf = self.leaf_name(path)
        dry_code, dry = self.sweep()
        self.assertEqual(0, dry_code, dry)
        self.assertFalse(os.path.isdir(self.archive_root()), "the dry run created the archive")
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        self.assertIn("SWEEP applied: removed=1 stopped=0 left=0 skipped=0", out)
        self.assertFalse(os.path.isdir(path))
        self.assertEqual(sorted(names), self.zip_children(leaf, "build", "lockrun"), out)

    def test_a_test_results_nested_under_a_feature_module_is_found_and_archived(self):
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        deep = os.path.join(path, "feature", "money", "build", "test-results", "testFamily")
        self.make_files(deep, ("TEST-money.xml", "TEST-more.xml"))
        leaf = self.leaf_name(path)
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        self.assertTrue(self.line_for(out, "ARCHIVED").endswith(" files=2 bytes=8"), out)
        for name in ("TEST-money.xml", "TEST-more.xml"):
            self.assertTrue(
                self.zip_has(leaf, "feature", "money", "build", "test-results", "testFamily", name),
                name + " missing:\n" + out,
            )

    def test_the_generated_children_of_a_build_directory_are_never_archived(self):
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.make_files(os.path.join(path, "app", "build", "reports"), ("lint.html",))
        for generated in ("intermediates", "tmp", "kotlin", "generated", "outputs"):
            self.make_files(os.path.join(path, "app", "build", generated), ("bulk.bin",))
        leaf = self.leaf_name(path)
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        self.assertTrue(self.line_for(out, "ARCHIVED").endswith(" files=1 bytes=4"), out)
        self.assertEqual(["reports"], self.zip_children(leaf, "app", "build"))

    def test_a_link_inside_a_subtree_is_never_followed_into_the_archive(self):
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        lockrun = self.lockrun_files(path, ("x.done", "x.log"))
        outside = self.make_files(
            os.path.join(self.root, "outside"), ("secret-one.txt", "secret-two.txt")
        )
        if not self.make_link(os.path.join(lockrun, "linked"), outside):
            self.skipTest("neither a symlink nor a junction can be created by this account")
        leaf = self.leaf_name(path)
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        self.assertTrue(self.line_for(out, "ARCHIVED").endswith(" files=2 bytes=8"), out)
        self.assertEqual(["x.done", "x.log"], self.zip_children(leaf, "build", "lockrun"))
        self.assertFalse(any("linked" in name for name in self.zip_names(leaf)), out)
        # And the removal that followed did not walk through the link either.
        self.assertEqual(
            ["secret-one.txt", "secret-two.txt"], sorted(os.listdir(outside)), out
        )

    def test_a_zip_of_another_worktree_at_the_same_name_is_kept_and_the_next_name_written(self):
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.lockrun_files(path, ("x.done", "x.log"))
        leaf = self.leaf_name(path)
        elsewhere = os.path.join(self.root, "elsewhere", "wt-landed")
        self.write_marker(leaf, elsewhere, extra=("build/lockrun/theirs.log",))
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "REMOVED"), path)
        self.assertEqual(["theirs.log"], self.zip_children(leaf, "build", "lockrun"))
        self.assertEqual(elsewhere, self.zip_text(leaf, ".source").strip())
        self.assertEqual(["x.done", "x.log"], self.zip_children(leaf + "-2", "build", "lockrun"))
        self.assertEqual(os.path.abspath(path), self.zip_text(leaf + "-2", ".source").strip())

    def test_a_zip_left_by_an_earlier_run_of_the_same_worktree_is_kept_whole(self):
        # A run that archived and then skipped (a gate started) leaves its zip. The next run
        # never writes over it: the entry the source no longer holds stays in the first zip,
        # and the second zip holds the tree as it is now.
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.lockrun_files(path, ("x.done", "x.log"))
        leaf = self.leaf_name(path)
        self.write_marker(leaf, path, extra=("build/lockrun/ghost.log",))
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "REMOVED"), path)
        self.assertEqual(["ghost.log"], self.zip_children(leaf, "build", "lockrun"))
        self.assertEqual(["x.done", "x.log"], self.zip_children(leaf + "-2", "build", "lockrun"))

    def test_an_archive_that_does_not_count_the_same_as_its_source_stops_the_removal(self):
        # A zip that misses a file of its source must never let the removal run: the writer is
        # handed one file fewer than the independent count walks.
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.lockrun_files(path, ("x.done", "x.log"))
        real = SWEEP.list_files
        SWEEP.list_files = lambda worktree, relative: real(worktree, relative)[:-1]
        try:
            code, out = self.sweep_in_process("--apply")
        finally:
            SWEEP.list_files = real
        self.assertEqual(2, code, out)
        stopped = self.line_for(out, "STOPPED")
        self.assert_names(stopped, path)
        self.assertIn("archive not verified", stopped)
        self.assertIn("source files=2 bytes=8, archive files=1 bytes=4", stopped)
        self.assertNotIn("REMOVED", out)
        self.assertTrue(os.path.isdir(path))
        # the unproved zip stays a .part, never a zip that looks complete; the sweep deletes
        # nothing itself, so the next attempt of this worktree writes that .part again
        self.assertFalse(os.path.exists(self.zip_path(self.leaf_name(path))), out)
        self.assertTrue(os.path.exists(self.zip_path(self.leaf_name(path)) + ".part"), out)

    def test_a_report_nested_past_the_old_copy_limit_is_zipped_and_the_worktree_removed(self):
        # The loose copy stopped when its destination passed 240 characters (54 worktrees on
        # 2026-09-24); a zip entry has no such limit.
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        deep = os.path.join(path, "app", "build", "reports", "tests", "testDebugUnitTest", "classes")
        name = "com.example.app.feature.capture.domain.suggestion." + "Long" * 20 + ".html"
        self.make_files(deep, (name,))
        leaf = self.leaf_name(path)
        old_dest = os.path.join(self.archive_root(), leaf, os.path.relpath(os.path.join(deep, name), path))
        self.assertGreater(len(old_dest), SWEEP.MAX_DEST_PATH, "the fixture must pass the old limit")
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "REMOVED"), path)
        self.assertTrue(self.zip_has(leaf, "app", "build", "reports", "tests", "testDebugUnitTest",
                                     "classes", name), out)

    def test_the_dry_run_creates_no_archive_and_prints_the_totals_it_would_write(self):
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.make_files(os.path.join(path, "build", "lockrun"), ("x.done", "x.log"))
        self.make_files(os.path.join(path, "app", "build", "reports"), ("lint.html",))
        code, out = self.sweep()
        self.assertEqual(0, code, out)
        line = self.line_for(out, "WOULD-ARCHIVE")
        self.assert_names(line, path)
        self.assertIn(" leaf=" + self.leaf_name(path) + ".zip ", line)
        self.assertTrue(line.endswith(" files=3 bytes=12"), line)
        self.assertTrue(
            out.rstrip().splitlines()[-1].endswith("archive-files=3 archive-bytes=12"),
            out,
        )
        self.assertFalse(os.path.isdir(self.archive_root()), "the dry run created the archive")
        self.assertTrue(os.path.isdir(path))

    # -- round 5: the window the archive itself opens, and one leaf per tip ---------------

    def test_a_mutex_taken_during_the_archive_stops_that_worktrees_removal(self):
        # The archive is the longest step of the run, so a gate can start inside it. The
        # first re-check has already passed; only a second one, after the copy, sees this.
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.lockrun_files(path, ("x.done", "x.log"))
        leaf = self.leaf_name(path)

        def a_gate_starts(archived):
            self.assertEqual(norm(path), norm(archived))
            os.makedirs(os.path.join(self.lock, "gradle.lock.d"))

        code, out = self.sweep_in_process("--apply", after_archive=a_gate_starts)
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "ARCHIVED"), path)
        skipped = self.line_for(out, "SKIPPED")
        self.assert_names(skipped, path)
        self.assertIn("a mutex is held under", skipped)
        self.assertIn("gradle.lock.d", skipped)
        self.assertNotIn("REMOVED", out)
        self.assertIn("SWEEP applied: removed=0 stopped=0 left=1 skipped=1", out)
        self.assertTrue(os.path.isdir(path))
        # The archive already written stays where it is: it costs nothing and proves nothing wrong.
        self.assertTrue(self.zip_has(leaf, "build", "lockrun", "x.log"), out)

    def test_a_run_started_during_the_archive_stops_that_worktrees_removal(self):
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.lockrun_files(path, ("x.done", "x.log"))
        leaf = self.leaf_name(path)

        def a_run_starts(archived):
            self.assertEqual(norm(path), norm(archived))
            self.lockrun_files(archived, ("y.log",))

        code, out = self.sweep_in_process("--apply", after_archive=a_run_starts)
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "ARCHIVED"), path)
        skipped = self.line_for(out, "SKIPPED")
        self.assert_names(skipped, path)
        self.assertTrue(skipped.endswith("a run is in flight: y.log"), skipped)
        self.assertNotIn("REMOVED", out)
        self.assertIn("SWEEP applied: removed=0 stopped=0 left=1 skipped=1", out)
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(self.zip_has(leaf, "build", "lockrun", "x.log"), out)

    def test_the_evidence_root_defaults_to_the_env_then_beside_the_repository(self):
        saved = os.environ.pop("EVIDENCE_ROOT", None)
        try:
            self.assertEqual(
                norm(os.path.join(self.root, "evidence")), norm(SWEEP.default_evidence_root(self.repo))
            )
            os.environ["EVIDENCE_ROOT"] = os.path.join(self.root, "elsewhere")
            self.assertEqual(
                norm(os.path.join(self.root, "elsewhere")), norm(SWEEP.default_evidence_root(self.repo))
            )
        finally:
            os.environ.pop("EVIDENCE_ROOT", None)
            if saved is not None:
                os.environ["EVIDENCE_ROOT"] = saved

    def test_run_from_a_linked_worktree_the_default_archive_is_beside_the_main_checkout(self):
        anchor = self.worktree(os.path.join("nest", "wt-anchor"), "anchor", self.commits[0])
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.exclude_build()
        self.lockrun_files(path, ("x.done", "x.log"))
        leaf = self.leaf_name(path)
        argv = ["--repo", anchor, "--keep", anchor, "--lock-root", self.lock, "--apply"]
        self.assert_confined(argv)
        env = dict(os.environ)
        env.pop("EVIDENCE_ROOT", None)
        done = subprocess.run(
            [sys.executable, SCRIPT] + argv, capture_output=True, text=True, timeout=300, env=env
        )
        self.assertEqual(0, done.returncode, done.stdout)
        self.assertTrue(self.zip_has(leaf, "build", "lockrun", "x.log"), done.stdout)
        self.assertFalse(os.path.isdir(os.path.join(self.root, "nest", "evidence")), done.stdout)
        self.assertTrue(os.path.isdir(anchor))

    def test_no_lock_root_is_no_mutex_belt_and_the_archive_lands_beside_the_repository(self):
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.exclude_build()
        self.lockrun_files(path, ("x.done", "x.log"))
        leaf = self.leaf_name(path)
        argv = ["--repo", self.repo, "--apply"]
        self.assert_confined(argv)
        env = dict(os.environ)
        env.pop("EVIDENCE_ROOT", None)
        done = subprocess.run(
            [sys.executable, SCRIPT] + argv, capture_output=True, text=True, timeout=300, env=env
        )
        self.assertEqual(0, done.returncode, done.stdout)
        self.assertIn("REMOVED", done.stdout)
        self.assertFalse(os.path.isdir(path))
        self.assertTrue(self.zip_has(leaf, "build", "lockrun", "x.log"), done.stdout)

    def test_apply_exits_three_while_the_bench_mutex_alone_is_held(self):
        # The refusal claims every `*.lock.d`, not only gradle's: the bench mutex is one too.
        path = self.worktree("wt-landed", "landed", self.commits[0])
        os.makedirs(os.path.join(self.lock, "bench.lock.d"))
        code, out = self.sweep("--apply")
        self.assertEqual(3, code, out)
        self.assertIn("bench.lock.d", self.line_for(out, "REFUSED"))
        self.assertNotIn("REMOVED", out)
        self.assertTrue(os.path.isdir(path))

    def test_the_same_worktree_name_at_another_tip_gets_its_own_archive_leaf(self):
        # A lane's worktree is swept, then another lane recreates the same directory name at
        # the same path. Two tips, two leaves, and the first archive is never written over.
        self.exclude_build()
        first = self.worktree("wt-lane", "lane-one", self.commits[0])
        self.make_files(os.path.join(first, "build", "lockrun"), ("x.done",))
        self.write_file(os.path.join(first, "build", "lockrun", "x.log"), "abcd")
        first_leaf = self.leaf_name(first)
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "REMOVED"), first)

        second = self.worktree("wt-lane", "lane-two", self.commits[1])
        self.make_files(os.path.join(second, "build", "lockrun"), ("x.done",))
        self.write_file(os.path.join(second, "build", "lockrun", "x.log"), "wxyz")
        second_leaf = self.leaf_name(second)
        self.assertNotEqual(first_leaf, second_leaf)
        again_code, again = self.sweep("--apply")
        self.assertEqual(0, again_code, again)
        self.assert_names(self.line_for(again, "REMOVED"), second)

        self.assertEqual(
            sorted([first_leaf + ".zip", second_leaf + ".zip"]),
            sorted(os.listdir(self.archive_root())),
            again,
        )
        self.assertEqual("abcd", self.zip_text(first_leaf, "build", "lockrun", "x.log"))
        self.assertEqual("wxyz", self.zip_text(second_leaf, "build", "lockrun", "x.log"))


    # -- round 6: one pin per guard of the zip that a mutant showed unpinned -------------

    def test_a_zip_entry_that_fails_its_crc_stops_the_removal(self):
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.lockrun_files(path, ("x.done", "x.log"))
        real = zipfile.ZipFile.testzip
        zipfile.ZipFile.testzip = lambda archive: "build/lockrun/x.log"
        try:
            code, out = self.sweep_in_process("--apply")
        finally:
            zipfile.ZipFile.testzip = real
        self.assertEqual(2, code, out)
        stopped = self.line_for(out, "STOPPED")
        self.assert_names(stopped, path)
        self.assertIn("the zip entry build/lockrun/x.log fails its CRC", stopped)
        self.assertNotIn("REMOVED", out)
        self.assertTrue(os.path.isdir(path))
        self.assertFalse(os.path.exists(self.zip_path(self.leaf_name(path))), out)

    def test_a_file_at_the_zip_name_that_is_no_zip_is_kept_and_the_next_name_written(self):
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.lockrun_files(path, ("x.done", "x.log"))
        leaf = self.leaf_name(path)
        target = self.zip_path(leaf)
        self.write_file(target, "not a zip")
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "REMOVED"), path)
        self.assertEqual("not a zip", self.read_file(target))
        self.assertEqual(["x.done", "x.log"], self.zip_children(leaf + "-2", "build", "lockrun"))

    def test_a_source_file_past_260_characters_is_zipped(self):
        # The copy's old stop was the destination; a zip entry has none, but the source must
        # still be read through the long-path prefix or the walk skips it without a word.
        self.exclude_build()
        self.git("-C", self.repo, "config", "core.longpaths", "true")
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.lockrun_files(path, ("x.done", "x.log"))
        parts = ["segment-of-a-long-report-path-{:02d}".format(i) for i in range(8)]
        deep = os.path.join(path, "build", "reports", *parts)
        name = "Deep$1.html"
        self.assertGreater(len(os.path.join(deep, name)), 260, "the fixture must pass 260")
        os.makedirs(own_long_path(deep))
        with open(own_long_path(os.path.join(deep, name)), "w", encoding="utf-8") as handle:
            handle.write(FILE_BODY)
        leaf = self.leaf_name(path)
        code, out = self.sweep("--apply")
        self.assertTrue(self.line_for(out, "ARCHIVED").endswith(" files=3 bytes=12"), out)
        self.assertTrue(self.zip_has(leaf, "build", "reports", *(parts + [name])), out)

    def test_an_archive_path_past_the_bound_stops_before_anything_is_written(self):
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.lockrun_files(path, ("x.done", "x.log"))
        leaf = self.leaf_name(path) + ".zip"
        tail = len(os.path.join(SWEEP.ARCHIVE_LEAF, leaf)) + 1
        pad = SWEEP.MAX_DEST_PATH + 5 - tail - len(self.root) - 1
        evidence = os.path.join(self.root, "e" * pad)
        self.assertLess(len(os.path.join(evidence, SWEEP.ARCHIVE_LEAF, leaf)), 255)
        code, out = self.sweep("--apply", evidence=evidence)
        self.assertEqual(2, code, out)
        stopped = self.line_for(out, "STOPPED")
        self.assertIn("archive path longer than {} characters".format(SWEEP.MAX_DEST_PATH), stopped)
        self.assertNotIn("REMOVED", out)
        self.assertTrue(os.path.isdir(path))
        self.assertFalse(os.path.exists(evidence), out)

    def test_a_file_written_after_the_plan_stops_the_removal(self):
        # A run that writes into the subtree after the plan was counted: the zip and the count
        # after it agree with each other, and only the plan shows the source moved.
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        lockrun = self.lockrun_files(path, ("x.done", "x.log"))
        real = SWEEP.list_files

        def a_file_lands(worktree, relative):
            self.write_file(os.path.join(lockrun, "late.txt"), FILE_BODY)
            return real(worktree, relative)

        SWEEP.list_files = a_file_lands
        try:
            code, out = self.sweep_in_process("--apply")
        finally:
            SWEEP.list_files = real
        self.assertEqual(2, code, out)
        stopped = self.line_for(out, "STOPPED")
        self.assertIn("the source changed during the zip: planned files=2 bytes=8", stopped)
        self.assertNotIn("REMOVED", out)
        self.assertTrue(os.path.isdir(path))

    def test_a_file_the_walk_reads_as_a_link_is_left_out_of_the_zip(self):
        # This account cannot make a file symlink, so the link test above covers directories
        # only; here the link rule itself names one file, and both walks must leave it out.
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.lockrun_files(path, ("x.done", "x.log", "linked.txt"))
        leaf = self.leaf_name(path)
        real = SWEEP.is_reparse
        SWEEP.is_reparse = lambda entry: os.path.basename(entry) == "linked.txt" or real(entry)
        try:
            code, out = self.sweep_in_process("--apply")
        finally:
            SWEEP.is_reparse = real
        self.assertEqual(0, code, out)
        self.assertTrue(self.line_for(out, "ARCHIVED").endswith(" files=2 bytes=8"), out)
        self.assertEqual(["x.done", "x.log"], self.zip_children(leaf, "build", "lockrun"))

    # -- round 7: review wtzp r1, a zip is never replaced ---------------------------------

    def test_a_path_reused_at_the_same_tip_keeps_the_first_zip_whole(self):
        # Review wtzp r1 finding 1: a worktree is archived and removed, its branch stays at the
        # same tip, a hand worktree is added again at the same path and runs a gate. The second
        # sweep must not replace the first zip with the new logs alone.
        self.exclude_build()
        path = self.worktree("wt-hand", "hand", self.commits[0])
        self.write_file(os.path.join(path, "build", "lockrun", "first.log"), "abcd")
        self.make_files(os.path.join(path, "build", "lockrun"), ("first.done",))
        leaf = self.leaf_name(path)
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        self.assert_names(self.line_for(out, "REMOVED"), path)
        with open(self.zip_path(leaf), "rb") as handle:
            first_bytes = handle.read()

        self.git("-C", self.repo, "worktree", "add", path, "hand")
        self.assertEqual(leaf, self.leaf_name(path), "the fixture must reuse the same tip")
        self.write_file(os.path.join(path, "build", "lockrun", "second.log"), "wxyz")
        self.make_files(os.path.join(path, "build", "lockrun"), ("second.done",))
        again_code, again = self.sweep("--apply")
        self.assertEqual(0, again_code, again)
        self.assert_names(self.line_for(again, "REMOVED"), path)
        with open(self.zip_path(leaf), "rb") as handle:
            self.assertEqual(first_bytes, handle.read(), "the first zip was written over")
        self.assertEqual(["first.done", "first.log"], self.zip_children(leaf, "build", "lockrun"))
        self.assertEqual(
            ["second.done", "second.log"], self.zip_children(leaf + "-2", "build", "lockrun")
        )
        self.assertEqual("wxyz", self.zip_text(leaf + "-2", "build", "lockrun", "second.log"))

    def test_every_name_taken_stops_the_removal(self):
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        self.lockrun_files(path, ("x.done", "x.log"))
        leaf = self.leaf_name(path)
        self.write_marker(leaf, path)
        self.write_marker(leaf + "-2", path)
        real = SWEEP.MAX_LEAVES
        SWEEP.MAX_LEAVES = 2
        try:
            code, out = self.sweep_in_process("--apply")
        finally:
            SWEEP.MAX_LEAVES = real
        self.assertEqual(2, code, out)
        stopped = self.line_for(out, "STOPPED")
        self.assertIn("no free archive name: 2 zips of", stopped)
        self.assertNotIn("REMOVED", out)
        self.assertTrue(os.path.isdir(path))
        self.assertFalse(os.path.exists(self.zip_path(leaf + "-3")), out)

    def test_a_report_stamped_before_1980_is_zipped(self):
        # Review wtzp r1 finding 2: zipfile refuses a timestamp before 1980 unless told not to,
        # and the oldest worktree is always tried first, so one such file would stop every run.
        self.exclude_build()
        path = self.worktree("wt-landed", "landed", self.commits[0])
        lockrun = self.lockrun_files(path, ("x.done", "x.log"))
        os.utime(os.path.join(lockrun, "x.log"), (86400 * 2, 86400 * 2))
        leaf = self.leaf_name(path)
        code, out = self.sweep("--apply")
        self.assertEqual(0, code, out)
        self.assertTrue(self.line_for(out, "ARCHIVED").endswith(" files=2 bytes=8"), out)
        self.assertEqual("abcd", self.zip_text(leaf, "build", "lockrun", "x.log"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
