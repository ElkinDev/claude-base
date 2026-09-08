"""Tests for train-overlap.py. No network, no real repository, nothing outside a temporary directory.

Every case builds its own git repository in a fresh temporary directory and runs the CLI
the way a caller runs it: as a subprocess. HOME and USERPROFILE point at that directory
and the system configuration is switched off, so no user or machine git setting reaches
a case. Assertions compare the whole stdout, not a fragment, because a tool whose job is
a list is only correct when nothing else is on the list.

    python test-train-overlap.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "train-overlap.py")


def force_remove(func, path, _info):
    """A git object store is read-only on Windows; clear the bit and delete again."""
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        pass


def clean_env(tmp):
    env = os.environ.copy()
    for name in ("HOMEDRIVE", "HOMEPATH", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    env["HOME"] = tmp
    env["USERPROFILE"] = tmp
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.path.join(tmp, "no-such-gitconfig")
    return env


def git(tmp, *args):
    done = subprocess.run(
        ["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@t"] + list(args),
        capture_output=True,
        env=clean_env(tmp),
        timeout=120,
    )
    if done.returncode != 0:
        raise AssertionError(
            "git " + " ".join(args) + " failed: " + done.stderr.decode("utf-8", "replace")
        )
    return done.stdout.decode("utf-8", "replace").replace("\r\n", "\n").strip()


def run(tmp, args):
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True,
        env=clean_env(tmp),
        cwd=tmp,
        timeout=120,
    )


def out(done):
    return done.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def err(done):
    return done.stderr.decode("utf-8", "replace").replace("\r\n", "\n")


def put(tmp, name, text):
    with open(os.path.join(tmp, name), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def shared_lines(first, last):
    rows = ["line %d" % number for number in range(1, 31)]
    rows[0] = first
    rows[29] = last
    return "\n".join(rows) + "\n"


def build(tmp):
    """A base commit, two lane branches, a train that merges both with --no-ff.

    Both lanes touch shared.txt, in regions far enough apart to merge without a
    conflict, and one file of their own each.
    """
    git(tmp, "init", "-q")
    put(tmp, "shared.txt", shared_lines("line 1", "line 30"))
    put(tmp, "a.txt", "a base\n")
    put(tmp, "b.txt", "b base\n")
    git(tmp, "add", "-A")
    git(tmp, "commit", "-q", "-m", "base")
    base = git(tmp, "rev-parse", "HEAD")

    git(tmp, "checkout", "-q", "-b", "lane-a", base)
    put(tmp, "shared.txt", shared_lines("line 1 from a", "line 30"))
    put(tmp, "a.txt", "a lane\n")
    git(tmp, "add", "-A")
    git(tmp, "commit", "-q", "-m", "lane a work")

    git(tmp, "checkout", "-q", "-b", "lane-b", base)
    put(tmp, "shared.txt", shared_lines("line 1", "line 30 from b"))
    put(tmp, "b.txt", "b lane\n")
    git(tmp, "add", "-A")
    git(tmp, "commit", "-q", "-m", "lane b work")

    git(tmp, "checkout", "-q", "-b", "train", base)
    git(tmp, "merge", "-q", "--no-ff", "-m", "member a", "lane-a")
    merge_a = git(tmp, "rev-parse", "--short", "HEAD")
    git(tmp, "merge", "-q", "--no-ff", "-m", "member b", "lane-b")
    merge_b = git(tmp, "rev-parse", "--short", "HEAD")
    return base, merge_a, merge_b


def case_table(tmp):
    base, merge_a, merge_b = build(tmp)
    done = run(tmp, ["--base", base])
    assert done.returncode == 0, err(done)
    expected = (
        "files: 3 members: 2 shared: 1\n"
        "shared.txt  <- %s member b, %s member a\n"
        "\n"
        "%s x %s: 1 files\n" % (merge_b, merge_a, merge_b, merge_a)
    )
    assert out(done) == expected, repr(out(done))
    assert err(done) == "", repr(err(done))
    # The files only one member touched are the lane review's business, not this list.
    assert "a.txt" not in out(done) and "b.txt" not in out(done), repr(out(done))


def case_one_member(tmp):
    base, merge_a, _ = build(tmp)
    done = run(tmp, ["--base", base, "--tip", merge_a])
    assert done.returncode == 0, err(done)
    assert out(done) == "files: 2 members: 1 shared: 0\n", repr(out(done))


def case_repo_argument(tmp):
    base, merge_a, merge_b = build(tmp)
    elsewhere = tempfile.mkdtemp()
    try:
        done = subprocess.run(
            [sys.executable, SCRIPT, "--repo", tmp, "--base", base],
            capture_output=True,
            env=clean_env(tmp),
            cwd=elsewhere,
            timeout=120,
        )
    finally:
        shutil.rmtree(elsewhere, ignore_errors=True)
    assert done.returncode == 0, err(done)
    assert out(done).startswith("files: 3 members: 2 shared: 1\n"), repr(out(done))


def case_bad_base(tmp):
    build(tmp)
    done = run(tmp, ["--base", "no-such-ref"])
    assert done.returncode == 2, done.returncode
    assert out(done) == "", repr(out(done))
    assert err(done).count("\n") == 1, repr(err(done))
    assert "no-such-ref" in err(done), repr(err(done))


def case_bad_repo(tmp):
    empty = tempfile.mkdtemp()
    try:
        done = subprocess.run(
            [sys.executable, SCRIPT, "--repo", empty, "--base", "HEAD"],
            capture_output=True,
            env=clean_env(tmp),
            cwd=tmp,
            timeout=120,
        )
    finally:
        shutil.rmtree(empty, ignore_errors=True)
    assert done.returncode == 2, done.returncode
    assert out(done) == "", repr(out(done))
    assert err(done).count("\n") == 1, repr(err(done))
    # The one line must name the folder, so a missing script cannot pass for this case.
    assert empty in err(done), repr(err(done))


def case_json(tmp):
    base, merge_a, merge_b = build(tmp)
    done = run(tmp, ["--base", base, "--format", "json"])
    assert done.returncode == 0, err(done)
    assert json.loads(out(done)) == {
        "files": {"shared.txt": [merge_b, merge_a]},
        "members": {merge_b: "member b", merge_a: "member a"},
        "pairs": [[merge_b, merge_a, 1]],
    }, out(done)


CASES = [
    case_table,
    case_one_member,
    case_repo_argument,
    case_bad_base,
    case_bad_repo,
    case_json,
]


def main():
    failures = 0
    for case in CASES:
        tmp = tempfile.mkdtemp()
        try:
            case(tmp)
            print("ok   " + case.__name__)
        except AssertionError as failure:
            failures += 1
            print("FAIL " + case.__name__ + ": " + str(failure))
        except Exception as failure:  # a broken case must be as loud as a failing one
            failures += 1
            print("ERROR " + case.__name__ + ": " + repr(failure))
        finally:
            shutil.rmtree(tmp, onerror=force_remove)
    print("%d cases, %d failures" % (len(CASES), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
