"""Tests for scripts/quality.py, the three quality numbers of a ledger window.

Every source is a fixture written to a temp dir: gate exit files (green, ktlint red,
tests red, a sentinel-only run, a killed run, and one older file outside the window),
a landings file with the four REVIEW shapes seen so far plus a hook row, a dash
prefixed row, a row whose verdict sits behind a lane decimal, a row that carries the
word REVIEW only after its colon, and a row outside the window; a defects file with
rows inside and outside the window; and a features list carrying one `fix(` subject.
The live ledger folder is never written; one smoke case reads ledger/defects.md read-only
for its row shape and skips when the file is absent.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from importlib.machinery import SourceFileLoader

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(SCRIPTS_DIR, "quality.py")
# the kit's own lane-state tool, never whatever copy this machine has installed
os.environ["QUALITY_LANE_STATE"] = os.path.join(os.path.dirname(SCRIPTS_DIR), "claude", "tools", "lane-state.py")
LEDGER_NEW = os.path.join(SCRIPTS_DIR, "ledger-day.py")
# Read by the one smoke case below, never written and never counted.
LIVE_DEFECTS = os.path.join(os.path.dirname(SCRIPTS_DIR), "ledger", "defects.md")

_spec = importlib.util.spec_from_file_location("quality", SCRIPT)
quality = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(quality)


NOW = datetime(2026, 9, 6, 10, 0, 0)
START = datetime(2026, 9, 5, 18, 0, 0)
END = datetime(2026, 9, 6, 10, 0, 0)

GREEN = """RUN=vpk-g2
GATE_EXIT=97
TIP_START=618c91db0a1f0d2c3b4a5968770e1d2c3b4a5968
GATE_START=1788652915
LOCK_WAIT_SECS=590
PHASE_compile_EXIT=0 secs=72
PHASE_ktlint_EXIT=0 secs=93
PHASE_detekt_EXIT=0 secs=101
PHASE_test_app_testStandardDebugUnitTest_EXIT=0 secs=1804
FAILED_TESTS=
PHASE_FAILURES=0
TIP_END=618c91db0a1f0d2c3b4a5968770e1d2c3b4a5968
TIP_MOVED=0
GATE_EXIT=0
"""

KTLINT_RED = """RUN=rdr-g1
GATE_EXIT=97
TIP_START=05060afa780998f0354bcbe0c42e033eec533864
GATE_START=1788660284
LOCK_WAIT_SECS=246
PHASE_compile_EXIT=0 secs=301
PHASE_ktlint_EXIT=1 secs=332
PHASE_detekt_EXIT=1 secs=341
FAILED_TESTS=
PHASE_FAILURES=2
TIP_END=05060afa780998f0354bcbe0c42e033eec533864
TIP_MOVED=0
GATE_EXIT=1
"""

TESTS_RED = """RUN=vpk-g1
GATE_EXIT=97
TIP_START=6f536980a1b2c3d4e5f60718293a4b5c6d7e8f90
GATE_START=1788658000
LOCK_WAIT_SECS=0
PHASE_compile_EXIT=0 secs=64
PHASE_ktlint_EXIT=0 secs=71
PHASE_detekt_EXIT=0 secs=83
PHASE_test__feature_voice_testDebugUnitTest_EXIT=1 secs=153
FAILED_TESTS=VoiceCaptureTest
PHASE_FAILURES=1
TIP_END=6f536980a1b2c3d4e5f60718293a4b5c6d7e8f90
TIP_MOVED=0
GATE_EXIT=1
"""

RUNNING = """RUN=vpk-g3-merge
GATE_EXIT=97
TIP_START=52433f9d188671cf86709e0a04a095a2c8348b1a
GATE_START=1788705977
LOCK_WAIT_SECS=0
PHASE_compile_EXIT=0 secs=62
"""

KILLED = """RUN=pwp-g5
GATE_EXIT=97
TIP_START=5cba841c0d2e3f405162738495a6b7c8d9e0f102
GATE_START=1788650000
GATE_EXIT=143
"""

OLD_GREEN = """RUN=cmo-g3
GATE_EXIT=97
TIP_START=e2a5869a0b1c2d3e4f5061728394a5b6c7d8e9f0
GATE_START=1788600000
LOCK_WAIT_SECS=635
PHASE_compile_EXIT=0 secs=90
GATE_EXIT=0
"""

LANDINGS = """# Landings

| 2026-09-05 19:00 SubagentStop hook row REVIEW BLOCK never counted, it is a hook row |
2026-09-05 20:30 REVIEW BLOCK, lane 58.3 paywall promises on f896ccad: one MAJOR found.
2026-09-05 21:45 REVIEW CLEAR (delta), lane 58.3 on 9dbfb8d49, same reviewer, 8 tool uses.
2026-09-05 22:10 REVIEW E CLEAR on 37919cbbd: the strings diff adds one plural key.
2026-09-06 09:15 REVIEW CLEAR on 2470951c8: the three merges are 0 bytes under git show.
2026-09-04 11:00 REVIEW CLEAR on aaaaaaaaa: outside the window, never counted.
2026-09-05 23:00 GATE vpk-g2 green on 618c91db: no review word in the head, not counted.
2026-09-05 23:30 GATE alm-g2 green on 20d3215: the word REVIEW arrives after the colon.
2026-09-06 09:30 REVIEW invqr (90.1) BLOCK, cited from the report, one MAJOR on the fold.
- 2026-09-06 09:40 REVIEW BATCH CLEAR on 4b1c2d3e4: the dash prefix is still a row.
"""

DEFECTS = """# Post-landing defects

