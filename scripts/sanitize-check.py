#!/usr/bin/env python3
"""Keep private identifiers out of a public repository.

    python scripts/sanitize-check.py --all           every tracked text file
    python scripts/sanitize-check.py --staged        what the next commit would record
    python scripts/sanitize-check.py --range A..B    a push: every commit in the range, the
                                                     blobs it adds or modifies, and the names
                                                     of the refs passed with --ref-name
    python scripts/sanitize-check.py PATH [PATH ...] explicit files or folders
    python scripts/sanitize-check.py --install-hook  the pre-push guard (--pre-commit adds the
                                                     staged check as a pre-commit hook)

Three rule sets. The committed generic rules (`scripts/sanitize-rules.txt`) describe leaks
without naming anything: personal home paths, real email addresses, tracker org URLs, credential
shapes. The private denylist (`.sanitize/private-denylist.txt`, gitignored) holds the real names
of the setups this content came from. The allowlist (`scripts/sanitize-allow.txt`) clears a
sanctioned placeholder by full-matching the name a rule captured.

The output is meant to be publishable. A denylist term is never echoed: a private finding prints
its rule id and a location, a generic finding on the same line prints `redacted` instead of its
fragment, a path whose name carries a term prints as `<redacted path>`, and waiver reasons are
never printed at all. The `.sanitize` folder is never scanned in any mode.

Waivers. A line carrying `sanitize-ok: <rule-id> <reason>` is not reported for that rule and is
listed in the summary. The id `all` is the whole-file waiver: it is honoured on the first
non-empty line of a file under a `fixtures` directory, where it skips the file, and reported as
`waiver-abuse` anywhere else, with the file still scanned.

Exit codes: 0 clean, 1 findings, 2 usage or a hard failure (a tracked `.sanitize` file, a path
that does not exist, an unreadable rule set, a rule that does not compile, git failing).

The scan lives in `scripts/sanitize_guard.py`, the rule sets in `scripts/sanitize_rules.py` and
the git plumbing in `scripts/sanitize_git.py`; this file is the command.
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import sanitize_git as plumbing  # noqa: E402  (the path above is what makes it importable)
import sanitize_guard as guard  # noqa: E402
import sanitize_rules as rules  # noqa: E402

RULES_FILE = HERE / "sanitize-rules.txt"
ALLOW_FILE = HERE / "sanitize-allow.txt"
HOOK_SOURCE = HERE / "git-hooks"

MARKER = "# kit sanitize guard v1"
INVOKE = 'sh "$(git rev-parse --show-toplevel)/scripts/git-hooks/%s" "$@"'
HOOK_KINDS = ("pre-push", "pre-commit")


# ---------------------------------------------------------------- the git hooks

def hooks_dir(repo):
    _, out, _ = plumbing.git(["-C", str(repo), "rev-parse", "--git-path", "hooks"])
    path = Path(out.strip() or ".git/hooks")
    return path if path.is_absolute() else repo / path


def write_hook(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))
    try:
        os.chmod(str(target), 0o755)
    except OSError:
        pass


def install_hooks(repo, kinds):
    code, out, _ = plumbing.git(["-C", str(repo), "config", "--get", "core.hooksPath"])
    managed = out.strip() if code == 0 else ""
    if managed:
        print("core.hooksPath is set to %s, so nothing was written." % managed)
        for kind in kinds:
            print("Add this line to the %s hook it manages:" % kind)
            print("  " + INVOKE % kind)
        return 1
    target_dir = hooks_dir(repo)
    status = 0
    for kind in kinds:
        source = HOOK_SOURCE / kind
        if not source.is_file():
            print("missing hook source: %s" % source, file=sys.stderr)
            return 2
        target = target_dir / kind
        if target.exists():
            body = target.read_text(encoding="utf-8", errors="replace")
            if MARKER not in body:
                print("%s already exists and is not ours, so nothing was written." % target)
                print("Add this line to it:")
                print("  " + INVOKE % kind)
                status = 1
                continue
            write_hook(source, target)
            print("rewrote %s" % target)
            continue
        write_hook(source, target)
        print("installed %s" % target)
    return status


def uninstall_hooks(repo, kinds):
    target_dir = hooks_dir(repo)
    for kind in kinds:
        target = target_dir / kind
        if not target.exists():
            continue
        body = target.read_text(encoding="utf-8", errors="replace")
        if MARKER not in body:
            print("left %s alone: it is not ours" % target)
            continue
        target.unlink()
        print("removed %s" % target)
    return 0


# ---------------------------------------------------------------- output

def place(item):
    """A finding's location. Line 0 means the file or its name, not a line inside it."""
    return "%s: %s" % (item.rule, item.label) if not item.line \
        else "%s: %s:%d" % (item.rule, item.label, item.line)


def render(scan):
    for notice in scan.notices:
        print("notice: %s" % rules.redact(notice, scan.private))
    for label in scan.skipped:
        print("skipped whole: %s" % label)
    for item in scan.findings:
        location = place(item)
        print("%s  %s" % (location, item.fragment) if item.fragment else location)
    for item in scan.waivers:
        # The reason a waiver carries is written by whoever waived it, so it is never echoed.
        print("waived %s: %s:%d" % (item.rule, item.label, item.line))
    print("%d findings, %d waivers, %d files scanned"
          % (len(scan.findings), len(scan.waivers), scan.files))
    if scan.skipped:
        print("%d files skipped whole by a fixture waiver" % len(scan.skipped))


