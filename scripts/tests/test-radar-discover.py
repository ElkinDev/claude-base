"""Offline tests for radar-discover.py. Fixtures under fixtures/radar/ are real responses recorded on
2026-09-30 (trimmed); the Reddit success payload is synthetic because Reddit answered 403 HTML that day.
No test touches the network: HTTP, GH_RUN and NPM_VERSION are replaced before each run.
Run: python test-radar-discover.py
"""
import contextlib, importlib.util, io, json, os, shutil, socket, tempfile, unittest
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
FX = HERE / "fixtures" / "radar"
spec = importlib.util.spec_from_file_location("rd", HERE.parent / "radar-discover.py")
rd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)


def fx(name):
    return (FX / name).read_text(encoding="utf-8")


def fake_http(url, headers):
    if "pricing.md" in url:
        return 200, "text/markdown; charset=utf-8", fx("pricing.md")
    if "/trending?since=" in url:
        return 200, "text/html; charset=utf-8", fx("trending-daily.html")
    if "hn.algolia.com" in url:
        return 200, "application/json", fx("hn-claude.json")
    if "reddit.com" in url:
        return 403, "text/html", fx("reddit-403.html")
    if "huggingface.co" in url:
        return 200, "application/json", fx("hf-trending.json")
    if "CHANGELOG" in url:
        return 200, "text/plain", fx("changelog.md")
    raise AssertionError("unexpected url " + url)


def fake_gh(args):
    path = args[2]
    if path == "search/repositories":
        return 0, fx("search-created.json"), ""
    if path == "repos/tamaratran/fast-jev-compaction/readme":
        return 0, fx("readme-jev.json"), ""
    if path.startswith("repos/") and path.count("/") == 2:
        return 0, json.dumps({"created_at": "2026-09-01T00:00:00Z", "pushed_at": "2026-09-29T00:00:00Z",
                              "stargazers_count": 12105, "topics": ["agents"], "description": "x"}), ""
    return 1, '{"message":"Not Found"}', "gh: Not Found (HTTP 404)"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rdd test "))
        rd.HTTP, rd.GH_RUN, rd.NPM_VERSION = fake_http, fake_gh, lambda: "2.1.285"
        self.prices = self.tmp / "radar-prices.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, *extra, now="2026-09-30T15:00"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = rd.main(["--state", str(self.tmp / "state"), "--prices", str(self.prices),
                            "--profile", str(HERE.parent / "radar-profile.txt"), "--now", now] + list(extra))
        return code, buf.getvalue()

    def test_the_price_table_defaults_to_the_state_folder_and_the_seed_is_never_written(self):
        seed = Path(rd.__file__).resolve().parent / "radar-prices.json"
        before = seed.read_bytes() if seed.exists() else None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rd.main(["--state", str(self.tmp / "s2"), "--profile", str(HERE.parent / "radar-profile.txt"),
                     "--now", "2026-09-30T15:00", "--out", str(self.tmp / "p2.md")])
        self.assertTrue((self.tmp / "s2" / "radar-prices.json").exists(), buf.getvalue())
        self.assertEqual(before, seed.read_bytes() if seed.exists() else None)

    def ctx(self, **kw):
        return rd.Ctx(dt.datetime(2026, 9, 30, 15, tzinfo=dt.timezone.utc), kw.get("max_calls", 400),
                      kw.get("budget_s", 60))


