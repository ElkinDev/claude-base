"""kit_adopt.py - where install.py's files come from, and how a run fits into a repository (F13).

The Python twin of install/adopt.ps1, for macOS and Linux. Three jobs: build the source-to-target
pairs a scope installs, report what the repository already carries before anything is planned, and
keep the run out of a shared history when the adopter asks for that. The ownership rule itself is
in kit_lib.py.
"""
import json
import os
import re
import shutil
import subprocess

from kit_lib import Pair, sha256_file

# What a clone carries beyond the kit is not the kit: bytecode caches, the .bak and .new files a run
# leaves in a source tree, and the .DS_Store Finder drops in every folder it opens, would otherwise
# make the installed set depend on whose clone it was built from.
SKIP_SUFFIXES = (".pyc", ".bak", ".new")
SKIP_NAMES = (".DS_Store",)


# ---------------------------------------------------------------- sources and directories

def kit_pairs(source_dir, target_dir):
    if not os.path.isdir(source_dir):
        return []
    base = os.path.realpath(source_dir)
    pairs = []
    for folder, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES):
                continue
            full = os.path.join(folder, name)
            rel = os.path.relpath(full, base)
            pairs.append(Pair(full, os.path.join(target_dir, rel)))
    return pairs


def plugin_channel_only(plugin_dir):
    """Whether a plugin says it reaches a machine one way only, through `claude plugin install`.
    Declared in kit.json at the plugin root, a file this kit owns (install/adopt.ps1 says why it is
    not in the plugin manifest)."""
    sidecar = os.path.join(plugin_dir, "kit.json")
    if not os.path.isfile(sidecar):
        return False
    try:
        with open(sidecar, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("channel") == "marketplace"


def _git(args, cwd=None):
    git = shutil.which("git")
    if not git:
        return None
    try:
        done = subprocess.run([git] + args, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def kit_origin(root):
    """Where this clone came from, as an owner/repository slug and the commit it sits on, or None.
    The repository has to BE this tree: `git -C` walks up until it finds one, so a kit unpacked
    inside somebody else's clone would otherwise be read as that clone (install/adopt.ps1)."""
    top = _git(["-C", root, "rev-parse", "--show-toplevel"])
    url = _git(["-C", root, "remote", "get-url", "origin"])
    sha = _git(["-C", root, "rev-parse", "HEAD"])
    if not top or not url or not sha:
        return None
    if os.path.realpath(top) != os.path.realpath(root):
        return None
    match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    if not match or not re.match(r"^[0-9a-f]{40}$", sha):
        return None
    return {"slug": match.group(1) + "/" + match.group(2), "sha": sha}


# ---------------------------------------------------------------- project adoption

def hook_scripts(gradle_hook):
    """The scripts project scope lands in <project>/scripts/hooks. run-logged.py on every run: the
    scaffolded CLAUDE.md sends long tools through it. filter-gradle-output.py only with --gradle-hook."""
    if gradle_hook:
        return ["run-logged.py", "filter-gradle-output.py"]
    return ["run-logged.py"]


def hook_script_action(engine, project, template, name, force=False):
    """What the plan will do with one project hook script: the plan's own ownership rule, so nothing
    said from here can disagree with the plan. 'nosource' is a kit tree that does not carry it."""
    source = os.path.join(template, "scripts", "hooks", name)
    if not os.path.isfile(source):
        return "nosource"
    return engine.resolve(os.path.join(project, "scripts", "hooks", name), sha256_file(source), force=force)


def gradle_hook_refusal(engine, project, template, force=False):
    """Why --gradle-hook cannot be honoured, as one line, or None when it can (install/adopt.ps1
    Get-KitGradleHookRefusal: an explicit request is refused rather than half honoured)."""
    names = hook_scripts(True)
    needed = [os.path.join(".claude", "settings.gradle.json")] + [os.path.join("scripts", "hooks", n) for n in names]
    missing = [os.path.join(template, rel) for rel in needed if not os.path.isfile(os.path.join(template, rel))]
    if missing:
        return ("refused: --gradle-hook needs %s, which this kit tree does not carry. Run the installer "
                "from a complete clone of the kit. Nothing was written." % " and ".join(missing))
    own = [os.path.join(project, "scripts", "hooks", n) for n in names
           if hook_script_action(engine, project, template, n, force=force) == "new"]
    if own:
        return ("refused: --gradle-hook would wire a hook that runs %s, yours and not the kit's. Put the "
                "kit's file there (from %s) and run again, or add --force, which does that after a "
                "backup. Nothing was written." % (" and ".join(own), os.path.join(template, "scripts", "hooks")))
    return None


def adoption_findings(engine, project, template, force=False, gradle_hook=False):
    """What the repository already has, and what this run will do about it. Only findings are
    printed: an adopter cares about the files that already exist."""
    shared = "kept as is, the kit version lands beside it as <name>.new"
    same = "kept as is, already matches the kit"
    never = "never touched by the installer"
    forced = "overwritten by --force, after a copy of yours goes to the backup"
    forced_dir = ("under --force a file in it that the kit writes and that differs is overwritten after a "
                  "backup, the rest is kept as is")
    unwritten = "kept as is, this run writes no file of that name"
    known = [
        ("CLAUDE.md", shared), ("AGENTS.md", shared), (".claude", shared),
        (".cursor", never), (".github/copilot-instructions.md", never), (".husky", never),
        (".pre-commit-config.yaml", never), ("lefthook.yml", never), ("CODEOWNERS", never),
        (".editorconfig", never),
    ]
    out = []
    for item, note in known:
        here = os.path.join(project, item)
        if not os.path.exists(here):
            continue
        kit = os.path.join(template, item) if template else ""
        if (note == shared and kit and os.path.isfile(kit) and os.path.isfile(here)
                and sha256_file(here) == sha256_file(kit)):
            note = same
        if force and note == shared and kit and os.path.exists(kit):
            note = forced if os.path.isfile(here) else forced_dir
        if item == "AGENTS.md" and note == shared and template:
            # A team AGENTS.md is merged by its markers, never by hand (F03 edge case 1).
            tool = os.path.join(os.path.dirname(template), "scripts", "agents-md.py")
            note += ('; once a real run has written AGENTS.md.new, take the kit sections into yours: '
                     'python3 "%s" render "%s.new" "%s"' % (tool, here, here))
        out.append((item, note))
    if template:
        installs = hook_scripts(gradle_hook)
        for name in hook_scripts(True):
            writes = name in installs
            action = hook_script_action(engine, project, template, name, force=(force and writes))
            on_disk = os.path.isfile(os.path.join(project, "scripts", "hooks", name))
            note = None
            if writes and action == "new":
                note = shared
            elif writes and action == "force":
                note = forced
            elif on_disk and action in ("new", "nosource"):
                note = unwritten
            if note:
                out.append(("scripts/hooks/" + name, note))
    try:
        for name in sorted(os.listdir(project)):
            if name.startswith("CONTRIBUTING") and os.path.isfile(os.path.join(project, name)):
                out.append((name, never))
    except OSError:
        pass
    hooks = os.path.join(project, ".git", "hooks")
    if os.path.isdir(hooks):
        for name in sorted(os.listdir(hooks)):
            if os.path.isfile(os.path.join(hooks, name)) and not name.endswith(".sample"):
                out.append((".git/hooks/" + name, never))
    if os.path.exists(os.path.join(project, ".git")):
        configured = _git(["-C", project, "config", "--get", "core.hooksPath"])
        if configured:
            out.append(("core.hooksPath = " + configured.splitlines()[0], never))
    return out


# ---------------------------------------------------------------- keeping it local

def git_exclude_path(project):
    """The exclude file git actually reads: in a worktree or a submodule it lives in the common
    directory, so git itself is asked (install/adopt.ps1 Get-GitExcludePath)."""
    if not os.path.exists(os.path.join(project, ".git")):
        return None
    path = _git(["-C", project, "rev-parse", "--git-path", "info/exclude"])
    if not path:
        return None
    path = path.splitlines()[0].strip()
    if not os.path.isabs(path):
        path = os.path.join(project, path)
    return os.path.abspath(path)


def exclude_lines(project, owned):
    """The paths this run manages inside the project, as git patterns relative to its root, with
    everything under .claude/ folded into the single folder line .claude/. Derived from what the
    plan actually did, never from a fixed list."""
    prefix = project.rstrip("/") + "/"
    lines = []
    for path in owned:
        if not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        if rel.startswith(".claude/"):
            rel = ".claude/"
        if rel not in lines:
            lines.append(rel)
    return sorted(lines)


def add_exclude_lines(project, lines, dry_run=False):
    """Appends only the lines that are not there yet. It never rewrites or reorders what the
    repository already excludes."""
    exclude = git_exclude_path(project)
    if not exclude:
        return ["no git directory here, --local-only skipped"]
    existing = []
    current = ""
    if os.path.isfile(exclude):
        with open(exclude, encoding="utf-8-sig", newline="") as handle:
            current = handle.read()
        existing = [line.strip() for line in current.splitlines()]
    missing = [line for line in lines if line.strip() not in existing]
    if not missing:
        return ["already excluded: %s is up to date" % exclude]
    if dry_run:
        return ["would append to %s : %s" % (exclude, ", ".join(missing))]
    folder = os.path.dirname(exclude)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    if current and not current.endswith("\n"):
        current += "\n"
    with open(exclude, "w", encoding="utf-8", newline="") as handle:
        handle.write(current + "\n".join(missing) + "\n")
    return ["excluded     %s (in %s)" % (line, exclude) for line in missing if not line.startswith("#")]
