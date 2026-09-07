#!/usr/bin/env python3
"""Tests the bulk-reader agent, its skill, and the two agent definitions that cite it.

The bulk reader is the one explicit exception to the rule that work agents run on Opus: it reads
large files whole on a cheap model and returns bullets, so a reviewer or an analyst can orient
itself over an 841-line file without carrying the file in its own window. The exception only pays
while three properties hold, and each of them is asserted here rather than left to a habit.

Cheap: the definition names the small model. Read-only: the tool list is exactly Read, Grep and
Glob, so nothing that could edit, run or delegate is reachable from a summary the caller did not
verify. Bounded: the turn budget stays small and the input is capped at four files per call, because an
agent that keeps reading defeats the point of sending the read away, and a turn budget only bounds
the agent when the caller cannot hand it an unbounded list.

Reachable: the reviewer enumerates its tools, so listing the skill in its definition does nothing
until Agent is on that tools line, and the sentence that says which agent it may delegate to is
what keeps that new tool from becoming a general licence to fan out.

The fourth property is the discipline at the calling end, and it lives in prose, so it is asserted
as prose: the agent closes every answer with the orientation line, the skill states the two limits
in full sentences, and reviewer.md and analyst.md both carry the verification sentence next to
their reading discipline. Deleting that sentence from either agent reddens this test, which is the
point: the sentence is the only thing standing between a summary and a verdict founded on it.

The install pairing is checked last. The kit reaches a machine through install.ps1, which copies
folder pairs into the kit home; an agent and a skill that are not in that list ship to nobody.

Run:
    python test-bulk-reader.py
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

AGENT = os.path.join(ROOT, "claude", "agents", "bulk-reader.md")
SKILL = os.path.join(ROOT, "claude", "skills", "bulk-read", "SKILL.md")
REVIEWER = os.path.join(ROOT, "claude", "agents", "reviewer.md")
ANALYST = os.path.join(ROOT, "claude", "agents", "analyst.md")
DOCS = os.path.join(ROOT, "docs", "CONTEXT-ECONOMICS.md")
INSTALL = os.path.join(ROOT, "install.ps1")

ORIENTATION_LINE = (
    "Orientation only: verify anything that reaches a finding, a verdict or an edit "
    "with a direct ranged read."
)
LIMIT_EDIT = (
    "Never for a file the caller will edit: an edit needs line numbers the summary "
    "does not carry."
)
LIMIT_VERDICT = (
    "Never a verdict on a summary alone: any claim that reaches a finding, a verdict "
    "or an edit is verified with a direct ranged read."
)
COST_LINE = (
    "About 34 K Haiku tokens and 37 s per call measured on an 841-line file; "
    "the caller keeps about 400 tokens."
)
VERIFY_SENTENCE = (
    "A bulk-reader summary is orientation: any claim about the code that reaches a finding, "
    "a verdict or an edit is verified with a direct ranged read."
)
DELEGATION_SENTENCE = "Delegation is allowed only to bulk-reader, never to any other agent."
# The two halves of the input cap, matched against lowercased text so the sentence may open a
# paragraph in one file and sit mid-line in the other.
CAP_IN_AGENT = "if more than four files are named, read the first four and say which were left"
CAP_IN_SKILL = "up to four files per call; more files, more calls"

MAX_TURNS_CEILING = 12
EXPECTED_TOOLS = ["Read", "Grep", "Glob"]

DOCS_HEADING = "### A cheap reader for a whole file"
DOCS_SOURCE = "one measured day of the reference project, 297 transcripts"
DOCS_HYGIENE = "hygiene, not savings"


def read(path):
    """The file as text, with an assertion message that names it when it is missing."""
    if not os.path.isfile(path):
        raise AssertionError("missing file: %s" % os.path.relpath(path, ROOT))
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def frontmatter(text, path):
    """The YAML-ish frontmatter block as a dict of the single-line keys it declares."""
    if not text.startswith("---\n"):
        raise AssertionError("no frontmatter block in %s" % os.path.relpath(path, ROOT))
    end = text.find("\n---\n", 4)
    if end < 0:
        raise AssertionError("unterminated frontmatter in %s" % os.path.relpath(path, ROOT))
    fields = {}
    for line in text[4:end].split("\n"):
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", line)
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


def body(text):
    """Everything after the frontmatter block."""
    end = text.find("\n---\n", 4)
    return text[end + 5:] if end >= 0 else text


def skills_list(text):
    """The names under the `skills:` key of a frontmatter block, in order."""
    block = re.search(r"^skills:[ \t]*\n((?:[ \t]+-[ \t]*\S+[ \t]*\n)+)", text, re.MULTILINE)
    if not block:
        return []
    return re.findall(r"-[ \t]*(\S+)", block.group(1))


class BulkReaderAgent(unittest.TestCase):
    def setUp(self):
        self.text = read(AGENT)
        self.fields = frontmatter(self.text, AGENT)

    def test_name_and_model(self):
        self.assertEqual("bulk-reader", self.fields.get("name"))
        self.assertEqual("haiku", self.fields.get("model"))

    def test_tools_are_exactly_read_grep_glob(self):
        raw = self.fields.get("tools")
        self.assertIsNotNone(raw, "the definition declares no tools list")
        tools = [item.strip() for item in raw.split(",") if item.strip()]
        self.assertEqual(EXPECTED_TOOLS, tools)

    def test_turn_budget_is_bounded(self):
        raw = self.fields.get("maxTurns")
        self.assertIsNotNone(raw, "the definition declares no maxTurns")
        self.assertLessEqual(int(raw), MAX_TURNS_CEILING)

    def test_body_closes_with_the_orientation_line(self):
        # Last non-empty line, not merely present: a line buried mid-body is not a closing line,
        # and the caller reads the tail of the answer to know the summary was scoped.
        lines = [line.strip() for line in body(self.text).split("\n") if line.strip()]
        self.assertTrue(lines, "the definition has no body")
        self.assertEqual(ORIENTATION_LINE, lines[-1])

    def test_body_caps_the_input_at_four_files(self):
        self.assertIn(CAP_IN_AGENT, self.text.lower())


class BulkReadSkill(unittest.TestCase):
    def setUp(self):
        self.text = read(SKILL)
        self.fields = frontmatter(self.text, SKILL)

    def test_name(self):
        self.assertEqual("bulk-read", self.fields.get("name"))

    def test_names_the_subagent_type_a_caller_types(self):
        self.assertIn("subagent_type: bulk-reader", self.text)

    def test_states_both_limits_in_full(self):
        self.assertIn(LIMIT_EDIT, self.text)
        self.assertIn(LIMIT_VERDICT, self.text)

    def test_states_the_measured_cost(self):
        self.assertIn(COST_LINE, self.text)

    def test_caps_the_input_at_four_files_per_call(self):
        self.assertIn(CAP_IN_SKILL, self.text.lower())


class CallingAgents(unittest.TestCase):
    def test_reviewer_lists_the_skill(self):
        self.assertIn("bulk-read", skills_list(read(REVIEWER)))

    def test_analyst_lists_the_skill(self):
        self.assertIn("bulk-read", skills_list(read(ANALYST)))

    def test_reviewer_carries_the_verification_sentence(self):
        self.assertIn(VERIFY_SENTENCE, read(REVIEWER))

    def test_analyst_carries_the_verification_sentence(self):
        self.assertIn(VERIFY_SENTENCE, read(ANALYST))

    def test_reviewer_can_reach_the_agent_tool(self):
        # The reviewer enumerates its tools, so a skill that tells it to delegate is inert
        # unless Agent is on that line. The analyst enumerates none and gets them all.
        tools = [item.strip() for item in frontmatter(read(REVIEWER), REVIEWER)["tools"].split(",")]
        self.assertIn("Agent", tools)

    def test_reviewer_bounds_what_it_may_delegate_to(self):
        self.assertIn(DELEGATION_SENTENCE, read(REVIEWER))


class Documentation(unittest.TestCase):
    def setUp(self):
        self.text = read(DOCS)

    def test_context_economics_names_the_reader(self):
        self.assertIn("bulk-reader", self.text)

    def test_the_subsection_exists(self):
        self.assertIn(DOCS_HEADING, self.text)

    def section(self):
        start = self.text.index(DOCS_HEADING)
        end = self.text.find("\n### ", start + 1)
        return self.text[start:end if end > 0 else len(self.text)]

    def test_the_measured_figures_are_in_the_subsection(self):
        section = self.section()
        for figure in ("841", "37 s", "34"):
            self.assertIn(figure, section, "missing figure %s" % figure)

    def test_both_limits_are_in_the_subsection(self):
        section = self.section().lower()
        self.assertIn("edit", section)
        self.assertIn("verdict", section)

    def test_the_prize_is_stated_honestly(self):
        self.assertIn(DOCS_HYGIENE, self.section())

    def test_the_percentages_carry_their_source(self):
        # A bare percentage is a number nobody can check. The source stays generic, because
        # this page ships to strangers and the project it was measured on is not theirs.
        self.assertIn(DOCS_SOURCE, self.section())


class InstallPairing(unittest.TestCase):
    """install.ps1 must keep copying both folders, or the agent and the skill ship to nobody."""

    def setUp(self):
        self.text = read(INSTALL)

    def test_agents_folder_is_paired_into_the_kit_home(self):
        self.assertIn(
            "Get-KitPairs (Join-Path $root 'claude\\agents') (Join-Path $kitHome 'agents')",
            self.text,
        )

    def test_skills_folder_is_paired_into_the_kit_home(self):
        self.assertIn(
            "Get-KitPairs (Join-Path $root 'claude\\skills') (Join-Path $kitHome 'skills')",
            self.text,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
