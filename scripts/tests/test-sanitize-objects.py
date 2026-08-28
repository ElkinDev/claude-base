"""What a pushed ref publishes, and what the guard does with each layer of it.

python scripts/tests/test-sanitize-objects.py   (needs git on PATH)

A ref does not have to be a branch. It can be an annotated tag, a chain of them, or a tag on a
blob or a tree with no commit behind it at all, and every one of those shapes publishes objects
the push would carry. The rest of the range behaviour lives in test-sanitize-check.py.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_case import ADDRESS, CODENAME, GuardCase, rules_of  # noqa: E402


class ObjectTest(GuardCase):
    def test_a_range_scans_an_annotated_tag_message(self):
        self.commit("initial commit")
        self.commit("a clean subject", name="ported.md")
        self.git("tag", "-a", "v9.9.9", "-m", "port the " + CODENAME + " notes")
        self.denylist(CODENAME)
        result = self.run_guard("--range", "%s..%s" % ("0" * 40, "v9.9.9"))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("private-1: tag ", result.stdout)
        self.assertNoTerm(result)

    def test_a_tag_pointing_at_a_blob_is_scanned(self):
        self.commit("initial commit")
        self.file("loose.md", "the " + CODENAME + " migration notes\n")
        blob = self.git("hash-object", "-w", "loose.md").stdout.strip()
        self.git("tag", "-a", "v1.0", blob, "-m", "a clean message")
        tag = self.git("rev-parse", "v1.0").stdout.strip()
        self.denylist(CODENAME)
        result = self.run_guard("--range", "%s..%s" % ("0" * 40, tag))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("private-1: blob ", result.stdout)
        self.assertNoTerm(result)

    def test_a_tag_pointing_at_a_tag_scans_the_inner_message(self):
        self.commit("initial commit")
        self.git("tag", "-a", "inner", "-m", "port the " + CODENAME + " notes")
        inner = self.git("rev-parse", "inner").stdout.strip()
        self.git("tag", "-a", "outer", inner, "-m", "a clean message")
        outer = self.git("rev-parse", "outer").stdout.strip()
        self.denylist(CODENAME)
        result = self.run_guard("--range", "%s..%s" % ("0" * 40, outer))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("private-1: tag ", result.stdout)
        self.assertNoTerm(result)

    def test_a_tag_whose_tagger_line_carries_an_address_is_caught(self):
        self.commit("initial commit")
        self.git("-c", "user.email=" + ADDRESS, "-c", "user.name=a tagger",
                 "tag", "-a", "v2.0", "-m", "a clean message")
        tag = self.git("rev-parse", "v2.0").stdout.strip()
        result = self.run_guard("--range", "%s..%s" % ("0" * 40, tag))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(rules_of(result.stdout), ["email"], result.stdout)

    def test_a_tag_pointing_at_a_tree_scans_the_blobs_under_it(self):
        self.commit("initial commit")
        self.file("docs/" + CODENAME + "-notes.md", "a clean body\n")
        self.file("docs/plain.md", "the " + CODENAME + " plan\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "add the docs")
        tree = self.git("rev-parse", "HEAD^{tree}").stdout.strip()
        self.git("tag", "-a", "treetag", tree, "-m", "a clean message")
        tag = self.git("rev-parse", "treetag").stdout.strip()
        self.denylist(CODENAME)
        result = self.run_guard("--range", "%s..%s" % ("0" * 40, tag))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("private-path", rules_of(result.stdout), result.stdout)
        self.assertIn("private-1", rules_of(result.stdout), result.stdout)
        self.assertNoTerm(result)

    def buried_denylist(self):
        """A history that committed the denylist and then took it back out of the index."""
        base = self.commit("initial commit")
        self.denylist(CODENAME)
        self.git("add", "-f", ".sanitize/private-denylist.txt")
        self.git("commit", "-q", "-m", "add the denylist by mistake")
        self.git("rm", "-q", "--cached", ".sanitize/private-denylist.txt")
        self.git("commit", "-q", "-m", "take it back out")
        return base, self.git("rev-parse", "HEAD").stdout.strip()

    def test_a_range_that_publishes_the_denylist_is_a_hard_failure(self):
        base, tip = self.buried_denylist()
        self.assertEqual(self.git("ls-files").stdout.strip(), "notes.md")
        result = self.run_guard("--range", "%s..%s" % (base, tip))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("private denylist is in the pushed history", result.stderr)
        self.assertNoTerm(result)

    def test_a_tree_that_holds_the_denylist_is_a_hard_failure(self):
        self.buried_denylist()
        tree = self.git("rev-parse", "HEAD~1^{tree}").stdout.strip()
        self.git("tag", "-a", "treetag", tree, "-m", "a clean message")
        result = self.run_guard("--range", "%s..%s" % ("0" * 40, "treetag"))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("private denylist is in the pushed history", result.stderr)
        self.assertNoTerm(result)

    def test_a_tree_entry_that_carries_no_content_is_still_name_scanned(self):
        # An empty directory and a submodule have no blob to read, and a forged tree can hold
        # either. The name is the leak, so `ls-tree` keeps the directory entries themselves.
        self.commit("initial commit")
        self.denylist(CODENAME)
        empty = self.git("hash-object", "-t", "tree", "-w", self.file("empty.bin", ""))
        blob = self.git("hash-object", "-w", self.file("plain.md", "a clean body\n"))
        tree = self.git("mktree", stdin="100644 blob %s\tplain.md\n040000 tree %s\t%s-dir\n"
                        % (blob.stdout.strip(), empty.stdout.strip(), CODENAME))
        self.git("tag", "-a", "treetag", tree.stdout.strip(), "-m", "a clean message")
        result = self.run_guard("--range", "%s..%s" % ("0" * 40, "treetag"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(rules_of(result.stdout), ["private-path"], result.stdout)
        self.assertNoTerm(result)


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1).result.wasSuccessful() is False)
