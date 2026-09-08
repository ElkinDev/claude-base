"""Lists the files that two or more members of a train touched since the train base.

A train is a branch that merged several already reviewed branches. Read as the sum of
its members it looks reviewed, and the files two members changed at once are exactly
where a union regression hides. This tool answers that one question mechanically,
before anyone opens the diff: which paths more than one member touched, which members
touched each, and which pairs of members meet.

One member is one first-parent commit between the base and the tip. For a first-parent
commit C with first parent P the member's files are what `git diff --name-only P C`
reports, so a merge counts as the whole branch it brought in and a plain commit made on
the train counts as itself. Members are listed newest first, the order of git log, and a
member's label is the short sha of C plus the first 60 characters of its subject.

    python train-overlap.py --base <sha> [--repo <path>] [--tip <ref>] [--format table|json]

Table output is a header line `files: <n> members: <m> shared: <k>`, then one line per
shared path sorted by path, then a blank line and one line per member pair that shares
at least one file, most shared files first. Files only one member touched are not
printed: they belong to that member's own review. JSON output carries the same data,
the shared files only.

Read-only. It runs `git log` and `git diff` and writes nothing. Exit 0 whenever git
answers, including a train of one member, and exit 2 with one line on stderr when the
repository, the base or the tip cannot be read.
"""
import argparse
import itertools
import json
import os
import subprocess
import sys

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
SUBJECT_CLIP = 60
GIT_TIMEOUT = 300
SEP = "\x1f"  # no subject or path carries a unit separator
NAME = "train-overlap"


class GitError(Exception):
    """Git could not answer. Carries the single line the caller prints on stderr."""


def git(repo, *args):
    command = ["git", "-C", repo, "-c", "core.quotepath=false"] + list(args)
    try:
        done = subprocess.run(command, capture_output=True, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise GitError("git %s timed out after %d s" % (args[0], GIT_TIMEOUT))
    except OSError as problem:
        raise GitError("git cannot be run: %s" % problem)
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip().replace("\n", " ")
        raise GitError(detail or ("git %s exited %d" % (args[0], done.returncode)))
    return done.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def check_repo(repo):
    if not os.path.isdir(repo):
        raise GitError("cannot read repository %s: no such directory" % repo)
    try:
        git(repo, "rev-parse", "--git-dir")
    except GitError as problem:
        raise GitError("cannot read repository %s: %s" % (repo, problem))


def check_ref(repo, flag, ref):
    try:
        git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    except GitError as problem:
        raise GitError("cannot read %s %s in %s: %s" % (flag, ref, repo, problem or "unknown ref"))


def read_members(repo, base, tip):
    """One entry per first-parent commit in base..tip, newest first."""
    fmt = "--format=%H" + SEP + "%h" + SEP + "%P" + SEP + "%s"
    log = git(repo, "log", "--first-parent", fmt, base + ".." + tip)
    members = []
    for line in log.split("\n"):
        if not line.strip():
            continue
        full, short, parents, subject = line.split(SEP, 3)
        first_parent = parents.split()[0] if parents.split() else EMPTY_TREE
        members.append(
            {
                "full": full,
                "sha": short,
                "parent": first_parent,
                "subject": subject[:SUBJECT_CLIP],
            }
        )
    return members


def read_files(repo, member):
    diff = git(repo, "diff", "--name-only", member["parent"], member["full"])
    return [line for line in diff.split("\n") if line.strip()]


def collect(repo, base, tip):
    members = read_members(repo, base, tip)
    touched = {}
    for member in members:
        for path in read_files(repo, member):
            touched.setdefault(path, [])
            if member["sha"] not in touched[path]:
                touched[path].append(member["sha"])
    shared = {path: shas for path, shas in touched.items() if len(shas) > 1}
    order = {member["sha"]: index for index, member in enumerate(members)}
    counts = {}
    for shas in shared.values():
        for pair in itertools.combinations(sorted(shas, key=lambda sha: order[sha]), 2):
            counts[pair] = counts.get(pair, 0) + 1
    pairs = sorted(counts.items(), key=lambda row: (-row[1], order[row[0][0]], order[row[0][1]]))
    return members, touched, shared, [(a, b, count) for (a, b), count in pairs]


def label(members, sha):
    for member in members:
        if member["sha"] == sha:
            return "%s %s" % (sha, member["subject"])
    return sha


def render_table(members, touched, shared, pairs):
    lines = ["files: %d members: %d shared: %d" % (len(touched), len(members), len(shared))]
    for path in sorted(shared):
        names = ", ".join(label(members, sha) for sha in shared[path])
        lines.append("%s  <- %s" % (path, names))
    if pairs:
        lines.append("")
        for first, second, count in pairs:
            lines.append("%s x %s: %d files" % (first, second, count))
    return "\n".join(lines) + "\n"


def render_json(members, shared, pairs):
    payload = {
        "files": {path: list(shas) for path, shas in sorted(shared.items())},
        "members": {member["sha"]: member["subject"] for member in members},
        "pairs": [[first, second, count] for first, second, count in pairs],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog=NAME,
        description="Files two or more train members touched since the train base.",
    )
    parser.add_argument("--repo", default=".", help="repository or worktree, default the current directory")
    parser.add_argument("--base", required=True, help="the train base sha or ref")
    parser.add_argument("--tip", default="HEAD", help="the train tip, default HEAD")
    parser.add_argument("--format", default="table", choices=("table", "json"), help="output format")
    parser.add_argument(
        "--first-parent-only",
        action="store_true",
        help="the only walk there is, accepted so a caller can say it out loud",
    )
    return parser.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    repo = os.path.abspath(args.repo)
    try:
        check_repo(repo)
        check_ref(repo, "--base", args.base)
        check_ref(repo, "--tip", args.tip)
        members, touched, shared, pairs = collect(repo, args.base, args.tip)
    except GitError as problem:
        sys.stderr.write("%s: %s\n" % (NAME, problem))
        return 2
    if args.format == "json":
        text = render_json(members, shared, pairs)
    else:
        text = render_table(members, touched, shared, pairs)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
