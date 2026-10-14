"""Pins for scripts/trim-index.py: the title-only rewrite, the ASCII word rule with its three-character floor, a title
outside ASCII kept whole unless the whole title is already in the file name or the hook, the lines it leaves alone,
the backup that exists before the index changes and is never overwritten, and the refusal at a root.

    python scripts/tests/test-trim-index.py
    TRIM_INDEX_PY=<path> python scripts/tests/test-trim-index.py      # against a staged copy or a mutant

Every case works on an index in its own temp folder; nothing outside it is read or written. The fixtures outside
ASCII are written as escape sequences, so this file is ASCII.
"""
import contextlib
import importlib.util
import io
import ntpath
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("TRIM_INDEX_PY") or os.path.join(os.path.dirname(HERE), "trim-index.py")

INDEX = (
    "# Memory index\n"
    "\n"
    "- [Gate rerun](gate-rerun.md) - rerun the gate one task at a time\n"
    "- [Tests filter](tests-filter.md) - the filter expands at the repo root\t(tab before the tail)\n"
    "  - [Indented entry](indented-entry.md) - an entry under a bullet\n"
    "- [Zebra stripes](stripes.md) - hook about gates\n"
)
INDEX_TRIMMED = (
    "# Memory index\n"
    "\n"
    "- gate-rerun.md - rerun the gate one task at a time\n"
    "- tests-filter.md - the filter expands at the repo root\t(tab before the tail)\n"
    "  - indented-entry.md - an entry under a bullet\n"
    "- stripes.md (Zebra stripes) - hook about gates\n"
)
OTHER = (
    "# Memory index\n"
    "\n"
    "- a plain note with no link\n"
    "- [no file](nothing) - the target is not a .md file\n"
    "* [star bullet](star.md) - a star is not a dash bullet\n"
    "some prose line\n"
)

# Titles outside ASCII. Each hook is in the title's own script and holds every letter run of the title, or its words
# in another order, but never the whole title: a script that splits such a title into words finds each piece there.
HINDI = "\u0939\u093f\u0902\u0926\u0940"  # two letters, each carrying combining marks
HINDI_HOOK = " - \u0926\u094b\u092a\u0939\u0930 \u0915\u0940 \u092f\u093e\u0926"
THAI = "\u0e14\u0e35"  # a consonant under a vowel mark
THAI_HOOK = " - \u0e40\u0e14\u0e47\u0e01"
HEBREW = "\u05e9\u05c1\u05b8\u05dc\u05d5\u05b9\u05dd"  # pointed, marks out of canonical order; the hook is unpointed
HEBREW_HOOK = " - \u05e9\u05dc\u05d5\u05dd"
HEBREW_NFC = "\u05e9\u05b8\u05c1\u05dc\u05d5\u05b9\u05dd"  # the same word with its marks in canonical order
PERSIAN = "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"  # a zero-width non-joiner inside the word
PERSIAN_HOOK = " - \u0645\u06cc\u062e\u0648\u0627\u0647\u0645"
CJK = "\u6771\u4eac\u30fb\u5927\u962a"  # two words around a middle dot
CJK_HOOK = " - \u5927\u962a\u3068\u6771\u4eac"
CYRILLIC = "\u041f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440"  # two words, held by the hook in the other order
CYRILLIC_HOOK = " - \u043c\u0438\u0440 \u0438 \u043f\u0440\u0438\u0432\u0435\u0442"
MIXED = "Notes \u041f\u0440\u0438\u0432\u0435\u0442"  # an ASCII word and a Cyrillic word
MIXED_HOOK = " - \u043f\u0440\u0438\u0432\u0435\u0442 notes"
EMOJI = "\U0001f680\U0001f525"  # no letter and no digit at all
EMOJI_HOOK = " - hook"
WHOLE = (
    ("Hindi with combining marks", HINDI, HINDI_HOOK, "0939 093f 0902 0926 0940"),
    ("Thai with a vowel mark", THAI, THAI_HOOK, "0e14 0e35"),
    ("pointed Hebrew", HEBREW, HEBREW_HOOK, "05e9 05c1 05b8 05dc 05d5 05b9 05dd"),
    ("Persian with a zero-width non-joiner", PERSIAN, PERSIAN_HOOK, "0645 06cc 200c 062e 0648 0627 0647 0645"),
    ("CJK", CJK, CJK_HOOK, "6771 4eac 30fb 5927 962a"),
    ("Cyrillic", CYRILLIC, CYRILLIC_HOOK, "041f 0440 0438 0432 0435 0442 0020 043c 0438 0440"),
    ("ASCII mixed with Cyrillic", MIXED, MIXED_HOOK, "004e 006f 0074 0065 0073 0020 041f 0440 0438 0432 0435 0442"),
    ("emoji only", EMOJI, EMOJI_HOOK, "1f680 1f525"),
)
PRIVET = "\u041f\u0440\u0438\u0432\u0435\u0442"
NINO = "Ni\u00f1o"  # composed
NINO_DECOMPOSED = "Nin\u0303o"
SHARP_S = "\u00df"  # folds to "ss"
FI_LIGATURE = "\ufb01"  # folds to "fi"
FULL_WIDTH_ABC = "\uff21\uff22\uff23"  # NFC leaves it alone, NFKC would make it ASCII
CIRCLED_ONE_SQUARE_M2 = "\u2460 \u33a1"  # NFKC would make it "1 m2"


