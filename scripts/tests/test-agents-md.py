"""Pins for scripts/agents-md.py: render and check of the kit's marked sections (F03 acceptance 3, the marker round
trip), the learnings section kept as the target's own, line endings and byte order mark kept, malformed markers
refused before any write, and the project template's own AGENTS.md parsing into its three sections.

    python scripts/tests/test-agents-md.py
    AGENTS_MD_PY=<path> python scripts/tests/test-agents-md.py    # against a staged copy

Every file is a temp file; nothing outside the temp folder is written.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.environ.get("AGENTS_MD_PY") or os.path.join(ROOT, "scripts", "agents-md.py")
TEMPLATE = os.path.join(ROOT, "project-template", "AGENTS.md")

SOURCE = """# Kit rules

<!-- cb:rules -->
## Voice
- No em-dashes.
<!-- /cb:rules -->

<!-- cb:pipeline -->
## Pipeline
One chain.
<!-- /cb:pipeline -->

<!-- cb:learnings -->
## Learnings
<!-- /cb:learnings -->
"""


def main():
    tmp = tempfile.mkdtemp(prefix="agents-md-")
    results = []

    def check(name, fn):
        try:
            ok, note = bool(fn()), ""
        except Exception as e:
            ok, note = False, " (%s: %s)" % (type(e).__name__, str(e)[:90])
        results.append(ok)
        print(("OK   " if ok else "FAIL ") + name + note)

    def put(name, text, raw=False):
        path = os.path.join(tmp, name)
        with open(path, "wb" if raw else "w", **({} if raw else {"encoding": "utf-8", "newline": ""})) as fh:
            fh.write(text)
        return path

    def get(path, raw=False):
        with open(path, "rb" if raw else "r", **({} if raw else {"encoding": "utf-8", "newline": ""})) as fh:
            return fh.read()

    def run(*args):
        return subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)

    try:
        src = put("source.md", SOURCE)

        def creates_then_in_step():
            dst = os.path.join(tmp, "new-target.md")
            r = run("render", src, dst)
            c = run("check", src, dst)
            text = get(dst)
            return (r.returncode == 0 and c.returncode == 0 and "in step" in c.stdout
                    and text.startswith("<!-- cb:rules -->\n## Voice") and "<!-- /cb:learnings -->" in text
                    and "# Kit rules" not in text)
        check("a missing target is created from the source's sections alone, and then checks in step", creates_then_in_step)

        def round_trip_keeps_hand_edits():
            dst = os.path.join(tmp, "round.md")
            run("render", src, dst)
            edited = "# Team header written by hand\n\n" + get(dst) + "\n## Team notes\nKeep the linter on.\n"
            put("round.md", edited)
            put("source2.md", SOURCE.replace("- No em-dashes.", "- No em-dashes.\n- Direct tone."))
            r = run("render", os.path.join(tmp, "source2.md"), dst)
            text = get(dst)
            return (r.returncode == 0 and "rules replaced" in r.stdout and text.startswith("# Team header written by hand\n")
                    and text.rstrip("\n").endswith("## Team notes\nKeep the linter on.") and "- Direct tone." in text
                    and text.count("<!-- cb:rules -->") == 1)
        check("render, hand-edit outside the markers, render again: the hand edits are intact and the section moved",
              round_trip_keeps_hand_edits)

        def learnings_are_the_targets():
            dst = put("learn.md", "<!-- cb:learnings -->\n## Learnings\n- 2026-09-24 a lesson of this repo\n"
                                  "<!-- /cb:learnings -->\n")
            r = run("render", src, dst)
            text = get(dst)
            bare = put("bare.md", "# only a header\n")
            r2 = run("render", src, bare)
            c = run("check", src, dst)
            return (r.returncode == 0 and "- 2026-09-24 a lesson of this repo" in text and "rules added" in r.stdout
                    and "learnings" not in r.stdout and r2.returncode == 0 and "learnings added" in r2.stdout
                    and c.returncode == 0)
        check("learnings are never replaced (the target's lessons survive) but are added when missing; check ignores "
              "them", learnings_are_the_targets)

        def crlf_and_bom_kept():
            dst = put("crlf.md", b"\xef\xbb\xbf# mine\r\n\r\n<!-- cb:rules -->\r\nold\r\n<!-- /cb:rules -->\r\n", raw=True)
            r = run("render", src, dst)
            raw = get(dst, raw=True)
            return (r.returncode == 0 and raw.startswith(b"\xef\xbb\xbf# mine\r\n") and b"- No em-dashes.\r\n" in raw
                    and b"\n" not in raw.replace(b"\r\n", b""))
        check("a CRLF target with a byte order mark keeps both", crlf_and_bom_kept)

        def mixed_endings_kept_per_line():
            # review agmd r1 finding 2: one CRLF anywhere turned every LF line of the owner's into CRLF
            head = b"# mine\r\nline kept by the team\n\nsecond team line\n"
            dst = put("mixed.md", head + b"<!-- cb:rules -->\r\nold\r\n<!-- /cb:rules -->\r\n", raw=True)
            r = run("render", src, dst)
            raw = get(dst, raw=True)
            tail = put("tail.md", b"# mine\r\nlast line with no ending", raw=True)
            r2 = run("render", src, tail)
            raw2 = get(tail, raw=True)
            # review agmd r2 note 1: a last line ended by a CR alone keeps its one CR and gains only the LF
            lone = put("lone.md", b"# mine\r\nlast line\r", raw=True)
            r3 = run("render", src, lone)
            return (r.returncode == 0 and raw.startswith(head) and b"<!-- cb:rules -->\r\n## Voice\r\n- No em-dashes.\r\n" in raw
                    and r2.returncode == 0 and raw2.startswith(b"# mine\r\nlast line with no ending\r\n\r\n<!-- cb:rules -->\r\n")
                    and r3.returncode == 0 and get(lone, raw=True).startswith(b"# mine\r\nlast line\r\n\r\n<!-- cb:rules -->\r\n"))
        check("the owner's lines keep their own endings in a mixed file, the written lines take the majority ending, "
              "and a last line with no ending gets the target's", mixed_endings_kept_per_line)

        def empty_target_no_lead():
            # review agmd r1 finding 4: a 0-byte target got a blank line before its first marker
            empty = put("zero.md", b"", raw=True)
            blank = put("blanks.md", b"\n\n", raw=True)
            r, r2 = run("render", src, empty), run("render", src, blank)
            return (r.returncode == 0 and get(empty).startswith("<!-- cb:rules -->\n")
                    and r2.returncode == 0 and get(blank).startswith("<!-- cb:rules -->\n"))
        check("an empty or blank-only target starts with its first marker, no blank line before it", empty_target_no_lead)

        def malformed_refused():
            cases = ["<!-- cb:rules -->\nx\n", "x\n<!-- /cb:rules -->\n",
                     "<!-- cb:rules -->\n<!-- cb:pipeline -->\n<!-- /cb:pipeline -->\n<!-- /cb:rules -->\n",
                     "<!-- cb:rules -->\n<!-- /cb:rules -->\n<!-- cb:rules -->\n<!-- /cb:rules -->\n",
                     "<!-- cb:rules -->\n<!-- /cb:pipeline -->\n"]
            ok = []
            for i, bad in enumerate(cases):
                dst = put("bad%d.md" % i, bad)
                r = run("render", src, dst)
                ok.append(r.returncode == 2 and "do not pair" in r.stderr and get(dst) == bad and "Traceback" not in r.stderr)
                badsrc = put("badsrc%d.md" % i, bad)
                good = put("good%d.md" % i, "# mine\n")
                r2 = run("render", badsrc, good)
                ok.append(r2.returncode == 2 and get(good) == "# mine\n")
            return all(ok)
        check("an unclosed, stray, nested, doubled or crossed marker in the source or the target is refused, nothing "
              "written", malformed_refused)

        def quoted_marker_is_prose():
            dst = put("quoted.md", "Sections sit between `<!-- cb:<id> -->` markers.\n  <!-- cb:rules --> indented\n")
            r = run("render", src, dst)
            text = get(dst)
            return r.returncode == 0 and text.startswith("Sections sit between `<!-- cb:<id> -->` markers.\n") \
                and text.count("<!-- cb:rules -->\n") == 1
        check("a marker quoted in prose or not alone on its line is text, not a marker", quoted_marker_is_prose)

        def check_names_the_difference():
            dst = put("drift.md", "<!-- cb:rules -->\nold\n<!-- /cb:rules -->\n")
            c = run("check", src, dst)
            same = run("check", src, dst)
            # review agmd r1 finding 3: a learnings that differs is in step (learnings_are_the_targets), a missing
            # one is named, since render would add it
            return c.returncode == 1 and "rules differs" in c.stdout and "pipeline missing" in c.stdout \
                and "learnings missing" in c.stdout \
                and get(dst) == "<!-- cb:rules -->\nold\n<!-- /cb:rules -->\n" and same.returncode == 1
        check("check exits 1 naming each section that differs or is missing, a missing learnings included, and writes "
              "nothing", check_names_the_difference)

        def dry_run_and_usage():
            dst = put("dry.md", "# mine\n")
            d = run("render", src, dst, "--dry-run")
            bad = [run("render", " ", dst), run("check", src, dst, "--dry-run"), run("render", src, tmp),
                   run("render", os.path.join(tmp, "no-such.md"), dst), run("render", put("empty.md", ""), dst),
                   run("check", put("plain.md", "# no sections\n"), dst)]
            nothing = run("render", src, os.path.join(tmp, "new-target.md"))
            return (d.returncode == 0 and "would get" in d.stdout and get(dst) == "# mine\n"
                    and [b.returncode for b in bad] == [2, 2, 2, 2, 2, 2] and all("Traceback" not in b.stderr for b in bad)
                    and nothing.returncode == 0 and "nothing to change" in nothing.stdout)
        check("--dry-run writes nothing; a blank path, --dry-run on check, a folder target, a missing source and a "
              "source with no sections are exit 2; a target in step is left alone", dry_run_and_usage)

        def tilde_paths_expand():
            # review agmc r1 finding 1: PowerShell passes ~/... to python literally
            home = os.path.join(tmp, "home")
            os.makedirs(os.path.join(home, ".pi"), exist_ok=True)
            shutil.copy(src, os.path.join(home, "rules.md"))
            env = dict(os.environ, HOME=home, USERPROFILE=home)
            r = subprocess.run([sys.executable, SCRIPT, "render", "~/rules.md", "~/.pi/AGENTS.md"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
            made = os.path.join(home, ".pi", "AGENTS.md")
            return r.returncode == 0 and os.path.isfile(made) and get(made).startswith("<!-- cb:rules -->\n")
        check("a source and a target written as ~/... resolve to the home folder in any shell", tilde_paths_expand)

        def template_parses():
            spec_path = SCRIPT
            import importlib.machinery
            import importlib.util
            loader = importlib.machinery.SourceFileLoader("agents_md_under_test", spec_path)
            spec = importlib.util.spec_from_loader("agents_md_under_test", loader)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            text, _, _ = mod.read(TEMPLATE)
            return sorted(mod.sections(text, "template")) == ["learnings", "pipeline", "rules"]
        check("the project template's AGENTS.md parses into rules, pipeline and learnings", template_parses)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of %d OK" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