class Parsers(Base):
    def test_pricing_rows_and_ids(self):
        rows = rd.parse_pricing(fx("pricing.md"))
        self.assertGreaterEqual(len(rows), 19)
        self.assertEqual({k: rows["claude-opus-5-5"][k] for k in rd.PRICE_KEYS},
                         {"input": 4.0, "cache_write_5m": 5.0, "cache_write_1h": 8.0, "cache_read": 0.2, "output": 20.0})
        self.assertEqual(rows["claude-opus-4-1"]["name"], "Claude Opus 4.1")
        self.assertIn("claude-sonnet-5-5", rows)

    def test_pricing_html_gives_no_rows(self):
        self.assertEqual(rd.parse_pricing(fx("reddit-403.html")), {})

    def test_trending_rows(self):
        rows = rd.parse_trending(fx("trending-daily.html"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["name"], "NVIDIA/OpenShell")
        self.assertEqual((rows[0]["stars"], rows[0]["gain"]), (12105, 1280))
        self.assertTrue(rows[0]["desc"].startswith("OpenShell is the safe"))

    def test_changelog_versions(self):
        e = rd.parse_changelog(fx("changelog.md"))
        self.assertEqual([v for v, _ in e], ["2.1.285", "2.1.284", "2.1.283"])
        self.assertGreater(len(e[0][1]), 10)
        self.assertGreater(rd.vtuple("2.1.285"), rd.vtuple("2.1.99"))


class Rules(Base):
    now = dt.datetime(2026, 9, 30, 12, tzinfo=dt.timezone.utc)

    def test_npm_ahead_of_the_changelog_is_listed_when_its_heading_appears(self):
        rd.NPM_VERSION = lambda: "2.1.300"
        code, _ = self.run_main("--out", str(self.tmp / "a.md"))
        self.assertEqual(code, 0)
        self.assertIn("npm latest 2.1.300, not yet in the CHANGELOG (top 2.1.285)", (self.tmp / "a.md").read_text(encoding="utf-8"))
        st = self.tmp / "state"
        self.assertEqual(json.loads((st / "claude-code-last.json").read_text(encoding="utf-8"))["version"], "2.1.285")
        newer = "## 2.1.300\n\n- the newer entry\n\n" + fx("changelog.md")
        base = rd.HTTP
        rd.HTTP = lambda u, h: (200, "text/plain", newer) if "CHANGELOG" in u else base(u, h)
        self.run_main("--out", str(self.tmp / "b.md"), now="2026-10-01T08:00")
        txt = (self.tmp / "b.md").read_text(encoding="utf-8")
        self.assertIn("Claude Code 2.1.300", txt)
        self.assertIn("the newer entry", txt)

    def test_a_missing_source_exits_one(self):
        base = rd.HTTP
        rd.HTTP = lambda u, h: (500, "text/plain", "down") if "hn.algolia.com" in u else base(u, h)
        code, _ = self.run_main("--out", str(self.tmp / "m.md"))
        self.assertEqual(code, 1)

    def test_old_snapshots_pruned_and_the_week_delta_reads_six_to_eight_days(self):
        st = self.tmp / "state"
        st.mkdir(parents=True, exist_ok=True)
        repo = "tamaratran/fast-jev-compaction"
        for day, stars in (("2026-07-01", 1), ("2026-09-01", 100)):
            (st / ("stars-%s.json" % day)).write_text(json.dumps({"repos": {repo: {"stars": stars}}}), encoding="utf-8")
        self.run_main("--out", str(self.tmp / "p.md"))
        self.assertFalse((st / "stars-2026-07-01.json").exists())
        self.assertTrue((st / "stars-2026-09-01.json").exists())
        row = [x for x in (self.tmp / "p.md").read_text(encoding="utf-8").splitlines() if "| %s |" % repo in x]
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0].split("|")[6].strip(), "n/a", row[0])  # a 29-day-old snapshot is no 7-day delta

    def test_velocity_snapshot_first(self):
        v, b = rd.velocity(1200, self.now, self.now - dt.timedelta(days=5), (1000, self.now - dt.timedelta(days=1)))
        self.assertEqual((round(v), b), (200, "snapshot"))

    def test_velocity_bootstrap_young(self):
        v, b = rd.velocity(1000, self.now, self.now - dt.timedelta(days=10))
        self.assertEqual((round(v), b), (100, "bootstrap"))
        v, b = rd.velocity(500, self.now, self.now - dt.timedelta(hours=2))  # age floor of one day
        self.assertEqual((round(v), b), (500, "bootstrap"))

    def test_velocity_old_trending_or_none(self):
        old = self.now - dt.timedelta(days=200)
        self.assertEqual(rd.velocity(9000, self.now, old, None, 700, 7), (100.0, "trending"))
        self.assertEqual(rd.velocity(9000, self.now, old), (0.0, "no baseline"))

    def test_velocity_gap_too_short_falls_through(self):
        v, b = rd.velocity(1000, self.now, self.now - dt.timedelta(days=10), (990, self.now - dt.timedelta(hours=1)))
        self.assertEqual(b, "bootstrap")

    def test_relevance_terms(self):
        prof = [(5, "claude-code"), (4, "claude"), (3, "llama.cpp"), (4, "compaction")]
        score, terms = rd.relevance("A Claude Code plugin; llama cpp too", prof)
        self.assertEqual((score, terms), (12, ["claude-code", "claude", "llama.cpp"]))
        self.assertEqual(rd.relevance("claudette and compactions", prof), (0, []))

    def test_profile_parse(self):
        p = self.tmp / "p.txt"
        p.write_text("# c\n3 claude\n\n0 zero\n9 big\nnoweight\n2 terminal multiplexer\n4 CLAUDE\n1 Terminal-Multiplexer\n",
                     encoding="utf-8")
        terms, bad = rd.load_profile(p)
        self.assertEqual(terms, [(3.0, "claude"), (2.0, "terminal multiplexer")])
        self.assertEqual(len(bad), 5)
        self.assertIn("repeated term CLAUDE", bad[3])


