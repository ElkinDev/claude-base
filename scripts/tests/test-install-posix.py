"""install.py, the macOS and Linux installer, driven end to end into a throwaway home:
python3 scripts/tests/test-install-posix.py

Each case runs the real installer as a child process with HOME pointed at a temp folder, so the
user's own ~/.claude, shell files and PATH links are never touched, and rolls back with the real
scripts/kit-restore.py where the case is about the backup record. POSIX only: install.ps1 is the
Windows installer and has its own suites.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
INSTALLER = os.path.join(ROOT, "install.py")
spec = importlib.util.spec_from_file_location("kit_restore", os.path.join(ROOT, "scripts", "kit-restore.py"))
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


def tree_hash(folder):
    digest = hashlib.sha256()
    for base, dirs, files in os.walk(folder):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(base, name)
            digest.update(path.encode("utf-8"))
            with open(path, "rb") as handle:
                digest.update(handle.read())
    return digest.hexdigest()


@unittest.skipIf(os.name == "nt", "install.py is the macOS and Linux installer")
class InstallPosixTest(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="kit-posix-"))
        self.user = os.path.join(self.base, "user")
        self.home = os.path.join(self.user, ".claude")
        self.links = os.path.join(self.base, "links")
        self.rc = os.path.join(self.base, "rc")
        os.makedirs(self.user)
        self.saved = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)
        shutil.rmtree(self.base, ignore_errors=True)

    def env(self, path_extra=None):
        env = {k: v for k, v in os.environ.items() if k not in ("KIT_HOME", "CLAUDE_CONFIG_DIR")}
        env["HOME"] = self.user
        env["KIT_PATH_LINK_DIR"] = self.links
        env["KIT_SHELL_RC"] = self.rc
        env["KIT_ASSUME_INTERACTIVE"] = "0"
        # The Command Line Tools python keeps its bytecode cache under ~/Library/Caches, which is
        # this throwaway home: off, so a tree hash sees the installer's writes and nothing else.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PATH"] = os.pathsep.join(([path_extra] if path_extra else []) + ["/usr/bin", "/bin"])
        return env

    def install(self, *args, code=0, path_extra=None):
        done = subprocess.run([sys.executable, INSTALLER, "--no-herdr"] + list(args), capture_output=True,
                              text=True, env=self.env(path_extra), timeout=300)
        self.assertEqual(done.returncode, code, done.stdout + done.stderr)
        return done.stdout

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def rollback(self, stamp):
        os.environ["KIT_HOME"] = self.home
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = restore.main(["--stamp", stamp])
        return code, out.getvalue()

    def stamps(self):
        return sorted(os.listdir(os.path.join(self.home, "backups")))

    # ------------------------------------------------------------ user scope

    def test_a_fresh_install_renders_settings_for_this_system(self):
        self.install()
        settings = json.loads(self.read(os.path.join(self.home, "settings.json")))
        self.assertEqual(settings["statusLine"]["command"],
                         'python3 "%s"' % os.path.join(self.home, "statusline.py"))
        commands = [h["command"] for entries in settings["hooks"].values() for e in entries for h in e["hooks"]]
        self.assertTrue(commands)
        for command in commands:
            self.assertTrue(command.startswith('python3 "%s/hooks/' % self.home), command)
            self.assertNotIn("%USERPROFILE%", command)
        self.assertEqual(settings["permissions"]["defaultMode"], "bypassPermissions")
        for rel in ("hooks/guard-read.py", "statusline.py", "CLAUDE.md", "seats/orchestrator.md"):
            self.assertTrue(os.path.isfile(os.path.join(self.home, rel)), rel)
        for rel in ("statusline.ps1", "claude-account.ps1", "cc-launch.ps1", "bin/cc.cmd"):
            self.assertFalse(os.path.exists(os.path.join(self.home, rel)), rel)
        self.assertFalse(os.path.exists(os.path.join(self.home, "skills", "codebase-design")),
                         "a single-channel plugin is never copied")
        manifest = json.loads(self.read(os.path.join(self.home, ".kit-manifest.json")))
        self.assertEqual(manifest["version"], 1)
        self.assertIn(os.path.join(self.home, "settings.json"), manifest["files"])

    def test_a_script_with_a_shebang_lands_executable(self):
        self.install()
        script = os.path.join(self.home, "tools", "wait-marker.sh")
        self.assertTrue(os.stat(script).st_mode & stat.S_IXUSR)
        plain = os.path.join(self.home, "CLAUDE.md")
        self.assertFalse(os.stat(plain).st_mode & stat.S_IXUSR)

    def test_ask_keeps_the_permission_prompts(self):
        self.install("--permissions", "ask")
        settings = json.loads(self.read(os.path.join(self.home, "settings.json")))
        self.assertEqual(settings["permissions"]["defaultMode"], "default")
        self.assertFalse(settings["skipDangerousModePermissionPrompt"])

    def test_the_powershell_spellings_are_accepted(self):
        out = self.install("-DryRun", "-Permissions", "ask")
        self.assertIn("Dry run: nothing was written", out)

    def test_a_dry_run_writes_nothing(self):
        self.write(os.path.join(self.home, "CLAUDE.md"), "mine\n")
        before = tree_hash(self.user)
        out = self.install("--dry-run")
        self.assertIn("backup+new", out)
        self.assertEqual(before, tree_hash(self.user))
        self.assertFalse(os.path.exists(self.links))
        self.assertFalse(os.path.exists(self.rc))

    def test_a_rerun_is_a_no_op(self):
        self.install()
        before = tree_hash(self.user)
        out = self.install()
        self.assertIn("Nothing changed, so there is nothing to roll back.", out)
        self.assertEqual(before, tree_hash(self.user))
        self.assertEqual(len(self.stamps()), 1)

    def test_a_file_you_edited_is_kept_with_the_kit_version_beside_it(self):
        self.install()
        mine = os.path.join(self.home, "CLAUDE.md")
        self.write(mine, "my own rules\n")
        out = self.install()
        self.assertIn("backup+new   %s" % mine, out)
        self.assertEqual(self.read(mine), "my own rules\n")
        self.assertEqual(self.read(mine + ".new"), self.read(os.path.join(ROOT, "claude", "CLAUDE.md")))
        out = self.install("--force")
        self.assertIn("force        %s" % mine, out)
        self.assertEqual(self.read(mine), self.read(os.path.join(ROOT, "claude", "CLAUDE.md")))

    def test_rolling_back_a_fresh_install_removes_what_it_created(self):
        self.write(os.path.join(self.home, "settings.json"), '{\n  "theme": "dark"\n}\n')
        self.install()
        code, out = self.rollback(self.stamps()[0])
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(os.path.join(self.home, "hooks", "guard-read.py")))
        # A .new proposal is left for the adopter to delete once merged, by design of kit-restore.py.
        self.assertIn("left         %s.new" % os.path.join(self.home, "settings.json"), out)
        self.assertEqual(self.read(os.path.join(self.home, "settings.json")), '{\n  "theme": "dark"\n}\n')

    def test_a_settings_file_with_no_kit_hook_is_left_alone(self):
        mine = os.path.join(self.home, "settings.json")
        self.write(mine, '{\n  "model": "opus",\n  "theme": "dark"\n}\n')
        out = self.install()
        self.assertIn("backup+new   %s" % mine, out)
        self.assertIn("left alone", out)
        self.assertEqual(self.read(mine), '{\n  "model": "opus",\n  "theme": "dark"\n}\n')

    def test_a_settings_file_that_runs_kit_hooks_gets_the_new_ones_and_rolls_back(self):
        self.install()
        mine = os.path.join(self.home, "settings.json")
        data = json.loads(self.read(mine))
        del data["hooks"]["PostCompact"]
        data["theme"] = "light"
        edited = json.dumps(data, indent=2) + "\n"
        self.write(mine, edited)
        os.remove(os.path.join(self.home, ".kit-hooks-merged.json")) if os.path.exists(
            os.path.join(self.home, ".kit-hooks-merged.json")) else None
        out = self.install()
        self.assertIn("hooks+", out)
        merged = json.loads(self.read(mine))
        self.assertIn("PostCompact", merged["hooks"])
        self.assertEqual(merged["theme"], "light")
        code, text = self.rollback(self.stamps()[-1])
        self.assertEqual(code, 0, text)
        self.assertEqual(self.read(mine), edited)

    def test_the_merge_can_be_skipped(self):
        self.install()
        mine = os.path.join(self.home, "settings.json")
        data = json.loads(self.read(mine))
        del data["hooks"]["PostCompact"]
        data["theme"] = "light"
        self.write(mine, json.dumps(data, indent=2) + "\n")
        out = self.install("--no-hook-merge")
        self.assertIn("not merged, --no-hook-merge", out)
        self.assertNotIn("PostCompact", json.loads(self.read(mine))["hooks"])

    def test_a_broken_manifest_is_moved_aside(self):
        self.install()
        manifest = os.path.join(self.home, ".kit-manifest.json")
        self.write(manifest, "[1, 2]")
        out = self.install()
        self.assertIn("cannot be read", out)
        self.assertTrue(any(n.startswith(".kit-manifest.json.broken-") for n in os.listdir(self.home)))

    # ------------------------------------------------------------ the kit's commands on PATH

    def fake_cca(self):
        # The switcher may not be in this tree yet; the PATH step only needs a command in bin/.
        return os.path.isfile(os.path.join(ROOT, "claude", "bin", "cca"))

    def test_commands_are_linked_where_path_already_looks(self):
        if not self.fake_cca():
            self.skipTest("claude/bin/cca is not in this tree")
        os.makedirs(self.links)
        out = self.install(path_extra=self.links)
        link = os.path.join(self.links, "cca")
        self.assertTrue(os.path.islink(link), out)
        self.assertEqual(os.readlink(link), os.path.join(self.home, "bin", "cca"))
        # The rc file gets the cc function (test-install-cc-posix.py), never a PATH line.
        self.assertNotIn("export PATH", self.read(self.rc))
        self.assertNotIn("linked", self.install(path_extra=self.links))

    def test_a_name_that_is_not_ours_is_kept(self):
        if not self.fake_cca():
            self.skipTest("claude/bin/cca is not in this tree")
        self.write(os.path.join(self.links, "cca"), "mine\n")
        out = self.install(path_extra=self.links)
        self.assertIn("keep %s" % os.path.join(self.links, "cca"), out)
        self.assertEqual(self.read(os.path.join(self.links, "cca")), "mine\n")

    def test_without_a_link_folder_the_rc_file_gets_one_appended_line(self):
        if not self.fake_cca():
            self.skipTest("claude/bin/cca is not in this tree")
        self.write(self.rc, "export FOO=1")
        self.install()
        self.install("--force")
        text = self.read(self.rc)
        line = 'export PATH="$PATH:%s"' % os.path.join(self.home, "bin")
        self.assertTrue(text.startswith("export FOO=1\n"))
        self.assertEqual(text.count(line), 1)

    def test_unattended_leaves_the_path_alone(self):
        out = self.install("--unattended")
        self.assertIn("left as it is (--unattended)", out)
        self.assertFalse(os.path.exists(self.rc))

    # ------------------------------------------------------------ refusals

    def test_refusals_write_nothing(self):
        for args in (["--gradle-hook"], ["--unattended", "--project", self.base],
                     ["--auto-update", "--no-auto-update"]):
            out = self.install(*args, code=1)
            self.assertIn("Nothing was written", out)
        self.assertFalse(os.path.exists(self.home))

    # ------------------------------------------------------------ project scope

    def git(self, *args):
        subprocess.run(["git"] + list(args), check=True, capture_output=True)

    def test_a_project_is_scaffolded_with_the_python_branch_hooks(self):
        proj = os.path.join(self.base, "app")
        os.makedirs(proj)
        self.git("init", "-q", proj)
        out = self.install("--project", proj, "--local-only")
        self.assertIn("Adoption preflight", out)
        for rel in ("AGENTS.md", "CLAUDE.md", "CLAUDE.project.md", ".claude/settings.json",
                    ".claude/hooks/branch-check.py", ".claude/hooks/branch-upstream-fix.py",
                    "scripts/hooks/run-logged.py"):
            self.assertTrue(os.path.isfile(os.path.join(proj, rel)), rel)
        local = self.read(os.path.join(proj, ".claude", "settings.local.json"))
        self.assertIn("python3", local)
        self.assertNotIn("powershell", local)
        self.assertFalse(os.path.exists(os.path.join(proj, ".claude", "hooks", "branch-check.ps1")))
        exclude = self.read(os.path.join(proj, ".git", "info", "exclude"))
        for line in (".claude/", "AGENTS.md", "CLAUDE.md", "CLAUDE.project.md", "scripts/hooks/run-logged.py"):
            self.assertIn("\n" + line + "\n", exclude)
        again = self.install("--project", proj, "--local-only")
        self.assertIn("already excluded", again)

    def test_the_branch_hook_sets_the_upstream_of_a_feature_branch(self):
        proj = os.path.join(self.base, "app")
        os.makedirs(proj)
        self.git("init", "-q", "-b", "main", proj)
        self.git("-C", proj, "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
        self.git("-C", proj, "checkout", "-q", "-b", "1234-thing")
        self.install("--project", proj)
        done = subprocess.run([sys.executable, os.path.join(proj, ".claude", "hooks", "branch-upstream-fix.py")],
                              cwd=proj, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0)
        self.assertIn("push target set to origin/1234-thing", done.stdout)
        check = subprocess.run([sys.executable, os.path.join(proj, ".claude", "hooks", "branch-check.py")],
                               cwd=proj, capture_output=True, text=True)
        self.assertIn("On story branch '1234-thing' (work item 1234).", check.stdout)

    def test_the_gradle_hook_is_refused_over_a_runner_of_yours(self):
        proj = os.path.join(self.base, "app")
        self.write(os.path.join(proj, "scripts", "hooks", "run-logged.py"), "print('mine')\n")
        out = self.install("--project", proj, "--gradle-hook", code=1)
        self.assertIn("refused: --gradle-hook would wire a hook", out)
        self.assertFalse(os.path.exists(os.path.join(proj, ".claude")))
        out = self.install("--project", proj, "--gradle-hook", "--force")
        settings = json.loads(self.read(os.path.join(proj, ".claude", "settings.json")))
        command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertTrue(command.startswith("python3 "), command)
        self.assertIn("filter-gradle-output.py", command)


if __name__ == "__main__":
    unittest.main()
