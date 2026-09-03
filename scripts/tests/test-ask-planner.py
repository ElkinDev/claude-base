#!/usr/bin/env python3
"""Tests for claude/tools/ask-planner.sh and its .ps1 twin, the two halves of one contract.

Run:
    python test-ask-planner.py

The tool is one stateless call to the planning model: a packet on stdin, the answer on stdout,
one ledger row, no session and no tools. Neither half may reach the real CLI here, so both are
run against a stub named `claude` that is put first on PATH: it records the argv it was given, the
directory it ran in, what that directory held at the time and the bytes it read on stdin, opens
the system prompt file it was pointed at from that same directory the way the real CLI does, then
prints a canned envelope and exits with a canned code. Every case reads that record, so a half
that calls with the wrong flags, from the wrong directory, with a project file beside it, with a
path only the caller could resolve, or without the packet fails even when its own output looks
right.

Nothing under the user's home is touched: the ledger, the law and the scratch files all live in a
temp folder that the test removes. Each half is skipped, with a note naming the missing
interpreter, when it is not on PATH.
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
# The pair ships with the kit, not with this repository: the installer copies the whole
# claude/tools directory to <kit home>/tools, so an orchestrator calls it as
# ~/.claude/tools/ask-planner.sh. The tests run it from the source tree.
TOOLS = os.path.join(ROOT, "claude", "tools")
SH = os.path.join(TOOLS, "ask-planner.sh")
PS1 = os.path.join(TOOLS, "ask-planner.ps1")
LAW = os.path.join(TOOLS, "planner-law.md")

MODEL = "claude-fable-5-1"
EFFORT = "xhigh"
CEILING = 65536
HEADER = ("time,model,effort,packet_bytes,input,cache_create,cache_read,output,"
          "thinking,duration_ms,cost_usd,answer_file")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

# The shape the installed CLI returned on the smoke of 2026-09-03, trimmed to the keys the tool
# reads. The three figures the ledger case asserts come from that run, so a parser that invents
# its own numbers cannot pass.
SMOKE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "READY",
    "duration_api_ms": 3153,
    "total_cost_usd": 0.05902,
    "num_turns": 1,
    "usage": {
        "input_tokens": 2,
        "cache_creation_input_tokens": 2940,
        "cache_read_input_tokens": 0,
        "output_tokens": 4,
        "output_tokens_details": {"thinking_tokens": 0},
    },
}
# Two lines and a currency sign: it proves stdout is bytes the tool copied, not text a shell
# re-encoded or a writer that appended a newline of its own.
ANSWER = "DECISIONS\nGRAPH: one node, review included, budget \u20ac0.06"

STUB = r'''import json, os, shutil, sys
argv = sys.argv[1:]
record = os.environ["STUB_RECORD"]
with open(os.environ["STUB_JSON"], "rb") as handle:
    payload = handle.read()
# STUB_STDERR_PRELUDE_BYTES fills the stderr pipe before a byte of the packet is read, which is
# what a half that keeps that pipe to itself and drains it only after the call deadlocks against.
prelude = int(os.environ.get("STUB_STDERR_PRELUDE_BYTES", "0"))
if prelude:
    sys.stderr.buffer.write(b"e" * prelude)
    sys.stderr.buffer.flush()
# STUB_SHOUT answers before reading a byte of the packet, which is what a half that writes the
# whole packet before it starts reading the answer deadlocks against.
early = os.environ.get("STUB_SHOUT") == "1"
if early:
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
data = sys.stdin.buffer.read().decode("utf-8", "replace")
# The real CLI opens the system prompt file from the directory it was launched in, so the stub
# opens it too, from that same place and by the argument exactly as it was handed over. A half
# that passes a path relative to the caller records law_readable 0 here, the way the real call
# of 2026-09-03 failed, instead of passing on a recorded argv nobody ever opened.
law = ""
if "--system-prompt-file" in argv:
    spot = argv.index("--system-prompt-file") + 1
    if spot < len(argv):
        law = argv[spot]
readable = 0
try:
    with open(law, "rb") as handle:
        handle.read(1)
    readable = 1
except (OSError, ValueError):
    readable = 0
with open(record, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "stdin": data,
                             "here": sorted(os.listdir(".")),
                             "law_readable": readable}) + "\n")
if not readable:
    sys.stderr.write("Error: System prompt file not found: %s\n" % law)
    sys.exit(3)
# STUB_RMDIR is taken away mid call, so a place the tool checked before spending the money is
# gone by the time it writes there. Nothing else can make that write fail any more.
victim = os.environ.get("STUB_RMDIR")
if victim:
    shutil.rmtree(victim, ignore_errors=True)
if not early:
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
'''

# Git Bash first, the same candidates doctor.py pins: a bare `bash` on the Windows PATH may be
# WSL, which reads a different filesystem than the temp folder these cases write.
GIT_BASH = (r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe")


def find_bash():
    for candidate in GIT_BASH:
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("bash")


BASH = find_bash()
POWERSHELL = shutil.which("powershell")


class Contract:
    """The table both halves answer. A mixin, so it never runs on its own."""

    runner = None
    script = None
    skip_note = ""
    shim = "claude"

    # ------------------------------------------------------------- harness
    def setUp(self):
        if not self.runner:
            self.skipTest(self.skip_note)
        self.tmp = tempfile.mkdtemp(prefix="ask-planner-")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.record = os.path.join(self.tmp, "record.jsonl")
        self.ledger = os.path.join(self.tmp, "ledger", "planner-calls.csv")
        self.envelope = self.write_json(SMOKE)
        stub = os.path.join(self.tmp, "stub.py")
        self.put(stub, STUB)
        self.write_shim(stub)

    def tearDown(self):
        if getattr(self, "tmp", None):
            shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, path, text, newline="\n"):
        with open(path, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(text)
        return path

    def write_shim(self, stub):
        raise NotImplementedError

    def write_json(self, payload, name="envelope.json"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def argument(self, path):
        """The path spelling this interpreter reads."""
        return path

    def command(self, arguments, script=None):
        """The full argv that runs this half. A half whose host mangles a word overrides it."""
        return list(self.runner) + [script or self.script] + list(arguments)

    def ask(self, *arguments, **kwargs):
        environment = dict(os.environ)
        environment["PATH"] = self.bin + os.pathsep + environment.get("PATH", "")
        environment["PLANNER_LEDGER"] = self.ledger
        law = kwargs.get("law", LAW)
        # law=None asks for the tool's own default, the file beside the script, so the variable
        # has to be gone rather than empty: an empty one would only prove the fallback twice.
        if law is None:
            environment.pop("PLANNER_LAW", None)
        else:
            environment["PLANNER_LAW"] = self.argument(law)
        environment["STUB_RECORD"] = self.record
        environment["STUB_JSON"] = kwargs.get("envelope", self.envelope)
        environment["STUB_EXIT"] = str(kwargs.get("stub_exit", 0))
        for key in ("PLANNER_MODEL", "PLANNER_EFFORT", "PLANNER_BIN"):
            environment.pop(key, None)
        environment.update(kwargs.get("env", {}))
        process = subprocess.run(
            self.command(arguments, kwargs.get("script")),
            input=kwargs.get("stdin", b""),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            cwd=kwargs.get("cwd"),
            timeout=kwargs.get("timeout", 90),
        )
        return (process.returncode,
                process.stdout,
                process.stderr.decode("utf-8", "replace"))

    def packet(self, text="PLAN\nShip the reserve mode.", name="packet.md"):
        return self.put(os.path.join(self.tmp, name), text)

    def calls(self):
        """Every call the stub saw, oldest first. An empty list means no call was made."""
        if not os.path.exists(self.record):
            return []
        with open(self.record, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def rows(self, path=None):
        with open(path or self.ledger, "rb") as handle:
            raw = handle.read()
        # One newline style from both halves, so a machine that runs each of them at different
        # hours reads one file rather than two shapes of it.
        self.assertNotIn(b"\r", raw, "the ledger is LF in both halves")
        return [line for line in raw.decode("utf-8").splitlines() if line.strip()]

    def expected_argv(self, model=MODEL, effort=EFFORT):
        """The flag list without its last word, the law path, whose spelling is per half."""
        return ["--print",
                "--model", model,
                "--effort", effort,
                "--tools", "",
                "--no-session-persistence",
                "--permission-mode", "dontAsk",
                "--output-format", "json",
                "--system-prompt-file"]

    def assert_law(self, call, path):
        # Each half spells a path its own way, and the default one it composes itself. The file
        # the CLI would have opened is what matters, so both sides are resolved before comparing,
        # and the stub says whether the argument it was handed opened from where it stood: a
        # recorded argv nobody could open is the mutant this second assertion kills.
        self.assertEqual(os.path.normcase(os.path.realpath(call["argv"][-1])),
                         os.path.normcase(os.path.realpath(path)))
        self.assertEqual(call["law_readable"], 1,
                         "the law file opened from the directory the call ran in")

    # ------------------------------------------------------------- the call
    def test_the_argv_is_the_flag_list_the_contract_names(self):
        code, out, err = self.ask(self.argument(self.packet()))
        self.assertEqual(code, 0, err)
        calls = self.calls()
        self.assertEqual(len(calls), 1, "exactly one call per invocation, never a retry")
        self.assertEqual(calls[0]["argv"][:-1], self.expected_argv())
        self.assert_law(calls[0], LAW)

    def test_the_default_law_is_the_file_beside_the_script(self):
        code, out, err = self.ask(self.argument(self.packet()), law=None)
        self.assertEqual(code, 0, err)
        call = self.calls()[0]
        self.assertTrue(call["argv"][-1].replace("\\", "/").endswith("/planner-law.md"),
                        call["argv"][-1])
        self.assert_law(call, LAW)

    def test_the_model_and_effort_overrides_reach_the_argv(self):
        code, out, err = self.ask(self.argument(self.packet()),
                                  env={"PLANNER_MODEL": "claude-opus-5",
                                       "PLANNER_EFFORT": "max"})
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls()[0]["argv"][:-1],
                         self.expected_argv(model="claude-opus-5", effort="max"))

    def test_a_law_override_reaches_the_argv(self):
        law = self.put(os.path.join(self.tmp, "own-law.md"), "Answer in one word.\n")
        code, out, err = self.ask(self.argument(self.packet()), law=law)
        self.assertEqual(code, 0, err)
        self.assert_law(self.calls()[0], law)

    def test_a_packet_file_arrives_on_the_stub_stdin(self):
        text = "ADJUDICATE\nBranch tip 7806092, one report, reviewer said CLEAR.\n"
        code, out, err = self.ask(self.argument(self.packet(text)))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls()[0]["stdin"], text)

    def test_a_packet_on_stdin_arrives_on_the_stub_stdin(self):
        text = "DECIDE\nOne node or three.\n"
        code, out, err = self.ask(stdin=text.encode("utf-8"))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls()[0]["stdin"], text)

    def test_a_dash_means_stdin(self):
        text = "PLAN\nRead from the pipe.\n"
        code, out, err = self.ask("-", stdin=text.encode("utf-8"))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls()[0]["stdin"], text)

    # ------------------------------------------------------------- the answer
    def test_stdout_is_the_result_byte_for_byte(self):
        envelope = self.write_json(dict(SMOKE, result=ANSWER), "answer.json")
        code, out, err = self.ask(self.argument(self.packet()), envelope=envelope)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, ANSWER.encode("utf-8"),
                         "only result goes to stdout, with no newline of the tool's own")

    def test_dash_o_writes_the_same_text(self):
        envelope = self.write_json(dict(SMOKE, result=ANSWER), "answer.json")
        answer = os.path.join(self.tmp, "answer.txt")
        code, out, err = self.ask("-o", self.argument(answer),
                                  self.argument(self.packet()), envelope=envelope)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, ANSWER.encode("utf-8"))
        with open(answer, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), ANSWER)

    def kit(self):
        """The pair the way the installer lands it, a tools folder the caller reaches by a
        relative name from the directory above it."""
        home = os.path.join(self.tmp, "kit")
        tools = os.path.join(home, "tools")
        os.makedirs(tools)
        for name in (os.path.basename(self.script), "planner-law.md"):
            shutil.copy2(os.path.join(TOOLS, name), os.path.join(tools, name))
        return home, tools

    def test_every_relative_path_resolves_against_the_caller_not_the_scratch(self):
        # The defect a real call found on 2026-09-03: the half read its own name as the caller
        # spelled it, kept the law path relative, and the call changed to the scratch directory,
        # where the CLI looked for the law file and did not find it. Script name, packet, answer
        # file and ledger are all relative here, and the caller stands somewhere else.
        home, tools = self.kit()
        text = "PLAN\nEverything relative.\n"
        self.put(os.path.join(home, "packet.md"), text)
        # The folder of the -o file has to be there already: a wrong -o is far likelier than a
        # deliberate new tree, so the tool refuses one it would have to invent.
        os.makedirs(os.path.join(home, "out"))
        envelope = self.write_json(dict(SMOKE, result=ANSWER), "relative.json")
        code, out, err = self.ask(
            "-o", "out/answer.txt", "packet.md",
            script=self.argument(os.path.join("tools", os.path.basename(self.script))),
            cwd=home, law=None, envelope=envelope,
            env={"PLANNER_LEDGER": "books/planner-calls.csv"})
        self.assertEqual(code, 0, err)
        self.assertEqual(out, ANSWER.encode("utf-8"))
        call = self.calls()[0]
        self.assertEqual(call["stdin"], text, "the packet is read from the caller's directory")
        self.assert_law(call, os.path.join(tools, "planner-law.md"))
        answer = os.path.join(home, "out", "answer.txt")
        with open(answer, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), ANSWER)
        rows = self.rows(os.path.join(home, "books", "planner-calls.csv"))
        self.assertEqual(rows[0], HEADER)
        self.assertEqual(len(rows), 2)
        self.assertEqual(os.path.normcase(os.path.realpath(rows[1].split(",")[11])),
                         os.path.normcase(os.path.realpath(answer)),
                         "the ledger names the answer file where it actually landed")

    def test_a_relative_law_override_is_rooted_at_the_caller(self):
        # The default law is composed from the script's own name, so a half whose host always
        # hands it an absolute one would pass the case above and still break on this: a law file
        # the caller named relative to itself.
        home, tools = self.kit()
        packet = self.packet()
        code, out, err = self.ask(self.argument(packet), cwd=home,
                                  law=os.path.join("tools", "planner-law.md"))
        self.assertEqual(code, 0, err)
        self.assert_law(self.calls()[0], os.path.join(tools, "planner-law.md"))

    def test_the_call_runs_from_a_scratch_directory_with_no_claude_md(self):
        code, out, err = self.ask(self.argument(self.packet()))
        self.assertEqual(code, 0, err)
        cwd = self.calls()[0]["cwd"]
        self.assertNotEqual(os.path.normcase(os.path.realpath(cwd)),
                            os.path.normcase(os.path.realpath(HERE)),
                            "a project CLAUDE.md would be billed on every call")
        self.assertIn("planner-cwd", cwd.replace("\\", "/"))
        self.assertFalse(os.path.exists(os.path.join(cwd, "CLAUDE.md")))

    def temp_env(self):
        """A temp directory of this case's own, in the three names the two halves read."""
        base = os.path.join(self.tmp, "temp")
        if not os.path.isdir(base):
            os.makedirs(base)
        return base, {"TMPDIR": self.argument(base),
                      "TMP": self.argument(base),
                      "TEMP": self.argument(base)}

    def test_each_call_runs_in_a_fresh_directory_that_is_gone_afterwards(self):
        # The shared folder survives the calls, so anything may leave a file in it, and a
        # CLAUDE.md there would be read and billed as cache creation on every call from then on.
        # Emptying it is the wrong answer twice over: it would delete files this tool never made,
        # and two calls at the same time would stand in the same place. Each call gets its own
        # directory instead, empty because it is new and gone on the way out. The listing is
        # taken by the stub, in the call itself.
        base, environment = self.temp_env()
        parent = os.path.join(base, "planner-cwd")
        os.makedirs(parent)
        stale = self.put(os.path.join(parent, "CLAUDE.md"), "# stale project law\n")
        packet = self.argument(self.packet())
        for _ in range(2):
            code, out, err = self.ask(packet, env=environment)
            self.assertEqual(code, 0, err)
        seen = []
        for call in self.calls():
            cwd = call["cwd"]
            self.assertEqual(os.path.normcase(os.path.realpath(os.path.dirname(cwd))),
                             os.path.normcase(os.path.realpath(parent)),
                             "the call stands in a child of the folder under the temp in use")
            self.assertEqual(call["here"], [], "created empty, so there is nothing to load")
            self.assertFalse(os.path.exists(cwd), "and removed when the tool exits")
            seen.append(os.path.normcase(os.path.realpath(cwd)))
        self.assertEqual(len(set(seen)), 2, "two calls never share a working directory")
        self.assertTrue(os.path.exists(stale),
                        "the shared folder is not this tool's to empty")

    def test_a_spelled_out_relative_planner_bin_is_rooted_at_the_caller(self):
        # A name with no separator is a PATH lookup and stays one. A spelled out path is the
        # caller's, so it is rooted like every other path before the directory changes.
        self.kit()
        code, out, err = self.ask(self.argument(self.packet()), cwd=self.tmp, law=None,
                                  script=self.argument(os.path.join("kit", "tools",
                                                                    os.path.basename(self.script))),
                                  env={"PLANNER_BIN": self.argument(
                                      os.path.join("bin", self.shim))})
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.calls()), 1, "the shim beside the caller was the one run")

    def test_a_planner_bin_outside_the_path_is_run_by_its_absolute_name(self):
        # The kit can be installed on a machine where the CLI is not on PATH at all, so the
        # override has to be enough on its own. The shim on PATH is moved away here, which makes
        # a lookup impossible: a recorded call proves the absolute name was the one launched.
        elsewhere = os.path.join(self.tmp, "off-path")
        os.makedirs(elsewhere)
        moved = os.path.join(elsewhere, self.shim)
        shutil.move(os.path.join(self.bin, self.shim), moved)
        code, out, err = self.ask(self.argument(self.packet()),
                                  env={"PLANNER_BIN": self.argument(moved)})
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.calls()), 1, "the override alone found the program")

    def test_a_child_that_answers_before_it_drains_the_packet_does_not_hang(self):
        # The answer is read as it comes, not after the packet has gone in. A child that starts
        # printing while it is still being fed fills its own stdout pipe, stops reading, and both
        # sides wait on a buffer the other one holds. The packet here is the widest the tool
        # accepts and the answer is far wider than any pipe buffer, so a half that writes first
        # and reads afterwards stops here instead of failing on a machine one day.
        wide = "x" * 200000
        envelope = self.write_json(dict(SMOKE, result=wide), "wide.json")
        text = "PLAN\n" + "y" * (CEILING - 6) + "\n"
        packet = self.packet(text, "wide.md")
        self.assertEqual(os.path.getsize(packet), CEILING)
        code, out, err = self.ask(self.argument(packet), envelope=envelope,
                                  env={"STUB_SHOUT": "1"}, timeout=45)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out), len(wide), "the whole answer came back")
        self.assertEqual(self.calls()[0]["stdin"], text, "and the whole packet went in")

    def test_a_child_that_fills_the_stderr_pipe_first_does_not_hang(self):
        # Whatever the CLI says about itself reaches the caller, and the tool never holds that
        # stream in a buffer it drains only once the call is over. The stub writes far more than
        # a pipe holds before it reads a byte of the packet, so a half that keeps stderr to
        # itself stops here instead of on a machine one day.
        envelope = self.write_json(dict(SMOKE, result=ANSWER), "prelude.json")
        text = "PLAN\n" + "y" * (CEILING - 6) + "\n"
        packet = self.packet(text, "prelude.md")
        self.assertEqual(os.path.getsize(packet), CEILING)
        code, out, err = self.ask(self.argument(packet), envelope=envelope,
                                  env={"STUB_STDERR_PRELUDE_BYTES": "200000"}, timeout=30)
        self.assertEqual(code, 0, err[:200])
        self.assertEqual(out, ANSWER.encode("utf-8"))
        self.assertGreaterEqual(len(err), 200000, "the stderr of the call reached the caller")

    def test_a_packet_that_is_not_ascii_arrives_whole_and_is_counted_in_bytes(self):
        text = "PLAN\nRevisar el módulo, coste €0,06, 計画を確認\n"
        packet = self.packet(text)
        size = os.path.getsize(packet)
        self.assertGreater(size, len(text), "the packet is wider than one byte per character")
        code, out, err = self.ask(self.argument(packet))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls()[0]["stdin"], text)
        self.assertEqual(self.rows()[1].split(",")[3], str(size),
                         "the ledger counts the packet in bytes, not in characters")

    # ------------------------------------------------------------- the ledger
    def test_the_ledger_is_created_with_its_header_and_the_smoke_figures(self):
        answer = os.path.join(self.tmp, "answer.txt")
        packet = self.packet()
        size = os.path.getsize(packet)
        code, out, err = self.ask("-o", self.argument(answer), self.argument(packet))
        self.assertEqual(code, 0, err)
        rows = self.rows()
        self.assertEqual(rows[0], HEADER)
        self.assertEqual(len(rows), 2)
        fields = rows[1].split(",")
        self.assertEqual(len(fields), 12, rows[1])
        self.assertRegex(fields[0], STAMP)
        self.assertEqual(fields[1:], [MODEL, EFFORT, str(size),
                                      "2", "2940", "0", "4", "0",
                                      "3153", "0.05902", self.argument(answer)])

    def test_a_second_call_appends_one_row_and_no_second_header(self):
        packet = self.argument(self.packet())
        self.assertEqual(self.ask(packet)[0], 0)
        self.assertEqual(self.ask(packet)[0], 0)
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], HEADER)
        self.assertNotIn(HEADER, rows[1:])
        self.assertEqual(rows[1].split(",")[11], "", "no -o file means an empty last field")

    # ------------------------------------------------------------- exit 2
    def usage_case(self, *arguments, **kwargs):
        code, out, err = self.ask(*arguments, **kwargs)
        self.assertEqual(code, 2, err)
        self.assertEqual(out, b"")
        lines = err.strip().splitlines()
        self.assertEqual(len(lines), 1, err)
        self.assertTrue(lines[0].startswith("usage:"), err)
        self.assertEqual(self.calls(), [], "a usage error never spends a call")
        self.assertFalse(os.path.exists(self.ledger))

    def test_an_empty_packet_is_a_usage_error(self):
        self.usage_case(self.argument(self.packet("")))

    def test_an_empty_packet_on_stdin_is_a_usage_error(self):
        self.usage_case(stdin=b"")

    def test_a_packet_over_the_ceiling_is_a_usage_error(self):
        big = "x" * 70000
        self.assertGreater(len(big), CEILING)
        self.usage_case(self.argument(self.packet(big, "big.md")))

    def test_a_missing_law_file_is_a_usage_error(self):
        self.usage_case(self.argument(self.packet()),
                        law=os.path.join(self.tmp, "no-such-law.md"))

    def test_an_unreadable_packet_file_is_a_usage_error(self):
        self.usage_case(self.argument(os.path.join(self.tmp, "absent.md")))

    def test_an_answer_file_that_names_a_directory_is_a_usage_error(self):
        # Everywhere the answer has to land is checked before the call, because the call is the
        # money: a mistyped -o costs nothing here rather than a paid answer with nowhere to go.
        blocked = os.path.join(self.tmp, "occupied")
        os.makedirs(blocked)
        self.usage_case("-o", self.argument(blocked), self.argument(self.packet()))

    def test_an_answer_file_under_a_folder_that_is_not_there_is_a_usage_error(self):
        answer = os.path.join(self.tmp, "no-such-folder", "answer.txt")
        self.usage_case("-o", self.argument(answer), self.argument(self.packet()))

    def test_a_ledger_that_names_a_directory_is_a_usage_error(self):
        blocked = os.path.join(self.tmp, "books")
        os.makedirs(blocked)
        self.usage_case(self.argument(self.packet()),
                        env={"PLANNER_LEDGER": self.argument(blocked)})

    def test_a_ledger_that_cannot_be_opened_for_append_is_a_usage_error(self):
        # The ledger folder is created when it is missing, so the failure to catch early is the
        # one no mkdir can fix: a file sitting where the folder would go.
        blocker = self.put(os.path.join(self.tmp, "blocker"), "not a folder\n")
        self.usage_case(self.argument(self.packet()),
                        env={"PLANNER_LEDGER": self.argument(
                            os.path.join(blocker, "planner-calls.csv"))})

    # ------------------------------------------------------------- exit 1
    def assert_no_row(self):
        """The ledger is opened for append before the call, so a call that failed leaves the
        file there with nothing in it. Zero bytes, not a header: the next call still writes one.
        """
        self.assertTrue(os.path.exists(self.ledger),
                        "the ledger is opened before the call, so it exists either way")
        self.assertEqual(os.path.getsize(self.ledger), 0,
                         "a failed call writes no ledger row")

    def test_a_non_zero_cli_exit_is_a_failed_call(self):
        code, out, err = self.ask(self.argument(self.packet()), stub_exit=1)
        self.assertEqual(code, 1)
        self.assertEqual(out, b"")
        self.assertEqual(len(self.calls()), 1, "one call, no retry after a failure")
        self.assert_no_row()

    def test_an_error_envelope_is_a_failed_call_and_its_result_reaches_stderr(self):
        envelope = self.write_json(dict(SMOKE, is_error=True,
                                        result="Credit balance is too low"), "error.json")
        code, out, err = self.ask(self.argument(self.packet()), envelope=envelope)
        self.assertEqual(code, 1)
        self.assertEqual(out, b"")
        self.assertIn("Credit balance is too low", err)
        self.assert_no_row()

    def test_a_subtype_other_than_success_is_a_failed_call(self):
        envelope = self.write_json(dict(SMOKE, subtype="error_max_turns"), "subtype.json")
        code, out, err = self.ask(self.argument(self.packet()), envelope=envelope)
        self.assertEqual(code, 1)
        self.assertEqual(out, b"")
        self.assert_no_row()

    def test_output_that_is_not_json_is_a_failed_call_and_reaches_stderr(self):
        envelope = self.put(os.path.join(self.tmp, "raw.txt"), "Usage: claude [options]\n")
        code, out, err = self.ask(self.argument(self.packet()), envelope=envelope)
        self.assertEqual(code, 1)
        self.assertEqual(out, b"")
        self.assertIn("Usage: claude [options]", err)
        self.assert_no_row()

    # --------------------------------------------- the answer before the books
    def assert_one_line(self, err, opening):
        """A handled failure says one sentence. An unhandled one says a stack or a host banner,
        which is how a case tells a guard apart from an interpreter falling over."""
        lines = err.strip().splitlines()
        self.assertEqual(len(lines), 1, err)
        self.assertTrue(lines[0].startswith(opening), err)

    def test_an_answer_file_that_cannot_be_written_still_leaves_the_answer_on_stdout(self):
        # The call is paid for by the time the answer is in hand, so bookkeeping never costs the
        # caller the answer. Every place was checked before the call, so the only failure left is
        # the place going away during it, which is what the stub does here.
        envelope = self.write_json(dict(SMOKE, result=ANSWER), "answer.json")
        vanishing = os.path.join(self.tmp, "vanishing")
        os.makedirs(vanishing)
        code, out, err = self.ask("-o", self.argument(os.path.join(vanishing, "answer.txt")),
                                  self.argument(self.packet()), envelope=envelope,
                                  env={"STUB_RMDIR": vanishing})
        self.assertEqual(code, 1)
        self.assertEqual(out, ANSWER.encode("utf-8"), "the answer reached the caller anyway")
        self.assert_one_line(err, "cannot write the answer file")
        rows = self.rows()
        self.assertEqual(len(rows), 2, "the call happened, so the row still says what it cost")
        self.assertEqual(rows[1].split(",")[11], "",
                         "with an empty answer_file, since the answer is not on disk")

    def test_a_ledger_that_cannot_be_written_still_leaves_the_answer_on_stdout(self):
        # One step further along the same order: the ledger was openable before the call and its
        # folder is gone by the time the row is appended. The answer is already out and the -o
        # file is already written, and the failure is still one line and exit 1.
        envelope = self.write_json(dict(SMOKE, result=ANSWER), "answer.json")
        books = os.path.join(self.tmp, "books")
        answer = os.path.join(self.tmp, "answer.txt")
        code, out, err = self.ask("-o", self.argument(answer),
                                  self.argument(self.packet()), envelope=envelope,
                                  env={"PLANNER_LEDGER": self.argument(
                                           os.path.join(books, "planner-calls.csv")),
                                       "STUB_RMDIR": books})
        self.assertEqual(code, 1)
        self.assertEqual(out, ANSWER.encode("utf-8"))
        self.assert_one_line(err, "cannot append the ledger row to")
        with open(answer, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), ANSWER, "the -o file is written before the row too")
        self.assertFalse(os.path.exists(books), "the folder went away mid call")

    # ------------------------------------------------------------- the file
    def test_the_header_names_the_contract_and_the_law_default(self):
        with open(self.script, encoding="utf-8") as handle:
            header = "".join(handle.readlines()[:40])
        self.assertIn("planner-law.md", header)
        self.assertIn("exit 2", header)


