#!/usr/bin/env python3
"""Tests for the plugin marketplace: the two channels a machine can take the kit's skills
through, and the rule that no skill is ever offered by both at once.

The marketplace at `.claude-plugin/marketplace.json` lists the plugins under `plugins/`, each
with its own `.claude-plugin/plugin.json` and its skills. Everything else stays under `claude/`
and reaches a machine through `install.ps1`. The load bearing test here is the duplicate guard:
a skill directory that exists under `claude/skills` and under a plugin would install twice, once
bare and once namespaced, and the two copies would drift.

`claude plugin validate --strict` is the format's own checker. It runs when the `claude` command
is on PATH and is skipped when it is not.

Run:
    python test-marketplace.py
"""
import json
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MARKETPLACE = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
PLUGINS = os.path.join(ROOT, "plugins")
KIT_SKILLS = os.path.join(ROOT, "claude", "skills")
TEMPLATE = os.path.join(ROOT, "project-template", "CLAUDE.md")
README = os.path.join(ROOT, "README.md")
TOKEN = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
HYPHENED = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+"
KEBAB = re.compile(r"^%s$" % TOKEN)
PERSON = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z]+)+$")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def read_json(path):
    return json.loads(read(path))


def skill_dirs(root):
    """The skill directory names under a skills root: a directory holding a SKILL.md."""
    if not os.path.isdir(root):
        return []
    return sorted(name for name in os.listdir(root)
                  if os.path.isfile(os.path.join(root, name, "SKILL.md")))


def plugin_names():
    if not os.path.isdir(PLUGINS):
        return []
    return sorted(name for name in os.listdir(PLUGINS)
                  if os.path.isdir(os.path.join(PLUGINS, name)))


def frontmatter(path):
    """The key/value pairs of a SKILL.md frontmatter block, or {} when there is no block."""
    lines = read(path).splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line and not line.startswith((" ", "\t", "-")):
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip()
    return out


def mentions(text):
    """The skill names a document points a reader at, read from the shapes it uses to name one.

    Seven shapes, and every one of them needs a hyphen or an explicit marker, so that ordinary
    prose inside the same brackets, arrows and slashes is not mistaken for a skill: a name
    carrying the noun that labels it (`(<name> skill)`, or a hyphenated `<name> skill` and
    `<name> sheet` outside brackets), a hyphenated name alone in brackets, a bracketed comma
    list of names, a hyphenated step of an arrow chain, a slash list, a hyphenated name closing
    a clause after "with", and a slash command in backticks.

    A comma or slash list is read as names only when at least two of its parts are hyphenated
    and every part is kebab, which is what keeps `merge/deploy` and `implementer/designer` out.
    """
    flat = re.sub(r"\s+", " ", text)
    found = set(re.findall(r"\((%s) skill\)" % TOKEN, flat))
    found |= set(re.findall(r"(?<![\w-])(%s) (?:skill|sheet)\b" % HYPHENED, flat))
    found |= set(re.findall(r"\((%s)\)" % HYPHENED, flat))
    found |= set(re.findall(r"\bwith (%s)[.,]" % HYPHENED, flat))
    found |= set(re.findall(r"`/(%s)" % TOKEN, flat))

    def take(parts):
        parts = [part.strip() for part in parts]
        hyphened = [part for part in parts if "-" in part]
        if len(hyphened) >= 2 and all(KEBAB.match(part) for part in parts):
            found.update(parts)

    for group in re.findall(r"\(([^()]*,[^()]*,[^()]*)\)", flat):
        take(group.split(","))
    for chain in re.findall(r"(?<![\w-])(?:%s ?/ ?)+%s" % (TOKEN, TOKEN), flat):
        take(chain.split("/"))
    for chain in re.findall(r"(?:[^ ]+ -> )+[^ ]+[^.]*", flat):
        for step in re.split(r"->|/", chain):
            step = step.strip().rstrip(".")
            if "-" in step and KEBAB.match(step):
                found.add(step)
    return found


class MarketplaceFile(unittest.TestCase):
    """The root file: what it declares, and that every source it points at is really there."""

    def setUp(self):
        self.data = read_json(MARKETPLACE)

    def test_the_marketplace_is_named_after_the_repository_and_owns_itself(self):
        heading = read(README).splitlines()[0]
        self.assertEqual(heading[:2], "# ")
        self.assertEqual(self.data["name"], heading[2:].strip())
        self.assertTrue(KEBAB.match(self.data["name"]), self.data["name"])
        self.assertEqual(self.data["owner"]["name"], self.data["name"])

    def test_every_entry_points_at_a_plugin_that_declares_the_same_name(self):
        entries = self.data["plugins"]
        self.assertEqual(sorted(entry["name"] for entry in entries), plugin_names())
        for entry in entries:
            source = os.path.join(ROOT, entry["source"].replace("./", "").replace("/", os.sep))
            self.assertTrue(os.path.isdir(source), source)
            manifest = read_json(os.path.join(source, ".claude-plugin", "plugin.json"))
            self.assertEqual(manifest["name"], entry["name"])
            self.assertTrue(entry["description"].strip())
            self.assertTrue(re.match(r"^\d+\.\d+\.\d+$", manifest["version"]), manifest["version"])

    def test_the_two_plugins_carry_the_skills_that_only_work_as_a_set(self):
        self.assertEqual(skill_dirs(os.path.join(PLUGINS, "delivery", "skills")),
                         ["sdd", "story", "work-item"])
        self.assertEqual(skill_dirs(os.path.join(PLUGINS, "orchestration", "skills")),
                         ["herdr-driving", "wave-orchestration"])