def load():
    spec = importlib.util.spec_from_file_location("trim_index", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="trim-index-")
        self.folder = os.path.join(self.tmp, "impl")
        os.makedirs(self.folder)
        self.index = os.path.join(self.folder, "MEMORY.md")
        self.baks = os.path.join(self.tmp, "baks")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, text, newline="\n"):
        with open(self.index, "wb") as f:
            f.write(text.replace("\n", newline).encode("utf-8"))

    def read(self):
        with open(self.index, "rb") as f:
            return f.read().decode("utf-8")

    def run_it(self, *args):
        done = subprocess.run([sys.executable, "-I", SCRIPT, self.index] + list(args), capture_output=True,
                              text=True, encoding="utf-8", timeout=60)
        return done.returncode, done.stdout + done.stderr

    def baks_listed(self):
        return sorted(os.listdir(self.baks)) if os.path.isdir(self.baks) else []


class LineBase(Base):
    """One entry line through --apply, read back as bytes."""
    calls = 0

    def trim(self, line):
        self.calls += 1  # a backup folder of its own per call: two backups in one second share a name
        with open(self.index, "wb") as f:
            f.write((line if isinstance(line, bytes) else line.encode("utf-8")) + b"\n")
        code, out = self.run_it("--apply", "--bak-dir", os.path.join(self.baks, str(self.calls)))
        with open(self.index, "rb") as f:
            return code, out, f.read()

    def kept(self, title, hook, name="kid.md"):
        """The whole title stays in parentheses, byte for byte, and the report counts it."""
        code, out, now = self.trim("- [%s](%s)%s" % (title, name, hook))
        self.assertEqual(0, code, out)
        self.assertEqual(("- %s (%s)%s\n" % (name, title, hook)).encode("utf-8"), now)
        self.assertIn("1 lines rewritten (1 keep their title)", out)

    def dropped(self, title, hook, name="kid.md"):
        """The title goes, every name and hook byte stays, and the report counts no kept title."""
        code, out, now = self.trim("- [%s](%s)%s" % (title, name, hook))
        self.assertEqual(0, code, out)
        self.assertEqual(("- %s%s\n" % (name, hook)).encode("utf-8"), now)
        self.assertIn("1 lines rewritten (0 keep their title)", out)


