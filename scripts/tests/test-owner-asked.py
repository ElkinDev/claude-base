"""Pins for owner-asked.py: a seeded evidence root and memory folder in a temp dir, the script run as a session
runs it (a subprocess with OWNER_ASKED_ROOT and OWNER_ASKED_MEMORY), one case per rule the docstring states.

    python scripts/tests/test-owner-asked.py                        # against scripts/owner-asked.py
    OWNER_ASKED_PY=<path> python scripts/tests/test-owner-asked.py  # against a staged copy or a mutant
"""
import datetime as _dt
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("OWNER_ASKED_PY") or os.path.join(HERE, "..", "owner-asked.py")
SCRUBBED = ("OWNER_ASKED_ROOT", "OWNER_ASKED_MEMORY", "OWNER_ASKED_KINDS", "EVIDENCE_ROOT", "CLAUDE_CONFIG_DIR",
            "CLAUDE_RULINGS_FILE", "CLAUDE_LANDINGS_FILE")

RULINGS = (
    "# register\n"
    "- 2026-01-10 10:0x [owner] Owner 10:0x: the app ships English only for now.\n"
    "2026-01-22 17:5x [owner] Owner 17:5x: the app has two résumé templates and the café menu is per country. (analyst pane 17:5x)\n"
    "- 2026-01-08 14:2x [decision] Owner '1' on the analyst agent model: model-large-5.\n"
    "2026-01-22 18:0x [analyst] The effort of the lanes read medium; the owner asked to fix the effort.\n"
    "2026-01-21 10:0x [decision] train landed 20d3215c9 cleanly.\n"
    "2026-01-21 11:0x [decision] palette cafe12 kept for the widget.\n"
)
DECISIONS = (
    "# Owner decisions and asks, 2026-01-02\n\n"
    "- 62 RULED (2026-01-10 17:22). Owner, verbatim: that only works in one region, the app serves many more regions.\n"
    "- 11:0x Store upload: owner chose the service account, DECIDED.\n"
    "- 12:0x long answer: " + "word " * 60 + "FINAL.\n"
)
REPORTS = (
    "# Owner reports ledger\n\n| id | reported | words | lane | landed | validation | status |\n|---|---|---|---|---|---|---|\n"
    "| R-35 | 2026-01-17 14:3x | the image check failed on the test phone | none | none | none | OPEN |\n"
    "| R-59 | 2026-01-17 14:3x | trial premium, window 2026-01-18 to 2099-01-18 | none | none | none | OPEN |\n"
)
LANDINGS = (
    "# Landings\n\n## 2026-01-16\n\n"
    "- 13:3x Owner ratified voice D1 A, D2 A (typeInstead → TypingPane). Owner: 'go on'.\n"
    "- 14:00 landed lane voice D3 on main.\n"
)
MEMORY = ("---\nname: autonomy-frontmatter\nmodified: 2026-01-18T10:00:00Z\n---\n\n"
          "Owner rule: never write money alone, the autonomy doctrine.\n"
          "**How to apply (until 2026-01-30):** keep the calendar rule per country.\n"
          "2026-01-05 owner said keep the widget tiny.\n"
          "**Update 2026-01-06 11:0x:** owner chose the blue theme.\n")
MEMORY_STALE_FIELD = ("---\nname: build-daemons\nmodified: 2026-01-10T10:00:00Z\n---\n\n"
                      "The owner saw RAM at 70 percent and asked for a daemon cap.\n")


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED}
    env.update(extra)
    return env


def run(root, mem, *args, **kw):
    env = clean_env(OWNER_ASKED_ROOT=root, OWNER_ASKED_MEMORY=mem, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", **kw)
    r = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True, encoding="utf-8",
                       env=env, timeout=60)
    return r.returncode, r.stdout


