"""Pins for claude/statusline.ps1 and its Python twin claude/statusline.py.

The role segment, in both: the role the launcher passes in CLAUDE_ROLE is the first segment of the line, as plain text
in one pastel colour per role; a session with no role gets no role segment; the folder map ($RoleByFolder in the .ps1,
ROLE_BY_FOLDER in the .py) relabels a lane or role-less session by the folder of its transcript, and never a session
that has another role. The Python twin also pins the rest of the line: the context bar colours, the quota "left"
text, the git segment in a repository and its absence outside one, the account label, and a payload that is not JSON.

  python scripts/tests/test-statusline.py              the two scripts under claude/
  STATUSLINE=<path> python scripts/tests/test-statusline.py      another copy of the .ps1 (a staged one, or a mutant)
  STATUSLINE_PY=<path> python scripts/tests/test-statusline.py   another copy of the .py

The .ps1 cases are Windows only (PowerShell 5.1) and skipped where powershell is not on PATH; the .py cases run
anywhere, under the interpreter running this file. Each case pipes a fixed payload, the copies it edits live in a temp
folder, and the .py cases point the home folder at a temp folder too, so no real account file is read.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("STATUSLINE") or os.path.join(os.path.dirname(os.path.dirname(HERE)), "claude", "statusline.ps1")
SCRIPT_PY = os.environ.get("STATUSLINE_PY") or os.path.join(os.path.dirname(os.path.dirname(HERE)), "claude",
                                                             "statusline.py")
ESC = "\x1b["


def payload(folder="C--work-my-app"):
    return ('{"model":{"display_name":"Opus 5"},"effort":{"level":"high"},"workspace":{"current_dir":"%s"},'
            '"transcript_path":"%s/projects/%s/s1.jsonl","context_window":{"used_percentage":12}}'
            % (tempfile.gettempdir().replace("\\", "/"), tempfile.gettempdir().replace("\\", "/"), folder))


@unittest.skipUnless(shutil.which("powershell"), "PowerShell not on PATH")
class StatuslineRoleTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="slr-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def render(self, role, script=SCRIPT, folder="C--work-my-app"):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_ROLE"}
        if role is not None:
            env["CLAUDE_ROLE"] = role
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
                           input=payload(folder).encode("utf-8"), capture_output=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        return r.stdout.decode("utf-8", "replace")

    def with_map(self, entry):
        with open(SCRIPT, "rb") as f:
            text = f.read().decode("ascii")
        line = "$RoleByFolder = @{}"
        self.assertEqual(text.count(line), 1, "the $RoleByFolder line moved")
        path = os.path.join(self.root, "statusline.ps1")
        with open(path, "wb") as f:
            f.write(text.replace(line, "$RoleByFolder = @{ " + entry + " }", 1).encode("ascii"))
        return path

    def test_each_role_is_the_first_segment_with_its_colour(self):
        for role, colour in (("analyst", "38;5;153"), ("orchestrator", "38;5;183"), ("lane", "38;5;250"),
                             ("research", "38;5;151"), ("reviewer", "38;5;223")):
            line = self.render(role)
            self.assertTrue(line.startswith(ESC + colour + "m" + role + ESC + "0m"), (role, line[:40]))

    def test_role_is_read_trimmed_and_case_folded(self):
        self.assertTrue(self.render("  Analyst ").startswith(ESC + "38;5;153manalyst" + ESC))

    def test_no_role_no_segment(self):
        for role in (None, ""):
            line = self.render(role)
            self.assertTrue(line.startswith(ESC + "95;1m"), line[:40])
            self.assertNotIn(ESC + "38;5;", line)

    def test_folder_map_relabels_a_lane_by_its_transcript_folder(self):
        mapped = self.with_map("'C--work-my-app' = 'orchestrator my-app'")
        self.assertTrue(self.render("lane", mapped).startswith(ESC + "38;5;183morchestrator my-app" + ESC))
        self.assertTrue(self.render(None, mapped).startswith(ESC + "38;5;183morchestrator my-app" + ESC))
        self.assertTrue(self.render("lane", mapped, folder="C--work-other").startswith(ESC + "38;5;250mlane" + ESC))

    def test_folder_map_never_relabels_another_role(self):
        mapped = self.with_map("'C--work-my-app' = 'orchestrator my-app'")
        self.assertTrue(self.render("analyst", mapped).startswith(ESC + "38;5;153manalyst" + ESC))


class StatuslinePyTest(unittest.TestCase):
    """statusline.py through the interpreter running this file, so no PowerShell is needed."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="slp-")
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def env(self, role=None, **extra):
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_ROLE", "CLAUDE_CONFIG_DIR")}
        # The home folder is a temp one, so the account segment reads no real ~/.claude.json; USERPROFILE is what
        # expanduser reads on Windows. The ceiling keeps git from finding a repository above the temp folder.
        env.update(HOME=self.home, USERPROFILE=self.home, GIT_CEILING_DIRECTORIES=os.path.dirname(self.root))
        if role is not None:
            env["CLAUDE_ROLE"] = role
        env.update(extra)
        return env

    def run_raw(self, data, script=SCRIPT_PY, **env):
        r = subprocess.run([sys.executable, script], input=data, capture_output=True, env=self.env(**env), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertEqual(r.stderr, b"")
        return r.stdout.decode("utf-8")

    def render(self, role=None, script=SCRIPT_PY, folder="C--work-my-app", **env):
        return self.run_raw(payload(folder).encode("utf-8"), script, role=role, **env)

    def render_json(self, doc, **env):
        return self.run_raw(json.dumps(doc).encode("utf-8"), **env)

    def with_map(self, entry):
        with open(SCRIPT_PY, "rb") as f:
            text = f.read().decode("utf-8")
        line = "ROLE_BY_FOLDER = {}"
        self.assertEqual(text.count(line), 1, "the ROLE_BY_FOLDER line moved")
        path = os.path.join(self.root, "statusline.py")
        with open(path, "wb") as f:
            f.write(text.replace(line, "ROLE_BY_FOLDER = {" + entry + "}", 1).encode("utf-8"))
        return path

    def base(self, cwd=None, **fields):
        doc = {"model": {"display_name": "Opus 5"}, "workspace": {"current_dir": cwd or self.root}}
        doc.update(fields)
        return doc

    # ---- role ----

    def test_each_role_is_the_first_segment_with_its_colour(self):
        for role, colour in (("analyst", "38;5;153"), ("orchestrator", "38;5;183"), ("lane", "38;5;250"),
                             ("research", "38;5;151"), ("reviewer", "38;5;223")):
            line = self.render(role)
            self.assertTrue(line.startswith(ESC + colour + "m" + role + ESC + "0m"), (role, line[:40]))

    def test_role_is_read_trimmed_and_case_folded(self):
        self.assertTrue(self.render("  Analyst ").startswith(ESC + "38;5;153manalyst" + ESC))

    def test_no_role_no_badge(self):
        for role in (None, "", "   "):
            line = self.render(role)
            self.assertTrue(line.startswith(ESC + "95;1m"), line[:40])
            self.assertNotIn(ESC + "38;5;", line)

    def test_an_escape_in_the_role_never_reaches_the_line(self):
        line = self.render("analyst\x1b[31m")   # the escape byte is dropped, the rest is shown as text
        self.assertTrue(line.startswith(ESC + "38;5;223manalyst[31m" + ESC + "0m  " + ESC + "95;1m"), line[:40])

    def test_folder_map_relabels_a_lane_by_its_transcript_folder(self):
        mapped = self.with_map("'C--work-my-app': 'orchestrator my-app'")
        self.assertTrue(self.render("lane", mapped).startswith(ESC + "38;5;183morchestrator my-app" + ESC))
        self.assertTrue(self.render(None, mapped).startswith(ESC + "38;5;183morchestrator my-app" + ESC))
        self.assertTrue(self.render("lane", mapped, folder="C--work-other").startswith(ESC + "38;5;250mlane" + ESC))

    def test_folder_map_never_relabels_another_role(self):
        mapped = self.with_map("'C--work-my-app': 'orchestrator my-app'")
        self.assertTrue(self.render("analyst", mapped).startswith(ESC + "38;5;153manalyst" + ESC))

    # ---- model, effort, folder ----

    def test_model_effort_and_folder_segments(self):
        line = self.render_json(self.base(effort={"level": "max"}, model={"display_name": "Sonnet 5"}))
        segs = line.split("  ")
        self.assertEqual(segs[0], ESC + "95;1m\U0001F430 Sonnet 5" + ESC + "0m")
        self.assertEqual(segs[1], ESC + "96mmax" + ESC + "0m")
        self.assertIn(ESC + "94m\U0001F4C1 " + os.path.basename(self.root) + ESC + "0m", segs)

    # ---- context bar ----

    def ctx(self, used, size=None):
        window = {"used_percentage": used}
        if size is not None:
            window["context_window_size"] = size
        line = self.render_json(self.base(context_window=window))
        return line[line.index("ctx ["):]

    def test_the_context_bar_is_green_under_70_yellow_to_89_red_from_90(self):
        for used, colour, filled in ((0, "92", 0), (41, "92", 4), (69.9, "92", 6), (70, "93", 7), (89, "93", 8),
                                     (90, "91", 9), (100, "91", 10)):
            pct = int(used)
            want = ("ctx [" + ESC + colour + "m" + "=" * filled + ESC + "0m" + ESC + "90m" + "." * (10 - filled)
                    + ESC + "0m] %d%%" % pct)
            self.assertEqual(self.ctx(used), want, used)

    def test_the_context_bar_clamps_and_names_the_window_size(self):
        self.assertTrue(self.ctx(150, 200000).endswith("] 100%/200k"))
        self.assertTrue(self.ctx(-5, 1000000).endswith("] 0%/1000k"))
        self.assertTrue(self.render_json(self.base()).endswith("] 0%"))

    # ---- quota ----

    def test_quota_shows_what_is_left_with_its_colour(self):
        line = self.render_json(self.base(rate_limits={"seven_day": {"used_percentage": 32.7},
                                                       "five_hour": {"used_percentage": 10}}))
        self.assertIn(ESC + "92m7d 68% left" + ESC + "0m", line)
        self.assertTrue(line.endswith(ESC + "92m5h 90% left" + ESC + "0m"), line[-40:])
        for used, want in ((60, "93m7d 40% left"), (85, "91m7d 15% left"), (120, "91m7d 0% left")):
            line = self.render_json(self.base(rate_limits={"seven_day": {"used_percentage": used}}))
            self.assertIn(ESC + want + ESC + "0m", line, used)

    def test_the_reset_hint_shows_only_once_the_quota_runs_low(self):
        now = int(time.time())
        def week(used, reset):
            return self.render_json(self.base(rate_limits={"seven_day": {"used_percentage": used, "resets_at": reset}}))
        self.assertIn("7d 40% left 3d" + ESC, week(60, now + 3 * 86400 + 600))
        self.assertIn("7d 40% left 10h" + ESC, week(60, now + 10 * 3600 - 600))
        self.assertIn("7d 41% left" + ESC, week(59, now + 3 * 86400))
        self.assertIn("7d 40% left" + ESC, week(60, now - 3600))

    def test_no_rate_limits_no_quota_segment(self):
        self.assertNotIn("left", self.render_json(self.base()))

    # ---- git ----

    def test_a_folder_outside_any_repository_has_no_git_segment(self):
        line = self.render_json(self.base())
        self.assertNotIn(ESC + "95m", line)
        self.assertNotIn(ESC + "92m+", line)
        self.assertIn(ESC + "94m\U0001F4C1 " + os.path.basename(self.root), line)

    @unittest.skipUnless(shutil.which("git"), "git not on PATH")
    def test_a_repository_shows_branch_dirty_and_lines_against_head(self):
        repo = os.path.join(self.root, "repo")
        os.makedirs(repo)
        def git(*args):
            subprocess.run(["git", "-C", repo, "-c", "user.email=t@example.com", "-c", "user.name=t"] + list(args),
                           check=True, capture_output=True, env=self.env())
        git("init", "-q")
        git("symbolic-ref", "HEAD", "refs/heads/topic")
        # No commit yet: "HEAD", as `git rev-parse --abbrev-ref HEAD` prints for the .ps1.
        self.assertIn(ESC + "95mHEAD" + ESC + "0m  " + ESC + "92m+0", self.render_json(self.base(repo)))
        with open(os.path.join(repo, "a.txt"), "w") as f:
            f.write("one\ntwo\nthree\n")
        git("add", "a.txt")
        git("commit", "-q", "-m", "one")
        clean = self.render_json(self.base(repo))
        self.assertIn(ESC + "95mtopic" + ESC + "0m  " + ESC + "92m+0" + ESC + "0m " + ESC + "91m-0" + ESC + "0m", clean)
        with open(os.path.join(repo, "a.txt"), "w") as f:
            f.write("one\n2\n3\nfour\n")
        dirty = self.render_json(self.base(repo))
        self.assertIn(ESC + "95mtopic" + ESC + "0m" + ESC + "93m*" + ESC + "0m  " + ESC + "92m+3" + ESC + "0m "
                      + ESC + "91m-2" + ESC + "0m", dirty)
        git("checkout", "-q", "--", "a.txt")
        with open(os.path.join(repo, "new.txt"), "w") as f:
            f.write("untracked\n")
        untracked = self.render_json(self.base(repo))
        self.assertIn(ESC + "93m*" + ESC + "0m  " + ESC + "92m+0", untracked)
        git("checkout", "-q", "--detach")
        self.assertIn(ESC + "95mHEAD" + ESC, self.render_json(self.base(repo)))

    # ---- account ----

    def test_the_account_is_the_email_local_part_then_the_companion_file_then_the_config_folder(self):
        self.assertIn(ESC + "33m\u2B50 default" + ESC, self.render_json(self.base()))
        with open(os.path.join(self.home, ".claude.json"), "w") as f:
            f.write('{"oauthAccount": {"emailAddress" : "pat.example@example.com"}}')
        self.assertIn(ESC + "33m\u2B50 pat.example" + ESC, self.render_json(self.base()))
        os.makedirs(os.path.join(self.home, ".claude-accounts"))
        with open(os.path.join(self.home, ".claude-accounts", ".names.json"), "w", encoding="utf-8-sig") as f:
            f.write('{"default": "work", "icons": {"default": "1F3E0", "side": "1F680"}}')
        self.assertIn(ESC + "33m\U0001F3E0 work" + ESC, self.render_json(self.base()))
        side = os.path.join(self.home, ".claude-accounts", "side") + os.sep
        self.assertIn(ESC + "33m\U0001F680 side" + ESC, self.render_json(self.base(), CLAUDE_CONFIG_DIR=side))
        other = os.path.join(self.home, ".claude-accounts", "other")
        self.assertIn(ESC + "33m\U0001F464 other" + ESC, self.render_json(self.base(), CLAUDE_CONFIG_DIR=other))
        local = os.path.join(self.home, ".claude-local")
        self.assertIn(ESC + "92m\U0001F464 local" + ESC, self.render_json(self.base(), CLAUDE_CONFIG_DIR=local))

    # ---- failure ----

    def test_a_payload_that_is_not_json_prints_nothing_and_exits_zero(self):
        for data in (b"not json", b"", b"{\"model\": ", b"\xff\xfe\x00garbage"):
            self.assertEqual(self.run_raw(data), "", data)

    def test_a_payload_of_the_wrong_shape_still_renders(self):
        line = self.run_raw(b'{"model": "Opus", "workspace": [], "context_window": {"used_percentage": "x"},'
                            b' "rate_limits": {"seven_day": {"used_percentage": "lots"}}}')
        self.assertTrue(line.startswith(ESC + "95;1m\U0001F43A " + ESC + "0m"), line[:40])
        self.assertTrue(line.endswith("] 0%"), line[-20:])


if __name__ == "__main__":
    unittest.main(verbosity=1)
