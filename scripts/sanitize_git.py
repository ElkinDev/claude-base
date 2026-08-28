"""Git plumbing for the sanitization guard: running git, and reading objects out of a repository.

`scripts/sanitize_guard.py` holds the scan that uses this, `scripts/sanitize-check.py` is the
command. Nothing here decides anything about a finding; it only answers what git says. Every call
is bounded by a timeout, because a hook that hangs is a hook people disable.
"""
import os
import subprocess
from pathlib import Path

from sanitize_rules import GuardError


def git(args, binary=False, stdin=None):
    """Run git and return (exit code, output, stderr). Output is text unless binary=True."""
    proc = subprocess.run(["git"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          input=stdin, timeout=300)
    out = proc.stdout if binary else proc.stdout.decode("utf-8", "replace")
    return proc.returncode, out, proc.stderr.decode("utf-8", "replace")


def call(args, binary=False, stdin=None):
    """git, but a non-zero exit is a GuardError carrying what git said. Never fails silently."""
    code, out, err = git(args, binary=binary, stdin=stdin)
    if code != 0:
        shown = [item for item in args]
        if shown[:1] == ["-C"]:
            shown = shown[2:]
        message = " ".join(err.split()) or "exit %d" % code
        raise GuardError("git %s: %s" % (" ".join(shown[:3]), message))
    return out


def repo_root():
    """(top level, inside a repository). The current folder stands in when there is no repo."""
    code, out, _ = git(["rev-parse", "--show-toplevel"])
    if code == 0 and out.strip():
        return Path(os.path.abspath(out.strip())), True
    return Path.cwd(), False


def split_z(text):
    return [item for item in text.split("\0") if item]


def batch_blobs(repo, specs):
    """[(spec, bytes or None)] for git object names, read through one `git cat-file` process."""
    if not specs:
        return []
    payload = ("\n".join(specs) + "\n").encode("utf-8")
    out = call(["-C", str(repo), "cat-file", "--batch"], binary=True, stdin=payload)
    result = []
    position = 0
    for spec in specs:
        end = out.find(b"\n", position)
        if end < 0:
            result.append((spec, None))
            continue
        header = out[position:end].decode("utf-8", "replace")
        position = end + 1
        parts = header.split()
        if len(parts) < 3 or not parts[-1].isdigit():
            result.append((spec, None))
            continue
        size = int(parts[-1])
        result.append((spec, out[position:position + size]))
        position += size + 1
    return result


def resolve(repo, ref):
    """The sha a ref names, unpeeled, or "" when git cannot resolve it at all."""
    code, out, _ = git(["-C", str(repo), "rev-parse", "--verify", "--quiet", ref])
    if code != 0 or not out.strip():
        return ""
    return out.strip().splitlines()[0]


def object_type(repo, sha):
    """commit, tag, tree, blob, or "" when git will not say."""
    code, out, _ = git(["-C", str(repo), "cat-file", "-t", sha])
    return out.strip() if code == 0 else ""


def tag_target(body):
    """The sha in the `object` header of a tag object, read from its header block only."""
    for line in body.split("\n\n", 1)[0].splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "object":
            return parts[1]
    return ""


def changed_blobs(repo, sha):
    """[(path, blob sha)] for what a commit added or modified, its own paths only."""
    out = call(["-C", str(repo), "diff-tree", "-r", "-m", "-z", "--no-commit-id", "--root",
                "--diff-filter=AM", sha])
    items = out.split("\0")
    entries = []
    index = 0
    while index < len(items) - 1:
        header = items[index]
        if not header.startswith(":"):
            index += 1
            continue
        parts = header.split()
        if len(parts) >= 5:
            entries.append((items[index + 1], parts[3]))
        index += 2
    return entries


def tree_entries(repo, sha):
    """[(path, blob sha or "")] for every entry under a tree, at any depth.

    `-t` keeps the directory entries themselves, which `-r` alone drops: an empty subtree and a
    submodule publish a name and no content, and the name is what a forged tree hides a term in.
    Only a blob is given a sha, so only a blob is ever read.
    """
    entries = []
    for record in split_z(call(["-C", str(repo), "ls-tree", "-r", "-t", "-z", sha])):
        header, tab, name = record.partition("\t")
        parts = header.split()
        if tab and len(parts) >= 3:
            entries.append((name, parts[2] if parts[1] == "blob" else ""))
    return entries
