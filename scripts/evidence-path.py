#!/usr/bin/env python3
"""Resolve the evidence root, or the worktree root, for the repository at hand, on the OS at hand.

Evidence and worktrees live outside the repository, beside it. The convention and the spec
grammar are in docs/EVIDENCE.md; this script is the single place that turns a portable spec
such as `{repo_parent}/evidence` into a real absolute path, so no skill and no document has to
carry a machine path.

    python scripts/evidence-path.py                  the evidence root
    python scripts/evidence-path.py --id 1234        the folder of one work item
    python scripts/evidence-path.py --mockups        the shared mockups folder
    python scripts/evidence-path.py --id 1234 --create
    python scripts/evidence-path.py --print-spec     which spec won and where it came from
    python scripts/evidence-path.py --worktree-root  the folder the repository's worktrees go in
    python scripts/evidence-path.py --worktree ab12  one lane's worktree, <worktree root>/<repo name>-ab12

Spec resolution, highest first: --spec, the EVIDENCE_ROOT environment variable, the
`Evidence root:` line of CLAUDE.local.md, the same line of CLAUDE.project.md, the default
`{repo_parent}/evidence`. The worktree root resolves the same way from --spec, WORKTREE_ROOT
and the `Worktree root:` line, with the default `{repo_parent}/worktree-{project}`. With
--create, --worktree creates only the worktree root: `git worktree add` makes the lane's folder.

Exit codes: 0 on success, 2 on a spec the grammar does not accept or a lane name that is not
one folder name.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_SPEC = "{repo_parent}/evidence"
ENV_VAR = "EVIDENCE_ROOT"
WORKTREE_DEFAULT_SPEC = "{repo_parent}/worktree-{project}"
WORKTREE_ENV_VAR = "WORKTREE_ROOT"
PROFILE_FILES = ("CLAUDE.local.md", "CLAUDE.project.md")
TOKENS = ("repo_parent", "repo", "repo_name", "home", "project")

ROOT_LINE = re.compile(r"^\s*-?\s*Evidence root:\s*(.+?)\s*$", re.IGNORECASE)
WORKTREE_LINE = re.compile(r"^\s*-?\s*Worktree root:\s*(.+?)\s*$", re.IGNORECASE)
NAME_LINE = re.compile(r"^\s*-?\s*Project name:\s*(.+?)\s*$", re.IGNORECASE)
TOKEN = re.compile(r"\{([^{}]*)\}")
# Each root: its environment variable, its profile line and its default spec.
KINDS = {
    "evidence": (ENV_VAR, ROOT_LINE, DEFAULT_SPEC),
    "worktree": (WORKTREE_ENV_VAR, WORKTREE_LINE, WORKTREE_DEFAULT_SPEC),
}


class SpecError(Exception):
    """A spec the grammar does not accept."""


def repo_root(start=None):
    """The repository the paths are relative to: the git top level of `start`, else `start`."""
    base = Path(os.path.abspath(str(start))) if start else Path.cwd()
    try:
        proc = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(os.path.abspath(proc.stdout.strip()))
    except Exception:
        pass
    return base


def _first_match(path, pattern):
    """The first capture of `pattern` in the file, backticks and spaces stripped. None if absent."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        found = pattern.match(line)
        if not found:
            continue
        value = found.group(1).strip().strip("`").strip()
        if value:
            return value
    return None


def find_spec(repo, kind="evidence"):
    """(spec, source) of the evidence or the worktree root, without the --spec argument, which the caller owns."""
    env_var, line, default = KINDS[kind]
    from_env = os.environ.get(env_var, "").strip()
    if from_env:
        return from_env, env_var
    for name in PROFILE_FILES:
        value = _first_match(Path(repo) / name, line)
        if value:
            return value, name
    return default, "default"


def find_project(repo):
    """The project name from the profile's Identity section, or None when it is still a placeholder."""
    for name in PROFILE_FILES:
        value = _first_match(Path(repo) / name, NAME_LINE)
        if value and not (value.startswith("<") and value.endswith(">")):
            return value
    return None


def resolve(spec, repo, home=None, project=None, kind="evidence"):
    """The absolute evidence or worktree root for `spec`, in the native form of the running OS."""
    repo = Path(os.path.abspath(str(repo)))
    home = Path(os.path.abspath(str(home))) if home else Path.home()
    text = str(spec).strip().strip("`").strip().replace("\\", "/")
    if not text:
        raise SpecError("the %s root spec is empty" % kind)
    if text == "~" or text.startswith("~/"):
        text = str(home).replace("\\", "/") + text[1:]
    values = {
        "repo_parent": str(repo.parent),
        "repo": str(repo),
        "repo_name": repo.name,
        "home": str(home),
        "project": project or repo.name,
    }
    for name in TOKEN.findall(text):
        if name not in values:
            raise SpecError(
                "unknown token {%s} in the %s root spec; known tokens: %s"
                % (name, kind, ", ".join("{%s}" % t for t in TOKENS))
            )
    text = TOKEN.sub(lambda found: values[found.group(1)].replace("\\", "/"), text)
    if "{" in text or "}" in text:
        raise SpecError("stray brace in the %s root spec: %s" % (kind, spec))
    path = Path(text)
    if not path.is_absolute():
        path = repo / text
    return Path(os.path.normpath(str(path)))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Resolve the evidence root, or the worktree root, beside the repository. See docs/EVIDENCE.md.")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--id", help="append the folder of this work item")
    where.add_argument("--mockups", action="store_true", help="append the shared mockups folder")
    where.add_argument("--worktree-root", action="store_true", help="the worktree root instead of the evidence root")
    where.add_argument("--worktree", metavar="LANE", help="the worktree of this lane: <worktree root>/<repo name>-LANE")
    parser.add_argument("--spec", help="override the spec (highest precedence)")
    parser.add_argument("--repo", help="repository path (default: the git top level of the cwd)")
    parser.add_argument("--create", action="store_true", help="create the folder if it is missing")
    parser.add_argument("--print-spec", action="store_true",
                        help="also print which spec won and where it came from")
    args = parser.parse_args(argv)

    repo = repo_root(args.repo)
    kind = "worktree" if (args.worktree_root or args.worktree is not None) else "evidence"
    if args.spec:
        spec, source = args.spec, "--spec"
    else:
        spec, source = find_spec(repo, kind)

    lane = None
    if args.worktree is not None:
        lane = str(args.worktree).strip()
        if not lane or lane in (".", "..") or "/" in lane or "\\" in lane:
            print("the lane name must be one folder name: %r" % args.worktree, file=sys.stderr)
            return 2

    try:
        path = resolve(spec, repo, project=find_project(repo), kind=kind)
    except SpecError as error:
        print(str(error), file=sys.stderr)
        return 2

    if args.create:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if args.id:
                (path / str(args.id).strip().strip("/\\")).mkdir(parents=True, exist_ok=True)
            elif args.mockups:
                (path / "mockups").mkdir(parents=True, exist_ok=True)
        except OSError as error:
            print("could not create %s: %s" % (path, error), file=sys.stderr)
            return 1

    if args.id:
        path = path / str(args.id).strip().strip("/\\")
    elif args.mockups:
        path = path / "mockups"
    elif lane is not None:
        path = path / ("%s-%s" % (Path(os.path.abspath(str(repo))).name, lane))

    if args.print_spec:
        print("spec: %s (source: %s)" % (spec, source))
    print(str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
