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
            return ("`bash scripts/run-own-tests.sh /c/src/myapp-demo demo-fix1 '<<the test selection" in checks
                    and "END YOUR TURN with /c/src/myapp-demo/build/runs/demo-fix1.done as your last line" in checks
                    and "`bash scripts/precheck.sh /c/src/myapp-demo` on the new tip" in checks
                    and "## Change, one commit, subject ending `[skip ci]`" in body
                    and "Laws: /ev/laws.md." in body and "the precheck line" in report_of(body))
        check("configured commands, done path, precheck, suffix and laws are filled in", configured_rendered)

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
                     "broken.json": ("{\"laws\": ,}", "cannot be read")}
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of %d OK" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
