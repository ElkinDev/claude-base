"""Tests for claude/hooks/guard-background.py. The hook runs as the harness runs it, a subprocess with a payload on
stdin; GUARD_BACKGROUND_LOG puts its log in a temporary folder.

    python -I -B claude/hooks/tests/test-guard-background.py
    GUARD_BACKGROUND_PY=<path> python -I -B claude/hooks/tests/test-guard-background.py   # a staged copy (mutants)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.environ.get("GUARD_BACKGROUND_PY") or os.path.join(os.path.dirname(HERE), "guard-background.py")
SESSION = "11111111-2222-3333-4444-555555555555"
MAIN_TRANSCRIPT = "D:/work/agent-home/projects/C--work-x/%s.jsonl" % SESSION
SUB_TRANSCRIPT = "D:/work/agent-home/projects/C--work-x/%s/subagents/agent-a1.jsonl" % SESSION


class GuardBackground(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="guard background ")
        self.log = os.path.join(self.tmp, "guard-background.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def payload(self, command, background=True, tool="Bash", agent=True, **extra):
        data = {"session_id": SESSION, "transcript_path": MAIN_TRANSCRIPT, "hook_event_name": "PreToolUse",
                "tool_name": tool, "tool_input": {"command": command}}
        if background:
            data["tool_input"]["run_in_background"] = True
        if agent:
            data.update(agent_id="a1", agent_type="implementer")
        data.update(extra)
        return data

    def run_hook(self, payload=None, raw=None, **env_extra):
        env = dict(os.environ, GUARD_BACKGROUND_LOG=self.log)
        env.pop("GUARD_BACKGROUND_OFF", None)
        env.update(env_extra)
        data = raw if raw is not None else json.dumps(payload).encode("utf-8")
        proc = subprocess.run([sys.executable, "-I", HOOK], input=data, capture_output=True, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.decode("utf-8")

    def assertDenied(self, out):
        self.assertTrue(out, "expected a deny")
        hso = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn("A subagent may not use run_in_background", hso["permissionDecisionReason"])
        self.assertIn("FOREGROUND", hso["permissionDecisionReason"])

    def log_lines(self):
        try:
            with open(self.log, encoding="utf-8") as fh:
                return fh.read().splitlines()
        except OSError:
            return []

    def test_subagent_background_denied_and_logged(self):
        self.assertDenied(self.run_hook(self.payload("./gradlew test")))
        lines = self.log_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split("\t")[1:], ["session=" + SESSION[:8], "agent_type=implementer", "tool=Bash"])

    def test_powershell_denied(self):
        self.assertDenied(self.run_hook(self.payload("Start-Sleep 400", tool="PowerShell")))

    def test_agent_type_alone_is_a_subagent(self):
        data = self.payload("make all", agent=False, agent_type="reviewer")
        self.assertDenied(self.run_hook(data))

    def test_subagents_transcript_is_a_subagent(self):
        data = self.payload("make all", agent=False, transcript_path=SUB_TRANSCRIPT.replace("/", "\\"))
        self.assertDenied(self.run_hook(data))

    def test_main_session_passes(self):
        self.assertEqual(self.run_hook(self.payload("./gradlew test", agent=False)), "")
        self.assertEqual(self.log_lines(), [])

    def test_foreground_passes(self):
        self.assertEqual(self.run_hook(self.payload("./gradlew test", background=False)), "")

    def test_background_false_passes(self):
        data = self.payload("./gradlew test", background=False)
        data["tool_input"]["run_in_background"] = False
        self.assertEqual(self.run_hook(data), "")

    def test_other_tool_passes(self):
        self.assertEqual(self.run_hook(self.payload("x", tool="Read")), "")

    SERVERS = (
        # npm, pnpm, yarn and bun with dev, start, serve or preview, with or without run
        "npm run dev", "npm run start", "npm start", "npm run serve", "npm run preview", "npm run dev:host",
        "pnpm dev", "pnpm run dev", "pnpm start", "pnpm preview", "yarn dev", "yarn start", "yarn run serve",
        "bun run dev", "bun dev", "bun run preview",
        # next and serve through npx or bunx
        "npx next dev", "bunx next dev --turbo", "cd site && npx serve -l 5000",
        # vite and vite preview
        "vite", "vite --port 5173", "vite preview", "npx vite preview --port 4173",
        # python
        "python -m http.server 8000", "python3 -m http.server", "py -3 manage.py runserver 8000",
        "python manage.py runserver", "uvicorn app:app --reload", "flask run",
        # php, rails, dotnet, angular
        "php -S localhost:8000", "php -S 127.0.0.1:8080 -t public", "rails s", "bin/rails server -p 3001",
        "bundle exec rails s", "dotnet run --project Api", "dotnet watch run", "ng serve --port 4300",
        # the emulator
        "emulator -avd Pixel_8", "emulator.exe -no-snapshot -gpu host -avd Pixel_8",
    )
    NOT_SERVERS = (
        "npm run build", "npm test", "npm run test", "npm run lint", "npm run development-build",
        "pnpm build", "yarn test", "bun run build", "bun test", "npx next build", "vite build",
        "npx vite build --mode prod", "dotnet watch test", "npm run dev:test", "vite --mode x build", "pytest", "python -m pytest tests", "python manage.py test",
        "php -s index.php", "php artisan test", "rails test", "dotnet build", "dotnet test", "ng build",
        "./gradlew assembleDebug", "emulator -list-avds",
    )

    def test_each_server_passes(self):
        for command in self.SERVERS:
            with self.subTest(command=command):
                self.assertEqual(self.run_hook(self.payload(command)), "", command)
        self.assertEqual(self.log_lines(), [])

    def test_each_build_or_test_denied(self):
        for command in self.NOT_SERVERS:
            with self.subTest(command=command):
                self.assertDenied(self.run_hook(self.payload(command)))
        self.assertEqual(len(self.log_lines()), len(self.NOT_SERVERS))

    def test_server_with_wait_loop_denied(self):
        for command in ("npm run dev & while ! curl -s localhost:5173; do sleep 5; done",
                        "python -m http.server 8000; while ($true) { Start-Sleep 5 }",
                        "flask run & python -c \"import time\nwhile True:\n    time.sleep(5)\""):
            self.assertDenied(self.run_hook(self.payload(command)))

    def test_loop_inside_heredoc_is_not_a_wait(self):
        command = ("cat > /tmp/poll.sh <<'EOF'\nwhile true; do sleep 5; done\nEOF\n"
                   "python -m http.server 8000")
        self.assertEqual(self.run_hook(self.payload(command)), "")

    def test_off_switch(self):
        self.assertEqual(self.run_hook(self.payload("./gradlew test"), GUARD_BACKGROUND_OFF="1"), "")

    def test_bad_input(self):
        for raw in (b"", b"not json", b"[1,2]", b'{"run_in_background": true}', b'"run_in_background"',
                    b'{"tool_name": "Bash", "agent_id": "a", "tool_input": "run_in_background"}'):
            self.assertEqual(self.run_hook(raw=raw), "", raw)


if __name__ == "__main__":
    unittest.main(verbosity=1)