class BashContract(Contract, unittest.TestCase):
    script = SH
    runner = [BASH] if BASH else None
    skip_note = "bash is not on PATH, so the POSIX half is not exercised here"

    def argument(self, path):
        # Forward slashes: a Windows path reaches a POSIX shell as an argv word, and a
        # backslash in it is an escape waiting to happen.
        return path.replace("\\", "/")

    def write_shim(self, stub):
        shim = os.path.join(self.bin, self.shim)
        self.put(shim, "#!/bin/sh\nexec '%s' '%s' \"$@\"\n"
                 % (sys.executable.replace("\\", "/"), stub.replace("\\", "/")))
        os.chmod(shim, 0o755)


class PowerShellContract(Contract, unittest.TestCase):
    script = PS1
    runner = ([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
              if POWERSHELL else None)
    dash_runner = ([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]
                   if POWERSHELL else None)
    skip_note = "powershell is not on PATH, so the Windows half is not exercised here"
    # A spelled out PLANNER_BIN is a path, and on Windows a path names the file with its
    # extension: the PATH lookup that supplies .cmd is the thing such a name opts out of.
    shim = "claude.cmd"

    def command(self, arguments, script=None):
        if "-" not in arguments:
            return super(PowerShellContract, self).command(arguments, script)
        # Windows PowerShell rejects a bare dash in its own argument binder, before -File hands
        # anything to the script, so the case that proves the script honours `-` is run the one
        # way a caller can deliver it. test_the_file_host_refuses_a_bare_dash pins the limit.
        words = " ".join("'%s'" % word.replace("'", "''")
                         for word in [script or self.script] + list(arguments))
        return list(self.dash_runner) + ["& " + words]

    def test_the_file_host_refuses_a_bare_dash(self):
        process = subprocess.run(list(self.runner) + [self.script, "-"],
                                 input=b"PLAN\nnever read.\n",
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        self.assertNotEqual(process.returncode, 0,
                            "the header tells a Windows caller to omit the packet instead")
        self.assertEqual(self.calls(), [])

    def write_shim(self, stub):
        # A .cmd, because that is the shape the CLI itself takes on Windows: the half has to
        # resolve and launch a batch shim, not only a bare .exe.
        self.put(os.path.join(self.bin, self.shim),
                 '@echo off\r\n"%s" "%s" %%*\r\nexit /b %%ERRORLEVEL%%\r\n'
                 % (sys.executable, stub), newline="")


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    unittest.main(verbosity=2)