class PluginSkills(unittest.TestCase):
    """Each plugin skill is a real skill: a frontmatter name matching the directory it sits in."""

    def test_every_plugin_skill_declares_its_own_directory_name(self):
        checked = 0
        for plugin in plugin_names():
            skills = os.path.join(PLUGINS, plugin, "skills")
            self.assertTrue(skill_dirs(skills), plugin)
            for skill in skill_dirs(skills):
                data = frontmatter(os.path.join(skills, skill, "SKILL.md"))
                self.assertEqual(data.get("name"), skill)
                self.assertTrue(data.get("description", "").strip(), skill)
                checked += 1
        self.assertEqual(checked, 5)

    def test_no_skill_is_offered_by_both_channels(self):
        # The load bearing one. A name on both sides installs twice, bare from install.ps1 and
        # namespaced from the marketplace, and the two copies drift from there.
        bare = set(skill_dirs(KIT_SKILLS))
        for plugin in plugin_names():
            packed = set(skill_dirs(os.path.join(PLUGINS, plugin, "skills")))
            self.assertEqual(bare & packed, set(), plugin)


class TemplateMentions(unittest.TestCase):
    """The template names skills bare. Every name it points at has to exist somewhere."""

    # Every skill the template hands a reader, counted by hand off the file. The parser has to
    # reach all of them: a shape it no longer reads drops names silently, and a reader sent to
    # a name nobody parsed is exactly the break this class is here to catch.
    NAMED_IN_TEMPLATE = 18

    def setUp(self):
        self.named = mentions(read(TEMPLATE))
        self.known = set(skill_dirs(KIT_SKILLS))
        for plugin in plugin_names():
            self.known |= set(skill_dirs(os.path.join(PLUGINS, plugin, "skills")))

    def test_the_reader_of_the_template_can_find_every_skill_it_names(self):
        # The count first: without it the check below passes on a parser that found nothing.
        self.assertGreaterEqual(len(self.named), self.NAMED_IN_TEMPLATE, sorted(self.named))
        self.assertEqual(sorted(self.named - self.known), [])

    def test_the_moved_skills_are_among_the_names_the_template_hands_out(self):
        # Proves the reader above is reading: these four moved into a plugin. The fifth,
        # wave-orchestration, is not named anywhere in the template, so only the plugin
        # inventory test above can cover it.
        self.assertLessEqual({"story", "sdd", "work-item", "herdr-driving"}, self.named)


class ManifestHygiene(unittest.TestCase):
    """The three json files are published. No person, no address, no personal attribution."""

    def files(self):
        out = [MARKETPLACE]
        for plugin in plugin_names():
            out.append(os.path.join(PLUGINS, plugin, ".claude-plugin", "plugin.json"))
        return out

    def test_no_manifest_carries_an_address_or_a_person(self):
        self.assertEqual(len(self.files()), 3)
        repository = read_json(MARKETPLACE)["name"]
        for path in self.files():
            text = read(path)
            self.assertNotIn("@", text)
            data = json.loads(text)
            author = data.get("author")
            if author is not None:
                # The format wants attribution. It gets the repository and nothing else: a
                # single `name`, equal to the marketplace name, so no address and no person
                # can ride in on the field the validator asks for.
                self.assertIsInstance(author, dict, path)
                self.assertEqual(sorted(author), ["name"], path)
                self.assertEqual(author["name"], repository, path)
            for value in re.findall(r'"([^"]*)"', text):
                self.assertIsNone(PERSON.match(value), "%s in %s" % (value, path))


class FormatValidator(unittest.TestCase):
    """What the format's own checker says, when the machine has it.

    The checker warns on a manifest with no `author`, and `--strict` turns every warning into a
    failure. The manifests carry the repository name as author, which is what the field is for
    here and is not a person, so there is nothing left to warn about: both runs have to come
    back clean. The strict run is read for its exit code and for its text, because a `claude`
    that rejects `--strict` outright would otherwise prove nothing.
    """

    def validate(self, path, strict):
        claude = shutil.which("claude")
        if not claude:
            self.skipTest("the claude command is not on PATH, so the format validator did not run")
        argv = [claude, "plugin", "validate", path] + (["--strict"] if strict else [])
        run = subprocess.run(argv, capture_output=True, timeout=180, cwd=ROOT,
                             encoding="utf-8", errors="replace")
        return run.returncode, run.stdout + run.stderr

    def check(self, path):
        code, out = self.validate(path, strict=False)
        self.assertEqual(code, 0, out)
        self.assertIn("Validation passed", out)
        code, strict = self.validate(path, strict=True)
        self.assertEqual(code, 0, strict)
        self.assertIn("Validation passed", strict)
        self.assertNotIn("warning", strict.lower(), strict)

    def test_the_marketplace_root_validates(self):
        self.check(ROOT)

    def test_every_plugin_validates(self):
        for plugin in plugin_names():
            self.check(os.path.join(PLUGINS, plugin))


if __name__ == "__main__":
    unittest.main(verbosity=2)
