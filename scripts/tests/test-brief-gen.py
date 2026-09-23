"""Pins for scripts/brief-gen.py: the fix and notes briefs carry the hostile-input line of the lane brief template and
the review brief does not; the defaults name no build tool; the configured commands, suffix and laws are rendered;
a bad config is exit 2; and one end-to-end run reads a lane report and a real git worktree.

    python scripts/tests/test-brief-gen.py
    BRIEF_GEN_PY=<path> python scripts/tests/test-brief-gen.py     # against a staged copy

The builders are called directly with a fixed set of lane facts and a temp review file; the end-to-end case makes
its own evidence root and git repository in a temp folder. Nothing real is read or written.
"""
import argparse
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("BRIEF_GEN_PY") or os.path.join(HERE, "..", "brief-gen.py")
CHECKER = os.path.join(os.path.dirname(os.path.abspath(SCRIPT)), "brief-check.py")
MARK = "the hostile-input table of the Report section of"
FX = dict(slug="demo-lane", token="demo", item="ABC-12", topic="a demo topic", tip="abc1234", base="def5678",
          wt="C:/src/myapp-demo", branch="demo-branch", report="/ev/lanes/demo-lane-2026-01-22.md",
          brief="/ev/briefs/demo-lane-2026-01-22.md", commits=["abc1234 demo change"])
REVIEW = "Disposition: BLOCK (1 MAJOR, 1 MINOR)\n\n## MAJOR\n\n1. A.kt:3 is wrong.\n\n## MINOR\n\n1. B.kt:4 reads badly.\n"
CONFIGURED = dict(evidence_root="/ev", laws="/ev/laws.md", subject_suffix="[skip ci]",
                  own_tests_command="bash scripts/run-own-tests.sh {worktree_posix} {tag} '{tests}'",
                  own_tests_done="{worktree_posix}/build/runs/{tag}.done",
                  precheck_command="bash scripts/precheck.sh {worktree_posix}")