def as_json(scan):
    return {
        "findings": [{"rule": item.rule, "file": item.label, "line": item.line,
                      "fragment": item.fragment} for item in scan.findings],
        "waivers": [{"rule": item.rule, "file": item.label, "line": item.line}
                    for item in scan.waivers],
        "notices": [rules.redact(notice, scan.private) for notice in scan.notices],
        "skipped": list(scan.skipped),
        "files": scan.files,
    }


# ---------------------------------------------------------------- the command

def parse(argv):
    parser = argparse.ArgumentParser(
        description="Report private identifiers before they reach a public repository.")
    parser.add_argument("paths", nargs="*", help="files or folders to scan")
    parser.add_argument("--staged", action="store_true", help="scan the staged contents")
    parser.add_argument("--all", action="store_true", help="scan every tracked file")
    parser.add_argument("--range", help="scan a push range, `<remote sha>..<local sha>`")
    parser.add_argument("--ref-name", action="append", default=[], metavar="REF",
                        help="a ref name being pushed, repeatable; only these are scanned")
    parser.add_argument("--messages-only", action="store_true",
                        help="with --range, scan only the commit messages and ref names")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--install-hook", action="store_true", help="install the pre-push hook")
    parser.add_argument("--uninstall-hook", action="store_true", help="remove our hooks")
    parser.add_argument("--pre-commit", action="store_true",
                        help="with --install-hook, also install the pre-commit hook")
    args = parser.parse_args(argv)
    if args.install_hook and args.uninstall_hook:
        parser.error("--install-hook and --uninstall-hook are mutually exclusive")
    if args.messages_only and not args.range:
        parser.error("--messages-only needs --range")
    if args.ref_name and not args.range:
        parser.error("--ref-name needs --range")
    if not (args.staged or args.all or args.range or args.paths
            or args.install_hook or args.uninstall_hook):
        parser.error("nothing to scan: pass --staged, --all, --range A..B, or paths")
    return args


def check_paths(paths):
    for item in paths:
        if rules.under_private_dir(item):
            raise rules.GuardError(
                "%s is never scanned: it is where the private denylist lives, and reading it "
                "would put the terms in this output" % rules.PRIVATE_DIR)
        if not os.path.exists(item):
            raise rules.GuardError("no such path: %s" % item)


def tracked_denylist(repo):
    code, out, _ = plumbing.git(["-C", str(repo), "ls-files", "-z", "--", rules.PRIVATE_DIR])
    return code == 0 and bool(out.strip("\0 \n"))


def build(repo):
    rule_set = rules.load_rules(RULES_FILE) + rules.load_private(repo / rules.PRIVATE_PATH)
    return guard.Scan(rule_set, rules.load_allow(ALLOW_FILE))


def closed_pipe():
    """The reader stopped reading. Say nothing more, and leave nothing for the interpreter to say.

    A hook is often read by a pager or a `head`, and whoever closes the pipe has already decided.
    The interpreter flushes stdout once more on the way out and prints an ignored-exception block
    when that flush fails too, so the descriptor is pointed at the null device before returning.
    """
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except OSError:
        pass
    return 1


def report(error, private):
    """git failing is a notice about git; everything else is ours. Both are hard failures."""
    message = rules.redact(str(error), private)
    print(("notice: " if message.startswith("git ") else "hard fail: ") + message,
          file=sys.stderr)
    return 2


def main(argv=None):
    # The console a hook inherits is whatever the terminal set, cp1252 on a default Windows
    # install, and a path this scan has to name can carry anything. Report in utf-8, and print
    # a replacement character rather than dying on the encode, which would fail the push open.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    args = parse(argv)
    repo, _ = plumbing.repo_root()
    if args.install_hook:
        return install_hooks(repo, HOOK_KINDS if args.pre_commit else HOOK_KINDS[:1])
    if args.uninstall_hook:
        return uninstall_hooks(repo, HOOK_KINDS)
    if tracked_denylist(repo):
        print("hard fail: %s is tracked by git; remove it from the index before anything else "
              "(git rm --cached -r %s). If it was already committed, clearing the index is not "
              "enough: the commits that carry it have to be rewritten"
              % (rules.PRIVATE_DIR, rules.PRIVATE_DIR), file=sys.stderr)
        return 2
    try:
        scan = build(repo)
    except rules.GuardError as error:
        return report(error, [])

    try:
        check_paths(args.paths)
        if args.range:
            guard.scan_range(scan, repo, args.range, args.messages_only, args.ref_name)
        elif args.staged:
            guard.scan_staged(scan, repo)
        elif args.all:
            guard.scan_all(scan, repo)
        if args.paths:
            guard.scan_disk(scan, args.paths, base=repo)
    except rules.GuardError as error:
        return report(error, scan.private)

    try:
        if args.json:
            print(json.dumps(as_json(scan), indent=2))
        else:
            render(scan)
        sys.stdout.flush()
    except OSError:
        # A closed pipe, which on Windows arrives as errno 22 rather than a broken-pipe error.
        return closed_pipe()
    return 1 if scan.findings else 0


if __name__ == "__main__":
    sys.exit(main())
