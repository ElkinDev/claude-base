"""Tests for `claude/tools/xmlframe.py`, the compact frame over one uiautomator dump.

    python test-xmlframe.py

No device, no adb, no network. The three dumps under `fixtures/xmlframe/` are synthetic,
written by `make-xmlframe-fixtures.py` from a tree spelled out in that script, and every
expected frame below was derived by hand from those trees before the filter was written:
the actionable nodes in document order, sorted by the top edge and then the left edge, the
tap centre as the integer midpoint of the bounds, and the label trimmed by the row grammar.
That is what makes case 1 worth anything. A frame compared against a frame the same program
produced proves only that the program is deterministic.

The cases:

  1  the whole frame, byte for byte, for the three screens
  2  completeness: every clickable node reaches the frame, and no row invents a target
  3  the state rules: a disabled control is flagged and never silently dropped, the
     selected option with no click target is state and never numbered
  4  the scrollable container is named once, with the size of the subtree it hides
  5  the package prefix: resolved from the dump by default, overridable, never hard coded
  6  text and json carry the same content
  7  the row grammar: the label trim, the row width, the frame against the dump it replaces
  8  failure is loud: a broken dump and a missing dump exit non-zero and say why
"""
import json
import os
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "xmlframe")
XMLFRAME = os.path.join(os.path.dirname(HERE), "xmlframe.py")
SCREENS = ("home", "form", "ledger")

# Case 1. Derived by hand from the trees in make-xmlframe-fixtures.py, not from a run.
#
# home: the settings button, the scrollable list and its three rows, the embedded control
# from another package, the selected tab published as state, the two tabs that are taps.
# The third row's name is 36 characters and the grammar trims it to 28. The status bar
# clock and the navigation bar background are chrome and never appear.
EXPECTED = {
    "home": [
        "frame com.example.app 1080x2400 8 actions",
        "1 Settings #btn_settings @1010,155",
        "2 Groceries #list_items ~scroll(9) @540,1110",
        "3 Groceries @540,330",
        "4 Transport @540,510",
        "5 Rent and building maintena.. @540,690",
        "6 Zoom in #com.example.plugin:zoom_in @1010,945",
        "= Home *sel @180,2300",
        "7 Ledger @540,2300",
        "8 Profile @900,2300",
        "read Overview | 12.50 | 8.00 | 450.00",
    ],
    # form: the segmented control whose left option holds no click target, the two fields
    # (one labelled by its own text, one by the label beside it), the checked row, and the
    # disabled Save flagged rather than dropped.
    "form": [
        "frame com.example.app 1080x2400 6 actions",
        "= Expense *sel @290,300",
        "1 Income @790,300",
        "2 12.00 #field_amount _type @540,490",
        "3 Note #field_note _type @540,770",
        "4 Repeat monthly #row_repeat *on @540,960",
        "5 Save #btn_save !off @290,1160",
        "6 Cancel #btn_cancel @790,1160",
        "read New entry",
    ],
    # ledger: the drawing surface uiautomator marks neither clickable nor scrollable, over
    # a scrollable list of four rows, and the amounts kept on the read line.
    "ledger": [
        "frame com.example.app 1080x2400 7 actions",
        "1 chart_canvas #chart_canvas ~draw @540,570",
        "2 Coffee #list_rows ~scroll(12) @540,1570",
        "3 Coffee @540,1050",
        "4 Books @540,1230",
        "5 Fuel @540,1410",
        "6 Rent @540,1590",
        "7 Export #btn_export @540,2300",
        "read Ledger | 3.20 | 24.90 | 60.00 | 450.00",
    ],
}

# Case 4. The subtree the scrollable container stands in for: 3 rows of 2 texts, then 4.
SCROLL = {"home": ("list_items", 9), "ledger": ("list_rows", 12)}

# Case 5. Every resource-id in the dumps carries one of these prefixes; the majority one is
# what the tool must resolve with no option given.
MAJORITY_PREFIX = "com.example.app"


def dump(screen):
    return os.path.join(FIXTURES, "%s.xml" % screen)