class TrimIndexTitleOnlyTest(Base):
    def test_a_link_line_becomes_the_file_name_and_keeps_every_hook_byte(self):
        self.put(INDEX)
        code, out = self.run_it("--apply", "--bak-dir", self.baks)
        self.assertEqual(0, code, out)
        text = self.read()
        self.assertIn("- gate-rerun.md - rerun the gate one task at a time\n", text)
        self.assertIn("- tests-filter.md - the filter expands at the repo root\t(tab before the tail)\n", text)
        self.assertIn("  - indented-entry.md - an entry under a bullet\n", text)
        self.assertNotIn("[Gate rerun]", text)
        for line in INDEX.splitlines():  # every file name and every hook byte of the input is in the output
            if "](" in line:
                name = line.split("](", 1)[1].split(")", 1)[0]
                hook = line.split(")", 1)[1]
                self.assertIn(name, text)
                self.assertIn(hook + "\n", text)

    def test_a_dry_run_prints_the_sizes_and_writes_nothing(self):
        self.put(INDEX)
        code, out = self.run_it("--bak-dir", self.baks)
        self.assertEqual(0, code, out)
        self.assertIn("4 lines rewritten", out)
        self.assertIn("names ok True, hooks ok True, other lines ok True", out)
        self.assertEqual(INDEX, self.read())
        self.assertFalse(os.path.exists(self.baks), "a dry run creates no backup folder")

    def test_crlf_lines_keep_their_ending(self):
        self.put(INDEX, newline="\r\n")
        code, out = self.run_it("--apply", "--bak-dir", self.baks)
        self.assertEqual(0, code, out)
        text = self.read()
        self.assertIn("- gate-rerun.md - rerun the gate one task at a time\r\n", text)
        self.assertEqual(text.count("\n"), text.count("\r\n"))


class TrimIndexKeepsUnknownWordTest(Base):
    def test_a_title_word_in_neither_the_name_nor_the_hook_is_kept_in_parentheses(self):
        self.put(INDEX)
        code, out = self.run_it("--apply", "--bak-dir", self.baks)
        self.assertEqual(0, code, out)
        self.assertIn("- stripes.md (Zebra stripes) - hook about gates\n", self.read())  # Zebra is in neither
        self.assertIn("4 lines rewritten (1 keep their title)", out)  # and no other line kept its title

    def test_a_word_found_in_the_hook_is_not_kept_whatever_its_case(self):
        self.put("- [GATES stripes](stripes.md) - hook about gates\n")
        code, out = self.run_it("--apply", "--bak-dir", self.baks)
        self.assertEqual(0, code, out)
        self.assertEqual("- stripes.md - hook about gates\n", self.read())


class TrimIndexLeavesOtherLinesTest(Base):
    def test_a_line_of_any_other_shape_is_unchanged(self):
        self.put(OTHER + "- [Gate rerun](gate-rerun.md) - rerun the gate\n")
        code, out = self.run_it("--apply", "--bak-dir", self.baks)
        self.assertEqual(0, code, out)
        self.assertEqual(OTHER + "- gate-rerun.md - rerun the gate\n", self.read())

    def test_a_second_run_changes_nothing(self):
        self.put(INDEX)
        self.assertEqual(0, self.run_it("--apply", "--bak-dir", self.baks)[0])
        once, baks = self.read(), self.baks_listed()
        code, out = self.run_it()
        self.assertEqual(0, code, out)
        self.assertIn("0 lines rewritten", out)
        code, out = self.run_it("--apply", "--bak-dir", self.baks)
        self.assertEqual(1, code, out)
        self.assertIn("nothing to change", out)
        self.assertEqual(once, self.read())
        self.assertEqual(baks, self.baks_listed(), "a refused run writes no backup")

    def test_a_missing_index_is_a_usage_error(self):
        code, out = self.run_it()
        self.assertEqual(2, code, out)


