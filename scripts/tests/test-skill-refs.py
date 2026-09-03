#!/usr/bin/env python3
"""Tests that every citation of a plugin skill names both forms a machine can hold it under.

Five skills reach a machine through two mutually exclusive channels. `install.ps1` copies them
into the kit home under bare names (`story`, `sdd`, `work-item`, `wave-orchestration`,
`herdr-driving`); `claude plugin install delivery@claude-base` and `orchestration@claude-base`
install the same five namespaced, so they are typed `/delivery:story` through
`/orchestration:herdr-driving`. A document that cites only the bare form sends a marketplace
user to a skill that does not exist on their machine, and a document that cites only the
namespaced form does the same to an installer user. Each citation in the table below therefore
carries both forms on one line, so the sentence reads on its own with no jump to the paragraph
that explains the two channels.

Citations are located by a regex anchor on the surrounding words, never by a line number: these
files are edited often and the numbers drift a line or two every sprint. The namespaced form is
matched whole, so the plugin segment on its own, `delivery`, never satisfies a citation; the
shape a user actually types, `/delivery:sdd`, is what counts.

Run:
    python test-skill-refs.py
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# One row per citation: the file, a regex anchor on the words around it, and the skills named on
# that line as (bare form as the prose writes it, the full namespaced invocation). Only skills the
# plugins publish are listed; explore-and-plan, tdd-workflow and the rest ship through the
# installer alone and stay bare wherever they appear.
CITATIONS = [
    ("INSTALL.md", r"then drive it with",
     [("/sdd", "/delivery:sdd")]),
    ("INSTALL.md", r"commit as the fix",
     [("herdr-driving", "/orchestration:herdr-driving")]),
    ("README.md", r"spec structure for spec-driven projects",
     [("/sdd", "/delivery:sdd")]),
    ("README.md", r"branch \+ evidence",
     [("/story", "/delivery:story"), ("work-item", "/delivery:work-item")]),
    ("docs/README.md", r"the source of truth for building the toolkit",
     [("/sdd", "/delivery:sdd")]),
    ("docs/00-product/domain-model.md", r"a composed delivery pipeline",
     [("/story", "/delivery:story"), ("/sdd", "/delivery:sdd")]),
    ("docs/01-requirements/user-stories.md", r"runs end to end without any edit to skills",
     [("/story", "/delivery:story")]),
    ("docs/03-features/F04-codex-adapter.md", r"exposed as prompt files",
     [("story", "/delivery:story"), ("sdd", "/delivery:sdd")]),
    ("project-template/docs/README.md", r"the source of truth for a spec-driven project",
     [("/sdd", "/delivery:sdd")]),
    ("project-template/docs/README.md", r"roadmap \+ the active sprint",
     [("/sdd", "/delivery:sdd")]),
    ("install.ps1", r"Drive it with",
     [("/sdd", "/delivery:sdd")]),
    ("scripts/doctor.py", r"see the .*skill",
     [("herdr-driving", "/orchestration:herdr-driving")]),
]


def token(text):
    """A whole-token matcher. The guards on both sides are what stop a bare form from being
    read out of a namespaced one: `work-item` inside `/delivery:work-item` is preceded by a
    colon and is rejected, and `delivery` inside `/delivery:sdd` is followed by a colon."""
    return re.compile(r"(?<![\w:/-])%s(?![\w:-])" % re.escape(text))


def carries(line, bare, namespaced):
    """True when one line names the skill both ways, the bare invocation and the namespaced one."""
    return bool(token(bare).search(line)) and bool(token(namespaced).search(line))


def lines_of(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        return handle.read().splitlines()


def locate(path, anchor):
    """Every 1-based line number in the file whose text matches the anchor."""
    pattern = re.compile(anchor)
    return [n for n, line in enumerate(lines_of(path), 1) if pattern.search(line)]


class AnchorTest(unittest.TestCase):
    def test_each_anchor_finds_exactly_one_line(self):
        drifted = []
        for path, anchor, _ in CITATIONS:
            hits = locate(path, anchor)
            if len(hits) != 1:
                drifted.append("%s  anchor %r matched %d lines" % (path, anchor, len(hits)))
        self.assertEqual([], drifted, "\n" + "\n".join(drifted))


class CitationTest(unittest.TestCase):
    def test_every_citation_carries_both_forms(self):
        missing = []
        for path, anchor, skills in CITATIONS:
            hits = locate(path, anchor)
            if len(hits) != 1:
                missing.append("%s  anchor %r matched %d lines" % (path, anchor, len(hits)))
                continue
            number = hits[0]
            line = lines_of(path)[number - 1]
            for bare, namespaced in skills:
                if not carries(line, bare, namespaced):
                    missing.append("%s:%d  %s needs %s on the same line: %s"
                                   % (path, number, bare, namespaced, line.strip()))
        self.assertEqual([], missing, "\n" + "\n".join(missing))


class SegmentAloneTest(unittest.TestCase):
    """The plugin name is not an invocation. Only the full `/delivery:sdd` shape counts."""

    def test_plugin_segment_alone_does_not_satisfy(self):
        self.assertFalse(carries("drive it with `/sdd` (the delivery plugin)",
                                 "/sdd", "/delivery:sdd"))

    def test_missing_leading_slash_does_not_satisfy(self):
        self.assertFalse(carries("drive it with `/sdd` (`delivery:sdd`)",
                                 "/sdd", "/delivery:sdd"))

    def test_namespaced_form_alone_does_not_satisfy(self):
        self.assertFalse(carries("drive it with `/delivery:sdd`", "/sdd", "/delivery:sdd"))

    def test_bare_form_alone_does_not_satisfy(self):
        self.assertFalse(carries("drive it with `/sdd`", "/sdd", "/delivery:sdd"))

    def test_both_forms_on_one_line_satisfy(self):
        self.assertTrue(carries("drive it with `/sdd` (`/delivery:sdd` from the marketplace)",
                                "/sdd", "/delivery:sdd"))

    def test_bare_form_is_not_read_out_of_the_namespaced_one(self):
        self.assertFalse(carries("run `/delivery:work-item` first",
                                 "work-item", "/delivery:work-item"))


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1, exit=False).result.wasSuccessful() is False)
