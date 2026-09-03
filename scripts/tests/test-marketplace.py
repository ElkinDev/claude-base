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
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MARKETPLACE = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
PLUGINS = os.path.join(ROOT, "plugins")
KIT_SKILLS = os.path.join(ROOT, "claude", "skills")
TEMPLATE = os.path.join(ROOT, "project-template", "CLAUDE.md")
RULES = os.path.join(ROOT, "claude", "CLAUDE.md")
README = os.path.join(ROOT, "README.md")
TOKEN = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
HYPHENED = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+"
KEBAB = re.compile(r"^%s$" % TOKEN)
# The guard is fail-closed by design: no lexical rule tells a person credit from a product
# credit, so every capitalised pair reads as a person and a product name a manifest legitimately
# publishes is an explicit, reviewed waiver here, one entry per pair. Empty today, because the
# three manifests carry none. A waiver matches the full greedy hit, so a three word product name
# has to be listed as its whole match.
WAIVED_NAMES = frozenset()
# Two or more capitalised words in a row, searched anywhere in a value: anchored to the whole
# value the pattern read a person credited inside a sentence as clean.
NAME = r"[A-Z][a-z]+(?: [A-Z][a-z]+)+"
PERSON = re.compile(NAME)
NAMESPACED = ["`/delivery:story`", "`/delivery:sdd`", "`/delivery:work-item`",
              "`/orchestration:wave-orchestration`", "`/orchestration:herdr-driving`"]


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

    Eight shapes, and every one of them needs a hyphen or an explicit marker, so that ordinary
    prose inside the same brackets, arrows and slashes is not mistaken for a skill: a name
    carrying the noun that labels it (`(<name> skill)`, or a hyphenated `<name> skill` and
    `<name> sheet` outside brackets), a hyphenated name alone in brackets, a bracketed comma
    list of names, a hyphenated step of an arrow chain, a slash list, a hyphenated name closing
    a clause after "with", a bare slash command in backticks, and the namespaced marketplace
    command a plugin installs, plugin segment then colon then name, which yields the name only:
    the plugin is a directory, not a skill, and a reader sent to it finds nothing to invoke.

    A comma or slash list is read as names only when at least two of its parts are hyphenated
    and every part is kebab, which is what keeps `merge/deploy` and `implementer/designer` out.
    """
    flat = re.sub(r"\s+", " ", text)
    found = set(re.findall(r"\((%s) skill\)" % TOKEN, flat))
    found |= set(re.findall(r"(?<![\w-])(%s) (?:skill|sheet)\b" % HYPHENED, flat))
    found |= set(re.findall(r"\((%s)\)" % HYPHENED, flat))
    found |= set(re.findall(r"\bwith (%s)[.,]" % HYPHENED, flat))
    found |= set(re.findall(r"`/(%s)(?![\w:-])" % TOKEN, flat))
    found |= set(re.findall(r"`/%s:(%s)(?![\w-])" % (TOKEN, TOKEN), flat))

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


def unwaived(value, waived=WAIVED_NAMES):
    """The capitalised pairs a value carries that no waiver clears, in the order they appear.
    One list, read by the filter and by the message, so the pair a failure names is always the
    pair that fired and never an earlier one the waiver already cleared.
    """
    return [hit for hit in PERSON.findall(value) if hit not in waived]


def person_hits(values, waived=WAIVED_NAMES):
    """The values that read as a person: every value carrying a capitalised pair that is not
    waived. Returned in the order given, so a failure names what it caught instead of only
    saying that something matched.
    """
    return [value for value in values if unwaived(value, waived)]


def quoted(text):
    """Every double quoted string of a json document, read off the text rather than the tree, so
    a key, a nested value and a value the schema does not describe are all covered."""
    return re.findall(r'"([^"]*)"', text)


def blocks_naming(path, needle):
    """The blank line separated blocks of a file that carry `needle`."""
    return [block for block in read(path).split("\n\n") if needle in block]


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

    # Every skill the template hands a reader, counted by machine off the file as the size of
    # `mentions(read(TEMPLATE))` and asserted exactly. The parser has to reach all of them: a
    # shape it no longer reads drops names silently, and a reader sent to a name nobody parsed
    # is exactly the break this class is here to catch. Exact rather than a floor, because a
    # floor clears on a parser that has stopped reading a shape the template still uses.
    NAMED_IN_TEMPLATE = 19

    def setUp(self):
        self.named = mentions(read(TEMPLATE))
        self.known = set(skill_dirs(KIT_SKILLS))
        for plugin in plugin_names():
            self.known |= set(skill_dirs(os.path.join(PLUGINS, plugin, "skills")))

    def test_the_reader_of_the_template_can_find_every_skill_it_names(self):
        # The count first: without it the check below passes on a parser that found nothing.
        self.assertEqual(len(self.named), self.NAMED_IN_TEMPLATE, sorted(self.named))
        self.assertEqual(sorted(self.named - self.known), [])

    def test_the_moved_skills_are_among_the_names_the_template_hands_out(self):
        # Proves the reader above is reading: these five moved into a plugin, and the convention
        # paragraph names all five in their namespaced form, which is where wave-orchestration
        # enters the set the template hands out.
        self.assertLessEqual({"story", "sdd", "work-item", "herdr-driving", "wave-orchestration"},
                             self.named)

    def test_the_parser_reads_a_namespaced_marketplace_command(self):
        # The marketplace shape: a plugin segment and a skill name in one backticked command.
        # The skill is the second segment, and a parser that yields the first sends a reader to
        # a plugin directory that holds no skill by that name. Read off a copy in a temp folder,
        # so the shape is exercised without the committed template having to carry the example.
        with tempfile.TemporaryDirectory() as folder:
            copy = os.path.join(folder, "CLAUDE.md")
            with open(copy, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(read(TEMPLATE))
                handle.write("\nOn a marketplace machine it is `/delivery:phantom-skill`.\n")
            named = mentions(read(copy))
        self.assertIn("phantom-skill", named)
        self.assertNotIn("delivery", named)


class SkillNamingConvention(unittest.TestCase):
    """The two rules files carry the same paragraph: names are written bare because that is the
    form the installer leaves, and the five plugin skills are invoked namespaced on a machine
    that took the marketplace. One text, byte for byte, so the two files cannot drift apart."""

    def test_both_rules_files_carry_the_same_convention_paragraph(self):
        found = {path: blocks_naming(path, NAMESPACED[0]) for path in (RULES, TEMPLATE)}
        for path, blocks in found.items():
            self.assertEqual(len(blocks), 1, path)
        kit, template = found[RULES][0], found[TEMPLATE][0]
        self.assertEqual(kit.encode("utf-8"), template.encode("utf-8"))
        for form in NAMESPACED:
            self.assertIn(form, kit)


class ManifestHygiene(unittest.TestCase):
    """The three json files are published. No person, no address, no personal attribution."""

    def files(self):
        out = [MARKETPLACE]
        for plugin in plugin_names():
            out.append(os.path.join(PLUGINS, plugin, ".claude-plugin", "plugin.json"))
        return out

    def person_failures(self, path, waived=WAIVED_NAMES):
        """What the guard says about a manifest, one line per value that reads as a person: the
        value, the pair it carries, and the waiver that clears a pair that is a product name."""
        return ["%s: capitalised pair '%s' reads as a person; add it to WAIVED_NAMES if it is a "
                "product name" % (value, unwaived(value, waived)[0])
                for value in person_hits(quoted(read(path)), waived)]

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
            self.assertEqual(self.person_failures(path), [], path)

    def test_a_person_inside_a_sentence_is_caught_too(self):
        # A manifest value is prose. A name that is the whole value is the easy case; the one
        # that would actually ship sits inside a description, so the check has to search the
        # value rather than match it end to end.
        sentence = "Kept current by Lorem Ipsum, who reviews the set."
        self.assertEqual(person_hits(["claude-base", "0.1.0", sentence]), [sentence])

    def test_an_unwaived_capitalised_pair_in_prose_fails_and_names_the_waiver(self):
        # The fail-closed half. A description naming a product carries a capitalised pair like
        # any other value, so the guard rejects it and says what a maintainer has to do about it.
        # Read off the real description, extended on a temp copy, so the case moves with the file
        # it guards without the published manifest having to carry the example.
        data = read_json(os.path.join(PLUGINS, "delivery", ".claude-plugin", "plugin.json"))
        for suffix in (" Powered by Claude Code.", " Runs from Claude Code."):
            described = data["description"] + suffix
            self.assertEqual(person_hits(["claude-base", "0.1.0", described]), [described])
            with tempfile.TemporaryDirectory() as folder:
                copy = os.path.join(folder, "plugin.json")
                with open(copy, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(dict(data, description=described), handle)
                complaints = self.person_failures(copy)
            self.assertEqual(len(complaints), 1, complaints)
            self.assertIn("capitalised pair 'Claude Code'", complaints[0])
            self.assertIn("WAIVED_NAMES", complaints[0])

    def test_a_waived_pair_passes(self):
        # The only product name property the pattern holds: an explicit waiver, matched against
        # the whole greedy hit, drops the value. Nothing else tells a product from a person.
        values = [" Powered by Claude Code.", " Runs from Claude Code."]
        self.assertEqual(person_hits(values, waived={"Claude Code"}), [])

    def test_the_complaint_names_the_pair_that_fired_not_a_waived_one(self):
        # The message has to read the list the filter reads. Once a waiver is in force, a
        # cleared pair can sit in front of the one that actually fired, and a message built
        # from the first match in the value would name the cleared pair and read as a guard
        # bug on the day a real leak ships.
        data = read_json(os.path.join(PLUGINS, "delivery", ".claude-plugin", "plugin.json"))
        described = "Powered by Claude Code. Maintained by Lorem Ipsum."
        with tempfile.TemporaryDirectory() as folder:
            copy = os.path.join(folder, "plugin.json")
            with open(copy, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(dict(data, description=described), handle)
            complaints = self.person_failures(copy, waived={"Claude Code"})
        self.assertEqual(len(complaints), 1, complaints)
        self.assertIn("capitalised pair 'Lorem Ipsum'", complaints[0])
        self.assertNotIn("'Claude Code'", complaints[0])

    def test_the_waiver_reaches_the_manifest_scan(self):
        # The waiver has to be live on the path the guard runs on the manifests, not only on a
        # direct call. One copy, two waivers: the pair clears when it is listed and complains
        # when it is not, so hardcoding an empty waiver into the scan reds here.
        data = read_json(os.path.join(PLUGINS, "delivery", ".claude-plugin", "plugin.json"))
        with tempfile.TemporaryDirectory() as folder:
            copy = os.path.join(folder, "plugin.json")
            with open(copy, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(dict(data, description="Powered by Claude Code."), handle)
            cleared = self.person_failures(copy, waived={"Claude Code"})
            bare = self.person_failures(copy)
        self.assertEqual(cleared, [])
        self.assertEqual(len(bare), 1, bare)
        self.assertIn("capitalised pair 'Claude Code'", bare[0])

    def test_a_person_after_owner_copyright_or_thanks_is_rejected(self):
        # Attribution is not a word list. A gate keyed on credit words read these three as clean
        # while firing on ordinary prepositions; a search with no gate has nothing to slip past.
        for value in ("Owner: Lorem Ipsum.", "Copyright 2026 Lorem Ipsum.",
                      "Thanks to Lorem Ipsum for the review."):
            self.assertEqual(person_hits(["claude-base", "0.1.0", value]), [value])
            self.assertEqual(PERSON.findall(value), ["Lorem Ipsum"])


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
