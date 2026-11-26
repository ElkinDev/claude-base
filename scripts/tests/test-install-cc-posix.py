"""The cc shell function install.py appends to the rc file (install/kit_cc.py):
python3 scripts/tests/test-install-cc-posix.py

The routing cases source the block in a real bash and zsh against stub `cca` and `cc` commands, so
no account opens and no compiler runs. The install cases run the real installer into a throwaway
home, as test-install-posix.py does, so the user's own shell files are never touched. POSIX only.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
INSTALLER = os.path.join(ROOT, "install.py")
sys.path.insert(0, os.path.join(ROOT, "install"))
import kit_cc  # noqa: E402

SHELLS = [(name, path) for name, path in (("bash", shutil.which("bash")), ("zsh", shutil.which("zsh"))) if path]
FLAGS = {"bash": ["--norc", "--noprofile"], "zsh": ["-f"]}


@unittest.skipIf(os.name == "nt", "the cc function is for macOS and Linux shells")
class CcFunctionTest(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="kit-cc-"))
        self.user = os.path.join(self.base, "user")
        self.home = os.path.join(self.user, ".claude")
        self.bin_dir = os.path.join(self.home, "bin")
        self.rc = os.path.join(self.base, "rc")
        self.stubs = os.path.join(self.base, "stubs")
        os.makedirs(self.user)
        os.makedirs(self.stubs)
        for name, said in (("cca", "cca"), ("cc", "compiler")):
            path = os.path.join(self.stubs, name)
            self.write(path, '#!/bin/sh\necho "%s:$*"\n' % said)
            os.chmod(path, 0o755)
        self.saved = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)
        shutil.rmtree(self.base, ignore_errors=True)

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def write(self, path, text):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def run_cc(self, shell, line):
        name, path = shell
        self.write(self.rc, kit_cc.BLOCK)
        env = {"HOME": self.user, "PATH": os.pathsep.join([self.stubs, "/usr/bin", "/bin"])}
        done = subprocess.run([path] + FLAGS[name] + ["-c", ". '%s'\n%s" % (self.rc, line)], env=env,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def add(self, targets=None, dry_run=False, rc="default"):
        os.environ["KIT_SHELL_RC"] = self.rc
        targets = [os.path.join(self.bin_dir, "cca")] if targets is None else targets
        return kit_cc.add_cc_function(self.rc if rc == "default" else rc, self.bin_dir, targets, dry_run)

    def install(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ("KIT_HOME", "CLAUDE_CONFIG_DIR")}
        env.update(HOME=self.user, KIT_PATH_LINK_DIR=os.path.join(self.base, "links"), KIT_SHELL_RC=self.rc,
                   KIT_ASSUME_INTERACTIVE="0", PYTHONDONTWRITEBYTECODE="1", PATH="/usr/bin:/bin")
        done = subprocess.run([sys.executable, INSTALLER, "--no-herdr"] + list(args), capture_output=True,
                              text=True, env=env, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout

    # ------------------------------------------------------------ routing, in each shell

    def test_an_account_word_goes_to_cca_with_every_argument(self):
        self.assertTrue(SHELLS, "neither bash nor zsh is on this machine")
        for shell in SHELLS:
            with self.subTest(shell=shell[0]):
                self.assertEqual(self.run_cc(shell, "cc"), "cca:")
                self.assertEqual(self.run_cc(shell, "cc n"), "cca:n")
                self.assertEqual(self.run_cc(shell, "cc help"), "cca:help")
                self.assertEqual(self.run_cc(shell, "cc work -- -r"), "cca:work -- -r")

    def test_a_dash_a_dot_or_a_slash_goes_to_the_compiler(self):
        for shell in SHELLS:
            with self.subTest(shell=shell[0]):
                self.assertEqual(self.run_cc(shell, "cc -o app main.c"), "compiler:-o app main.c")
                self.assertEqual(self.run_cc(shell, "cc --version"), "compiler:--version")
                self.assertEqual(self.run_cc(shell, "cc main.c"), "compiler:main.c")
                self.assertEqual(self.run_cc(shell, "cc ./x"), "compiler:./x")

    def test_a_child_process_never_sees_the_function(self):
        # Mutation: the block exports the function. Red: a build's sh would open an account.
        for shell in SHELLS:
            with self.subTest(shell=shell[0]):
                self.assertEqual(self.run_cc(shell, "sh -c 'cc n'"), "compiler:n")

    # ------------------------------------------------------------ what lands in the rc file

    def test_the_block_is_appended_once_after_what_was_there(self):
        self.write(self.rc, "export FOO=1")
        self.assertIn("appended to", self.add()[0])
        self.assertEqual(self.add(), [])
        text = self.read(self.rc)
        self.assertTrue(text.startswith("export FOO=1\n\n" + kit_cc.BEGIN), text)
        self.assertEqual(text.count(kit_cc.BEGIN), 1)
        self.assertTrue(text.endswith(kit_cc.END + "\n"))

    def test_a_cc_of_the_users_own_is_kept(self):
        for own in ("cc() { echo mine; }\n", "function cc { echo mine; }\n", "alias cc='gcc'\n", "  cc () {\n}\n"):
            with self.subTest(own=own):
                self.write(self.rc, own)
                self.assertIn("keep the cc already defined", self.add()[0])
                self.assertEqual(self.read(self.rc), own)

    def test_a_dry_run_writes_nothing(self):
        self.assertIn("would append", self.add(dry_run=True)[0])
        self.assertFalse(os.path.exists(self.rc))

    def test_no_cca_in_the_run_means_no_function(self):
        self.assertEqual(self.add(targets=[os.path.join(self.home, "statusline.py")]), [])
        self.assertFalse(os.path.exists(self.rc))

    def test_an_unknown_shell_gets_the_hand_line(self):
        self.assertIn("add the cc function", self.add(rc=None)[0])

    def test_a_kit_home_under_the_temp_folder_never_reaches_the_real_rc(self):
        os.environ.pop("KIT_SHELL_RC", None)
        out = kit_cc.add_cc_function(self.rc, self.bin_dir, [os.path.join(self.bin_dir, "cca")], False)
        self.assertIn("under the temp folder", out[0])
        self.assertFalse(os.path.exists(self.rc))

    # ------------------------------------------------------------ through the installer

    def test_the_installer_appends_it_once_and_unattended_leaves_it_out(self):
        if not os.path.isfile(os.path.join(ROOT, "claude", "bin", "cca")):
            self.skipTest("claude/bin/cca is not in this tree")
        self.assertIn("the cc function", self.install())
        self.install("--force")
        self.assertEqual(self.read(self.rc).count(kit_cc.BEGIN), 1)
        os.remove(self.rc)
        self.assertNotIn("the cc function", self.install("--unattended"))
        self.assertFalse(os.path.exists(self.rc))


if __name__ == "__main__":
    unittest.main()
