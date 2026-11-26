#!/usr/bin/env python3
"""install.py - install the personal Claude Code base on macOS or Linux without destroying anything
you already have. The twin of install.ps1, which stays the Windows installer; both keep the same
manifest and backup layout, so scripts/kit-restore.py rolls back a run of either.

  User scope (default): copy the skills, hooks, tools, agents, seats, the status line and the
  account switcher into the kit home (~/.claude, or $KIT_HOME when it is set) and render
  settings.json, whose hooks run with python3 and whose status line is statusline.py.

  Project scope (--project <path>): scaffold a project with AGENTS.md, CLAUDE.md, CLAUDE.project.md,
  a .claude/settings.local.json and the branch hooks (the Python twins of the Windows ones), the
  logged runner CLAUDE.md names (scripts/hooks/run-logged.py), plus docs/ when you pass --sdd.
  --gradle-hook opts a gradle project in, and is refused, with nothing written, when a hook script in
  the project would not be the kit's after the run. The run prints what the repository already has
  before it plans anything, and it never writes git hooks, CI files, or anything outside the paths it
  names in that preflight.

  Nothing is ever deleted. Every file the installer writes is recorded with its hash in
  <kit home>/.kit-manifest.json. A file that differs from that record, or that the manifest never
  heard of, belongs to you: it is backed up, left exactly as it is, and the kit version lands beside
  it as <name>.new for you to merge. --force overwrites it, still after a backup. Every backup goes to
  <kit home>/backups/<stamp>/ and comes back with scripts/kit-restore.py. See docs/ADOPTION.md.

Examples:
  python3 install.py --dry-run
  python3 install.py
  python3 install.py --project ~/code/my-app --local-only
  python3 install.py --project ~/code/my-app --gradle-hook
  python3 install.py --permissions ask --auto-update

The PowerShell spellings of the switches (-DryRun, -Project, -NoHerdr, ...) are accepted too, so a
line copied from the Windows docs works as it stands.
"""
import argparse
import base64
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
INSTALL_DIR = os.path.join(ROOT, "install")
sys.path.insert(0, INSTALL_DIR)

import kit_adopt  # noqa: E402  (found through INSTALL_DIR)
import kit_cc  # noqa: E402
import kit_lib  # noqa: E402
from kit_lib import Pair, plan_line, read_text  # noqa: E402

PYTHON = "python3"
ALIASES = {
    "--project": "-Project", "--sdd": "-Sdd", "--force": "-Force", "--dry-run": "-DryRun",
    "--local-only": "-LocalOnly", "--gradle-hook": "-GradleHook", "--permissions": "-Permissions",
    "--herdr": "-Herdr", "--no-herdr": "-NoHerdr", "--no-hook-merge": "-NoHookMerge",
    "--auto-update": "-AutoUpdate", "--no-auto-update": "-NoAutoUpdate", "--unattended": "-Unattended",
}


def say(line=""):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def load_optional(name):
    """The Herdr and daily-update steps live in modules of their own (install/kit_herdr.py,
    install/kit_schedule.py). A tree without one still installs the rest, and says so."""
    path = os.path.join(INSTALL_DIR, name + ".py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="install.py", allow_abbrev=False,
                                 description="Install claude-base on macOS or Linux (install.ps1 on Windows).")
    flag = lambda name, **kw: ap.add_argument(name, ALIASES[name], **kw)  # noqa: E731
    flag("--project", metavar="PATH", help="scaffold this project instead of the user-scope install")
    flag("--sdd", action="store_true", help="project scope: also the agents and the docs/ spec tree")
    flag("--force", action="store_true", help="overwrite a file of yours (after a backup) instead of writing .new")
    flag("--dry-run", action="store_true", help="print the full plan, one line per file, and write nothing")
    flag("--local-only", action="store_true", help="project scope: add what the kit wrote to the git exclude file")
    flag("--gradle-hook", action="store_true", help="project scope: wire the gradle output hook")
    flag("--permissions", choices=("bypass", "ask"), default="bypass",
         help="bypass (no prompts, what the skills assume) or ask; see docs/PERMISSIONS.md")
    flag("--herdr", action="store_true", help="add Herdr without asking")
    flag("--no-herdr", action="store_true", help="skip Herdr without asking")
    flag("--no-hook-merge", action="store_true", help="leave a settings.json of yours as it is")
    flag("--auto-update", action="store_true", help="register the daily update (macOS: a launchd agent)")
    flag("--no-auto-update", action="store_true", help="remove the daily update and do nothing else")
    flag("--unattended", action="store_true", help="the daily run's install: the kit home and nothing outside it")
    return ap.parse_args(argv)


