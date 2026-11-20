"""The optional Herdr step of install.py on macOS and Linux (install/kit_herdr.py): the switches, the question, and
each step against stubs, and the macOS launcher herdr/hotkey/launch-herdr.sh.

    python3 scripts/tests/test-install-herdr-posix.py

No test here reaches the real Herdr or opens a window. HOME points into a sandbox and PATH holds only the system
folders, so neither the herdr on this machine nor its ~/.local/bin is found; every run that can call herdr names a
stub through KIT_HERDR_EXE, or gets one from a stub installer (KIT_HERDR_INSTALLER) inside the sandbox; the config
folder is KIT_HERDR_CONFIG_DIR, also inside the sandbox; and the launcher calls a stub osascript (KIT_OSASCRIPT).
Skipped on Windows, where scripts/tests/test-install-herdr.ps1 covers install/herdr.ps1.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MODULE = os.path.join(REPO, "install", "kit_herdr.py")
KIT_CONFIG = os.path.join(REPO, "herdr", "config.toml")
LAUNCHER = os.path.join(REPO, "herdr", "hotkey", "launch-herdr.sh")
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

spec = importlib.util.spec_from_file_location("kit_herdr", MODULE)
herdr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(herdr)

# A herdr that records each call and the profile it was called under. channel show answers HERDR_STUB_CHANNEL
# (preview when unset), or fails on stderr when that is "stderr".
STUB_HERDR = r'''#!/bin/sh
echo "$* |${CLAUDE_CONFIG_DIR:-}" >> "%(log)s"
if [ "$1 $2" = "channel show" ]; then
    if [ "${HERDR_STUB_CHANNEL:-}" = stderr ]; then echo "error: unknown subcommand show" >&2; exit 2; fi
    echo "${HERDR_STUB_CHANNEL:-preview}"
    exit 0
fi
if [ "$1 $2" = "channel set" ]; then echo "Herdr update channel set to $3."; exit 0; fi
echo "stub herdr $*"
'''


def read(path):
    with open(path, "rb") as handle:
        return handle.read()


@unittest.skipIf(os.name == "nt", "install/herdr.ps1 is the Windows step, with its own suite")
class HerdrStepTest(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="kit-herdr-test-"))
        self.user = os.path.join(self.base, "user")
        self.kit_home = os.path.join(self.user, ".claude")
        self.config_dir = os.path.join(self.base, "config", "herdr")
        self.config = os.path.join(self.config_dir, "config.toml")
        self.stubs = os.path.join(self.base, "stubs")
        self.calls_log = os.path.join(self.base, "herdr-calls.txt")
        self.hotkey_mark = os.path.join(self.base, "hotkey-ran.txt")
        self.tmp = os.path.join(self.base, "tmp")
        for folder in (self.kit_home, self.stubs, self.tmp):
            os.makedirs(folder)
        self.stub = os.path.join(self.stubs, "herdr")
        self.write_exe(self.stub, STUB_HERDR % {"log": self.calls_log})
        self.hotkey = os.path.join(self.stubs, "hotkey.sh")
        self.write_exe(self.hotkey, 'echo "$1" > "%s"; echo "Created: stub hotkey"\n' % self.hotkey_mark)
        self.env = mock.patch.dict(os.environ, {"HOME": self.user, "PATH": SYSTEM_PATH, "TMPDIR": self.tmp,
                                                "KIT_HERDR_CONFIG_DIR": self.config_dir})
        self.env.start()
        for name in ("KIT_HERDR_EXE", "KIT_HERDR_HOTKEY", "KIT_HERDR_INSTALLER", "KIT_HERDR_ANSWER",
                     "KIT_ASSUME_INTERACTIVE", "HERDR_STUB_CHANNEL", "CLAUDE_CONFIG_DIR", "HERDR_CONFIG_PATH",
                     "XDG_CONFIG_HOME", "HERDR_INSTALL_DIR", "KIT_HOME"):
            os.environ.pop(name, None)
        self.tempdir = mock.patch.object(tempfile, "tempdir", self.tmp)
        self.tempdir.start()
        # the guard every test stands on: nothing here can find the herdr of this machine
        self.assertIsNone(herdr.find_herdr(), "the sandbox must hide every real herdr")
        self.lines = []

    def tearDown(self):
        self.tempdir.stop()
        self.env.stop()
        shutil.rmtree(self.base, ignore_errors=True)

    def write_exe(self, path, text):
        with open(path, "w", newline="\n") as handle:
            handle.write(text)
        os.chmod(path, 0o755)

    def use_stubs(self):
        os.environ["KIT_HERDR_EXE"] = self.stub
        os.environ["KIT_HERDR_HOTKEY"] = self.hotkey

    def install(self, choice="yes", dry_run=False):
        self.lines = []
        herdr.install_herdr(REPO, self.kit_home, choice, dry_run=dry_run, out=self.lines.append)
        return "\n".join(self.lines)

    def calls(self):
        if not os.path.exists(self.calls_log):
            return []
        with open(self.calls_log) as handle:
            return handle.read().splitlines()

    # ---- the switches and the question --------------------------------------------------------------------------

    def test_the_choice(self):
        with self.assertRaises(ValueError):
            herdr.get_choice(True, True, False)
        self.assertEqual(herdr.get_choice(True, False, False), "yes")
        self.assertEqual(herdr.get_choice(False, True, True), "no")
        self.assertEqual(herdr.get_choice(False, False, True), "ask", "a dry run never asks")
        os.environ["KIT_ASSUME_INTERACTIVE"] = "0"
        self.assertEqual(herdr.get_choice(False, False, False), "unattended", "no console, no question")
        for answer, expected in (("n", "no"), ("No ", "no"), (" ", "yes"), ("y", "yes"), ("", "yes")):
            os.environ["KIT_HERDR_ANSWER"] = answer
            self.assertEqual(herdr.get_choice(False, False, False), expected, repr(answer))
        del os.environ["KIT_HERDR_ANSWER"]
        os.environ["KIT_ASSUME_INTERACTIVE"] = "1"
        with mock.patch("builtins.input", return_value="no") as asked:
            self.assertEqual(herdr.get_choice(False, False, False), "no")
            self.assertIn("[Y/n]", asked.call_args[0][0])
        with mock.patch("builtins.input", return_value=""):
            self.assertEqual(herdr.get_choice(False, False, False), "yes", "Enter is yes")

    def test_no_and_unattended_add_nothing(self):
        self.assertIn("herdr        not added. Add it later with: install.py --herdr", self.install("no"))
        self.assertIn("there is no console to ask. Pass --herdr to add it", self.install("unattended"))
        self.assertEqual(self.lines[0], "", "the step opens with a blank line, as install.ps1's does")
        self.assertFalse(os.path.exists(self.config))

    def test_a_dry_run_lists_the_steps_and_writes_nothing(self):
        out = self.install("ask", dry_run=True)
        self.assertIn('a real run asks "Add Herdr? [Y/n]"', out)
        self.assertIn("  install    the official installer, https://herdr.dev/install.sh", out)
        self.assertIn("  config     write " + self.config, out)
        self.assertIn("  integrate  herdr integration install claude", out)
        if sys.platform == "darwin":
            self.assertIn("/bin/sh %s, key Ctrl+Option+N" % LAUNCHER, out)
        self.assertFalse(os.path.exists(self.config))
        self.use_stubs()
        out = self.install("yes", dry_run=True)
        self.assertIn("  install    skip, herdr is already at " + self.stub, out)
        self.assertIn("  hotkey     run " + self.hotkey, out)
        self.assertEqual(self.calls(), [], "a dry run calls no herdr")

    # ---- an installed herdr -------------------------------------------------------------------------------------

    def test_an_installed_herdr_on_preview(self):
        self.use_stubs()
        out = self.install()
        self.assertIn("  ok    already installed at " + self.stub, out)
        self.assertIn("  ok    channel preview", out)
        calls = self.calls()
        self.assertIn("channel show |", calls)
        self.assertFalse([call for call in calls if call.startswith("channel set")], "the channel is never changed")
        self.assertIn("integration install claude |" + self.kit_home, calls, "the integration lands in the kit home")
        self.assertEqual(read(self.config), read(KIT_CONFIG), "config.toml is the kit version")
        self.assertIn("  ok    config: write " + self.config, out)
        with open(self.hotkey_mark) as handle:
            self.assertEqual(handle.read().strip(), LAUNCHER, "the hotkey step ran, given the launcher")
        self.assertIn("  ok    hotkey Ctrl+Option+N: Created: stub hotkey", out)
        out = self.install()
        self.assertIn("  ok    config: skip same " + self.config, out)

    def test_a_herdr_on_stable_stays_there_and_the_integration_follows_the_kit_home(self):
        self.use_stubs()
        os.makedirs(self.config_dir)
        with open(self.config, "w") as handle:
            handle.write('[update]\nchannel = "stable"\n')
        os.environ["HERDR_STUB_CHANNEL"] = "stable"
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.base, "another-profile")
        out = self.install()
        self.assertIn("  note  your Herdr follows 'stable'; this kit is verified on preview. To follow it: herdr "
                      "channel set preview", out)
        self.assertFalse([call for call in self.calls() if call.startswith("channel set")])
        self.assertIn("integration install claude |" + self.kit_home, self.calls(),
                      "the kit home, not the profile the shell points at")
        with open(self.config) as handle:
            self.assertEqual(handle.read(), '[update]\nchannel = "stable"\n', "your config.toml is untouched")
        self.assertEqual(read(self.config + ".new"), read(KIT_CONFIG))

    def test_a_herdr_on_stable_with_no_config_is_told_the_kit_file_names_preview(self):
        self.use_stubs()
        os.environ["HERDR_STUB_CHANNEL"] = "stable"
        out = self.install()
        self.assertIn("  note  your Herdr follows 'stable' and has no config.toml; the kit config.toml written next "
                      "names preview", out)
        self.assertIn("To stay on 'stable': herdr channel set stable", out)
        self.assertLess(out.index("  note  your Herdr follows"), out.index("  ok    config: write"),
                        "the channel is read before the kit file lands")

    def test_a_channel_that_cannot_be_read_never_fails_the_step(self):
        self.use_stubs()
        os.environ["HERDR_STUB_CHANNEL"] = "stderr"
        out = self.install()
        self.assertIn("  note  could not read the channel of your Herdr; this kit is verified on preview", out)
        self.assertIn("  ok    integration install claude", out, "the next steps still run")
        self.assertNotIn("error: unknown subcommand", out)

    def test_a_config_of_yours_is_kept_and_the_kit_version_lands_beside_it(self):
        self.use_stubs()
        os.makedirs(self.config_dir)
        with open(self.config, "w") as handle:
            handle.write("mine = true\n")
        out = self.install()
        with open(self.config) as handle:
            self.assertEqual(handle.read(), "mine = true\n")
        self.assertEqual(read(self.config + ".new"), read(KIT_CONFIG))
        self.assertIn("  ok    config: keep yours %s, the kit version lands beside it as config.toml.new" % self.config,
                      out)

    def test_the_config_path_is_the_one_herdr_reads(self):
        del os.environ["KIT_HERDR_CONFIG_DIR"]
        self.assertEqual(herdr.config_path(), os.path.join(self.user, ".config", "herdr", "config.toml"))
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.base, "xdg")
        self.assertEqual(herdr.config_path(), os.path.join(self.base, "xdg", "herdr", "config.toml"))
        os.environ["HERDR_CONFIG_PATH"] = os.path.join(self.base, "mine.toml")
        self.assertEqual(herdr.config_path(), os.path.join(self.base, "mine.toml"))

    # ---- a fresh install ----------------------------------------------------------------------------------------

    def stub_installer(self, exit_code=0, put_herdr=True):
        """An installer that records how it was run and, like install.sh, puts herdr in ~/.local/bin."""
        path = os.path.join(self.stubs, "installer.sh")
        body = ['echo "$0" > "%s"' % os.path.join(self.base, "installer-path.txt"),
                'echo "${HERDR_CHANNEL:-}" > "%s"' % os.path.join(self.base, "installer-channel.txt")]
        if put_herdr:
            body += ['mkdir -p "$HOME/.local/bin"', 'cp "%s" "$HOME/.local/bin/herdr"' % self.stub]
        body += ["printf '  \\033[32m>\\033[0m installed herdr\\n'", "exit %d" % exit_code]
        with open(path, "w", newline="\n") as handle:
            handle.write("\n".join(body) + "\n")
        os.environ["KIT_HERDR_INSTALLER"] = path
        os.environ["KIT_HERDR_HOTKEY"] = self.hotkey
        return path

    def test_a_fresh_install_runs_the_saved_installer_then_moves_to_preview(self):
        self.stub_installer()
        out = self.install()
        self.assertIn("  ok    the Herdr installer: > installed herdr", out, "its last line, with no colour codes")
        with open(os.path.join(self.base, "installer-path.txt")) as handle:
            saved = handle.read().strip()
        self.assertRegex(saved, "^" + re.escape(self.tmp) + r"/kit-herdr-[^/]+/install\.sh$",
                         "it ran as a file saved in a temporary folder of its own")
        self.assertFalse(os.path.exists(os.path.dirname(saved)), "and the folder is removed after the run")
        local_bin = os.path.join(self.user, ".local", "bin")
        self.assertIn('  note  %s is not on your PATH; add it in your shell profile so a new terminal finds herdr: '
                      'export PATH="%s:$PATH"' % (local_bin, local_bin), out)
        self.assertEqual(read(self.config), read(KIT_CONFIG))
        calls = self.calls()
        self.assertIn("channel set preview |", calls, "the kit config.toml names preview; channel set installs it")
        self.assertLess(out.index("  ok    config: write"), out.index("  ok    channel preview"),
                        "after the kit config.toml is in place, so the command leaves it as it is")
        self.assertIn("integration install claude |" + self.kit_home, calls)
        self.assertTrue(os.path.exists(self.hotkey_mark))

    def test_a_fresh_install_never_edits_a_config_of_yours_for_the_channel(self):
        self.stub_installer()
        os.makedirs(self.config_dir)
        with open(self.config, "w") as handle:
            handle.write("mine = true\n")
        os.environ["HERDR_STUB_CHANNEL"] = "stable"
        out = self.install()
        self.assertFalse([call for call in self.calls() if call.startswith("channel set")])
        self.assertIn("  note  your config.toml keeps Herdr on 'stable'; this kit is verified on preview", out)
        with open(self.config) as handle:
            self.assertEqual(handle.read(), "mine = true\n")

    def test_a_failing_installer_stops_the_herdr_steps_never_the_install(self):
        self.stub_installer(exit_code=3, put_herdr=False)
        out = self.install()
        self.assertIn("  FAIL  the Herdr installer (exit 3): > installed herdr", out)
        self.assertIn("  skip  the rest of the Herdr setup, since Herdr did not install", out)
        self.assertFalse(os.path.exists(self.config))
        self.assertFalse(os.path.exists(self.hotkey_mark), "no hotkey after a failed install")
        with open(os.path.join(self.base, "installer-channel.txt")) as handle:
            self.assertEqual(handle.read().strip(), "preview", "asked for preview, should install.sh learn it")
        self.assertEqual(os.listdir(self.tmp), [], "the saved installer is removed")
        os.environ["KIT_HERDR_INSTALLER"] = os.path.join(self.stubs, "no-such-installer.sh")
        out = self.install()
        self.assertIn("  FAIL  the Herdr installer (exit 1)", out, "a download that fails is a FAIL line too")
        self.assertIn("  skip  the rest of the Herdr setup", out)

    def test_an_installer_that_leaves_no_herdr_to_find(self):
        self.stub_installer(put_herdr=False)
        out = self.install()
        self.assertIn("  FAIL  Herdr installed, but this run cannot find herdr. Open a new terminal and run: install.py "
                      "--herdr", out)
        self.assertFalse(os.path.exists(self.config))

    # ---- the hotkey ---------------------------------------------------------------------------------------------

    def test_the_hotkey_step_points_at_the_launcher_and_binds_nothing(self):
        os.environ["KIT_HERDR_EXE"] = self.stub
        with mock.patch.object(sys, "platform", "darwin"):
            out = self.install()
        self.assertIn("  note  hotkey Ctrl+Option+N is one step by hand on macOS", out)
        self.assertIn("a shortcut that runs /bin/sh %s, with Ctrl+Option+N as its key." % LAUNCHER, out)
        self.assertIn("Steps: %s, section macOS." % os.path.join(REPO, "herdr", "README.md"), out)
        with mock.patch.object(sys, "platform", "linux"):
            out = self.install()
        self.assertIn("  note  hotkey: none on Linux", out)


@unittest.skipUnless(sys.platform == "darwin", "the launcher is macOS")
class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="kit-launcher-test-"))
        self.user = os.path.join(self.base, "user")
        self.herdr = os.path.join(self.user, ".local", "bin", "herdr")
        os.makedirs(os.path.dirname(self.herdr))
        with open(self.herdr, "w") as handle:
            handle.write("#!/bin/sh\npwd\n")
        os.chmod(self.herdr, 0o755)
        self.seen = os.path.join(self.base, "osascript")
        self.osascript = os.path.join(self.base, "fake-osascript")
        with open(self.osascript, "w") as handle:
            handle.write('#!/bin/sh\nprintf "%%s\\n" "$@" > "%s.args"\ncat > "%s.script"\n' % (self.seen, self.seen))
        os.chmod(self.osascript, 0o755)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def launch(self, *args, **env):
        full = {"HOME": self.user, "PATH": SYSTEM_PATH, "KIT_OSASCRIPT": self.osascript,
                "KIT_HERDR_TERMINAL": "Terminal"}
        full.update(env)
        return subprocess.run([LAUNCHER] + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True, env=full, timeout=60)

    def seen_call(self):
        with open(self.seen + ".args") as handle:
            args = handle.read().splitlines()
        with open(self.seen + ".script") as handle:
            return args, handle.read()

    def test_the_file_is_a_posix_script_with_lf_endings(self):
        data = read(LAUNCHER)
        self.assertNotIn(b"\r", data)
        self.assertTrue(data.startswith(b"#!/bin/sh\n"))
        self.assertTrue(os.access(LAUNCHER, os.X_OK), "executable")
        self.assertEqual(subprocess.run(["sh", "-n", LAUNCHER]).returncode, 0)

    def test_terminal_opens_herdr_in_the_folder(self):
        folder = os.path.join(self.base, "it's a repo")
        os.makedirs(folder)
        process = self.launch(folder)
        self.assertEqual(process.returncode, 0, process.stdout)
        args, script = self.seen_call()
        self.assertEqual(args[0], "-", "the script comes on stdin")
        self.assertIn('tell application "Terminal"', script)
        self.assertIn("do script", script)
        self.assertNotIn("iTerm", script, "no other app is named: osascript would ask where it is")
        # the command, run by a shell, lands in the folder and starts the herdr found, quotes and all
        ran = subprocess.run(["sh", "-c", args[1]], stdout=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(ran.stdout.strip(), folder)
        self.assertIn("'%s'" % self.herdr, args[1], "herdr by its full path, found off the bare PATH")

    def test_no_folder_is_the_home_folder_and_a_missing_one_is_refused(self):
        self.assertEqual(self.launch().returncode, 0)
        args, _ = self.seen_call()
        self.assertTrue(args[1].startswith("cd '%s' && " % self.user), args[1])
        process = self.launch(os.path.join(self.base, "no-such-folder"))
        self.assertEqual(process.returncode, 1)
        self.assertIn("no such folder", process.stdout)

    def test_iterm_gets_a_script_of_its_own(self):
        self.assertEqual(self.launch(KIT_HERDR_TERMINAL="iTerm").returncode, 0)
        _, script = self.seen_call()
        self.assertIn('tell application "iTerm"', script)
        self.assertIn("create window with default profile", script)
        self.assertNotIn('"Terminal"', script)
        process = self.launch(KIT_HERDR_TERMINAL="Ghostty")
        self.assertEqual(process.returncode, 1)
        self.assertIn("use Terminal or iTerm", process.stdout)

    @unittest.skipUnless(os.path.exists("/usr/bin/osacompile"), "needs osacompile")
    def test_the_terminal_script_compiles(self):
        """Compiled, never run: compiling reads Terminal's dictionary and opens no window."""
        self.assertEqual(self.launch().returncode, 0)
        _, script = self.seen_call()
        source = os.path.join(self.base, "terminal.applescript")
        with open(source, "w") as handle:
            handle.write(script)
        compiled = subprocess.run(["/usr/bin/osacompile", "-o", os.path.join(self.base, "terminal.scpt"), source],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(compiled.returncode, 0, compiled.stdout)


if __name__ == "__main__":
    unittest.main()