class Bounds(Base):
    def test_html_where_json_expected(self):
        c = self.ctx()
        rd.HTTP = lambda u, h: (200, "text/html", "<!DOCTYPE html><html></html>")
        self.assertIsNone(rd.fetch_text(c, "hf", "u"))
        self.assertEqual(c.missing, ["source missing: hf (HTML where json was expected)"])

    def test_timeout_is_one_line(self):
        def boom(u, h):
            raise socket.timeout("timed out")
        rd.HTTP, c = boom, self.ctx()
        self.assertIsNone(rd.fetch_text(c, "hn", "u"))
        self.assertEqual(c.missing, ["source missing: hn (TimeoutError: timed out)"])

    def test_call_cap_fires(self):
        c = self.ctx(max_calls=1)
        self.assertIsNotNone(rd.gh(c, "a", "search/repositories"))
        self.assertIsNone(rd.gh(c, "b", "search/repositories"))
        self.assertEqual(c.gh_calls, 1)
        self.assertIn("call cap 1 reached", c.missing[-1])

    def test_budget_fires(self):
        c = self.ctx(budget_s=0.000001)
        c.deadline -= 1
        self.assertIsNone(rd.fetch_text(c, "p", "u"))
        self.assertIn("time budget reached", c.missing[0])

    def test_hf_fallback_reports_param(self):
        seen = []

        def http(u, h):
            seen.append(u)
            return (400, "application/json", "{}") if "trendingScore" in u else fake_http(u, h)
        rd.HTTP, c = http, self.ctx()
        sort, models = rd.hf_models(c)
        self.assertEqual((sort, len(models)), ("trending_score", 5))

    def test_reddit_success_synthetic(self):
        body = json.dumps({"data": {"children": [{"data": {"id": "a", "score": 900, "title": "New Claude Code subagent trick",
                                                            "permalink": "/r/ClaudeAI/x", "url": ""}},
                                                  {"data": {"id": "b", "score": 5000, "title": "cat picture", "permalink": "/y"}}]}})
        rd.HTTP = lambda u, h: (200, "application/json", body) if "r/ClaudeAI/" in u else (200, "application/json", '{"hits": []}')
        saved, rd.SUBREDDITS = rd.SUBREDDITS, ("ClaudeAI",)  # off by default; the path stays
        try:
            top = rd.social(self.ctx(), [(4, "claude")], 48)
        finally:
            rd.SUBREDDITS = saved
        self.assertEqual([(t[0], t[1]) for t in top], [("r/ClaudeAI", 900)])
        self.assertEqual(rd.SUBREDDITS, ())