def run(screen, *args):
    return subprocess.run(
        [sys.executable, XMLFRAME] + list(args) + [dump(screen)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )


def frame(screen, *args):
    done = run(screen, *args)
    if done.returncode != 0:
        raise AssertionError("exit %d: %s" % (done.returncode, done.stderr.decode("utf-8", "replace")))
    return done.stdout.decode("utf-8").replace("\r\n", "\n")


def rows(text):
    """The action and state lines, without the header and without the read line."""
    return [ln for ln in text.split("\n")[1:] if ln and not ln.startswith("read ")]


def bounds_of(node):
    raw = node.get("bounds", "")
    body = raw.replace("[", " ").replace("]", " ").replace(",", " ").split()
    return tuple(int(v) for v in body) if len(body) == 4 else None


def centre_of(node):
    b = bounds_of(node)
    return None if b is None else ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)


def nodes(screen):
    return list(ET.parse(dump(screen)).getroot().iter("node"))


def source(screen):
    with open(dump(screen), encoding="utf-8") as fh:
        return fh.read()


class WholeFrame(unittest.TestCase):
    """Case 1. The frame the hand derivation says the filter owes for each screen."""

    def test_text_frame_is_the_hand_derived_frame(self):
        for screen in SCREENS:
            with self.subTest(screen=screen):
                done = run(screen)
                self.assertEqual(0, done.returncode, done.stderr.decode("utf-8", "replace"))
                self.assertEqual(b"", done.stderr)
                self.assertEqual(
                    "\n".join(EXPECTED[screen]) + "\n",
                    done.stdout.decode("utf-8").replace("\r\n", "\n"),
                )

    def test_the_numbering_has_no_hole_and_no_cap(self):
        for screen in SCREENS:
            with self.subTest(screen=screen):
                numbered = [ln for ln in rows(frame(screen)) if ln[:1].isdigit()]
                self.assertEqual(
                    list(range(1, len(numbered) + 1)),
                    [int(ln.split(" ", 1)[0]) for ln in numbered],
                )
                header = frame(screen).split("\n")[0]
                self.assertEqual("%d actions" % len(numbered), header.split(" ", 3)[3])


class Completeness(unittest.TestCase):
    """Case 2. Nothing an agent could tap is missing, and nothing offered is invented.

    Both directions are derived from the XML here, independently of the filter: the set of
    tap centres the dump publishes against the set of centres the frame prints.
    """

    def app_clickables(self, screen):
        out = {}
        for node in nodes(screen):
            if node.get("clickable") != "true":
                continue
            if node.get("package", "").startswith("com.android."):
                continue
            if node.get("resource-id", "").startswith("android:id/"):
                continue
            out[centre_of(node)] = node.get("resource-id", "")
        return out

    def printed_centres(self, screen):
        out = set()
        for line in rows(frame(screen)):
            at = line.rsplit(" @", 1)[1]
            x, y = at.split(",")
            out.add((int(x), int(y)))
        return out

    def test_every_clickable_node_reaches_the_frame(self):
        for screen in SCREENS:
            printed = self.printed_centres(screen)
            for centre, rid in self.app_clickables(screen).items():
                with self.subTest(screen=screen, rid=rid, centre=centre):
                    self.assertIn(centre, printed)

    def test_no_row_invents_a_target(self):
        for screen in SCREENS:
            available = {centre_of(node) for node in nodes(screen)}
            for centre in self.printed_centres(screen):
                with self.subTest(screen=screen, centre=centre):
                    self.assertIn(centre, available)

    def test_system_chrome_is_dropped(self):
        text = frame("home")
        self.assertIn("com.android.systemui:id/clock", source("home"))
        self.assertNotIn("systemui", text)
        self.assertNotIn("9:41", text)
        self.assertNotIn("navigationBarBackground", text)

    def test_a_row_is_never_offered_twice(self):
        for screen in SCREENS:
            with self.subTest(screen=screen):
                printed = [ln.rsplit(" @", 1)[1] for ln in rows(frame(screen))]
                self.assertEqual(len(printed), len(set(printed)))