def load():
    # An explicit loader: a staged copy may end in .py.new, which spec_from_file_location does not recognise.
    loader = importlib.machinery.SourceFileLoader("brief_gen_under_test", SCRIPT)
    spec = importlib.util.spec_from_loader("brief_gen_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_DATE="@1767000000 +0000", GIT_COMMITTER_DATE="@1767000000 +0000")
    subprocess.run(["git", "-C", repo, "-c", "user.name=fixture", "-c", "user.email=fixture@example.com",
                    "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false"] + list(args),
                   check=True, capture_output=True, env=env, timeout=60)


def main():
    tmp = tempfile.mkdtemp(prefix="brief-gen-pin-")
    results = []

    def check(name, fn):
        try:
            ok = bool(fn())
            note = ""
        except Exception as e:
            ok, note = False, " (%s: %s)" % (type(e).__name__, str(e)[:90])
        results.append(ok)
        print(("OK   " if ok else "FAIL ") + name + note)

    try:
        mod = load()
        review = os.path.join(tmp, "demo-review.md")
        with open(review, "w", encoding="utf-8") as f:
            f.write(REVIEW)
        args = argparse.Namespace(round=2, review=review, tools=None, change=None, pins=None, tests=None, delta=0,
                                  attack=None, purpose=None)

        def configure(**over):
            mod.CFG = dict(mod.DEFAULTS, **dict({"evidence_root": "/ev"}, **over))

        def report_of(body):
            return body.split("## Report", 1)[1].split("## Forbidden", 1)[0]

        def fix_has_it():
            configure()
            _, body = mod.fix_or_notes_brief(dict(FX), args, "fix")
            rep = report_of(body)
            return (MARK in rep and "H1 to H8" in rep and "timeout 120" in rep
                    and "/ev/briefs/TEMPLATE-lane-brief.md" in rep)
        check("a fix brief's Report section carries the hostile-input line and the template under the root", fix_has_it)

        def notes_has_it():
            configure()
            _, body = mod.fix_or_notes_brief(dict(FX), args, "notes")
            return MARK in report_of(body)
        check("a notes brief's Report section carries it too", notes_has_it)

        def review_has_not():
            configure()
            _, body = mod.review_brief(dict(FX), args)
            return MARK not in body and "/ev/reviews/demo-lane-" in body
        check("a review brief, written for a reviewer, does not, and names its report under the reviews folder",
              review_has_not)

        def ascii_only():
            configure(**CONFIGURED)
            bodies = [mod.fix_or_notes_brief(dict(FX), args, k)[1] for k in ("fix", "notes")]
            bodies.append(mod.review_brief(dict(FX), args)[1])
            return all(ord(c) < 128 for b in bodies for c in b)
        check("every brief is ASCII and carries no em-dash", ascii_only)

        def defaults_are_neutral():
            configure()
            _, body = mod.fix_or_notes_brief(dict(FX), args, "fix")
            low = body.lower()
            return (all(w not in low for w in ("gradle", "lockrun", "gate-detach", "[skip ci]", "laws:", "precheck"))
                    and "<<the command that runs your own test classes" in body and "subject ending" not in body)
        check("the defaults name no build tool, no suffix, no laws file and no precheck", defaults_are_neutral)

        def configured_rendered():
            configure(**CONFIGURED)
            _, body = mod.fix_or_notes_brief(dict(FX, wt="C:/src/myapp-demo"), args, "fix")
            checks = body.split("## Checks", 1)[1].split("## Report", 1)[0]
            steps = [ln for ln in checks.splitlines() if ln.startswith("- ")]
            return ("`bash scripts/run-own-tests.sh /c/src/myapp-demo demo-fix1 '<<the test selection" in checks
                    and "END YOUR TURN with one line naming /ev/lanes/demo-lane-2026-01-22.md and "
                        "/c/src/myapp-demo/build/runs/demo-fix1.done" in checks
                    and "A green run is not resumed" in checks
                    and "`bash scripts/precheck.sh /c/src/myapp-demo` on the committed tip" in checks
                    # the order: commit, precheck, report, then the launch as the last step
                    and len(steps) == 4 and steps[0].startswith("- First the commit") and "precheck.sh" in steps[1]
                    and steps[2].startswith("- Then the report") and steps[3].startswith("- Last, the own-tests run")
                    and "## Change, one commit, subject ending `[skip ci]`" in body
                    and "Laws: /ev/laws.md." in body and "the precheck line" in report_of(body)
                    and "before the own-tests launch" in report_of(body))
        check("configured commands, done path, precheck, suffix and laws are filled in; the launch is the last step "
              "and the report comes before it", configured_rendered)

        def no_done_keeps_the_foreground_run():
            configure(**dict(CONFIGURED, own_tests_done=None))
            _, body = mod.fix_or_notes_brief(dict(FX), args, "fix")
            checks = body.split("## Checks", 1)[1].split("## Report", 1)[0]
            return ("- Green, on the committed tip: `bash scripts/run-own-tests.sh" in checks
                    and "the report names the tag and the exit" in checks and "END YOUR TURN" not in checks
                    and "the test run tag and exit" in report_of(body))
        check("with no done file the run stays a foreground step and the report names its exit",
              no_done_keeps_the_foreground_run)

        def tier_three():
            configure(**CONFIGURED)
            a2 = argparse.Namespace(**dict(vars(args), pins="- DemoParserTest: the parser keeps the last key."))
            _, body = mod.fix_or_notes_brief(dict(FX), a2, "fix")
            out = os.path.join(tmp, "tier3.md")
            with open(out, "w", encoding="utf-8") as f:
                f.write(body)
            r = subprocess.run([sys.executable, CHECKER, out, "--deny-tier", "3"], capture_output=True, text=True,
                               timeout=60)
            return r.returncode == 0
        check("a fix brief with a pin class passes brief-check.py at deny tier 3", tier_three)

        def blank_token_refused():
            out = os.path.join(tmp, "blank.md")
            r = subprocess.run([sys.executable, SCRIPT, "fix", "  ", "--review", review, "--out", out],
                               capture_output=True, timeout=60)
            return r.returncode == 2 and not os.path.exists(out) and b"not blank" in r.stderr
        check("a blank lane token is a usage error and writes no brief", blank_token_refused)

        def bad_configs():
            cases = {"list.json": ("[1, 2]", "not a JSON object"),
                     "unknown.json": (json.dumps({"worktre": "x"}), "unknown key"),
                     "type.json": (json.dumps({"review_tools": "14"}), "whole number"),
                     "blank.json": (json.dumps({"base_branch": " "}), "not blank"),
                     "brace.json": (json.dumps({"precheck_command": "run {wt}"}), "does not fill"),
                     "broken.json": ("{\"laws\": ,}", "cannot be read"),
                     "doneonly.json": (json.dumps({"own_tests_done": "/w/{tag}.done"}), "needs own_tests_command")}
            ok = 0
            for name, (text, why) in cases.items():
                path = os.path.join(tmp, name)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                r = subprocess.run([sys.executable, SCRIPT, "review", "demo"], capture_output=True, text=True,
                                   timeout=60, env=dict(os.environ, BRIEF_GEN_CONFIG=path, EVIDENCE_ROOT=tmp))
                ok += r.returncode == 2 and why in r.stderr
            r = subprocess.run([sys.executable, SCRIPT, "review", "demo"], capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, BRIEF_GEN_CONFIG=os.path.join(tmp, "absent.json")))
            ok += r.returncode == 2 and "does not exist" in r.stderr
            return ok == len(cases) + 1
        check("a config that is not an object, has an unknown key, a wrong type, a blank branch, a stray field or "
              "broken JSON, or is named and absent, is exit 2 with the reason", bad_configs)

        def report_worktree_word():
            ev = os.path.join(tmp, "ev-word")
            os.makedirs(os.path.join(ev, "lanes"))
            with open(os.path.join(ev, "lanes", "word-lane-2026-01-22.md"), "w", encoding="utf-8") as f:
                f.write("# Lane word (item 7): a word topic\n\nWorktree `/srv/lanes/word-wt`, branch x.\n")
            mod.CFG = dict(mod.DEFAULTS, evidence_root=ev)
            fx = mod.lane_facts("word")
            return fx["wt"] == "/srv/lanes/word-wt" and fx["item"] == "item 7" and fx["topic"] == "a word topic"
        check("with no worktree pattern the report's Worktree word is used, a POSIX path included", report_worktree_word)

        def worktree_with_a_space():
            ev = os.path.join(tmp, "ev-space")
            os.makedirs(os.path.join(ev, "lanes"))
            with open(os.path.join(ev, "lanes", "spc-lane-2026-01-22.md"), "w", encoding="utf-8") as f:
                f.write("# Lane spc: a topic\n\nWorktree `C:/src/my app-spc`, branch x.\n")
            with open(os.path.join(ev, "lanes", "bare-lane-2026-01-22.md"), "w", encoding="utf-8") as f:
                f.write("# Lane bare: a topic\n\nWorktree C:/src/plain-bare, branch x.\n")
            with open(os.path.join(ev, "lanes", "abv-lane-2026-01-22.md"), "w", encoding="utf-8") as f:
                f.write("# Lane abv: a topic\n\nRun in the orchestrator worktree `C:/src/other checkout`.\n"
                        "Worktree C:/src/abv-wt, branch lane-abv.\n")
            with open(os.path.join(ev, "lanes", "ord-lane-2026-01-22.md"), "w", encoding="utf-8") as f:
                f.write("# Lane ord (item 3): a topic\n\nWorktree C:/src/ord-wt, branch lane-ord.\n"
                        "The orchestrator worktree `C:/src/other checkout` holds the kit scripts.\n")
            mod.CFG = dict(mod.DEFAULTS, evidence_root=ev)
            return (mod.lane_facts("spc")["wt"] == "C:/src/my app-spc" and mod.lane_facts("bare")["wt"] == "C:/src/plain-bare"
                    and mod.lane_facts("ord")["wt"] == "C:/src/ord-wt"
                    # the rule is position, case aside: a prose "worktree `...`" above the field wins, by design
                    # (brief-gen.py, the comment above the search); a report names its Worktree first
                    and mod.lane_facts("abv")["wt"] == "C:/src/other checkout")
        check("a backticked Worktree path keeps its spaces; a bare one ends at the comma; the first Worktree word "
              "wins over a later backticked mention", worktree_with_a_space)

        def end_to_end():
            ev, wt = os.path.join(tmp, "ev"), os.path.join(tmp, "wt-e2e")
            os.makedirs(os.path.join(ev, "lanes"))
            os.makedirs(os.path.join(ev, "briefs"))
            with open(os.path.join(ev, "lanes", "e2e-lane-2026-01-22.md"), "w", encoding="utf-8") as f:
                f.write("# Lane e2e (ABC-12): parser keeps the last key, 2026-01-22\n")
            os.makedirs(wt)
            git(wt, "init", "-q", "-b", "main")
            with open(os.path.join(wt, "a.txt"), "w") as f:
                f.write("a\n")
            git(wt, "add", "-A")
            git(wt, "commit", "-q", "-m", "base commit")
            git(wt, "checkout", "-q", "-b", "e2e-branch")
            with open(os.path.join(wt, "a.txt"), "w") as f:
                f.write("b\n")
            git(wt, "commit", "-q", "-am", "the lane commit")
            cfg = os.path.join(tmp, "e2e.json")
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"worktree": os.path.join(tmp, "wt-{token}").replace("\\", "/")}, f)
            r = subprocess.run([sys.executable, SCRIPT, "review", "e2e"], capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, BRIEF_GEN_CONFIG=cfg, EVIDENCE_ROOT=ev))
            written = [n for n in os.listdir(os.path.join(ev, "briefs")) if n.startswith("e2e-lane-review-")]
            again = subprocess.run([sys.executable, SCRIPT, "review", "e2e"], capture_output=True, text=True,
                                   timeout=60, env=dict(os.environ, BRIEF_GEN_CONFIG=cfg, EVIDENCE_ROOT=ev))
            return (r.returncode == 0 and len(written) == 1 and "(ABC-12): parser keeps the last key," in r.stdout
                    and "branch e2e-branch" in r.stdout and "the lane commit" in r.stdout
                    and "merge-base with main" in r.stdout and "base commit" not in r.stdout
                    and again.returncode != 0 and "exists" in again.stderr)
        check("end to end: the report and a real worktree give the item, branch, base and commits; a second run "
              "never overwrites", end_to_end)

        def fresh_root_gets_its_briefs_folder():
            ev = os.path.join(tmp, "ev-fresh")
            os.makedirs(os.path.join(ev, "lanes"))
            with open(os.path.join(ev, "lanes", "e2e-lane-2026-01-22.md"), "w", encoding="utf-8") as f:
                f.write("# Lane e2e (ABC-12): parser keeps the last key, 2026-01-22\n")
            r = subprocess.run([sys.executable, SCRIPT, "review", "e2e"], capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, BRIEF_GEN_CONFIG=os.path.join(tmp, "e2e.json"), EVIDENCE_ROOT=ev))
            briefs = os.path.join(ev, "briefs")
            return (r.returncode == 0 and "Traceback" not in r.stderr and os.path.isdir(briefs)
                    and any(n.startswith("e2e-lane-review-") for n in os.listdir(briefs)))
        check("a first run on an evidence root with no briefs folder creates it, no traceback",
              fresh_root_gets_its_briefs_folder)

        def out_folder_refused():
            ev = os.path.join(tmp, "ev-out")
            os.makedirs(os.path.join(ev, "lanes"), exist_ok=True)
            with open(os.path.join(ev, "lanes", "e2e-lane-2026-01-22.md"), "w", encoding="utf-8") as f:
                f.write("# Lane e2e (ABC-12): parser keeps the last key, 2026-01-22\n")
            env = dict(os.environ, BRIEF_GEN_CONFIG=os.path.join(tmp, "e2e.json"), EVIDENCE_ROOT=ev)
            a_dir = os.path.join(tmp, "a folder")
            os.makedirs(a_dir, exist_ok=True)
            r = subprocess.run([sys.executable, SCRIPT, "review", "e2e", "--out", a_dir, "--force", "--force-review"],
                               capture_output=True, text=True, timeout=60, env=env)
            typo = os.path.join(tmp, "breifs", "x.md")
            r2 = subprocess.run([sys.executable, SCRIPT, "review", "e2e", "--out", typo, "--force-review"],
                                capture_output=True, text=True, timeout=60, env=env)
            return (r.returncode == 1 and "--out is a folder" in r.stderr and "Traceback" not in r.stderr
                    and os.path.isdir(a_dir) and r2.returncode == 1 and "the folder of --out does not exist" in r2.stderr
                    and "Traceback" not in r2.stderr and not os.path.exists(os.path.dirname(typo)))
        check("an --out that is a folder, or whose folder is missing, is refused with one line and no folder made; "
              "only the default path creates the briefs folder", out_folder_refused)

        def review_names_its_lane_on_line_1():
            # train-due.py and train-wait.py find a review's lane on its line 1
            configure()
            _, body = mod.review_brief(dict(FX), args)
            _, delta = mod.review_brief(dict(FX), argparse.Namespace(**dict(vars(args), delta=2, review="r.md")))
            want = "`Disposition: <CLEAR, CLEAR with notes or BLOCK>, lane %s`" % FX["token"]
            return want in body and want in delta
        check("a review brief asks for the verdict and the lane token on the review's first line",
              review_names_its_lane_on_line_1)

        def disposition_drops_the_lane():
            p = os.path.join(tmp, "disp.md")
            got = []
            for first in ("Disposition: BLOCK, lane demo\n", "Disposition: CLEAR with notes, lane demo\n",
                          "Disposition: BLOCK (1 MAJOR)\n"):
                with open(p, "w", encoding="utf-8") as f:
                    f.write(first)
                got.append(mod.disposition(p))
            return got == ["BLOCK", "CLEAR with notes", "BLOCK (1 MAJOR)"]
        check("the quoted disposition drops the lane the review's first line names", disposition_drops_the_lane)

        # The review guards and the no-git shape, on a temp evidence root with no config (the defaults).
        ev = os.path.join(tmp, "ev-guards")
        for sub in ("lanes", "briefs", "reviews"):
            os.makedirs(os.path.join(ev, sub))
        today = mod.TODAY
        with open(os.path.join(ev, "lanes", "gd-lane-%s.md" % today), "w", encoding="utf-8") as f:
            f.write("# Lane gd (ABC-9): a guard topic\n\nNo worktree, no branch, no commit.\n")
        genv = dict(os.environ, BRIEF_GEN_CONFIG=os.path.join(tmp, "e2e.json"), EVIDENCE_ROOT=ev)

        def gen(*argv):
            return subprocess.run([sys.executable, SCRIPT] + list(argv), capture_output=True, text=True, env=genv,
                                  timeout=60)

        def round_and_delta_guards():
            out = os.path.join(tmp, "guard.md")
            rs = [gen("review", "gd", "--round", "2", "--out", out),
                  gen("notes", "gd", "--review", review, "--round", "2", "--out", out),
                  gen("fix", "gd", "--review", review, "--round", "1", "--out", out),
                  gen("notes", "gd", "--review", review, "--delta", "5", "--out", out),
                  gen("review", "gd", "--delta", "1", "--out", out)]
            return (all(r.returncode == 2 for r in rs) and "--delta N" in rs[0].stderr and "--round" in rs[2].stderr
                    and "--delta" in rs[3].stderr and not os.path.exists(out))
        check("--round only on fix and 2 or more; --delta only on review and 2 or more; no brief is written",
              round_and_delta_guards)

        def review_never_overwrites():
            r1 = os.path.join(ev, "reviews", "gd-lane-%s.md" % today)
            with open(r1, "w", encoding="utf-8") as f:
                f.write("Disposition: BLOCK (1 MAJOR)\n")
            out, out2, out3 = (os.path.join(tmp, n) for n in ("rv.md", "rv2.md", "rv3.md"))
            again = gen("review", "gd", "--out", out)
            none_written = not os.path.exists(out)
            delta = gen("review", "gd", "--delta", "2", "--review", r1, "--out", out2)
            body2 = open(out2, encoding="utf-8").read() if os.path.exists(out2) else ""
            plain_force = gen("review", "gd", "--force", "--out", out3)
            refused_too = plain_force.returncode == 1 and "--force-review" in plain_force.stderr and not os.path.exists(out3)
            forced = gen("review", "gd", "--force-review", "--out", out3)
            body3 = open(out3, encoding="utf-8").read() if os.path.exists(out3) else ""
            guard = "already exists when you start, write nothing"
            return (again.returncode == 1 and "gd-lane-%s.md exists" % today in again.stderr and none_written
                    and delta.returncode == 0 and "/reviews/gd-lane-fix1-%s.md" % today in body2 and guard in body2
                    and refused_too and forced.returncode == 0 and guard not in body3
                    and open(r1, encoding="utf-8").read() == "Disposition: BLOCK (1 MAJOR)\n")
        check("a review whose deliverable exists is refused; a delta review names its own file and tells its reviewer "
              "to stop if that file exists; --force alone does not pass it, --force-review does and drops the line", review_never_overwrites)

        def nogit_shape():
            n, rv = os.path.join(tmp, "nogit-notes.md"), os.path.join(tmp, "nogit-review.md")
            notes = gen("notes", "gd", "--review", review, "--no-git", "--pins", "- DemoReadingTest, red on the live file",
                        "--out", n)
            rev = gen("review", "gd", "--no-git", "--delta", "3", "--review", review, "--out", rv)
            nb = open(n, encoding="utf-8").read() if os.path.exists(n) else ""
            rb = open(rv, encoding="utf-8").read() if os.path.exists(rv) else ""
            chk = subprocess.run([sys.executable, CHECKER, n, "--deny-tier", "3"], capture_output=True, text=True,
                                 timeout=60)
            bad = gen("fix", "gd", "--review", review, "--no-git", "--tests", "SomeTest", "--out", os.path.join(tmp, "x.md"))
            return (notes.returncode == 0 and rev.returncode == 0 and chk.returncode == 0
                    and not any(w in nb + rb for w in ("<<branch>>", "<<tip>>", "<<base>>", "<<worktree", "rev-parse"))
                    and "## Change, in the staged .new files" in nb and "Never a live file" in nb
                    and "reading and running the live file for the red pin is allowed" in nb
                    and MARK in report_of(nb) and "B.kt:4 reads badly." in nb and "`.new` file as it is on disk" in rb
                    and "no live file touched" in rb and all(ord(c) < 128 for c in nb + rb)
                    and bad.returncode == 2 and "--no-git" in bad.stderr)
        check("--no-git: a notes and a review brief name no worktree, tip or run, the notes brief passes brief-check "
              "tier 3, and --tests with it is refused", nogit_shape)

        # Stacked lanes: a temp repo whose base branch is trunk (set by the config), lane-a (A1 on trunk), lane-b
        # (B1, B2 on lane-a) checked out in the repo, lane-c (C1 on trunk) in its own worktree.
        ev = os.path.join(tmp, "ev-stack")
        for sub in ("lanes", "briefs", "reviews"):
            os.makedirs(os.path.join(ev, sub))
        repo = os.path.join(tmp, "stack-repo").replace("\\", "/")
        wtc = os.path.join(tmp, "stack-wtc").replace("\\", "/")
        os.makedirs(repo)
        git(repo, "init", "-q", "-b", "trunk")
        git(repo, "commit", "-q", "--allow-empty", "-m", "root")
        git(repo, "checkout", "-q", "-b", "lane-a")
        git(repo, "commit", "-q", "--allow-empty", "-m", "A1 the parent lane")
        git(repo, "checkout", "-q", "-b", "lane-b")
        for s in ("B1 the stacked lane", "B2 the stacked lane"):
            git(repo, "commit", "-q", "--allow-empty", "-m", s)
        git(repo, "branch", "lane-c", "trunk")
        git(repo, "worktree", "add", "-q", wtc, "lane-c")
        git(wtc, "commit", "-q", "--allow-empty", "-m", "C1 a plain lane")
        rev = lambda ref: subprocess.run(["git", "-C", repo, "rev-parse", ref], capture_output=True, text=True,
                                         check=True, timeout=60).stdout.strip()
        a1, root, head = rev("lane-a"), rev("trunk"), rev("lane-b")
        for tok, wt in (("stk", repo), ("pln", wtc)):
            with open(os.path.join(ev, "lanes", "%s-lane-%s.md" % (tok, today)), "w", encoding="utf-8") as f:
                f.write("# Lane %s (ABC-7): a stacked topic\n\nWorktree %s, branch as git says.\n" % (tok, wt))
            with open(os.path.join(ev, "briefs", "%s-lane-%s.md" % (tok, today)), "w", encoding="utf-8") as f:
                f.write("# the lane brief\n")
        plain_dir = os.path.join(tmp, "stack-plain-dir").replace("\\", "/")
        os.makedirs(plain_dir)
        for tok, wt in (("gone", os.path.join(tmp, "no-such-wt").replace("\\", "/")), ("nogit", plain_dir)):
            with open(os.path.join(ev, "lanes", "%s-lane-%s.md" % (tok, today)), "w", encoding="utf-8") as f:
                f.write("# Lane %s (ABC-8): a refused topic\n\nWorktree %s, branch none.\n" % (tok, wt))
        scfg = os.path.join(tmp, "stack.json")
        with open(scfg, "w", encoding="utf-8") as f:
            json.dump({"base_branch": "trunk"}, f)
        senv = dict(os.environ, BRIEF_GEN_CONFIG=scfg, EVIDENCE_ROOT=ev)

        def sgen(*argv):
            return subprocess.run([sys.executable, SCRIPT] + list(argv), capture_output=True, text=True, env=senv, timeout=60)

        def lane_line(path):
            body = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
            return body.split("## The lane", 1)[1].split("##", 1)[0] if "## The lane" in body else ""

        def stacked():
            warn, given = os.path.join(tmp, "stk-warn.md"), os.path.join(tmp, "stk-base.md")
            r = sgen("review", "stk", "--out", warn)
            g = sgen("review", "stk", "--base", " lane-a ", "--out", given)
            lw, lg = lane_line(warn), lane_line(given)
            return (r.returncode == 0 and "base %s (merge-base with trunk; branch lane-a at %s is below the tip" % (root[:9], a1[:9]) in lw
                    and "--base %s" % a1[:9] in lw and "A1 the parent lane" in lw and "warning" in r.stderr
                    and g.returncode == 0 and "base %s (given by --base" % a1[:9] in lg and "B1 the stacked lane" in lg
                    and "A1 the parent lane" not in lg and "warning" not in g.stderr)
        check("stacked lane: with no --base the parent branch is named with the --base to pass; --base lists only the "
              "lane's own commits", stacked)

        def plain_and_merged_in():
            out, out2 = os.path.join(tmp, "pln.md"), os.path.join(tmp, "pln-merged.md")
            r = sgen("review", "pln", "--out", out)
            git(repo, "branch", "lane-d", "trunk")
            wtd = os.path.join(tmp, "stack-wtd").replace("\\", "/")
            git(repo, "worktree", "add", "-q", wtd, "lane-d")
            git(wtd, "commit", "-q", "--allow-empty", "-m", "D1 a sibling")
            git(wtc, "merge", "-q", "--no-ff", "-m", "merge lane-d", "lane-d")
            r2 = sgen("review", "pln", "--out", out2)
            l1, l2 = lane_line(out), lane_line(out2)
            return (r.returncode == 0 and "base %s (merge-base with trunk)" % root[:9] in l1 and "warning" not in r.stderr
                    and r2.returncode == 0 and "below the tip" not in l2 and "warning" not in r2.stderr and "D1 a sibling" in l2)
        check("a lane cut from the base branch, and one that merged a sibling in, get no stacked warning", plain_and_merged_in)

        def base_refused():
            out = os.path.join(tmp, "stk-bad.md")
            cases = [(sgen("review", "stk", "--base", "lane-c", "--out", out), 1, "is not below the tip"),
                     (sgen("review", "stk", "--base", "nosuchref", "--out", out), 1, "is not a commit"),
                     (sgen("review", "stk", "--base", head, "--out", out), 1, "is the tip itself"),
                     (sgen("review", "stk", "--base", "  ", "--out", out), 2, "--base is blank"),
                     (sgen("review", "stk", "--base", "", "--out", out), 2, "--base is blank"),
                     (sgen("review", "gone", "--base", a1, "--out", out), 1, "is not a directory"),
                     (sgen("review", "nogit", "--base", a1, "--out", out), 1, "needs a git worktree"),
                     (sgen("notes", "stk", "--review", review, "--base", a1, "--out", out), 2, "review kind's"),
                     (sgen("review", "stk", "--no-git", "--base", a1, "--out", out), 2, "--no-git")]
            return all(r.returncode == code and why in r.stderr for r, code, why in cases) and not os.path.exists(out)
        check("--base is refused when not strictly below the tip, unknown, the tip, blank, on another kind or with "
              "--no-git", base_refused)

        def long_lists_bounded():
            stream = "".join("commit refs/heads/lane-b\ncommitter f <f@example.com> %d +0000\ndata %d\n%s\n%s"
                             % (1767000000 + i, len("B%d bulk" % (i + 3)), "B%d bulk" % (i + 3), "from %s\n" % head if i == 0 else "")
                             for i in range(105))
            subprocess.run(["git", "-C", repo, "fast-import", "--quiet"], input=stream.encode(), check=True, timeout=60)  # bytes: no CRLF
            refused = sgen("review", "stk", "--base", a1, "--out", os.path.join(tmp, "stk-long-base.md"))
            out = os.path.join(tmp, "stk-long.md")
            plain = sgen("review", "stk", "--attack", "1. a point", "--purpose", "quality, the pins", "--out", out)
            return (refused.returncode == 1 and "107 commits above it, more than a lane's 40" in refused.stderr
                    and not os.path.exists(os.path.join(tmp, "stk-long-base.md")) and plain.returncode == 0
                    and "(8 more commits above the base: git log --oneline %s.." % root[:9] in lane_line(out)
                    and ", 0 placeholders" in plain.stdout)
        check("above the base: more than 40 refused under --base, more than 100 cut with a pointer that is no "
              "placeholder", long_lists_bounded)
    finally:
        def unlock(fn, path, _exc):  # git writes read-only pack files
            try:
                os.chmod(path, 0o700)
                fn(path)
            except OSError:
                pass
        shutil.rmtree(tmp, onerror=unlock)
    print("%d of %d OK" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
