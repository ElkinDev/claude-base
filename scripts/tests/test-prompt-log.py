#!/usr/bin/env python3
"""Tests for claude/hooks/prompt-log.py: the line every typed prompt appends on submit, the
prompts it refuses to log, the caps on an entry and on the file, and the block it prints back
at a compaction.

Run:
    python test-prompt-log.py

The hook runs the way the harness runs it, as a subprocess with the payload JSON on stdin and
CLAUDE_CHECKPOINT_DIR pointed at a temp folder, so no real checkpoint folder is touched.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
HOOK = os.path.join(ROOT, "claude", "hooks", "prompt-log.py")
SESSION = "abcdef12-3456-7890-abcd-ef1234567890"
STAMP = re.compile(r"^- \d{4}-\d{2}-\d{2} \d{2}:\d{2} ")
HEADER = "Last typed prompts of this session (owner or the analyst relay), verbatim, file "
ENTRY_CAP = 1500
FILE_CAP = 200_000
BLOCK_CAP = 6000
BOM = b"\xef\xbb\xbf"


def run_hook(payload, checkpoint_dir, args=()):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["CLAUDE_CHECKPOINT_DIR"] = checkpoint_dir
    process = subprocess.run(
        [sys.executable, HOOK] + list(args),
        input=payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return (process.returncode,
            process.stdout.decode("utf-8", "replace"),
            process.stderr.decode("utf-8", "replace"))


class PromptLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prompt-log-")
        self.checkpoints = os.path.join(self.tmp, "cp")
        self.transcript = os.path.join(self.tmp, "proj", "x.jsonl")
        self.log = os.path.join(self.checkpoints, "abcdef12-prompts.md")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def payload(self, prompt, **extra):
        data = {
            "session_id": SESSION,
            "transcript_path": self.transcript,
            "cwd": self.tmp,
            "prompt": prompt,
        }
        data.update(extra)
        return data

    def append(self, prompt, **extra):
        code, out, err = run_hook(self.payload(prompt, **extra), self.checkpoints)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "", "a UserPromptSubmit hook that prints turns its output into context")
        return out

    def lines(self):
        with open(self.log, encoding="utf-8") as handle:
            return [line for line in handle.read().split("\n") if line]

    def text_of(self, line):
        return line.split(" ", 3)[3]

    # --------------------------------------------------------------- one prompt, one line
    def test_a_prompt_appends_one_stamped_line_and_creates_the_folder(self):
        self.append("  hola\n  dos lineas  ")
        self.assertTrue(os.path.isdir(self.checkpoints), "the checkpoint folder is created")
        lines = self.lines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], STAMP)
        self.assertEqual(self.text_of(lines[0]), "hola dos lineas")

    def test_the_file_is_named_after_the_first_eight_characters_of_the_session(self):
        self.append("uno")
        self.assertEqual(os.listdir(self.checkpoints), ["abcdef12-prompts.md"])

    def test_prompts_accumulate_in_order(self):
        self.append("uno")
        self.append("dos")
        self.assertEqual([self.text_of(line) for line in self.lines()], ["uno", "dos"])

    # --------------------------------------------------------------- what is never logged
    def test_nothing_is_written_for_the_skipped_prompts(self):
        for prompt in ["", "   ", "\n\t ",
                       "/compact",
                       "/context and the rest",
                       "<task-notification>agent x finished</task-notification>",
                       "<system-reminder>do not forget</system-reminder>",
                       "<local-command-stdout>output</local-command-stdout>"]:
            self.append(prompt)
            self.assertFalse(os.path.exists(self.log), "logged a prompt it must skip: %r" % prompt)

    def test_a_subagent_prompt_is_never_logged(self):
        self.append("work on the lane", agent_id="9f3c21", agent_type="implementer-light")
        self.assertFalse(os.path.exists(self.log))

    # --------------------------------------------------------------- the caps
    def test_a_long_prompt_is_cut_at_the_cap_with_the_marker_inside_it(self):
        self.append("x" * 4000)
        text = self.text_of(self.lines()[0])
        self.assertEqual(len(text), ENTRY_CAP)
        self.assertTrue(text.endswith(" [cut]"), text[-20:])
        self.assertEqual(text, "x" * (ENTRY_CAP - len(" [cut]")) + " [cut]")

    def test_a_prompt_at_the_cap_is_left_whole(self):
        self.append("y" * ENTRY_CAP)
        self.assertEqual(self.text_of(self.lines()[0]), "y" * ENTRY_CAP)

    def test_the_file_is_trimmed_to_the_last_bytes_keeping_whole_lines(self):
        os.makedirs(self.checkpoints, exist_ok=True)
        old = "".join("- 2026-09-01 10:00 %s\n" % ("z" * 900) for _ in range(300))
        with open(self.log, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(old)
        self.assertGreater(os.path.getsize(self.log), FILE_CAP)
        self.append("la ultima")
        self.assertLessEqual(os.path.getsize(self.log), FILE_CAP)
        lines = self.lines()
        for line in lines:
            self.assertRegex(line, STAMP, "a partial line survived the trim")
        self.assertEqual(self.text_of(lines[-1]), "la ultima")

    def test_a_tail_without_a_line_boundary_keeps_the_prompt_just_written(self):
        """A log whose last FILE_CAP bytes hold no earlier newline: the only boundary is the
        terminator of the line just appended, so cutting at it would empty the file."""
        os.makedirs(self.checkpoints, exist_ok=True)
        with open(self.log, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("z" * 300_000)
        self.append("la ultima")
        size = os.path.getsize(self.log)
        self.assertGreater(size, 0, "the trim emptied the log")
        self.assertLessEqual(size, FILE_CAP)
        with open(self.log, encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(text.rstrip("\n").endswith("la ultima"), text[-60:])
        code, out, err = run_hook(self.payload(""), self.checkpoints, args=["--recover"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("No prompt log", out, "the trim lost the whole log")

    # --------------------------------------------------------------- the BOM PowerShell pipes
    def test_a_payload_behind_a_utf8_bom_is_still_logged(self):
        raw = BOM + json.dumps(self.payload("con bom")).encode("utf-8")
        code, out, err = run_hook(raw, self.checkpoints)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")
        self.assertEqual(self.text_of(self.lines()[0]), "con bom")

    def test_recover_reads_a_payload_behind_a_utf8_bom(self):
        self.append("primera")
        raw = BOM + json.dumps(self.payload("")).encode("utf-8")
        code, out, err = run_hook(raw, self.checkpoints, args=["--recover"])
        self.assertEqual(code, 0, err)
        lines = [line for line in out.split("\n") if line]
        self.assertEqual(lines[0], HEADER + self.log + ":")
        self.assertEqual(self.text_of(lines[1]), "primera")

    # --------------------------------------------------------------- reading it back
    def test_recover_prints_the_last_entries_under_the_header(self):
        for index in range(14):
            self.append("prompt numero %d" % index)
        code, out, err = run_hook(self.payload(""), self.checkpoints, args=["--recover"])
        self.assertEqual(code, 0, err)
        lines = [line for line in out.split("\n") if line]
        self.assertEqual(lines[0], HEADER + self.log + ":")
        self.assertEqual(len(lines) - 1, 10, "at most the last ten entries")
        self.assertEqual(self.text_of(lines[1]), "prompt numero 4")
        self.assertEqual(self.text_of(lines[-1]), "prompt numero 13")

    def test_recover_drops_the_oldest_entries_until_the_block_fits(self):
        for index in range(10):
            self.append("%d %s" % (index, "w" * 1200))
        code, out, err = run_hook(self.payload(""), self.checkpoints, args=["--recover"])
        self.assertEqual(code, 0, err)
        self.assertLessEqual(len(out), BLOCK_CAP)
        lines = [line for line in out.split("\n") if line]
        self.assertEqual(lines[0], HEADER + self.log + ":")
        for line in lines[1:]:
            self.assertRegex(line, STAMP)
            self.assertEqual(len(self.text_of(line)), 1 + 1 + 1200, "entries are printed whole")
        self.assertEqual(self.text_of(lines[-1]).split(" ")[0], "9", "the newest entry is kept")
        self.assertGreater(len(lines), 1, "at least one entry still fits")

    def test_recover_without_a_file_says_so_in_one_line(self):
        code, out, err = run_hook(self.payload(""), self.checkpoints, args=["--recover"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "No prompt log for this session at %s." % self.log)
        self.assertEqual(len([line for line in out.split("\n") if line]), 1)
        self.assertFalse(os.path.exists(self.log), "--recover writes nothing")

    # --------------------------------------------------------------- never blocks a prompt
    def test_malformed_stdin_exits_zero_and_prints_nothing(self):
        for raw in [b"", b"not json at all", b"{", b'{"prompt": "hola"}', b'{"session_id": null}']:
            code, out, err = run_hook(raw, self.checkpoints)
            self.assertEqual(code, 0, err)
            self.assertEqual(out, "", "printed on a malformed payload: %r" % raw)

    def test_malformed_stdin_in_recover_mode_exits_zero(self):
        code, out, err = run_hook(b"not json", self.checkpoints, args=["--recover"])
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    unittest.main(verbosity=2)
