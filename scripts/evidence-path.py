#!/usr/bin/env python3
"""Resolve the evidence root for the repository at hand, on the OS at hand.

Evidence lives outside the repository, beside it. The convention and the spec grammar are
in docs/EVIDENCE.md; this script is the single place that turns a portable spec such as
`{repo_parent}/evidence` into a real absolute path, so no skill and no document has to
carry a machine path.

    python scripts/evidence-path.py                  the evidence root
    python scripts/evidence-path.py --id 1234        the folder of one work item
    python scripts/evidence-path.py --mockups        the shared mockups folder
    python scripts/evidence-path.py --id 1234 --create
    python scripts/evidence-path.py --print-spec     which spec won and where it came from

Spec resolution, highest first: --spec, the EVIDENCE_ROOT environment variable, the
`Evidence root:` line of CLAUDE.local.md, the same line of CLAUDE.project.md, the default
`{repo_parent}/evidence`.

Exit codes: 0 on success, 2 on a spec the grammar does not accept.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_SPEC = "{repo_parent}/evidence"
ENV_VAR = "EVIDENCE_ROOT"
PROFILE_FILES = ("CLAUDE.local.md", "CLAUDE.project.md")
TOKENS = ("repo_parent", "repo", "repo_name", "home", "project")

ROOT_LINE = re.compile(r"^\s*-?\s*Evidence root:\s*(.+?)\s*$", re.IGNORECASE)
NAME_LINE = re.compile(r"^\s*-?\s*Project name:\s*(.+?)\s*$", re.IGNORECASE)
TOKEN = re.compile(r"\{([^{}]*)\}")


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


def find_spec(repo):
    """(spec, source) for the repository, without the --spec argument, which the caller owns."""
    from_env = os.environ.get(ENV_VAR, "").strip()
    if from_env:
        return from_env, ENV_VAR
    for name in PROFILE_FILES:
        value = _first_match(Path(repo) / name, ROOT_LINE)
        if value:
            return value, name
    return DEFAULT_SPEC, "default"


def find_project(repo):
    """The project name from the profile's Identity section, or None when it is still a placeholder."""
    for name in PROFILE_FILES:
        value = _first_match(Path(repo) / name, NAME_LINE)
        if value and not (value.startswith("<") and value.endswith(">")):
            return value
    return None


def resolve(spec, repo, home=None, project=None):
    """The absolute evidence root for `spec`, in the native form of the running OS."""
    repo = Path(os.path.abspath(str(repo)))
    home = Path(os.path.abspath(str(home))) if home else Path.home()
    text = str(spec).strip().strip("`").strip().replace("\\", "/")
    if not text:
        raise SpecError("the evidence root spec is empty")
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
                "unknown token {%s} in the evidence root spec; known tokens: %s"
                % (name, ", ".join("{%s}" % t for t in TOKENS))
            )
    text = TOKEN.sub(lambda found: values[found.group(1)].replace("\\", "/"), text)
    if "{" in text or "}" in text:
        raise SpecError("stray brace in the evidence root spec: %s" % spec)
    path = Path(text)
    if not path.is_absolute():
        path = repo / text
    return Path(os.path.normpath(str(path)))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Resolve the evidence root beside the repository. See docs/EVIDENCE.md.")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--id", help="append the folder of this work item")
    where.add_argument("--mockups", action="store_true", help="append the shared mockups folder")
    parser.add_argument("--spec", help="override the spec (highest precedence)")
    parser.add_argument("--repo", help="repository path (default: the git top level of the cwd)")
    parser.add_argument("--create", action="store_true", help="create the folder if it is missing")
    parser.add_argument("--print-spec", action="store_true",
                        help="also print which spec won and where it came from")
    args = parser.parse_args(argv)

    repo = repo_root(args.repo)
    if args.spec:
        spec, source = args.spec, "--spec"
    else:
        spec, source = find_spec(repo)

    try:
        path = resolve(spec, repo, project=find_project(repo))
    except SpecError as error:
        print(str(error), file=sys.stderr)
        return 2

    if args.id:
        path = path / str(args.id).strip().strip("/\\")
    elif args.mockups:
        path = path / "mockups"

    if args.create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            print("could not create %s: %s" % (path, error), file=sys.stderr)
            return 1

    if args.print_spec:
        print("spec: %s (source: %s)" % (spec, source))
    print(str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
