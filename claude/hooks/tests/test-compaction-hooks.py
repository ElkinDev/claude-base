"""Tests for the compaction hooks: precompact-checkpoint.py, postcompact-persist.py and the
checkpoint part of compact-recover.py.

Every case runs the hook the way the harness runs it, as a subprocess with the payload JSON
on stdin, against a throwaway git repository and a synthetic transcript in a temp folder.
CLAUDE_CHECKPOINT_DIR points at that folder, so nothing under ~/.claude is touched. No
model runs and nothing touches the network.

    python test-compaction-hooks.py
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.dirname(HERE)
PRE = os.path.join(HOOKS, "precompact-checkpoint.py")
POST = os.path.join(HOOKS, "postcompact-persist.py")
RECOVER = os.path.join(HOOKS, "compact-recover.py")
SESSION = "11111111-2222-3333-4444-555555555555"


def git(repo, *args):
    subprocess.run(["git", "-C", repo, "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


class CompactionHooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compaction-hooks-")
        self.ckpt = os.path.join(self.tmp, "ckpt")
        # a repository with one commit and one untracked file
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "tests@example.com")
        git(self.repo, "config", "user.name", "tests")
        with open(os.path.join(self.repo, "a.txt"), "w", encoding="utf-8") as handle:
            handle.write("a\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "first commit")
        with open(os.path.join(self.repo, "dirty.txt"), "w", encoding="utf-8") as handle:
            handle.write("not committed\n")
        # a transcript folder shaped like ~/.claude/projects/<project>/
        self.project = os.path.join(self.tmp, "projects", "C--tmp-repo")
        os.makedirs(self.project)
        self.transcript = os.path.join(self.project, f"{SESSION}.jsonl")
        rows = [
            {"type": "user", "timestamp": "2026-01-01T10:00:00Z", "message": {"role": "user", "content": "please build the widget"}},
            {"type": "assistant", "timestamp": "2026-01-01T10:00:05Z", "message": {"role": "assistant", "id": "m1", "content": [{"type": "text", "text": "Building the widget now, tip is abc1234."}], "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 5000, "output_tokens": 20}}},
            {"type": "user", "timestamp": "2026-01-01T10:01:00Z", "isMeta": True, "message": {"role": "user", "content": "Called the Read tool with the following input"}},
        ]
        with open(self.transcript, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        sub = os.path.join(self.project, SESSION, "subagents")
        os.makedirs(sub)
        with open(os.path.join(sub, "agent-abc123.meta.json"), "w", encoding="utf-8") as handle:
            json.dump({"agentType": "reviewer", "description": "Review the widget branch", "model": "test-model"}, handle)
        with open(os.path.join(sub, "agent-abc123.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Disposition: CLEAR, nothing blocks the merge."}]}}) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, **extra):
        env = os.environ.copy()
        for name in list(env):
            if name.startswith("CLAUDE_CHECKPOINT_"):
                env.pop(name)
        env["CLAUDE_CHECKPOINT_DIR"] = self.ckpt
        for name, value in extra.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        return env

    def payload(self, **over):
        data = {"session_id": SESSION, "transcript_path": self.transcript, "cwd": self.repo, "hook_event_name": "PreCompact", "trigger": "auto", "custom_instructions": ""}
        data.update(over)
        return data

    def run_hook(self, script, data, env=None, raw=None):
        stdin = raw if raw is not None else json.dumps(data)
        proc = subprocess.run([sys.executable, script], input=stdin, capture_output=True, text=True, encoding="utf-8", env=env or self.env(), timeout=60)
        return proc.returncode, proc.stdout, proc.stderr

    def checkpoints(self):
        return [p for p in sorted(glob.glob(os.path.join(self.ckpt, "*.md"))) if not p.endswith("-summary.md")]

    # ---------------------------------------------------------------- precompact

    def test_precompact_writes_checkpoint_and_prints_instructions(self):
        code, out, err = self.run_hook(PRE, self.payload())
        self.assertEqual(code, 0, err)
        files = self.checkpoints()
        self.assertEqual(len(files), 1)
        self.assertIn("checkpoint of the disk state was written to", out)
        self.assertIn(files[0], out)
        self.assertIn("verbatim", out)
        with open(files[0], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("## Disk truth", text)
        self.assertIn("first commit", text)
        self.assertIn("1 uncommitted", text)
        self.assertIn("?? dirty.txt", text)
        self.assertIn("## Subagents of this session", text)
        self.assertIn("abc123 reviewer: Review the widget branch", text)
        self.assertIn("last words: Disposition: CLEAR", text)
        self.assertIn("## Last words before compaction", text)
        self.assertIn("- user: please build the widget", text)
        self.assertIn("- assistant: Building the widget now, tip is abc1234.", text)
        self.assertNotIn("Called the Read tool", text)
        self.assertIn("trigger: auto", text)

    def test_precompact_echoes_user_instructions_first(self):
        code, out, _ = self.run_hook(PRE, self.payload(custom_instructions="keep the migration plan"))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("User instructions for this compaction: keep the migration plan "))

    def test_precompact_quiet_writes_but_prints_nothing(self):
        code, out, _ = self.run_hook(PRE, self.payload(), env=self.env(CLAUDE_CHECKPOINT_QUIET="1"))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertEqual(len(self.checkpoints()), 1)

    def test_precompact_skips_subagent_compactions(self):
        sub_transcript = os.path.join(self.project, SESSION, "subagents", "agent-abc123.jsonl")
        code, out, _ = self.run_hook(PRE, self.payload(transcript_path=sub_transcript))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        # the real shape on 2.1.248: agent_id and agent_type set, transcript_path is the parent's
        code, out, _ = self.run_hook(PRE, self.payload(agent_id="abc123", agent_type="reviewer"))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertFalse(os.path.isdir(self.ckpt))

    def test_precompact_subagent_opt_in_tags_the_file_and_reads_its_own_transcript(self):
        env = self.env(CLAUDE_CHECKPOINT_SUBAGENTS="1")
        code, out, _ = self.run_hook(PRE, self.payload(agent_id="abc123", agent_type="reviewer"), env=env)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")  # the harness ignores hook stdout for a subagent's summary
        files = self.checkpoints()
        self.assertEqual(len(files), 1)
        self.assertIn(f"-{SESSION[:8]}-agent-abc123-auto.md", files[0])
        with open(files[0], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("agent: abc123 (reviewer), a subagent of the session", text)
        self.assertIn("- assistant: Disposition: CLEAR, nothing blocks the merge.", text)
        self.assertNotIn("- assistant: Building the widget now", text)
        # the session's own checkpoint ignores the tagged file when the summary looks for it
        code, _, _ = self.run_hook(POST, self.payload(hook_event_name="PostCompact", compact_summary="main summary"), env=env)
        self.assertEqual(code, 0)
        summaries = glob.glob(os.path.join(self.ckpt, "*-summary.md"))
        self.assertEqual(len(summaries), 1)
        with open(summaries[0], encoding="utf-8") as handle:
            self.assertIn("checkpoint: (none found)", handle.read())

    def test_precompact_cwd_without_repo_lists_child_repositories(self):
        code, _, _ = self.run_hook(PRE, self.payload(cwd=self.tmp))
        self.assertEqual(code, 0)
        with open(self.checkpoints()[0], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(os.path.join(self.tmp, "repo"), text)
        self.assertIn("?? dirty.txt", text)

    def test_precompact_prunes_old_checkpoints(self):
        os.makedirs(self.ckpt)
        for n in range(45):
            with open(os.path.join(self.ckpt, f"20250101-{n:06d}-{SESSION[:8]}-auto.md"), "w", encoding="utf-8") as handle:
                handle.write("old\n")
        code, _, _ = self.run_hook(PRE, self.payload(), env=self.env(CLAUDE_CHECKPOINT_KEEP="10"))
        self.assertEqual(code, 0)
        files = self.checkpoints()
        self.assertEqual(len(files), 10)
        self.assertTrue(os.path.basename(files[-1]).startswith("2026") or os.path.basename(files[-1]) > "20250101-000044")

    def test_precompact_never_fails_on_garbage_stdin(self):
        code, out, _ = self.run_hook(PRE, None, raw="this is not json")
        self.assertEqual(code, 0)
        self.assertIn("checkpoint", out.lower())

    def test_precompact_custom_instruction_file(self):
        template = os.path.join(self.tmp, "instructions.txt")
        with open(template, "w", encoding="utf-8") as handle:
            handle.write("Keep only the next step. Checkpoint: {path}")
        code, out, _ = self.run_hook(PRE, self.payload(), env=self.env(CLAUDE_CHECKPOINT_INSTRUCTIONS=template))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Keep only the next step. Checkpoint: "))
        self.assertIn(self.checkpoints()[0], out)

    # ---------------------------------------------------------------- postcompact

    def test_postcompact_saves_summary_next_to_checkpoint(self):
        self.run_hook(PRE, self.payload())
        checkpoint = self.checkpoints()[0]
        data = self.payload(hook_event_name="PostCompact", compact_summary="1. Next step: run the tests.\n2. Tip abc1234.")
        code, out, err = self.run_hook(POST, data)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")
        summaries = glob.glob(os.path.join(self.ckpt, "*-summary.md"))
        self.assertEqual(len(summaries), 1)
        with open(summaries[0], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(f"checkpoint: {checkpoint}", text)
        self.assertIn("summary characters: 44", text)
        self.assertIn("1. Next step: run the tests.", text)

    def test_postcompact_ignores_empty_summary_and_subagents(self):
        code, out, _ = self.run_hook(POST, self.payload(hook_event_name="PostCompact", compact_summary="  "))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        sub_transcript = os.path.join(self.project, SESSION, "subagents", "agent-abc123.jsonl")
        code, out, _ = self.run_hook(POST, self.payload(hook_event_name="PostCompact", transcript_path=sub_transcript, compact_summary="text"))
        self.assertEqual(code, 0)
        code, out, _ = self.run_hook(POST, self.payload(hook_event_name="PostCompact", agent_id="abc123", agent_type="reviewer", compact_summary="text"))
        self.assertEqual(code, 0)
        self.assertEqual(glob.glob(os.path.join(self.ckpt, "*")), [])

    def test_postcompact_subagent_opt_in_pairs_with_the_agent_checkpoint(self):
        env = self.env(CLAUDE_CHECKPOINT_SUBAGENTS="1")
        self.run_hook(PRE, self.payload(agent_id="abc123", agent_type="reviewer"), env=env)
        checkpoint = self.checkpoints()[0]
        code, out, _ = self.run_hook(POST, self.payload(hook_event_name="PostCompact", agent_id="abc123", agent_type="reviewer", compact_summary="agent summary"), env=env)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        summaries = glob.glob(os.path.join(self.ckpt, "*-agent-abc123-summary.md"))
        self.assertEqual(len(summaries), 1)
        with open(summaries[0], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("agent: abc123 (reviewer), a subagent of the session", text)
        self.assertIn(f"checkpoint: {checkpoint}", text)

    # ---------------------------------------------------------------- recovery

    def test_recover_points_at_the_newest_checkpoint(self):
        self.run_hook(PRE, self.payload())
        checkpoint = self.checkpoints()[0]
        data = {"session_id": SESSION, "transcript_path": self.transcript, "cwd": self.repo, "hook_event_name": "SessionStart", "source": "compact"}
        code, out, err = self.run_hook(RECOVER, data)
        self.assertEqual(code, 0, err)
        self.assertIn(f"Checkpoint written just before this compaction: {checkpoint}", out)
        self.assertIn("## Disk truth", out)
        self.assertIn("?? dirty.txt", out)
        self.assertLessEqual(len(out), 4000 + len("\n[recovery output capped]"))

    def test_recover_without_checkpoint_keeps_the_old_behaviour(self):
        data = {"session_id": SESSION, "transcript_path": self.transcript, "cwd": self.repo, "hook_event_name": "SessionStart", "source": "compact"}
        code, out, _ = self.run_hook(RECOVER, data)
        self.assertEqual(code, 0)
        self.assertNotIn("Checkpoint written", out)
        self.assertIn("compaction recovery", out)

    def test_recover_ignores_summary_files_and_other_sessions(self):
        os.makedirs(self.ckpt)
        for name in (f"20260101-120000-{SESSION[:8]}-summary.md", "20260101-120000-99999999-auto.md"):
            with open(os.path.join(self.ckpt, name), "w", encoding="utf-8") as handle:
                handle.write("## Disk truth\n- other\n")
        data = {"session_id": SESSION, "transcript_path": self.transcript, "cwd": self.repo, "hook_event_name": "SessionStart", "source": "compact"}
        code, out, _ = self.run_hook(RECOVER, data)
        self.assertEqual(code, 0)
        self.assertNotIn("Checkpoint written", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
