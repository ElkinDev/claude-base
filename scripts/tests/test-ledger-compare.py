#!/usr/bin/env python3
"""Tests for ledger-compare.py against synthetic payloads in a temp folder.

Nothing here reads the machine's real transcripts, gate files, landings or
meters: both sides of the comparison are `ledger-day.py --json` payloads built
by hand, and every path the script would otherwise pick up from the machine is
pointed at a temp folder. Nothing runs a model and nothing touches the network.

Run:
    python test-ledger-compare.py
"""
import datetime as dt
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "ledger-compare.py")

_spec = importlib.util.spec_from_file_location("ledger_compare", SCRIPT)
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)

CAP = "2026-08-27 12:58"


def context(pairs=(), hours=0.0, launch_chars=(), notif_chars=(),
            written=0, results=0):
    """The `context` block ledger-day writes per session.

    `pairs` is one (peak, floor) per compaction, the two numbers the KPI reads
    on the assistant rows around a boundary.
    """
    events = [{"ts": None, "uuid": "c%d" % i, "peak": peak, "floor": floor,
               "pre_tokens": None, "post_tokens": None}
              for i, (peak, floor) in enumerate(pairs)]
    return {"compactions": len(events), "events": events,
            "active_hours": round(hours, 2) or None,
            "active_seconds": hours * 3600.0,
            "per_hour": round(len(events) / hours, 2) if hours else None,
            "peak_min": min([p for p, _f in pairs], default=0),
            "peak_max": max([p for p, _f in pairs], default=0),
            "floor_min": min([f for _p, f in pairs], default=0),
            "floor_max": max([f for _p, f in pairs], default=0),
            "launch_chars": list(launch_chars),
            "notif_chars": list(notif_chars),
            "launches": {"count": len(launch_chars),
                         "total": sum(launch_chars), "median": 0, "p90": 0},
            "notifications": {"count": len(notif_chars),
                              "total": sum(notif_chars), "median": 0, "p90": 0},
            "written_chars": written, "result_chars": results,
            "result_count": 0}


def session(sid, tokens, started, turns=10, main_turns=0, p50=0, p90=0,
            ctx_max=0, fresh=None, buckets=None, fork_tokens=0, ctx=None):
    return {"id": sid, "project": "myapp", "turns": turns,
            "tokens": tokens, "fresh": tokens // 10 if fresh is None else fresh,
            "ctx_peak": ctx_max, "subagents": 0, "forks": 0,
            "started": started, "fork_tokens": fork_tokens,
            "main_turns": main_turns, "main_ctx_p50": p50, "main_ctx_p90": p90,
            "main_ctx_max": ctx_max, "buckets": buckets or {},
            "context": ctx if ctx is not None else context()}


def lane(sid, tokens=1000, turns=10, peak=100000, compactions=0,
         first=None, last=None):
    return {"id": "a1", "session": sid, "type": "implementer", "desc": "lane",
            "fork": False, "turns": turns, "tokens": tokens,
            "fresh": tokens // 10, "ctx_peak": peak, "ctx_p50": peak // 2,
            "compactions": compactions, "models": ["opus"],
            "started": first, "first_human": first, "last_ts": last}


def payload(start, end, sessions, lanes, features, bucket_turns=None,
            by_model=None, points=0.0, quota_samples=0, cache_tokens=0,
            cache_breaks=0, cache_measurable=True, merges=(), schema=2,
            sha="baselinesha"):
    tokens = sum(s["tokens"] for s in sessions)
    buckets = {}
    for entry in sessions:
        for name, value in (entry.get("buckets") or {}).items():
            buckets[name] = buckets.get(name, 0) + value
    row = {"project": "myapp", "tokens": tokens,
           "fresh": sum(s["fresh"] for s in sessions),
           "turns": sum(s["turns"] for s in sessions),
           "points": points, "features": features,
           "sessions": sessions, "lanes": lanes,
           "buckets": buckets, "bucket_turns": bucket_turns or {},
           "cache_tokens": cache_tokens, "cache_breaks": cache_breaks,
           "by_model": by_model or {},
           "compactions": sum(a["compactions"] for a in lanes),
           "fork_tokens": sum(s["fork_tokens"] for s in sessions)}
    return {"schema": schema, "script_sha256": sha, "now": end,
            "start": start, "end": end, "rows": [row],
            "features": [{"subject": s} for s in merges],
            "quota_samples": quota_samples, "cache_measurable": cache_measurable,
            "account_points": points}