class TrimIndexBackupExclusiveTest(Base):
    def test_the_backup_holds_the_original_bytes_and_lands_in_bak_dir(self):
        self.put(INDEX)
        code, out = self.run_it("--apply", "--bak-dir", self.baks)
        self.assertEqual(0, code, out)
        names = self.baks_listed()
        self.assertEqual(1, len(names), names)
        self.assertTrue(names[0].startswith("impl-MEMORY.md.") and names[0].endswith(".bak"), names)
        with open(os.path.join(self.baks, names[0]), "rb") as f:
            self.assertEqual(INDEX.encode("utf-8"), f.read())

    def test_the_backup_exists_before_the_index_changes(self):
        self.put(INDEX)
        mod = load()
        with mock.patch.object(mod.os, "replace", side_effect=OSError("swap refused")):
            with mock.patch("sys.stdout"):
                code = mod.main([self.index, "--apply", "--bak-dir", self.baks])
        self.assertEqual(1, code)
        self.assertEqual(INDEX, self.read(), "the swap failed: the index is untouched")
        self.assertFalse(os.path.exists(self.index + ".trim-tmp"), "the temp file is removed")
        names = self.baks_listed()
        self.assertEqual(1, len(names), "the backup was written before the swap was tried")
        with open(os.path.join(self.baks, names[0]), "rb") as f:
            self.assertEqual(INDEX.encode("utf-8"), f.read())

    def test_an_existing_backup_of_the_same_name_is_never_overwritten(self):
        self.put(INDEX)
        os.makedirs(self.baks)
        taken = os.path.join(self.baks, "impl-MEMORY.md.20260101-000000.bak")
        with open(taken, "wb") as f:
            f.write(b"an earlier backup\n")
        mod = load()
        with mock.patch.object(mod.time, "strftime", return_value="20260101-000000"):
            with mock.patch("sys.stdout"):
                code = mod.main([self.index, "--apply", "--bak-dir", self.baks])
        self.assertEqual(1, code)
        with open(taken, "rb") as f:
            self.assertEqual(b"an earlier backup\n", f.read())
        self.assertEqual(INDEX, self.read(), "no backup, no rewrite")

    def test_an_index_that_changed_since_it_was_read_is_refused(self):
        self.put(INDEX)
        mod = load()
        real = mod.os.makedirs

        def edit_then_make(*args, **kw):  # a live agent appends an entry between the read and the swap
            real(*args, **kw)
            with open(self.index, "ab") as f:
                f.write(b"- late.md - added meanwhile\n")

        with mock.patch.object(mod.os, "makedirs", side_effect=edit_then_make):
            with mock.patch("sys.stdout"):
                code = mod.main([self.index, "--apply", "--bak-dir", self.baks])
        self.assertEqual(1, code)
        self.assertEqual(INDEX + "- late.md - added meanwhile\n", self.read())


class TrimIndexNonAsciiTitleKeptWholeTest(LineBase):
    def test_the_fixtures_are_ascii_escapes_of_the_named_code_points(self):
        for label, title, hook, points in WHOLE:
            with self.subTest(label):
                self.assertEqual(points, " ".join("%04x" % ord(c) for c in title))
                self.assertFalse(title.isascii())
                self.assertNotIn(title, hook)
        self.assertEqual(HEBREW_NFC, unicodedata.normalize("NFC", HEBREW))
        self.assertNotEqual(HEBREW_NFC, HEBREW)
        self.assertEqual(NINO, unicodedata.normalize("NFC", NINO_DECOMPOSED))
        self.assertEqual(5, len(NINO_DECOMPOSED))
        with open(os.path.abspath(__file__), "rb") as f:
            self.assertTrue(f.read().isascii(), "the fixtures are escapes: this file is ASCII")

    def test_a_title_outside_ascii_stays_whole_when_the_hook_holds_only_its_pieces(self):
        for label, title, hook, _ in WHOLE:
            with self.subTest(label):
                self.kept(title, hook)

    def test_a_title_outside_ascii_stays_whole_against_a_latin_hook(self):
        for title in (PRIVET, "\u65e5\u672c\u8a9e", "\u65e5\u672c", NINO, "\u00f1", NINO_DECOMPOSED):
            with self.subTest(ascii(title)):
                self.kept(title, " - hook")

    def test_a_title_found_only_across_the_name_and_the_hook_stays(self):
        stem, tail = "\u043f\u0440\u0438", "\u0432\u0435\u0442"  # the title is the name, a space, the hook's first word
        self.kept("%s.md %s" % (stem, tail), " %s rest" % tail, name=stem + ".md")

    def test_a_title_that_is_not_utf8_stays_whole(self):
        code, out, now = self.trim(b"- [caf\xe9](kid.md) - a caf\xe9 here")
        self.assertEqual(0, code, out)
        self.assertEqual(b"- kid.md (caf\xe9) - a caf\xe9 here\n", now)
        self.assertIn("1 lines rewritten (1 keep their title)", out)

    def test_a_bad_byte_in_the_hook_is_never_read_as_a_title_character(self):
        code, out, now = self.trim(b"- [\xef\xbf\xbd](kid.md) - a bad \xff byte")  # the title is U+FFFD, well formed
        self.assertEqual(0, code, out)
        self.assertEqual(b"- kid.md (\xef\xbf\xbd) - a bad \xff byte\n", now)
        self.assertIn("1 lines rewritten (1 keep their title)", out)

    def test_a_title_that_folds_to_ascii_stays_whole_and_is_not_split_into_words(self):
        two_words = "ma%s fu%s" % (SHARP_S, SHARP_S)  # folds to "mass fuss", and both words are in the hook
        for title, hook in ((SHARP_S, " - hook"), (FI_LIGATURE, " - hook"), (two_words, " - mass, then fuss")):
            with self.subTest(ascii(title)):
                self.kept(title, hook)

    def test_a_bad_byte_cutting_a_word_of_the_hook_is_not_skipped_over(self):
        word = "\u043f\u0440\u0438\u0432\u0435\u0442"
        hook = b" - " + word[:3].encode("utf-8") + b"\xff" + word[3:].encode("utf-8")  # one word, cut in two
        code, out, now = self.trim(("- [%s](kid.md)" % word).encode("utf-8") + hook)
        self.assertEqual(0, code, out)
        self.assertEqual(("- kid.md (%s)" % word).encode("utf-8") + hook + b"\n", now)
        self.assertIn("1 lines rewritten (1 keep their title)", out)

    def test_the_report_counts_the_title_that_stays_not_the_shape_of_the_line(self):
        for title in (CYRILLIC, "Zebra"):
            with self.subTest(ascii(title)):
                self.kept(title, " - hook", name="a.md.md")  # a name that holds .md twice
                self.dropped(title, " (%s) later" % title.lower())  # a hook that opens with a parenthesis