# ---------------------------------------------------------------- settings

def render_user_settings(kit_home, permissions):
    """claude/settings.json for this machine. The template names the kit home as %USERPROFILE%/.claude
    and the interpreter as `python`, the Windows spellings; here the kit home is the real path
    (KIT_HOME moves where the files land, so it has to move what the hooks point at), the
    interpreter is python3 (macOS has no `python`), and the status line is statusline.py. Parsed and
    serialised, not patched as text: the template round-trips byte for byte through json.dumps with
    an indent of 2, so the layout stays the one merge-hooks.py writes back."""
    home = os.path.expanduser("~")
    data = json.loads(read_text(os.path.join(ROOT, "claude", "settings.json")))

    def command(text):
        text = text.replace("%USERPROFILE%/.claude", kit_home).replace("%USERPROFILE%\\.claude", kit_home)
        text = text.replace("%USERPROFILE%", home)
        if text.startswith("python "):
            text = PYTHON + text[len("python"):]
        return text

    status = data.get("statusLine")
    if isinstance(status, dict) and "command" in status:
        status["command"] = '%s "%s"' % (PYTHON, os.path.join(kit_home, "statusline.py"))
    for entries in (data.get("hooks") or {}).values():
        for entry in entries:
            for handler in entry.get("hooks", []):
                if isinstance(handler.get("command"), str):
                    handler["command"] = command(handler["command"])
    # Permission mode. Two keys move together: the mode itself, and whether Claude Code warns you
    # when it starts in the dangerous one.
    if permissions == "ask":
        data.setdefault("permissions", {})["defaultMode"] = "default"
        data["skipDangerousModePermissionPrompt"] = False
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def render_project_settings(template_dir, gradle_hook):
    """The project's .claude/settings.json, with the plugin source filled from this clone and pinned
    to its commit, or with both plugin keys dropped when the clone cannot name its source (the
    reasons are in install.ps1)."""
    name = "settings.gradle.json" if gradle_hook else "settings.json"
    text = read_text(os.path.join(template_dir, ".claude", name))
    # The gradle hook runs its filter with `python`, which macOS does not have. The file is rendered
    # per machine anyway (it pins this clone's commit), so it names this system's interpreter.
    text = text.replace('"command": "python ', '"command": "%s ' % PYTHON)
    origin = kit_adopt.kit_origin(ROOT)
    if origin:
        return text.replace("<owner>/<repo>", origin["slug"]).replace("<pinned-commit>", origin["sha"]), True
    data = json.loads(text)
    data.pop("extraKnownMarketplaces", None)
    data.pop("enabledPlugins", None)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n", False


# ---------------------------------------------------------------- the run