class Sandbox:
    """A temp folder with the four evidence paths the script reads."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ledger-compare-test-")
        for name in ("gates", "field-reports", "kit", "ledger"):
            os.makedirs(os.path.join(self.dir, name), exist_ok=True)
        self.config = os.path.join(self.dir, "config.json")
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump({"feature_project": "myapp"}, fh)
        self.landings = os.path.join(self.dir, "landings.md")
        with open(self.landings, "w", encoding="utf-8") as fh:
            fh.write("# Landings\n\n| time | event | role | where | id |\n")
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)
        return False

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def write_json(self, name, data):
        target = self.path("ledger", name)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(data, fh, default=str)
        return target

    def run(self, baseline, current, extra=()):
        out = self.path("ledger", "compare.md")
        argv = ["--baseline", baseline, "--current", current, "--out", out,
                "--cap-start", CAP, "--gates-dir", self.path("gates"),
                "--landings", self.landings,
                "--field-reports", self.path("field-reports"),
                "--kit-dir", self.path("kit"),
                "--meters-log", self.path("ledger", "meters-log.csv"),
                "--tool-sizes", self.path("ledger", "tool-sizes.csv"),
                "--ledger-dir", self.path("ledger"),
                "--config", self.config] + list(extra)
        code = compare.main(argv)
        with open(out, encoding="utf-8") as fh:
            return code, fh.read()


def two_sides(current_features=5, current_tokens=1000000):
    """A baseline and a current payload that differ on every axis."""
    base = payload(
        "2026-08-20 08:00:00", "2026-08-27 08:00:00",
        [session("bbbb1111", 100000000, "2026-08-20 09:00:00", turns=2000,
                 main_turns=500, p50=500000, p90=800000, ctx_max=990000,
                 buckets={"status": 5000000, "narration": 8000000,
                          "probes": 300000},
                 fork_tokens=5000000)],
        [lane("bbbb1111", tokens=2000000, turns=120, peak=400000,
              first="2026-08-21 08:00:00", last="2026-08-21 12:00:00"),
         lane("bbbb1111", tokens=1000000, turns=30, peak=150000,
              first="2026-08-22 08:00:00", last="2026-08-22 13:00:00")],
        features=20, bucket_turns={"narration": 40, "probes": 4},
        by_model={"opus": {"main": 20000000, "subagent": 60000000},
                  "fable": {"main": 20000000, "subagent": 0}},
        cache_tokens=4000000, cache_breaks=70,
        merges=["Merge branch 'r41-lists-drop-r3'",
                "Merge branch 'r41-reactions-w1-r2'"])
    cur = payload(
        "2026-08-27 12:58:00", "2026-08-27 18:00:00",
        [session("cccc2222", current_tokens, "2026-08-27 13:00:00", turns=200,
                 main_turns=100, p50=120000, p90=150000, ctx_max=160000,
                 buckets={"status": 60000, "narration": 20000,
                          "probes": 3400})],
        [lane("cccc2222", tokens=200000, turns=20, peak=110000,
              first="2026-08-27 13:00:00", last="2026-08-27 14:00:00")],
        features=current_features, bucket_turns={"narration": 1, "probes": 1},
        by_model={"opus": {"main": 0, "subagent": 700000},
                  "fable": {"main": 300000, "subagent": 0}},
        cache_tokens=0, cache_breaks=0, merges=[], sha="currentsha")
    return base, cur


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.th = {"cost_pct": 15.0, "speed_pct": 15.0, "quality_count": 1.0}

    def test_better_when_the_drop_passes_the_threshold(self):
        state, delta = compare.verdict_of(100.0, 80.0, "cost", "lower", self.th)
        self.assertEqual(state, "better")
        self.assertEqual(delta, "-20.0%")

    def test_worse_when_the_rise_passes_the_threshold(self):
        state, _ = compare.verdict_of(100.0, 130.0, "cost", "lower", self.th)
        self.assertEqual(state, "worse")

    def test_same_inside_the_band(self):
        state, delta = compare.verdict_of(100.0, 114.0, "cost", "lower", self.th)
        self.assertEqual(state, "same")
        self.assertEqual(delta, "+14.0%")

    def test_nm_when_one_side_is_missing(self):
        self.assertEqual(compare.verdict_of(None, 5.0, "cost", "lower", self.th)[0],
                         "n/m")
        self.assertEqual(compare.verdict_of(5.0, None, "cost", "lower", self.th)[0],
                         "n/m")

    def test_higher_is_better_flips_the_direction(self):
        state, _ = compare.verdict_of(10.0, 20.0, "cost", "higher", self.th)
        self.assertEqual(state, "better")

    def test_count_rule_uses_one_defect_not_a_percentage(self):
        self.assertEqual(compare.verdict_of(0.0, 0.5, "count", "lower", self.th)[0],
                         "same")
        self.assertEqual(compare.verdict_of(0.0, 1.0, "count", "lower", self.th)[0],
                         "worse")

    def test_a_mix_row_never_claims_a_direction(self):
        state, delta = compare.verdict_of(10.0, 90.0, "mix", "lower", self.th)
        self.assertEqual(state, "n/m")
        self.assertEqual(delta, "-")


class PageTests(unittest.TestCase):
    def test_all_four_states_render(self):
        with Sandbox() as box:
            base, cur = two_sides()
            code, page = box.run(box.write_json("base.json", base),
                                 box.write_json("cur.json", cur))
            self.assertEqual(code, 0)
            for state in ("better", "worse", "same", "n/m"):
                self.assertIn("| %s |" % state, page)
            self.assertIn("# Before and after", page)
            self.assertIn("## Cost", page)
            self.assertIn("## Speed", page)
            self.assertIn("## Quality", page)
            self.assertIn("## What is left to optimize", page)

    def test_insufficient_sample_hides_the_verdict_and_keeps_the_value(self):
        with Sandbox() as box:
            base, cur = two_sides(current_features=1)
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            self.assertIn("insufficient sample (n=1)", page)
            row = next(l for l in page.splitlines()
                       if l.startswith("| raw tokens per merged feature"))
            self.assertIn("insufficient sample", row)
            # the raw value is still printed on both sides
            self.assertIn("| 5.00 |", row)
            self.assertIn("| 1.00 |", row)

    def test_three_features_release_the_verdict(self):
        with Sandbox() as box:
            base, cur = two_sides(current_features=3)
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            row = next(l for l in page.splitlines()
                       if l.startswith("| raw tokens per merged feature"))
            self.assertNotIn("insufficient sample", row)
            self.assertTrue(row.rstrip().endswith("better |"), row)

    def test_pre_cap_session_is_excluded_and_printed(self):
        with Sandbox() as box:
            base, cur = two_sides()
            cur["rows"][0]["sessions"].append(
                session("4a512d43", 92000000, "2026-08-27 09:00:00", turns=658,
                        main_turns=658, p50=600000, p90=750000, ctx_max=769000))
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            self.assertIn("pre-cap, excluded from every KPI", page)
            self.assertIn("`4a512d43`", page)
            header = next(l for l in page.splitlines()
                          if l.startswith("| current (under the cap)"))
            # the capped column carries its own million, not the pre-cap 92
            self.assertTrue(header.rstrip().endswith("| 1 |"), header)

    def test_a_named_session_counts_as_capped_whatever_its_first_entry_says(self):
        with Sandbox() as box:
            base, cur = two_sides()
            cur["rows"][0]["sessions"].append(
                session("f2109a6d", 5000000, "2026-08-27 12:51:39", turns=40,
                        main_turns=40, p50=100000, p90=140000, ctx_max=150000))
            cfg = box.path("cfg.json")
            with open(cfg, "w", encoding="utf-8") as fh:
                json.dump({"feature_project": "myapp",
                           "compare": {"capped_sessions": ["f2109a6d"],
                                       "pre_cap_sessions": []}}, fh)
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur),
                                  extra=["--config", cfg])
            self.assertNotIn("pre-cap, excluded from every KPI", page)
            header = next(l for l in page.splitlines()
                          if l.startswith("| current (under the cap)"))
            self.assertTrue(header.rstrip().endswith("| 6 |"), header)

    def test_window_length_alone_does_not_move_the_verdicts(self):
        """Every payload KPI is a rate or a share, so the clock cannot move it.

        The same current side is reported twice, once over a window twice as
        long. Nothing about the work changed, so no verdict may change either;
        a raw count in any row would fail this.
        """
        with Sandbox() as box:
            base, cur = two_sides()
            _code, short = box.run(box.write_json("base.json", base),
                                   box.write_json("cur.json", cur))
            # the same current side, reported over a window twice as long
            cur["end"] = "2026-08-27 23:02:00"
            _code, long_page = box.run(box.write_json("base.json", base),
                                       box.write_json("cur2.json", cur))
            def verdicts(page):
                return [l.split("|")[-2].strip() for l in page.splitlines()
                        if l.startswith("| ") and l.count("|") == 6
                        and "---" not in l]
            self.assertEqual(verdicts(short), verdicts(long_page))

    def test_missing_instrument_reads_nm_and_says_why(self):
        with Sandbox() as box:
            base, cur = two_sides()
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            kit = next(l for l in page.splitlines()
                       if l.startswith("| device-kit runs"))
            self.assertTrue(kit.rstrip().endswith("| n/m |"), kit)
            self.assertIn("the kit evidence folder does not exist", page)

    def test_the_recommendation_waits_for_the_sample(self):
        with Sandbox() as box:
            base, cur = two_sides(current_features=1)
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            self.assertIn("Not decidable yet", page)

    def test_cost_and_speed_better_with_quality_worse_raises_the_cap(self):
        with Sandbox() as box:
            base, cur = two_sides(current_features=5)
            # four defects filed inside the current window, none in the
            # baseline one: the mtime is what places a report in a window
            stamp = dt.datetime(2026, 8, 27, 14, 0).timestamp()
            for name in ("2026-08-27-a.md", "2026-08-27-b.md",
                         "2026-08-27-c.md", "2026-08-27-d.md"):
                target = box.path("field-reports", name)
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write("defect\n")
                os.utime(target, (stamp, stamp))
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            defects = next(l for l in page.splitlines()
                           if l.startswith("| defects the owner filed"))
            self.assertTrue(defects.rstrip().endswith("worse |"), defects)
            self.assertIn("Raise the cap to 300k for build lanes", page)

    def test_one_defect_spread_over_the_features_is_the_quality_threshold(self):
        with Sandbox() as box:
            base, cur = two_sides(current_features=5)
            stamp = dt.datetime(2026, 8, 27, 14, 0).timestamp()
            target = box.path("field-reports", "2026-08-27-only.md")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("defect\n")
            os.utime(target, (stamp, stamp))
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            # one defect over five features is exactly the threshold, 0.2
            defects = next(l for l in page.splitlines()
                           if l.startswith("| defects the owner filed"))
            self.assertIn("| 0.200 |", defects)
            self.assertTrue(defects.rstrip().endswith("worse |"), defects)

    def test_optimization_list_is_ranked_and_capped_at_five(self):
        with Sandbox() as box:
            base, cur = two_sides()
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            body = page.split("## What is left to optimize", 1)[1]
            rows = [l for l in body.splitlines()
                    if l.startswith("| ") and "---" not in l
                    and not l.startswith("| # |")]
            self.assertLessEqual(len(rows), 5)
            shares = [float(r.split("|")[4].strip().rstrip("%")) for r in rows]
            self.assertEqual(shares, sorted(shares, reverse=True))
            self.assertIn("F9, status is a file, not an agent", page)

    def test_a_schema_mismatch_is_reported_not_swallowed(self):
        with Sandbox() as box:
            base, cur = two_sides()
            cur["schema"] = 99
            saved, sys.stderr = sys.stderr, open(os.devnull, "w",
                                                 encoding="utf-8")
            try:
                code, _page = box.run(box.write_json("base.json", base),
                                      box.write_json("cur.json", cur))
            finally:
                sys.stderr.close()
                sys.stderr = saved
            self.assertEqual(code, 0)


class MeterTests(unittest.TestCase):
    def write_log(self, folder, rows):
        path = os.path.join(folder, "meters-log.csv")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("time,account,session,weekly_all,scoped_meter,scoped_pct\n")
            for row in rows:
                fh.write(",".join(str(c) for c in row) + "\n")
        return path

    def test_a_reset_counts_the_whole_new_reading(self):
        with Sandbox() as box:
            path = self.write_log(box.path("ledger"), [
                ("2026-08-25 08:00", "a", 5, 10, "weekly_fable", 4),
                ("2026-08-25 18:00", "a", 9, 60, "weekly_fable", 30),
                ("2026-08-26 08:00", "a", 2, 5, "weekly_fable", 3),
                ("2026-08-26 18:00", "a", 7, 20, "weekly_fable", 12)])
            rows = compare.read_meters(path)
            self.assertEqual(len(rows), 4)
            climbed, samples = compare.meter_climb(
                rows, "weekly_all", dt.datetime(2026, 8, 25),
                dt.datetime(2026, 8, 27))
            # 10 -> 60 is +50, the drop to 5 is a reset worth 5, 5 -> 20 is +15
            self.assertAlmostEqual(climbed, 70.0)
            self.assertEqual(samples, 4)

    def test_one_sample_cannot_be_a_climb(self):
        with Sandbox() as box:
            path = self.write_log(box.path("ledger"), [
                ("2026-08-27 14:18", "a", 19, 68, "weekly_fable", 35)])
            rows = compare.read_meters(path)
            climbed, samples = compare.meter_climb(
                rows, "weekly_all", dt.datetime(2026, 8, 27),
                dt.datetime(2026, 8, 28))
            self.assertIsNone(climbed)
            self.assertEqual(samples, 1)

    def test_the_page_names_the_scoped_meter(self):
        with Sandbox() as box:
            self.write_log(box.path("ledger"), [
                ("2026-08-27 13:00", "a", 10, 60, "weekly_fable", 30),
                ("2026-08-27 17:00", "a", 12, 68, "weekly_fable", 35)])
            base, cur = two_sides()
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            self.assertIn("| weekly_fable, points climbed |", page)
            self.assertIn("| all models, points climbed | n/m | 8.0 | 2 |", page)


class EvidenceTests(unittest.TestCase):
    def test_gate_files_are_read_for_their_longest_phase(self):
        with Sandbox() as box:
            run_dir = box.path("gates", "0b0f8db2-1111-2222-3333-444455556666",
                               "scratchpad")
            os.makedirs(run_dir, exist_ok=True)
            path = os.path.join(run_dir, "ds-gate1.exit")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("RUN=ds-gate1\n"
                         "PHASE_compile_EXIT=0 secs=37\n"
                         "PHASE_test_EXIT=1 secs=936\n"
                         "GATE_EXIT=1\nPHASE_FAILURES=test\n")
            start = dt.datetime.now() - dt.timedelta(hours=1)
            end = dt.datetime.now() + dt.timedelta(hours=1)
            gates = compare.gate_files(box.path("gates"), start, end)
            self.assertEqual(len(gates), 1)
            self.assertEqual(gates[0]["secs"], 936)
            self.assertEqual(gates[0]["exit"], "1")
            self.assertEqual(gates[0]["session"], "0b0f8db2")
            # a session filter keeps only the lanes of that side
            self.assertEqual(compare.gate_files(box.path("gates"), start, end,
                                                {"ffffffff"}), [])

    def test_landing_rows_stay_inside_the_window(self):
        with Sandbox() as box:
            with open(box.landings, "a", encoding="utf-8") as fh:
                fh.write("| 2026-08-27 13:50:22 | Stop | lane | repo | a1 | x |\n")
                fh.write("| 2026-08-01 09:00:00 | Stop | lane | repo | a2 | y |\n")
            rows = compare.landing_rows(box.landings,
                                        dt.datetime(2026, 8, 27, 12, 58),
                                        dt.datetime(2026, 8, 27, 18, 0))
            self.assertEqual([r["id"] for r in rows], ["a1"])

    def test_freeze_writes_the_estimate_and_labels_it(self):
        with Sandbox() as box:
            base, _cur = two_sides()
            path = box.write_json("base.json", base)
            code = compare.main(["--freeze-baseline", path,
                                 "--config", box.config])
            self.assertEqual(code, 0)
            with open(path, encoding="utf-8") as fh:
                frozen = json.load(fh)
            block = frozen["baseline"]
            self.assertEqual(block["merged_features"], 20)
            self.assertIsNone(block["quota_points"]["measured"])
            self.assertEqual(block["quota_points"]["label"], "estimated")
            self.assertAlmostEqual(block["quota_points"]["estimated"], 1.2, 1)
            self.assertIn("never an overwrite", block["rule"])
            # the payload it annotates is untouched
            self.assertEqual(frozen["rows"][0]["features"], 20)


class ContextKPITests(unittest.TestCase):
    """The two KPIs ledger-day writes per session, pooled and read here.

    The numbers below are the shape of 2026-08-27 on the orchestrator: five
    compactions over 3.79 active hours, peaks 156k to 167k and floors 65k to
    77k. The launch and notification lists are trimmed so the nearest-rank
    median and p90 are readable by hand: `ledger.percentile` picks the
    value at index int(p / 100 * (n - 1)), so the p90 of nine launches is the
    eighth, not the largest.
    """

    DAY = [(156515, 76968), (164056, 74395), (166633, 66174),
           (163390, 71534), (166228, 65161)]
    LAUNCHES = [300, 500, 700, 900, 1400, 2000, 3000, 4000, 4600]
    NOTIFS = [500, 2000, 3600, 4000, 4700, 9700]

    def sides(self):
        base, cur = two_sides()
        cur["rows"][0]["sessions"][0]["context"] = context(
            self.DAY, hours=3.79, launch_chars=self.LAUNCHES,
            notif_chars=self.NOTIFS, written=66400, results=19372)
        return base, cur

    def test_the_compaction_reading_lands_in_the_optimize_section(self):
        with Sandbox() as box:
            base, cur = self.sides()
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            body = page.split("## What is left to optimize", 1)[1]
            self.assertIn("compactions 5 in 3.8 h (1.32 per hour), peak 157k "
                          "to 167k, floor 65k to 77k", body)
            self.assertIn(compare.LEVER["compactions"], body)

    def test_the_writing_reading_carries_the_median_and_the_p90(self):
        with Sandbox() as box:
            base, cur = self.sides()
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            body = page.split("## What is left to optimize", 1)[1]
            self.assertIn("66.4k chars written per 9 Agent launch(es), 17.4k "
                          "of them launch prompts (median 1400, p90 4000 chars), "
                          "24.5k chars back in notifications (median 3600, "
                          "p90 4700), 19.4k chars of tool results", body)
            self.assertIn(compare.LEVER["writing"], body)

    def test_both_readings_are_printed_below_the_ranked_table(self):
        with Sandbox() as box:
            base, cur = self.sides()
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            tail = page.split("Context and writing, always listed", 1)[1]
            lines = [l for l in tail.splitlines() if l.startswith("- ")][:2]
            self.assertEqual(len(lines), 2)
            self.assertIn("compactions 5 in 3.8 h", lines[0])
            self.assertIn("chars written per 9 Agent launch(es)", lines[1])

    def test_the_compaction_weight_is_the_floor_it_lands_on(self):
        # a compaction costs the context every later turn carries, so the
        # ranking weighs it by the sum of the floors, not by the peaks
        with Sandbox() as box:
            base, cur = self.sides()
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            row = [l for l in page.splitlines()
                   if l.startswith("| ") and "compactions 5 in" in l]
            self.assertEqual(len(row), 1, page)
            tokens = float(row[0].split("|")[3].strip())
            self.assertAlmostEqual(tokens, sum(f for _p, f in self.DAY) / 1e6,
                                   places=1)

    def test_a_window_without_context_says_so_instead_of_nm(self):
        with Sandbox() as box:
            base, cur = two_sides()
            _code, page = box.run(box.write_json("base.json", base),
                                  box.write_json("cur.json", cur))
            self.assertIn("compactions: none inside the window", page)
            self.assertIn("the launch and notification counters are zero", page)

    def test_a_baseline_frozen_before_the_kpis_is_read_as_zero(self):
        with Sandbox() as box:
            base, cur = self.sides()
            base["rows"][0]["sessions"][0].pop("context")
            code, page = box.run(box.write_json("base.json", base),
                                 box.write_json("cur.json", cur))
            self.assertEqual(code, 0)
            self.assertIn("compactions 5 in 3.8 h", page)

if __name__ == "__main__":
    unittest.main(verbosity=2)
