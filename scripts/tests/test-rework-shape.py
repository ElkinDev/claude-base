"""Pins for scripts/rework-shape.py: the register's round counter reads rows of every kind, and the line carries
no register BLOCK count, which a retired row kind once carried; the BLOCK count is the review files'.

    python scripts/tests/test-rework-shape.py
    REWORK_SHAPE_PY=<path> python scripts/tests/test-rework-shape.py     # against a staged copy

The module is loaded and its RULINGS, REVIEWS_DIR and GATES_DIRS point at a temp root, so nothing live is read.
"""
import contextlib, importlib.machinery, importlib.util, io, os, shutil, sys, tempfile, unittest

SCRIPT = os.environ.get("REWORK_SHAPE_PY") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rework-shape.py")
ROWS = """2026-01-10 10:0x [diagnosis] abcd round 3 BLOCK (third; S8): the pin reads the wrong field. (orchestrator pane 10:0x)
2026-01-10 10:1x [decision] abcd-g2 launched on the round 3 tip. (orchestrator pane 10:1x)
- 2026-01-10 10:2x [analyst] efgh round 2 delta BLOCK read, a fresh implementer on the brief. (analyst pane 10:2x)
2026-01-10 10:3x [measurement] the [quality] kind is retired; a row quoting BLOCK is no verdict. (analyst pane 10:3x)
2026-01-10 10:4x [decision] zzzz round 5 names no lane token, never counted. (orchestrator pane 10:4x)
2026-01-11 10:0x [diagnosis] abcd round 4 outside the window. (orchestrator pane 10:0x)
2026-01-09 18:0x [decision] ijkl-g1 launched the day before. (orchestrator pane 18:0x)
2026-01-10 11:0x [diagnosis] ijkl round 3 (continuation): a fresh implementer on the round 3 brief. (orchestrator pane 11:0x)
"""


def load():
    loader = importlib.machinery.SourceFileLoader("rework_shape_under_test", SCRIPT)
    spec = importlib.util.spec_from_loader("rework_shape_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ReworkShapeTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rework-shape-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "reviews"))
        os.makedirs(os.path.join(self.root, "temp"))
        with open(os.path.join(self.root, "rulings.md"), "w", encoding="utf-8") as f:
            f.write(ROWS)
        stamp = __import__("datetime").datetime(2026, 1, 10, 11, 0).timestamp()
        for name, text in (("abcd-2026-01-10.md", "Disposition: BLOCK (1 MAJOR)\n"),
                           ("abcd-fix1-2026-01-10.md", "Disposition: CLEAR with notes (1 MINOR)\n")):
            p = os.path.join(self.root, "reviews", name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            os.utime(p, (stamp, stamp))
        self.mod = load()
        self.mod.RULINGS = os.path.join(self.root, "rulings.md")
        self.mod.REVIEWS_DIR = os.path.join(self.root, "reviews")
        self.mod.GATES_DIRS = [os.path.join(self.root, "temp")]

    def line(self):
        out = io.StringIO()
        argv = sys.argv
        sys.argv = ["rework-shape.py", "--since", "2026-01-10 08:00", "--until", "2026-01-10 22:00"]
        try:
            with contextlib.redirect_stdout(out):
                self.mod.main()
        finally:
            sys.argv = argv
        return out.getvalue()

    def test_rounds_are_read_from_rows_of_every_kind(self):
        rows = self.mod.register_rows(__import__("datetime").datetime(2026, 1, 10, 8, 0),
                                      __import__("datetime").datetime(2026, 1, 10, 22, 0))
        self.assertEqual(self.mod.review_rounds(rows, ["efgh"]), {"abcd": 3, "efgh": 2})
        # ijkl names its gate only the day before: the token is learned from the whole register (review note 2)
        self.assertIn("lanes at review round 3+ 2 (abcd r3, ijkl r3)", self.line())

    def test_the_line_carries_no_register_block_count_and_the_files_give_it(self):
        text = self.line()
        self.assertNotIn("register: BLOCK rows", text)
        self.assertIn("register: delta BLOCK rows 1, diagnoses 1, fresh-agent rounds 2,", text)
        self.assertIn("review files 2: BLOCK 1, CLEAR+notes 1, CLEAR 0", text)

    def test_the_config_gives_the_paths_and_a_wrong_type_is_absent(self):
        cfg = os.path.join(self.root, "cfg.json")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write('{"gates_dirs": "%s", "rulings_file": 7, "reviews_dir": [1]}' % os.path.join(self.root, "temp").replace("\\", "/"))
        gates, rulings, reviews = self.mod.load_paths(cfg)
        self.assertEqual(gates, [os.path.join(self.root, "temp").replace("\\", "/")])
        self.assertEqual(os.path.normcase(rulings), os.path.normcase(os.path.join(self.root, "rulings.md")))
        self.assertEqual(os.path.normcase(reviews), os.path.normcase(os.path.join(self.root, "reviews")))
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("{bad json")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.mod.load_paths(cfg)[0], [])
        self.assertIn("not read", err.getvalue())  # named, never silent (review f6f7 note 4)

    def test_a_missing_config_takes_its_defaults_from_the_home_folder_like_the_sheet(self):
        saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "CLAUDE_RULINGS_FILE")}
        try:
            os.environ["HOME"] = os.environ["USERPROFILE"] = self.root
            os.environ.pop("CLAUDE_RULINGS_FILE", None)
            rulings = self.mod.load_paths(os.path.join(self.root, "nowhere", "none.json"))[1]
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(os.path.normcase(rulings), os.path.normcase(os.path.join(self.root, ".claude", "rulings.md")))

    @unittest.skipUnless(os.name == "nt", "a junction is a Windows folder link")
    def test_a_junction_and_its_target_are_one_folder(self):
        import subprocess
        temp = os.path.join(self.root, "temp")
        with open(os.path.join(temp, "r1.exit"), "w", encoding="utf-8") as f:
            f.write("GATE_EXIT=0\n")
        link = os.path.join(self.root, "link")
        subprocess.run(["cmd", "/c", "mklink", "/J", link, temp], capture_output=True, timeout=60, check=True)
        self.mod.GATES_DIRS = [temp, link]
        now = __import__("datetime").datetime.now()
        found = self.mod.exit_files(now.replace(year=now.year - 1), now.replace(year=now.year + 1))
        self.assertEqual(len(found), 1)

    def test_a_folder_named_exit_and_a_repeated_gates_dir_are_one_run_or_none(self):
        temp = os.path.join(self.root, "temp")
        os.makedirs(os.path.join(temp, "g", "dir.exit"))
        with open(os.path.join(temp, "g", "r1.exit"), "w", encoding="utf-8") as f:
            f.write("GATE_EXIT=0\n")
        self.mod.GATES_DIRS = [temp, temp + os.sep, os.path.join(temp, "g", "..")]
        now = __import__("datetime").datetime.now()
        found = self.mod.exit_files(now.replace(year=now.year - 1), now.replace(year=now.year + 1))
        self.assertEqual([os.path.basename(fp) for _, fp in found], ["r1.exit"])

    def test_a_bare_date_is_a_usage_error(self):
        import subprocess
        r = subprocess.run([sys.executable, SCRIPT, "--since", "2026-01-10", "--until", "2026-01-10 22:00"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr[-200:])
        self.assertIn("YYYY-MM-DD HH:MM", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
