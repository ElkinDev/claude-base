"""Tests for the rulings rows and the env seams of claude/tools/lane-state.py.

    python scripts/tests/test-lane-state-rulings.py

Every source is a fixture in a temp dir, wired through a temp config file: the
register, an empty gates dir, a landings file, and lane and brief globs. The default
sheet under the home dir is never rendered and the user's own config is never read,
so a run of this suite cannot touch the sheet a live session reads.
"""
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, "claude", "tools", "lane-state.py")


def load_lane_state():
    """Load lane-state.py by path: the dash keeps the import machinery from
    finding the file on its own."""
    loader = SourceFileLoader("lane_state_rulings", SCRIPT)
    spec = importlib.util.spec_from_file_location("lane_state_rulings", SCRIPT,
                                                  loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lane_state = load_lane_state()

# The seams a live session sets. A case that reads them reads the machine it runs on,
# so they are dropped for the length of every case, in process and in a subprocess.
SEAMS = ("CLAUDE_LANE_STATE_CONFIG", "CLAUDE_LANE_STATE_SHEET", "CLAUDE_RULINGS_FILE")


def clear_seams(case):
    for name in SEAMS:
        previous = os.environ.pop(name, None)
        if previous is not None:
            case.addCleanup(os.environ.__setitem__, name, previous)


HEADER = """# Rulings register

Row shape: `- YYYY-MM-DD HH:MM [scope] ruling in one line (source)`.
"""


def row(index, minute="09:00", scope="process"):
    return "- 2026-09-06 %s [%s] ruling number %d (source %d)" % (minute, scope, index, index)


def clipped(line, limit=400):
    """What the sheet prints for a row, computed without the module under test."""
    line = line.rstrip()
    return line if len(line) <= limit else line[:limit - 3] + "..."


class RulingsRowsCase(unittest.TestCase):
    def setUp(self):
        clear_seams(self)
        self.tmp = tempfile.mkdtemp(prefix="rulings-test-").replace("\\", "/")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, text):
        path = self.tmp + "/" + name
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path

    def test_last_thirty_of_thirty_five_rows_in_file_order(self):
        rows = [row(i) for i in range(1, 36)]
        path = self.write("rulings.md", HEADER + "\n".join(rows) + "\n")
        got = lane_state.rulings_rows(path)
        self.assertEqual(len(got), 30)
        self.assertEqual(got, rows[5:])
        self.assertEqual(got[0], row(6))
        self.assertEqual(got[-1], row(35))

    def test_count_argument_overrides_the_default(self):
        rows = [row(i) for i in range(1, 36)]
        path = self.write("rulings.md", HEADER + "\n".join(rows) + "\n")
        self.assertEqual(lane_state.rulings_rows(path, 3), [row(33), row(34), row(35)])

    def test_default_row_count_is_thirty(self):
        self.assertEqual(lane_state.RULINGS_ROWS, 30)
        self.assertEqual(lane_state.RULINGS_CLIP, 400)
        default = inspect.signature(lane_state.rulings_rows).parameters["count"].default
        self.assertEqual(default, 30)

    def test_out_of_order_rows_are_selected_by_stamp(self):
        """The register is append-only, so a ruling decided at 11:47 and written at
        12:30 sits below a 12:2x row while carrying the older stamp. File order is
        therefore not time order, and the selection has to sort by the stamp before
        it cuts, or the cut drops the newest ruling and keeps an older one."""
        early = row(1, "11:4x", "rulings")
        late = row(2, "12:2x", "ops")
        older_day = "- 2026-09-05 21:15 [process] a ruling of the day before (source)"
        path = self.write("rulings.md", HEADER + "\n".join([late, early, older_day]) + "\n")
        self.assertEqual(lane_state.rulings_rows(path), [older_day, early, late])
        self.assertEqual(lane_state.rulings_rows(path, 1), [late])
        self.assertEqual(lane_state.rulings_rows(path, 2), [early, late])

    def test_an_x_hour_sorts_after_every_real_digit_of_its_position(self):
        """An hour written with an x is only known to be later within its day: 12:1x
        is any minute of 12:10 to 12:19, so it sorts after 12:19 and before 12:20."""
        exact = row(1, "12:19")
        blurred = row(2, "12:1x")
        next_ten = row(3, "12:20")
        unknown = row(4, "xx:xx")
        path = self.write("rulings.md",
                          HEADER + "\n".join([unknown, next_ten, blurred, exact]) + "\n")
        self.assertEqual(lane_state.rulings_rows(path), [exact, blurred, next_ten, unknown])

    def test_rows_with_the_same_stamp_keep_file_order(self):
        first = row(1, "10:00")
        second = row(2, "10:00")
        path = self.write("rulings.md", HEADER + first + "\n" + second + "\n")
        self.assertEqual(lane_state.rulings_rows(path), [first, second])

    def test_header_blank_and_malformed_lines_are_ignored(self):
        good_one = "- 2026-09-06 10:5x [quality] a defect seen once gets a lane (memory)"
        good_two = "- 2026-09-06 11:0x [process] the register is injected at recovery (analyst)"
        path = self.write("rulings.md", "\n".join([
            "# Rulings register",
            "Row shape: `- YYYY-MM-DD HH:MM [scope] ruling (source)`.",
            "",
            good_one,
            "- not a ruling at all",
            "-2026-09-06 10:50 [ops] no space after the dash (source)",
            "- 2026-09-06 10:50 quality no bracket (source)",
            "- 2026-9-6 10:50 [ops] short date (source)",
            "",
            good_two,
            "",
        ]) + "\n")
        self.assertEqual(lane_state.rulings_rows(path), [good_one, good_two])

    def test_a_row_over_the_clip_is_clipped_and_one_at_the_clip_is_not(self):
        prefix = "- 2026-09-06 09:00 [design] "
        long_row = prefix + "b" * (600 - len(prefix))
        exact_row = prefix + "c" * (400 - len(prefix))
        path = self.write("rulings.md", long_row + "\n" + exact_row + "\n")
        got = lane_state.rulings_rows(path)
        self.assertEqual(len(got), 2)
        self.assertEqual(len(got[0]), 400)
        self.assertTrue(got[0].startswith(prefix + "bbb"))
        self.assertTrue(got[0].endswith("..."))
        self.assertEqual(got[1], exact_row)
        self.assertEqual(len(got[1]), 400)
        self.assertEqual(clipped(long_row), got[0])

    def test_a_missing_file_gives_one_row_and_never_raises(self):
        missing = self.tmp + "/no-such-register.md"
        self.assertEqual(lane_state.rulings_rows(missing),
                         ["no rulings file at " + missing])

    def test_an_unreadable_path_gives_one_row_and_never_raises(self):
        folder = self.tmp + "/a-folder"
        os.makedirs(folder)
        self.assertEqual(lane_state.rulings_rows(folder),
                         ["no rulings file at " + folder])


