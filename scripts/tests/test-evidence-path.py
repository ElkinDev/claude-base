"""Tests for scripts/evidence-path.py:
python scripts/tests/test-evidence-path.py   (needs git on PATH)"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "evidence-path.py")
spec = importlib.util.spec_from_file_location("evidence_path", SCRIPT)
resolver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolver)


def run_git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)


class EvidencePathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="evidence-path-")
        self.parent = os.path.join(self.tmp, "workspace")
        self.repo = os.path.join(self.parent, "sample-repo")
        os.makedirs(self.repo)
        self.assertEqual(run_git(self.repo, "init", "-q").returncode, 0)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.saved_env = os.environ.pop(resolver.ENV_VAR, None)

    def tearDown(self):
        if self.saved_env is None:
            os.environ.pop(resolver.ENV_VAR, None)
        else:
            os.environ[resolver.ENV_VAR] = self.saved_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers -------------------------------------------------------------

    def profile(self, name, *lines):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def cli(self, *args, **kwargs):
        env = dict(os.environ)
        env.update(kwargs.pop("env", {}))
        cwd = kwargs.pop("cwd", self.repo)
        return subprocess.run([sys.executable, SCRIPT] + list(args),
                              capture_output=True, text=True, cwd=cwd, env=env)

    def assertSamePath(self, produced, expected):
        self.assertEqual(os.path.normcase(os.path.normpath(str(produced))),
                         os.path.normcase(os.path.normpath(str(expected))))

    # the default ---------------------------------------------------------

    def test_the_default_root_sits_beside_the_repository(self):
        spec_used, source = resolver.find_spec(self.repo)
        self.assertEqual(spec_used, resolver.DEFAULT_SPEC)
        self.assertEqual(source, "default")
        self.assertSamePath(resolver.resolve(spec_used, self.repo),
                            os.path.join(self.parent, "evidence"))

    def test_the_default_root_is_found_from_inside_the_repository(self):
        deep = os.path.join(self.repo, "src", "deep")
        os.makedirs(deep)
        proc = self.cli(cwd=deep)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertSamePath(os.path.realpath(proc.stdout.strip()),
                            os.path.realpath(os.path.join(self.parent, "evidence")))

    def test_outside_a_repository_the_folder_at_hand_is_the_repository(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        proc = self.cli(cwd=plain)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertSamePath(os.path.realpath(proc.stdout.strip()),
                            os.path.realpath(os.path.join(self.tmp, "evidence")))

    # the grammar ---------------------------------------------------------

    def test_every_token_resolves(self):
        cases = {
            "{repo_parent}/evidence": os.path.join(self.parent, "evidence"),
            "{repo}/evidence": os.path.join(self.repo, "evidence"),
            "{repo_parent}/{repo_name}-evidence": os.path.join(self.parent, "sample-repo-evidence"),
            "{home}/evidence": os.path.join(self.home, "evidence"),
            "{home}/evidence/{project}": os.path.join(self.home, "evidence", "the-project"),
        }
        for text, expected in cases.items():
            got = resolver.resolve(text, self.repo, home=self.home, project="the-project")
            self.assertSamePath(got, expected)

    def test_the_project_token_falls_back_to_the_repository_name(self):
        self.assertSamePath(resolver.resolve("{home}/evidence/{project}", self.repo, home=self.home),
                            os.path.join(self.home, "evidence", "sample-repo"))

    def test_the_project_token_reads_the_identity_section_and_ignores_a_placeholder(self):
        self.profile("CLAUDE.project.md", "## Identity", "- Project name: <name>")
        self.assertIsNone(resolver.find_project(self.repo))
        self.profile("CLAUDE.project.md", "## Identity", "- Project name: sample-service")
        self.assertEqual(resolver.find_project(self.repo), "sample-service")

    def test_a_tilde_expands_to_the_home_folder(self):
        self.assertSamePath(resolver.resolve("~/evidence", self.repo, home=self.home),
                            os.path.join(self.home, "evidence"))
        self.assertSamePath(resolver.resolve("~", self.repo, home=self.home), self.home)

    def test_an_unknown_token_is_a_spec_error(self):
        with self.assertRaises(resolver.SpecError):
            resolver.resolve("{repo_parent}/{tickets}", self.repo)

    def test_an_unknown_token_exits_two_on_the_command_line(self):
        proc = self.cli("--spec", "{nope}/evidence")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("nope", proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertEqual(len(proc.stderr.strip().splitlines()), 1)

    def test_a_spec_with_forward_slashes_resolves_on_this_os(self):
        proc = self.cli("--spec", "{repo_parent}/evidence/nested/deep")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertSamePath(proc.stdout.strip(),
                            os.path.join(self.parent, "evidence", "nested", "deep"))

    def test_the_output_is_one_native_line_with_no_braces_and_no_trailing_separator(self):
        proc = self.cli("--id", "1234")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        line = proc.stdout.rstrip("\r\n")
        self.assertEqual(len(proc.stdout.strip().splitlines()), 1)
        self.assertNotIn("{", line)
        self.assertNotIn("}", line)
        self.assertTrue(os.path.isabs(line), line)
        self.assertFalse(line.endswith(os.sep))
        self.assertIn(os.sep, line)
        if os.sep == "\\":
            self.assertNotIn("/", line)

    # precedence ----------------------------------------------------------

    def test_the_profile_line_beats_the_default(self):
        self.profile("CLAUDE.project.md", "## Evidence", "- Evidence root: `{home}/evidence`")
        found, source = resolver.find_spec(self.repo)
        self.assertEqual(found, "{home}/evidence")  # backticks stripped
        self.assertEqual(source, "CLAUDE.project.md")

    def test_the_local_profile_beats_the_committed_one(self):
        self.profile("CLAUDE.project.md", "- Evidence root: {repo_parent}/team-evidence")
        self.profile("CLAUDE.local.md", "- Evidence root: {home}/my-evidence")
        found, source = resolver.find_spec(self.repo)
        self.assertEqual(found, "{home}/my-evidence")
        self.assertEqual(source, "CLAUDE.local.md")

    def test_the_environment_variable_beats_the_profile_line(self):
        self.profile("CLAUDE.local.md", "- Evidence root: {home}/my-evidence")
        os.environ[resolver.ENV_VAR] = "{repo_parent}/from-the-environment"
        found, source = resolver.find_spec(self.repo)
        self.assertEqual(found, "{repo_parent}/from-the-environment")
        self.assertEqual(source, resolver.ENV_VAR)
        proc = self.cli(env={resolver.ENV_VAR: "{repo_parent}/from-the-environment"})
        self.assertSamePath(proc.stdout.strip(), os.path.join(self.parent, "from-the-environment"))

    def test_the_spec_argument_beats_everything(self):
        self.profile("CLAUDE.local.md", "- Evidence root: {home}/my-evidence")
        proc = self.cli("--spec", "{repo_parent}/chosen", "--print-spec",
                        env={resolver.ENV_VAR: "{home}/from-the-environment"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.strip().splitlines()
        self.assertIn("--spec", lines[0])
        self.assertSamePath(lines[1], os.path.join(self.parent, "chosen"))

    def test_print_spec_names_the_source(self):
        self.profile("CLAUDE.project.md", "- Evidence root: {repo_parent}/evidence")
        proc = self.cli("--print-spec")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("CLAUDE.project.md", proc.stdout.splitlines()[0])

    # the folders ---------------------------------------------------------

    def test_id_and_mockups_append_their_folder(self):
        item = self.cli("--id", "1234").stdout.strip()
        self.assertSamePath(item, os.path.join(self.parent, "evidence", "1234"))
        key = self.cli("--id", "PROJ-77").stdout.strip()
        self.assertSamePath(key, os.path.join(self.parent, "evidence", "PROJ-77"))
        mockups = self.cli("--mockups").stdout.strip()
        self.assertSamePath(mockups, os.path.join(self.parent, "evidence", "mockups"))

    def test_create_makes_the_folder(self):
        target = os.path.join(self.parent, "evidence", "1234")
        self.assertFalse(os.path.isdir(target))
        proc = self.cli("--id", "1234", "--create")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isdir(target))
        self.assertEqual(self.cli("--id", "1234", "--create").returncode, 0)  # idempotent
        self.assertEqual(self.cli("--mockups", "--create").returncode, 0)
        self.assertTrue(os.path.isdir(os.path.join(self.parent, "evidence", "mockups")))

    def test_nothing_is_created_without_the_create_flag(self):
        proc = self.cli("--id", "1234")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.parent, "evidence")))


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1).result.wasSuccessful() is False)
