#!/usr/bin/env python3
"""Tests for claude/tools/shell-output-by-family.py.

Run:
    python test-shell-output-by-family.py

The report is run the way a session runs it, as a subprocess against a projects directory
built in a temporary folder: one main transcript, one lane transcript under subagents, one
result outside the date window. The family classifier is imported and held to a table.
Nothing under the user's home is read and nothing outlives its test.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, "claude", "tools", "shell-output-by-family.py")


def load_module():
    spec = importlib.util.spec_from_file_location("shell_output_by_family", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def use_row(tool_id, command, tool="Bash"):
    return {"type": "assistant", "timestamp": "2026-03-02T10:00:00.000Z",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_id, "name": tool, "input": {"command": command}}]}}


def result_row(tool_id, content, stamp="2026-03-02T10:00:01.000Z"):
    return {"type": "user", "timestamp": stamp,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": content}]}}


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


class FamilyTable(unittest.TestCase):
    def test_families(self):
        fam = load_module().family
        table = {
            "cd /repo && git diff --stat": "git diff",
            "git -C /repo log --oneline": "git log",
            "JAVA_HOME=/j ./gradlew :app:test": "gradle",
            "python3 scripts/x.py": "python",
            "if [ -f x ]; then echo y; fi": "shell-ctl",
            "sed -n '1,5p' f.md": "sed",
            "Get-ChildItem -Recurse": "get-childitem",
            "   ": "(empty)",
            "C:/tools/rg.exe pattern": "rg.exe",
            "echo \"== a\"; git diff --stat": "git diff",
            "R=/x; echo ok; sed -n 1p f": "sed",
            "date \"+%H:%M:%S\"; herdr agent get w4:p1": "herdr",
            "echo \"a; b\"; ls": "ls",
            "echo hello": "echo",
        }
        for command, expected in table.items():
            self.assertEqual(fam(command), expected, command)


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sobf-")
        project = os.path.join(self.tmp, "C--repo")
        write_jsonl(os.path.join(project, "aaaa.jsonl"), [
            use_row("t1", "cd /x && git diff --stat"),
            result_row("t1", "diff text"),
            use_row("t2", "sed -n '1,5p' f.md"),
            result_row("t2", [{"type": "text", "text": "x" * 100}]),
            use_row("t3", "cat big.md"),
            result_row("t3", "y" * 50, stamp="2025-01-01T00:00:00.000Z"),
        ])
        write_jsonl(os.path.join(project, "aaaa", "subagents", "agent-1.jsonl"), [
            use_row("t4", "Get-ChildItem", tool="PowerShell"),
            result_row("t4", "z" * 30),
        ])
        self.project = project

    def tearDown(self):
        for folder, _dirs, names in os.walk(self.tmp, topdown=False):
            for name in names:
                os.remove(os.path.join(folder, name))
            os.rmdir(folder)

    def run_report(self, *extra):
        cmd = [sys.executable, SCRIPT, "--projects", self.tmp, "--since", "2026-03-01", "--until", "2026-03-03"] + list(extra)
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")

    def test_window_and_classes(self):
        done = self.run_report()
        self.assertEqual(done.returncode, 0, done.stderr)
        out = done.stdout
        self.assertIn("2 transcript files", out)
        self.assertIn("== main: 2 results, 109 chars", out)
        self.assertIn("== lanes: 1 results, 30 chars", out)
        self.assertIn("== other: 0 results, 0 chars", out)
        self.assertRegex(out, r"\n\s+100\s+1\s+100\s+91%\s+sed\n")
        self.assertRegex(out, r"\n\s+9\s+1\s+9\s+8%\s+git diff\n")
        self.assertRegex(out, r"\n\s+30\s+1\s+30\s+100%\s+get-childitem\n")
        self.assertNotIn("cat", out)
        self.assertIn("sed -n '1,5p' f.md  [aaaa.jsonl]", out)

    def test_project_filter_and_bad_date(self):
        done = self.run_report("--project", "C--repo")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("== main: 2 results", done.stdout)
        bad = subprocess.run([sys.executable, SCRIPT, "--projects", self.tmp, "--since", "yesterday"],
                             capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("bad --since", bad.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
