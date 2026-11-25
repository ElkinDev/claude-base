"""The `cc` shell function of macOS and Linux: `cc work` opens an account through cca, the same line
Windows types, and `cc` alone lists them. A first word that starts with a dash or holds a dot or a
slash (`cc -o app main.c`, `cc --version`, `cc ./x.c`) still runs the C compiler.

It is a function in the interactive shell's rc file, never a file on PATH. make, pip, node-gyp and
every other build call `cc` through PATH from a child process, which never sees a function of the
terminal, so no build changes. The block is appended once between its own markers and never
rewritten; an rc file that already defines a `cc` of the user's own is left alone. A rollback does
not remove it, the same as the PATH line. docs/ACCOUNTS.md, "macOS and Linux", is the user's page.
"""
import os
import re
import tempfile

from kit_lib import plan_line

BEGIN = "# >>> claude-base: cc opens a Claude Code account, cc -o app main.c still compiles (docs/ACCOUNTS.md)"
END = "# <<< claude-base: cc"
# POSIX case patterns, so one body runs in zsh and in bash.
BODY = """cc() {
  if [ "$#" -eq 0 ]; then cca; return; fi
  case "$1" in
    -*|*.*|*/*) command cc "$@" ;;
    *) cca "$@" ;;
  esac
}"""
BLOCK = "%s\n%s\n%s\n" % (BEGIN, BODY, END)
# A cc the user defined: a function in either spelling, or an alias.
OWN_CC = re.compile(r"^\s*(function\s+cc\b|cc\s*\(\s*\)|alias\s+cc=)", re.M)


def _read(path):
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def add_cc_function(rc, bin_dir, targets, dry_run):
    """Append the block to rc when the run installs cca. Returns the plan lines to print; none when
    the block is already there. KIT_SHELL_RC points a test at a file of its own."""
    if os.path.join(bin_dir, "cca") not in targets:
        return []
    temp = os.path.realpath(tempfile.gettempdir()) + os.sep
    if not os.environ.get("KIT_SHELL_RC") and os.path.realpath(bin_dir).startswith(temp):
        # A kit home under the temp folder is a test's: it never reaches the real shell setup.
        return [plan_line("cc", "skip %s is under the temp folder, so no shell file gets the cc function" % bin_dir)]
    if rc is None:
        return [plan_line("cc", "your shell is not zsh or bash: cca opens the accounts, or add the cc function "
                                "of docs/ACCOUNTS.md to its rc file by hand")]
    current = _read(rc)
    if BEGIN in current:
        return []
    if OWN_CC.search(current):
        return [plan_line("cc", "keep the cc already defined in %s; cca opens the accounts" % rc)]
    if dry_run:
        return [plan_line("cc", "would append to %s the cc function: cc <account> opens it, as on Windows" % rc)]
    with open(rc, "a", encoding="utf-8") as handle:
        if current and not current.endswith("\n"):
            handle.write("\n")
        handle.write("\n" + BLOCK)
    return [plan_line("cc", "appended to %s the cc function: in a terminal opened from now on, cc <account> "
                            "opens it, as on Windows" % rc)]