class TrimIndexNonAsciiWholeTitleDroppedTest(LineBase):
    def test_each_title_is_dropped_when_the_hook_holds_the_whole_title(self):
        for label, title, _, _ in WHOLE:
            with self.subTest(label):
                self.dropped(title, " - see %s there" % title)

    def test_dropped_when_the_file_name_holds_the_whole_title(self):
        self.dropped(PRIVET, " - hook", name=PRIVET.lower() + ".md")

    def test_dropped_in_another_case_where_the_script_has_case(self):
        cases = ((CYRILLIC, " - " + CYRILLIC.upper()), (MIXED, " - " + MIXED.upper() + " here"),
                 (PRIVET, " - " + PRIVET.upper() + " here"), (NINO, " - the NI\u00d1O plays"),
                 (NINO, " - a ni\u00f1o"), ("Stra\u00dfe", " - die STRASSE"))  # the last is folding, not lowering
        for title, hook in cases:
            with self.subTest(ascii(title + hook)):
                self.dropped(title, hook)

    def test_dropped_when_composed_against_decomposed_and_the_reverse(self):
        self.dropped(NINO, " - a %s plays" % NINO_DECOMPOSED.lower())
        self.dropped(NINO_DECOMPOSED, " - a %s plays" % NINO.lower())


class TrimIndexNormalizationPinnedTest(LineBase):
    def test_the_title_side_is_normalized(self):
        self.dropped(NINO_DECOMPOSED, " - the %s plays" % NINO)
        self.dropped(HEBREW, " - %s" % HEBREW_NFC)

    def test_the_hook_side_is_normalized(self):
        self.dropped(NINO, " - the %s plays" % NINO_DECOMPOSED)
        self.dropped(HEBREW_NFC, " - %s" % HEBREW)

    def test_the_compatibility_forms_are_not_folded_into_the_title(self):
        for title, hook in ((FULL_WIDTH_ABC, " - abc"), (CIRCLED_ONE_SQUARE_M2, " - 1 m2")):
            with self.subTest(ascii(title)):
                self.kept(title, hook)

    def test_a_title_that_stays_keeps_its_own_bytes_not_the_normalized_ones(self):
        self.kept(NINO_DECOMPOSED, " - a nino plays")
        self.kept(HEBREW, HEBREW_HOOK)

    def test_runs_of_white_space_read_as_one_space(self):
        first, second = CYRILLIC.lower().split(" ")
        self.dropped(CYRILLIC.replace(" ", "  "), " - %s %s here" % (first, second))
        self.dropped(CYRILLIC, " - %s\t %s here" % (first, second))
        self.dropped(CYRILLIC, " - %s\u00a0%s here" % (first, second))


