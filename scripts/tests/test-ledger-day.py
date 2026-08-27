#!/usr/bin/env python3
"""Tests for ledger-day.py against synthetic transcripts in a temp folder.

Nothing here runs the official analyzer: its output is injected through
`--analyzer-json`, and `fixtures/analyzer-sample.json` is a real run trimmed to
the fields the ledger reads. Nothing here needs a git repository either: the
merge list arrives through `--git-log-file` in the same format the script asks
git for (`%h|%ad|%s`).

The two context KPIs are tested on synthetic rows, where every number is
chosen so the definition is visible in the assertion. The cut of a real
orchestrator transcript that pins the same definitions against a live
session stays in the private evidence repository, because that fixture is a
work transcript and not sample data.

Run:
    python test-ledger-day.py
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "ledger-day.py")
FIXTURES = os.path.join(HERE, "fixtures")

_spec = importlib.util.spec_from_file_location("ledger_day", SCRIPT)
ledger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger)

# The twin is config-driven, so the suite pins the mapping its fixtures assume:
# a `ledger-config.json` sitting beside the script must never decide whether
# these assertions are green.
ledger.PROJECT_PREFIXES = {
    "c--repo-myapp": "myapp",
    "c--repo": "myapp",
    "c--repo-claude-base": "myapp",
    "c--repo-myapp-evidence": "myapp",
    "c--repo-thirdapp": "thirdapp",
    "c--repo-otherapp": "otherapp",
}
ledger.PROJECT_ORDER = ["myapp", "thirdapp", "otherapp", "other"]
ledger.CONFIG_FEATURE_PROJECT = "myapp"


def utc(when):
    """Local naive time to the UTC stamp a transcript writes."""
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def assistant(ts, rid, tokens, tools=(), cmds=(), prompts=(), text="working"):
    """One assistant line: content blocks plus the usage of the whole response.

    `prompts` fills the `prompt` input of the launch tools in `tools`, in the
    same order, which is what the writing KPI measures.
    """
    content = [{"type": "text", "text": text}]
    launches = 0
    for i, name in enumerate(tools):
        block = {"type": "tool_use", "id": "%s-t%d" % (rid, i), "name": name,
                 "input": {}}
        if i < len(cmds):
            block["input"] = {"command": cmds[i]}
        if name in ledger.LAUNCH_TOOLS and launches < len(prompts):
            block["input"] = {"prompt": prompts[launches]}
            launches += 1
        content.append(block)
    return {"type": "assistant", "timestamp": ts, "requestId": rid,
            "uuid": rid + "-u", "isSidechain": False,
            "message": {"id": rid, "model": "claude-opus-5", "content": content,
                        "usage": {"input_tokens": 0,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": tokens - 500,
                                  "output_tokens": 500}}}


def user_human(ts, text="do the thing"):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": text}}


def user_tool_result(ts, tool_use_id="x-t0", body="ok"):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user",
                        "content": [{"type": "tool_result",
                                     "tool_use_id": tool_use_id,
                                     "content": body}]}}


def user_task_notification(ts, pad=""):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user",
                        "content": "<task-notification>\n<task-id>b1</task-id>\n"
                                   "</task-notification>" + pad}}


def user_compaction(ts):
    return {"type": "user", "timestamp": ts, "isCompactSummary": True,
            "uuid": "compact-" + ts,
            "message": {"role": "user",
                        "content": "This session is being continued from a "
                                   "previous conversation."}}


def system_compaction(ts, pre, post):
    """The row the harness writes for the compaction, read for its metadata."""
    return {"type": "system", "timestamp": ts, "subtype": "compact_boundary",
            "uuid": "sys-" + ts,
            "compactMetadata": {"trigger": "auto", "preTokens": pre,
                                "postTokens": post}}


def write_jsonl(path, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


class LedgerFixture(unittest.TestCase):
    """A window with two sessions, one fork, one status read and three narrations."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ledger-test-")
        now = dt.datetime.now().replace(microsecond=0)
        cls.start = now - dt.timedelta(hours=2)
        cls.end = now
        t = utc(now - dt.timedelta(minutes=90))
        projects = os.path.join(cls.tmp, "projects")

        # Session A: two productive turns, plus a fork subagent of 10000 tokens.
        write_jsonl(os.path.join(projects, "C--Repo-myapp", "aaaa1111-sess.jsonl"), [
            user_human(t),
            assistant(t, "ra1", 1100, tools=["Agent"]),
            user_tool_result(t, "ra1-t0"),
            assistant(t, "ra2", 1500, tools=["Bash"], cmds=["git commit -m 'w1'"]),
        ])
        fork = os.path.join(projects, "C--Repo-myapp", "aaaa1111-sess",
                            "subagents", "agent-af00000000000001")
        write_jsonl(fork + ".jsonl", [
            user_compaction(t),
            assistant(t, "rf1", 5000),
            user_tool_result(t, "rf1-t0"),
            assistant(t, "rf2", 5000),
        ])
        with open(fork + ".meta.json", "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"agentType": "fork", "description": "fork of the parent",
                       "toolUseId": "ra1-t0", "spawnDepth": 1, "isFork": True}, fh)

        # Session B, the orchestrator folder: one status read, three narration
        # turns, and a final write so the last turn is not the narration one.
        write_jsonl(os.path.join(projects, "C--Repo", "bbbb2222-sess.jsonl"), [
            user_human(t),
            assistant(t, "rb1", 2000, tools=["Bash"], cmds=["git status --short"]),
            user_tool_result(t, "rb1-t0"),
            assistant(t, "rb2", 3000),
            user_tool_result(t, "rb1-t0"),
            assistant(t, "rb3", 3000),
            user_task_notification(t),
            assistant(t, "rb4", 3000),
            user_tool_result(t, "rb1-t0"),
            assistant(t, "rb5", 4000, tools=["Write"]),
        ])

        # A quota log with one reset: 60 to 70, reset to 5, then 12. 22 points.
        cls.usage_log = os.path.join(cls.tmp, "usage-log.csv")
        rows = ["time,account,five_hour_used,seven_day_used,context_pct"]
        for minutes, value in ((100, 60), (80, 70), (60, 5), (40, 12)):
            when = (now - dt.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
            rows.append("%s,tester,20,%d,50" % (when, value))
        with open(cls.usage_log, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(rows) + "\n")

        # Two feature merges and one merge that is listed but not counted.
        cls.git_log = os.path.join(cls.tmp, "git-log.txt")
        with open(cls.git_log, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("aaa1111|2026-08-27 09:10:00 -0500|Merge branch 'r42-lists-r1' [skip ci]\n")
            fh.write("bbb2222|2026-08-27 10:20:00 -0500|Merge branch 'spec-f33-w2' [skip ci]\n")
            fh.write("ccc3333|2026-08-27 11:30:00 -0500|Merge pull request #7 from a fork\n")

        # The analyzer output keeps the real shape and one break of 1000 tokens
        # inside the window, so the arithmetic of the row stays checkable.
        with open(os.path.join(FIXTURES, "analyzer-sample.json"), encoding="utf-8") as fh:
            sample = json.load(fh)
        sample["cache_breaks"] = [{"ts": t, "session": "aaaa1111",
                                   "project": "C--Repo-myapp",
                                   "uncached": 1000, "total": 1000, "kind": "main"}]
        sample["by_project"] = {}
        cls.analyzer_json = os.path.join(cls.tmp, "analyzer.json")
        with open(cls.analyzer_json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(sample, fh)

        cls.args = argparse.Namespace(
            projects_dir=projects, max_file_mb=512,
            analyzer_json=cls.analyzer_json, no_analyzer=False, analyzer=None,
            no_token_ledger=True, usage_log=cls.usage_log, repo=None,
            git_log_file=cls.git_log)
        cls.report = ledger.build_report(cls.args, cls.start, cls.end, now)
        cls.row = cls.report["rows"][0]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_one_project_and_its_folders(self):
        self.assertEqual([r["project"] for r in self.report["rows"]], ["myapp"])
        self.assertEqual(self.row["folders"], ["C--Repo", "C--Repo-myapp"])

    def test_total_tokens(self):
        self.assertEqual(self.row["tokens"], 27600)

    def test_points_climbed_over_a_reset(self):
        rows, column = ledger.read_usage_log(self.usage_log)
        self.assertEqual(column, "seven_day_used")
        climbed, samples, resets = ledger.points_climbed(rows, self.start, self.end)
        self.assertEqual(climbed, 22.0)
        self.assertEqual(samples, 4)
        self.assertEqual(len(resets), 1)
        self.assertEqual(self.report["account_points"], 22.0)
        self.assertEqual(self.row["points"], 22.0)

    def test_feature_count_and_points_per_feature(self):
        self.assertEqual(len(self.report["features"]), 2)
        self.assertEqual(len(self.report["other_merges"]), 1)
        self.assertEqual(self.row["features"], 2)
        self.assertEqual(self.row["points_per_feature"], 11.0)

    def test_bucket_tokens(self):
        self.assertEqual(self.row["fork_tokens"], 10000)
        self.assertEqual(self.row["forks"], 1)
        self.assertEqual(self.row["buckets"].get("status", 0), 2000)
        self.assertEqual(self.row["buckets"].get("narration", 0), 9000)
        self.assertEqual(self.row["buckets"].get("probes", 0), 0)
        self.assertEqual(self.row["bucket_turns"].get("narration", 0), 3)
        self.assertEqual(self.row["bucket_turns"].get("status", 0), 1)
        self.assertEqual(self.row["cache_tokens"], 1000)

    def test_waste_share(self):
        self.assertEqual(self.row["waste_pct"], 79.7)
        self.assertEqual(self.row["waste_pct_no_overlap"], 76.1)

    def test_rendered_line(self):
        line = ledger.render_row(self.report, self.row)
        self.assertIn("| myapp | 27.6 | 22.0 | 2 | 11.00 | 79.7% "
                      "| 10.0 / 2.0 / 9.0 / 0.0 / 1.0 |", line)
        self.assertTrue(line.startswith("| "))
        self.assertEqual(line.count("|"), ledger.LEDGER_HEADER.count("|"))

    def test_context_columns_are_present_even_with_no_compaction(self):
        # The only compaction of this window is inside a subagent transcript,
        # and the KPI reads main transcripts only, so the cell stays at zero.
        ctx = self.row["context"]
        self.assertEqual(ctx["compactions"], 0)
        self.assertEqual(ctx["launches"]["count"], 1)
        self.assertEqual(ctx["notifications"]["count"], 1)
        self.assertGreater(ctx["written_chars"], 0)
        self.assertGreater(ctx["result_chars"], 0)
        line = ledger.render_row(self.report, self.row)
        self.assertTrue(line.rstrip().endswith("| 0 / - / - / - | 1 / 0.1 / 0.1 / 0.0 |"),
                        line)

    def test_sessions_in_the_breakdown(self):
        sessions = {s["id"]: s for s in self.row["sessions"]}
        self.assertEqual(sorted(sessions), ["aaaa1111", "bbbb2222"])
        self.assertEqual(sessions["aaaa1111"]["subagents"], 1)
        self.assertEqual(sessions["aaaa1111"]["forks"], 1)
        self.assertEqual(sessions["bbbb2222"]["turns"], 5)

    def test_command_line_writes_both_files(self):
        ledger_dir = os.path.join(self.tmp, "out")
        cmd = [sys.executable, SCRIPT,
               "--since", self.start.strftime("%Y-%m-%d %H:%M:%S"),
               "--until", self.end.strftime("%Y-%m-%d %H:%M:%S"),
               "--ledger-dir", ledger_dir,
               "--projects-dir", self.args.projects_dir,
               "--usage-log", self.usage_log,
               "--git-log-file", self.git_log,
               "--analyzer-json", self.analyzer_json,
               "--no-token-ledger"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        self.assertEqual(out.returncode, 0, out.stderr)
        with open(os.path.join(ledger_dir, "ledger.md"), encoding="utf-8") as fh:
            written = fh.read()
        self.assertIn(ledger.LEDGER_HEADER, written)
        # the subprocess reads the local `ledger-config.json`, so the project
        # name is whatever that file maps the folder to; the row is asserted
        # from the tokens on, which no mapping can move
        self.assertIn("| 27.6 | 22.0 | 2 | 11.00 | 79.7%", written)
        daily = os.listdir(os.path.join(ledger_dir, "daily"))
        self.assertEqual(len(daily), 1)
        with open(os.path.join(ledger_dir, "daily", daily[0]), encoding="utf-8") as fh:
            breakdown = fh.read()
        self.assertIn("## Sources that ran", breakdown)
        self.assertIn("Merge branch 'r42-lists-r1'", breakdown)
        self.assertIn("Merge pull request #7", breakdown)


class BucketRules(unittest.TestCase):
    """The bucket functions on their own, one turn at a time."""

    def turn(self, **kw):
        base = {"ts": dt.datetime.now(), "model": "m", "tools": [], "cmds": [],
                "tokens": 100, "ctx": 100, "out": 0, "wake": "tool_result"}
        base.update(kw)
        return base

    def test_status_reader_needs_only_reads(self):
        self.assertTrue(ledger.is_status_reader(
            self.turn(tools=["Bash"], cmds=["git log --oneline -5"])))
        self.assertTrue(ledger.is_status_reader(
            self.turn(tools=["Bash", "Bash"],
                      cmds=["herdr agent list", "git status"])))
        self.assertFalse(ledger.is_status_reader(
            self.turn(tools=["Bash"], cmds=["git log -1 && git commit -m x"])))
        self.assertFalse(ledger.is_status_reader(
            self.turn(tools=["Read"], cmds=[])))
        self.assertFalse(ledger.is_status_reader(self.turn()))

    def test_a_redirection_of_stderr_is_not_a_write(self):
        self.assertTrue(ledger.is_status_reader(
            self.turn(tools=["Bash"], cmds=["git status 2>&1"])))
        self.assertFalse(ledger.is_status_reader(
            self.turn(tools=["Bash"], cmds=["git status > out.txt"])))

    def test_narration_only_after_a_machine_wake(self):
        self.assertTrue(ledger.is_narration(self.turn(wake="tool_result")))
        self.assertTrue(ledger.is_narration(self.turn(wake="task_notification")))
        self.assertFalse(ledger.is_narration(self.turn(wake="human")))
        self.assertFalse(ledger.is_narration(self.turn(wake="tool_result", final=True)))
        self.assertFalse(ledger.is_narration(self.turn(tools=["Read"])))

    def test_probe_is_a_scheduled_wake_that_changed_nothing(self):
        self.assertTrue(ledger.is_probe(self.turn(wake="scheduled")))
        self.assertTrue(ledger.is_probe(
            self.turn(wake="monitor", tools=["Bash"], cmds=["git status"])))
        self.assertFalse(ledger.is_probe(
            self.turn(wake="scheduled", tools=["Write"])))
        self.assertFalse(ledger.is_probe(self.turn(wake="tool_result")))

    def test_one_bucket_per_turn(self):
        both = self.turn(wake="scheduled", tools=["Bash"], cmds=["git status"])
        self.assertEqual(ledger.bucket_of(both), "status")
        self.assertEqual(ledger.bucket_of(self.turn(wake="scheduled")), "probes")
        self.assertIsNone(ledger.bucket_of(self.turn(tools=["Write"])))


class ProjectMapping(unittest.TestCase):
    """Longest matching prefix, and nothing is dropped silently."""

    def test_folders(self):
        cases = {
            "C--Repo-myapp": "myapp",
            "C--Repo-myapp-w99": "myapp",
            "C--repo": "myapp",
            "C--Repo": "myapp",
            "C--Repo-claude-base": "myapp",
            "C--Repo-myapp-evidence": "myapp",
            "C--Repo-thirdapp": "thirdapp",
            "C--Repo-otherapp": "otherapp",
            "C--Users-dev-01-Personal": "other",
        }
        for folder, expected in cases.items():
            self.assertEqual(ledger.project_of(folder), expected, folder)

    def test_real_analyzer_fixture_is_readable(self):
        with open(os.path.join(FIXTURES, "analyzer-sample.json"), encoding="utf-8") as fh:
            sample = json.load(fh)
        self.assertIn("by_project", sample)
        for folder, stats in sample["by_project"].items():
            self.assertIn("total", stats["input_tokens"])
            self.assertIn(ledger.project_of(folder),
                          ("myapp", "thirdapp", "otherapp", "other"))
        for cb in sample["cache_breaks"]:
            self.assertIn("uncached", cb)
            self.assertIsNotNone(ledger.local_ts(cb["ts"]))


class ForkFallback(unittest.TestCase):
    """A subagent with no isFork flag whose first message is a compaction summary."""

    def test_fallback_flag(self):
        tmp = tempfile.mkdtemp(prefix="ledger-fork-")
        try:
            now = dt.datetime.now().replace(microsecond=0)
            start, end = now - dt.timedelta(hours=1), now
            t = utc(now - dt.timedelta(minutes=30))
            projects = os.path.join(tmp, "projects")
            write_jsonl(os.path.join(projects, "C--Repo-myapp", "cccc3333.jsonl"),
                        [user_human(t), assistant(t, "r1", 1000, tools=["Agent"])])
            sub = os.path.join(projects, "C--Repo-myapp", "cccc3333",
                               "subagents", "agent-a99.jsonl")
            write_jsonl(sub, [user_compaction(t), assistant(t, "r2", 7000)])
            args = argparse.Namespace(
                projects_dir=projects, max_file_mb=512, analyzer_json=None,
                no_analyzer=True, analyzer=None, no_token_ledger=True,
                usage_log=os.path.join(tmp, "missing.csv"), repo=None,
                git_log_file=None)
            report = ledger.build_report(args, start, end, now)
            row = report["rows"][0]
            self.assertEqual(row["forks"], 1)
            self.assertEqual(row["fork_tokens"], 7000)
            self.assertFalse(report["cache_measurable"])
            self.assertIn("n/m", ledger.render_row(report, row))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ContextKPIsOnSyntheticRows(unittest.TestCase):
    """The two KPI definitions, one number at a time.

    The peak is the row before the boundary and the floor the row after it, the
    rate divides by the span of the rows inside the window, and the character
    counters see only what the window holds.
    """

    def scan(self, entries, start, end):
        tmp = tempfile.mkdtemp(prefix="ledger-ctx-")
        try:
            path = os.path.join(tmp, "s.jsonl")
            write_jsonl(path, entries)
            return ledger.context_summary(ledger.scan_file(path, start, end)["context"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_peak_is_before_the_boundary_and_floor_after_it(self):
        now = dt.datetime.now().replace(microsecond=0)
        start, end = now - dt.timedelta(hours=4), now
        first = now - dt.timedelta(hours=3)
        second = now - dt.timedelta(hours=1)
        entries = [
            assistant(utc(first), "r1", 50500),                 # ctx 50000
            assistant(utc(first + dt.timedelta(minutes=5)), "r2", 160500),
            system_compaction(utc(first + dt.timedelta(minutes=6)), 174977, 15851),
            user_compaction(utc(first + dt.timedelta(minutes=6))),
            assistant(utc(first + dt.timedelta(minutes=7)), "r3", 70500),
            assistant(utc(second), "r4", 150500),
            user_compaction(utc(second + dt.timedelta(minutes=1))),
            assistant(utc(second + dt.timedelta(minutes=2)), "r5", 66500),
        ]
        ctx = self.scan(entries, start, end)
        self.assertEqual(ctx["compactions"], 2)
        self.assertEqual([e["peak"] for e in ctx["events"]], [160000, 150000])
        self.assertEqual([e["floor"] for e in ctx["events"]], [70000, 66000])
        self.assertEqual(ctx["peak_min"], 150000)
        self.assertEqual(ctx["peak_max"], 160000)
        self.assertEqual(ctx["floor_min"], 66000)
        self.assertEqual(ctx["floor_max"], 70000)
        # the metadata of the system row travels with the first boundary only
        self.assertEqual(ctx["events"][0]["pre_tokens"], 174977)
        self.assertEqual(ctx["events"][0]["post_tokens"], 15851)
        self.assertIsNone(ctx["events"][1]["pre_tokens"])

    def test_rate_divides_by_the_active_hours_in_the_window(self):
        now = dt.datetime.now().replace(microsecond=0)
        start, end = now - dt.timedelta(hours=5), now
        base = now - dt.timedelta(hours=4)
        entries = [assistant(utc(base), "r1", 100500),
                   user_compaction(utc(base + dt.timedelta(hours=1))),
                   assistant(utc(base + dt.timedelta(hours=1, minutes=1)), "r2", 20500),
                   user_compaction(utc(base + dt.timedelta(hours=2))),
                   assistant(utc(base + dt.timedelta(hours=2, minutes=1)), "r3", 30500)]
        ctx = self.scan(entries, start, end)
        self.assertEqual(ctx["compactions"], 2)
        self.assertEqual(ctx["active_hours"], 2.02)
        self.assertEqual(ctx["per_hour"], round(2 / 2.0166666, 2))

    def test_a_boundary_outside_the_window_is_not_counted(self):
        now = dt.datetime.now().replace(microsecond=0)
        old = now - dt.timedelta(hours=6)
        entries = [assistant(utc(old), "r0", 90500),
                   user_compaction(utc(old + dt.timedelta(minutes=1))),
                   assistant(utc(now - dt.timedelta(minutes=30)), "r1", 40500)]
        ctx = self.scan(entries, now - dt.timedelta(hours=1), now)
        self.assertEqual(ctx["compactions"], 0)
        self.assertIsNone(ctx["per_hour"])

    def test_launch_and_notification_statistics(self):
        now = dt.datetime.now().replace(microsecond=0)
        start, end = now - dt.timedelta(hours=2), now
        t = utc(now - dt.timedelta(hours=1))
        entries = [
            assistant(t, "r1", 1000, tools=["Agent", "Agent"],
                      prompts=["a" * 100, "b" * 300]),
            assistant(t, "r2", 1000, tools=["Agent"], prompts=["c" * 900]),
            user_task_notification(t, pad="x" * 50),
            user_task_notification(t, pad="x" * 250),
            user_tool_result(t, body="y" * 1000),
        ]
        ctx = self.scan(entries, start, end)
        self.assertEqual(ctx["launches"],
                         {"count": 3, "total": 1300, "median": 300, "p90": 900})
        base = len("<task-notification>\n<task-id>b1</task-id>\n</task-notification>")
        self.assertEqual(ctx["notifications"]["count"], 2)
        self.assertEqual(ctx["notifications"]["total"], 2 * base + 300)
        self.assertEqual(ctx["result_chars"], 1000)
        self.assertEqual(ctx["result_count"], 1)
        # own writing: three text blocks plus the JSON of every tool input
        self.assertEqual(ctx["written_chars"],
                         2 * len("working") + sum(
                             len(json.dumps({"prompt": p}, ensure_ascii=False))
                             for p in ("a" * 100, "b" * 300, "c" * 900)))

    def test_a_window_with_no_main_transcript_reports_zeros(self):
        ctx = ledger.context_summary(ledger.empty_context())
        self.assertEqual(ctx["compactions"], 0)
        self.assertIsNone(ctx["per_hour"])
        self.assertEqual(ctx["launches"]["count"], 0)
        self.assertEqual(ledger.render_context_cells(None),
                         ("0 / - / - / -", "0 / 0.0 / 0.0 / 0.0"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
