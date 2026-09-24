#!/usr/bin/env python3
"""Tests for claude/tools/evidence-retention.py.

Every test builds its own evidence root under tempfile.mkdtemp(); no test names the real root. Ages
are set with os.utime against a fixed now, so the cutoff is arithmetic, never the machine clock.
"""

import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "evidence-retention.py")
SCOPE = os.path.join(HERE, "fixtures", "evidence-retention-scope.json")  # the machine scope the cases were written on
NOW = dt.datetime(2026, 9, 2, 8, 0)  # in the past: --apply refuses a clock ahead of the machine


def load_script():
    spec = importlib.util.spec_from_file_location("evidence_retention", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RET = load_script()


def set_birthtime(path, stamp):
    """Set a file's creation time on Windows, where the script reads st_birthtime; elsewhere the
    script has no creation time to read, so there is nothing to set."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.CreateFileW(path, 0x100, 0x7, None, 3, 0x02000000, None)  # write attributes, open existing
    if handle in (None, 0, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ticks = int((stamp + 11644473600) * 10 ** 7)
        when = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
        if not k32.SetFileTime(handle, ctypes.byref(when), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        k32.CloseHandle(handle)


class EvidenceRetentionTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="evr root ")
        self.trash = self.root + "-trash"
        self.put("rulings.md", 1)

    def tearDown(self):
        for path in (self.root, self.trash):
            self.assertTrue(os.path.abspath(path).startswith(os.path.abspath(tempfile.gettempdir())))
            shutil.rmtree(path, ignore_errors=True)

    def put(self, rel, days, body="abcd"):
        """A file `days` old against NOW (modified and created), with a four-byte body unless told
        otherwise."""
        full = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as handle:
            handle.write(body)
        self.date(full, NOW - dt.timedelta(days=days))
        return full

    def date(self, full, when, created=None):
        stamp = when.timestamp()
        os.utime(full, (stamp, stamp))
        set_birthtime(full, (created or when).timestamp())

    def trashed(self, rel, day="2026-09-02"):
        return os.path.exists(os.path.join(self.trash, day, *rel.split("/")))

    def exists(self, rel):
        return os.path.exists(os.path.join(self.root, *rel.split("/")))

    def run_it(self, *extra, scope=SCOPE, now=NOW):
        # a fixture root holds a handful of files, so the share bound is lifted unless a test sets it
        share = [] if "--max-share" in extra else ["--max-share", "1"]
        args = ["--root", self.root, "--scope", scope, "--now", now.isoformat()] + share + list(extra)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = RET.main(args)
        return code, buffer.getvalue()

    # -- the two-month rule ---------------------------------------------------------------

    def test_a_round_whose_newest_file_is_past_the_cutoff_goes_whole(self):
        self.put("Findings/bench/old-round/a.png", 70)
        self.put("Findings/bench/old-round/sub/b.xml", 65)
        self.put("Findings/bench/new-round/a.png", 70)
        self.put("Findings/bench/new-round/b.png", 59)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("Findings/bench/old-round"), out)
        self.assertTrue(self.exists("Findings/bench/new-round/a.png"), "a round with a fresh file stays whole")
        self.assertTrue(self.exists("Findings/bench/new-round/b.png"))

    def test_a_record_file_goes_on_its_own_and_a_fresher_one_stays(self):
        self.put("lanes/old-2026-07-30.md", 64)
        self.put("lanes/new-2026-08-10.md", 53)
        self.put("reviews/old.md", 90)
        self.put("x/y/z.bak", 65)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("lanes/old-2026-07-30.md"))
        self.assertTrue(self.exists("lanes/new-2026-08-10.md"))
        self.assertFalse(self.exists("reviews/old.md"))
        self.assertFalse(self.exists("x/y/z.bak"))
        self.assertTrue(self.exists("lanes"), "a folder that still holds a file stays")
        self.assertFalse(self.exists("x/y"), "a folder the deletion emptied goes")
        self.assertTrue(self.exists("x"), "a record's top folder stays even when empty")

    def test_keep_and_everything_out_of_scope_survive_any_age(self):
        for rel in ("briefs/TEMPLATE-lane-brief.md", "scripts/tool.py", "ledger/ledger.md",
                    "Findings/02_specs/F81.md", "BACKLOG.md", "waitlist-20260819.csv", "mockups/m.png",
                    "drafts/block-causes-2026-09-22.md"):
            self.put(rel, 400)
        self.put("briefs/old-brief.md", 400)
        runtime_reads = ("briefs/agent-laws.md", "drafts/decisions-2026-08-28.md", "drafts/release-notes-vc84.md")
        backups = ("scripts/tool.py.20260701.bak", "ledger/ledger.md.20260701.bak",
                   "briefs/TEMPLATE-lane-brief.md.20260701.bak")
        for rel in runtime_reads + backups:
            self.put(rel, 400)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        for rel in ("briefs/TEMPLATE-lane-brief.md", "scripts/tool.py", "ledger/ledger.md",
                    "Findings/02_specs/F81.md", "BACKLOG.md", "waitlist-20260819.csv", "mockups/m.png",
                    "drafts/block-causes-2026-09-22.md", "rulings.md") + runtime_reads:
            self.assertTrue(self.exists(rel), rel + " was deleted")
        self.assertFalse(self.exists("briefs/old-brief.md"))
        for rel in backups:
            self.assertFalse(self.exists(rel), "option 1 deletes any .bak, a kept file's backup too: " + rel)

    def test_the_cutoff_is_two_calendar_months_by_the_day(self):
        self.put("lanes/one-minute-past.md", 0)
        cutoff = RET.months_back(NOW, 2)
        self.assertEqual(dt.datetime(2026, 7, 2, 8, 0), cutoff)
        just_old = self.put("lanes/just-old.md", 0)
        just_new = self.put("lanes/just-new.md", 0)
        self.date(just_old, cutoff - dt.timedelta(minutes=1))
        self.date(just_new, cutoff + dt.timedelta(minutes=1))
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("lanes/just-old.md"))
        self.assertTrue(self.exists("lanes/just-new.md"))

    def test_months_back_clamps_the_day_to_the_month(self):
        cases = {
            dt.datetime(2026, 10, 31): dt.datetime(2026, 8, 31),
            dt.datetime(2026, 4, 30): dt.datetime(2026, 2, 28),
            dt.datetime(2028, 4, 30): dt.datetime(2028, 2, 29),
            dt.datetime(2026, 1, 15): dt.datetime(2025, 11, 15),
            dt.datetime(2026, 12, 31): dt.datetime(2026, 10, 31),
        }
        for now, want in cases.items():
            self.assertEqual(want, RET.months_back(now, 2), now)

    # -- the APK rule -------------------------------------------------------------------------

    def release(self, number, days):
        self.put("builds/myapp-vc{}-abc-release.aab".format(number), days)

    def test_an_apk_more_than_ten_releases_behind_goes_and_ten_behind_stays(self):
        for number, days in ((79, 44), (80, 43), (81, 37), (90, 8)):
            self.release(number, days)
        self.put("builds/myapp-vc79-candidate.apk", 44)
        self.put("builds/myapp-vc80-candidate.apk", 43)
        self.put("Findings/04_bench/phone-vc78-t2-2026-08-15/t2-installed.apk", 20)  # young, but vc78
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("builds/myapp-vc79-candidate.apk"))
        self.assertTrue(self.exists("builds/myapp-vc80-candidate.apk"), "exactly ten behind stays")
        self.assertFalse(self.exists("Findings/04_bench/phone-vc78-t2-2026-08-15/t2-installed.apk"))
        self.assertTrue(self.exists("builds/myapp-vc79-abc-release.aab"), "under the letter a bundle is not an APK")

    def test_an_apk_without_a_release_in_its_path_takes_the_release_of_its_date(self):
        for number, days in ((78, 46), (79, 44), (80, 43), (90, 8)):
            self.release(number, days)
        self.put("builds/myapp-main-111-0817.apk", 45)  # before vc79's bundle: vc79, 11 behind
        self.put("builds/myapp-main-222-0819.apk", 43.5)  # before vc80's bundle: vc80, 10 behind
        self.put("builds/myapp-main-333-0930.apk", 2)  # after the newest bundle: the next release
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("builds/myapp-main-111-0817.apk"), out)
        self.assertTrue(self.exists("builds/myapp-main-222-0819.apk"), out)
        self.assertTrue(self.exists("builds/myapp-main-333-0930.apk"), out)

    def test_a_debug_versioncode_in_a_name_is_not_a_release(self):
        for number, days in ((79, 44), (80, 43), (90, 8)):
            self.release(number, days)
        self.put("artifacts/myapp-dev-debug-cb1591c50-vc1.apk", 13)  # debug vc1, dated after vc90: vc91
        self.put("bench/vc79/myapp-dev-debug-c8b6818ce-vc1.apk", 5)  # vc1 skipped, its folder says vc79
        self.put("builds/myapp-vc85-candidate.apk", 44)  # no vc85 bundle, still a release label
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertTrue(self.exists("artifacts/myapp-dev-debug-cb1591c50-vc1.apk"), out)
        self.assertFalse(self.exists("bench/vc79/myapp-dev-debug-c8b6818ce-vc1.apk"), out)
        self.assertTrue(self.exists("builds/myapp-vc85-candidate.apk"), out)
        self.assertEqual(51, RET.apk_release("artifacts/x-vc1.apk", 3, {39: 1, 50: 2}))

    def test_an_apk_takes_its_sha256_sidecar_with_it(self):
        for number, days in ((79, 44), (80, 43), (90, 8)):
            self.release(number, days)
        self.put("builds/myapp-vc79-candidate.apk", 44)
        self.put("builds/myapp-vc79-candidate.apk.sha256", 1)  # young, but it names a doomed APK
        self.put("builds/myapp-vc80-candidate.apk", 43)
        self.put("builds/myapp-vc80-candidate.apk.sha256", 1)
        self.put("builds/myapp-vc79-probe.apk.txt", 1)  # not a sha256 sidecar: out of the APK rule
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("builds/myapp-vc79-candidate.apk.sha256"), out)
        self.assertTrue(self.exists("builds/myapp-vc80-candidate.apk.sha256"), out)
        self.assertTrue(self.exists("builds/myapp-vc79-probe.apk.txt"), out)
        with open(os.path.join(self.root, "ledger", "deleted", "2026-09-02.txt"), encoding="utf-8") as handle:
            self.assertIn("myapp-vc79-candidate.apk.sha256	4	release apk vc79 of newest vc90 sidecar", handle.read())

    def test_no_bundle_means_no_apk_is_judged(self):
        self.put("builds/myapp-vc50-old.apk", 30)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertTrue(self.exists("builds/myapp-vc50-old.apk"))

    # -- dry run, manifest, folders -------------------------------------------------------------

    def test_the_dry_run_deletes_nothing_and_writes_no_manifest(self):
        self.put("lanes/old.md", 90)
        code, out = self.run_it()
        self.assertEqual(0, code, out)
        self.assertIn("WOULD-TRASH lanes/old.md 4 (record)", out)
        self.assertIn("RETENTION dry: files=1 bytes=4", out)
        self.assertTrue(self.exists("lanes/old.md"))
        self.assertFalse(self.exists("ledger/deleted"))

    def test_apply_writes_every_deleted_path_to_the_manifest(self):
        self.put("lanes/old.md", 90)
        self.put("Findings/bench/r1/a.png", 70)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        manifest = os.path.join(self.root, "ledger", "deleted", "2026-09-02.txt")
        with open(manifest, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(2, len(lines), lines)
        self.assertTrue(any("\tlanes/old.md\t4\trecord" in line for line in lines), lines)
        self.assertTrue(any("\tFindings/bench/r1/a.png\t4\tround Findings/bench/r1" in line for line in lines), lines)
        self.assertIn("RETENTION applied: deleted=2 bytes=8 errors=0 already-gone=0", out)
        self.assertTrue(self.exists("Findings/bench"), "the parent of an emptied round stays")

    # -- belts ------------------------------------------------------------------------------

    def test_the_share_bound_refuses_the_whole_run(self):
        for i in range(10):
            self.put("lanes/old-{}.md".format(i), 90)
        code, out = self.run_it("--apply", "--max-share", "0.5")
        self.assertEqual(5, code, out)
        self.assertIn("REFUSED 10 of 11 files", out)
        self.assertEqual(10, len(os.listdir(os.path.join(self.root, "lanes"))))

    def test_a_root_without_the_register_is_refused(self):
        os.remove(os.path.join(self.root, "rulings.md"))
        self.put("lanes/old.md", 90)
        code, out = self.run_it("--apply")
        self.assertEqual(4, code, out)
        self.assertTrue(self.exists("lanes/old.md"))

    def test_a_malformed_or_missing_scope_is_refused(self):
        self.put("lanes/old.md", 90)
        bad = os.path.join(self.root, "bad.json")
        for body in ("{not json", json.dumps({"round_folders": [], "record_files": ["lanes/*"], "keep": [""],
                                               "apk_rule": True}),
                     json.dumps({"round_folders": [], "record_files": ["lanes/*"], "keep": [], "apk_rule": "yes"})):
            with open(bad, "w", encoding="utf-8") as handle:
                handle.write(body)
            code, out = self.run_it("--apply", scope=bad)
            self.assertEqual(4, code, out)
        code, out = self.run_it("--apply", scope=os.path.join(self.root, "absent.json"))
        self.assertEqual(4, code, out)
        self.assertTrue(self.exists("lanes/old.md"))

    def test_a_clock_ahead_refuses_apply_and_allows_a_dry_run(self):
        self.put("lanes/old.md", 90)
        ahead = dt.datetime.now() + dt.timedelta(days=30)
        code, out = self.run_it("--apply", now=ahead)
        self.assertEqual(4, code, out)
        self.assertTrue(self.exists("lanes/old.md"))
        code, out = self.run_it(now=ahead)
        self.assertEqual(0, code, out)

    def test_a_link_inside_a_round_is_never_followed_or_deleted(self):
        outside = tempfile.mkdtemp(prefix="evr outside ")
        try:
            with open(os.path.join(outside, "keep.txt"), "w") as handle:
                handle.write("abcd")
            self.put("Findings/bench/old/a.png", 70)
            link = os.path.join(self.root, "Findings", "bench", "old", "linked")
            made = subprocess.run(["cmd", "/c", "mklink", "/J", link, outside], capture_output=True, timeout=60)
            if made.returncode != 0:
                self.skipTest("no junction on this machine")
            code, out = self.run_it("--apply")
            self.assertEqual(0, code, out)
            self.assertTrue(os.path.exists(os.path.join(outside, "keep.txt")), "the walk went through the link")
            self.assertFalse(self.exists("Findings/bench/old/a.png"))
        finally:
            link = os.path.join(self.root, "Findings", "bench", "old", "linked")
            if os.path.isdir(link):
                os.rmdir(link)  # removes the junction itself, never its target
            shutil.rmtree(outside, ignore_errors=True)

    def test_a_file_already_gone_counts_as_gone_not_as_an_error(self):
        self.put("lanes/old.md", 90)
        doomed, _ = RET.plan(self.root, RET.load_scope(SCOPE), RET.months_back(NOW, 2), 10)
        os.remove(os.path.join(self.root, "lanes", "old.md"))
        deleted, freed, errors, gone = RET.delete(self.root, doomed, NOW, RET.open_manifest(self.root, NOW))
        self.assertEqual((0, 0, 0, 1), (deleted, freed, errors, gone))

    def test_usage_errors_a_bad_clock_and_a_non_object_scope_are_refusals(self):
        self.put("lanes/old.md", 90)
        listed = os.path.join(self.root, "list.json")
        with open(listed, "w", encoding="utf-8") as handle:
            handle.write("[]")
        cases = (["--root", ""], ["--root", "   "], ["--scope", " "], ["--trash", " "], ["--months", "two"], ["--bogus"])
        here = os.getcwd()
        os.chdir(self.root)  # an empty --root would resolve to the working directory: make that the root
        try:
            for extra in cases:
                code, out = self.run_it("--apply", *extra)
                self.assertEqual(4, code, extra)
        finally:
            os.chdir(here)
        for bad in ("", "tomorrow", "2026-09-02T08:00+00:00"):
            args = ["--root", self.root, "--scope", SCOPE, "--max-share", "1", "--now", bad, "--apply"]
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(4, RET.main(args), bad)
            self.assertIn("REFUSED --now", buffer.getvalue())
        code, out = self.run_it("--apply", scope=listed)
        self.assertEqual(4, code, out)
        self.assertTrue(self.exists("lanes/old.md"))

    def test_a_held_lock_refuses_a_second_apply_and_a_throw_releases_it(self):
        self.put("lanes/old.md", 90)
        held = RET.take_lock(self.root)
        self.assertIsNotNone(held)
        try:
            code, out = self.run_it("--apply")
            self.assertEqual(4, code, out)
            self.assertIn("another run holds", out)
            self.assertTrue(self.exists("lanes/old.md"))
            code, out = self.run_it()
            self.assertEqual(0, code, "a dry run takes no lock: " + out)
        finally:
            held.close()
        real_plan = RET.plan

        def boom(*args):
            raise RuntimeError("thrown inside the lock")

        RET.plan = boom
        try:
            with self.assertRaises(RuntimeError):
                self.run_it("--apply")
        finally:
            RET.plan = real_plan
        again = RET.take_lock(self.root)
        self.assertIsNotNone(again, "a throw inside the run left the lock held")
        again.close()
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("lanes/old.md"))

    def test_a_manifest_that_cannot_open_deletes_nothing(self):
        self.put("lanes/old.md", 90)
        self.put("ledger/deleted", 1)  # a file where the manifest folder goes
        code, out = self.run_it("--apply")
        self.assertEqual(4, code, out)
        self.assertIn("nothing removed", out)
        self.assertTrue(self.exists("lanes/old.md"))

    def test_a_manifest_file_that_cannot_open_under_a_good_lock_deletes_nothing(self):
        self.put("lanes/old.md", 90)
        os.makedirs(os.path.join(self.root, "ledger", "deleted", "2026-09-02.txt"))  # a folder at the day's name
        code, out = self.run_it("--apply")
        self.assertEqual(4, code, out)
        self.assertIn("REFUSED the manifest under", out)
        self.assertTrue(self.exists("lanes/old.md"))

    def test_a_manifest_line_that_fails_is_an_error_and_the_rest_still_goes(self):
        self.put("lanes/a.md", 90)
        self.put("lanes/b.md", 90)

        class Refusing(io.StringIO):
            def write(self, text):
                raise OSError("disk full")

        doomed, _ = RET.plan(self.root, RET.load_scope(SCOPE), RET.months_back(NOW, 2), 10)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            deleted, freed, errors, gone = RET.delete(self.root, doomed, NOW, Refusing())
        self.assertEqual((2, 2), (deleted, errors), buffer.getvalue())
        self.assertIn("ERROR manifest line for lanes/a.md 4 (record) -> deleted: disk full", buffer.getvalue())
        self.assertFalse(self.exists("lanes/b.md"))

    def test_a_file_held_open_is_an_error_and_the_rest_still_goes(self):
        held = self.put("lanes/held.md", 90)
        self.put("lanes/free.md", 90)
        with open(held, "rb"):
            code, out = self.run_it("--apply")
        if os.name != "nt":
            self.skipTest("only Windows refuses to delete an open file")
        self.assertEqual(2, code, out)
        self.assertIn("errors=1", out)
        self.assertFalse(self.exists("lanes/free.md"))
        self.assertTrue(self.exists("lanes/held.md"))

    # -- round 2: a file's age, nested record folders, floors, the trash ----------------------

    def scope_with(self, **changes):
        with open(SCOPE, encoding="utf-8") as handle:
            scope = json.load(handle)
        scope.update(changes)
        path = os.path.join(self.root, "scope-variant.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(scope, handle)
        return path

    def test_name_stamp_reads_the_forms_backups_use(self):
        cases = {
            "pulse.md.20260924-1150.bak": dt.datetime(2026, 9, 24, 11, 50),
            "TEMPLATE-lane-brief.md.20260910-095901.bak": dt.datetime(2026, 9, 10, 9, 59, 1),
            "orchestrator.md.20260915.bak": dt.datetime(2026, 9, 15),
            "gate.py.20260910-r1-bak": dt.datetime(2026, 9, 10),
            "x.20260924-9999.bak": dt.datetime(2026, 9, 24),  # not a time: the day stands
        }
        for name, want in cases.items():
            self.assertEqual(want.timestamp(), RET.name_stamp(name), name)
        for name in ("x.20261399.bak", "cb1591c50-vc1.apk", "lane-2026-09-24.md", "a123202609241150.bak"):
            self.assertEqual(0, RET.name_stamp(name), name)

    def test_a_backup_counts_from_its_name_stamp_or_its_creation_not_its_copied_mtime(self):
        self.put("scripts/tests/test-ledger-day.py.20260830-0905.bak", 90)  # copied mtime, stamped 3 days ago
        renamed = self.put("ledger/defects.md.bak", 90)  # a rename keeps both old times ...
        self.date(renamed, NOW - dt.timedelta(days=90), created=NOW - dt.timedelta(days=1))  # ... a copy does not
        self.put("briefs/old.md.20260601.bak", 90)  # stamped before the cutoff: goes
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertTrue(self.exists("scripts/tests/test-ledger-day.py.20260830-0905.bak"), out)
        if os.name == "nt":
            self.assertTrue(self.exists("ledger/defects.md.bak"), "a backup created yesterday stays")
        self.assertFalse(self.exists("briefs/old.md.20260601.bak"), out)

    def test_a_nested_record_folder_goes_whole_or_not_at_all(self):
        self.put("lanes/gates/l1/a.log", 70)
        self.put("lanes/gates/l1/b.log", 65)
        self.put("lanes/gates/l2/co.md", 70)
        self.put("lanes/gates/l2/us.md", 50)  # one fresh file holds its folder whole
        self.put("lanes/x/fresh.md", 5)
        self.put("lanes/x/old.md.bak", 90)  # a backup stands alone, even beside a fresh file
        self.put("lanes/top-old.md", 90)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("lanes/gates/l1"), out)
        self.assertTrue(self.exists("lanes/gates/l2/co.md"), out)
        self.assertTrue(self.exists("lanes/gates/l2/us.md"), out)
        self.assertFalse(self.exists("lanes/x/old.md.bak"), out)
        self.assertTrue(self.exists("lanes/x/fresh.md"), out)
        self.assertFalse(self.exists("lanes/top-old.md"), out)

    def test_the_emptied_folder_cleanup_stops_at_each_floor(self):
        for number, days in ((79, 44), (90, 8)):
            self.release(number, days)
        self.put("Findings/bench/only-round/a.png", 70)  # a round's floor is its parent
        self.put("bench-old/x.png", 70)  # a top-level round's floor is the root
        self.put("reviews/deep/one.md", 90)  # a record's floor is its top folder
        self.put("artifacts/myapp-vc79-x.apk", 40)  # an APK's floor is its own folder
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("Findings/bench/only-round"), out)
        self.assertTrue(self.exists("Findings/bench"), "the round's parent stays even when emptied")
        self.assertFalse(self.exists("bench-old"), out)
        self.assertFalse(self.exists("reviews/deep"), out)
        self.assertTrue(self.exists("reviews"), "the record's top folder stays even when emptied")
        self.assertFalse(self.exists("artifacts/myapp-vc79-x.apk"), out)
        self.assertTrue(self.exists("artifacts"), "the APK's own folder stays")

    def test_removed_files_wait_in_the_trash_and_old_trash_days_are_purged(self):
        self.put("lanes/old.md", 90)
        self.put("Findings/bench/r1/a.png", 70)
        os.makedirs(os.path.join(self.trash, "2026-07-01", "lanes"))
        os.makedirs(os.path.join(self.trash, "2026-08-20"))
        os.makedirs(os.path.join(self.trash, "notes"))
        open(os.path.join(self.trash, RET.TRASH_MARK), "w").close()
        open(os.path.join(self.trash, "2026-07-01", "lanes", "gone.md"), "w").close()
        code, out = self.run_it()
        self.assertIn("WOULD-PURGE", out)
        self.assertTrue(os.path.isdir(os.path.join(self.trash, "2026-07-01")), "a dry run purges nothing")
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertTrue(self.trashed("lanes/old.md"), out)
        self.assertTrue(self.trashed("Findings/bench/r1/a.png"), out)
        self.assertFalse(self.exists("lanes/old.md"))
        self.assertFalse(os.path.exists(os.path.join(self.trash, "2026-07-01")), "a day past 30 days is purged")
        self.assertTrue(os.path.isdir(os.path.join(self.trash, "2026-08-20")), "a day inside 30 days stays")
        self.assertTrue(os.path.isdir(os.path.join(self.trash, "notes")), "a folder that is not a day is never purged")
        with open(os.path.join(self.root, "ledger", "deleted", "2026-09-02.txt"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(os.path.join(self.trash, "2026-09-02", "lanes", "old.md"), text)
        self.assertIn("purged 1 of 1 day folders, purge errors 0", out)

    def test_a_trash_without_its_mark_that_holds_files_is_refused(self):
        self.put("lanes/old.md", 90)
        os.makedirs(os.path.join(self.trash, "2026-07-01"))
        open(os.path.join(self.trash, "2026-07-01", "someone-elses.txt"), "w").close()
        code, out = self.run_it("--apply")
        self.assertEqual(4, code, out)
        self.assertIn("nothing removed", out)
        self.assertTrue(self.exists("lanes/old.md"))
        self.assertTrue(os.path.exists(os.path.join(self.trash, "2026-07-01", "someone-elses.txt")))

    def test_the_trash_and_the_root_may_not_hold_each_other(self):
        self.put("lanes/old.md", 90)
        for trash in (os.path.join(self.root, "t"), self.root, os.path.dirname(self.root)):
            code, out = self.run_it("--apply", "--trash", trash)
            self.assertEqual(4, code, trash)
        self.assertTrue(self.exists("lanes/old.md"))

    def test_a_path_trashed_twice_the_same_day_keeps_both(self):
        self.put("lanes/a.md", 90, body="first")
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.put("lanes/a.md", 90, body="second")
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        with open(os.path.join(self.trash, "2026-09-02", "lanes", "a.md"), encoding="utf-8") as handle:
            self.assertEqual("first", handle.read())
        with open(os.path.join(self.trash, "2026-09-02", "lanes", "a.md.2"), encoding="utf-8") as handle:
            self.assertEqual("second", handle.read())

    def test_trash_days_zero_deletes_and_makes_no_trash(self):
        self.put("lanes/old.md", 90)
        code, out = self.run_it("--apply", scope=self.scope_with(trash_days=0))
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("lanes/old.md"))
        self.assertFalse(os.path.exists(self.trash))
        self.assertIn("no trash, deleted", out)

    def test_trash_days_must_be_a_whole_number(self):
        self.put("lanes/old.md", 90)
        for bad in ("30", -1, True, 2.5, None):
            code, out = self.run_it("--apply", scope=self.scope_with(trash_days=bad))
            self.assertEqual(4, code, repr(bad))
        self.assertTrue(self.exists("lanes/old.md"))

    def test_a_purge_that_cannot_remove_a_file_is_an_error_and_the_run_goes_on(self):
        self.put("lanes/old.md", 90)
        os.makedirs(os.path.join(self.trash, "2026-07-01"))
        open(os.path.join(self.trash, RET.TRASH_MARK), "w").close()
        stuck = os.path.join(self.trash, "2026-07-01", "stuck.txt")
        open(stuck, "w").close()
        with open(stuck, "rb"):
            code, out = self.run_it("--apply")
        if os.name != "nt":
            self.skipTest("only Windows refuses to delete an open file")
        self.assertEqual(2, code, out)
        self.assertIn("purged 0 of 1 day folders", out)
        self.assertTrue(self.trashed("lanes/old.md"), "the removals still ran")

    def test_release_artifacts_and_their_companions_go_only_by_the_release_rule(self):
        for number, days in ((79, 44), (80, 43), (90, 8)):
            self.release(number, days)
        for rel in ("builds/myapp-vc82-a-0829.apk", "builds/myapp-vc82-a-0829.aab", "builds/myapp-vc82-a-0829.sha256",
                    "builds/myapp-vc82-a-0829.apk.txt"):
            self.put(rel, 90)  # two months old, but 8 releases back: the whole build set stays
        for rel in ("builds/myapp-vc78-b-0815.apk", "builds/myapp-vc78-b-0815.aab", "builds/myapp-vc78-b-0815.sha256"):
            self.put(rel, 90)
        self.put("builds/notes-old.txt", 90)  # not a companion: the age rule takes it
        self.put("Findings/bench/r-old/shot.png", 70)
        self.put("Findings/bench/r-old/myapp-vc85-installed.apk", 70)  # inside a doomed round, 5 behind
        code, out = self.run_it("--apply", scope=self.scope_with(release_kinds=[".apk", ".aab"]))
        self.assertEqual(0, code, out)
        for rel in ("builds/myapp-vc82-a-0829.apk", "builds/myapp-vc82-a-0829.aab", "builds/myapp-vc82-a-0829.sha256",
                    "builds/myapp-vc82-a-0829.apk.txt", "Findings/bench/r-old/myapp-vc85-installed.apk"):
            self.assertTrue(self.exists(rel), rel + " was taken by age: " + out)
        for rel in ("builds/myapp-vc78-b-0815.apk", "builds/myapp-vc78-b-0815.aab", "builds/myapp-vc78-b-0815.sha256",
                    "builds/notes-old.txt", "Findings/bench/r-old/shot.png"):
            self.assertFalse(self.exists(rel), rel + " stayed: " + out)

    def test_a_short_sidecar_waits_for_every_artifact_it_names(self):
        for number, days in ((79, 44), (90, 8)):
            self.release(number, days)
        self.put("builds/myapp-vc78-c.apk", 30)
        self.put("builds/myapp-vc78-c.sha256", 30)
        self.put("builds/myapp-vc78-c.aab", 30)
        self.put("keep-this/myapp-vc78-c.aab", 30)  # a copy elsewhere is its own set
        scope = self.scope_with(keep=json.load(open(SCOPE, encoding="utf-8"))["keep"] + ["builds/myapp-vc78-c.aab"],
                                release_kinds=[".apk", ".aab"])
        code, out = self.run_it("--apply", scope=scope)
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("builds/myapp-vc78-c.apk"), out)
        self.assertTrue(self.exists("builds/myapp-vc78-c.aab"), "kept by the scope")
        self.assertTrue(self.exists("builds/myapp-vc78-c.sha256"), "its .aab stays, so its short sidecar stays")

    def test_under_the_letter_a_bundle_ages_out_and_the_apk_set_waits_for_its_release(self):
        for number, days in ((79, 44), (80, 43), (90, 8)):
            self.release(number, days)
        for rel in ("builds/myapp-vc82-a-0829.apk", "builds/myapp-vc82-a-0829.aab", "builds/myapp-vc82-a-0829.sha256"):
            self.put(rel, 90)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("builds/myapp-vc82-a-0829.aab"), "a bundle two months old goes by age: " + out)
        self.assertTrue(self.exists("builds/myapp-vc82-a-0829.apk"), out)
        self.assertTrue(self.exists("builds/myapp-vc82-a-0829.sha256"), "the short sidecar follows its APK")

    def test_release_kinds_must_be_extensions(self):
        self.put("lanes/old.md", 90)
        for bad in ("apk", ["apk"], [""], [".a pk"], None):
            code, out = self.run_it("--apply", scope=self.scope_with(release_kinds=bad))
            self.assertEqual(4, code, repr(bad))
        self.assertTrue(self.exists("lanes/old.md"))

    def test_a_file_beside_subfolders_goes_on_its_own(self):
        self.put("lanes/gates/l9/fresh.log", 5)
        self.put("lanes/gates/loose-old.done", 90)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("lanes/gates/loose-old.done"), out)
        self.assertTrue(self.exists("lanes/gates/l9/fresh.log"))

    def test_a_quiet_run_writes_no_manifest_and_makes_no_trash(self):
        self.put("lanes/new.md", 5)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("ledger/deleted/2026-09-02.txt"), out)
        self.assertFalse(os.path.exists(self.trash), out)

    def test_the_purge_runs_even_when_the_share_bound_refuses_the_plan(self):
        for i in range(10):
            self.put("lanes/old-{}.md".format(i), 90)
        os.makedirs(os.path.join(self.trash, "2026-07-01"))
        open(os.path.join(self.trash, RET.TRASH_MARK), "w").close()
        code, out = self.run_it("--apply", "--max-share", "0.5")
        self.assertEqual(5, code, out)
        self.assertFalse(os.path.exists(os.path.join(self.trash, "2026-07-01")), out)
        self.assertEqual(10, len(os.listdir(os.path.join(self.root, "lanes"))))

    def test_a_purge_unlinks_a_junction_and_spares_its_target(self):
        outside = tempfile.mkdtemp(prefix="evr outside ")
        try:
            open(os.path.join(outside, "keep.txt"), "w").close()
            day = os.path.join(self.trash, "2026-07-01")
            os.makedirs(day)
            open(os.path.join(self.trash, RET.TRASH_MARK), "w").close()
            made = subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(day, "linked"), outside],
                                  capture_output=True, timeout=60)
            if made.returncode != 0:
                self.skipTest("no junction on this machine")
            code, out = self.run_it("--apply")
            self.assertEqual(0, code, out)
            self.assertFalse(os.path.exists(day), out)
            self.assertTrue(os.path.exists(os.path.join(outside, "keep.txt")), "the purge went through the link")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_no_purge_runs_through_a_trash_the_run_refuses(self):
        outside = tempfile.mkdtemp(prefix="evr outside ")
        link = self.root + " linked-trash"
        try:
            open(os.path.join(outside, RET.TRASH_MARK), "w").close()
            os.makedirs(os.path.join(outside, "2026-07-01"))
            open(os.path.join(outside, "2026-07-01", "precious.md"), "w").close()
            for i in range(3):
                self.put("lanes/old-{}.md".format(i), 90)
            made = subprocess.run(["cmd", "/c", "mklink", "/J", link, outside], capture_output=True, timeout=60)
            if made.returncode != 0:
                self.skipTest("no junction on this machine")
            for extra in (("--apply",), ("--apply", "--max-share", "0.1"), ()):
                code, out = self.run_it("--trash", link, *extra)
                self.assertEqual(4, code, out)
                self.assertTrue(os.path.exists(os.path.join(outside, "2026-07-01", "precious.md")), out)
                self.assertEqual(3, len(os.listdir(os.path.join(self.root, "lanes"))), out)
        finally:
            if os.path.lexists(link):
                os.rmdir(link)  # removes the junction, never its target
            shutil.rmtree(outside, ignore_errors=True)

    def test_under_the_letter_a_short_sidecar_waits_for_a_bundle_of_its_stem(self):
        for number, days in ((60, 300), (90, 8)):
            self.release(number, days)
        for rel in ("builds/myapp-vc60.apk", "builds/myapp-vc60.aab", "builds/myapp-vc60.sha256"):
            self.put(rel, 5)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("builds/myapp-vc60.apk"), out)
        self.assertTrue(self.exists("builds/myapp-vc60.aab"))
        self.assertTrue(self.exists("builds/myapp-vc60.sha256"), "its bundle stays, so its checksum stays: " + out)
        self.put("builds/myapp-vc61.apk", 90)
        self.put("builds/myapp-vc61.aab", 90)
        self.put("builds/myapp-vc61.sha256", 90)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        for rel in ("builds/myapp-vc61.apk", "builds/myapp-vc61.aab", "builds/myapp-vc61.sha256"):
            self.assertFalse(self.exists(rel), rel + " stayed: " + out)

    def test_a_short_sidecar_names_its_bundle_in_any_case(self):
        for number, days in ((60, 300), (90, 8)):
            self.release(number, days)
        self.put("builds/myapp-vc60.APK", 20)
        self.put("builds/myapp-vc60.AAB", 1)
        self.put("builds/myapp-vc60.sha256", 20)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("builds/myapp-vc60.APK"), out)
        self.assertTrue(self.exists("builds/myapp-vc60.sha256"), "its bundle stays, so its checksum stays: " + out)

    def test_a_past_round_goes_without_its_release_set(self):
        for number, days in ((85, 200), (90, 8)):
            self.release(number, days)
        self.put("Findings/bench/rnd1/notes.md", 200)
        self.put("Findings/bench/rnd1/shot.png", 200)
        self.put("Findings/bench/rnd1/base-vc85.apk", 200)
        self.put("Findings/bench/rnd1/base-vc85.apk.sha256", 200)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertFalse(self.exists("Findings/bench/rnd1/notes.md"), out)
        self.assertFalse(self.exists("Findings/bench/rnd1/shot.png"), out)
        self.assertTrue(self.exists("Findings/bench/rnd1/base-vc85.apk"), "five releases back: it waits")
        self.assertTrue(self.exists("Findings/bench/rnd1/base-vc85.apk.sha256"))

    def test_a_lane_folder_below_a_bucket_goes_whole_or_not_at_all(self):
        self.put("lanes/gates/lane1/report.md", 200)
        self.put("lanes/gates/lane1/captures/shot.png", 1)
        self.put("lanes/gates/lane2/report.md", 200)
        self.put("lanes/gates/lane2/captures/shot.png", 200)
        self.put("lanes/gates/lane3/report.md", 1)
        self.put("lanes/gates/lane3/captures/shot.png", 200)
        code, out = self.run_it("--apply")
        self.assertEqual(0, code, out)
        self.assertTrue(self.exists("lanes/gates/lane1/report.md"), "a fresh capture holds its lane: " + out)
        self.assertTrue(self.exists("lanes/gates/lane1/captures/shot.png"))
        self.assertTrue(self.exists("lanes/gates/lane3/captures/shot.png"), "a fresh report holds its captures: " + out)
        self.assertFalse(self.exists("lanes/gates/lane2"), out)


    def test_a_run_without_a_root_is_refused_and_touches_the_working_directory_not_at_all(self):
        self.put("lanes/old.md", 90)
        here = os.getcwd()
        os.chdir(self.root)  # were a missing --root read as the working directory, this root would be swept
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = RET.main(["--scope", SCOPE, "--max-share", "1", "--now", NOW.isoformat(), "--apply"])
        finally:
            os.chdir(here)
        self.assertEqual(4, code)
        self.assertTrue(self.exists("lanes/old.md"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