class TrimIndexRootIndexRefusedTest(Base):
    """No real root is written. The root is simulated in process: os.path.dirname answers the index folder for the
    index folder, which is what it answers for a drive root, a share root and '/' (the last case reads those three)."""

    def at_a_root(self, *args):
        mod = load()
        real = mod.os.path.dirname
        here = os.path.normcase(os.path.abspath(self.folder))

        def parentless(p):
            return p if os.path.normcase(os.path.abspath(p)) == here else real(p)

        said = io.StringIO()
        with mock.patch.object(mod.os.path, "dirname", side_effect=parentless):
            with contextlib.redirect_stdout(said):
                code = mod.main([self.index, "--apply"] + list(args))
        return code, said.getvalue()

    def test_an_index_at_a_root_without_bak_dir_is_refused_and_unchanged(self):
        self.put(INDEX)
        code, out = self.at_a_root()
        self.assertEqual(1, code, out)
        self.assertIn("refused, index unchanged", out)
        self.assertIn("--bak-dir", out)
        self.assertEqual(INDEX, self.read())
        self.assertEqual(["MEMORY.md"], os.listdir(self.folder), "no backup folder in the index folder, no temp file")

    def test_an_index_at_a_root_with_bak_dir_is_trimmed(self):
        self.put(INDEX)
        code, out = self.at_a_root("--bak-dir", self.baks)
        self.assertEqual(0, code, out)
        self.assertEqual(INDEX_TRIMMED, self.read())
        self.assertEqual(1, len(self.baks_listed()))
        self.assertEqual(["MEMORY.md"], os.listdir(self.folder))

    def test_a_root_is_its_own_parent_in_both_path_flavours(self):
        for flavour, index in ((ntpath, "C:\\MEMORY.md"), (ntpath, "\\\\host\\share\\MEMORY.md"),
                               (posixpath, "/MEMORY.md")):
            folder = flavour.dirname(index)
            self.assertEqual(folder, flavour.dirname(folder), index)


class TrimIndexAsciiRuleUnchangedTest(LineBase):
    """The bytes asserted here are the bytes the script gave before the whole-title rule."""

    def test_the_earlier_index_gives_the_same_bytes(self):
        self.put(INDEX)
        code, out = self.run_it("--apply", "--bak-dir", self.baks)
        self.assertEqual(0, code, out)
        self.assertEqual(INDEX_TRIMMED, self.read())
        self.assertIn("4 lines rewritten (1 keep their title)", out)

    def test_words_under_three_characters_are_ignored(self):
        self.dropped("of it", " - hook")  # two-letter words are no words
        self.kept("the zq", " - hook")  # "the" has three characters and is in neither the name nor the hook
        self.dropped("kid 12", " - decision")  # "12" is under the floor, "kid" is in the name

    def test_an_ascii_title_is_read_word_by_word_in_any_order_and_case(self):
        self.dropped("GATES stripes", " - hook about gates", name="stripes.md")
        self.dropped("Stripes of gates", " - gates, then stripes")  # the whole title is nowhere, each word is
        self.kept("Zebra stripes", " - hook about gates", name="stripes.md")


class TrimIndexAsciiRuleLowersTest(LineBase):
    """An ASCII title is lowered, not case folded: a search for its words still finds the line."""

    def test_strasse_against_a_hook_written_with_the_sharp_s_stays(self):
        self.kept("strasse", " - die Stra%se" % SHARP_S)

    def test_fish_against_a_hook_written_with_the_fi_ligature_stays(self):
        self.kept("fish", " - a %ssh" % FI_LIGATURE)