One row per defect found after its lane landed. Row shape:
`YYYY-MM-DD HH:MM <lane or item> <where: bench|owner|review|ci> <one line, source file>`.

An owner item, a spec or a mockup is never a defect: item 58.11 owner opened a request
for a Premium notice, and spec 90.13 was rewritten after review, and neither is a row.

2026-09-05 20:06 f69-w3 bench VERDICT FAIL on the audio probe (lanes/s21u-session.md)
2026-09-06 09:20 item 41 owner the pack row reads the wrong label (drafts/decisions.md)
2026-09-01 08:00 item 12 ci an older defect outside the window (field-reports/old.md)
"""

FEATURES = [
    {"sha": "9dbfb8d49", "date": "2026-09-05 20:00:00 -0500",
     "subject": "fix(paywall): key the lapse branch on the plan"},
    {"sha": "2470951c8", "date": "2026-09-06 09:00:00 -0500",
     "subject": "merge: feature-ttl into main"},
]


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write(path, text, when=None):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    if when is not None:
        stamp = when.timestamp()
        os.utime(path, (stamp, stamp))
    return path


def table_row(when, window, landings, red="2/3 (66.7%)", cause="ktlint 1",
              per_landing="5/2 (2.50)", block="1/4 (25.0%)", unclassified="0",
              declared="2", fix="1/2 (50.0%)"):
    """One raw quality.md row, so a head case can be built without a full report."""
    return "| " + " | ".join([when, window, str(landings), red, cause, per_landing,
                              block, unclassified, declared, fix]) + " |"


def load_ledger_day_new():
    """Load ledger-day.py by path: its name carries a dash and an extension
    the import machinery does not know, so it needs an explicit source loader."""
    loader = SourceFileLoader("ledger_day_new", LEDGER_NEW)
    spec = importlib.util.spec_from_file_location("ledger_day_new", LEDGER_NEW,
                                                  loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QualityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="quality-test-")
        self.addCleanup(self._clean)
        self.gate_dir = os.path.join(self.tmp, "gates", "vpkgate")
        os.makedirs(self.gate_dir)
        for name, body, when in (
                ("vpk-g2.exit", GREEN, datetime(2026, 9, 5, 21, 8)),
                ("rdr-g1.exit", KTLINT_RED, datetime(2026, 9, 5, 21, 10)),
                ("vpk-g1.exit", TESTS_RED, datetime(2026, 9, 5, 20, 25)),
                ("vpk-g3-merge.exit", RUNNING, datetime(2026, 9, 6, 9, 47)),
                ("pwp-g5.exit", KILLED, datetime(2026, 9, 5, 18, 32)),
                ("cmo-g3.exit", OLD_GREEN, datetime(2026, 9, 4, 12, 0))):
            write(os.path.join(self.gate_dir, name), body, when)
        self.gate_dirs = [os.path.join(self.tmp, "gates")]
        self.landings = write(os.path.join(self.tmp, "landings.md"), LANDINGS)
        self.defects = write(os.path.join(self.tmp, "defects.md"), DEFECTS)

    def _clean(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compute(self, **kw):
        args = dict(start=START, end=END, landings_path=self.landings,
                    defects_path=self.defects, features=FEATURES,
                    gate_dirs=self.gate_dirs, now=NOW)
        args.update(kw)
        return quality.compute(**args)

    def cells(self, row):
        return [c.strip() for c in row.strip().strip("|").split("|")]

    # --- gates ------------------------------------------------------------
    def test_gate_classes(self):
        gates = self.compute()["gates"]
        self.assertEqual(gates["total"], 5)
        self.assertEqual(gates["green"], 1)
        self.assertEqual(gates["red"], 2)
        self.assertEqual(gates["aborted"], 2)
        self.assertEqual(gates["red_share"], round(2 / 3.0, 4))
        self.assertNotIn("unavailable", gates)

    def test_gate_causes(self):
        gates = self.compute()["gates"]
        self.assertEqual(gates["by_cause"], {"compile": 0, "ktlint": 1, "detekt": 0,
                                             "tests": 1, "other": 0})

    def test_gate_window_drops_the_older_file(self):
        rows = self.compute()["gates"]["rows"]
        self.assertEqual(len(rows), 5)
        self.assertEqual([r for r in rows if "cmo-g3" in r], [])
        self.assertEqual(len([r for r in rows if "rdr-g1" in r]), 1)

    def test_gates_per_landing(self):
        rep = self.compute()
        self.assertEqual(rep["landings"], 2)
        self.assertEqual(rep["gates"]["per_landing"], 2.5)

    def test_gates_unavailable_when_no_directory_exists(self):
        gates = self.compute(gate_dirs=[os.path.join(self.tmp, "not-here")])["gates"]
        self.assertIn("unavailable", gates)
        self.assertEqual(gates["total"], 0)
        self.assertIsNone(gates["red_share"])

    # --- review -----------------------------------------------------------
    def test_review_counts(self):
        review = self.compute()["review"]
        self.assertEqual(review["block"], 2)
        self.assertEqual(review["clear"], 4)
        self.assertEqual(review["block_share"], round(2 / 6.0, 4))

    def test_review_head_ends_at_a_sentence_period_not_at_a_lane_decimal(self):
        """MAJOR 1: `REVIEW invqr (90.1) BLOCK` is a block, not an unclassified row."""
        review = self.compute()["review"]
        blocks = [h for h in review["heads"] if "invqr (90.1)" in h]
        self.assertEqual(len(blocks), 1)
        self.assertIn("BLOCK", blocks[0])
        self.assertEqual([h for h in review["unclassified"] if "invqr" in h], [])

    def test_review_head_still_ends_at_the_first_colon(self):
        self.assertEqual(quality.head_of("REVIEW CLEAR on 2470951c8: BLOCK in prose"),
                         "REVIEW CLEAR on 2470951c8")

    def test_review_head_ends_at_a_period_before_a_space(self):
        self.assertEqual(quality.head_of("REVIEW CLEAR (delta) on 9dbfb8d4. BLOCK here"),
                         "REVIEW CLEAR (delta) on 9dbfb8d4")

    def test_review_row_with_a_dash_prefix_is_counted(self):
        review = self.compute()["review"]
        batch = [h for h in review["heads"] if "REVIEW BATCH CLEAR" in h]
        self.assertEqual(len(batch), 1)
        self.assertTrue(batch[0].startswith("- 2026-09-06 09:40"))

    def test_review_audit_heads(self):
        review = self.compute()["review"]
        self.assertEqual(len(review["heads"]), 6)
        self.assertEqual(review["heads"][0],
                         "2026-09-05 20:30 REVIEW BLOCK, lane 58.3 paywall promises on"
                         " f896ccad: one MAJOR")
        self.assertTrue(all(len(h) <= 80 for h in review["heads"]))
        self.assertEqual([h for h in review["heads"] if "hook row" in h], [])
        self.assertEqual([h for h in review["heads"] if "2026-09-04" in h], [])

    def test_review_row_without_a_verdict_in_its_head_is_listed_not_counted(self):
        review = self.compute()["review"]
        self.assertEqual(len(review["unclassified"]), 1)
        self.assertIn("alm-g2", review["unclassified"][0])
        self.assertTrue(all(len(h) <= 80 for h in review["unclassified"]))

    def test_review_unavailable_when_the_file_is_missing(self):
        review = self.compute(landings_path=os.path.join(self.tmp, "gone.md"))["review"]
        self.assertIn("unavailable", review)
        self.assertEqual(review["block"], 0)
        self.assertIsNone(review["block_share"])

    # --- defects ----------------------------------------------------------
    def test_declared_defects_in_the_window(self):
        defects = self.compute()["defects"]
        self.assertEqual(defects["declared"], 2)
        self.assertEqual(len(defects["rows"]), 2)
        self.assertIn("f69-w3 bench", defects["rows"][0])
        self.assertEqual([r for r in defects["rows"] if "2026-09-01" in r], [])

    def test_a_row_with_an_unrecorded_minute_counts_at_the_first_minute_of_its_ten(self):
        # row writers and most hands stamp "13:4x"; a digits-only pattern dropped such rows from the count
        path = os.path.join(self.tmp, "defects-x.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("2026-09-06 09:3x 91.1 bench the sheet saves an empty title\n"
                         "2026-09-05 17:5x 91.2 bench before the window\n"
                         "2026-09-06 10:0x 91.3 bench at the window's end\n")
        defects = quality.defect_numbers(START, END, path, [], 0)
        self.assertEqual(defects["declared"], 2)  # 09:30 and 10:00 are inside the window, 17:50 is not
        self.assertIn("91.1", defects["rows"][0])

    def test_fix_landings(self):
        defects = self.compute()["defects"]
        self.assertEqual(defects["fix_landings"], 1)
        self.assertEqual(defects["fix_share"], 0.5)
        self.assertEqual(defects["fix_subjects"],
                         ["9dbfb8d49 fix(paywall): key the lapse branch on the plan"])

    def test_defects_unavailable_when_the_file_is_missing(self):
        defects = self.compute(defects_path=os.path.join(self.tmp, "gone.md"))["defects"]
        self.assertIn("unavailable", defects)
        self.assertEqual(defects["declared"], 0)
        self.assertEqual(defects["fix_landings"], 1)

    def test_no_landing_leaves_the_ratios_open(self):
        rep = self.compute(features=[])
        self.assertEqual(rep["landings"], 0)
        self.assertIsNone(rep["gates"]["per_landing"])
        self.assertIsNone(rep["defects"]["fix_share"])

    def test_every_source_missing_never_raises(self):
        rep = quality.compute(START, END, os.path.join(self.tmp, "a.md"),
                              os.path.join(self.tmp, "b.md"), None,
                              gate_dirs=[os.path.join(self.tmp, "c")], now=NOW)
        for key in ("gates", "review", "defects"):
            self.assertIn("unavailable", rep[key])
        self.assertEqual(rep["landings"], 0)

    # --- the seeded ledger/defects.md -------------------------------------
    def test_the_seeded_defects_are_only_landed_failures(self):
        """MAJOR 2: an owner item, a spec or a mockup is never a defect.

        The fixture carries an owner item and a spec in a note above the rows, both
        named in prose and neither stamped. Only a stamped line is a defect, so the
        classifier has to leave those two out and count the three landed failures.

        The file read here is the seeded fixture, like every other source of this
        suite: reading the live ledger/defects.md made the case fail whenever the
        real register grew, which says nothing about the row shape under test."""
        text = read(self.defects)
        self.assertIn("item 58.11 owner opened", text)
        self.assertIn("spec 90.13 was rewritten", text)
        rows = [line for line in text.splitlines() if quality.ROW_RE.match(line)]
        self.assertEqual([r for r in rows if "owner opened" in r or "spec 90.13" in r], [],
                         "an owner item or a spec was counted as a defect")
        self.assertEqual(len(rows), 3)
        self.assertEqual(len([r for r in rows if "f69-w3 bench VERDICT FAIL" in r]), 1)
        self.assertEqual(len([r for r in rows if r.endswith(".md)")]), 3)
        self.assertIn("One row per defect found after its lane landed", text)

    @unittest.skipUnless(os.path.isfile(LIVE_DEFECTS), "no live defects register beside this checkout")
    def test_the_live_register_keeps_the_row_shape(self):
        """Smoke over the real ledger/defects.md, read only and never counted.

        The register is append-only and a row written in the wrong shape is invisible
        to the count, so nobody notices it went missing. The paragraph that opens the
        file is its documentation; from the first row down, every non-blank line has
        to read as a row. The number of rows is not asserted: the register grows."""
        lines = read(LIVE_DEFECTS).splitlines()
        first = next((i for i, line in enumerate(lines) if quality.ROW_RE.match(line)),
                     None)
        self.assertIsNotNone(first, "the register carries no row at all")
        # A "## " heading opens a prose section (a state note between rows); its lines are documentation
        # until the next row, and only a line outside such a section must be a row. A line that opens with a date
        # ends the section and must be a row, so a malformed row right after a note is still reported.
        malformed, prose = [], False
        for number, line in enumerate(lines[first:], first + 1):
            dated = line[2:] if line.startswith("- ") else line
            if quality.ROW_RE.match(line):
                prose = False
            elif dated[:4].isdigit() and dated[4:5] == "-":
                prose = False
                malformed.append("%d: %s" % (number, line))
            elif line.startswith("## "):
                prose = True
            elif line.strip() and not prose:
                malformed.append("%d: %s" % (number, line))
        self.assertEqual(malformed, [],
                         "a line below the first row is not in the row shape")

    # --- the quality.md table ---------------------------------------------
    def test_row_carries_every_column(self):
        rep = self.compute()
        cells = self.cells(quality.format_row(NOW, START, END, rep))
        self.assertEqual(len(cells), len(quality.QUALITY_COLUMNS))
        self.assertEqual(cells[0], "2026-09-06 10:00")
        self.assertEqual(cells[1], "2026-09-05 18:00 to 2026-09-06 10:00")
        self.assertEqual(cells[2], "2")
        self.assertEqual(cells[3], "2/3 (66.7%)")
        self.assertEqual(cells[4], "ktlint 1, tests 1")
        self.assertEqual(cells[5], "5/2 (2.50)")
        self.assertEqual(cells[6], "2/6 (33.3%)")
        self.assertEqual(cells[7], "1")
        self.assertEqual(cells[8], "2")
        self.assertEqual(cells[9], "1/2 (50.0%)")

    def test_the_window_cell_carries_full_dates(self):
        rep = self.compute()
        rows = quality.parse_rows(quality.format_row(NOW, START, END, rep))
        self.assertEqual(rows[0]["span"], (START, END))
        self.assertEqual(quality.coverage_hours(rows), 16.0)

    def test_gate_cell_keeps_the_files_when_the_window_had_no_landing(self):
        """MINOR 4: a run with no landing still contributes its gate files."""
        rep = self.compute(features=[])
        cells = self.cells(quality.format_row(NOW, START, END, rep))
        self.assertEqual(cells[2], "0")
        self.assertEqual(cells[5], "5/0 (-)")
        parsed = quality.parse_rows(quality.format_row(NOW, START, END, rep))[0]
        self.assertEqual((parsed["gate_files"], parsed["gate_landings"]), (5, 0))

    def test_head_sums_the_rows_of_the_table(self):
        path = os.path.join(self.tmp, "quality.md")
        rep = self.compute()
        older = datetime(2026, 8, 28, 18, 0)  # inside the seven days before
        quality.append_quality(path, older, START, END, rep)
        quality.append_quality(path, NOW, START, END, rep)
        text = read(path)
        self.assertIn(quality.HEAD_TITLE, text)
        rows = quality.parse_rows(text)
        self.assertEqual(len(rows), 2)
        joined = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("| Runs | 1 | 1 |", joined)
        self.assertIn("| Landings | 2 (3.0/day) | 2 (3.0/day) |", joined)
        self.assertIn("| Gates red / (red + green) | 2/3 (66.7%) | 2/3 (66.7%) |", joined)
        self.assertIn("| Gates per landing | 5/2 (2.50) | 5/2 (2.50) |", joined)
        self.assertIn("| Review block / (block + clear) | 2/6 (33.3%) | 2/6 (33.3%) |",
                      joined)
        self.assertIn("| Review unclassified | 1 | 1 |", joined)
        self.assertIn("| Defects declared | 2 (1.00/landing) | 2 (1.00/landing) |",
                      joined)
        self.assertIn("| Fix landings / landings | 1/2 (50.0%) | 1/2 (50.0%) |", joined)

    def test_head_prints_coverage_per_bucket(self):
        """MAJOR 3: counts cannot be compared across buckets of unequal coverage."""
        rep = self.compute()
        rows = quality.parse_rows("\n".join([
            quality.format_row(NOW, START, END, rep),
            quality.format_row(NOW - timedelta(days=8), datetime(2026, 8, 29, 2, 0),
                               datetime(2026, 8, 29, 18, 0), rep)]))
        joined = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("| Coverage | 0.7 days | 0.7 days |", joined)
        self.assertNotIn("Coverage differs", joined)

    def test_head_warns_when_the_buckets_measured_different_time(self):
        rep = self.compute()
        rows = quality.parse_rows("\n".join([
            quality.format_row(NOW, START, END, rep),
            quality.format_row(NOW - timedelta(days=8), datetime(2026, 8, 29, 14, 0),
                               datetime(2026, 8, 29, 18, 0), rep)]))
        joined = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("| Coverage | 0.7 days | 0.2 days |", joined)
        self.assertIn("Coverage differs by 75.0 percent; read the rates, not the counts.",
                      joined)

    def test_head_disclaimer_names_the_coverage_line(self):
        joined = "\n".join(quality.render_head([], NOW))
        self.assertIn("coverage", joined)

    def test_append_keeps_the_earlier_rows(self):
        path = os.path.join(self.tmp, "quality.md")
        rep = self.compute()
        quality.append_quality(path, NOW, START, END, rep)
        quality.append_quality(path, NOW.replace(hour=18), START, END, rep)
        rows = quality.parse_rows(read(path))
        self.assertEqual([r["time"].strftime("%H:%M") for r in rows], ["10:00", "18:00"])

    def test_head_ignores_rows_older_than_fourteen_days(self):
        rep = self.compute()
        path = os.path.join(self.tmp, "quality.md")
        quality.append_quality(path, datetime(2026, 8, 1, 8, 0), START, END, rep)
        quality.append_quality(path, NOW, START, END, rep)
        head = "\n".join(quality.render_head(
            quality.parse_rows(read(path)), NOW))
        self.assertIn("| Runs | 1 | 0 |", head)

    # --- coverage over overlapping runs -----------------------------------
    def test_coverage_unions_overlapping_windows(self):
        """MAJOR 8: a manual run beside the scheduled one measures the same hours."""
        rows = quality.parse_rows("\n".join([
            table_row("2026-09-06 08:00", "2026-09-05 08:00 to 2026-09-06 08:00", 10),
            table_row("2026-09-06 10:00", "2026-09-05 10:00 to 2026-09-06 10:00", 10)]))
        self.assertEqual(quality.coverage_hours(rows), 26.0)
        joined = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("| Coverage | 1.1 days, rows overlap | 0.0 days |", joined)
        self.assertIn("| Landings | 20 (rows overlap, no rate) | 0 (-) |", joined)

    def test_coverage_adds_windows_that_do_not_overlap(self):
        rows = quality.parse_rows("\n".join([
            table_row("2026-09-05 12:00", "2026-09-04 12:00 to 2026-09-05 12:00", 5),
            table_row("2026-09-06 10:00", "2026-09-05 12:00 to 2026-09-06 10:00", 5)]))
        self.assertEqual(quality.coverage_hours(rows), 46.0)

    def test_a_gap_just_over_twenty_percent_warns(self):
        """MINOR 9: the rule reads the real gap, not a gap rounded to an int."""
        rows = quality.parse_rows("\n".join([
            table_row("2026-09-06 10:00", "2026-09-05 09:00 to 2026-09-06 10:00", 10),
            table_row("2026-08-29 20:00", "2026-08-29 00:00 to 2026-08-29 19:54", 10)]))
        joined = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("Coverage differs by 20.4 percent; read the rates, not the "
                      "counts.", joined)

    def test_a_gap_of_exactly_twenty_percent_stays_silent(self):
        rows = quality.parse_rows("\n".join([
            table_row("2026-09-06 10:00", "2026-09-05 09:00 to 2026-09-06 10:00", 10),
            table_row("2026-08-29 20:00", "2026-08-29 00:00 to 2026-08-29 20:00", 10)]))
        joined = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("| Coverage | 1.0 days | 0.8 days |", joined)
        self.assertNotIn("Coverage differs", joined)

    def test_a_bucket_that_measured_nothing_prints_a_dash_not_a_zero(self):
        """MINOR 10: an unavailable source is not a week without defects."""
        rows = quality.parse_rows(table_row(
            "2026-09-06 10:00", "2026-09-05 10:00 to 2026-09-06 10:00", 10,
            unclassified="-", declared="-"))
        joined = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("| Review unclassified | - | 0 |", joined)
        self.assertIn("| Defects declared | - | 0 (-) |", joined)

    def test_a_partly_measured_bucket_names_the_rows_it_skipped(self):
        rows = quality.parse_rows("\n".join([
            table_row("2026-09-06 08:00", "2026-09-05 08:00 to 2026-09-05 20:00", 10,
                      unclassified="3", declared="2"),
            table_row("2026-09-06 10:00", "2026-09-05 20:00 to 2026-09-06 10:00", 10,
                      unclassified="-", declared="-")]))
        joined = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("| Review unclassified | 3, 1 row not measured | 0 |", joined)
        self.assertIn("| Defects declared | 2 (0.10/landing), 1 row not measured "
                      "| 0 (-) |", joined)

    def test_a_row_whose_window_runs_backwards_is_named_not_read_as_zero(self):
        """NOTE 11: a malformed window must not pass as a measured no-time run."""
        rows = quality.parse_rows(table_row(
            "2026-09-06 10:00", "2026-09-06 10:00 to 2026-09-05 10:00", 5))
        joined = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("| Coverage | 0.0 days, 1 row with no window | 0.0 days |",
                      joined)
        self.assertIn("| Landings | 5 (-) | 0 (-) |", joined)
        self.assertIsNone(rows[0]["span"])

    def test_the_disclaimer_names_the_duplicate_run_and_the_union(self):
        joined = "\n".join(quality.render_head([], NOW))
        self.assertIn("counted twice", joined)
        self.assertIn("union", joined)
        self.assertIn("coverage", joined)

    # --- counts over a bucket whose rows overlap ---------------------------
    def test_overlapping_rows_print_the_count_with_no_rate(self):
        """MAJOR 12: the union fixed the denominator, the numerator is still doubled."""
        rows = quality.parse_rows("\n".join([
            table_row("2026-09-06 09:38", "2026-09-05 09:38 to 2026-09-06 09:38", 11),
            table_row("2026-09-06 09:38", "2026-09-03 09:38 to 2026-09-06 09:38", 20)]))
        joined = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("| Coverage | 3.0 days, rows overlap | 0.0 days |", joined)
        self.assertIn("| Landings | 31 (rows overlap, no rate) | 0 (-) |", joined)
        self.assertIn("Rows overlap in a bucket: a manual run beside the scheduled "
                      "one measured the same hours again, so every number of that "
                      "bucket is an upper bound on the counts and no rate, share or "
                      "quotient is printed from it. The daily file of each of those "
                      "runs holds its own window.", joined)
        self.assertIn("| Gates per landing | 10/4 (rows overlap) | 0/0 (-) |",
                      joined)
        self.assertIn("| Defects declared | 4/31 (rows overlap) | 0 (-) |",
                      joined)
        self.assertTrue(quality.rows_overlap(rows))

    def test_rows_that_do_not_overlap_still_print_the_rate(self):
        rows = quality.parse_rows("\n".join([
            table_row("2026-09-05 12:00", "2026-09-04 12:00 to 2026-09-05 12:00", 5),
            table_row("2026-09-06 10:00", "2026-09-05 12:00 to 2026-09-06 10:00", 5)]))
        joined = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("| Coverage | 1.9 days | 0.0 days |", joined)
        self.assertIn("| Landings | 10 (5.2/day) | 0 (-) |", joined)
        self.assertNotIn("rows overlap", joined)
        self.assertFalse(quality.rows_overlap(rows))

    def test_the_gap_text_always_carries_a_decimal(self):
        """NOTE 13: a gap of 20.04 percent must not print as a bare 20."""
        self.assertEqual(quality.gap_text(20.04), "20.0")
        self.assertEqual(quality.gap_text(75.0), "75.0")
        self.assertEqual(quality.gap_text(20.4), "20.4")

    def test_every_ratio_of_an_overlapping_bucket_is_marked(self):
        """MAJOR 15: overlapping rows of different lengths are two populations, so
        a ratio of their sums is a weighted average, not the share of the time."""
        rows = quality.parse_rows("\n".join([
            table_row("2026-09-06 10:00", "2026-08-31 10:00 to 2026-09-06 10:00", 20,
                      red="30/100 (30.0%)", per_landing="100/20 (5.00)",
                      block="3/10 (30.0%)", declared="3", fix="2/20 (10.0%)"),
            table_row("2026-09-06 10:00", "2026-09-05 10:00 to 2026-09-06 10:00", 5,
                      red="0/20 (0.0%)", per_landing="20/5 (4.00)",
                      block="0/2 (0.0%)", declared="0", fix="0/5 (0.0%)"),
            table_row("2026-08-29 10:00", "2026-08-28 10:00 to 2026-08-29 10:00", 10)]))
        joined = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("| Coverage | 6.0 days, rows overlap | 1.0 days |", joined)
        self.assertIn("| Landings | 25 (rows overlap, no rate) | 10 (10.0/day) |",
                      joined)
        self.assertIn("| Gates red / (red + green) | 30/120 (rows overlap) "
                      "| 2/3 (66.7%) |", joined)
        self.assertIn("| Gates per landing | 120/25 (rows overlap) | 5/2 (2.50) |",
                      joined)
        self.assertIn("| Review block / (block + clear) | 3/12 (rows overlap) "
                      "| 1/4 (25.0%) |", joined)
        self.assertIn("| Defects declared | 3/25 (rows overlap) "
                      "| 2 (0.20/landing) |", joined)
        self.assertIn("| Fix landings / landings | 2/25 (rows overlap) "
                      "| 1/2 (50.0%) |", joined)
        self.assertIn("every number of that bucket is an upper bound on the counts",
                      joined)

    # --- the ledger step --------------------------------------------------
    def test_a_quality_failure_never_stops_the_ledger_run(self):
        """MINOR 5: an exception inside compute must not lose the spend numbers."""
        ledger_day = load_ledger_day_new()

        class Boom(object):
            @staticmethod
            def compute(*args, **kwargs):
                raise RuntimeError("gate dir vanished mid run")

        rep, note = ledger_day.quality_report(Boom, START, END, self.landings,
                                              self.defects, FEATURES, NOW)
        expected = "quality.compute failed: RuntimeError: gate dir vanished mid run"
        self.assertEqual(rep, {"unavailable": expected})
        self.assertEqual(note, expected)

    def test_the_ledger_step_passes_the_numbers_through(self):
        ledger_day = load_ledger_day_new()
        module, why = ledger_day.load_quality()
        self.assertIsNone(why)
        rep, note = ledger_day.quality_report(module, START, END, self.landings,
                                              self.defects, FEATURES, NOW)
        self.assertIsNone(note)
        self.assertEqual(rep["review"]["block"], 2)
        self.assertEqual(rep["defects"]["declared"], 2)

    # --- the command line -------------------------------------------------
    def test_cli_prints_json(self):
        out = subprocess.run(
            [sys.executable, SCRIPT, "--since", "24h",
             "--now", "2026-09-06 10:00", "--landings", self.landings,
             "--defects", self.defects, "--gate-dir", self.gate_dirs[0],
             "--no-git"],
            capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertEqual(data["window"]["start"], "2026-09-05 10:00:00")
        self.assertEqual(data["gates"]["red"], 2)
        self.assertEqual(data["review"]["block"], 2)
        self.assertEqual(len(data["review"]["unclassified"]), 1)
        self.assertEqual(data["defects"]["declared"], 2)


def lane(fix, head="abc123def", subjects=None):
    return {"head": head, "fix": fix,
            "subjects": subjects or (["fix(x): a correction"] if fix else ["feat(x): a new door"])}


class FixLaneCase(unittest.TestCase):
    """The fix lane column: the lanes each landing carried, the old ten-cell rows kept beside the new ones,
    and the head never summing a landing count with a lane count."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="quality-lanes-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def numbers(self, features):
        return quality.defect_numbers(START, END, None, features, len(features))

    def test_lanes_counted_per_landing_and_an_unread_landing_named(self):
        got = self.numbers([{"sha": "a", "lanes": [lane(True), lane(False)]},
                            {"sha": "b", "lanes": [lane(True, "fed987cba")]},
                            {"sha": "c"}])
        self.assertEqual((got["fix_lanes"], got["lanes"], got["lanes_unread"]), (2, 3, 1))
        self.assertEqual(quality.lane_cell(got), "2/3 (66.7%) +1 unread")
        self.assertEqual(got["fix_lane_subjects"], ["abc123def fix(x): a correction",
                                                    "fed987cba fix(x): a correction"])

    def test_no_landing_read_is_not_measured_and_no_landing_is_zero(self):
        self.assertEqual(quality.lane_cell(self.numbers([{"sha": "a"}])), "-")
        self.assertEqual(quality.lane_cell(self.numbers(FEATURES)), "-")
        self.assertEqual(quality.lane_cell(self.numbers([])), quality.pct_cell(0, 0))

    def test_a_landing_that_carried_no_lane_reads_zero_not_unread(self):
        got = self.numbers([{"sha": "a", "lanes": []}])
        self.assertEqual((got["lanes"], got["lanes_unread"]), (0, 0))

    def test_the_row_gains_an_eleventh_cell_after_the_landing_share(self):
        rep = quality.compute(START, END, None, None, [{"sha": "a", "subject": "merge(train): x",
                                                        "lanes": [lane(True), lane(False)]}], [], NOW)
        cells = [c.strip() for c in quality.format_row(NOW, START, END, rep).strip().strip("|").split("|")]
        self.assertEqual(len(cells), 11)
        self.assertEqual(cells[9], "0/1 (0.0%)")
        self.assertEqual(cells[10], "1/2 (50.0%)")

    def test_an_old_row_survives_the_rewrite_and_the_head_reads_lanes_from_new_rows_only(self):
        path = os.path.join(self.tmp, "quality.md")
        old = table_row("2026-09-05 18:00", "2026-09-05 08:00 to 2026-09-05 18:00", 3, fix="1/3 (33.3%)")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Quality ledger\n\n" + quality.QUALITY_HEADER + "\n" + quality.QUALITY_SEP + "\n" + old + "\n")
        rep = quality.compute(START, END, None, None, [{"sha": "a", "subject": "merge(train): x",
                                                        "lanes": [lane(True), lane(True), lane(False)]}], [], NOW)
        quality.append_quality(path, NOW, START, END, rep)
        text = read(path)
        self.assertIn(old, text, "the ten-cell row of an older run must survive append_quality")
        rows = quality.parse_rows(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual([(r["fix_lanes"], r["lanes"]) for r in rows], [(None, None), (2, 3)])
        head = "\n".join(quality.render_head(rows, NOW))
        # the landing share sums both rows (1 of 3, then 0 of 1); the lane share reads the new row alone
        self.assertIn("| Fix landings / landings | 1/4 (25.0%) |", head)
        self.assertIn("| Fix lanes / lanes | 2/3 (66.7%); 1 row(s) without it |", head)

    def test_a_week_of_old_rows_prints_a_dash_never_a_zero(self):
        rows = quality.parse_rows(table_row("2026-09-05 18:00", "2026-09-05 08:00 to 2026-09-05 18:00", 3))
        head = "\n".join(quality.render_head(rows, NOW))
        self.assertIn("| Fix lanes / lanes | - (no row of the week measured it) |", head)

    def test_an_eleven_cell_row_with_a_dash_is_not_measured(self):
        row = table_row("2026-09-05 18:00", "2026-09-05 08:00 to 2026-09-05 18:00", 3)[:-2] + " | - |"
        parsed = quality.parse_rows(row)
        self.assertEqual((parsed[0]["fix_lanes"], parsed[0]["lanes"]), (None, None))


class LandingLanesCase(unittest.TestCase):
    """ledger-day.py's parse_reflog_rows gives each landing the tip it moved from, one row past the window, and
    fill_landing_lanes reads the lanes of that range on a real git repository in a temp folder."""

    def setUp(self):
        self.ld = load_ledger_day_new()
        self.tmp = tempfile.mkdtemp(prefix="quality-git-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.tmp, "nohooks"))
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")

    def git(self, *args):
        out = subprocess.run(["git", "-c", "core.hooksPath=" + os.path.join(self.tmp, "nohooks"),
                              "-c", "commit.gpgsign=false", "-C", self.repo] + list(args),
                             capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def commit(self, subject):
        self.git("commit", "--allow-empty", "-q", "-m", subject)
        return self.git("rev-parse", "HEAD")

    def lane_branch(self, name, subjects):
        self.git("checkout", "-q", "-b", name, "main")
        for s in subjects:
            self.commit(s)
        self.git("checkout", "-q", "train")
        self.git("merge", "--no-ff", "-q", name, "-m", "merge(train): %s into train" % name)

    def test_prev_is_read_one_row_past_the_window(self):
        rows = ["ccc main@{2026-09-06 09:00:00 -0500}: merge ccc: Fast-forward",
                "bbb main@{2026-09-05 20:00:00 -0500}: merge bbb: Fast-forward",
                "aaa main@{2026-09-04 10:00:00 -0500}: merge aaa: Fast-forward"]
        features, _ = self.ld.parse_reflog_rows(rows, START, END)
        self.assertEqual([(f["sha"], f["prev"]) for f in features], [("ccc", "bbb"), ("bbb", "aaa")])
        oldest, _ = self.ld.parse_reflog_rows(rows, datetime(2026, 9, 4), END)
        self.assertIsNone(oldest[-1]["prev"])

    def test_a_train_landing_and_a_straight_landing(self):
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        base = self.commit("chore: base")
        self.git("checkout", "-q", "-b", "train")
        self.lane_branch("lanea", ["test(x): pin the refusal, red", "fix(x): refuse the empty name"])
        self.lane_branch("laneb", ["feat(y): a new door", "fix(y): the notes of the review"])
        self.lane_branch("lanec", ["test(z): the sample records the row", "docs(z): say why"])
        self.git("checkout", "-q", "main")
        self.git("merge", "--ff-only", "-q", "train")
        train_tip = self.git("rev-parse", "HEAD")
        self.commit("test(app): re-record a fixture")
        straight_tip = self.commit("fix(app): the fixture follows its file")
        entries = [{"sha": straight_tip, "prev": train_tip}, {"sha": train_tip, "prev": base},
                   {"sha": base, "prev": None}]
        self.ld.fill_landing_lanes(self.repo, entries)
        straight, train, first = entries
        self.assertEqual(sorted(l["fix"] for l in train["lanes"]), [False, False, True])
        self.assertEqual(len(train["lanes"]), 3)
        fixes = [l for l in train["lanes"] if l["fix"]]
        self.assertEqual(sorted(fixes[0]["subjects"]), ["fix(x): refuse the empty name",
                                                        "test(x): pin the refusal, red"])
        self.assertEqual(len(straight["lanes"]), 1)
        self.assertTrue(straight["lanes"][0]["fix"])
        self.assertEqual(len(straight["lanes"][0]["subjects"]), 2)
        self.assertNotIn("lanes", first)
        rep = quality.compute(START, END, None, None, entries, [], NOW)
        self.assertEqual(quality.lane_cell(rep["defects"]), "2/4 (50.0%) +1 unread")

    def test_read_merges_gives_each_landing_its_lanes(self):
        # the call inside read_merges is what puts the lanes on a real ledger run
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        self.commit("chore: base")
        self.git("checkout", "-q", "-b", "train")
        self.lane_branch("lanea", ["test(x): pin the refusal, red", "fix(x): refuse the empty name"])
        self.lane_branch("laneb", ["feat(y): a new door"])
        self.git("checkout", "-q", "main")
        self.git("merge", "--ff-only", "-q", "train")
        now = datetime.now()
        features, _others, source = self.ld.read_merges(self.repo, now - timedelta(hours=1),
                                                        now + timedelta(hours=1), None, branch="main")
        self.assertTrue(source.startswith("reflog main"), source)
        self.assertEqual(len(features), 1)
        self.assertEqual(sorted(l["fix"] for l in features[0]["lanes"]), [False, True])

    def test_the_fix_lanes_list_names_a_fix_subject_never_a_fixture(self):
        got = quality.defect_numbers(START, END, None, [{"sha": "a", "lanes": [
            lane(True, "abc123def", ["fixture: a sample", "fix(a): the correction"])]}], 1)
        self.assertEqual(got["fix_lane_subjects"], ["abc123def fix(a): the correction"])

    def test_an_unreadable_range_and_an_oversized_one_stay_unread(self):
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        base = self.commit("chore: base")
        tip = self.commit("fix(a): one")
        tip = self.commit("fix(a): two")
        entries = [{"sha": tip, "prev": "0123456789abcdef0123456789abcdef01234567"}]
        self.ld.fill_landing_lanes(self.repo, entries)
        self.assertNotIn("lanes", entries[0])
        saved = self.ld.LANDING_MAX_COMMITS
        self.ld.LANDING_MAX_COMMITS = 1
        try:
            entries = [{"sha": tip, "prev": base}]
            self.ld.fill_landing_lanes(self.repo, entries)
            self.assertNotIn("lanes", entries[0])
        finally:
            self.ld.LANDING_MAX_COMMITS = saved

    def test_a_fixup_or_a_feature_word_is_not_a_type(self):
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        base = self.commit("chore: base")
        tip = self.commit("fixup! test(a): tidy")
        tip = self.commit("fixture: a new sample")
        entries = [{"sha": tip, "prev": base}]
        self.ld.fill_landing_lanes(self.repo, entries)
        self.assertEqual([l["fix"] for l in entries[0]["lanes"]], [False])


if __name__ == "__main__":
    unittest.main()
