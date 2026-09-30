"""Tests for radar-flags.py: every detector against stub readers in a temp folder, the age, the rank and the cap.

    python test-radar-flags.py

The fixtures hold a dear pin (the reviewer on claude-opus-5, the 2026-09-30 case), a stub probe output per profile, a
stub drift row and table, and a stub pulse block. cca-pace.py is the one beside the script under test, copied,
fed through --probe-output. Every setting is passed by environment, scrubbed first; nothing real is read.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("RADAR_FLAGS_PY") or os.path.join(os.path.dirname(HERE), "radar-flags.py")
PRICES = os.path.join(os.path.dirname(SCRIPT), "radar-prices.json")
CCA = os.path.join(os.path.dirname(SCRIPT), "cca-pace.py")
SCRUB = ("EVIDENCE_ROOT", "PULSE_REGISTER", "PULSE_ROOT", "CLAUDE_CONFIG_DIR")
BASE_ENV = {k: v for k, v in os.environ.items() if not k.startswith("RADAR_") and k not in SCRUB}
NOW = dt.datetime(2026, 9, 30, 13, 0).astimezone()
NOW_ARG = "2026-09-30T13:00"
pass  # temp folders live under the system temp dir

SEED = [  # the five rows of the 2026-09-30 seed (platform.claude.com pricing page), USD per MTok
    {"name": "Claude Fable 5.1", "id": "claude-fable-5-1", "input": 10, "cache_write_5m": 12.5, "cache_write_1h": 20,
     "cache_read": 0.25, "output": 50},
    {"name": "Claude Opus 5.5", "id": "claude-opus-5-5", "input": 4, "cache_write_5m": 5, "cache_write_1h": 8,
     "cache_read": 0.2, "output": 20},
    {"name": "Claude Opus 5", "id": "claude-opus-5", "input": 5, "cache_write_5m": 6.25, "cache_write_1h": 10,
     "cache_read": 0.5, "output": 25},
    {"name": "Claude Sonnet 5.5", "id": "claude-sonnet-5-5", "input": 2, "cache_write_5m": 2.5, "cache_write_1h": 4,
     "cache_read": 0.2, "output": 10},
    {"name": "Claude Haiku 4.5", "id": "claude-haiku-4-5", "input": 1, "cache_write_5m": 1.25, "cache_write_1h": 2,
     "cache_read": 0.1, "output": 5}]
PROBE = r'''import json, os, sys
d = os.environ.get("CLAUDE_CONFIG_DIR", "")
name = os.path.basename(d.rstrip("/\\")) if d else "default"
fx = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe.json"), encoding="utf-8"))
r = fx.get(name, {"exit": 1, "text": "cannot read credentials: none"})
print(r["text"])
sys.exit(r["exit"])
'''
CAT = r'''import os, sys
here = os.path.dirname(os.path.abspath(__file__))
name = "%s"
if "--row" in sys.argv:
    name = name.replace("table", "row")
code = int(open(os.path.join(here, "%s-exit.txt")).read()) if os.path.exists(os.path.join(here, "%s-exit.txt")) else 0
print(open(os.path.join(here, name), encoding="utf-8").read(), end="")
sys.exit(code)
'''
NPM = r'''import os, sys
v = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "npm.txt")).read().strip()
if v == "offline":
    print("npm ERR! code ENOTFOUND"); sys.exit(1)
print(v)
'''
DRIFT_ROW = ("kit-twin-drift 2026-09-30 13:00: kit last commit 2026-09-26 09:32, twins trailing 11 (owed, live change "
             "older than 24 h: %d), live tools with no twin since 2026-09-18 13:28: 2, carried by verdict: 13\n")
DRIFT_TABLE = ("kit last commit 2026-09-26 09:32; 11 twins trail their live file, 11 owed; 13 carried by verdict\n"
               "| twin | live | twin commit | live change | owed | lines differ |\n|---|---|---|---|---|---|\n\n"
               "Live tools changed after 2026-09-18 13:28 with no twin (2):\n"
               "- C:/x/old tool.py (2026-09-25 10:00)\n- C:/x/new.py (2026-09-30 12:00)\n")
PULSE_OUT = ("pulse 2026-09-30 13:00: mechanisms 4 (alive 0, silent 1, idle 3, dark 0, nomatch 0, due 1), window 24 h\n"
             "SILENT kit-twin-drift: trigger 2, use 0 [pulse:kit-twin-drift]\n"
             "DUE maestro-route since 2026-09-30: run `find x`, keep if flows 1 [pulse:maestro-route]\n"
             "STANDING 5 runs (5 of the last 14): reports | owner-reports lint 3 [pulse:w-12345678]\n")
REGISTER = ("# register\nid | principle | ruling | trigger | use | number command | keep line | review date\n"
            "kit-twin-drift | automation | r | a | b | python x.py --row | owed 0 | 2026-10-05\n"
            "maestro-route | quality, tokens | r | a | b | find x | flows 1 | 2026-09-30\n"
            "s16-hostile-input | quality | r | a | b | `python a.py | wc -l` | 0.54 or less | 2026-09-27\n"
            "future-one | optimization | r | a | b | cmd | keep | 2026-10-10\n")
DEC_30 = ("# Owner decisions, 2026-09-30\n\n"
          "- 07:4x (analyst) AUTO class, si no respondes antes de 11:00 corre la recomendada (1): open one.\n"
          "- DECIDED 08:3x by the owner. 07:4x AUTO class, si no respondes antes de 11:00 corre: closed one.\n"
          "- 12:0x OWNER class, espera, es tuya, antes de 09:00: not auto.\n"
          "- 12:3x AUTO class, si no respondes antes de 21:15 corre la recomendada: future.\n"
          "  - child AUTO class, si no respondes antes de 01:00: not a top-level row.\n")
DEC_28 = ("- 08:0x Pulse escalation, 4 keys. Auto class each, si no respondes antes de 21:15 corre la recomendada.\n"
          "- 18:0x Pulse escalation, RULED 19:0x. Auto class each, si no respondes antes de 21:15: ruled one.\n"
          "- 18:1x AUTO class, si no respondes antes de 20:00, DONE 20:1x: done one.\n")
DEC_29 = ("- 21:3x (analyst) AUTO class, the ruling of 08:0x, si no respondes antes de 10:00 corre la recomendada: "
          "next morning.\n"
          "- 08:0x (analyst) AUTO class, the 14:00 gate slipped, si no respondes antes de 10:00 corre: citing row.\n")
DEC_26 = "- 09:0x AUTO class, si no respondes antes de 10:00 corre: too old to read.\n"


def probe_text(account, week, hours=10.0):
    reset = (NOW + dt.timedelta(days=3.5)).isoformat()
    return ("account: %s\nfive_hour: %.1f%% used, resets 2026-09-30T18:00:00+00:00\nseven_day: %.1f%% used, resets %s\n"
            "limit weekly_all: %g%% used\n" % (account, hours, week, reset, week))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as h:
        h.write(text)


def agent(root, name, model):
    write(os.path.join(root, name + ".md"), "---\nname: %s\nmodel: %s\neffort: high\n---\nbody\n" % (name, model))


def world(tmp, owed=11, drift_exit=0, dear=0, extra=False):
    s = os.path.join(tmp, "scripts")
    write(os.path.join(s, "usage-probe.py"), PROBE)
    write(os.path.join(s, "kit-twin-drift.py"), CAT % ("drift-table.txt", "drift", "drift"))
    write(os.path.join(s, "pulse.py"), CAT % ("pulse-out.txt", "pulse", "pulse"))
    write(os.path.join(s, "claude.py"), 'print("2.1.285 (Claude Code)")\n')
    write(os.path.join(s, "npm.py"), NPM)
    write(os.path.join(s, "npm.txt"), "2.1.290\n")
    shutil.copy(CCA, os.path.join(s, "cca-pace.py"))
    write(os.path.join(s, "drift-row.txt"), DRIFT_ROW % owed)
    write(os.path.join(s, "drift-table.txt"), DRIFT_TABLE)
    write(os.path.join(s, "drift-exit.txt"), str(drift_exit))
    write(os.path.join(s, "pulse-out.txt"), PULSE_OUT)
    write(os.path.join(s, "probe.json"), json.dumps({
        "acctlow": {"exit": 0, "text": probe_text("acctlow", 20)},
        "accthigh": {"exit": 0, "text": probe_text("accthigh", 45)},
        "acctwall": {"exit": 0, "text": probe_text("acctwall", 60)},
        "acctwall2": {"exit": 0, "text": probe_text("acctwall2", 70)},  # listed only when extra
        "acct401": {"exit": 1, "text": "HTTP 401: the stored access token of this profile has expired"},
        "acctpace": {"exit": 0, "text": probe_text("acctpace", 20)},
        "default": {"exit": 0, "text": probe_text("default", 50)}}))
    a = os.path.join(tmp, "agents")
    for name, model in (("reviewer", "claude-opus-5"), ("analyst", "claude-opus-5[1m]"),
                        ("implementer", "claude-opus-5-5"), ("bulk-reader", "haiku"), ("fable", "claude-fable-5-1"),
                        ("old", "claude-opus-4-1"), ("sonnet", "claude-sonnet-5-5")) + (
                       (("quoted", '"claude-opus-5"'),) if extra else ()):
        agent(a, name, model)
    for i in range(dear):
        agent(a, "dear%02d" % i, "claude-opus-5")
    write(os.path.join(a, "odd.md"), "no front matter\nmodel: claude-opus-5\n")
    write(os.path.join(a, "reviewer.md.20260930-1255.bak"), "---\nmodel: claude-opus-5\n---\n")
    write(os.path.join(tmp, "pulse.md"), REGISTER)
    d = os.path.join(tmp, "drafts")
    write(os.path.join(d, "owner-decisions-2026-09-30.md"), DEC_30)
    write(os.path.join(d, "owner-decisions-2026-09-28.md"), DEC_28)
    if extra:
        write(os.path.join(d, "owner-decisions-2026-09-29.md"), DEC_29)
    write(os.path.join(d, "owner-decisions-2026-09-26.md"), DEC_26)
    p = os.path.join(tmp, "p")
    write(os.path.join(tmp, "prices.json"), json.dumps({"models": SEED}))
    return dict(BASE_ENV, RADAR_AGENTS=a, RADAR_PRICES=os.path.join(tmp, "prices.json"), RADAR_SCRIPTS=s, RADAR_DECISIONS=d,
                RADAR_PULSE_REGISTER=os.path.join(tmp, "pulse.md"), RADAR_CLAUDE=os.path.join(s, "claude.py"),
                RADAR_NPM=os.path.join(s, "npm.py"), RADAR_PACE_PROFILE="acctpace",
                RADAR_PROFILES=";".join("%s=%s/%s" % (n, p, n) for n in ("acctlow", "accthigh", "acctwall", "acct401", "acctpace") + (("acctwall2",) if extra else ()))
                + ";default=")


def radar(env, out, *extra, now=NOW_ARG):
    args = [sys.executable, SCRIPT, "--out", out] + (["--now", now] if now else []) + list(extra)
    done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", env=env, timeout=110)
    text = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
    return done.returncode, done.stdout + done.stderr, text


def rows(text):
    return [ln for ln in text.splitlines() if re.match(r"^\| \d+ \|", ln)]


class Radar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pass  # temp folders live under the system temp dir
        cls.tmp = tempfile.mkdtemp(prefix="radar-flags-")
        cls.env = world(cls.tmp)
        cls.code, cls.out, cls.text = radar(cls.env, os.path.join(cls.tmp, "out", "radar.md"))
        cls.rows = rows(cls.text)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def has(self, needle):
        return [r for r in self.rows if needle in r]

    def test_dear_pin_flagged_with_tokens_principle(self):
        r = self.has("agent reviewer pins claude-opus-5 (effort high)")
        self.assertEqual(len(r), 1, self.text)
        self.assertIn("| tokens |", r[0])
        self.assertIn("Repin reviewer to claude-opus-5-5", r[0])
        self.assertEqual(len(self.has("agent analyst pins claude-opus-5[1m]")), 1)

    def test_current_alias_body_and_backup_pins_not_flagged(self):
        for name in ("implementer", "bulk-reader", "fable", "sonnet", "odd", "old"):
            self.assertEqual(self.has("agent %s pins" % name), [], name)
        self.assertIn("old.md pins claude-opus-4-1, which radar-prices.json does not hold", self.text)
        self.assertIn("model fit: 8 definitions read, 6 pinned to an id, 2 flagged", self.text)

    def test_staged_price_table_holds_the_seed_rows_and_opus_55_is_cheaper(self):
        # the discover lane refreshes the staged table with more rows, so this is a subset check
        m = {r["name"]: r for r in json.load(open(PRICES, encoding="utf-8"))["models"]}
        for r in SEED:
            self.assertEqual({b: float(m[r["name"]][b]) for b in r if b not in ("name",) and b != "id"},
                             {b: float(r[b]) for b in r if b not in ("name",) and b != "id"}, r["name"])
            self.assertEqual(m[r["name"]]["id"], r["id"])
        for b in ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output"):
            self.assertLess(m["Claude Opus 5.5"][b], m["Claude Opus 5"][b], b)

    def test_capacity_projection_flags_low_and_401_is_a_reading(self):
        self.assertEqual(len(self.has("profile acctlow is on course to end its week at 40 pct")), 1, self.text)
        self.assertEqual(self.has("profile accthigh"), [])
        self.assertEqual(self.has("acct401"), [])
        self.assertIn("capacity acct401: reading missing (401, the stored token expired)", self.out)
        self.assertEqual(self.code, 1)

    def test_capacity_over_100_is_a_wall_risk(self):
        self.assertEqual(len(self.has("profile acctwall is on course to reach its weekly wall before the reset")), 1,
                         self.text)
        self.assertEqual(self.has("profile acctlow is on course to reach"), [])

    def test_pace_behind_flagged_from_the_pace_reader(self):
        r = self.has("profile acctpace is behind its pace")
        self.assertEqual(len(r), 1, self.text)
        self.assertIn("-> behind", r[0])

    def test_owed_and_old_no_twin(self):
        self.assertEqual(len(self.has("11 kit twins owed")), 1)
        self.assertEqual(len(self.has("1 live tools changed since 2026-09-18 13:28 have no twin")), 1, self.text)

    def test_pulse_lines_and_verdicts_due(self):
        r = self.has("SILENT kit-twin-drift")
        self.assertEqual(len(r), 1)
        self.assertIn("| automation |", r[0])
        self.assertEqual(len(self.has("STANDING 5 runs")), 1)
        self.assertEqual(len(self.has("verdict due on maestro-route since 2026-09-30; keep line: flows 1")), 1)
        r = self.has("verdict due on s16-hostile-input since 2026-09-27")
        self.assertEqual(len(r), 1)
        self.assertIn("python a.py / wc -l", r[0])
        self.assertEqual(self.has("future-one"), [])

    def test_newer_release_flagged(self):
        self.assertEqual(len(self.has("Claude Code 2.1.285 is installed and 2.1.290 is released")), 1)

    def test_quoted_model_value_is_a_pin(self):
        _, _, text = radar(world(self.tmp, extra=True), os.path.join(self.tmp, "q", "radar.md"))
        self.assertIn("model fit: 9 definitions read, 7 pinned to an id, 3 flagged", text)

    def test_a_deadline_before_the_row_time_is_the_next_morning(self):
        env = world(self.tmp, extra=True)
        for day in ("30", "28"):  # only the 09-29 asks, so both of its rows sit inside the stale-waits floor
            os.remove(os.path.join(env["RADAR_DECISIONS"], "owner-decisions-2026-09-%s.md" % day))
        _, _, text = radar(env, os.path.join(self.tmp, "n", "radar.md"))
        r = [x for x in text.splitlines() if "drafts/owner-decisions-2026-09-29.md:1" in x]
        self.assertEqual(len(r), 1, text)
        self.assertIn("past by 3 h", r[0])
        r = [x for x in text.splitlines() if "drafts/owner-decisions-2026-09-29.md:2" in x]
        self.assertEqual(len(r), 1, text)
        self.assertIn("past by 27 h", r[0])  # a cited 14:00 never moves an 08:0x ask to the next morning

    def test_stale_waits(self):
        self.assertEqual(len(self.has("drafts/owner-decisions-2026-09-30.md:3")), 1, self.text)
        self.assertEqual(len(self.has("drafts/owner-decisions-2026-09-28.md:1")), 1)
        for gone in ("closed one", "ruled one", "done one", "not auto", "future", "too old", "not a top-level"):
            self.assertEqual(self.has(gone), [], gone)

    def test_table_shape_and_every_row_has_a_proof(self):
        self.assertIn("| rank | principle | finding | number (source) | proposal | age |", self.text)
        for r in self.rows:
            self.assertEqual(r.count(" | "), 5, r)
            self.assertIn("proof:", r)


class Edges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="radar-flags-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_missing_input_prints_one_line_and_the_file_is_written(self):
        gone = os.path.join(self.tmp, "gone")
        env = dict(BASE_ENV, RADAR_AGENTS=gone, RADAR_PRICES=os.path.join(gone, "p.json"), RADAR_SCRIPTS=gone,
                   RADAR_PULSE_REGISTER=os.path.join(gone, "pulse.md"), RADAR_DECISIONS=gone,
                   RADAR_CLAUDE=os.path.join(gone, "claude.exe"), RADAR_NPM=os.path.join(gone, "npm.exe"))
        code, out, text = radar(env, os.path.join(self.tmp, "out", "r.md"))
        self.assertEqual(code, 1, out)
        for det in ("model fit: input missing", "capacity: input missing", "owed twins: input missing",
                    "pulse: input missing", "versions: reading missing", "stale waits: input missing"):
            self.assertIn(det, out)
        self.assertEqual(rows(text), [])

    def test_age_rank_repeat_drop_and_carry(self):
        env = world(self.tmp)
        out = os.path.join(self.tmp, "out", "r.md")
        state = os.path.join(self.tmp, "out", "radar-state.json")
        radar(env, out, now="2026-09-30T08:00")
        radar(env, out, now="2026-09-30T08:00")
        self.assertEqual(json.load(open(state))["keys"]["twins:owed"]["runs"], 1)
        code, _, text = radar(env, out, now="2026-09-30T18:00")
        keys = json.load(open(state))["keys"]
        self.assertEqual(keys["twins:owed"]["runs"], 2)
        self.assertTrue(keys["twins:owed"]["first"].startswith("2026-09-30T08:00"))
        first = rows(text)[0]
        self.assertTrue("| quality |" in first or "| tokens |" in first, first)
        self.assertTrue(first.endswith("| 2 |"), first)
        write(os.path.join(env["RADAR_SCRIPTS"], "drift-exit.txt"), "1")
        radar(env, out, now="2026-10-01T08:00")
        self.assertEqual(json.load(open(state))["keys"]["twins:owed"]["runs"], 2)
        write(os.path.join(env["RADAR_SCRIPTS"], "drift-exit.txt"), "0")
        write(os.path.join(env["RADAR_SCRIPTS"], "drift-row.txt"), DRIFT_ROW % 0)
        radar(env, out, now="2026-10-01T18:00")
        self.assertNotIn("twins:owed", json.load(open(state))["keys"])

    def test_rerun_keeps_the_discover_section(self):
        env = world(self.tmp)
        out = os.path.join(self.tmp, "d", "radar.md")
        radar(env, out)
        with open(out, "a", encoding="utf-8") as h:
            h.write("\n## Discover\n\n| rank | repo |\n|---|---|\n| 1 | a/b |\n")
        radar(env, out, now="2026-09-30T14:00")
        text = open(out, encoding="utf-8").read()
        self.assertEqual(text.count("## Discover"), 1)
        self.assertTrue(text.rstrip().endswith("| 1 | a/b |"))
        self.assertIn("# Radar 2026-09-30 14:00", text)

    def test_cap_at_15(self):
        env = world(self.tmp, dear=20, extra=True)
        code, _, text = radar(env, os.path.join(self.tmp, "r.md"))
        self.assertEqual(len(rows(text)), 15)
        self.assertRegex(text, r"Beyond the cap \(\d+\): ")
        capacity = [r for r in rows(text) if "is on course" in r or "is behind its pace" in r]
        self.assertEqual(len(capacity), 2, text)  # the per-detector floor keeps two capacity rows above 20 dearer pins
        self.assertLessEqual(sum("pins claude-opus-5" in r for r in rows(text)), 15 - 2)
        self.assertTrue([r for r in capacity if "is on course to reach its weekly wall" in r], text)
        self.assertTrue([r for r in capacity if "is on course to end its week" in r or "behind its pace" in r], text)

    def test_a_file_that_starts_with_the_discover_section_keeps_it(self):
        env = world(self.tmp)
        out = os.path.join(self.tmp, "d0", "radar.md")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as h:
            h.write("## Discover\n\n| 1 | a/b |\n")
        radar(env, out)
        text = open(out, encoding="utf-8").read()
        self.assertEqual(text.count("## Discover"), 1)
        self.assertTrue(text.rstrip().endswith("| 1 | a/b |"), text[-200:])


    def test_the_price_table_falls_back_from_the_state_folder_to_the_seed(self):
        ev = os.path.join(self.tmp, "ev")
        env = {k: v for k, v in world(self.tmp).items() if k != "RADAR_PRICES"}
        env["EVIDENCE_ROOT"] = ev
        out = os.path.join(self.tmp, "pf", "radar.md")
        _, _, text = radar(env, out)
        self.assertIn("model fit:", text)
        self.assertNotIn("input missing: price table", text)  # no state table yet: the seed beside the script
        os.makedirs(os.path.join(ev, "ledger", "radar"))
        with open(os.path.join(ev, "ledger", "radar", "radar-prices.json"), "w", encoding="utf-8") as h:
            h.write("{not json")
        _, _, text = radar(env, out, now="2026-09-30T14:00")
        self.assertIn("input missing: price table " + os.path.join(ev, "ledger", "radar", "radar-prices.json"), text)

    def test_usage_errors_exit_2(self):
        env = world(self.tmp)
        out = os.path.join(self.tmp, "u.md")
        for extra, now in ((["--bogus"], NOW_ARG), ([], ""), ([], "   "), ([], "abc"), (["--out", "  "], NOW_ARG)):
            args = [sys.executable, SCRIPT, "--out", out] + (["--now", now] if now is not None else []) + extra
            done = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(done.returncode, 2, (extra, now, done.stderr))
        self.assertFalse(os.path.exists(out))


class Settings(unittest.TestCase):
    """The readers resolve beside the script, each one overridable, and the house-only detector is off by default."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="radar flags settings ")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_resolve_beside_the_script_and_the_kit_pulse_is_found(self):
        kit = os.path.join(self.tmp, "kit layout")
        scripts = os.path.join(kit, "scripts")
        os.makedirs(scripts)
        copy = os.path.join(scripts, "radar-flags.py")
        shutil.copy(SCRIPT, copy)
        shutil.copy(PRICES, os.path.join(scripts, "radar-prices.json"))
        home, ev = os.path.join(self.tmp, "home"), os.path.join(self.tmp, "ev")
        agent(os.path.join(home, "agents"), "reviewer", "claude-opus-5")
        env = dict(BASE_ENV, CLAUDE_CONFIG_DIR=home, EVIDENCE_ROOT=ev, PULSE_ROOT=home,
                   RADAR_CLAUDE=os.path.join(self.tmp, "none", "claude.exe"))
        run = lambda: subprocess.run([sys.executable, copy, "--now", NOW_ARG], capture_output=True, text=True,
                                     encoding="utf-8", env=env, timeout=110)
        done = run()
        out = (done.stdout + done.stderr).replace("\\", "/")
        radar_md = os.path.join(ev, "ledger", "radar-2026-09-30.md")
        self.assertEqual(done.returncode, 1, out)
        self.assertTrue(os.path.isfile(radar_md), out)
        text = open(radar_md, encoding="utf-8").read()
        s = scripts.replace("\\", "/")
        for line in ("capacity: input missing: %s/usage-probe.py" % s, "owed twins: input missing: %s/kit-twin-drift.py" % s,
                     "pulse: input missing: %s/pulse.py" % s):
            self.assertIn(line, out)
        self.assertIn("agent reviewer pins claude-opus-5 (effort high)", text)
        self.assertIn("stale waits: off (RADAR_DECISIONS is unset", text)
        self.assertNotIn("stale waits", out)
        tools = os.path.join(kit, "claude", "tools")
        write(os.path.join(tools, "pulse.py"), 'print("pulse 2026-09-30 13:00: mechanisms 0, window 24 h")\n')
        write(os.path.join(home, "pulse.md"), REGISTER)
        out2 = (lambda d: d.stdout + d.stderr)(run()).replace("\\", "/")
        self.assertNotIn("pulse: input missing", out2)
        self.assertIn("pulse 2026-09-30 13:00: mechanisms 0", open(radar_md, encoding="utf-8").read())

    def test_the_pace_check_is_off_by_default_and_an_unlisted_pace_profile_is_missing(self):
        env = world(self.tmp)
        env.pop("RADAR_PACE_PROFILE")
        _, _, text = radar(env, os.path.join(self.tmp, "a", "radar.md"))
        self.assertNotIn("is behind its pace", text)
        self.assertNotIn(" pace: ", text)
        env["RADAR_PACE_PROFILE"] = "nosuch"
        code, out, text = radar(env, os.path.join(self.tmp, "b", "radar.md"))
        self.assertEqual(code, 1)
        self.assertIn("capacity nosuch pace: input missing: RADAR_PACE_PROFILE names nosuch", out)
        self.assertNotIn("is behind its pace", text)

    def test_a_reader_override_wins_over_the_scripts_folder(self):
        env = world(self.tmp)
        other = os.path.join(self.tmp, "elsewhere")
        write(os.path.join(other, "drift.py"), CAT % ("table.txt", "drift", "drift"))
        write(os.path.join(other, "row.txt"), DRIFT_ROW % 4)
        write(os.path.join(other, "table.txt"), DRIFT_TABLE)
        env["RADAR_DRIFT_PY"] = os.path.join(other, "drift.py")
        _, _, text = radar(env, os.path.join(self.tmp, "c", "radar.md"))
        self.assertEqual(len([r for r in rows(text) if "4 kit twins owed" in r]), 1, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