class StateRules(unittest.TestCase):
    """Case 3. Disabled is flagged, and the option already on is state, never a tap."""

    def test_the_disabled_control_is_flagged_and_its_enabled_twin_is_not(self):
        lines = rows(frame("form"))
        save = [ln for ln in lines if "#btn_save" in ln]
        cancel = [ln for ln in lines if "#btn_cancel" in ln]
        self.assertEqual(["5 Save #btn_save !off @290,1160"], save)
        self.assertEqual(["6 Cancel #btn_cancel @790,1160"], cancel)

    def test_the_selected_option_is_state_and_never_numbered(self):
        for screen, line in (("form", "= Expense *sel @290,300"), ("home", "= Home *sel @180,2300")):
            with self.subTest(screen=screen):
                lines = rows(frame(screen))
                self.assertIn(line, lines)
                label = line.split(" ")[1]
                numbered = [ln for ln in lines if ln[:1].isdigit() and (" %s " % label) in ln]
                self.assertEqual([], numbered)

    def test_every_state_row_carries_the_selected_flag(self):
        for screen in SCREENS:
            for line in rows(frame(screen)):
                if line.startswith("= "):
                    with self.subTest(screen=screen, row=line):
                        self.assertIn(" *sel", line)

    def test_the_checked_row_and_the_text_fields_are_marked(self):
        lines = rows(frame("form"))
        self.assertIn("4 Repeat monthly #row_repeat *on @540,960", lines)
        self.assertEqual(2, sum(1 for ln in lines if " _type" in ln))

    def test_the_drawing_surface_is_named(self):
        self.assertIn("1 chart_canvas #chart_canvas ~draw @540,570", rows(frame("ledger")))


class ScrollableContainer(unittest.TestCase):
    """Case 4. One row per scrollable container, carrying the size of the subtree it hides."""

    def test_named_once_with_its_child_count(self):
        for screen, (rid, count) in SCROLL.items():
            with self.subTest(screen=screen):
                marked = [ln for ln in rows(frame(screen)) if "~scroll(" in ln]
                self.assertEqual(1, len(marked))
                self.assertIn("#%s ~scroll(%d)" % (rid, count), marked[0])

    def test_the_count_is_the_real_subtree(self):
        for screen, (rid, count) in SCROLL.items():
            with self.subTest(screen=screen):
                found = [n for n in nodes(screen) if n.get("resource-id", "").endswith(":id/" + rid)]
                self.assertEqual(1, len(found))
                self.assertEqual(count, len(list(found[0].iter("node"))) - 1)

    def test_the_container_replaces_no_row_of_its_own(self):
        """The rows inside stay addressable; the container is an extra handle, not a lid."""
        lines = rows(frame("ledger"))
        self.assertEqual(4, sum(1 for ln in lines if ln.split(" ", 1)[1].split(" ")[0]
                                in ("Coffee", "Books", "Fuel", "Rent") and "~scroll(" not in ln))


class PackagePrefix(unittest.TestCase):
    """Case 5. The prefix stripped from a resource id is read from the dump, never assumed."""

    def test_the_majority_prefix_is_resolved_from_the_dump(self):
        counts = {}
        for node in nodes("home"):
            rid = node.get("resource-id", "")
            if ":" in rid:
                counts[rid.split(":", 1)[0]] = counts.get(rid.split(":", 1)[0], 0) + 1
        self.assertEqual(MAJORITY_PREFIX, max(counts, key=counts.get))
        self.assertGreater(counts[MAJORITY_PREFIX], counts["com.example.plugin"])
        self.assertIn("1 Settings #btn_settings @1010,155", rows(frame("home")))

    def test_an_id_from_another_package_keeps_its_package(self):
        self.assertIn("6 Zoom in #com.example.plugin:zoom_in @1010,945", rows(frame("home")))

    def test_the_option_overrides_the_resolved_prefix(self):
        lines = rows(frame("home", "--package", "com.example.plugin"))
        self.assertIn("6 Zoom in #zoom_in @1010,945", lines)
        self.assertIn("1 Settings #com.example.app:btn_settings @1010,155", lines)
        self.assertIn("2 Groceries #com.example.app:list_items ~scroll(9) @540,1110", lines)

    def test_naming_the_resolved_prefix_changes_nothing(self):
        self.assertEqual(frame("home"), frame("home", "--package", MAJORITY_PREFIX))