class Runs(Base):
    def test_full_offline_run(self):
        out = self.tmp / "out dir" / "d.md"
        code, log = self.run_main("--out", str(out))
        self.assertEqual(code, 0, log)
        txt = out.read_text(encoding="utf-8")
        for s in ("## Discover", "Pricing baseline recorded", "Claude Code 2.1.285", "sort=trendingScore worked",
                  "tamaratran/fast-jev-compaction", "HN, "):
            self.assertIn(s, txt)
        self.assertNotIn("reddit", txt.lower())
        models = {m["id"]: m for m in json.loads(self.prices.read_text(encoding="utf-8"))["models"]}
        self.assertEqual(sorted(models["claude-haiku-4-5"]), sorted(("id", "name") + rd.PRICE_KEYS))
        self.assertTrue(all(isinstance(m[k], float) for m in models.values() for k in rd.PRICE_KEYS))
        self.assertEqual(list(models)[:2], ["claude-fable-5-1", "claude-mythos-5-1"])
        self.assertTrue((self.tmp / "state" / "stars-2026-09-30.json").exists())

    def test_second_run_diffs_prices_and_versions(self):
        self.run_main("--out", str(self.tmp / "a.md"))
        st = self.tmp / "state"
        p = json.loads((st / "prices-last.json").read_text(encoding="utf-8"))
        del p["claude-sonnet-5-5"]
        p["claude-opus-5"]["output"] = 30.0
        (st / "prices-last.json").write_text(json.dumps(p), encoding="utf-8")
        (st / "claude-code-last.json").write_text('{"version": "2.1.283"}', encoding="utf-8")
        snap = json.loads((st / "stars-2026-09-30.json").read_text(encoding="utf-8"))
        snap["repos"]["tamaratran/fast-jev-compaction"]["stars"] -= 1000
        (st / "stars-2026-09-30.json").write_text(json.dumps(snap), encoding="utf-8")
        code, _ = self.run_main("--out", str(self.tmp / "b.md"), now="2026-10-01T08:00")
        txt = (self.tmp / "b.md").read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertIn("NEW model price row: claude-sonnet-5-5", txt)
        self.assertIn("Price change: claude-opus-5: output 30 to 25", txt)
        self.assertIn("Claude Code 2.1.284", txt)
        self.assertNotIn("Claude Code 2.1.283", txt)
        self.assertIn("| snapshot |", txt)

    def test_section_replaced_once_and_crlf_kept(self):
        f = self.tmp / "radar.md"
        f.write_bytes(b"# Radar\r\n\r\n## Flags\r\nrow\r\n\r\n## Discover (old)\r\nold\r\n\r\n## Tail\r\nkeep\r\n")
        rd.write_section(f, "## Discover (new)\n\nbody\n", alone=False)
        rd.write_section(f, "## Discover (new)\n\nbody\n", alone=False)
        b = f.read_bytes()
        self.assertEqual(b.count(b"## Discover"), 1)
        self.assertNotIn(b"old", b)
        self.assertIn(b"## Tail\r\nkeep", b)
        self.assertEqual(b.count(b"\n"), b.count(b"\r\n"))

    def test_corrupt_state_is_a_line(self):
        st = self.tmp / "state"
        st.mkdir()
        (st / "stars-2026-09-29.json").write_text("{not json", encoding="utf-8")
        (st / "prices-last.json").write_text("", encoding="utf-8")
        code, log = self.run_main("--out", str(self.tmp / "c.md"))
        self.assertEqual(code, 1, log)  # written, with a reading missing
        self.assertIn("source missing: state stars-2026-09-29.json", log)

    def test_snapshot_write_refused_keeps_ranking(self):
        """S16 H2 found it: a snapshot held open by a reader made the ranking stage fail whole."""
        real = rd.write_json

        def refuse(path, data):
            if Path(path).name.startswith("stars-"):
                raise PermissionError(5, "Access is denied")
            return real(path, data)
        rd.write_json = refuse
        try:
            code, log = self.run_main("--out", str(self.tmp / "s.md"))
        finally:
            rd.write_json = real
        self.assertEqual(code, 1)  # written, with a reading missing
        self.assertIn("source missing: state stars-2026-09-30.json (not written: PermissionError)", log)
        self.assertIn("tamaratran/fast-jev-compaction", (self.tmp / "s.md").read_text(encoding="utf-8"))

    def test_bad_arguments_exit_2(self):
        for extra in (["--now", "yesterday"], ["--max-calls", "0"], ["--out", "  "], ["--now", " "]):
            self.assertEqual(self.run_main(*extra[:2])[0] if extra[0] != "--now" else
                             rd.main(["--now", extra[1], "--state", str(self.tmp)]), 2, extra)
        empty = self.tmp / "empty.txt"
        empty.write_text("# only comments\n", encoding="utf-8")
        self.assertEqual(self.run_main("--profile", str(empty))[0], 2)


class Defaults(Base):
    def test_state_and_output_default_under_the_evidence_root(self):
        ev = self.tmp / "ev root"
        saved = os.environ.get("EVIDENCE_ROOT")
        os.environ["EVIDENCE_ROOT"] = str(ev)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                code = rd.main(["--prices", str(self.prices), "--profile", str(HERE.parent / "radar-profile.txt"),
                                "--now", "2026-09-30T15:00"])
        finally:
            if saved is None:
                os.environ.pop("EVIDENCE_ROOT", None)
            else:
                os.environ["EVIDENCE_ROOT"] = saved
        self.assertIn(code, (0, 1), buf.getvalue())
        self.assertTrue((ev / "ledger" / "radar-2026-09-30.md").is_file(), buf.getvalue())
        self.assertTrue((ev / "ledger" / "radar" / "last-run.json").is_file())

    def test_the_default_user_agent_names_the_script_only(self):
        self.assertTrue(rd.UA.startswith("radar-discover/"), rd.UA)
        self.assertNotIn("github.com/", rd.UA)


if __name__ == "__main__":
    unittest.main(verbosity=1)
