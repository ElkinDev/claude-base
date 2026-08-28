"""Shared sandbox for the sanitize guard suites.

Not a suite itself: `test-sanitize-check.py`, `test-sanitize-redaction.py` and
`test-sanitize-hooks.py` import it. Every test runs in a temporary git repository whose git
config is empty, so nothing on the machine can answer for the sandbox.

No leak is written into these files. The generic ones live in fixtures/sanitize/, each carrying a
whole-file waiver that the seed helper strips before a scan; the private ones are a made-up
codename that no generic rule matches. The waiver marker itself is built from two pieces so that
this file does not read as a waiver to the guard that scans it.
"""
import io
import os
import shutil
import subprocess
import sys
import unittest
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "sanitize-check.py")
FIXTURES = os.path.join(HERE, "fixtures", "sanitize")
KIT_ROOT = os.path.dirname(os.path.dirname(HERE))
FILE_WAIVER = "sanitize-ok:" + " all"
CODENAME = "zzquux"

# Leak-shaped strings are assembled from pieces, never written whole: these files are scanned by
# the guard's own full-repository run, and a home path or an address written out would fail it.
SLASH = chr(92)
PERSON = "realuser"
WIN_HOME = "C:" + SLASH + "Users" + SLASH + PERSON
MAC_HOME = "/Users/" + PERSON
LINUX_HOME = "/home/" + PERSON
ADDRESS = "first.last@" + "acme-corp.io"
# Written as escapes so these sources stay ascii: the kit's own non-ascii grep reads them.
CJK_NAME = "\u4e2d\u6587\u540d"


def write(path, text):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def read(path):
    with io.open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def rules_of(stdout):
    """The rule ids of the finding lines, in order."""
    skip = ("waived ", "notice:", "skipped whole:", "hard fail:")
    found = []
    for line in stdout.splitlines():
        if ": " in line and not line.startswith(skip) and "findings," not in line:
            found.append(line.split(":", 1)[0].strip())
    return found


def git_bash():
    """Git Bash, resolved the way scripts/doctor.py resolves it. None when there is none."""
    if os.name != "nt":
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git") or "git"
    try:
        out = subprocess.run([git, "--exec-path"], capture_output=True, text=True, timeout=30)
        base = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
    except OSError:
        base = ""
    if base:
        folder = os.path.abspath(base)
        while True:
            candidates.append(os.path.join(folder, "bin", "bash.exe"))
            candidates.append(os.path.join(folder, "usr", "bin", "bash.exe"))
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
    candidates.append(r"C:\Program Files\Git\usr\bin\bash.exe")
    candidates.append(r"C:\Program Files\Git\bin\bash.exe")
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


class GuardCase(unittest.TestCase):
    """A sandbox repository plus the helpers every guard suite needs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sanitize-check-")
        self.repo = os.path.join(self.tmp, "sandbox")
        os.makedirs(self.repo)
        empty_config = os.path.join(self.tmp, "gitconfig")
        write(empty_config, "")
        # The sandbox must not inherit the machine's git config: a global core.hooksPath there
        # would answer the hook tests instead of the sandbox.
        self.env = {"GIT_CONFIG_GLOBAL": empty_config, "GIT_CONFIG_SYSTEM": empty_config}
        self.git("init", "-q")
        self.git("config", "user.email", "tests@example.com")
        self.git("config", "user.name", "kit tests")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def environment(self, extra=None):
        """The sandbox environment. A value of None removes the variable, which is how a test
        asks for the conditions a hook actually runs under."""
        env = dict(os.environ)
        env.update(self.env)
        for key, value in (extra or {}).items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def git(self, *args, **kwargs):
        # utf-8 with replacement, never the console codepage: a hook's own output travels back
        # through git's pipe, and a non-ascii path in it must not break the test process.
        cwd = kwargs.pop("cwd", self.repo)
        return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=self.environment(),
                              input=kwargs.pop("stdin", None))

    def run_guard(self, *args, **kwargs):
        """Extra keywords reach subprocess.run, so a test can pick the decoding it needs."""
        cwd = kwargs.pop("cwd", self.repo)
        env = self.environment(kwargs.pop("env", None))
        return subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True,
                              text=True, cwd=cwd, env=env, **kwargs)

    def seed(self, name, target_name=None):
        """A fixture copied into the sandbox without its whole-file waiver, so it is scannable."""
        with io.open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        target = os.path.join(self.repo, target_name or name)
        write(target, "\n".join(line for line in lines if FILE_WAIVER not in line) + "\n")
        return target

    def file(self, name, body):
        write(os.path.join(self.repo, name), body)
        return os.path.join(self.repo, name)

    def denylist(self, *terms):
        """The untracked denylist, plus the ignore rule a real repository keeps beside it."""
        write(os.path.join(self.repo, ".gitignore"), ".sanitize/\n")
        write(os.path.join(self.repo, ".sanitize", "private-denylist.txt"),
              "\n".join(terms) + "\n")

    def commit(self, message, name="notes.md", body="nothing to see here\n"):
        write(os.path.join(self.repo, name), body)
        self.git("add", "-A")
        result = self.git("commit", "-q", "-m", message)
        # Strict on purpose: with the pre-commit hook installed a rejected commit leaves the
        # sandbox without the history the test is about, and the test would pass on the wrong
        # failure.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def hook(self, kind="pre-push"):
        return os.path.join(self.repo, ".git", "hooks", kind)

    def assertNoTerm(self, result, term=CODENAME):
        """Neither stream carries the private term, in any casing."""
        both = result.stdout + result.stderr
        self.assertNotIn(term.lower(), both.lower(), both)