class TextAndJsonAgree(unittest.TestCase):
    """Case 6. The two formats are the same frame, one for a model and one for a caller."""

    def test_counts_labels_and_coordinates_match(self):
        for screen in SCREENS:
            with self.subTest(screen=screen):
                doc = json.loads(frame(screen, "--format", "json"))
                lines = rows(frame(screen))
                self.assertEqual(MAJORITY_PREFIX, doc["package"])
                self.assertEqual([1080, 2400], [doc["w"], doc["h"]])
                self.assertEqual(sum(1 for ln in lines if ln[:1].isdigit()), len(doc["actions"]))
                self.assertEqual(sum(1 for ln in lines if ln.startswith("= ")), len(doc["state"]))
                for item in doc["actions"] + doc["state"]:
                    self.assertIn("@%d,%d" % (item["x"], item["y"]), "\n".join(lines))

    def test_the_read_line_is_the_read_list(self):
        for screen in SCREENS:
            with self.subTest(screen=screen):
                doc = json.loads(frame(screen, "--format", "json"))
                read = [ln for ln in frame(screen).split("\n") if ln.startswith("read ")]
                self.assertEqual(1, len(read))
                self.assertEqual(read[0][5:], " | ".join(doc["read"]))

    def test_the_json_carries_the_flags_the_text_carries(self):
        doc = json.loads(frame("form", "--format", "json"))
        by_id = {item.get("id"): item for item in doc["actions"]}
        self.assertIs(False, by_id["btn_save"]["enabled"])
        self.assertNotIn("enabled", by_id["btn_cancel"])
        self.assertIs(True, by_id["row_repeat"]["checked"])
        self.assertEqual("12.00", by_id["field_amount"]["value"])
        self.assertIs(True, by_id["field_amount"]["type"])
        self.assertEqual([{"label": "Expense", "x": 290, "y": 300, "selected": True}], doc["state"])


class RowGrammar(unittest.TestCase):
    """Case 7. The row stays one menu line, and the frame stays a fraction of the dump."""

    def test_no_row_is_wider_than_the_grammar(self):
        for screen in SCREENS:
            for line in rows(frame(screen)):
                with self.subTest(screen=screen, row=line):
                    self.assertLessEqual(len(line), 60)

    def test_a_long_label_is_trimmed_to_the_cap(self):
        raw = "Rent and building maintenance charge"
        self.assertIn(raw, source("home"))
        line = [ln for ln in rows(frame("home")) if ln.startswith("5 ")][0]
        self.assertEqual("5 Rent and building maintena.. @540,690", line)
        self.assertEqual(28, len("Rent and building maintena.."))

    def test_the_resource_id_is_never_trimmed(self):
        """The id is the only stable handle a later step can address, so the label yields."""
        line = [ln for ln in rows(frame("home", "--package", "com.example.plugin"))
                if ln.startswith("2 ")][0]
        self.assertIn("#com.example.app:list_items", line)

    def test_the_frame_is_a_small_fraction_of_the_dump(self):
        for screen in SCREENS:
            with self.subTest(screen=screen):
                raw = os.path.getsize(dump(screen))
                produced = len(run(screen).stdout)
                self.assertLess(produced / raw, 0.08)
                self.assertGreater(produced, 0)


class FailureIsLoud(unittest.TestCase):
    """Case 8. Nothing here may pass in silence: a caller writes an error file on non-zero."""

    def broken(self, tmpdir, body):
        path = os.path.join(tmpdir, "broken.xml")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        return path

    def test_a_truncated_dump_exits_two_and_says_why(self):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="xmlframe-") as tmp:
            path = self.broken(tmp, "<hierarchy><node truncated")
            done = subprocess.run([sys.executable, XMLFRAME, path],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            self.assertEqual(2, done.returncode)
            self.assertEqual(b"", done.stdout)
            self.assertIn("xmlframe:", done.stderr.decode("utf-8", "replace"))

    def test_a_missing_dump_exits_two_and_names_the_path(self):
        done = subprocess.run([sys.executable, XMLFRAME, os.path.join(FIXTURES, "absent.xml")],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(2, done.returncode)
        self.assertEqual(b"", done.stdout)
        self.assertIn("absent.xml", done.stderr.decode("utf-8", "replace"))

    def test_an_empty_hierarchy_is_an_empty_frame_not_a_crash(self):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="xmlframe-") as tmp:
            path = self.broken(tmp, '<?xml version="1.0"?><hierarchy rotation="0"></hierarchy>')
            done = subprocess.run([sys.executable, XMLFRAME, path],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            self.assertEqual(0, done.returncode)
            self.assertEqual("frame  0x0 0 actions\n",
                             done.stdout.decode("utf-8").replace("\r\n", "\n"))


class FixturesStaySmall(unittest.TestCase):
    """The dumps are carried in the kit, so they stay small enough to read in a review."""

    def test_every_fixture_is_under_twelve_kilobytes(self):
        for screen in SCREENS:
            with self.subTest(screen=screen):
                self.assertLess(os.path.getsize(dump(screen)), 12 * 1024)

    def test_every_fixture_is_ascii(self):
        for screen in SCREENS:
            with self.subTest(screen=screen):
                with open(dump(screen), "rb") as fh:
                    body = fh.read()
                self.assertEqual(body, body.decode("ascii").encode("ascii"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