class TrimIndexBackupOutsideIndexFolderTest(Base):
    def test_the_default_backup_folder_is_beside_the_index_folder_and_the_index_folder_gets_none(self):
        self.put(INDEX)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        beside = os.path.join(self.tmp, "memory-bak")  # the parent of the folder that holds the index
        self.assertEqual(1, len(os.listdir(beside)))
        self.assertIn(beside.replace("\\", "/"), out.replace("\\", "/"))
        self.assertEqual(["MEMORY.md"], os.listdir(self.folder), "nothing is created inside the index folder")


class TrimIndexBackupInsideIndexRefusedTest(Base):
    def test_an_index_folder_named_like_the_default_backup_folder_is_refused_and_unchanged(self):
        self.folder = os.path.join(self.tmp, "memory-bak")  # the default backup folder would be the index folder
        os.makedirs(self.folder)
        self.index = os.path.join(self.folder, "MEMORY.md")
        self.put(INDEX)
        code, out = self.run_it("--apply")
        self.assertEqual(1, code, out)
        self.assertIn("refused, index unchanged", out)
        self.assertIn("--bak-dir", out)
        self.assertEqual(INDEX, self.read())
        self.assertEqual(["MEMORY.md"], os.listdir(self.folder), "no backup and no temp file in the index folder")
        self.assertEqual(["impl", "memory-bak"], sorted(os.listdir(self.tmp)), "no folder was made")  # impl: setUp
        code, out = self.run_it("--apply", "--bak-dir", self.baks)  # a folder of its own is fine
        self.assertEqual(0, code, out)
        self.assertEqual(INDEX_TRIMMED, self.read())


class TrimIndexMixedEndingsTest(Base):
    def run_bytes(self, raw):
        with open(self.index, "wb") as f:
            f.write(raw)
        code, out = self.run_it("--apply", "--bak-dir", self.baks)
        with open(self.index, "rb") as f:
            return code, out, f.read()

    def test_an_lf_index_with_one_crlf_line_keeps_each_line_ending(self):
        raw = b"# Index\n- [A first](a.md) - first\r\n- [B second](b.md) - second\n- [C third](c.md) - third\n"
        code, out, now = self.run_bytes(raw)
        self.assertEqual(0, code, out)
        self.assertEqual(b"# Index\n- a.md - first\r\n- b.md - second\n- c.md - third\n", now)
        self.assertIn("3 lines rewritten", out)

    def test_a_crlf_index_ending_in_a_lone_lf_keeps_that_byte(self):
        raw = b"# Index\r\n- [A first](a.md) - first\r\n- [B second](b.md) - second\n"
        code, out, now = self.run_bytes(raw)
        self.assertEqual(0, code, out)
        self.assertEqual(b"# Index\r\n- a.md - first\r\n- b.md - second\n", now)

    def test_a_crlf_index_without_a_final_ending_stays_without_one(self):
        code, out, now = self.run_bytes(b"- [A first](a.md) - first\r\n- [B second](b.md) - second")
        self.assertEqual(0, code, out)
        self.assertEqual(b"- a.md - first\r\n- b.md - second", now)


class TrimIndexBomAndBadBackupDirTest(Base):
    def test_a_bom_at_the_head_is_kept_and_the_first_entry_is_trimmed(self):
        with open(self.index, "wb") as f:
            f.write(b"\xef\xbb\xbf- [A first](a.md) - first\n- [B second](b.md) - second\n")
        code, out = self.run_it("--apply", "--bak-dir", self.baks)
        self.assertEqual(0, code, out)
        with open(self.index, "rb") as f:
            self.assertEqual(b"\xef\xbb\xbf- a.md - first\n- b.md - second\n", f.read())
        self.assertIn("2 lines rewritten", out)

    def test_a_backup_folder_that_is_a_file_is_a_refusal_not_a_traceback(self):
        self.put(INDEX)
        with open(self.baks, "wb") as f:
            f.write(b"i am a file\n")
        code, out = self.run_it("--apply", "--bak-dir", self.baks)
        self.assertEqual(1, code, out)
        self.assertNotIn("Traceback", out)
        self.assertIn("refused, index unchanged", out)
        self.assertEqual(INDEX, self.read())
        self.assertEqual(["MEMORY.md"], os.listdir(self.folder), "no temp file is left")
        with open(self.baks, "rb") as f:
            self.assertEqual(b"i am a file\n", f.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
