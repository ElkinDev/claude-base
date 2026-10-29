"""Tests for claude/hooks/long-run.py. The hook runs as the harness runs it, a subprocess with a payload on stdin,
against fixture transcripts in a temporary folder; LONG_RUN_DIR puts its state and log there too.

    python -I -B claude/hooks/tests/test-long-run.py
    LONG_RUN_PY=<path> python -I -B claude/hooks/tests/test-long-run.py      # against a staged copy (mutants)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.environ.get("LONG_RUN_PY") or os.path.join(os.path.dirname(HERE), "long-run.py")
SESSION = "11111111-2222-3333-4444-555555555555"
AGENT = "a0123456789abcdef"
NOTICE = ("Long-run rule: your last turn carried %d k tokens of context, and every call re-reads it; past 200k a "
          "fresh agent started from a checkpoint report is cheaper. "
          "At the next clean point (a commit, a finished step, a detached job with an exit file), write your report "
          "as a checkpoint: what is done, the state, the next step, any exit file. Then end. Before you end: if your "
          "task is a review or a single answer, give that verdict or answer first, in full; release any lock you "
          "hold; leave no staged change, rebase or merge in progress. The parent continues with a fresh agent from "
          "that report. Go on only if fewer than 10 calls remain.")
GUARD = ("Before you end: if your task is a review or a single answer, give that verdict or answer first, in full; "
         "release any lock you hold; leave no staged change, rebase or merge in progress.")


def line(rec):
    return json.dumps(rec, separators=(",", ":")) + "\n"


def assistant(context):
    """An assistant tool_use record of a subagent whose turn carried `context` tokens."""
    rec = {"parentUuid": None, "isSidechain": True, "agentId": AGENT, "type": "assistant",
           "uuid": str(uuid.uuid4()), "timestamp": "2026-10-23T11:00:01.000Z",
           "message": {"id": "msg_" + uuid.uuid4().hex[:8], "role": "assistant", "type": "message",
                       "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}],
                       "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1000,
                                 "cache_read_input_tokens": context - 1002, "output_tokens": 50}}}
    return line(rec)


def result(size=10):
    return line({"isSidechain": True, "type": "user", "uuid": str(uuid.uuid4()),
                 "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                                          "content": "x" * size}]}})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="long run ")
        self.proj = os.path.join(self.tmp, "projects", "C--work-Fixture")
        self.main = os.path.join(self.proj, SESSION + ".jsonl")
        self.sub = os.path.join(self.proj, SESSION, "subagents", "agent-%s.jsonl" % AGENT)
        os.makedirs(os.path.dirname(self.sub))
        open(self.main, "w").close()
        open(self.sub, "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def turn(self, context, path=None):
        with open(path or self.sub, "a", encoding="utf-8") as fh:
            fh.write(result())
            fh.write(assistant(context))

    def payload(self, **extra):
        data = {"session_id": SESSION, "transcript_path": self.main, "tool_name": "Bash",
                "hook_event_name": "PostToolUse", "agent_id": AGENT, "agent_type": "implementer",
                "agent_transcript_path": self.sub}
        data.update(extra)
        return {k: v for k, v in data.items() if v is not None}

    def run_hook(self, payload=None, raw=None):
        payload = self.payload() if payload is None else payload
        env = dict(os.environ, LONG_RUN_DIR=self.tmp)
        data = raw if raw is not None else json.dumps(payload).encode("utf-8")
        proc = subprocess.run([sys.executable, "-I", HOOK], input=data, capture_output=True, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.decode("utf-8")

    def assertNotice(self, out, k):
        self.assertEqual(json.loads(out), {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                                  "additionalContext": NOTICE % k}})

    def log_lines(self):
        try:
            with open(os.path.join(self.tmp, "long-run.log"), encoding="utf-8") as fh:
                return fh.read().splitlines()
        except OSError:
            return []


class LongRun(Base):
    def test_main_session_nothing(self):
        self.turn(400000)
        main = {"session_id": SESSION, "transcript_path": self.sub, "tool_name": "Bash"}
        self.assertEqual(self.run_hook(main), "")
        self.assertEqual(self.run_hook(dict(main, agent_type="")), "")
        self.assertEqual(self.log_lines(), [])

    def test_150k_nothing(self):
        self.turn(150000)
        self.assertEqual(self.run_hook(), "")

    def test_levels_once_each(self):
        self.turn(150000)
        self.assertEqual(self.run_hook(), "")
        self.turn(210000)
        self.assertNotice(self.run_hook(), 210)
        self.assertEqual(self.run_hook(), "")           # parallel tools in the same turn
        self.turn(220000)
        self.assertEqual(self.run_hook(), "")
        self.turn(310000)
        self.assertNotice(self.run_hook(), 310)
        self.turn(320000)
        self.assertEqual(self.run_hook(), "")
        log = self.log_lines()
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0].split("\t")[1:], [SESSION[:8], AGENT[:8], "C--work-Fixture", "210000"])

    def test_other_agent_own_state(self):
        self.turn(210000)
        self.assertNotice(self.run_hook(), 210)
        other = os.path.join(os.path.dirname(self.sub), "agent-b0123456789abcdef.jsonl")
        self.turn(230000, other)
        self.assertNotice(self.run_hook(self.payload(agent_id="b0123456789abcdef", agent_transcript_path=other)), 230)

    def test_missing_transcript(self):
        os.remove(self.sub)
        self.assertEqual(self.run_hook(), "")
        self.assertEqual(self.run_hook(self.payload(agent_transcript_path=os.path.join(self.tmp, "nope.jsonl"))), "")

    def test_bad_input(self):
        for raw in (b"", b"not json", b"[1,2]", b'{"agent_id": "../../x", "transcript_path": "y"}',
                    b'{"agent_id": 5, "transcript_path": 7}'):
            self.assertEqual(self.run_hook(raw=raw), "")

    def test_derived_path(self):
        self.turn(250000)
        self.assertNotice(self.run_hook(self.payload(agent_transcript_path=None)), 250)

    def test_derived_path_backslashes(self):
        self.turn(250000)
        out = self.run_hook(self.payload(agent_transcript_path=None, transcript_path=self.main.replace("/", "\\")))
        self.assertNotice(out, 250)

    def test_glob_path(self):
        """The agent file has a suffix in the session's subagents folder: found by the glob."""
        os.remove(self.sub)
        moved = os.path.join(os.path.dirname(self.sub), "agent-%s-x.jsonl" % AGENT)
        self.turn(260000, moved)
        self.assertNotice(self.run_hook(self.payload(agent_transcript_path=None)), 260)

    def test_no_project_wide_glob(self):
        """A file in another session's subagents folder is not searched for."""
        os.remove(self.sub)
        moved = os.path.join(self.proj, "other-session", "subagents", "agent-%s.jsonl" % AGENT)
        os.makedirs(os.path.dirname(moved))
        self.turn(260000, moved)
        self.assertEqual(self.run_hook(self.payload(agent_transcript_path=None)), "")

    def test_notice_guards_end(self):
        """The notice tells a reviewer to give its verdict, and a lane to release locks and leave no half-made
        commit, before it ends."""
        self.turn(210000)
        text = json.loads(self.run_hook())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Then end. " + GUARD + " The parent continues", text)

    def test_parallel_calls_one_notice(self):
        """Two hook processes started together on the same turn print one notice."""
        self.turn(210000)
        env = dict(os.environ, LONG_RUN_DIR=self.tmp)
        data = json.dumps(self.payload()).encode("utf-8")
        for _ in range(5):                  # a fresh state each round, so every round races
            shutil.rmtree(os.path.join(self.tmp, "long-run-state"), ignore_errors=True)
            procs = [subprocess.Popen([sys.executable, "-I", HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, env=env) for _ in range(2)]
            for proc in procs:
                proc.stdin.write(data)
            for proc in procs:
                proc.stdin.close()
            outs = []
            for proc in procs:
                outs.append(proc.stdout.read().decode("utf-8"))
                proc.wait(timeout=30)
                proc.stdout.close()
                proc.stderr.close()
                self.assertEqual(proc.returncode, 0)
            self.assertEqual(len([o for o in outs if o]), 1, outs)
        self.assertEqual(len(self.log_lines()), 5)

    def test_big_result_beyond_tail(self):
        """The newest usage record is found even with a result bigger than the tail after an older one."""
        self.turn(210000)
        with open(self.sub, "a", encoding="utf-8") as fh:
            fh.write(result(300 * 1024))
            fh.write(assistant(205000))
        self.assertNotice(self.run_hook(), 205)


WALL_REASON = ("Context wall (330k). Stop working now. Write your checkpoint report (what is done, what is left, the "
               "exit files or logs you wait on, any lock you hold and any rebase, merge or staged change in progress, "
               "the next step) as a new file or as your final message, then end your turn. Your parent continues "
               "with a fresh agent.")


class Wall(Base):
    """PreToolUse mode, long-run.py --wall."""

    def run_wall(self, payload=None, raw=None, tool="Bash"):
        payload = self.payload(tool_name=tool, hook_event_name="PreToolUse") if payload is None else payload
        env = dict(os.environ, LONG_RUN_DIR=self.tmp)
        data = raw if raw is not None else json.dumps(payload).encode("utf-8")
        proc = subprocess.run([sys.executable, "-I", HOOK, "--wall"], input=data, capture_output=True, env=env,
                              timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        return proc.stdout.decode("utf-8")

    def notice_300k(self):
        """The 300k notice through the PostToolUse mode, which leaves the marker."""
        self.turn(310000)
        self.assertNotice(self.run_hook(), 310)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "long-run-state", AGENT + ".300000.mark")))

    def assertDeny(self, out):
        self.assertEqual(json.loads(out), {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                                  "permissionDecision": "deny",
                                                                  "permissionDecisionReason": WALL_REASON}})

    def test_wall_denies_bash_allows_write_edit(self):
        self.notice_300k()
        self.turn(340000)
        self.assertDeny(self.run_wall(tool="Bash"))
        self.assertEqual(self.run_wall(tool="Write"), "")
        self.assertEqual(self.run_wall(tool="Edit"), "")
        self.assertDeny(self.run_wall(tool="Read"))
        log = self.log_lines()
        self.assertEqual(len(log), 3)                   # the 300k notice and two denials
        self.assertEqual(log[1].split("\t")[1:], [SESSION[:8], AGENT[:8], "C--work-Fixture", "wall 340000"])
        self.assertEqual(log[2].split("\t")[4], "wall 340000")

    def test_wall_needs_marker(self):
        """An agent at 340k that never got the 300k notice is allowed; the PostToolUse notice comes first."""
        self.turn(340000)
        self.assertEqual(self.run_wall(), "")
        self.assertEqual(self.log_lines(), [])
        self.assertNotice(self.run_hook(), 340)
        self.assertDeny(self.run_wall())

    def test_wall_below_330k(self):
        self.notice_300k()
        self.turn(329999)
        self.assertEqual(self.run_wall(), "")
        self.turn(330000)
        self.assertDeny(self.run_wall())

    def test_wall_main_session(self):
        self.notice_300k()
        self.turn(400000)
        main = {"session_id": SESSION, "transcript_path": self.sub, "tool_name": "Bash",
                "hook_event_name": "PreToolUse"}
        self.assertEqual(self.run_wall(main), "")
        self.assertEqual(len(self.log_lines()), 1)

    def test_wall_broken_transcript(self):
        self.notice_300k()
        with open(self.sub, "w", encoding="utf-8") as fh:   # a cut assistant line, garbage, a usage that is no dict
            fh.write(assistant(340000)[:120] + "\n")
            fh.write("not json at all \x00\xff\n")
            fh.write('{"type":"assistant","message":{"usage":"340000"}}\n')
            fh.write('{"type":"assistant","message":[1,2]}\n')
        self.assertEqual(self.run_wall(), "")
        with open(self.sub, "wb") as fh:
            fh.write(b"\xff\xfe\x00broken" * 1000)
        self.assertEqual(self.run_wall(), "")
        os.remove(self.sub)
        os.makedirs(self.sub)                                 # the path is a folder
        self.assertEqual(self.run_wall(), "")
        self.assertEqual(len(self.log_lines()), 1)

    def test_wall_bad_input(self):
        for raw in (b"", b"not json", b"[1,2]", b'{"agent_id": "../../x", "transcript_path": "y"}',
                    b'{"agent_id": 5, "transcript_path": 7}', b'{"agent_id": null}'):
            self.assertEqual(self.run_wall(raw=raw), "")

    def test_post_tool_use_mode_never_denies(self):
        """Without --wall the hook only ever sends the notice, even past the wall."""
        self.notice_300k()
        self.turn(400000)
        self.assertEqual(self.run_hook(), "")


if __name__ == "__main__":
    unittest.main(verbosity=1)