def run_env(env, *args, cwd=None):
    r = subprocess.run([sys.executable, os.path.abspath(SCRIPT)] + list(args), capture_output=True, text=True,
                       encoding="utf-8", env=dict(env, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"), cwd=cwd, timeout=60)
    return r.returncode, r.stdout, r.stderr


def run_console(root, mem, *args):
    """As a console runs it: no PYTHONIOENCODING, no PYTHONUTF8, stdout a pipe in the console's codepage."""
    env = clean_env(OWNER_ASKED_ROOT=root, OWNER_ASKED_MEMORY=mem)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    r = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, env=env, timeout=60)
    return r.returncode, r.stdout


def stamp(day):
    return _dt.datetime.strptime(day + " 12:00", "%Y-%m-%d %H:%M").timestamp()


def main():
    tmp = tempfile.mkdtemp(prefix="owner-asked-pin-")
    results = []
    try:
        root, mem = os.path.join(tmp, "ev"), os.path.join(tmp, "mem")
        os.makedirs(os.path.join(root, "drafts"))
        os.makedirs(os.path.join(root, "ledger"))
        os.makedirs(mem)
        seeds = {os.path.join(root, "rulings.md"): RULINGS,
                 os.path.join(root, "drafts", "owner-decisions-2026-01-02.md"): DECISIONS,
                 os.path.join(root, "ledger", "owner-reports.md"): REPORTS,
                 os.path.join(root, "landings.md"): LANDINGS,
                 os.path.join(mem, "autonomy.md"): MEMORY,
                 os.path.join(mem, "build-daemons.md"): MEMORY_STALE_FIELD}
        for path, text in seeds.items():
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        # A memory file is dated by the later of its modified field and its mtime, so the fixtures carry set
        # mtimes: autonomy older than its field (the field wins), build-daemons newer (the mtime wins).
        os.utime(os.path.join(mem, "autonomy.md"), (stamp("2026-01-10"), stamp("2026-01-10")))
        os.utime(os.path.join(mem, "build-daemons.md"), (stamp("2026-01-19"), stamp("2026-01-19")))

        def check(name, fn):
            try:
                ok = bool(fn())
            except Exception as e:
                ok = False
                name += " (%s: %s)" % (type(e).__name__, str(e)[:80])
            results.append(ok)
            print(("OK   " if ok else "FAIL ") + name)

        def row_sh_shape():
            rc, out = run(root, mem, "cafe", "menu")
            return rc == 0 and "rulings.md:3:" in out and out.startswith("2026-01-22")
        check("a row.sh row (no leading dash) is found; cafe finds the accented word", row_sh_shape)

        def accent_other_way():
            rc, out = run(root, mem, "résumé")
            return rc == 0 and "rulings.md:3:" in out
        check("an accented query finds the row", accent_other_way)

        def miss():
            rc, out = run(root, mem, "templates", "portuguese")
            return rc == 1 and "question is new" in out
        check("every word must match; a miss exits 1", miss)

        def since():
            rc_all, out_all = run(root, mem, "ships", "English")
            rc_new, _ = run(root, mem, "ships", "English", "--since", "2026-01-15")
            return rc_all == 0 and "rulings.md:2:" in out_all and rc_new == 1
        check("--since drops older lines", since)

        def kinds():
            rc_dec, out_dec = run(root, mem, "model-large-5")
            rc_def, _ = run(root, mem, "effort", "medium")
            rc_all, out_all = run(root, mem, "effort", "medium", "--all-rows")
            return (rc_dec == 0 and "rulings.md:4:" in out_dec and rc_def == 1
                    and rc_all == 0 and "rulings.md:5:" in out_all)
        check("[decision] rows by default, [analyst] only with --all-rows", kinds)

        def kinds_env():
            rc_an, out_an = run(root, mem, "effort", "medium", OWNER_ASKED_KINDS="analyst")
            rc_dec, _ = run(root, mem, "model-large-5", OWNER_ASKED_KINDS="analyst")
            return rc_an == 0 and "rulings.md:5:" in out_an and rc_dec == 1
        check("OWNER_ASKED_KINDS replaces the default kinds", kinds_env)

        def decisions_inline_date():
            rc, out = run(root, mem, "regions", "--since", "2026-01-05")
            return rc == 0 and out.startswith("2026-01-10 drafts/owner-decisions-2026-01-02.md:3:")
        check("a decisions line is dated by its latest inline date, not the file name", decisions_inline_date)

        def decisions_name_fallback():
            rc, out = run(root, mem, "service", "account")
            return rc == 0 and out.startswith("2026-01-02 drafts/owner-decisions-2026-01-02.md:4:")
        check("a decisions line with no date takes the file name's", decisions_name_fallback)

        def owner_reports():
            rc, out = run(root, mem, "image", "check")
            return rc == 0 and out.startswith("2026-01-17 ledger/owner-reports.md:")
        check("owner reports are a source", owner_reports)

        def report_header_skipped():
            rc, _ = run(root, mem, "reported", "validation")
            return rc == 1
        check("the reports table header and separator are not rows", report_header_skipped)

        def landings():
            rc, out = run(root, mem, "voice", "D1")
            rc_non, _ = run(root, mem, "landed", "lane", "D3")
            return rc == 0 and out.startswith("2026-01-16 landings.md:5:") and rc_non == 1
        check("landings: owner lines only, dated by the date above them", landings)

        def memory():
            rc, out = run(root, mem, "money", "alone")
            rc_fm, _ = run(root, mem, "frontmatter")
            return rc == 0 and out.startswith("2026-01-18 memory/autonomy.md:6:") and rc_fm == 1
        check("memory: body dated by modified, frontmatter not searched", memory)

        def newest_first():
            rc, out = run(root, mem, "app", "--all-rows")
            days = [ln[:10] for ln in out.splitlines() if ln[:2] == "20"]
            return rc == 0 and len(days) >= 3 and days == sorted(days, reverse=True)
        check("hits print newest first", newest_first)

        def console_codepage():
            rc, out = run_console(root, mem, "typeInstead")
            return rc == 0 and b"landings.md:5:" in out and "→".encode("utf-8") in out
        check("a non-ASCII line prints whatever the console codepage, exit 0", console_codepage)

        def memory_head_date():
            rc, out = run(root, mem, "calendar", "rule")
            return rc == 0 and out.startswith("2026-01-18 memory/autonomy.md:")
        check("a memory date past the head of the line does not date it", memory_head_date)

        def cut():
            rc, out = run(root, mem, "long", "answer")
            line = out.splitlines()[0]
            text = line.split(": ", 1)[1]
            return rc == 0 and text.endswith(" [cut]") and len(text) == 320
        check("a long line is cut at 320 characters with the marker", cut)

        def hash_not_a_hit():
            rc, _ = run(root, mem, "d3", "cleanly")
            rc2, out2 = run(root, mem, "landed", "cleanly")
            return rc == 1 and rc2 == 0 and "rulings.md:6:" in out2
        check("a word found only inside a commit hash is not a hit", hash_not_a_hit)

        def mtime_beats_stale_field():
            rc, out = run(root, mem, "daemon", "cap", "--since", "2026-01-15")
            return rc == 0 and out.startswith("2026-01-19 memory/build-daemons.md:")
        check("a memory file edited after its modified field is dated by its mtime", mtime_beats_stale_field)

        def head_date_wins():
            rc, out = run(root, mem, "widget", "tiny")
            return rc == 0 and out.startswith("2026-01-05 memory/autonomy.md:")
        check("a memory line that opens with a date takes that date", head_date_wins)

        def short_hex_searchable():
            rc, out = run(root, mem, "cafe12")
            return rc == 0 and "rulings.md:7:" in out
        check("a run of six hex characters is still searchable", short_hex_searchable)

        def blank_word():
            rc, _ = run(root, mem, " ")
            return rc == 2
        check("a blank word is a usage error, never every line", blank_word)

        def future_date_ignored():
            rc, out = run(root, mem, "trial", "window")
            return rc == 0 and out.startswith("2026-01-18 ledger/owner-reports.md:")
        check("a date later than today does not date a line", future_date_ignored)

        def head_date_after_bold():
            rc, out = run(root, mem, "blue", "theme")
            return rc == 0 and out.startswith("2026-01-06 memory/autonomy.md:")
        check("a date that opens a line after bold marks and a word dates it", head_date_after_bold)

        def evidence_root_fallback():
            rc, out, _ = run_env(clean_env(EVIDENCE_ROOT=root, OWNER_ASKED_MEMORY=mem), "cafe", "menu")
            return rc == 0 and "rulings.md:3:" in out
        check("with no OWNER_ASKED_ROOT the evidence root is EVIDENCE_ROOT", evidence_root_fallback)

        def default_memory_folder():
            cfg, work = os.path.join(tmp, "cfg"), os.path.join(tmp, "work dir")
            os.makedirs(work)
            folder = os.path.join(cfg, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(work)), "memory")
            os.makedirs(folder)
            shutil.copy(os.path.join(mem, "autonomy.md"), folder)
            rc, out, _ = run_env(clean_env(OWNER_ASKED_ROOT=root, CLAUDE_CONFIG_DIR=cfg), "money", "alone", cwd=work)
            return rc == 0 and "memory/autonomy.md:6:" in out
        check("the default memory folder is <config dir>/projects/<working directory, dashed>/memory",
              default_memory_folder)

        def register_and_landings_vars():
            moved = os.path.join(tmp, "moved")
            os.makedirs(moved)
            shutil.copy(os.path.join(root, "rulings.md"), os.path.join(moved, "register.md"))
            shutil.copy(os.path.join(root, "landings.md"), os.path.join(moved, "moves.md"))
            env = clean_env(OWNER_ASKED_ROOT=os.path.join(tmp, "none"), OWNER_ASKED_MEMORY=mem,
                            CLAUDE_RULINGS_FILE=os.path.join(moved, "register.md"),
                            CLAUDE_LANDINGS_FILE=os.path.join(moved, "moves.md"))
            rc, out, _ = run_env(env, "cafe", "menu")
            rc2, out2, _ = run_env(env, "voice", "D1")
            return rc == 0 and "register.md:3:" in out and rc2 == 0 and "moves.md:5:" in out2
        check("CLAUDE_RULINGS_FILE and CLAUDE_LANDINGS_FILE move the register and the landings", register_and_landings_vars)

        def missing_sources():
            empty = os.path.join(tmp, "empty")
            os.makedirs(empty)
            rc, out, err = run_env(clean_env(OWNER_ASKED_ROOT=empty, OWNER_ASKED_MEMORY=os.path.join(tmp, "none")),
                                   "anything")
            return rc == 1 and "question is new" in out and "Traceback" not in err
        check("a root and a memory folder with no sources are a clean miss, never a crash", missing_sources)

        def reader_closes_early():
            big = os.path.join(tmp, "big")
            os.makedirs(os.path.join(big, "drafts"))
            with open(os.path.join(big, "drafts", "owner-decisions-2026-01-20.md"), "w", encoding="utf-8") as f:
                for i in range(3000):
                    f.write("- %04d bulkword owner line padded to make the output larger than a pipe buffer.\n" % i)
            env = clean_env(OWNER_ASKED_ROOT=big, OWNER_ASKED_MEMORY=mem)
            proc = subprocess.Popen([sys.executable, SCRIPT, "bulkword", "--max", "3000"], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=env)
            proc.stdout.readline()
            proc.stdout.readline()
            proc.stdout.close()
            err = proc.stderr.read()
            rc = proc.wait(timeout=60)
            return rc == 0 and err.strip() == b""
        check("a reader that closes early (| head) is exit 0 with no traceback", reader_closes_early)

        def bad_since():
            rc, _ = run(root, mem, "x", "--since", "22-01-2026")
            return rc == 2
        check("a malformed --since is a usage error", bad_since)

        def bad_max():
            return run(root, mem, "x", "--max", "0")[0] == 2 and run(root, mem, "x", "--max", "-1")[0] == 2
        check("--max under 1 is a usage error, never an empty or clipped list", bad_max)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of %d OK (%s)" % (sum(results), len(results), SCRIPT))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