class Run(object):
    def __init__(self, args):
        self.args = args
        self.home = kit_lib.kit_home()
        self.stamp = kit_lib.new_stamp(self.home)
        self.manifest = kit_lib.Manifest(os.path.join(self.home, ".kit-manifest.json"))
        self.backup = kit_lib.Backup()
        self.engine = kit_lib.Engine(self.manifest, self.backup, force=args.force, dry_run=args.dry_run)
        self.merge_target = None
        self.plan_lines = []

    # -- the plan

    def execute(self, pairs, trigger):
        """The whole plan, file by file. The closing block runs in a finally, so a run that dies on a
        locked file or a permission denial still records what it had already done and still prints
        the rollback command for it. A run killed from outside never reaches it: what makes that
        case recoverable is the backup line written before each change."""
        self.backup.start(os.path.join(self.home, "backups", self.stamp), trigger)
        lines, finished = [], False
        try:
            for folder in kit_lib.missing_dirs(pairs):
                lines.append(plan_line("create dir", folder))
            for pair in pairs:
                got = self.engine.set_file(pair)
                lines.extend(got)
                if got and self.merge_target and pair.target == self.merge_target and \
                        (got[0].startswith("backup+new ") or got[0].startswith("kept ")):
                    lines.extend(self.hook_merge(pair.target, pair.text))
            finished = True
        finally:
            self.plan_lines = list(lines)
            if self.args.dry_run:
                say("plan (dry run, nothing is written):")
                for line in lines:
                    say("  " + line)
            else:
                for line in lines:
                    if not line.startswith("skip same"):
                        say("  " + line)
            self.summary(lines, finished)
            self.closing()

    def summary(self, lines, finished):
        counts = {}
        for line in lines:
            verb = line.split(" ")[0]
            counts[verb] = counts.get(verb, 0) + 1
        if not finished:
            say("STOPPED  this run did not finish. Everything below is what it had done by then.")
        say("summary  %d written, %d refreshed, %d kept with a .new beside them, %d forced, %d unchanged" % (
            counts.get("write", 0), counts.get("refresh", 0),
            counts.get("backup+new", 0) + counts.get("kept", 0), counts.get("force", 0), counts.get("skip", 0)))
        if counts.get("draft", 0):
            say("         plus %d .new draft(s) of your own, backed up before the kit version replaced them"
                % counts["draft"])

    def closing(self):
        if self.args.dry_run:
            say()
            say("Dry run: nothing was written, no backup was needed, the manifest was not touched.")
            say("Run the same command without --dry-run to apply this plan.")
            return
        if self.manifest.write():
            say(plan_line("manifest", self.manifest.path))
        folder = self.backup.folder()
        say()
        if folder:
            say("This run is recorded at %s, with a copy of every file it replaced." % folder)
            say('Roll it back with: %s "%s" --stamp %s' % (PYTHON, os.path.join(ROOT, "scripts", "kit-restore.py"),
                                                          self.stamp))
        else:
            say("Nothing changed, so there is nothing to roll back.")

    # -- your settings.json, kept: the kit's new hooks go into it

    def hook_merge(self, target, kit_text):
        """The kit's new hooks into a settings.json of yours, through claude/merge-hooks.py: additions
        only, and only into a file that already runs the kit's hooks. A dry probe runs first, so a
        run with nothing to write writes no backup line. Before a real write the file is backed up
        and recorded as `merged <path> <sha256 after the merge>`, and the merge record is backed up
        and put on the manifest, so a rollback takes both back (install.ps1 Invoke-KitHookMerge)."""
        by_hand = "take new hooks from %s.new by hand" % target
        if self.args.no_hook_merge:
            return [plan_line("hooks", "%s (not merged, --no-hook-merge; %s)" % (target, by_hand))]
        tool = os.path.join(ROOT, "claude", "merge-hooks.py")
        record = os.path.join(self.home, ".kit-hooks-merged.json")
        b64 = base64.b64encode(kit_text.encode("utf-8")).decode("ascii")
        base = [sys.executable, "-I", tool, "--kit-b64", b64, "--record", record, "--kit-home", self.home, target]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")

        def call(extra):
            try:
                done = subprocess.run(base + extra, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", env=env, timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                return 1, [str(exc)]
            return done.returncode, (done.stdout + done.stderr).splitlines()

        code, probe = call(["--dry-run"])
        if code != 0:
            return [plan_line("hooks", "%s (not merged: %s; %s)" % (target, (probe + [""])[0], by_hand))]
        adds = [l for l in probe if l.startswith("would add")]
        after = [l.split(" ")[1] for l in probe if l.startswith("after-sha256 ") and len(l.split(" ")[1]) == 64]
        record_changes = "record-changes" in probe
        notes = [plan_line("hooks", l) for l in probe
                 if not l.startswith("would add") and not l.startswith("after-sha256") and l != "record-changes"]
        if adds and len(after) != 1:
            return [plan_line("hooks", "%s (not merged: the probe gave no hash of the result; %s)" % (target, by_hand))]
        added = [plan_line("hooks+", "%s %s" % (target, l[len("would add"):].strip())) for l in adds]
        if self.args.dry_run or (not adds and not record_changes):
            return notes + added
        if adds:
            self.backup.copy(target)
            self.backup.line("merged %s %s" % (target, after[0]))
        if record_changes:
            if os.path.isfile(record):
                self.backup.copy(record)
                self.backup.line("overwritten %s" % record)
            else:
                self.backup.line("created %s" % record)
        code, done = call([])
        if os.path.isfile(record):
            self.manifest.set(record, kit_lib.sha256_file(record))
        if code != 0:
            return [plan_line("hooks", "%s (not merged: %s; %s)" % (target, (done + [""])[0], by_hand))]
        return notes + added

    # -- project scope

    def gradle_note(self, settings, before, rendered):
        """One line for a project run that would otherwise leave the gradle hook unwired without a
        word (install.ps1 Get-KitGradleHookNote)."""
        filt = "filter-gradle-output.py"
        if self.args.dry_run:
            verb = ""
            for line in self.plan_lines:
                if line.endswith(" " + settings) or (" %s (" % settings) in line:
                    verb = line.split(" ")[0]
            after = rendered if verb in ("write", "refresh", "force") else before
        else:
            after = read_text(settings) if os.path.isfile(settings) else ""
        wired = filt in after
        if filt in before and not wired:
            if self.args.dry_run:
                return ("warn: a real run would leave the gradle hook unwired: it renders %s without it. "
                        "Add --gradle-hook to keep it wired." % settings)
            return ("warn: the gradle hook is no longer wired: this run rendered %s without it. Run again "
                    "with --gradle-hook to wire it again." % settings)
        if self.args.gradle_hook and not wired:
            if self.args.dry_run:
                return ("warn: a real run would not wire the gradle hook yet: it keeps %s as it is, and the "
                        "hook block to merge is then in %s.new." % (settings, settings))
            return ("warn: the gradle hook is not wired yet: %s is kept as it is, and the hook block to "
                    "merge is in %s.new." % (settings, settings))
        return None

    def project(self):
        args = self.args
        asked = os.path.abspath(os.path.expanduser(args.project))
        if not os.path.exists(asked):
            if args.dry_run:
                say(plan_line("create dir", asked))
            else:
                os.makedirs(asked)
        proj = asked
        tpl = os.path.join(ROOT, "project-template")

        say("Adoption preflight: what %s already has" % proj)
        findings = kit_adopt.adoption_findings(self.engine, proj, tpl, force=args.force, gradle_hook=args.gradle_hook)
        if findings:
            width = max(len(item) for item, _ in findings)
            for item, note in findings:
                say("  %s  %s" % (item.ljust(width), note))
        else:
            say("  nothing of yours is in the way; this looks like a fresh repository")
        writes = ("AGENTS.md, CLAUDE.md, CLAUDE.project.md, .claude/, scripts/hooks/ and docs/ (the --sdd spec tree)"
                  if args.sdd else "AGENTS.md, CLAUDE.md, CLAUDE.project.md, .claude/ and scripts/hooks/")
        say("  the installer writes only %s, and deletes nothing" % writes)
        say()

        claude_dir = os.path.join(proj, ".claude")
        pairs = [
            Pair(os.path.join(tpl, "AGENTS.md"), os.path.join(proj, "AGENTS.md")),
            Pair(os.path.join(tpl, "CLAUDE.md"), os.path.join(proj, "CLAUDE.md")),
            Pair(os.path.join(tpl, "CLAUDE.project.md"), os.path.join(proj, "CLAUDE.project.md")),
            # The branch hooks run with python3 here: the template's settings.local.json starts the
            # Windows PowerShell twins, so this system has a template of its own.
            Pair(None, os.path.join(claude_dir, "settings.local.json"),
                 read_text(os.path.join(tpl, ".claude", "settings.local.posix.json"))),
        ]
        for hook in ("branch-check.py", "branch-upstream-fix.py"):
            pairs.append(Pair(os.path.join(ROOT, "claude", "hooks", hook), os.path.join(claude_dir, "hooks", hook)))
        for script in kit_adopt.hook_scripts(args.gradle_hook):
            source = os.path.join(tpl, "scripts", "hooks", script)
            if os.path.isfile(source):
                pairs.append(Pair(source, os.path.join(proj, "scripts", "hooks", script)))
            else:
                say("warn: this kit tree does not carry %s," % source)
                say("      so this run writes no scripts/hooks/%s into the project. The scaffolded" % script)
                say("      CLAUDE.md names that runner; scaffold from a complete clone of the kit to get it.")
                say()
        proj_settings, has_origin = render_project_settings(tpl, args.gradle_hook)
        if not has_origin:
            say("warn: this kit tree has no readable git origin, so the scaffolded project gets")
            say("      no plugin source: extraKnownMarketplaces and enabledPlugins are left out.")
            say("      Install the plugins by hand, or scaffold from a clone with an origin remote.")
            say()
        settings_target = os.path.join(claude_dir, "settings.json")
        pairs.append(Pair(None, settings_target, proj_settings))
        if args.sdd:
            pairs += kit_adopt.kit_pairs(os.path.join(ROOT, "claude", "agents"), os.path.join(claude_dir, "agents"))
            pairs += kit_adopt.kit_pairs(os.path.join(tpl, "docs"), os.path.join(proj, "docs"))

        before = read_text(settings_target) if os.path.isfile(settings_target) else ""
        self.execute(pairs, "project scope install at %s" % proj)
        note = self.gradle_note(settings_target, before, proj_settings)
        if note:
            say()
            say(note)

        if args.local_only:
            say()
            lines = ["# claude-base, kept local to this machine"] + kit_adopt.exclude_lines(proj, self.engine.owned)
            for line in kit_adopt.add_exclude_lines(proj, lines, dry_run=args.dry_run):
                say("  " + line)

        say()
        say("Project scaffolded at %s." % proj)
        say("Next: copy the closest project-template/profiles/*.md over CLAUDE.project.md and fill it.")
        if args.sdd:
            say("SDD scaffolding added: .claude/agents/ + docs/. Drive it with /sdd, or /delivery:sdd from the marketplace.")
        if not args.local_only:
            say("Keep it out of the team's history: re-run with --local-only to add exactly the files")
            say("this run wrote to the repository's exclude file. Recommended for a repository you share.")
        say("Before you commit any of these files, check your team's policy on AI tooling in the repo.")
        say("How the kit adapts to a repository that already has rules: docs/ADOPTION.md")
        return 0

    # -- user scope

    def user_pairs(self):
        home = self.home
        pairs = []
        for folder in ("skills", "hooks", "tools", "agents", "seats"):
            # The seats are prompt, not agent definitions: they live in their own folder so nothing
            # can delegate to a seat, and the launcher appends the file of the role it was given.
            pairs += kit_adopt.kit_pairs(os.path.join(ROOT, "claude", folder), os.path.join(home, folder))
        plugin_root = os.path.join(ROOT, "plugins")
        if os.path.isdir(plugin_root):
            for name in sorted(os.listdir(plugin_root)):
                plugin = os.path.join(plugin_root, name)
                if not os.path.isdir(plugin):
                    continue
                if kit_adopt.plugin_channel_only(plugin):
                    say(plan_line("skip plugin", "%s (single channel, installed with claude plugin install)" % name))
                    continue
                pairs += kit_adopt.kit_pairs(os.path.join(plugin, "skills"), os.path.join(home, "skills"))
        # The status line and the account switcher for this system. The Windows files (statusline.ps1,
        # claude-account.ps1, cc-launch.ps1, bin/cc.cmd) run nowhere here, so they are not copied.
        # The switcher's command on PATH is `cca`, not `cc`: on macOS and Linux `cc` is the C compiler,
        # and a cc on PATH ahead of it would break every build that calls it. The `cc` typed in a
        # terminal is a shell function in the rc file instead (install/kit_cc.py, docs/ACCOUNTS.md).
        for rel in ("statusline.py", "claude-account.py", "merge-settings.py", "CLAUDE.md", os.path.join("bin", "cca")):
            source = os.path.join(ROOT, "claude", rel)
            if os.path.isfile(source):
                pairs.append(Pair(source, os.path.join(home, rel)))
            else:
                say("warn: this kit tree does not carry claude/%s, so this run does not install it." % rel)
        # The daily update's runner, which its job runs from here, not from the clone, so a moved
        # clone is still reported. Every install refreshes it, --auto-update or not.
        runner = os.path.join(INSTALL_DIR, "auto-update.py")
        if os.path.isfile(runner):
            pairs.append(Pair(runner, os.path.join(home, "kit-auto-update.py")))
        settings = render_user_settings(home, self.args.permissions)
        self.merge_target = os.path.join(home, "settings.json")
        pairs.append(Pair(None, self.merge_target, settings))
        return pairs

    def user(self):
        args = self.args
        pairs = self.user_pairs()
        say("Kit home: %s" % self.home)
        self.execute(pairs, "user scope install")
        # The kit's commands (cca) reach the PATH once, and the cc function the rc file; a terminal
        # opened after this run finds them. Not on an unattended run: a user who took them off keeps
        # them off.
        say()
        if args.unattended:
            say("  user PATH    left as it is (--unattended)")
        else:
            bin_dir, targets = os.path.join(self.home, "bin"), [p.target for p in pairs]
            for line in add_kit_commands(bin_dir, targets, args.dry_run):
                say("  " + line)
            for line in kit_cc.add_cc_function(_rc_file(), bin_dir, targets, args.dry_run):
                say("  " + line)
        say()
        if args.permissions == "bypass":
            say("  PERMISSIONS: bypass. Claude Code will act without asking for confirmation.")
            say("  Re-run with --permissions ask to keep the prompts. See docs/PERMISSIONS.md.")
        else:
            say("  PERMISSIONS: ask. Claude Code will prompt before acting.")
            say("  Some skills in this base assume bypass and will stop on each step.")
        if args.unattended:
            say()
            say("herdr        left as it is (--unattended)")
        else:
            herdr = load_optional("kit_herdr")
            if herdr is None:
                say()
                say("herdr        skipped: this kit tree does not carry install/kit_herdr.py")
            else:
                choice = herdr.get_choice(args.herdr, args.no_herdr, args.dry_run)
                herdr.install_herdr(ROOT, self.home, choice, dry_run=args.dry_run, out=say)
        failed = False
        schedule = load_optional("kit_schedule")
        if args.auto_update:
            say()
            if schedule is None:
                say("auto-update  FAIL this kit tree does not carry install/kit_schedule.py")
                failed = True
            elif sys.platform != "darwin":
                # register() says in one line that Linux has no daily job yet. An option this system
                # does not offer is not a failed install, so the record is still written.
                schedule.register(ROOT, self.home, dry_run=args.dry_run, out=say)
            else:
                failed = not schedule.register(ROOT, self.home, dry_run=args.dry_run, out=say)
        # Last, so only an install that finished leaves its record: what the daily run replays.
        if not args.dry_run and not failed and schedule is not None:
            schedule.save_install_record(ROOT, self.home, args.permissions, args.no_hook_merge)
        say()
        say("Done.")
        say("Run: %s \"%s\"" % (PYTHON, os.path.join(ROOT, "scripts", "doctor.py")))
        return 1 if failed else 0


# ---------------------------------------------------------------- the kit's commands on PATH

def _on_path(folder):
    want = os.path.realpath(folder)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and os.path.realpath(os.path.expanduser(entry)) == want:
            return True
    return False


def _rc_file():
    """The file a new interactive terminal of the login shell reads, or None for a shell we do not
    know (the run then prints the line to add by hand)."""
    if os.environ.get("KIT_SHELL_RC"):
        return os.environ["KIT_SHELL_RC"]
    shell = os.path.basename(os.environ.get("SHELL", ""))
    home = os.path.expanduser("~")
    if shell == "zsh":
        return os.path.join(home, ".zshrc")
    if shell == "bash":
        # Terminal.app starts bash as a login shell, which reads .bash_profile and not .bashrc.
        return os.path.join(home, ".bash_profile" if sys.platform == "darwin" else ".bashrc")
    return None


def add_kit_commands(bin_dir, targets, dry_run):
    """Make the commands in <kit home>/bin (cca) reachable from any new terminal, the job the user
    PATH entry does on Windows. In order of preference:
      1. <kit home>/bin is already on PATH: nothing to do.
      2. ~/.local/bin is on PATH (the Claude Code installer puts it there): a symlink per command in
         it, so no shell file is touched. A name there that is not our link is left alone.
      3. Otherwise one line that appends <kit home>/bin to PATH, at the end of the shell's rc file,
         so no system command is ever shadowed. Appended once, never rewritten.
    A rollback does not undo any of these, the same as the PATH entry on Windows; docs/ADOPTION.md
    names them. KIT_PATH_LINK_DIR and KIT_SHELL_RC point a test somewhere else."""
    names = sorted(os.path.basename(t) for t in targets if os.path.dirname(t) == bin_dir)
    if not names:
        return []
    if _on_path(bin_dir):
        return []
    link_dir = os.environ.get("KIT_PATH_LINK_DIR") or os.path.join(os.path.expanduser("~"), ".local", "bin")
    overridden = bool(os.environ.get("KIT_PATH_LINK_DIR") or os.environ.get("KIT_SHELL_RC"))
    temp = os.path.realpath(tempfile.gettempdir()) + os.sep
    if not overridden and os.path.realpath(bin_dir).startswith(temp):
        # A kit home under the temp folder is a test's: it never reaches the real shell setup.
        return [plan_line("path", "skip %s is under the temp folder, so it is not added to your PATH" % bin_dir)]
    if os.path.isdir(link_dir) and _on_path(link_dir):
        out = []
        for name in names:
            link, target = os.path.join(link_dir, name), os.path.join(bin_dir, name)
            if os.path.islink(link) and os.readlink(link) == target:
                continue
            if os.path.lexists(link):
                out.append(plan_line("path", "keep %s, it is not the kit's; run %s, or add %s to PATH"
                                     % (link, target, bin_dir)))
                continue
            if dry_run:
                out.append(plan_line("path", "would link %s -> %s" % (link, target)))
            else:
                os.symlink(target, link)
                out.append(plan_line("path", "linked %s -> %s" % (link, target)))
        return out
    rc = _rc_file()
    line = 'export PATH="$PATH:%s"' % bin_dir
    if rc is None:
        return [plan_line("path", "add %s to your PATH by hand, for example: %s" % (bin_dir, line))]
    current = ""
    if os.path.isfile(rc):
        with open(rc, encoding="utf-8", errors="replace") as handle:
            current = handle.read()
    if line in current:
        return []
    if dry_run:
        return [plan_line("path", "would append to %s: %s" % (rc, line))]
    with open(rc, "a", encoding="utf-8") as handle:
        if current and not current.endswith("\n"):
            handle.write("\n")
        handle.write("\n# claude-base: the kit's commands (cca), added by install.py\n%s\n" % line)
    return [plan_line("path", "appended to %s: %s (a terminal opened from now on finds cca)" % (rc, line))]


# ---------------------------------------------------------------- entry point

def main(argv=None):
    if os.name == "nt":
        say("install.py is the macOS and Linux installer. On Windows run install.ps1 (INSTALL.md).")
        return 1
    if sys.version_info < (3, 8):
        say("install.py needs Python 3.8 or newer; this is %s." % sys.version.split()[0])
        return 1
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.herdr and args.no_herdr:
        say("refused: --herdr and --no-herdr cannot both be given. Nothing was written.")
        return 1
    if args.auto_update and args.no_auto_update:
        say("refused: --auto-update and --no-auto-update cannot both be given. Nothing was written.")
        return 1
    if args.unattended and (args.project or args.auto_update or args.no_auto_update or args.herdr):
        say("refused: --unattended is the daily run's kit-home-only install, so it takes no --project, "
            "--auto-update, --no-auto-update or --herdr. Nothing was written.")
        return 1
    if (args.auto_update or args.no_auto_update) and args.project:
        say("refused: --auto-update and --no-auto-update act on the user-scope install, so they take no "
            "--project. Nothing was written.")
        return 1
    if args.gradle_hook and not args.project:
        say("refused: --gradle-hook wires a hook into a project, so it needs --project <path>. Nothing was written.")
        return 1

    if args.no_auto_update:
        # A removal and nothing else: no plan and no install, so no option of this run can change the kit home.
        schedule = load_optional("kit_schedule")
        if schedule is None:
            say("auto-update  this kit tree does not carry install/kit_schedule.py; nothing to remove")
            return 1
        schedule.unregister(kit_lib.kit_home(), dry_run=args.dry_run, out=say)
        return 0

    run = Run(args)
    if args.gradle_hook:
        # Answered before anything else happens, so a refusal leaves the disk as it was.
        project = os.path.abspath(os.path.expanduser(args.project))
        refusal = kit_adopt.gradle_hook_refusal(run.engine, project, os.path.join(ROOT, "project-template"),
                                                force=args.force)
        if refusal:
            say(refusal)
            return 1
    if run.manifest.broken:
        say("warn: %s cannot be read, so every file already on disk counts as yours." % run.manifest.path)
        if args.dry_run:
            say("      A real run would move it aside and start a fresh record.")
        else:
            say("      Moved aside to %s; this run starts a fresh record." % run.manifest.move_broken_aside(run.stamp))
        say()
    if args.project:
        return run.project()
    return run.user()


if __name__ == "__main__":
    sys.exit(main())
