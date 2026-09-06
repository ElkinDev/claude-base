"""Tests for claude/tools/lane-state.py.

    python scripts/tests/test-lane-state.py

Fixtures are written to a temp dir: a sentinel-only gate still running, the same one
aged past the dead threshold, a finished green gate carrying the full block a gate
runner writes, an older gate whose ktlint phase failed, and a landings file with
SubagentStop hook rows mixed into the prose rows. Nothing under the user's home is
read or written: every path the renderer touches comes from a temp config.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, "claude", "tools", "lane-state.py")


def load_lane_state():
    """Load lane-state.py by path: the dash keeps the import machinery from
    finding the file on its own."""
    loader = SourceFileLoader("lane_state", SCRIPT)
    spec = importlib.util.spec_from_file_location("lane_state", SCRIPT, loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lane_state = load_lane_state()


GREEN = """RUN=pwp-g7-merge
GATE_EXIT=97
TIP_START=7862dd1b2c5a19942316bd7438c6b9918ae70564
GATE_START=1788652915
LOCK_WAIT_SECS=0
PHASE_compile_EXIT=0 secs=72
PHASE_ktlint_EXIT=0 secs=93
PHASE_detekt_EXIT=0 secs=102
PHASE_test_EXIT=0 secs=608
FAILED_TESTS=
PHASE_FAILURES=0
TIP_END=7862dd1b2c5a19942316bd7438c6b9918ae70564
TIP_MOVED=0
GATE_EXIT=0
"""

SENTINEL = """RUN=afo-g1
GATE_EXIT=97
TIP_START=16e54ceab81f5be284bb87188b09186ea361e9e2
GATE_START=1788627304
"""

KTLINT_FAIL = """RUN=bud68-g1
GATE_EXIT=97
PHASE_compile_EXIT=0 secs=10
PHASE_ktlint_EXIT=1 secs=40
PHASE_FAILURES=1
GATE_EXIT=1
"""

LANDINGS = "".join(
    [
        "Some preamble that is not a row.\n",
        "2026-09-05 10:00 ROW ONE landed on aaaaaaa.\n",
        "2026-09-05 10:05 ROW TWO landed on bbbbbbb.\n",
        "| 2026-09-05 10:06 | SubagentStop | lane | repo @ main | deadbee1 | hook row |\n",
        "2026-09-05 10:10 ROW THREE landed on ccccccc.\n",
        "2026-09-05 10:15 ROW FOUR landed on ddddddd.\n",
        "2026-09-05 10:20 ROW FIVE landed on eeeeeee.\n",
        "| 2026-09-05 10:21 | SubagentStop | lane | repo @ main | deadbee2 | hook row |\n",
        "2026-09-05 10:25 ROW SIX landed on fffffff.\n",
        "2026-09-05 10:30 ROW SEVEN landed on ggggggg.\n",
        "2026-09-05 10:35 ROW EIGHT landed on hhhhhhh.\n",
        "2026-09-05 10:40 ROW NINE landed on iiiiiii. " + ("x" * 400) + "\n",
    ]
)

RULINGS = "".join(
    [
        "# Rulings register\n",
        "\n",
        "Row shape: `- YYYY-MM-DD HH:MM [scope] ruling in one line (source)`.\n",
        "\n",
        "- 2026-09-06 12:1x [process] the register is read at every compaction (owner)\n",
        "- 2026-09-06 12:2x [quality] a defect seen once gets a lane (owner)\n",
    ]
)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def hhmm(path):
    return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%H:%M")


class GateLineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lane-state-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = 1788660000.0
        self.green = write(os.path.join(self.tmp, "pwpgate", "pwp-g7-merge.exit"), GREEN)
        self.sentinel = write(os.path.join(self.tmp, "afogate", "afo-g1.exit"), SENTINEL)
        self.failed = write(os.path.join(self.tmp, "bud68", "bud68-g1.exit"), KTLINT_FAIL)
        os.utime(self.green, (self.now - 600, self.now - 600))
        os.utime(self.sentinel, (self.now - 300, self.now - 300))
        os.utime(self.failed, (self.now - 3600, self.now - 3600))

    def lines(self, **kwargs):
        kwargs.setdefault("now", self.now)
        return lane_state.gates_lines([self.tmp], **kwargs)

    def test_green_line_is_exact(self):
        expected = (
            "pwp pwp-g7-merge %s 7862dd1 exit=0 lock=0 moved=0 ok phases=4 secs=875"
            % hhmm(self.green)
        )
        self.assertIn(expected, self.lines())

    def test_running_sentinel_line_is_exact(self):
        expected = (
            "afo afo-g1 %s 16e54ce exit=running lock=- moved=- ok phases=0 secs=0"
            % hhmm(self.sentinel)
        )
        self.assertIn(expected, self.lines())

    def test_ktlint_failure_line_is_exact(self):
        expected = (
            "bud68 bud68-g1 %s - exit=1 lock=- moved=- failed=ktlint:1 phases=2 secs=50"
            % hhmm(self.failed)
        )
        self.assertIn(expected, self.lines())

    def test_newest_first(self):
        runs = [line.split()[1] for line in self.lines()]
        self.assertEqual(["afo-g1", "pwp-g7-merge", "bud68-g1"], runs)

    def test_sentinel_older_than_two_hours_reads_dead(self):
        os.utime(self.sentinel, (self.now - 7201, self.now - 7201))
        line = [ln for ln in self.lines() if ln.split()[1] == "afo-g1"][0]
        self.assertIn("exit=dead", line)

    def test_since_filter_drops_older_files(self):
        # 0.25 h is 900 s, so it still holds the file written 600 s ago
        self.assertEqual(["afo-g1", "pwp-g7-merge"],
                         [line.split()[1] for line in self.lines(since_hours=0.25)])
        # 0.1 h is 360 s, which only the file written 300 s ago is inside
        self.assertEqual(["afo-g1"],
                         [line.split()[1] for line in self.lines(since_hours=0.1)])

    def test_all_keeps_a_file_from_last_year(self):
        os.utime(self.failed, (self.now - 400 * 86400, self.now - 400 * 86400))
        self.assertEqual(3, len(self.lines(since_hours=None)))
        self.assertEqual(2, len(self.lines()))

    def test_lane_filter(self):
        runs = [line.split()[1] for line in self.lines(lane="pwp")]
        self.assertEqual(["pwp-g7-merge"], runs)

    def test_lane_name_strips_only_a_gate_suffix(self):
        self.assertEqual("pwp", lane_state.lane_name_from_dir("pwpgate"))
        self.assertEqual("bf", lane_state.lane_name_from_dir("bf-gate"))
        self.assertEqual("bud68", lane_state.lane_name_from_dir("bud68"))
        self.assertEqual("gate", lane_state.lane_name_from_dir("gate"))
        self.assertEqual("gate1", lane_state.lane_name_from_dir("gate1"))

    def test_last_gate_exit_wins_over_the_sentinel(self):
        killed = write(os.path.join(self.tmp, "pwpgate", "pwp-g5.exit"),
                       "RUN=pwp-g5\nGATE_EXIT=97\nGATE_START=1788650785\nGATE_EXIT=143\n")
        os.utime(killed, (self.now - 60, self.now - 60))
        line = [ln for ln in self.lines() if ln.split()[1] == "pwp-g5"][0]
        self.assertIn(" exit=143 ", line)


class LandingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lane-state-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = write(os.path.join(self.tmp, "landings.md"), LANDINGS)

    def test_hook_rows_are_dropped_and_the_last_eight_prose_rows_kept(self):
        rows = lane_state.last_landings(self.path, count=8, clip=220)
        self.assertEqual(8, len(rows))
        self.assertTrue(rows[0].startswith("2026-09-05 10:05 ROW TWO"))
        self.assertTrue(rows[-1].startswith("2026-09-05 10:40 ROW NINE"))
        self.assertFalse(any("SubagentStop" in row for row in rows))

    def test_rows_are_clipped_to_the_limit(self):
        rows = lane_state.last_landings(self.path, count=8, clip=220)
        self.assertEqual(220, max(len(row) for row in rows))
        self.assertTrue(rows[-1].endswith("..."))

    def test_missing_file_raises_so_the_section_can_say_why(self):
        with self.assertRaises(OSError):
            lane_state.last_landings(os.path.join(self.tmp, "gone.md"))


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lane-state-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.gates = os.path.join(self.tmp, "pwpgate")
        write(os.path.join(self.gates, "pwp-g7-merge.exit"), GREEN)
        self.config = {
            "fixed_lines": ["Fixed one.", "Fixed two."],
            "gates_dirs": [self.tmp],
            "landings_file": write(os.path.join(self.tmp, "landings.md"), LANDINGS),
            "rulings_file": write(os.path.join(self.tmp, "rulings.md"), RULINGS),
            "project_repo": os.path.join(self.tmp, "no-such-repo"),
            "lanes_glob": os.path.join(self.tmp, "lanes", "*.md"),
            "briefs_glob": os.path.join(self.tmp, "briefs", "*.md"),
        }
        # Built here rather than read off the module, so the case still fails if
        # the heading the sheet prints changes.
        self.rulings_heading = (
            "## Rulings (last %d of %s, append there in the same turn the owner rules)"
            % (lane_state.RULINGS_ROWS,
               self.config["rulings_file"].replace("\\", "/")))

    def render(self, max_lines=120):
        return lane_state.render_law(self.config, max_lines=max_lines)

    def test_first_line_and_section_order(self):
        lines = self.render().splitlines()
        self.assertRegex(
            lines[0],
            r"^# State sheet, rendered \d{4}-\d{2}-\d{2} \d{2}:\d{2} by lane-state\.py; "
            r"sources on disk, no model$",
        )
        headings = [ln for ln in lines if ln.startswith("## ")]
        self.assertEqual(
            ["## Fixed lines", "## Worktrees", self.rulings_heading,
             "## Gates (last 24 h)", "## Last landings",
             "## Lane reports and briefs (last 24 h)"],
            headings,
        )

    def test_a_missing_source_never_fails_the_render(self):
        self.config["landings_file"] = os.path.join(self.tmp, "gone.md")
        body = self.render()
        self.assertIn("## Last landings\nsection unavailable: ", body)
        self.assertIn(self.rulings_heading + "\n", body)

    def test_a_missing_worktree_repo_says_so_and_the_sheet_still_renders(self):
        body = self.render()
        self.assertIn("## Worktrees\nsection unavailable: ", body)
        self.assertIn("Fixed one.", body)

    def test_the_cut_spares_the_fixed_lines_and_the_gates(self):
        full = self.render()
        cut = self.render(max_lines=16)
        self.assertLessEqual(len(cut.splitlines()), 16)
        self.assertIn("Fixed one.", cut)
        self.assertIn("Fixed two.", cut)
        for line in full.splitlines():
            if line.startswith("pwp pwp-g7-merge "):
                self.assertIn(line, cut)
                break
        else:
            self.fail("the full sheet carried no gate line to check")
        self.assertIn("lines cut", cut)

    def test_render_is_deterministic_apart_from_the_stamp(self):
        first = self.render().splitlines()[1:]
        second = self.render().splitlines()[1:]
        self.assertEqual(first, second)

    def test_write_atomic_leaves_no_temp_file_behind(self):
        target = os.path.join(self.tmp, "law.md")
        lane_state.write_atomic(target, "body\n")
        with open(target, encoding="utf-8") as handle:
            self.assertEqual("body\n", handle.read())
        leftovers = [n for n in os.listdir(self.tmp)
                     if n.startswith("law.md.") or n.startswith(".law-")]
        self.assertEqual([], leftovers)


class PublicDefaultsCase(unittest.TestCase):
    """The kit is installed on any home, so a default that names a drive letter and
    somebody's folder is a default that works on one machine only. Every default is
    home-relative or comes from the config, and the env seams move it from there."""

    def test_the_tool_carries_no_path_of_one_machine(self):
        drive_path = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
        with open(SCRIPT, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        # The offending lines are named, not the whole file: a failure here has to be
        # readable, and printing the source drowns it.
        guilty = ["%d: %s" % (n, line) for n, line in enumerate(lines, 1)
                  if drive_path.search(line)]
        self.assertEqual(guilty, [], "the tool names a path of one machine")

    def test_every_module_default_sits_under_the_home_dir(self):
        home = os.path.normcase(os.path.expanduser("~"))
        for name in ("DEFAULT_CONFIG", "DEFAULT_SHEET"):
            value = getattr(lane_state, name)
            self.assertTrue(os.path.normcase(value).startswith(home),
                            "%s is not under the home dir: %s" % (name, value))
        for key in ("landings_file", "rulings_file", "lanes_glob", "briefs_glob"):
            value = lane_state.DEFAULTS[key]
            self.assertTrue(os.path.normcase(value).startswith(home),
                            "%s is not under the home dir: %s" % (key, value))
        self.assertEqual([], lane_state.DEFAULTS["gates_dirs"])
        self.assertEqual([], lane_state.DEFAULTS["fixed_lines"])
        self.assertEqual("", lane_state.DEFAULTS["project_repo"])

    def test_an_unset_config_renders_every_section_instead_of_raising(self):
        """A fresh install has no config yet. The sheet still renders: each section
        prints its empty line, and nothing scans a folder nobody configured."""
        tmp = tempfile.mkdtemp(prefix="lane-state-unset-").replace("\\", "/")
        self.addCleanup(shutil.rmtree, tmp, True)
        # The home is moved to the temp dir and the module reloaded from there, so the
        # defaults this case renders are the temp ones: a run of the suite never reads
        # the register, the lanes or the briefs of the machine it runs on.
        for name in ("USERPROFILE", "HOME", "CLAUDE_LANE_STATE_CONFIG",
                     "CLAUDE_LANE_STATE_SHEET", "CLAUDE_RULINGS_FILE"):
            previous = os.environ.pop(name, None)
            if previous is not None:
                self.addCleanup(os.environ.__setitem__, name, previous)
        os.environ["USERPROFILE"] = tmp
        os.environ["HOME"] = tmp
        fresh = load_lane_state()
        self.assertEqual(tmp + "/.claude", fresh.HOME_CLAUDE.replace("\\", "/"))
        config = fresh.load_config(tmp + "/.claude/no-such-config.json")
        self.assertEqual([], config["gates_dirs"])
        self.assertEqual(tmp + "/.claude/rulings.md",
                         config["rulings_file"].replace("\\", "/"))
        body = fresh.render_law(config)
        headings = [ln for ln in body.splitlines() if ln.startswith("## ")]
        self.assertEqual(6, len(headings))
        self.assertIn("## Fixed lines\nno fixed lines configured\n", body)
        self.assertIn("## Worktrees\nno repository configured\n", body)
        self.assertIn("## Gates (last 24 h)\nno gate exit file in the last 24 h\n", body)
        self.assertIn("## Lane reports and briefs (last 24 h)\nnone\n", body)


class ConfigEncodingCase(unittest.TestCase):
    """A config file written on Windows carries a UTF-8 BOM more often than not:
    PowerShell's `Out-File` and `Set-Content -Encoding utf8` both write one. A reader
    that opens it as plain utf-8 sees the BOM as a character, json rejects it, and the
    sheet renders with the defaults under a hook that says nothing. The tool reads the
    first line of a lane report with the BOM stripped, so the config reader agrees.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lane-state-bom-").replace("\\", "/")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_config_with_a_utf8_bom_is_read_and_not_ignored(self):
        fixed = "the fixed line a BOM used to hide"
        config = self.tmp + "/lane-state.json"
        with open(config, "wb") as handle:
            handle.write(b"\xef\xbb\xbf")
            handle.write(json.dumps({"fixed_lines": [fixed]}).encode("utf-8"))
        target = self.tmp + "/law.md"
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        for name in ("CLAUDE_LANE_STATE_CONFIG", "CLAUDE_LANE_STATE_SHEET",
                     "CLAUDE_RULINGS_FILE"):
            env.pop(name, None)
        # The home goes to the temp dir, so a fallback would read nothing of this machine.
        env["USERPROFILE"] = self.tmp
        env["HOME"] = self.tmp
        process = subprocess.run(
            [sys.executable, SCRIPT, "--config", config, "law", "--out", target],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        stderr = process.stderr.decode("utf-8", "replace")
        self.assertEqual(0, process.returncode, stderr)
        self.assertEqual("", stderr.strip(), "the config was ignored: " + stderr)
        with open(target, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("## Fixed lines\n" + fixed + "\n", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
