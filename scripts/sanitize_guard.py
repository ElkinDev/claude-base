"""Scanning for the sanitization guard: the line scan and the scan modes.

`scripts/sanitize-check.py` is the command, `scripts/sanitize_rules.py` holds the rule sets and
`scripts/sanitize_git.py` runs git. Nothing here prints; findings, waivers and notices are
collected on a Scan and rendered by the command, which is what keeps the redaction rules in one
place.
"""
import os
from pathlib import Path

from sanitize_git import (batch_blobs, call, changed_blobs, git, object_type, resolve, split_z,
                          tag_target, tree_entries)
from sanitize_rules import (ALL, MAX_BYTES, PRIVATE_DIR, REDACTED, REDACTED_PATH, SNIFF_BYTES,
                            WAIVER, Finding, GuardError, Waived, allowed, as_posix, is_fixture,
                            is_text_name, trim, under_private_dir)


# ---------------------------------------------------------------- the scan

class Scan(object):
    def __init__(self, rules, allows):
        self.rules = rules
        self.allows = allows
        self.private = [rule for rule in rules if rule.private]
        self.by_name = [rule for rule in rules if rule.names]
        self.findings = []
        self.waivers = []
        self.notices = []
        self.skipped = []
        self.files = 0

    # what a path is allowed to print ------------------------------------

    def path(self, label):
        """Scan a path itself and return the label to print for it, redacted when private."""
        label = as_posix(label)
        for rule in self.private:
            if rule.regex.search(label):
                shown = "%s (%s)" % (REDACTED_PATH, rule.id)
                self.findings.append(Finding("private-path", shown, 0, "", True))
                return shown
        if not os.path.isabs(label):
            for rule in self.by_name:
                if rule.private:
                    continue
                for match in rule.regex.finditer(label):
                    if allowed(rule, match, self.allows):
                        continue
                    self.findings.append(
                        Finding(rule.id, "%s (name)" % label, 0, trim(match.group(0)), False))
        return label

    # the line scan -------------------------------------------------------

    def text(self, label, body):
        """Scan one already-decoded unit: a file, a commit message, a tag object, the ref names."""
        lines = body.splitlines()
        first = next((index for index, line in enumerate(lines) if line.strip()), -1)
        for number, line in enumerate(lines, 1):
            found = WAIVER.search(line)
            if not found or found.group(1) != ALL:
                continue
            if number - 1 == first and is_fixture(label):
                self.skipped.append(label)
                # The whole-file waiver suppresses the generic rules only. A private name is
                # never allowed to hide behind a fixture, so those rules still read every line.
                self.scan_lines(label, lines, self.private)
                return
            self.findings.append(Finding("waiver-abuse", label, number, "", False))
        self.files += 1
        self.scan_lines(label, lines, self.rules)

    def scan_lines(self, label, lines, rules):
        for number, line in enumerate(lines, 1):
            if line.strip():
                self.line(label, number, line, rules)

    def line(self, label, number, line, rules):
        found = WAIVER.search(line)
        waived = found.group(1) if found else ""
        hits = []
        for rule in rules:
            for match in rule.regex.finditer(line):
                if not rule.private and allowed(rule, match, self.allows):
                    continue
                hits.append((rule, match))
        if not hits:
            return
        secret = any(rule.regex.search(line) for rule in self.private)
        for rule, match in hits:
            if waived and waived == rule.id:
                # A waiver is a claim about a generic rule matching something harmless. Nobody
                # gets to make that claim about a private name, in any file, so the attempt is
                # itself reported and the finding stands.
                if not rule.private:
                    self.waivers.append(Waived(rule.id, label, number))
                    continue
                self.findings.append(Finding("waiver-abuse", label, number, "", False))
            if rule.private:
                fragment = ""
            else:
                fragment = REDACTED if secret else trim(match.group(0))
            self.findings.append(Finding(rule.id, label, number, fragment, rule.private))

    def blob(self, label, data):
        """Scan raw bytes. A NUL byte in a file that should be text is itself a finding."""
        if data is None:
            return
        if len(data) > MAX_BYTES:
            self.notices.append("%s skipped: over %d MB" % (label, MAX_BYTES // (1024 * 1024)))
            return
        if b"\0" in data[:SNIFF_BYTES]:
            if not is_text_name(label, len(data)):
                self.notices.append("%s skipped: binary" % label)
                return
            self.findings.append(Finding("binary-in-text", label, 0, "", False))
        self.text(label, data.decode("utf-8", "replace").replace("\0", " "))


# ---------------------------------------------------------------- the scan modes

def scan_file(scan, label, data):
    if under_private_dir(label):
        return
    scan.blob(scan.path(label), data)


def scan_disk(scan, paths, base=None):
    """Files and folders read from the working tree."""
    for item in paths:
        path = Path(item)
        if path.is_dir():
            for root, folders, names in os.walk(str(path)):
                folders[:] = [name for name in folders if name not in (".git", PRIVATE_DIR)]
                scan_disk(scan, [os.path.join(root, name) for name in sorted(names)], base)
            continue
        try:
            data = path.read_bytes()
        except OSError as error:
            scan.notices.append("%s skipped: %s" % (item, error))
            continue
        label = str(path)
        if base:
            relative = os.path.relpath(str(path), str(base))
            label = relative if not relative.startswith("..") else str(path)
        scan_file(scan, as_posix(label), data)


def scan_all(scan, repo):
    names = split_z(call(["-C", str(repo), "ls-files", "-z"]))
    for name in names:
        if under_private_dir(name):
            continue
        try:
            data = (repo / name).read_bytes()
        except OSError as error:
            scan.notices.append("%s skipped: %s" % (name, error))
            continue
        scan_file(scan, name, data)


def scan_staged(scan, repo):
    names = split_z(call(["-C", str(repo), "diff", "--cached", "--name-only", "-z",
                          "--diff-filter=ACMR"]))
    names = [name for name in names if not under_private_dir(name)]
    for name, data in batch_blobs(repo, [":" + name for name in names]):
        scan_file(scan, name[1:], data)


def scan_pairs(scan, repo, entries):
    """Name-scan every (path, blob sha, source sha) triple, then read each distinct blob once.

    Every path is scanned even when several of them carry the same blob: deduplicating by blob
    sha before the name scan would let a rename publish a name for free. An entry with no blob,
    an empty subtree or a submodule, is name-scanned and nothing more.

    A path under the private directory is a hard failure here, not a skip. Only pushed objects
    reach this function, and a commit that once carried the denylist still carries it after a
    `git rm --cached`: the working tree looks clean while the push publishes every term. The
    contents are never read, so the failure says where it is and nothing else.
    """
    labels = {}
    blobs = {}
    for name, blob, source in entries:
        if under_private_dir(name):
            raise GuardError("private denylist is in the pushed history at %s:%s; rewrite the "
                             "history before pushing" % (source[:9], name))
        if name not in labels:
            labels[name] = scan.path(name)
        if blob:
            blobs.setdefault(blob, name)
    for blob, data in batch_blobs(repo, list(blobs)):
        scan.blob(labels[blobs[blob]], data)


def range_commits(scan, repo, left, right):
    """The commits a push would publish, falling back loudly when the remote sha is unknown."""
    if left.strip("0"):
        code, out, err = git(["-C", str(repo), "rev-list", "%s..%s" % (left, right)])
        if code == 0:
            return out.split()
        scan.notices.append("the remote sha is unknown here (%s), so every commit that is on no "
                            "remote was scanned instead" % " ".join(err.split()[:6]))
    return call(["-C", str(repo), "rev-list", right, "--not", "--remotes"]).split()


def peel(scan, repo, right):
    """Scan every annotated tag a pushed ref goes through, and return (sha, type) underneath.

    The whole tag object is scanned, header block included: the tagger identity a tag carries is
    published with it. The label is built from the sha, never from the ref name, because the ref
    name is untrusted input here and only the command knows how to print one safely.
    """
    sha = resolve(repo, right)
    seen = set()
    while sha and sha not in seen:
        kind = object_type(repo, sha)
        if kind != "tag":
            return sha, kind
        seen.add(sha)
        body = call(["-C", str(repo), "cat-file", "-p", sha])
        scan.text("tag %s" % sha[:9], body)
        sha = tag_target(body)
    return "", ""


def scan_range(scan, repo, spec, messages_only, refs):
    """A push: every object the ref publishes, and the pushed ref names themselves."""
    left, _, right = spec.partition("..")
    right = right.lstrip(".").strip() or "HEAD"
    if refs:
        scan.text("refs", "\n".join(refs))
    sha, kind = peel(scan, repo, right)
    if kind in ("blob", "tree"):
        # A tag can point straight at a blob or a tree, and pushing it publishes that object
        # without publishing a single commit. There is no range to walk, only the object.
        if messages_only:
            return
        if kind == "blob":
            for _, data in batch_blobs(repo, [sha]):
                scan.blob("blob %s" % sha[:9], data)
        else:
            pairs = tree_entries(repo, sha)
            scan_pairs(scan, repo, [(name, blob, sha) for name, blob in pairs])
        return
    commits = range_commits(scan, repo, left.strip(), sha or right)
    for commit in commits:
        scan.text("commit %s" % commit[:9],
                  call(["-C", str(repo), "log", "-1", "--format=%B", commit]))
    if messages_only:
        return
    entries = []
    for commit in commits:
        entries.extend((name, blob, commit) for name, blob in changed_blobs(repo, commit))
    scan_pairs(scan, repo, entries)
