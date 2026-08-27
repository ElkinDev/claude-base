"""Fixture tests for the tool hooks.

Hooks are snapshotted when a session starts, so they cannot be exercised from inside a live
session. This runner is the evidence instead: it pipes each fixture in tests/fixtures/ into
the real hook as a subprocess, exactly as the harness does, and checks what comes back.

Run:
    python scripts/hooks/tests/run-tests.py

Project hooks are read from the parent directory of this one. Global hooks are read from
CLAUDE_HOOKS_DIR when it is set, otherwise from ~/.claude/hooks.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")
PROJECT_HOOKS = os.path.dirname(TESTS_DIR)
PROJECT_DIR = os.path.dirname(os.path.dirname(PROJECT_HOOKS))
GLOBAL_HOOKS = os.environ.get("CLAUDE_HOOKS_DIR") or os.path.join(
    os.path.expanduser("~"), ".claude", "hooks"
)

FILTER_HOOK = os.path.join(PROJECT_HOOKS, "filter-gradle-output.py")
RUN_LOGGED = os.path.join(PROJECT_HOOKS, "run-logged.py")
GUARD_HOOK = os.path.join(GLOBAL_HOOKS, "guard-read.py")
ALARM_HOOK = os.path.join(GLOBAL_HOOKS, "alarm-big-result.py")

BIG_RESPONSE = "gradle line of noise that nobody reads\n" * 1600


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(name, replacements):
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as handle:
        text = handle.read()
    for key, value in replacements.items():
        text = text.replace(key, json.dumps(value)[1:-1])
    return text


def run_hook(hook, payload, env_overrides=None):
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    for key, value in (env_overrides or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    process = subprocess.run(
        [sys.executable, hook],
        input=payload.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return process.returncode, process.stdout.decode("utf-8", "replace").strip()


def decision(stdout):
    return json.loads(stdout)["hookSpecificOutput"]


class Workspace:
    def __init__(self, root):
        self.root = root
        self.big = os.path.join(root, "big-file.md")
        with open(self.big, "w", encoding="utf-8", newline="\n") as handle:
            for i in range(4000):
                handle.write("line %04d %s\n" % (i, "x" * 40))
        self.small = os.path.join(root, "small-file.md")
        with open(self.small, "w", encoding="utf-8", newline="\n") as handle:
            for i in range(100):
                handle.write("line %d\n" % i)
        # A device screenshot is over the size limit every time, so the fixture is too:
        # the size rule must not be what stops a lane from looking at one.
        self.image = os.path.join(root, "screenshot.png")
        with open(self.image, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * (300 * 1024))
        self.pdf = os.path.join(root, "report.pdf")
        with open(self.pdf, "wb") as handle:
            handle.write(b"%PDF-1.7\n" + b"\x00" * (300 * 1024))
        self.ledger = os.path.join(root, "ledger")

    def replacements(self):
        return {
            "__PROJECT__": PROJECT_DIR,
            "__BIGFILE__": self.big,
            "__SMALLFILE__": self.small,
            "__IMAGE__": self.image,
            "__PDF__": self.pdf,
            "__BIGRESPONSE__": BIG_RESPONSE,
        }


def test_gradle_command_is_wrapped(ws):
    payload = fixture("gradle-command.json", ws.replacements())
    code, stdout = run_hook(FILTER_HOOK, payload, {"CLAUDE_PROJECT_DIR": PROJECT_DIR})
    assert code == 0, "the hook must exit 0, got %d" % code
    out = decision(stdout)
    assert out["hookEventName"] == "PreToolUse", out
    assert out["permissionDecision"] == "allow", out
    command = out["updatedInput"]["command"]
    assert "run-logged.py" in command, command
    assert "--log" in command, command
    assert "build/tool-logs/" in command, command
    assert command.endswith("'"), command
    assert "gradlew :app:assembleDebug --stacktrace" in command, command
    assert out["updatedInput"]["description"] == "Build the debug variant", out
    assert out["updatedInput"]["timeout"] == 600000, out
    assert command.startswith("cd "), "the leading cd must stay in the session shell: %s" % command
    assert command.index("cd ") < command.index("run-logged.py"), command


def test_wrapped_command_is_untouched(ws):
    payload = fixture("gradle-already-wrapped.json", ws.replacements())
    code, stdout = run_hook(FILTER_HOOK, payload, {"CLAUDE_PROJECT_DIR": PROJECT_DIR})
    assert code == 0, code
    assert stdout == "", "an already wrapped command must be left alone, got: %s" % stdout


def test_rewrite_is_idempotent(ws):
    payload = fixture("gradle-command.json", ws.replacements())
    _, stdout = run_hook(FILTER_HOOK, payload, {"CLAUDE_PROJECT_DIR": PROJECT_DIR})
    rewritten = decision(stdout)["updatedInput"]
    second = json.dumps({"tool_name": "Bash", "cwd": PROJECT_DIR, "tool_input": rewritten})
    code, again = run_hook(FILTER_HOOK, second, {"CLAUDE_PROJECT_DIR": PROJECT_DIR})
    assert code == 0, code
    assert again == "", "a second pass must not wrap the command twice, got: %s" % again


def test_non_gradle_command_is_untouched(ws):
    payload = fixture("non-gradle-command.json", ws.replacements())
    code, stdout = run_hook(FILTER_HOOK, payload, {"CLAUDE_PROJECT_DIR": PROJECT_DIR})
    assert code == 0, code
    assert stdout == "", "a non gradle command must be left alone, got: %s" % stdout


def test_grep_that_mentions_gradle_is_untouched(ws):
    payload = fixture("non-gradle-command.json", ws.replacements()).replace(
        "git status --porcelain", "grep -rn 'gradle ' docs/ | head -20")
    code, stdout = run_hook(FILTER_HOOK, payload, {"CLAUDE_PROJECT_DIR": PROJECT_DIR})
    assert code == 0, code
    assert stdout == "", "a command that only mentions gradle must be left alone, got: %s" % stdout


def bash_command(ws, command):
    return fixture("non-gradle-command.json", ws.replacements()).replace(
        "git status --porcelain", command)


def wrapped_command(ws, command):
    """Run the filter over a command and return the rewritten command line, or "" if untouched."""
    payload = bash_command(ws, command)
    code, stdout = run_hook(FILTER_HOOK, payload, {"CLAUDE_PROJECT_DIR": PROJECT_DIR})
    assert code == 0, "the hook must exit 0, got %d" % code
    if not stdout:
        return ""
    return decision(stdout)["updatedInput"]["command"]


def test_gradle_behind_cd_env_and_timeout_is_wrapped(ws):
    command = wrapped_command(
        ws, "cd C:/Repo/myapp-w99 && JAVA_HOME=C:/jbr timeout 600 ./gradlew :app:testDebugUnitTest")
    assert "run-logged.py" in command, command


def test_timeout_before_the_env_prefix_is_wrapped(ws):
    """The prefix strip has to run again after `timeout N`, or the env assignment behind it
    is read as the command word and the build escapes the filter."""
    command = wrapped_command(ws, "timeout 600 JAVA_HOME=C:/jbr ./gradlew :app:test")
    assert "run-logged.py" in command, command
    assert "gradlew :app:test" in command, command


def test_the_leading_cd_stays_outside_the_wrapper(ws):
    """run-logged.py runs its argument in a fresh bash, so a cd inside it would not move the
    session shell. The chain stays in front and only the gradle part is wrapped."""
    command = wrapped_command(ws, "cd C:/Repo/myapp-w99 && ./gradlew :app:testDebugUnitTest")
    assert command.startswith("cd C:/Repo/myapp-w99 && python "), command
    assert command.count("cd C:/Repo/myapp-w99") == 1, "the cd must not also be wrapped: %s" % command
    quoted = command[command.index(" -- ") + 4:]
    assert quoted == "'./gradlew :app:testDebugUnitTest'", quoted


def test_git_log_naming_a_gradle_script_is_untouched(ws):
    """The script name is an argument here, not the command word. Wrapping this would
    replace the git output with a gradle-shaped digest of nothing."""
    command = wrapped_command(ws, "git log --oneline -5 -- scripts/lane-gate.sh")
    assert command == "", "git log must keep its own output, got: %s" % command


def test_cat_of_a_gradle_script_is_untouched(ws):
    command = wrapped_command(ws, "cat scripts/gradle-lockrun.ps1")
    assert command == "", "reading a script is not running it, got: %s" % command


def test_the_wrapper_scripts_are_wrapped_when_they_are_actually_run(ws):
    """Both wrappers are launched through a launcher in this repo, so the command word is
    found one token in: `bash <script>` and `powershell -File <script>`."""
    gate = wrapped_command(ws, "bash /c/Repo/myapp/scripts/lane-gate.sh w99 /tmp/scratch /c/Repo/myapp --app")
    assert "run-logged.py" in gate, gate
    lockrun = wrapped_command(
        ws, "powershell -NoProfile -File scripts/gradle-lockrun.ps1 -Repo C:/Repo/myapp -Tag w99 -Phases a")
    assert "run-logged.py" in lockrun, lockrun


def test_digest_keeps_what_matters(ws):
    runner = load_module(RUN_LOGGED, "run_logged")
    with open(os.path.join(FIXTURES_DIR, "gradle-output-failed.txt"), encoding="utf-8") as handle:
        output = handle.read()
    text = runner.digest(output, "C:/Repo/myapp/build/tool-logs/sample.log", 1)
    first = text.splitlines()[0]
    assert "build/tool-logs/sample.log" in first, first
    assert "exit 1" in first, first
    for needle in (
        "BUILD FAILED",
        "> Task :app:compileDebugKotlin FAILED",
        "e: file:///C:/Repo/myapp/app/src/main/java/com/example/budget/BudgetRepository.kt:88:31",
        "capIsClampedToTheMonth FAILED",
        "41 tests completed, 1 failed, 2 skipped",
        "What went wrong",
        "Execution failed for task ':app:compileDebugKotlin'.",
    ):
        assert needle in text, "the digest dropped: %s" % needle
    assert "14 actionable tasks: 6 executed, 8 up-to-date" in text, "the tail is missing"
    assert "Starting a Gradle Daemon" not in text, "the digest kept noise it should have dropped"
    assert len(text) <= runner.CHAR_CAP, len(text)
    assert len(text.splitlines()) <= runner.LINE_CAP, len(text.splitlines())


def test_digest_cuts_the_middle_not_the_ends(ws):
    runner = load_module(RUN_LOGGED, "run_logged")
    lines = ["error: failure number %d" % i for i in range(600)]
    text = runner.digest("\n".join(lines), "sample.log", 1)
    assert len(text) <= runner.CHAR_CAP, len(text)
    assert text.splitlines()[0].startswith("sample.log"), text.splitlines()[0]
    assert text.rstrip().endswith("error: failure number 599"), text[-80:]


def test_run_logged_writes_the_log_and_returns_the_exit_code(ws):
    log = os.path.join(ws.root, "logs", "sample.log")
    command = 'echo "BUILD FAILED"; echo "e: something broke"; exit 3'
    process = subprocess.run(
        [sys.executable, RUN_LOGGED, "--log", log, "--", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout = process.stdout.decode("utf-8", "replace")
    assert process.returncode == 3, "the original exit code must survive, got %d" % process.returncode
    assert os.path.isfile(log), "the log file was not written"
    with open(log, encoding="utf-8") as handle:
        whole = handle.read()
    assert "BUILD FAILED" in whole and "e: something broke" in whole, whole
    assert "sample.log" in stdout.splitlines()[0], stdout
    assert "exit 3" in stdout.splitlines()[0], stdout
    assert "BUILD FAILED" in stdout, stdout


def test_image_is_denied_for_the_orchestrator(ws):
    payload = fixture("read-image.json", ws.replacements())
    code, stdout = run_hook(GUARD_HOOK, payload, {"CLAUDE_ROLE": "orchestrator"})
    assert code == 0, code
    out = decision(stdout)
    assert out["permissionDecision"] == "deny", out
    assert "orchestrator" in out["permissionDecisionReason"], out


def test_image_is_allowed_for_a_lane(ws):
    """The fixture image is over the size limit on purpose: every device screenshot is, and
    a lane that cannot open one cannot report what the phone showed."""
    assert os.path.getsize(ws.image) > 150 * 1024, "the fixture must be above the size limit"
    payload = fixture("read-image.json", ws.replacements())
    code, stdout = run_hook(GUARD_HOOK, payload, {"CLAUDE_ROLE": None})
    assert code == 0, code
    assert stdout == "", "a lane reads images, got: %s" % stdout


def test_large_pdf_is_allowed(ws):
    """A PDF is read as pages, so a line limit means nothing and the size rule cannot apply."""
    assert os.path.getsize(ws.pdf) > 150 * 1024, "the fixture must be above the size limit"
    payload = fixture("read-pdf.json", ws.replacements())
    code, stdout = run_hook(GUARD_HOOK, payload, {"CLAUDE_ROLE": None})
    assert code == 0, code
    assert stdout == "", "a PDF must not be denied by size, got: %s" % stdout


def test_big_file_is_denied_without_a_limit(ws):
    payload = fixture("read-big-file.json", ws.replacements())
    code, stdout = run_hook(GUARD_HOOK, payload, {"CLAUDE_ROLE": None})
    assert code == 0, code
    out = decision(stdout)
    assert out["permissionDecision"] == "deny", out
    reason = out["permissionDecisionReason"]
    assert "4000 lines" in reason, reason
    assert "offset and limit" in reason, reason
    assert "sed -n" in reason, reason
    assert "grep -n" in reason, reason


def test_big_file_is_allowed_with_a_limit(ws):
    payload = fixture("read-big-file-with-limit.json", ws.replacements())
    code, stdout = run_hook(GUARD_HOOK, payload, {"CLAUDE_ROLE": "orchestrator"})
    assert code == 0, code
    assert stdout == "", "a bounded read must pass, got: %s" % stdout


def test_small_file_is_allowed(ws):
    payload = fixture("read-small-file.json", ws.replacements())
    code, stdout = run_hook(GUARD_HOOK, payload, {"CLAUDE_ROLE": "orchestrator"})
    assert code == 0, code
    assert stdout == "", "a small file must pass, got: %s" % stdout


def ledger_rows(path):
    with open(path, encoding="utf-8") as handle:
        return [line for line in handle.read().splitlines() if line]


def test_small_result_is_only_logged(ws):
    directory = os.path.join(ws.ledger, "small")
    payload = fixture("post-small-result.json", ws.replacements())
    code, stdout = run_hook(ALARM_HOOK, payload, {"CLAUDE_LEDGER_DIR": directory})
    assert code == 0, code
    assert stdout == "", "below the threshold nothing reaches the context, got: %s" % stdout
    expected = len(json.loads(payload)["tool_response"])
    rows = ledger_rows(os.path.join(directory, "tool-sizes.csv"))
    assert rows[0] == "time,session_id,tool_name,chars", rows[0]
    assert rows[-1].endswith(",Grep,%d" % expected), rows[-1]


def test_big_result_alarms_without_herdr(ws):
    directory = os.path.join(ws.ledger, "big")
    payload = fixture("post-big-result.json", ws.replacements())
    minimal_path = os.pathsep.join(
        [os.path.dirname(sys.executable), os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32")]
    )
    assert shutil.which("herdr", path=minimal_path) is None, "the test PATH must not carry herdr"
    code, stdout = run_hook(ALARM_HOOK, payload, {"CLAUDE_LEDGER_DIR": directory, "PATH": minimal_path})
    assert code == 0, "the alarm must never block, got %d" % code
    message = json.loads(stdout)["systemMessage"]
    assert "Bash" in message, message
    assert str(len(BIG_RESPONSE)) in message, message
    rows = ledger_rows(os.path.join(directory, "tool-sizes.csv"))
    assert rows[-1].endswith(",Bash,%d" % len(BIG_RESPONSE)), rows[-1]


TESTS = [
    test_gradle_command_is_wrapped,
    test_wrapped_command_is_untouched,
    test_rewrite_is_idempotent,
    test_non_gradle_command_is_untouched,
    test_grep_that_mentions_gradle_is_untouched,
    test_gradle_behind_cd_env_and_timeout_is_wrapped,
    test_timeout_before_the_env_prefix_is_wrapped,
    test_the_leading_cd_stays_outside_the_wrapper,
    test_git_log_naming_a_gradle_script_is_untouched,
    test_cat_of_a_gradle_script_is_untouched,
    test_the_wrapper_scripts_are_wrapped_when_they_are_actually_run,
    test_digest_keeps_what_matters,
    test_digest_cuts_the_middle_not_the_ends,
    test_run_logged_writes_the_log_and_returns_the_exit_code,
    test_image_is_denied_for_the_orchestrator,
    test_image_is_allowed_for_a_lane,
    test_large_pdf_is_allowed,
    test_big_file_is_denied_without_a_limit,
    test_big_file_is_allowed_with_a_limit,
    test_small_file_is_allowed,
    test_small_result_is_only_logged,
    test_big_result_alarms_without_herdr,
]


def main():
    missing = [path for path in (FILTER_HOOK, RUN_LOGGED, GUARD_HOOK, ALARM_HOOK) if not os.path.isfile(path)]
    if missing:
        for path in missing:
            print("MISSING %s" % path)
        return 2
    passed, failed = 0, []
    root = tempfile.mkdtemp(prefix="hook-tests-")
    try:
        ws = Workspace(root)
        for test in TESTS:
            try:
                test(ws)
            except AssertionError as error:
                failed.append((test.__name__, str(error)))
                print("FAIL %s: %s" % (test.__name__, error))
            except Exception as error:
                failed.append((test.__name__, repr(error)))
                print("ERROR %s: %r" % (test.__name__, error))
            else:
                passed += 1
                print("ok   %s" % test.__name__)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\n%d passed, %d failed, %d total" % (passed, len(failed), len(TESTS)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
