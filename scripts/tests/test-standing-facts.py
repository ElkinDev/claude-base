#!/usr/bin/env python3
"""Tests the standing-facts import in the project template.

A project's owner facts that stay true (bought, created, named, chosen) live in FACTS.local.md, which the
template's CLAUDE.md imports, so they load at every start and after every compaction instead of depending on
a summary. On 2026-10-16 a summary dropped a domain the register said was bought. The import only works as
its own line, and the file only stays private if INSTALL.md says to exclude it, so both are asserted.

Run:
    python test-standing-facts.py
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TEMPLATE = os.path.join(ROOT, "project-template", "CLAUDE.md")
INSTALL = os.path.join(ROOT, "INSTALL.md")


def read(path):
    with open(path, encoding="utf-8") as h:
        return h.read().replace("\r\n", "\n")


class StandingFacts(unittest.TestCase):
    def test_the_template_imports_the_facts_file_on_its_own_line(self):
        lines = [line.strip() for line in read(TEMPLATE).split("\n")]
        self.assertIn("## Standing facts", lines)
        self.assertIn("@FACTS.local.md", lines)
        self.assertLess(lines.index("## Standing facts"), lines.index("@FACTS.local.md"))

    def test_the_section_states_the_writer_rule_and_the_cap(self):
        section = read(TEMPLATE).split("## Standing facts", 1)[1].split("\n## ", 1)[0]
        self.assertIn("same turn as that row", section)
        self.assertIn("Cap 3,500 characters", section)
        self.assertIn("never a secret", section)

    def test_install_says_to_keep_the_file_out_of_git(self):
        self.assertIn(r"add `FACTS.local.md` to `.git\info\exclude`", read(INSTALL))


if __name__ == "__main__":
    unittest.main()