class RenderCase(unittest.TestCase):
    def setUp(self):
        clear_seams(self)
        self.tmp = tempfile.mkdtemp(prefix="rulings-render-").replace("\\", "/")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stderr = ""
        self.rows = [row(i) for i in range(1, 31)]
        self.register = self.tmp + "/rulings.md"
        with open(self.register, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(HEADER + "\n".join(self.rows) + "\n")
        os.makedirs(self.tmp + "/gates")
        os.makedirs(self.tmp + "/lanes")
        os.makedirs(self.tmp + "/briefs")
        for index in range(40):
            with open("%s/lanes/lane-%02d.md" % (self.tmp, index), "w",
                      encoding="utf-8", newline="\n") as handle:
                handle.write("# lane report %02d\nbody\n" % index)
        with open(self.tmp + "/landings.md", "w", encoding="utf-8", newline="\n") as handle:
            handle.write("".join("2026-09-06 10:%02d landing row %d\n" % (i, i)
                                 for i in range(20)))
        self.config_path = self.tmp + "/lane-state.json"
        with open(self.config_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({
                "fixed_lines": ["one fixed line"],
                "gates_dirs": [self.tmp + "/gates"],
                "landings_file": self.tmp + "/landings.md",
                "rulings_file": self.register,
                "project_repo": self.tmp + "/not-a-repo",
                "lanes_glob": self.tmp + "/lanes/*.md",
                "briefs_glob": self.tmp + "/briefs/*.md",
            }, handle)
        self.config = lane_state.load_config(self.config_path)
        self.sheet = self.tmp + "/sheet-nobody-reads.md"

    def headings(self, lines):
        return [line for line in lines if line.startswith("## ")]

    def test_rulings_section_sits_before_the_gates_section(self):
        lines = lane_state.render_law(self.config, max_lines=170).splitlines()
        headings = self.headings(lines)
        heading = ("## Rulings (last 30 of %s, append there in the same turn "
                   "the owner rules)" % self.register)
        self.assertIn(heading, headings)
        self.assertIn("## Gates (last 24 h)", headings)
        self.assertLess(headings.index(heading), headings.index("## Gates (last 24 h)"))

    def test_all_thirty_rows_survive_a_forty_line_cap_while_a_cuttable_section_is_cut(self):
        lines = lane_state.render_law(self.config, max_lines=40).splitlines()
        for one in self.rows:
            self.assertIn(one, lines)
        last = lines.index(self.rows[-1])
        self.assertTrue(lines[last + 1].startswith("## Gates"),
                        "a cut marker or another line follows the rulings: %r" % lines[last + 1])
        cut_markers = [line for line in lines if line.endswith(" lines cut]")]
        self.assertTrue(cut_markers, "no cuttable section was cut at max_lines=40")
        lane_rows = [line for line in lines if line.startswith("lanes/lane-")]
        self.assertLess(len(lane_rows), 40)

    def test_render_law_default_max_lines_is_one_hundred_seventy(self):
        default = inspect.signature(lane_state.render_law).parameters["max_lines"].default
        self.assertEqual(default, 170)

    def run_tool(self, args, env_extra=None):
        """The tool as the hook runs it, with the seams cleared so the ambient
        environment of the machine running the suite cannot reach it."""
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        for name in SEAMS:
            env.pop(name, None)
        env.update(env_extra or {})
        process = subprocess.run([sys.executable, SCRIPT] + list(args),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.stderr = process.stderr.decode("utf-8", "replace")
        self.assertEqual(process.returncode, 0, self.stderr)
        return process.stdout.decode("utf-8", "replace")

    def render_with_config(self, config_path):
        """Render with a config the tool has to survive, into a temp sheet, with the
        home pointed at the temp dir so no source of a live session is read."""
        target = self.tmp + "/broken-config-law.md"
        out = self.run_tool(["--config", config_path, "law", "--out", target],
                            {"USERPROFILE": self.tmp, "HOME": self.tmp})
        self.assertIn("wrote " + target, out)
        with open(target, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(6, len([ln for ln in text.splitlines() if ln.startswith("## ")]))
        return text

    def test_a_config_that_is_not_valid_json_is_ignored_and_the_sheet_renders(self):
        """The sheet is what the recovery hook prints at every compaction, and the hook
        swallows a renderer that fails, so a trailing comma in the config would take the
        paragraph away silently. The config is ignored, loudly, and the sheet renders."""
        path = self.tmp + "/broken.json"
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write('{ "fixed_lines": ["a"], }\n')
        text = self.render_with_config(path)
        self.assertIn("## Fixed lines\nno fixed lines configured\n", text)
        self.assertIn(path, self.stderr)

    def test_a_config_that_is_a_json_array_is_ignored_and_the_sheet_renders(self):
        path = self.tmp + "/array.json"
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write('["fixed_lines"]\n')
        self.render_with_config(path)
        self.assertIn(path, self.stderr)
        self.assertIn("not a JSON object", self.stderr)

    def test_a_config_path_that_is_a_directory_is_ignored_and_the_sheet_renders(self):
        path = self.tmp + "/a-folder"
        os.makedirs(path)
        self.render_with_config(path)
        self.assertIn(path, self.stderr)

    def test_out_writes_the_given_path_and_the_env_sheet_stays_untouched(self):
        target = self.tmp + "/out-law.md"
        out = self.run_tool(["--config", self.config_path, "law", "--out", target],
                            {"CLAUDE_LANE_STATE_SHEET": self.sheet})
        self.assertIn("wrote " + target, out)
        self.assertFalse(os.path.exists(self.sheet), "the run wrote the env sheet")
        with open(target, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("## Rulings (last 30 of ", text)
        self.assertIn(self.rows[-1], text)

    def test_the_config_and_sheet_env_seams_are_what_the_recovery_hook_wires(self):
        """The compaction recovery hook runs `lane-state.py law` with no arguments and
        then reads CLAUDE_LANE_STATE_SHEET, so the sheet the tool writes with no --out
        has to be that path, and the config has to come from the env as well."""
        out = self.run_tool(["law"], {"CLAUDE_LANE_STATE_CONFIG": self.config_path,
                                      "CLAUDE_LANE_STATE_SHEET": self.sheet})
        self.assertIn("wrote " + self.sheet, out)
        with open(self.sheet, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("one fixed line", text)
        self.assertIn(self.rows[-1], text)

    def test_the_rulings_env_seam_overrides_the_configured_register(self):
        other = self.tmp + "/other-rulings.md"
        only_row = row(99, "13:00", "ops")
        with open(other, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(HEADER + only_row + "\n")
        previous = os.environ.get("CLAUDE_RULINGS_FILE")
        os.environ["CLAUDE_RULINGS_FILE"] = other
        self.addCleanup(lambda: os.environ.__setitem__("CLAUDE_RULINGS_FILE", previous)
                        if previous is not None
                        else os.environ.pop("CLAUDE_RULINGS_FILE", None))
        config = lane_state.load_config(self.config_path)
        self.assertEqual(other, config["rulings_file"])
        body = lane_state.render_law(config)
        self.assertIn(only_row, body)
        self.assertNotIn(self.rows[-1], body)

    def test_relative_defaults_resolve_beside_the_config_file(self):
        """A config that names only some keys leaves the rest beside itself, which is
        what keeps one folder of sources working with a three-line config file."""
        bare = self.tmp + "/bare-config.json"
        with open(bare, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"fixed_lines": ["bare"]}, handle)
        config = lane_state.load_config(bare)
        self.assertEqual(self.register, config["rulings_file"].replace("\\", "/"))
        self.assertEqual(self.tmp + "/landings.md",
                         config["landings_file"].replace("\\", "/"))
        self.assertEqual(self.tmp + "/lanes/*.md", config["lanes_glob"].replace("\\", "/"))
        self.assertEqual(self.tmp + "/briefs/*.md", config["briefs_glob"].replace("\\", "/"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
