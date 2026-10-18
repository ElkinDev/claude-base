"""Board isolation: which kit readers, run for a second project, touch the first board of the machine.

    python scripts/tests/test-board-isolation.py              the pins, then every run, one line each, then the count
    python scripts/tests/test-board-isolation.py --pins-only  the pins alone
    python scripts/tests/test-board-isolation.py --kit <root> --keep

The kit seats one board per machine: one register, one briefs folder, one landings file, one state sheet. A second
project needs its own, and the candidate way to give it one is an env block in the project's settings file. This
harness measures what that block does not cover. It builds a sandbox: a home holding a decoy machine board, a first
project with its own evidence root, and a second project P (a git repository) with its evidence root E, a worktree
root and one linked worktree W. Every file of the two decoy boards carries a marker found nowhere else. Then it runs
every command wired in claude/settings.json, with a payload of its event, every tool the orchestrator seat names and
every tool scripts/ledger-nightly.ps1 starts outside any session, in six situations: P as orchestrator and P as lane
and W as lane with the block; W as lane without it, which is what a worktree pane gets today, since the settings file
that carries the block is untracked; and P as orchestrator and as lane without it, the project before an installer
wrote one. The run lists are read from the code: the settings file, the seat text, the ledger script's own lines and
a scan of every kit script for a board default token, each held by a pin. A name one of them holds with no call
written here, and a script the scan finds that is neither run nor listed as not run, is an error row.

A leak is an access. Every python child runs under a file trace (a sitecustomize.py of this file's own on its
PYTHONPATH, an audit hook that records each open, os.listdir, os.scandir and glob.glob with its path), and a run that
opens, lists or globs a decoy leaks, named by the files it reached, whether or not anything shows in its output. The
visible rules stand beside the trace: a decoy file changed, a decoy's marker or its path in the run's output (a reader
such as the resume line prints the path and never the text, so the path counts as the marker), a decoy's marker in a
file the run wrote inside the project, a file written outside P, E, the worktree root and the temp folder. A run that
fails some other way is an error, never clean. A reader the trace cannot follow (a PowerShell script, a shell script)
is judged by the visible rules alone and printed as untraced beside that verdict: it is counted apart and never clean.
The trace follows children: every spawn is a line of it, a python child must write a start line of its own, any other
child must be on the short allowed list, and a child handed a path under a decoy root is a leak naming that path. A
run with a child nothing followed is untraced too.

Three stores under the home are keyed on the session, on its project or on the repository and are shared by design:
the in-flight state of a session, its checkpoints and the worklog of a repository. A run that only writes there is
listed with the kind S, printed as a store and kept out of the leak count.

No child ever sees the real home: the home variables point into the sandbox, each interpreter is asked which home it
resolves before a reader runs under it, and a reader whose home falls outside the sandbox is refused. PATH is built
from scratch (python, git and its shell, the system folders), so no model binary is on it, the proxy variables name a
dead port, and a stub stands in for the notifier some hooks call. The harness refuses to start when a record.json sits
in a folder above the sandbox root: the record tool walks up from the working folder, and only the decoy inside the
sandbox may answer it.

EXPECTED_LEAKS documents the leaks of today, each with its kind, every decoy its reader reaches in that situation and
its reason. The exit code is 1 on a leak that is not listed, on a listed reader that reaches a decoy its entries do
not name, on a listed leak, a listed file or a listed error that no longer happens, on a listed run that was never
made, on an error that is not listed, on a leak row whose reader's exit code is off its list (EXPECTED_ENDS, held
both ways), on any refused run and on a failed pin; it is 2 when the harness refuses to
start.
"""
import argparse
import concurrent.futures
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.normpath(os.path.join(HERE, "..", ".."))
RUN_TIMEOUT = 40
SESSION = "0a1b2c3d-1111-2222-3333-444455556666"
AGENT = "abcdef1234567890"
WORD = "boardprobe"
LANE = "lane1"
BRIEF = "beta-topic-2026-10-12.md"
TRACEBACK = "Traceback (most recent call last)"

# name, the folder under test, the role the launcher exports, whether the candidate block is in the environment
SITUATIONS = (
    ("P orchestrator, block", "project", "orchestrator", True),
    ("P lane, block", "project", "lane", True),
    ("W lane, block", "worktree", "lane", True),
    ("W lane, no block", "worktree", "lane", False),
    ("P orchestrator, no block", "project", "orchestrator", False),
    ("P lane, no block", "project", "lane", False),
)
W_BARE, P_BARE, PL_BARE = ("W lane, no block",), ("P orchestrator, no block",), ("P lane, no block",)
NO_BLOCK = W_BARE + P_BARE + PL_BARE
P_NO_BLOCK, LANE_NO_BLOCK = P_BARE + PL_BARE, W_BARE + PL_BARE
EVERY = tuple(name for name, _, _, _ in SITUATIONS)
W_ONLY = ("W lane, block", "W lane, no block")
IN_P = tuple(name for name, where, _, _ in SITUATIONS if where == "project")

# The kind of a listed run. A, B and C are the three a leak of a session can be, D is a reader no settings block can
# reach, and S is not a leak: an S run is printed and listed, and kept out of the leak count.
LACKS, NO_SEAM, NO_PANE_BLOCK, OUTSIDE, STORE = "A", "B", "C", "D", "S"
KINDS = (
    (LACKS, "a variable the candidate block lacks"),
    (NO_SEAM, "no environment seam at all"),
    (NO_PANE_BLOCK, "no block in the environment: a worktree pane, or the project before an installer wrote one"),
    (OUTSIDE, "started outside a session, so no settings block reaches it"),
    (STORE, "a store keyed on the session, on its project or on the repository, shared by design"),
)
LEAK_KINDS = (LACKS, NO_SEAM, NO_PANE_BLOCK, OUTSIDE)

# The leaks of today: (reader, situations, kind, the files that leak, reason). A file is a path under the sandbox root
# as the run line prints it (the decoy changed, the file of the marker printed, the path created or named), with * for
# a part that changes from run to run. A listed reader that leaks a file its entries do not name fails the run, and
# so does a listed file that no longer leaks.
CHECKPOINTS, WORKLOG = "home/.claude/checkpoints/sandbox-project/", "home/.claude/worklog/beta-*/sessions.jsonl"
HOME_RULINGS, HOME_LANDINGS, HOME_LAW, HOME_PULSE = ("home/.claude/rulings.md", "home/.claude/landings.md",
                                                     "home/.claude/law.md", "home/.claude/pulse.md")
HOME_ASKS, FIRST_EV = "home/.claude/decisions/owner-decisions-2026-10-12.md", "repo/evidence"
HOME_RESUME = "home/.claude/briefs/orchestrator-resume-2026-10-12.md"
HOME_BRIEFS, HOME_DECISIONS, HOME_CONFIG = "home/.claude/briefs", "home/.claude/decisions", "home/.claude/lane-state.json"
HOME_LOG, KIT_LOG, LEDGER_CONFIG = "home/.claude/ledger/nightly.log", "kit/ledger/nightly.log", "kit/scripts/ledger-config.json"
FIRST_LANDINGS, FIRST_QUEUE, FIRST_HOLDS = "repo/evidence/landings.md", "repo/evidence/queue.md", "repo/evidence/train-holds.txt"
FIRST_BRIEFS, FIRST_LANES, FIRST_REVIEWS = "repo/evidence/briefs", "repo/evidence/lanes", "repo/evidence/reviews"
FIRST_LANE, FIRST_REVIEW = FIRST_LANES + "/alpha-topic-2026-10-12.md", FIRST_REVIEWS + "/alpha-topic-review-2026-10-12.md"
FIRST_REPORTS = "repo/evidence/ledger/owner-reports.md"
# what a session start opens with no block; what the machine sheet is rendered from (the home config names the first
# board's lanes, briefs and reports); what the train readers open of the evidence folder they take
START = (HOME_DECISIONS, HOME_ASKS, HOME_LOG, HOME_PULSE, HOME_RULINGS)
SHEET = (HOME_LANDINGS, HOME_CONFIG, HOME_LAW, HOME_RULINGS, FIRST_BRIEFS, FIRST_LANES, FIRST_LANE, FIRST_REPORTS)
TRAIN = (FIRST_LANDINGS, FIRST_LANES, FIRST_LANE, FIRST_QUEUE, FIRST_REVIEWS, FIRST_REVIEW)
EXPECTED_LEAKS = (
    # A: a seam exists and the candidate block does not name it, so the block changes nothing: all six situations
    ("PreToolUse[Agent] guard-delegate.py", EVERY, LACKS, ("home/.claude/hooks/brief-launches.log",),
     "GUARD_LOG: every graded launch is appended to brief-launches.log beside the hook (guard-delegate.py:53,106)"),
    ("tool record.py", EVERY, LACKS, ("repo/evidence/rulings.md", "repo/record.json"),
     "RECORD_CONFIG: a named target comes from the first record.json found walking up from the working folder "
     "(record.py:189,195-199). It rests on a fixture: a record.json in an ancestor the two projects share, here the "
     "first board's, so the row lands in its register; a record.json in P itself would win the walk"),
    ("Stop inflight.py", EVERY, LACKS, ("home/.claude/hooks/inflight.log",),
     "CLAUDE_INFLIGHT_LOG: the log of every session sits beside the hook (inflight.py:25-27,37,41)"),
    ("PreToolUse[AskUserQuestion] ask-inline.py", EVERY, LACKS, ("home/.claude/hooks/ask-inline.log",),
     "ASK_INLINE_LOG: every denied question is logged beside the hook (ask-inline.py:23)"),
    ("PostToolUse[*] alarm-big-result.py", EVERY, LACKS, ("home/.claude/ledger/tool-sizes.csv",),
     "CLAUDE_LEDGER_DIR: every tool result is a row of ~/.claude/ledger/tool-sizes.csv (alarm-big-result.py:34-35)"),
    # C: the block covers it wherever it is in the environment, and here it is not
    ("SessionStart[startup|resume|clear|fork] compact-recover.py --rulings", NO_BLOCK, NO_PANE_BLOCK, START,
     "prints the machine register, the machine's open asks and its pulse (compact-recover.py:197,255,315 with the "
     "defaults at :148,232 and pulse.py:53-54), the pulse opening the machine's ledger log (pulse.py:55-56); with no "
     "board root every folder is on the board (:134-135)"),
    ("SessionStart[compact] compact-recover.py", NO_BLOCK, NO_PANE_BLOCK, START + SHEET,
     "as the session start, and it renders the machine sheet over ~/.claude/law.md and prints it (compact-recover.py:"
     "365-373, lane-state.py:129) from the home config, which names the first board's lanes, briefs and reports"),
    ("SessionStart[startup|resume|clear|fork] compact-recover.py --rulings", P_BARE, NO_PANE_BLOCK, (HOME_BRIEFS, HOME_RESUME),
     "a seat's launcher exports the machine's briefs folder as CLAUDE_BRIEFS_DIR and, with no block, nothing replaces "
     "it: the start lists that folder and names the machine's resume brief (compact-recover.py:462-469)"),
    ("SessionStart[compact] compact-recover.py", P_BARE, NO_PANE_BLOCK, (HOME_BRIEFS, HOME_RESUME),
     "as the session start of a seat: it names the machine's resume brief (compact-recover.py:462-469)"),
    ("Stop landing.py", LANE_NO_BLOCK, NO_PANE_BLOCK, (HOME_LANDINGS,),
     "role lane and a NOTES.md append a row to ~/.claude/landings.md each turn (landing.py:137,178-185, default :32-33)"),
    ("SubagentStop[*] landing.py", NO_BLOCK, NO_PANE_BLOCK, (HOME_LANDINGS,),
     "a typed subagent's stop is a row of ~/.claude/landings.md (landing.py:137)"),
    ("TaskCompleted landing.py", NO_BLOCK, NO_PANE_BLOCK, (HOME_LANDINGS,),
     "a completed task is a row of ~/.claude/landings.md (landing.py:137)"),
    ("tool row.sh", NO_BLOCK, NO_PANE_BLOCK, (HOME_RULINGS,), "the row goes to ~/.claude/rulings.md (row.sh:16)"),
    ("tool lane-state.py", NO_BLOCK, NO_PANE_BLOCK, SHEET,
     "reads ~/.claude/lane-state.json and writes ~/.claude/law.md (lane-state.py:47-48,125,129), opening on the way "
     "the machine's register and landings and the lanes, briefs and reports the home config names, the first board's"),
    ("tool pulse.py", NO_BLOCK, NO_PANE_BLOCK, (HOME_LOG, HOME_PULSE),
     "reads ~/.claude/pulse.md and the machine's ledger log (pulse.py:53-56)"),
    ("tool brief-gen.py", NO_BLOCK, NO_PANE_BLOCK, ("kit/scripts/brief-gen.json", FIRST_EV, FIRST_LANES, FIRST_LANE),
     "it rests on a fixture: a brief-gen.json beside the script, owned by the first board (brief-gen.py:112-113), "
     "which names that board's lanes folder; the lane report it then opens is that board's"),
    ("tool evidence-path.py", NO_BLOCK, NO_PANE_BLOCK, (FIRST_EV,),
     "it rests on a fixture: a profile left at the template's line, {repo_parent}/evidence, the first board's folder "
     "(project-template/CLAUDE.project.md:38). That profile line is a seam of its own: it is tracked, so it reaches a "
     "worktree that has no block, and with no EVIDENCE_ROOT it answers (evidence-path.py:105-108)"),
    ("tool train-due.py", P_NO_BLOCK, NO_PANE_BLOCK, (FIRST_LANDINGS, FIRST_QUEUE, FIRST_REVIEWS, FIRST_REVIEW, FIRST_HOLDS),
     "with no EVIDENCE_ROOT the evidence folder is the one beside the checkout (train-wait.py:88-94), which beside P "
     "is the first board's: it opens that board's landings, queue, reviews and holds (train-due.py:236-250) and prints "
     "lane lines and counts worked out from them, never a text or a path, so only the trace shows it"),
    ("tool train-wait.py", P_NO_BLOCK, NO_PANE_BLOCK, TRAIN,
     "the same default (train-wait.py:88-94): the first board's landings, queue, reviews and lane reports, opened and "
     "shown as counts alone"),
    ("tool reports-check.py", NO_BLOCK, NO_PANE_BLOCK, (HOME_LANDINGS, HOME_CONFIG, FIRST_REPORTS),
     "with no CLAUDE_LANE_STATE_CONFIG its config is ~/.claude/lane-state.json (reports-check.py:82-83): it opens the "
     "reports ledger that config names, the first board's, and the machine's landings. No file a pin reads starts "
     "it: the code scan found it"),
    # D: the ledger script starts it, so the settings block of a project is never in its environment
    ("nightly ledger-day.py", EVERY, OUTSIDE,
     (HOME_CONFIG, "kit/ledger/daily/*.md", "kit/ledger/ledger.md", "kit/ledger/quality.md", LEDGER_CONFIG, FIRST_LANDINGS,
      "repo/alpha"),
     "one config beside the script names one repository (ledger-day.py:99,160; started at ledger-nightly.ps1:464): it "
     "opens the landings of the evidence folder beside that repository (:1079-1086), loads the installed state tool "
     "with the home config (quality.py:69-71,221-233) and writes the one ledger of the machine (:155-157). It rests "
     "on a fixture: the config names the first project"),
    ("nightly ledger-compare.py", EVERY, OUTSIDE, (LEDGER_CONFIG,),
     "reads the same one config (ledger-compare.py:62; started at ledger-nightly.ps1:486). It is measured that far "
     "only: the sandbox ledger holds no frozen baseline, and the tool ends with exit 1 on the missing file"),
    ("nightly train-wait.py", IN_P, OUTSIDE, TRAIN,
     "with no EVIDENCE_ROOT the evidence folder is the one beside the working folder's checkout (train-wait.py:88-94; "
     "started at ledger-nightly.ps1:496, which sets no working folder): from P that is the first board's, from a "
     "worktree it is a folder that does not exist"),
    ("nightly rework-shape.py", EVERY, OUTSIDE, (HOME_CONFIG, HOME_RULINGS),
     "the home config and the machine register (rework-shape.py:68-70,84; started at ledger-nightly.ps1:577)"),
    ("nightly tooling-block-rate.py", EVERY, OUTSIDE, (FIRST_REVIEWS,),
     "lists the reviews of the root one machine variable names (ledger-nightly.ps1:595-602). It rests on a fixture: "
     "TOOLING_BLOCK_ROOT set to the first board"),
    ("nightly pulse.py", EVERY, OUTSIDE, (HOME_PULSE, KIT_LOG),
     "reads ~/.claude/pulse.md whatever the project (pulse.py:53-54; started at ledger-nightly.ps1:653) and the run's "
     "own log, handed over as PULSE_LOG (:652)"),
    ("nightly pulse-escalate.py", EVERY, OUTSIDE,
     (HOME_DECISIONS, HOME_ASKS, "home/.claude/decisions/owner-decisions-2026-10-13.md", HOME_RULINGS, KIT_LOG),
     "reads ~/.claude/rulings.md, the asks of ~/.claude/decisions and the run's log, and writes the escalation row "
     "into ~/.claude/decisions (pulse-escalate.py:46-54; started at ledger-nightly.ps1:666)"),
    ("nightly evidence-retention.py", EVERY, OUTSIDE,
     (FIRST_EV, FIRST_BRIEFS, "repo/evidence/drafts", FIRST_LANES, "repo/evidence/ledger",
      "repo/evidence/ledger/deleted/.retention.lock", FIRST_REVIEWS),
     "walks the one root the config names and keeps its lock under it (ledger-nightly.ps1:722-737, evidence-"
     "retention.py:483). It rests on a fixture: the config names the first board's root"),
    ("nightly worktree-sweep.py", EVERY, OUTSIDE, ("repo/alpha",),
     "its git child works on the one repository the config names (worktree-sweep.py:122; started at ledger-"
     "nightly.ps1:696-706): the spawn is what shows it, the tool itself opens no decoy. It rests on a fixture: the "
     "config names the first project, which has no linked worktree to sweep"),
    # S: a store under the home that is keyed on the session, on its project or on the repository, shared by design
    ("Stop inflight.py", EVERY, STORE, ("home/.claude/hooks/inflight-state/*.json",),
     "the state of the session, one file per session id (inflight.py:7,95, as review round 2 cites them)"),
    ("UserPromptSubmit prompt-log.py", EVERY, STORE, (CHECKPOINTS + SESSION[:8] + "-prompts.md",),
     "the prompt log of the session, under ~/.claude/checkpoints/<session project> (prompt-log.py:39-43)"),
    ("PreCompact precompact-checkpoint.py", EVERY, STORE, (CHECKPOINTS + "*-auto.md",),
     "the checkpoint of the session, in the same folder (precompact-checkpoint.py:285,292)"),
    ("PostCompact postcompact-persist.py", EVERY, STORE, (CHECKPOINTS + "*-summary.md",),
     "the compaction summary of the session, in the same folder (postcompact-persist.py:23,30)"),
    ("PreCompact worklog-snapshot.ps1", W_ONLY, STORE, (WORKLOG,),
     "the worklog of the repository, ~/.claude/worklog/<repo id> (worklog-lib.ps1:25,30); it writes on a branch that "
     "has a task file, so not from P on its base branch"),
    ("SessionEnd worklog-snapshot.ps1", W_ONLY, STORE, (WORKLOG,), "as PreCompact worklog-snapshot.ps1"),
    ("Stop worklog-snapshot.ps1", W_ONLY, STORE, (WORKLOG,), "as PreCompact worklog-snapshot.ps1"),
)

# Runs that end as an error today, with the reason: (reader, situations, reason). A listed error that stops happening
# fails the run, as a listed leak does.
EXPECTED_ERRORS = (
    ("tool train-due.py", W_BARE,
     "with no EVIDENCE_ROOT the evidence folder is <the checkout's parent>/evidence (train-wait.py:94), which under a "
     "worktree root does not exist, so it exits 2 before reading (train-due.py:237-240)"),
    ("tool reports-check.py", tuple(name for name in EVERY if name not in NO_BLOCK),
     "with the block its config is the second board's, which names no reports ledger, and the one it then looks for "
     "at the evidence root does not exist there: it exits 1 (reports-check.py:416) before it reads a landing, so the "
     "three block columns are measured to that failure only"),
)

# The readers that end non-zero on a leak row, with the exit code: (reader, situations, exit code, reason). Such a
# row is measured only as far as its reader got. A listed code that changes fails the run, and so does a reader that
# fails on a leak row with no entry here.
EXPECTED_ENDS = (
    ("tool brief-gen.py", NO_BLOCK, 1,
     "the first board's lanes folder holds no report titled for the lane, so it stops after opening that folder and "
     "is measured to that first failure only"),
    ("nightly ledger-compare.py", EVERY, 1,
     "the sandbox ledger holds no frozen baseline, so it stops right after the config and is measured to that first "
     "failure only"),
)


def candidate_block(ev, parent):
    """The candidate env block: every board seam the design names, pointed at the second project's roots."""
    return {
        "CLAUDE_BRIEFS_DIR": ev + "/briefs", "CLAUDE_BRIEFS_ROOT": ev, "CLAUDE_RULINGS_FILE": ev + "/rulings.md",
        "CLAUDE_DECISIONS_GLOB": ev + "/drafts/owner-decisions-*.md", "CLAUDE_LANDINGS_FILE": ev + "/landings.md",
        "CLAUDE_LANE_STATE_SHEET": ev + "/law.md", "PULSE_REGISTER": ev + "/pulse.md",
        "CLAUDE_BOARD_ROOT": parent, "CLAUDE_BOARD_PREFIXES": "beta;evidence-beta;worktree-beta",
        "EVIDENCE_ROOT": ev, "CLAUDE_LANE_STATE_CONFIG": ev + "/lane-state.json",
        "BRIEF_GEN_CONFIG": ev + "/brief-gen.json", "PULSE_ROOT": ev,
    }


def mark(label):
    return "kbm-%s-%s" % (label, hashlib.sha1(label.encode("utf-8")).hexdigest()[:6])


def posix(path):
    return str(path).replace("\\", "/")


def norm(path):
    return os.path.normcase(os.path.abspath(path)).replace("\\", "/").rstrip("/")


def path_pattern(path):
    """The path as an output shows it: lower case, forward slashes, the drive as c: or as /c, and no longer name."""
    text = norm(path)
    if len(text) > 1 and text[1] == ":":
        head = "(?:%s:|/%s)" % (text[0], text[0])
        return re.compile(head + re.escape(text[2:]) + r"(?![\w.-])")
    return re.compile(re.escape(text) + r"(?![\w.-])")


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def remove_tree(path):
    def retry(func, target, _info):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=retry)


class Toolbox:
    """The interpreters a reader needs and the PATH the children get, with nothing else on it."""

    def __init__(self):
        system = os.environ["SystemRoot"]
        self.python = sys.executable
        self.git = shutil.which("git")
        if not self.git:
            raise RuntimeError("git is not on PATH")
        self.powershell = os.path.join(system, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        folders = [os.path.dirname(self.python), os.path.dirname(self.git)]
        self.bash = ""
        # the shell that ships with git, never a bash found on PATH: on Windows that name can be another system's
        probe = os.path.dirname(os.path.realpath(self.git))
        for _ in range(4):
            candidate = os.path.join(probe, "usr", "bin", "bash.exe")
            if os.path.isfile(candidate):
                self.bash = candidate
                folders += [os.path.join(probe, "cmd"), os.path.join(probe, "mingw64", "bin"), os.path.dirname(candidate)]
                break
            probe = os.path.dirname(probe)
        folders += [os.path.join(system, "System32"), os.path.join(system, "System32", "Wbem"),
                    os.path.dirname(self.powershell)]
        self.path = [f for i, f in enumerate(folders) if os.path.isdir(f) and f not in folders[:i]]


class Reader:
    """One thing to run: a command wired in the settings file (run through the shell as written) or a tool (argv)."""

    def __init__(self, rid, interps, command=None, argv=None, stdin="", event="", matcher="", script="", ok=(0,),
                 outside=False, extra=(), missing=""):
        self.rid, self.interps, self.command, self.argv = rid, interps, command, argv
        self.stdin, self.event, self.matcher, self.script, self.ok = stdin, event, matcher, script, ok
        self.outside = outside  # started by the ledger script, outside any session: no role and no block
        self.extra = tuple(extra)  # the variables the ledger script itself sets, or needs set, for this step
        self.missing = missing  # a source this file reads names it and no call is written for it: what to say

    @property
    def traced(self):
        """The file trace follows python alone: a reader that is, or starts from, another shell is never called clean."""
        return bool(self.interps) and all(interp == "python" for interp in self.interps)


WIRED_SCRIPT = re.compile(r"%USERPROFILE%[/\\]\.claude[/\\]([^\"]+)\"\s*(.*)$")


def wired_readers(kit):
    """Every command of the settings file, read from the file: the status line and each hook of each event."""
    with open(os.path.join(kit, "claude", "settings.json"), encoding="utf-8") as handle:
        settings = json.load(handle)
    found = []
    status = settings.get("statusLine") or {}
    if status.get("command"):
        found.append(("statusLine", "", status["command"]))
    for event, groups in (settings.get("hooks") or {}).items():
        for group in groups:
            for hook in group.get("hooks") or []:
                if hook.get("command"):
                    found.append((event, group.get("matcher") or "", hook["command"]))
    readers = []
    for event, matcher, command in found:
        hit = WIRED_SCRIPT.search(command)
        script = hit.group(1).replace("\\", "/") if hit else ""
        tail = (" " + hit.group(2).strip()) if hit and hit.group(2).strip() else ""
        first = command.split()[0].lower()
        interp = "powershell" if first.startswith("powershell") else ("python" if first.startswith("python") else first)
        label = "%s%s %s%s" % (event, "[%s]" % matcher if matcher else "", os.path.basename(script) or command, tail)
        readers.append(Reader(label, (interp,), command=command, event=event, matcher=matcher, script=script))
    return readers


SEAT_SCRIPT = re.compile(r"scripts/([a-z][a-z-]*\.(?:py|sh))")
ROW = "- 2026-10-13 19:0x [decision] %s row by the record tool (orchestrator pane 19:0x)\n" % WORD
# The tools a session starts, each with its call; {py} {bash} {home} {kit} {ev} {transcript} are filled per sandbox.
# The first six are named by the seat file as scripts/<name>. The rest are named by no file a pin reads: each is a
# script the code scan finds (code_scan), which is what holds it in this list, the last two with the call their own
# argument lines give (reports-check.py:427-434 with no argument, turn-typing.py:221-228 on the session's transcript).
# The last field is the exit codes that are an answer and not a failure: owner-asked.py exits 1 on no hit, train-due.py
# 1 on read and not due, pulse.py 1 when an input is missing (the second board keeps no ledger log).
TOOLS = (
    ("row.sh", ("bash", "python"), ["{bash}", "{kit}/scripts/row.sh", "decision", WORD + " row by the row script"], "", (0,)),
    ("owner-asked.py", ("python",), ["{py}", "{kit}/scripts/owner-asked.py", WORD], "", (0, 1)),
    ("train-due.py", ("python",), ["{py}", "{kit}/scripts/train-due.py", "--verbose"], "", (0, 1)),
    ("train-wait.py", ("python",), ["{py}", "{kit}/scripts/train-wait.py", "--row"], "", (0,)),
    ("brief-gen.py", ("python",), ["{py}", "{kit}/scripts/brief-gen.py", "review", LANE], "", (0,)),
    ("brief-check.py", ("python",), ["{py}", "{kit}/scripts/brief-check.py", "{ev}/briefs/" + BRIEF, "--deny-tier", "2"], "", (0,)),
    ("record.py", ("python",), ["{py}", "{home}/.claude/tools/record.py", "add", "rulings"], ROW, (0,)),
    ("lane-state.py", ("python",), ["{py}", "{home}/.claude/tools/lane-state.py", "law"], "", (0,)),
    ("pulse.py", ("python",), ["{py}", "{home}/.claude/tools/pulse.py", "--max", "20"], "", (0, 1)),
    ("evidence-path.py", ("python",), ["{py}", "{kit}/scripts/evidence-path.py"], "", (0,)),
    ("evidence-path.py --worktree-root", ("python",), ["{py}", "{kit}/scripts/evidence-path.py", "--worktree-root"], "", (0,)),
    ("reports-check.py", ("python",), ["{py}", "{home}/.claude/tools/reports-check.py"], "", (0,)),
    ("turn-typing.py", ("python",), ["{py}", "{kit}/scripts/turn-typing.py", "--jsonl", "{transcript}", "--day", "2026-10-12"],
     "", (0,)),
)


def seat_scripts(kit):
    with open(os.path.join(kit, "claude", "seats", "orchestrator.md"), encoding="utf-8") as handle:
        return sorted(set(SEAT_SCRIPT.findall(handle.read())))


def tool_readers(kit):
    readers = [Reader("tool " + name, interps, argv=argv, stdin=stdin, script=argv[1], ok=ok)
               for name, interps, argv, stdin, ok in TOOLS]
    covered = set(os.path.basename(argv[1]) for _, _, argv, _, _ in TOOLS)
    for name in seat_scripts(kit):
        if name not in covered:  # the seat grew a script this file has no call for: loud, in every situation
            readers.append(Reader("tool " + name, (), script=""))
    return readers


# The readers of kind D are read from the ledger script's own lines: every tool it names beside itself (Join-Path
# $scriptDir) and every tool it hands to its resolver, which takes the installed copy first (ledger-nightly.ps1:
# 645-649). Each is started as the script starts it: python, the arguments of its Invoke-Step line, no role, no block.
# The calls are written here, one per tool, and a tool the script names with no call here is an error row. What the
# script works out from the clock or from the disk is pinned, so a run does not depend on the hour: the window
# (--since, --until), the transcript (the session's own), and a morning clock for the escalation, which past 21:15
# writes nothing (pulse-escalate.py:184-186), and for the review rate (--today). Three steps take their target from
# the one config beside the script (ledger-day.py, and the sweep and the retention through ledger-nightly.ps1:689-737);
# the sandbox holds that config, pointed at the first project, the board the machine was set up for. The review rate
# runs only when a machine variable names a root and a practice day (ledger-nightly.ps1:595-601): both are set to
# the first board here, which is what one machine-wide variable can name.
NIGHTLY_SCRIPT = "scripts/ledger-nightly.ps1"
# A tool is every string of a non-comment line that is a python file, alone or at the end of a path, whatever starts
# it: Join-Path with either quote, -ChildPath, an interpolated string, $PSScriptRoot. A step is an Invoke-Step line.
NIGHTLY_NAMED = re.compile(r"""(["'])(?:[^"'\r\n]*[\\/])?([A-Za-z][\w.-]*\.py)\1""")
NIGHTLY_STEP = re.compile(r"(?<![\w-])Invoke-Step(?![\w-])")
PULSE_LOG = (("PULSE_LOG", "{kit}/ledger/nightly.log"),)
SINCE, UNTIL, SINCE_UTC, UNTIL_UTC = "2026-10-12 18:00", "2026-10-13 08:00", "2026-10-12T16:00:00", "2026-10-13T06:00:00"
# tool: (what follows python on its line, the exit codes that are an answer, the variables of that step)
NIGHTLY_CALLS = {
    "ledger-day.py": (["{tool}", "--since", SINCE], (0,), ()),
    "usage-probe.py": (["{tool}", "--csv"], (0, 1), ()),
    "ledger-compare.py": (["{tool}"], (0,), ()),
    "train-wait.py": (["{tool}", "--row"], (0,), ()),
    "tool-errors.py": (["{tool}", "--file", "{transcript}", "--row"], (0,), ()),
    "token-shape.py": (["{tool}", "{transcript}", "--since", SINCE_UTC, "--until", UNTIL_UTC, "--row"], (0,), ()),
    "rework-shape.py": (["{tool}", "--since", SINCE, "--until", UNTIL], (0,), ()),
    "tooling-block-rate.py": (["{tool}", "--last-complete", "--root", "{first_ev}", "--today", "2026-10-13"], (0,),
                              (("TOOLING_BLOCK_ROOT", "{first_ev}"), ("TOOLING_BLOCK_ANCHOR", "2026-10-01"))),
    "kit-twin-drift.py": (["{tool}", "--row"], (0,), ()),
    "pulse.py": (["{tool}"], (0, 1), PULSE_LOG),
    "pulse-escalate.py": (["{tool}", "--now", "2026-10-13 08:00"], (0,), PULSE_LOG),
    "worktree-sweep.py": (["-u", "{tool}", "--repo", "{first}", "--apply", "--limit", "30", "--lock-root", "{tmp}",
                           "--evidence-root", "{first_ev}"], (0,), ()),
    "evidence-retention.py": (["-u", "{tool}", "--root", "{first_ev}", "--apply"], (0,), ()),
}


def nightly_tools(text):
    """[(tool, where the script takes it from)] in the order of the script's own lines, comments left out. A tool a
    line hands to the resolver is the installed copy, any other is the one beside the script."""
    found, installed = [], set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for _quote, name in NIGHTLY_NAMED.findall(line):
            if name not in found:
                found.append(name)
            if "Resolve-KitTool" in line:
                installed.add(name)
    return [(name, ("{home}/.claude/tools/" if name in installed else "{kit}/scripts/") + name) for name in found]


def nightly_steps(text):
    """How many lines of the script start a step: its Invoke-Step calls, comments and the function's own line left out."""
    return len([line for line in text.splitlines()
                if NIGHTLY_STEP.search(line) and not re.match(r"\s*(#|function\b)", line)])


def nightly_step_check(kit, script=None):
    """[] when the script holds as many Invoke-Step lines as tools, else one reader that is an error row saying so."""
    with open(script or os.path.join(kit, *NIGHTLY_SCRIPT.split("/")), encoding="utf-8-sig") as handle:
        text = handle.read()
    steps, tools = nightly_steps(text), len(nightly_tools(text))
    if steps == tools:
        return []
    return [Reader("nightly " + os.path.basename(NIGHTLY_SCRIPT), (), outside=True,
                   missing="%s holds %d Invoke-Step lines and names %d tools: a step this file reads no tool for, or a "
                           "tool no step starts" % (NIGHTLY_SCRIPT, steps, tools))]


def nightly_readers(kit, script=None):
    with open(script or os.path.join(kit, *NIGHTLY_SCRIPT.split("/")), encoding="utf-8-sig") as handle:
        tools = nightly_tools(handle.read())
    readers = []
    for name, path in tools:
        if name not in NIGHTLY_CALLS:  # the script grew a tool this file has no call for: loud, in every situation
            readers.append(Reader("nightly " + name, (), outside=True,
                                  missing="%s starts %s and this file has no call for it" % (NIGHTLY_SCRIPT, name)))
            continue
        tail, ok, extra = NIGHTLY_CALLS[name]
        readers.append(Reader("nightly " + name, ("python",), argv=["{py}"] + [part.replace("{tool}", path) for part in tail],
                              script=path, ok=ok, outside=True, extra=extra))
    return readers


# The code itself is a source of the run list: every script under these folders that holds a board default token is a
# board reader, and it is run, or it is in NOT_RUN with the reason, or it is an error row. The tokens are the
# variables of the candidate block and the names of the decoy files (a dated name gives its stem), both taken from the
# tables the sandbox is built from, so the scan cannot drift from what the harness measures.
SCAN_FOLDERS, SCAN_KINDS = ("claude/tools", "claude/hooks", "scripts"), (".py", ".sh", ".ps1")
SCAN_SKIP, DATED = ("tests", "fixtures", "__pycache__"), re.compile(r"-?\d{4}-\d{2}-\d{2}.*$")
NOT_RUN = (
    ("scripts/ledger-nightly.ps1",
     "the ledger script itself: its tools are run one by one and the script never is, so its own reads (its config, "
     "the EVIDENCE_ROOT fallback, every project's transcripts) are read by hand"),
    ("scripts/quality.py",
     "a module and not a reader of its own: ledger-day.py loads it, and what it opens is in that tool's rows"),
    ("scripts/model-trial.py",
     "its call (--since, --until, model-trial.py:144-151) needs agents with a meta.json under the home's projects "
     "folder, which the sandbox does not hold: it exits 2 before it reads a review. With no EVIDENCE_ROOT it takes "
     "the reviews of the evidence folder beside the working folder's parent (model-trial.py:121), the default that "
     "train-wait.py is measured for"),
    ("scripts/board.py",
     "no settings line, seat line or ledger line starts it; it keeps a state of its own under CLAUDE_BOARD_DIR and "
     "only points at the landings file from the page it renders (board.py:16-17)"),
)


def board_tokens(decoys):
    """The board default tokens: the block's variables and the decoy files' names, a dated name cut to its stem."""
    names = set(DATED.sub("", os.path.basename(path)) for path in decoys)
    return tuple(sorted(set(candidate_block("e", "p")) | set(name for name in names if name)))


def code_scan(kit, tokens):
    """{kit-relative script: (the first token it holds, its line)} for every script under the scanned folders."""
    found = {}
    if not tokens:
        return found
    wanted = re.compile(r"(?<![\w.-])(%s)(?!\w)" % "|".join(re.escape(token) for token in tokens))
    for top in SCAN_FOLDERS:
        for base, folders, files in os.walk(os.path.join(kit, *top.split("/"))):
            folders[:] = [folder for folder in folders if folder not in SCAN_SKIP]
            for name in files:
                if not name.lower().endswith(SCAN_KINDS):
                    continue
                lines = (read_bytes(os.path.join(base, name)) or b"").decode("utf-8", "replace").splitlines()
                hits = [(hit.group(1), at) for at, hit in enumerate(map(wanted.search, lines), 1) if hit]
                if hits:
                    found[posix(os.path.relpath(os.path.join(base, name), kit))] = hits[0]
    return found


def reader_files(readers):
    """The kit-relative scripts a run list starts: a wired one sits under claude/, a call names the installed copy or
    the kit's own."""
    files = set()
    for reader in readers:
        if reader.command and reader.script:
            files.add("claude/" + reader.script)
        elif reader.argv:
            files.add(posix(reader.script).replace("{home}/.claude/", "claude/").replace("{kit}/", ""))
    return files


def code_readers(kit, tokens, run_files, not_run=NOT_RUN):
    """One reader that is an error row for every script the scan finds that is neither run nor listed as not run, and
    for every not-run entry that is run after all or that the scan does not find: the list is held both ways."""
    found, out, readers = code_scan(kit, tokens), dict(not_run), []
    if not tokens:
        return readers
    for path in sorted(found):
        if path not in run_files and path not in out:
            readers.append(Reader("code " + path, (), missing="%s holds the board default token %s (line %d) and is "
                                  "neither run nor listed as not run" % ((path,) + found[path])))
    for path in sorted(out):
        if path in run_files or path not in found:
            readers.append(Reader("code " + path, (), missing="%s is listed as not run and %s"
                                  % (path, "is run" if path in run_files else "the code scan does not find it")))
    return readers


def run_list(kit, tokens=()):
    """Every reader, from the settings file, the seat text and the ledger script, and, given the board default tokens,
    from the code itself."""
    readers = wired_readers(kit) + tool_readers(kit) + nightly_readers(kit) + nightly_step_check(kit)
    return readers + code_readers(kit, tokens, reader_files(readers))


# The trace every python child runs under: a sitecustomize.py on its PYTHONPATH that registers an audit hook and writes
# one line per open, listing and glob whose path is under the sandbox root. It holds its log by number and writes
# through os.write, which raises no event, so it never traces itself, and it changes nothing a reader sees. A python
# that never loaded it writes no start line, and its run is then untraced, never clean.
TRACE_HOOK = '''"""Written by the board isolation harness: one line per open, listing and glob under the sandbox root."""
import os
import sys

_LOG, _ROOT = os.environ.get("BOARD_ISO_TRACE"), os.environ.get("BOARD_ISO_ROOT")
_KINDS = {"open": "open", "os.listdir": "list", "os.scandir": "list", "glob.glob": "glob", "glob.glob/2": "glob",
          "subprocess.Popen": "spawn", "os.system": "spawn"}
_FD = None


def _spot(path, _abspath=os.path.abspath, _normcase=os.path.normcase, _sep=os.sep, _long=chr(92) * 2 + "?" + chr(92)):
    spot = _normcase(_abspath(path))
    if spot.startswith(_long):  # the long-path form names the same file
        spot = spot[4:]
        if spot.startswith("unc" + chr(92)):
            spot = chr(92) * 2 + spot[4:]
    return spot.replace(_sep, "/").rstrip("/")


if _LOG and _ROOT:
    try:
        _FD = os.open(_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    except OSError:
        _FD = None

if _FD is not None:
    _UNDER = _spot(_ROOT) + "/"

    def _note(kind, text, _write=os.write, _fd=_FD, _end=chr(10), _tab=chr(9)):
        _write(_fd, (kind + _tab + text + _end).encode("utf-8", "replace"))

    def _hook(event, args, _kinds=_KINDS, _fsdecode=os.fsdecode, _join=os.path.join, _getpid=os.getpid,
              _getcwd=os.getcwd, _tab=chr(9)):
        kind = _kinds.get(event)
        if kind is None:
            return
        try:
            if kind == "spawn":
                # subprocess.Popen hands (executable, args, cwd, env), os.system the one line its shell runs
                popen = event == "subprocess.Popen"
                line = args[1] if popen else args[0]
                if not isinstance(line, (str, bytes)):
                    line = " ".join(_fsdecode(part) for part in line)
                where = args[2] if popen and args[2] is not None else _getcwd()
                program = (args[0] or "") if popen else "shell"
                _note(kind, _tab.join([str(_getpid())] + [" ".join(_fsdecode(text).split())
                                                          for text in (where, program, line)]))
                return
            path = args[0] if args else None
            if path is None:
                path = "."
            if isinstance(path, int):
                return
            path = _fsdecode(path)
            if event == "glob.glob/2" and len(args) > 2 and args[2] is not None:
                path = _join(_fsdecode(args[2]), path)
            spot = _spot(path)
            if (spot + "/").startswith(_UNDER):
                _note(kind, spot)
        except Exception as error:
            _note("fault", "%s: %r" % (event, error))

    _note("start", "%d%s%d" % (os.getpid(), chr(9), os.getppid()))
    sys.addaudithook(_hook)
'''
TRACE_VERBS = {"open": "opens", "list": "lists", "glob": "globs"}

# The trace follows children. Each spawn of a traced python is a line: who spawned, where, what. A python child is
# followed when a start line names its spawner as the parent, so one started with its site or its environment switched
# off (-I, -S, -E, a rebuilt environment) is not. Any other child is followed by nothing: it passes only when it is on
# this list, (name, the reason), and even then what it is handed is read, a command line or a working folder under a
# decoy root being a leak. A run with a child nothing followed is untraced, never clean.
ALLOWED_CHILDREN = (
    ("git", "it reads the repository its command line or its working folder names and its own config under the home, "
            "never a board file by itself; both are checked against the decoy roots, the first project's checkout "
            "among them, and the ceiling variable keeps it inside the sandbox"),
)
ALLOWED_NAMES = frozenset(name for name, _ in ALLOWED_CHILDREN)
PYTHON_CHILD = re.compile(r"(?:python[\d.]*w?|py)$")


def child_name(program, command):
    """The bare name of a spawned program: the file of its executable, or the first word of its command line."""
    first = program or (command[1:].split('"')[0] if command.startswith('"') else (command.split() or [""])[0])
    return re.sub(r"\.(exe|cmd|bat|com)$", "", os.path.basename(first.replace("\\", "/")).lower())

# The one step that would leave the sandbox: the usage probe asks an endpoint (usage-probe.py:48). The kit's module is
# kept beside it under another name, and this one answers under the kit's name with the network call taken out.
USAGE_STUB = '''"""Written by the board isolation harness: the kit's module with the network call taken out."""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "usage_meters_kit", os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage_meters_kit.py"))
_KIT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_KIT)
globals().update((name, value) for name, value in vars(_KIT).items() if not name.startswith("__"))


def fetch(_token):
    return None, "stubbed: the usage endpoint is outside the sandbox"
'''


class Result:
    def __init__(self, verdict, detail="", stdout="", own=(), files=(), traced=True, ended=None):
        self.verdict, self.detail, self.stdout, self.own = verdict, detail, stdout, own
        self.ended = ended  # the exit code of a reader that failed ("timeout" when it never ended), None when it did not
        self.files = tuple(files)  # what leaked, as paths under the sandbox root
        self.traced = traced  # False: no file trace followed this run, its verdict rests on the visible rules alone

    def text(self, verdict=None):
        verdict = verdict or self.verdict
        if verdict in ("clean", "untraced"):
            clean = "clean" + (" (reads its own: %s)" % ", ".join(self.own) if self.own else "") + self.detail
            return clean if verdict == "clean" else "untraced (by the visible rules: %s)" % clean
        return "%s %s" % (verdict.upper(), self.detail)


class SandboxRefused(RuntimeError):
    """The harness will not start: a file above the sandbox root would answer a reader that walks up."""


def ancestor_records(root):
    """Every record.json in a folder above root. The record tool takes the first one it finds walking up from the
    working folder (record.py:195-199); the decoy inside the sandbox stops that walk, and nothing else may answer it."""
    found, here = [], os.path.dirname(os.path.abspath(root))
    while True:
        candidate = os.path.join(here, "record.json")
        if os.path.isfile(candidate):
            found.append(posix(candidate))
        parent = os.path.dirname(here)
        if parent == here:
            return found
        here = parent


class Sandbox:
    def __init__(self, kit, where="worktree", base=None, installed=True):
        self.kit, self.installed = kit, installed
        self.tools = Toolbox()
        if base:
            os.makedirs(base, exist_ok=True)
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="board-iso-", dir=base))
        above = ancestor_records(self.root)
        if above:
            os.rmdir(self.root)
            raise SandboxRefused("a record.json above the sandbox root %s: %s" % (posix(self.root), ", ".join(above)))
        join = lambda *parts: os.path.join(self.root, *parts)
        self.home, self.claude, self.kitcopy = join("home"), join("home", ".claude"), join("kit")
        self.parent, self.first, self.first_ev = join("repo"), join("repo", "alpha"), join("repo", "evidence")
        self.project, self.ev = join("repo", "beta"), join("repo", "evidence-beta")
        self.wt_root = join("repo", "worktree-beta")
        self.worktree = join("repo", "worktree-beta", "beta-" + LANE)
        self.tmp, self.stubs, self.trace_dir = join("tmp"), join("stubs"), join("trace")
        self.cwd = self.project if where == "project" else self.worktree
        self.decoys, self.decoy_dirs, self.own, self.probes = {}, [], {}, {}
        self.transcript = os.path.join(self.claude, "projects", "sandbox-project", SESSION + ".jsonl")
        self.agent_transcript = os.path.join(self.claude, "projects", "sandbox-project", SESSION, "subagents",
                                             "agent-%s.jsonl" % AGENT)
        self._build()
        # what the trace may name: every decoy file, and every folder of a decoy board (the home board's own folder,
        # its briefs and its asks, the first project's evidence root and all below it)
        self.spots = dict((norm(path), self.rel(path)) for path in self.decoys)
        for top in self.decoy_dirs + [self.claude]:
            for base, folders, _files in os.walk(top):
                self.spots[norm(base)] = self.rel(base)
                if top == self.claude:
                    del folders[:]  # the board's own folder, not the hooks, tools and stores beside its files
        # what a child may not be handed: every decoy file and folder but the home board's own folder, which holds the
        # installed tools too, and the first project's checkout
        self.spawn_roots = [(path_pattern(spot), rel) for spot, rel in sorted(self.spots.items()) if spot != norm(self.claude)]
        self.spawn_roots.append((path_pattern(self.first), self.rel(self.first)))
        files, _ = self.walk()
        self.pristine = dict((path, read_bytes(path)) for path in files)

    # --- the tree ---------------------------------------------------------------------------------------------

    def rel(self, path):
        return posix(os.path.relpath(path, self.root))

    def tokens(self):
        """The board default tokens the code scan looks for, from this sandbox's own decoys and the candidate block."""
        return board_tokens(self.decoys)

    def spawn_named(self, where, command):
        """The decoy roots a spawn is handed, in its command line or as its working folder, the deepest of each."""
        low = (command + "\n" + where).replace("\\\\", "/").replace("\\", "/").lower()
        named = [rel for pattern, rel in self.spawn_roots if pattern.search(low)]
        return [rel for rel in named if not any(other.startswith(rel + "/") for other in named)]

    def _marker(self, path):
        """One marker per file, from its place in the sandbox, so no two files share one."""
        return mark(re.sub(r"[^a-z0-9]+", "-", self.rel(path).lower()).strip("-"))

    def _decoy(self, _owner, path, shape):
        marker = self._marker(path)
        put(path, shape % {"m": marker, "w": WORD})
        self.decoys[path] = marker

    def _own(self, path, shape):
        marker = self._marker(path)
        put(path, shape % {"m": marker, "w": WORD})
        self.own[path] = marker

    def _board(self, owner, base, writer, sheet):
        """The files a board holds, in the shapes the readers parse: register, landings, sheet, pulse."""
        j = lambda *parts: os.path.join(base, *parts)
        writer(j("rulings.md"), "# Rulings\n\n- 2026-10-01 10:0x [owner] %(w)s ruling %(m)s (orchestrator pane 10:0x)\n")
        writer(j("landings.md"), "# Landings\n\n| time | event | role | where | id | summary |\n| --- | --- | --- | --- | --- | --- |\n"
                                 "| 2026-10-01 10:00:00 | Stop | lane | alpha | 00000000 | %(w)s owner landing %(m)s |\n"
                                 "2026-10-01 10:00 %(w)s owner landing %(m)s\n")
        if sheet:
            writer(j("law.md"), "# State sheet\n\n%(w)s sheet %(m)s\n")
        # the id is the marker: a mechanism whose trigger matches nothing and whose review date passed is printed by id
        writer(j("pulse.md"), "%(m)s | quality | 2026-10-01 %(w)s | no-such-folder/*.md | none | echo 1 | keep while read | 2026-01-01\n")

    def _build(self):
        kit, j = self.kit, os.path.join
        for folder in (self.tmp, self.stubs, self.first, j(self.home, "AppData", "Roaming"), j(self.home, "AppData", "Local")):
            os.makedirs(folder)
        # the home as the installer leaves it: hooks, tools, seats and the status line under ~/.claude
        # with the folders the twin check pairs with the kit's (kit-twin-drift.py:73-74)
        bare = shutil.ignore_patterns("__pycache__")
        for name in ("hooks", "tools", "seats", "agents", "skills"):
            if os.path.isdir(j(kit, "claude", name)):
                shutil.copytree(j(kit, "claude", name), j(self.claude, name), ignore=bare)
        shutil.copy2(j(kit, "claude", "statusline.ps1"), j(self.claude, "statusline.ps1"))
        os.makedirs(j(self.kitcopy, "scripts"))
        for name in os.listdir(j(kit, "scripts")):
            if os.path.isfile(j(kit, "scripts", name)):
                shutil.copy2(j(kit, "scripts", name), j(self.kitcopy, "scripts", name))
        # the kit copy is a checkout, as the clone the ledger script runs from is, holding the folders the twin check
        # asks git for (kit-twin-drift.py:71,80). It is committed here, before the sandbox writes its own files beside
        # the scripts, so those stay untracked, as a machine's config is in a clone
        for name in ("claude", "herdr", "install", "project-template"):
            if os.path.isdir(j(kit, name)):
                shutil.copytree(j(kit, name), j(self.kitcopy, name), ignore=bare)
        git = dict(self.base_env(), GIT_AUTHOR_NAME="lane", GIT_COMMITTER_NAME="lane", GIT_AUTHOR_EMAIL="lane.sandbox.invalid",
                   GIT_COMMITTER_EMAIL="lane.sandbox.invalid")
        for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "kit"]):
            self._git(self.kitcopy, args, git)
        put(j(self.trace_dir, "sitecustomize.py"), TRACE_HOOK)
        meters = j(self.kitcopy, "scripts", "usage_meters.py")
        if os.path.isfile(meters):
            os.replace(meters, j(self.kitcopy, "scripts", "usage_meters_kit.py"))
            put(meters, USAGE_STUB)
        # the scope file an owner copies when turning the retention on (ledger-nightly.ps1:716-718)
        scope = j(self.claude, "tools", "evidence-retention.example.json")
        if os.path.isfile(scope):
            shutil.copy2(scope, j(self.claude, "tools", "evidence-retention.json"))
        # the one config beside the ledger script, which its python steps read too (ledger-nightly.ps1:121-125,
        # ledger-day.py:99): never the kit's own copy, which may name real folders of this machine, but the committed
        # example pointed at the first project
        config, example = j(self.kitcopy, "scripts", "ledger-config.json"), j(kit, "scripts", "ledger-config.example.json")
        if os.path.isfile(config):
            os.remove(config)
        if os.path.isfile(example):
            with open(example, encoding="utf-8") as handle:
                keys = json.load(handle)
            keys.update(_marker="@marker@", repo=posix(self.first),
                        worktree_sweep={"repo": posix(self.first), "limit": 30, "lock_root": posix(self.tmp),
                                        "evidence_root": posix(self.first_ev)},
                        evidence_retention={"root": posix(self.first_ev), "scope": None, "trash": None})
            self._decoy("first", config, json.dumps(keys, indent=1).replace("%", "%%").replace("@marker@", "%(m)s") + "\n")

        # the decoy machine board, where each reader's default looks
        home = lambda path, shape: self._decoy("home", path, shape)
        self._board("home", self.claude, home, sheet=True)
        home(j(self.claude, "briefs", "orchestrator-resume-2026-10-12.md"), "# Resume brief\n\n%(w)s first actions %(m)s\n")
        home(j(self.claude, "decisions", "owner-decisions-2026-10-12.md"), "# Owner asks\n\n- 10:0x %(w)s open ask %(m)s\n")
        home(j(self.claude, "hooks", "brief-launches.log"), "2026-10-01 10:00 %(w)s %(m)s\n")
        home(j(self.claude, "ledger", "nightly.log"), "[2026-10-01 08:00] start, %(w)s %(m)s\n")
        home(j(self.claude, "owner-reports.md"), "%(w)s report %(m)s\n")
        home(j(self.claude, "lane-state.json"), json.dumps({
            "_marker": "%(m)s", "fixed_lines": ["%(w)s fixed line %(m)s"],
            "lanes_glob": posix(j(self.first_ev, "lanes", "*.md")), "briefs_glob": posix(j(self.first_ev, "briefs", "*.md")),
            "reports_file": posix(j(self.first_ev, "ledger", "owner-reports.md"))}, indent=1) + "\n")

        # the first project: another board on the same machine, at the place the profiles name by default
        first = lambda path, shape: self._decoy("first", path, shape)
        self._board("first", self.first_ev, first, sheet=True)
        first(j(self.first_ev, "queue.md"), "%(w)s queue %(m)s\n")
        first(j(self.first_ev, "train-holds.txt"), "alane %(w)s %(m)s\n")
        first(j(self.first_ev, "briefs", "orchestrator-resume-2026-10-11.md"), "# Resume brief\n\n%(w)s %(m)s\n")
        first(j(self.first_ev, "briefs", "alpha-topic-2026-10-12.md"), "# Brief: alpha topic (lane alane)\n\n%(w)s %(m)s\n")
        first(j(self.first_ev, "lanes", "alpha-topic-2026-10-12.md"), "# Lane alane (item 1) alpha topic\n\n%(w)s %(m)s\n")
        first(j(self.first_ev, "reviews", "alpha-topic-review-2026-10-12.md"), "CLEAR\n\n%(w)s %(m)s\n")
        first(j(self.first_ev, "drafts", "owner-decisions-2026-10-12.md"), "# Owner asks\n\n- 10:0x %(w)s open ask %(m)s\n")
        first(j(self.first_ev, "ledger", "owner-reports.md"), "%(w)s report %(m)s\n")
        # two configs a reader finds with no variable: record.json in an ancestor of the working folder, and the
        # round brief config beside its script
        first(j(self.parent, "record.json"), json.dumps({
            "_marker": "%(m)s", "targets": {"rulings": "evidence/rulings.md", "landings": "evidence/landings.md"}}) + "\n")
        first(j(self.kitcopy, "scripts", "brief-gen.json"), json.dumps({
            "_marker": "%(m)s", "evidence_root": posix(self.first_ev)}) + "\n")
        # the ledger's own log, in the kit clone the ledger script runs from (ledger-nightly.ps1:55,68): two runs whose
        # pulse step flagged the same key, which is what the escalation step acts on (pulse-escalate.py:158-165)
        ledger_run = ("[2026-10-12 HH:00] start, %(w)s %(m)s\n"
                      "[2026-10-12 HH:00] pulse | pulse 2026-10-12 HH:00: mechanisms 1 (due 1)\n"
                      "[2026-10-12 HH:00] pulse | %(w)s flagged item %(m)s [pulse:%(w)s-key]\n")
        first(j(self.kitcopy, "ledger", "nightly.log"), ledger_run.replace("HH", "08") + ledger_run.replace("HH", "18"))
        self.decoy_dirs = [j(self.claude, "briefs"), j(self.claude, "decisions"), self.first_ev]

        # the second project's own board
        own = self._own
        self._board("second", self.ev, own, sheet=False)
        own(j(self.ev, "queue.md"), "%(w)s queue %(m)s\n")
        own(j(self.ev, "briefs", "orchestrator-resume-2026-10-13.md"), "# Resume brief\n\n%(w)s %(m)s\n")
        own(j(self.ev, "briefs", BRIEF), "# Brief: beta topic (lane %s)\n\n%%(w)s %%(m)s\n\n## Pins\n\n- BetaTopicTest: the topic holds.\n\n"
                                         "## Report\n\nPath: lanes/%s\n" % (LANE, BRIEF))
        own(j(self.ev, "lanes", BRIEF), "# Lane %s (item 7) beta topic\n\nWorktree `%s`, branch lane/%s. %%(w)s %%(m)s\n"
            % (LANE, posix(self.worktree), LANE))
        own(j(self.ev, "reviews", "beta-topic-review-2026-10-12.md"), "CLEAR\n\n%(w)s %(m)s\n")
        own(j(self.ev, "drafts", "owner-decisions-2026-10-12.md"), "# Owner asks\n\n- 10:0x %(w)s open ask %(m)s\n")
        own(j(self.ev, "ledger", "owner-reports.md"), "%(w)s report %(m)s\n")
        own(j(self.ev, "lane-state.json"), json.dumps({"_marker": "%(m)s", "fixed_lines": ["%(w)s fixed line %(m)s"]}) + "\n")
        put(j(self.ev, "brief-gen.json"), json.dumps({"evidence_root": posix(self.ev),
                                                     "worktree": posix(self.wt_root) + "/beta-{token}"}) + "\n")
        template = j(kit, "project-template", "briefs", "TEMPLATE-lane-brief.md")
        if os.path.isfile(template):
            shutil.copy2(template, j(self.ev, "briefs", "TEMPLATE-lane-brief.md"))

        # P as the installer scaffolds it (the profile keeps the template's evidence line), then W, a linked worktree
        os.makedirs(self.project)
        for name in ("AGENTS.md", "CLAUDE.md", "CLAUDE.project.md"):
            source = j(kit, "project-template", name)
            if os.path.isfile(source):
                with open(source, encoding="utf-8") as handle:
                    text = handle.read()
                if name == "CLAUDE.project.md":
                    text = re.sub(r"(?im)^(\s*-?\s*Project name:).*$", r"\1 beta", text)
                put(j(self.project, name), text)
        put(j(self.project, "README.md"), "# beta\n")
        steps = (
            (self.project, ["init", "-q", "-b", "main"]), (self.project, ["add", "-A"]),
            (self.project, ["commit", "-q", "-m", "scaffold"]),
            (self.project, ["worktree", "add", "-q", "-b", "lane/" + LANE, self.worktree]),
            # the first project is a repository too: the ledger's config names it, and its steps ask git about it
            (self.first, ["init", "-q", "-b", "main"]),
            (self.first, ["commit", "-q", "--allow-empty", "-m", "scaffold"]),
        )
        for cwd, args in steps:
            self._git(cwd, args, git)
        put(j(self.worktree, "work.txt"), "lane work\n")
        self._git(self.worktree, ["add", "work.txt"], git)
        self._git(self.worktree, ["commit", "-q", "-m", "lane work"], git)
        for folder in (self.project, self.worktree):  # a lane's notes file, untracked
            own(j(folder, "NOTES.md"), "# NOTES\n\n%(w)s notes of the second project %(m)s\n")
            self.own.pop(j(folder, "NOTES.md"))
        if self.installed:  # the block as an installer would write it; the project before that has no such file
            put(j(self.project, ".claude", "settings.local.json"), json.dumps({"env": self.block()}, indent=1) + "\n")
        # the worklog task of the lane's branch, where the worklog hooks key it: the main checkout's name and the
        # hash of its lower-case path, then the branch with its slash as a dash (claude/hooks/worklog-lib.ps1)
        repo_id = "beta-" + hashlib.md5(self.project.lower().encode("utf-8")).hexdigest()[:8]
        own(j(self.claude, "worklog", repo_id, "tasks", "lane-%s.md" % LANE),
            "---\nid: lane-%s\nstatus: active\n---\n\n%%(w)s worklog task of the second project %%(m)s\n" % LANE)
        # the session's prompt log, where the prompt hook keeps it (prompt-log.py:39-43,53): with none, the recovery
        # at a session start returns before it reads anything (prompt-log.py:195-201)
        own(j(self.claude, "checkpoints", "sandbox-project", SESSION[:8] + "-prompts.md"),
            "- 2026-10-12 18:00 %(w)s: a prompt typed earlier in this session %(m)s\n")

        for name in ("claude", "herdr"):  # what a hook could call that leaves the machine: recorded, never run
            with open(j(self.stubs, name + ".cmd"), "w", newline="") as handle:
                handle.write("@echo off\r\necho %~n0 %*>>\"%BOARD_ISO_STUB_LOG%\"\r\n")
        rows = (
            {"type": "user", "sessionId": SESSION, "cwd": self.cwd, "timestamp": "2026-10-12T18:00:00.000Z", "uuid": "u1",
             "message": {"role": "user", "content": WORD + ": land the lane"}},
            {"type": "assistant", "sessionId": SESSION, "cwd": self.cwd, "timestamp": "2026-10-12T18:00:05.000Z", "uuid": "a1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Lane %s report written, tip on lane/%s." % (LANE, LANE)}]}},
        )
        for path in (self.transcript, self.agent_transcript):
            put(path, "".join(json.dumps(row) + "\n" for row in rows))

    def _git(self, cwd, args, env):
        done = subprocess.run([self.tools.git, "-C", cwd] + args, env=env, capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            raise RuntimeError("git %s failed in the sandbox: %s" % (" ".join(args[:2]), done.stderr.strip()))

    # --- the environment --------------------------------------------------------------------------------------

    def base_env(self):
        system = os.environ["SystemRoot"]
        drive, tail = os.path.splitdrive(self.home)
        dead = "http://127.0.0.1:9"
        env = {
            "SystemRoot": system, "windir": system, "SystemDrive": os.environ.get("SystemDrive", drive),
            "COMSPEC": os.environ.get("COMSPEC", os.path.join(system, "System32", "cmd.exe")),
            "PATHEXT": ".COM;.EXE;.BAT;.CMD", "PATH": os.pathsep.join([self.stubs] + self.tools.path),
            "USERPROFILE": self.home, "HOME": self.home, "HOMEDRIVE": drive, "HOMEPATH": tail,
            "APPDATA": os.path.join(self.home, "AppData", "Roaming"), "LOCALAPPDATA": os.path.join(self.home, "AppData", "Local"),
            "TEMP": self.tmp, "TMP": self.tmp, "TMPDIR": self.tmp, "USERNAME": "lane",
            "PSModulePath": os.path.join(system, "System32", "WindowsPowerShell", "v1.0", "Modules"),
            "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
            "HTTP_PROXY": dead, "HTTPS_PROXY": dead, "ALL_PROXY": dead, "NO_PROXY": "",
            "BOARD_ISO_STUB_LOG": os.path.join(self.tmp, "stub-calls.log"),
            # the trace every python child loads, and the root it keeps to; git never looks above the sandbox
            "PYTHONPATH": self.trace_dir, "BOARD_ISO_ROOT": self.root, "GIT_CEILING_DIRECTORIES": self.root,
            "CLAUDE_TEST_NOW": "22:10",  # past the closing hour, so the close notice of a seat is part of the run
        }
        for name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData", "CommonProgramFiles",
                     "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS"):
            if os.environ.get(name):
                env[name] = os.environ[name]
        return env

    def block(self):
        return candidate_block(posix(self.ev), posix(self.parent))

    def fill(self):
        """What a call written in this file may name in braces."""
        return {"py": self.tools.python, "bash": self.tools.bash, "ps": self.tools.powershell, "home": self.home,
                "kit": self.kitcopy, "ev": self.ev, "first": self.first, "first_ev": self.first_ev, "tmp": self.tmp,
                "transcript": self.transcript}

    def outside_env(self, reader):
        """What a step of the ledger script gets: no role and no block, since no session started it, and the variables
        of that step alone (the run's own log as PULSE_LOG for the two pulse steps, ledger-nightly.ps1:55,68,652,665)."""
        return dict(self.base_env(), **dict((name, value.format(**self.fill())) for name, value in reader.extra))

    def trace_read(self, path):
        """(a python child wrote its start line, [(what it did, the decoy)], [what the trace could not record]) of one
        run. A glob counts as a listing of the folder its pattern starts in; one with no wildcard names its target."""
        seen, hits, faults, starts, spawns = False, {}, [], [], []
        for line in (read_bytes(path) or b"").decode("utf-8", "replace").splitlines():
            kind, _, spot = line.partition("\t")
            if kind == "start":
                seen = True
                starts.append(spot.split("\t")[-1])  # the parent of the python that wrote it
            elif kind == "spawn":
                spawns.append((spot.split("\t", 3) + ["", "", ""])[:4])
            elif kind == "fault":
                faults.append(spot)
            elif kind in TRACE_VERBS:
                parts = spot.split("/")
                wild = [at for at, part in enumerate(parts) if re.search(r"[*?\[]", part)]
                if kind == "glob" and wild:
                    kind, spot = "list", "/".join(parts[:wild[0]])
                if spot in self.spots:
                    hits.setdefault(self.spots[spot], TRACE_VERBS[kind])
        # the children: what each was handed, and whether anything followed it
        loose = []
        for parent, where, program, command in spawns:
            name = child_name(program, command)
            for rel in self.spawn_named(where, command):
                hits.setdefault(rel, "starts %s on" % name)
            if PYTHON_CHILD.match(name):
                if parent in starts:
                    starts.remove(parent)  # one start line answers one spawn
                else:
                    loose.append(name + " with no start line")
            elif name not in ALLOWED_NAMES:
                loose.append(name)
        return seen, sorted((verb, rel) for rel, verb in hits.items()), faults, sorted(set(loose))

    def env_for(self, role, block):
        env = self.base_env()
        env["CLAUDE_ROLE"] = role
        if role != "lane":  # a seated launch also exports the machine's briefs folder and the closing hour
            env["CLAUDE_BRIEFS_DIR"] = os.path.join(self.claude, "briefs")
            env["CLAUDE_CLOSING_HOUR"] = "22:00"
        if block:
            env.update(self.block())  # measured: a settings env block replaces what the launching shell exported
        return env

    # --- the home each interpreter resolves -------------------------------------------------------------------

    def inside(self, path):
        return (norm(path) + "/").startswith(norm(self.root) + "/")

    def home_of(self, interp, env):
        """[(resolver, the home it gives, the source pattern that makes it matter or None for always)]."""
        key = (interp,) + tuple(env.get(name) for name in ("USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH"))
        if key in self.probes:
            return self.probes[key]
        if interp == "python":
            names = (("os.path.expanduser", None), ("pathlib.Path.home", None))
            argv = [self.tools.python, "-c", "import os, pathlib; print(os.path.expanduser('~')); print(pathlib.Path.home())"]
        elif interp == "powershell":
            names = (("$env:USERPROFILE", None), ("$HOME", r"\$HOME\b|~[\\/]"), ("GetFolderPath", r"GetFolderPath|SpecialFolder"))
            argv = [self.tools.powershell, "-NoProfile", "-Command",
                    "$env:USERPROFILE; $HOME; [Environment]::GetFolderPath('UserProfile')"]
        elif interp == "bash" and self.tools.bash:
            names = (("$HOME", None), ("~", None))
            argv = [self.tools.bash, "-c", 'cd "$HOME" && pwd -W; cd ~ && pwd -W']
        else:
            self.probes[key] = None
            return None
        done = subprocess.run(argv, env=env, cwd=self.tmp, capture_output=True, text=True, timeout=RUN_TIMEOUT)
        lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
        answer = [(name, lines[i] if i < len(lines) else "", pattern) for i, (name, pattern) in enumerate(names)]
        self.probes[key] = answer
        return answer

    def home_refusal(self, reader, env):
        """Why this reader must not run, or "": each interpreter it starts must resolve its home inside the sandbox."""
        source = ""
        if reader.script:
            path = reader.script.format(home=self.home, kit=self.kitcopy, ev=self.ev) if reader.argv else os.path.join(self.claude, reader.script)
            folder = os.path.dirname(path)
            names = [os.path.basename(path)]
            if path.lower().endswith(".ps1") and os.path.isdir(folder):  # a script and what it dot-sources
                names = [n for n in os.listdir(folder) if n.lower().endswith(".ps1")]
            source = "".join((read_bytes(os.path.join(folder, n)) or b"").decode("utf-8", "replace") for n in names)
        if not reader.interps:
            return "no interpreter named"
        for interp in reader.interps:
            answer = self.home_of(interp, env)
            if answer is None:
                return "no home probe for %s" % interp
            for name, value, pattern in answer:
                if pattern is not None and not re.search(pattern, source):
                    continue
                if not value or not self.inside(value):
                    return "%s resolves the home through %s to %s, outside the sandbox" % (interp, name, value or "nothing")
        return ""

    # --- payloads ---------------------------------------------------------------------------------------------

    def payload(self, reader):
        event, cwd = reader.event, self.cwd
        if event == "statusLine":
            return {"session_id": SESSION, "transcript_path": self.transcript, "cwd": cwd, "version": "2.1.296",
                    "model": {"id": "model-x", "display_name": "Model"}, "workspace": {"current_dir": cwd, "project_dir": cwd},
                    "cost": {"total_cost_usd": 0.0, "total_lines_added": 1, "total_lines_removed": 0},
                    "context_window": {"context_window_size": 200000, "used_percentage": 12}}
        data = {"session_id": SESSION, "prompt_id": "dddd1111-2222-3333-4444-555566667777", "transcript_path": self.transcript,
                "cwd": cwd, "permission_mode": "bypassPermissions", "hook_event_name": event}
        if event == "SessionStart":
            data.update(source=(reader.matcher or "startup").split("|")[0], model="model-x")
        elif event == "UserPromptSubmit":
            data.update(prompt=WORD + ": what did the owner decide about the register?")
        elif event in ("PreCompact", "PostCompact"):
            data.update(trigger="auto", custom_instructions="", compact_summary="The lane wrote its report.")
        elif event == "SessionEnd":
            data.update(reason="other")
        elif event == "Stop":
            data.update(stop_hook_active=False, last_assistant_message="Lane %s report written." % LANE)
        elif event == "SubagentStop":
            data.update(stop_hook_active=False, agent_id=AGENT, agent_type="implementer",
                        agent_transcript_path=self.agent_transcript, last_assistant_message="Lane %s report written." % LANE)
        elif event == "TaskCompleted":
            data.update(task_id="7766554433221100", task_subject="Lane %s: the report" % LANE, task_description="the lane report")
        elif event in ("PreToolUse", "PostToolUse"):
            tool = reader.matcher if reader.matcher not in ("", "*") else "Bash"
            inputs = {
                "Read": {"file_path": os.path.join(cwd, "NOTES.md")},
                "Bash": {"command": "git status --short", "description": "Show working tree status"},
                "PowerShell": {"command": "Get-ChildItem", "description": "List the folder"},
                "Agent": {"subagent_type": "implementer", "description": "lane round",
                          "prompt": "Your brief is briefs/%s. Read it whole first and do exactly what it says." % BRIEF},
                "AskUserQuestion": {"questions": [{"question": "Which option?", "header": "Option", "multiSelect": False,
                                                   "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]}]},
            }
            data.update(tool_name=tool, tool_input=inputs.get(tool, {}), tool_use_id="toolu_01")
            if event == "PostToolUse":
                data.update(tool_response={"stdout": " M NOTES.md", "stderr": "", "interrupted": False})
        return data

    # --- one run ----------------------------------------------------------------------------------------------

    def walk(self):
        """({file: (size, mtime)}, {folder}) of the whole sandbox, git's own folders left out."""
        files, folders, stack = {}, set(), [self.root]
        while stack:
            try:
                entries = list(os.scandir(stack.pop()))
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name != ".git":
                        folders.add(entry.path)
                        stack.append(entry.path)
                else:
                    try:
                        info = os.stat(entry.path)
                    except OSError:
                        continue
                    files[entry.path] = (info.st_size, info.st_mtime_ns)
        return files, folders

    def allowed(self, path):
        """P, E, the worktree root and the temp folder; and the startup cache the shell itself keeps under the local
        application data folder, which is the interpreter's write and not the reader's."""
        spot = norm(path) + "/"
        shell_cache = os.path.join(self.home, "AppData", "Local", "Microsoft", "Windows", "PowerShell")
        return any(spot.startswith(norm(root) + "/") for root in (self.project, self.ev, self.wt_root, self.tmp, shell_cache))

    def disk_leaks(self, before, after):
        """[(what the run did, the file that leaked)]; past three files outside the project the text is one count."""
        leaks = []
        for path, marker in sorted(self.decoys.items()):
            now = read_bytes(path)
            if now != self.pristine[path]:
                leaks.append(("%s decoy %s [%s]" % ("deletes" if now is None else "writes", self.rel(path), marker),
                              self.rel(path)))
        stray = []
        for path in sorted(set(before) | set(after)):
            if path in self.decoys or before.get(path) == after.get(path) or self.allowed(path):
                continue
            verb = "creates" if path not in before else ("deletes" if path not in after else "changes")
            stray.append(("%s %s" % (verb, self.rel(path)), self.rel(path)))
        if len(stray) > 3:
            more = "and %d more files outside the project" % (len(stray) - 3)
            stray = stray[:3] + [(more if at == 0 else "", rel) for at, (_, rel) in enumerate(stray[3:])]
        # a silent reader: decoy text that never reached the output and was written into the project's own files
        for path in sorted(after):
            if before.get(path) == after.get(path) or path in self.decoys or not self.allowed(path):
                continue
            data = read_bytes(path) or b""
            for decoy, marker in sorted(self.decoys.items()):
                if marker.encode("ascii") in data:
                    leaks.append(("copies %s [%s] into %s" % (self.rel(decoy), marker, self.rel(path)), self.rel(decoy)))
        return leaks + stray

    def output_leaks(self, text):
        """[(what the output shows, the decoy file or folder it shows)]."""
        low = text.replace("\\\\", "/").replace("\\", "/").lower()
        leaks, named = [], []
        for path, marker in sorted(self.decoys.items()):
            if marker in text:
                leaks.append(("prints %s [%s]" % (self.rel(path), marker), self.rel(path)))
                named.append(path)
            elif path_pattern(path).search(low):
                leaks.append(("names %s [%s]" % (self.rel(path), marker), self.rel(path)))
                named.append(path)
        for folder in self.decoy_dirs:
            if path_pattern(folder).search(low) and not any(norm(p).startswith(norm(folder) + "/") for p in named):
                leaks.append(("names the decoy folder %s" % self.rel(folder), self.rel(folder)))
        return leaks

    def restore(self, before, after, folders_before, folders_after):
        """Put the sandbox back, so a run never reads what an earlier run left."""
        moved = set(path for path in set(before) | set(after) if before.get(path) != after.get(path))
        for path in moved | set(self.decoys):
            keep = self.pristine.get(path)
            if keep is None:
                if os.path.isfile(path):
                    os.remove(path)
            elif read_bytes(path) != keep:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as handle:
                    handle.write(keep)
        for folder in sorted(folders_after - folders_before, key=len, reverse=True):
            try:
                os.rmdir(folder)
            except OSError:
                pass

    def run(self, reader, env, cwd=None):
        cwd = cwd or self.cwd
        if not reader.command and not reader.argv:
            return Result("error", reader.missing
                          or "the seat names scripts/%s and this file has no call for it" % reader.rid.split()[-1])
        refusal = self.home_refusal(reader, env)
        if refusal:
            return Result("refused", refusal)
        trace = os.path.join(self.tmp, "file-trace.log")
        if os.path.isfile(trace):
            os.remove(trace)
        env = dict(env, BOARD_ISO_TRACE=trace)
        before, folders_before = self.walk()
        fill = self.fill()
        try:
            if reader.command:
                # the wired line as written, through the shell, which expands the home variable from the child's env
                done = subprocess.run(reader.command, shell=True, cwd=cwd, env=env, input=json.dumps(self.payload(reader)).encode("utf-8"),
                                      capture_output=True, timeout=RUN_TIMEOUT)
            else:
                done = subprocess.run([part.format(**fill) for part in reader.argv], cwd=cwd, env=env,
                                      input=reader.stdin.encode("utf-8"), capture_output=True, timeout=RUN_TIMEOUT)
            code, out, err = done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            code, out, err = None, "", "timed out after %d s" % RUN_TIMEOUT
        after, folders_after = self.walk()
        stubs = read_bytes(env["BOARD_ISO_STUB_LOG"]) or b""
        leaks = self.disk_leaks(before, after) + self.output_leaks(out + "\n" + err)
        # the access itself: a decoy the run opened, listed or globbed, whether or not anything of it shows
        seen, touched, faults, loose = self.trace_read(trace)
        # traced: the reader is python, it wrote its start line, so did every python it started, and any other child
        # of its is on the allowed list
        traced, shown = reader.traced and seen and not loose, set(rel for _, rel in leaks)
        leaks += [("%s %s" % (verb, rel), rel) for verb, rel in touched if rel not in shown]
        own = sorted(set(os.path.basename(path) for path, marker in self.own.items() if marker in out + err))
        self.restore(before, after, folders_before, folders_after)
        note = " (stub called: %s)" % stubs.decode("utf-8", "replace").strip().replace("\r\n", "; ") if stubs.strip() else ""
        note += " (a child no trace followed: %s)" % ", ".join(loose) if loose else ""
        if faults:
            return Result("error", "the file trace failed: %s%s" % ("; ".join(faults)[:160], note), out, own, traced=traced)
        failed = code is None or code not in reader.ok or TRACEBACK in err
        last = [line.strip() for line in (err or out).splitlines() if line.strip()]
        ending = "exit %s: %s" % (code, (last[-1] if last else "no output")[:160])
        ended = ("timeout" if code is None else code) if failed else None
        if leaks:
            # a reader that leaks and then fails is still a leak row; its line says so, since it was read only so far
            note += " (and the reader ended with %s)" % ending if failed else ""
            return Result("leak", "; ".join(text for text, _ in leaks if text) + note, out, own,
                          sorted(set(rel for _, rel in leaks)), traced=traced, ended=ended)
        if failed:
            return Result("error", ending + note, out, own, traced=traced, ended=ended)
        return Result("clean", note, out, own, traced=traced)

    def close(self):
        remove_tree(self.root)


def block_names_unread(kit, names):
    """The names of the candidate block that no kit script names, tests left out."""
    text = []
    for base, folders, files in os.walk(kit):
        folders[:] = [f for f in folders if f not in (".git", "tests", "__pycache__", "docs")]
        for name in files:
            if name.lower().endswith((".py", ".ps1", ".sh")):
                text.append((read_bytes(os.path.join(base, name)) or b"").decode("utf-8", "replace"))
    text = "\n".join(text)
    return [name for name in names if not re.search(r"\b%s\b" % re.escape(name), text)]


def run_situation(kit, situation, keep):
    name, where, role, block = situation
    # the settings file that carries the block sits in P wherever an installer wrote it: every situation but the last
    sandbox = Sandbox(kit, where, installed=block or where != "project")
    try:
        # a reader of the ledger script gets the same environment in every situation; the working folder is the
        # situation's, since the script sets none and a step inherits the one the scheduler started it in
        env = sandbox.env_for(role, block)
        return [(reader.rid, name, sandbox.run(reader, sandbox.outside_env(reader) if reader.outside else env))
                for reader in run_list(kit, sandbox.tokens())]
    finally:
        if keep:
            print("kept %s: %s" % (name, sandbox.root))
        else:
            sandbox.close()


VERDICTS = ("clean", "untraced", "leak", "store", "error", "refused")
SCRIPT_NAME = re.compile(r"[\w.-]+\.(?:ps1|sh|py)\b")


def listed(table):
    """{(reader, situation): [what each row says of it]}: (kind, files, reason) of a leak, (reason,) of an error."""
    found = {}
    for row in table:
        for name in row[1]:
            found.setdefault((row[0], name), []).append(row[2:])
    return found


def judge(rows, expected_leaks, expected_errors, expected_ends=(), show=print):
    """One line per run, rows being [(reader, situation, Result)], then the count: (exit code, count)."""
    leaks, errors = listed(expected_leaks), listed(expected_errors)
    ends = dict(((rid, name), code) for rid, names, code, _ in expected_ends for name in names)
    said = lambda code: "none" if code is None else "exit %s" % code
    count = dict.fromkeys(VERDICTS + LEAK_KINDS, 0)
    new, gone, unlisted, healed, off, carried, blind, theirs = [], [], [], [], [], set(), set(), 0
    for rid, name, result in rows:
        key, tag, verdict = (rid, name), "", result.verdict
        carried.add(key)
        entries = leaks.get(key, [])
        if verdict == "leak":
            under = [next((kind for kind, names, _ in entries if any(fnmatch.fnmatchcase(path, n) for n in names)), None)
                     for path in result.files]
            stray = [path for path, kind in zip(result.files, under) if kind is None]
            idle = [n for _, names, _ in entries for n in names
                    if not any(fnmatch.fnmatchcase(path, n) for path in result.files)]
            if not entries:
                tag = " | NOT LISTED: " + ", ".join(result.files)
            elif stray:
                tag = " | LISTED, BUT NOT FOR " + ", ".join(stray)
            else:
                tag = " | listed " + "+".join(sorted(set(under)))
                if set(under) == set([STORE]):
                    verdict = "store"  # printed and listed, never counted as a leak
                for kind in set(under) - set([STORE]):
                    count[kind] += 1
            if not entries or stray:
                new.append(key)
            if idle:
                tag += " | LISTED FILE DID NOT LEAK: " + ", ".join(idle)
                gone.append(key)
        elif entries:
            tag = " | LISTED LEAK DID NOT HAPPEN: " + ", ".join(n for _, names, _ in entries for n in names)
            gone.append(key)
        # both ways: a reader that fails on a leak row was read only so far, and its exit code is on the list
        ended = result.ended if result.verdict == "leak" else None
        if ended != ends.get(key):
            tag += " | EXIT OFF THE LIST: ended with %s, listed %s" % (said(ended), said(ends.get(key)))
            off.append(key)
        if verdict == "error":
            tag += " | listed" if key in errors else " | NOT LISTED"
            if key not in errors:
                unlisted.append(key)
        elif key in errors:
            tag += " | LISTED ERROR DID NOT HAPPEN"
            healed.append(key)
        if not result.traced and verdict in ("clean", "leak", "store"):
            # no file trace followed this run: said beside its visible verdict, and never inside the clean count
            script = SCRIPT_NAME.search(rid)
            blind.add(script.group(0) if script else rid)
            if verdict == "clean":
                verdict = "untraced"
            else:
                tag, theirs = tag + " | untraced", theirs + 1
        count[verdict] += 1
        show("%s | %s | %s%s" % (rid, name, result.text(verdict), tag))
    # the list is held both ways: a listed leak or error of a run that was never made is as wrong as one not listed
    orphans = sorted(key for key in set(leaks) | set(errors) | set(ends) if key not in carried)
    for rid, name in orphans:
        show("%s | %s | LISTED, BUT NO SUCH RUN" % (rid, name))
    runs = sum(count[verdict] for verdict in VERDICTS)
    show("kinds: " + "; ".join("%s, %s" % pair for pair in KINDS))
    show("runs %d: clean %d, untraced %d (%s; %d more runs of theirs are among the leaks and stores), leaks %d (listed %d, "
         "not listed %d; %s), stores S %d, listed leaks that did not happen %d, listed errors that did not happen %d, "
         "listed with no run %d, errors %d (not listed %d), refused %d, exit codes off the list %d"
         % (runs, count["clean"], count["untraced"], ", ".join(sorted(blind)) or "none", theirs, count["leak"],
            count["leak"] - len(new), len(new), ", ".join("%s %d" % (kind, count[kind]) for kind in LEAK_KINDS),
            count["store"], len(gone), len(healed), len(orphans), count["error"], len(unlisted), count["refused"], len(off)))
    # unmeasured is never green: an error nobody listed and a reader that was refused fail the run as a new leak does
    return (1 if new or gone or unlisted or healed or orphans or off or count["refused"] else 0), count


def collect(kit, keep):
    """[(reader, situation, Result)], reader by reader, each in every situation."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(SITUATIONS)) as pool:
        columns = list(pool.map(lambda s: run_situation(kit, s, keep), SITUATIONS))
    return [column[index] for index in range(len(columns[0])) for column in columns]


def matrix(kit, keep):
    rows = collect(kit, keep)
    unread = block_names_unread(kit, sorted(candidate_block("e", "p")))
    print("block variables no kit script names: %s" % (", ".join(unread) or "none"))
    nightly = [reader.rid[len("nightly "):] for reader in nightly_readers(kit)]
    print("readers the ledger script starts, read from %s: %d (%s)" % (NIGHTLY_SCRIPT, len(nightly), ", ".join(nightly)))
    # the pins' sandbox is closed by now; its tables are what the tokens are read from
    found, ran = set(code_scan(kit, shared_sandbox().tokens())), reader_files(run_list(kit))
    print("scripts holding a board default token, read from the code: %d (run %d: %s; not run %d: %s)"
          % (len(found), len(found & ran), ", ".join(sorted(found & ran)), len(found - ran), ", ".join(sorted(found - ran)) or "none"))
    return judge(rows, EXPECTED_LEAKS, EXPECTED_ERRORS, EXPECTED_ENDS)[0]


# --- pins ---------------------------------------------------------------------------------------------------------

SHARED = []


def shared_sandbox():
    if not SHARED:
        SHARED.append(Sandbox(KIT, "worktree"))
    return SHARED[0]


def stub_reader(sandbox, name, body):
    """A reader of this file's own making: python, started the way a tool is."""
    path = os.path.join(sandbox.tmp, name)
    put(path, "import os\nhome = os.path.expanduser('~')\nboard = os.path.join(home, '.claude')\n" + body)
    return Reader("stub " + name, ("python",), argv=["{py}", path], script=path)


class BoardIsolationSandboxHomeTest(unittest.TestCase):
    def test_a_reader_that_resolves_the_real_home_is_refused_and_never_started(self):
        sandbox = shared_sandbox()
        ran = os.path.join(sandbox.tmp, "home-stub-ran.txt")
        reader = stub_reader(sandbox, "home_stub.py", "open(%r, 'w').write(home)\nprint('ran')\n" % ran)
        real = os.path.expanduser("~")
        self.assertFalse(sandbox.inside(real))
        drive, tail = os.path.splitdrive(real)
        env = dict(sandbox.env_for("lane", False), USERPROFILE=real, HOME=real, HOMEDRIVE=drive, HOMEPATH=tail)
        result = sandbox.run(reader, env)
        self.assertEqual(result.verdict, "refused")
        self.assertIn("outside the sandbox", result.detail)
        self.assertFalse(os.path.exists(ran))
        self.assertEqual(result.stdout, "")

    def test_the_same_reader_runs_when_its_home_is_the_sandbox(self):
        sandbox = shared_sandbox()
        reader = stub_reader(sandbox, "home_stub_inside.py", "print('ran in ' + home)\n")
        result = sandbox.run(reader, sandbox.env_for("lane", False))
        self.assertEqual(result.verdict, "clean")
        self.assertTrue(sandbox.inside(result.stdout.strip()[len("ran in "):]))


class BoardIsolationDetectsWriteTest(unittest.TestCase):
    def test_an_append_to_the_decoy_landings_is_a_leak_and_is_undone(self):
        sandbox = shared_sandbox()
        landings = os.path.join(sandbox.claude, "landings.md")
        reader = stub_reader(sandbox, "write_stub.py", "open(os.path.join(board, 'landings.md'), 'a').write('| a row |\\n')\n")
        result = sandbox.run(reader, sandbox.env_for("lane", False))
        self.assertEqual(result.verdict, "leak")
        self.assertIn("writes decoy home/.claude/landings.md [%s]" % sandbox.decoys[landings], result.detail)
        self.assertEqual(read_bytes(landings), sandbox.pristine[landings])

    def test_a_new_file_in_the_home_is_a_leak_and_one_in_the_project_is_not(self):
        sandbox = shared_sandbox()
        outside = stub_reader(sandbox, "create_stub.py", "open(os.path.join(board, 'new-board-file.md'), 'w').write('x')\n")
        result = sandbox.run(outside, sandbox.env_for("lane", False))
        self.assertEqual((result.verdict, result.detail), ("leak", "creates home/.claude/new-board-file.md"))
        inside = stub_reader(sandbox, "own_stub.py", "open(os.path.join(os.getcwd(), 'own-file.md'), 'w').write('x')\n")
        self.assertEqual(sandbox.run(inside, sandbox.env_for("lane", False)).verdict, "clean")


class BoardIsolationDetectsReadTest(unittest.TestCase):
    def test_printing_the_decoy_register_is_a_leak(self):
        sandbox = shared_sandbox()
        rulings = os.path.join(sandbox.claude, "rulings.md")
        reader = stub_reader(sandbox, "read_stub.py", "print(open(os.path.join(board, 'rulings.md')).read())\n")
        result = sandbox.run(reader, sandbox.env_for("lane", False))
        self.assertEqual(result.verdict, "leak")
        self.assertEqual(result.detail, "prints home/.claude/rulings.md [%s]" % sandbox.decoys[rulings])

    def test_decoy_text_written_into_the_project_with_no_output_is_a_leak(self):
        sandbox = shared_sandbox()
        rulings = os.path.join(sandbox.claude, "rulings.md")
        reader = stub_reader(sandbox, "copy_stub.py", "text = open(os.path.join(board, 'rulings.md')).read()\n"
                                                      "open(os.path.join(os.getcwd(), 'sheet.md'), 'w').write(text)\n")
        result = sandbox.run(reader, sandbox.env_for("lane", False))
        self.assertEqual(result.detail, "copies home/.claude/rulings.md [%s] into repo/worktree-beta/beta-%s/sheet.md"
                         % (sandbox.decoys[rulings], LANE))
        self.assertFalse(os.path.exists(os.path.join(sandbox.worktree, "sheet.md")))

    def test_naming_a_decoy_path_is_a_leak_and_the_second_board_is_not(self):
        sandbox = shared_sandbox()
        brief = os.path.join(sandbox.claude, "briefs", "orchestrator-resume-2026-10-12.md")
        reader = stub_reader(sandbox, "name_stub.py", "print('Resume brief: ' + os.path.join(board, 'briefs', %r))\n" % os.path.basename(brief))
        result = sandbox.run(reader, sandbox.env_for("lane", False))
        self.assertEqual(result.detail, "names home/.claude/briefs/orchestrator-resume-2026-10-12.md [%s]" % sandbox.decoys[brief])
        # evidence-beta starts with the first project's folder name and is not it
        own = stub_reader(sandbox, "own_read_stub.py", "print(open(%r).read() + %r)\n" % (os.path.join(sandbox.ev, "rulings.md"), posix(sandbox.ev)))
        result = sandbox.run(own, sandbox.env_for("lane", False))
        self.assertEqual((result.verdict, result.own), ("clean", ["rulings.md"]))


class BoardIsolationRunsEveryWiredHookTest(unittest.TestCase):
    def test_every_command_of_the_settings_file_is_in_the_run_list(self):
        with open(os.path.join(KIT, "claude", "settings.json"), encoding="utf-8") as handle:
            raw = handle.read()
        # a second road to the commands: the text of the file, not the walk the run list is built from
        in_file = sorted(json.loads(literal) for literal in re.findall(r'"command"\s*:\s*("(?:[^"\\]|\\.)*")', raw))
        in_runs = sorted(reader.command for reader in run_list(KIT) if reader.command)
        self.assertGreater(len(in_file), 20)
        self.assertEqual(in_runs, in_file)
        self.assertEqual(len(set(reader.rid for reader in run_list(KIT))), len(run_list(KIT)))

    def test_every_script_the_seat_names_has_a_call(self):
        named = seat_scripts(KIT)
        self.assertIn("row.sh", named)
        runnable = set(os.path.basename(reader.script) for reader in tool_readers(KIT) if reader.argv)
        self.assertEqual([name for name in named if name not in runnable], [])


def judged(rows, leaks=(), errors=(), ends=()):
    """(exit code, count, lines) of the verdict on these rows against these lists, nothing printed."""
    lines = []
    code, count = judge(rows, leaks, errors, ends, show=lines.append)
    return code, count, lines


class BoardIsolationUnlistedErrorFailsTest(unittest.TestCase):
    def test_a_reader_that_crashes_fails_the_run_until_its_error_is_listed(self):
        sandbox = shared_sandbox()
        crash = stub_reader(sandbox, "crash_stub.py", "raise RuntimeError('the reader broke')\n")
        result = sandbox.run(crash, sandbox.env_for("lane", False))
        self.assertEqual(result.verdict, "error")
        code, count, lines = judged([(crash.rid, "pin", result)])
        self.assertEqual((code, count["error"], count["clean"]), (1, 1, 0))
        self.assertTrue(lines[0].endswith("| NOT LISTED"), lines[0])
        self.assertEqual(judged([(crash.rid, "pin", result)], errors=((crash.rid, ("pin",), "listed by the pin"),))[0], 0)

    def test_a_reader_that_runs_clean_passes(self):
        sandbox = shared_sandbox()
        quiet = stub_reader(sandbox, "quiet_stub.py", "print('nothing of a board')\n")
        code, count, _ = judged([(quiet.rid, "pin", sandbox.run(quiet, sandbox.env_for("lane", False)))])
        self.assertEqual((code, count["clean"], count["error"]), (0, 1, 0))


class BoardIsolationRefusedRunFailsTest(unittest.TestCase):
    def test_a_refused_reader_fails_the_run(self):
        sandbox = shared_sandbox()
        reader = stub_reader(sandbox, "refused_stub.py", "print('ran in ' + home)\n")
        real = os.path.expanduser("~")
        drive, tail = os.path.splitdrive(real)
        env = dict(sandbox.env_for("lane", False), USERPROFILE=real, HOME=real, HOMEDRIVE=drive, HOMEPATH=tail)
        refused = sandbox.run(reader, env)
        self.assertEqual((refused.verdict, refused.stdout), ("refused", ""))
        code, count, _ = judged([(reader.rid, "pin", refused)])
        self.assertEqual((code, count["refused"]), (1, 1))
        ran = sandbox.run(reader, sandbox.env_for("lane", False))
        self.assertEqual((ran.verdict, judged([(reader.rid, "pin", ran)])[0]), ("clean", 0))


class BoardIsolationListedFileNamedTest(unittest.TestCase):
    def test_a_listed_leaker_that_starts_changing_a_second_decoy_fails_the_run(self):
        sandbox = shared_sandbox()
        env, rid = sandbox.env_for("lane", False), "stub listed_stub.py"
        entry = ((rid, ("pin",), LACKS, ("home/.claude/landings.md",), "listed by the pin"),)
        one = "open(os.path.join(board, 'landings.md'), 'a').write('| a row |\\n')\n"
        first = sandbox.run(stub_reader(sandbox, "listed_stub.py", one), env)
        self.assertEqual(first.files, ("home/.claude/landings.md",))
        self.assertEqual(judged([(rid, "pin", first)], leaks=entry)[0], 0)
        two = one + "open(os.path.join(board, 'rulings.md'), 'a').write('- a row\\n')\n"
        second = sandbox.run(stub_reader(sandbox, "listed_stub.py", two), env)
        self.assertEqual(second.files, ("home/.claude/landings.md", "home/.claude/rulings.md"))
        code, _, lines = judged([(rid, "pin", second)], leaks=entry)
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].endswith("| LISTED, BUT NOT FOR home/.claude/rulings.md"), lines[0])

    def test_a_listed_file_that_stopped_leaking_fails_the_run(self):
        sandbox = shared_sandbox()
        rid = "stub still_stub.py"
        body = "open(os.path.join(board, 'landings.md'), 'a').write('| a row |\\n')\n"
        result = sandbox.run(stub_reader(sandbox, "still_stub.py", body), sandbox.env_for("lane", False))
        entry = ((rid, ("pin",), LACKS, ("home/.claude/landings.md", "home/.claude/law.md"), "listed by the pin"),)
        code, _, lines = judged([(rid, "pin", result)], leaks=entry)
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].endswith("| listed A | LISTED FILE DID NOT LEAK: home/.claude/law.md"), lines[0])


class BoardIsolationNoAncestorRecordTest(unittest.TestCase):
    def test_a_record_file_above_the_sandbox_root_refuses_the_start(self):
        base = os.path.realpath(tempfile.mkdtemp(prefix="board-iso-pin-"))
        try:
            self.assertEqual(ancestor_records(os.path.join(base, "deep", "root")), [])
            put(os.path.join(base, "record.json"), "{}\n")
            with self.assertRaises(SandboxRefused) as caught:
                Sandbox(KIT, "worktree", base=os.path.join(base, "deep"))
            self.assertIn(posix(os.path.join(base, "record.json")), str(caught.exception))
            self.assertEqual(os.listdir(os.path.join(base, "deep")), [])
        finally:
            remove_tree(base)


class BoardIsolationStoreKindNotCountedTest(unittest.TestCase):
    def test_a_store_run_is_listed_and_is_not_in_the_leak_count(self):
        sandbox = shared_sandbox()
        reader = stub_reader(sandbox, "store_stub.py", "folder = os.path.join(board, 'checkpoints', 'sandbox-project')\n"
                                                       "open(os.path.join(folder, 'pin-note.md'), 'w').write('x')\n")
        result = sandbox.run(reader, sandbox.env_for("lane", False))
        note = "home/.claude/checkpoints/sandbox-project/pin-note.md"
        self.assertEqual((result.verdict, result.files), ("leak", (note,)))
        row = [(reader.rid, "pin", result)]
        entry = lambda kind: ((reader.rid, ("pin",), kind, (CHECKPOINTS + "*",), "listed by the pin"),)
        code, count, lines = judged(row, leaks=entry(STORE))
        self.assertEqual((code, count["store"], count["leak"]), (0, 1, 0))
        self.assertTrue(lines[0].endswith("| STORE creates %s | listed S" % note), lines[0])
        code, count, lines = judged(row, leaks=entry(LACKS))
        self.assertEqual((code, count["store"], count["leak"], count[LACKS]), (0, 0, 1, 1))
        self.assertTrue(lines[0].endswith("| LEAK creates %s | listed A" % note), lines[0])
        self.assertEqual(judged(row)[0], 1)  # the same write by a reader nobody listed is a leak, and fails the run


def shell_stub_reader(sandbox, name, body):
    """A reader the trace cannot follow: a PowerShell script of this file's own making, started as the wired ones are."""
    path = os.path.join(sandbox.tmp, name)
    put(path, "$board = Join-Path $env:USERPROFILE '.claude'\n" + body)
    return Reader("stub " + name, ("powershell",), script=path,
                  argv=["{ps}", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path])


class BoardIsolationSilentReadIsALeakTest(unittest.TestCase):
    def silent(self, name, body):
        sandbox = shared_sandbox()
        return sandbox.run(stub_reader(sandbox, name, body), sandbox.env_for("lane", False))

    def test_a_reader_that_opens_a_decoy_and_prints_nothing_is_a_leak_naming_the_file(self):
        result = self.silent("silent_open_stub.py", "text = open(os.path.join(board, 'rulings.md')).read()\n")
        self.assertEqual((result.verdict, result.detail, result.stdout),
                         ("leak", "opens home/.claude/rulings.md", ""))
        self.assertEqual((result.files, result.traced), (("home/.claude/rulings.md",), True))

    def test_a_listing_a_scan_and_a_glob_are_leaks_naming_what_they_reach(self):
        for name, body, detail in (
            ("silent_list_stub.py", "names = os.listdir(os.path.join(board, 'briefs'))\n", "lists home/.claude/briefs"),
            ("silent_scan_stub.py", "names = [entry.name for entry in os.scandir(os.path.join(board, 'decisions'))]\n",
             "lists home/.claude/decisions"),
            # a glob with no wildcard lists nothing and opens nothing: the glob event alone names its target
            ("silent_glob_stub.py", "import glob\nnames = glob.glob(os.path.join(board, 'pulse.md'))\n",
             "globs home/.claude/pulse.md"),
        ):
            result = self.silent(name, body)
            self.assertEqual((result.verdict, result.detail, result.stdout), ("leak", detail, ""), name)

    def test_a_reader_that_opens_its_own_board_alone_stays_clean_and_traced(self):
        own = os.path.join(shared_sandbox().ev, "rulings.md")
        result = self.silent("silent_own_stub.py", "text = open(%r).read()\n" % own)
        self.assertEqual((result.verdict, result.files, result.traced), ("clean", (), True))


class BoardIsolationListedFilesExactTest(unittest.TestCase):
    def test_a_listed_reader_that_silently_opens_a_decoy_its_entry_does_not_name_fails_the_run(self):
        sandbox = shared_sandbox()
        rid = "stub exact_stub.py"
        body = ("open(os.path.join(board, 'landings.md'), 'a').write('| a row |\\n')\n"
                "text = open(os.path.join(board, 'rulings.md')).read()\n")
        result = sandbox.run(stub_reader(sandbox, "exact_stub.py", body), sandbox.env_for("lane", False))
        self.assertEqual(result.files, ("home/.claude/landings.md", "home/.claude/rulings.md"))
        entry = lambda *files: ((rid, ("pin",), NO_PANE_BLOCK, files, "listed by the pin"),)
        code, _, lines = judged([(rid, "pin", result)], leaks=entry("home/.claude/landings.md"))
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].endswith("| LISTED, BUT NOT FOR home/.claude/rulings.md"), lines[0])
        both = entry("home/.claude/landings.md", "home/.claude/rulings.md")
        self.assertEqual(judged([(rid, "pin", result)], leaks=both)[0], 0)


class BoardIsolationUntracedNotCleanTest(unittest.TestCase):
    def test_a_shell_reader_that_reads_a_decoy_and_shows_nothing_is_untraced_and_never_clean(self):
        sandbox = shared_sandbox()
        reader = shell_stub_reader(sandbox, "silent_stub.ps1",
                                   "$text = Get-Content -LiteralPath (Join-Path $board 'rulings.md') -Raw\n")
        result = sandbox.run(reader, sandbox.env_for("lane", False))
        self.assertEqual((result.verdict, result.traced, result.stdout.strip()), ("clean", False, ""))
        code, count, lines = judged([(reader.rid, "pin", result)])
        self.assertEqual((code, count["clean"], count["untraced"]), (0, 0, 1))
        self.assertEqual(lines[0], "stub silent_stub.ps1 | pin | untraced (by the visible rules: clean)")
        self.assertIn("clean 0, untraced 1 (silent_stub.ps1; 0 more runs", lines[-1])

    def test_a_shell_reader_that_prints_a_decoy_keeps_its_visible_verdict_with_untraced_beside_it(self):
        sandbox = shared_sandbox()
        reader = shell_stub_reader(sandbox, "loud_stub.ps1", "Get-Content -LiteralPath (Join-Path $board 'rulings.md')\n")
        result = sandbox.run(reader, sandbox.env_for("lane", False))
        self.assertEqual((result.verdict, result.files, result.traced), ("leak", ("home/.claude/rulings.md",), False))
        code, count, lines = judged([(reader.rid, "pin", result)])
        self.assertEqual((code, count["leak"], count["untraced"], count["clean"]), (1, 1, 0, 0))
        self.assertTrue(lines[0].endswith("| NOT LISTED: home/.claude/rulings.md | untraced"), lines[0])
        self.assertIn("untraced 0 (loud_stub.ps1; 1 more runs", lines[-1])

    def test_a_shell_reader_that_starts_python_is_untraced_though_the_child_wrote_its_start_line(self):
        sandbox = shared_sandbox()
        reader = shell_stub_reader(sandbox, "mixed_stub.ps1", "& python -c 'import sys'\n")
        result = sandbox.run(reader, sandbox.env_for("lane", False))
        self.assertEqual((result.verdict, result.traced), ("clean", False))
        self.assertEqual(judged([(reader.rid, "pin", result)])[1]["untraced"], 1)
        # the same child alone, with no shell around it, is traced: the start line is there to be read
        alone = stub_reader(sandbox, "mixed_alone_stub.py", "import sys\n")
        self.assertTrue(sandbox.run(alone, sandbox.env_for("lane", False)).traced)

    def test_a_python_reader_that_shows_nothing_and_opens_no_decoy_is_clean(self):
        sandbox = shared_sandbox()
        quiet = stub_reader(sandbox, "traced_quiet_stub.py", "print('nothing of a board')\n")
        code, count, lines = judged([(quiet.rid, "pin", sandbox.run(quiet, sandbox.env_for("lane", False)))])
        self.assertEqual((code, count["clean"], count["untraced"]), (0, 1, 0))
        self.assertIn("clean 1, untraced 0 (none; 0 more runs", lines[-1])


class BoardIsolationNightlyListFromScriptTest(unittest.TestCase):
    def script(self):
        with open(os.path.join(KIT, *NIGHTLY_SCRIPT.split("/")), encoding="utf-8-sig") as handle:
            return handle.read()

    def readers(self, text):
        copy = os.path.join(shared_sandbox().tmp, "nightly-copy.ps1")
        put(copy, text)
        try:
            return nightly_readers(KIT, copy)
        finally:
            os.remove(copy)

    def test_the_run_list_holds_every_tool_the_script_names_and_each_has_a_call(self):
        # a second road to the names: every quoted python file in the script, not the two shapes the list is read from
        quoted = sorted(set(re.findall(r"'([A-Za-z][\w.-]*\.py)'", self.script())))
        in_runs = sorted(reader.rid[len("nightly "):] for reader in run_list(KIT) if reader.outside)
        self.assertEqual(in_runs, quoted)
        for name in ("train-wait.py", "rework-shape.py", "pulse.py", "pulse-escalate.py", "ledger-day.py",
                     "worktree-sweep.py", "evidence-retention.py"):
            self.assertIn(name, in_runs)
        self.assertEqual([reader.rid for reader in run_list(KIT) if reader.outside and not reader.argv], [])

    def test_a_tool_added_to_the_script_joins_the_run_list_and_one_removed_leaves_it(self):
        text = self.script()
        before = [reader.rid for reader in self.readers(text)]
        self.assertIn("nightly rework-shape.py", before)
        added = self.readers(text + "\n$newPy = Join-Path $scriptDir 'new-board-reader.py'\n"
                                    "$otherPy = Resolve-KitTool 'other-board-reader.py'\n"
                                    "# $noPy = Join-Path $scriptDir 'commented-out.py'\n")
        self.assertEqual([reader.rid for reader in added],
                         before + ["nightly new-board-reader.py", "nightly other-board-reader.py"])
        removed = self.readers("\n".join(line for line in text.splitlines() if "'rework-shape.py'" not in line))
        self.assertEqual([reader.rid for reader in removed], [rid for rid in before if rid != "nightly rework-shape.py"])
        # the new tool has no call in this file: it is an error row, never a silent gap
        sandbox = shared_sandbox()
        result = sandbox.run(added[-2], sandbox.outside_env(added[-2]))
        self.assertEqual((result.verdict, result.detail),
                         ("error", "scripts/ledger-nightly.ps1 starts new-board-reader.py and this file has no call for it"))


class BoardIsolationListedErrorGoneFailsTest(unittest.TestCase):
    def test_a_listed_error_that_stopped_happening_fails_the_run(self):
        sandbox = shared_sandbox()
        rid = "stub healed_stub.py"
        entry = ((rid, ("pin",), "listed by the pin"),)
        crash = sandbox.run(stub_reader(sandbox, "healed_stub.py", "raise RuntimeError('the reader broke')\n"),
                            sandbox.env_for("lane", False))
        self.assertEqual((crash.verdict, judged([(rid, "pin", crash)], errors=entry)[0]), ("error", 0))
        quiet = sandbox.run(stub_reader(sandbox, "healed_stub.py", "print('nothing of a board')\n"),
                            sandbox.env_for("lane", False))
        code, count, lines = judged([(rid, "pin", quiet)], errors=entry)
        self.assertEqual((quiet.verdict, code, count["clean"], count["error"]), ("clean", 1, 1, 0))
        self.assertTrue(lines[0].endswith("| clean | LISTED ERROR DID NOT HAPPEN"), lines[0])
        self.assertIn("listed errors that did not happen 1,", lines[-1])


class BoardIsolationListedKeyWithoutRowFailsTest(unittest.TestCase):
    def test_a_listed_leak_and_a_listed_error_of_a_run_that_was_never_made_fail_the_run(self):
        sandbox = shared_sandbox()
        quiet = stub_reader(sandbox, "present_stub.py", "print('nothing of a board')\n")
        rows = [(quiet.rid, "pin", sandbox.run(quiet, sandbox.env_for("lane", False)))]
        self.assertEqual(judged(rows)[0], 0)
        leak = (("stub dropped_stub.py", ("pin",), LACKS, ("home/.claude/landings.md",), "listed by the pin"),)
        code, _, lines = judged(rows, leaks=leak)
        self.assertEqual(code, 1)
        self.assertIn("stub dropped_stub.py | pin | LISTED, BUT NO SUCH RUN", lines)
        self.assertIn("listed with no run 1,", lines[-1])
        # the reader is run, in another situation than the one listed
        code, _, lines = judged(rows, errors=((quiet.rid, ("another situation",), "listed by the pin"),))
        self.assertEqual(code, 1)
        self.assertIn("%s | another situation | LISTED, BUT NO SUCH RUN" % quiet.rid, lines)


CHILD_READS = "import os; print(len(open(os.path.join(os.path.expanduser('~'), '.claude', 'rulings.md')).read()))"
CHILD_RUN = ("done = subprocess.run([sys.executable%s, '-c', CHILD]%s, capture_output=True, text=True)\n"
             "print(done.stdout.strip())\n")


class BoardIsolationChildDropsTraceNotCleanTest(unittest.TestCase):
    """Review round 3, finding 1: a child that loads no trace reaches a decoy, and the run is never printed clean."""

    def child(self, name, body):
        sandbox = shared_sandbox()
        reader = stub_reader(sandbox, name, "import subprocess, sys\nCHILD = %r\n" % CHILD_READS + body)
        result = sandbox.run(reader, sandbox.env_for("lane", False))
        size = len(sandbox.pristine[os.path.join(sandbox.claude, "rulings.md")])
        self.assertEqual(result.stdout.strip(), str(size), name)  # the child did read the decoy, all of it
        return reader, result

    def test_five_children_that_drop_the_trace_are_untraced_and_never_clean(self):
        keep = "keep = dict((name, os.environ[name]) for name in ('SystemRoot', 'USERPROFILE'))\n"
        typed = ("done = subprocess.run(['cmd', '/c', 'type', os.path.join('%USERPROFILE%', '.claude', 'rulings.md')], "
                 "capture_output=True)\nprint(len(done.stdout))\n")
        python = " (a child no trace followed: python with no start line)"
        for name, body, detail in (
            ("child_isolated_stub.py", CHILD_RUN % (", '-I'", ""), python),
            ("child_no_site_stub.py", CHILD_RUN % (", '-S'", ""), python),
            ("child_no_env_stub.py", CHILD_RUN % (", '-E'", ""), python),
            ("child_rebuilt_env_stub.py", keep + CHILD_RUN % ("", ", env=keep"), python),
            ("child_shell_type_stub.py", typed, " (a child no trace followed: cmd)"),
        ):
            reader, result = self.child(name, body)
            self.assertEqual((result.verdict, result.detail, result.files, result.traced), ("clean", detail, (), False), name)
            code, count, lines = judged([(reader.rid, "pin", result)])
            self.assertEqual((code, count["clean"], count["untraced"]), (0, 0, 1), name)
            self.assertEqual(lines[0], "%s | pin | untraced (by the visible rules: clean%s)" % (reader.rid, detail))

    def test_the_same_child_with_the_trace_loaded_is_followed_and_its_open_is_a_leak(self):
        _, result = self.child("child_followed_stub.py", CHILD_RUN % ("", ""))
        self.assertEqual((result.verdict, result.detail, result.traced), ("leak", "opens home/.claude/rulings.md", True))


class BoardIsolationSpawnNamesDecoyIsALeakTest(unittest.TestCase):
    def spawn(self, name, body):
        sandbox = shared_sandbox()
        return sandbox.run(stub_reader(sandbox, name, "import subprocess\n" + body), sandbox.env_for("lane", False))

    def test_a_child_that_is_handed_a_decoy_path_is_a_leak_naming_it(self):
        result = self.spawn("spawn_named_stub.py",
                            "subprocess.run(['cmd', '/c', 'type', os.path.join(board, 'rulings.md')], capture_output=True)\n")
        self.assertEqual((result.verdict, result.files, result.traced), ("leak", ("home/.claude/rulings.md",), False))
        self.assertEqual(result.detail, "starts cmd on home/.claude/rulings.md (a child no trace followed: cmd)")

    def test_an_allowed_child_in_a_decoy_folder_is_a_leak_and_one_that_names_no_decoy_stays_clean(self):
        first = shared_sandbox().first
        result = self.spawn("spawn_folder_stub.py",
                            "subprocess.run(['git', 'status', '--short'], cwd=%r, capture_output=True)\n" % first)
        self.assertEqual((result.verdict, result.detail, result.files, result.traced),
                         ("leak", "starts git on repo/alpha", ("repo/alpha",), True))
        result = self.spawn("spawn_allowed_stub.py", "subprocess.run(['git', '--version'], capture_output=True)\n")
        self.assertEqual((result.verdict, result.detail, result.files, result.traced), ("clean", "", (), True))


class BoardIsolationReaderFromCodeScanTest(unittest.TestCase):
    def test_a_new_script_holding_a_token_is_an_error_row_until_it_is_run_or_listed(self):
        sandbox = shared_sandbox()
        tokens = sandbox.tokens()
        for token in ("rulings.md", "lane-state.json", "owner-decisions", "CLAUDE_RULINGS_FILE", "EVIDENCE_ROOT"):
            self.assertIn(token, tokens)
        coded = lambda kit: [reader for reader in run_list(kit, tokens) if reader.rid.startswith("code ")]
        # the kit as it is: every script the scan finds is run or listed, and the three of review round 3 are found
        self.assertEqual([reader.rid for reader in coded(KIT)], [])
        found = code_scan(KIT, tokens)
        for path in ("claude/tools/reports-check.py", "scripts/model-trial.py", "scripts/turn-typing.py"):
            self.assertIn(path, found)
        copy, new = os.path.join(sandbox.tmp, "kit-copy"), "scripts/new-board-reader.py"
        for name in ("claude", "scripts"):
            shutil.copytree(os.path.join(KIT, name), os.path.join(copy, name),
                            ignore=shutil.ignore_patterns("tests", "__pycache__"))
        try:
            put(os.path.join(copy, "scripts", "plain-tool.py"), "print('no board here')\n")
            self.assertEqual(coded(copy), [])
            put(os.path.join(copy, *new.split("/")), "import os\n\nREGISTER = os.path.expanduser('~/.claude/rulings.md')\n")
            readers = coded(copy)
            self.assertEqual([reader.rid for reader in readers], ["code " + new])
            result = sandbox.run(readers[0], sandbox.env_for("lane", False))
            self.assertEqual((result.verdict, result.detail), ("error", new + " holds the board default token rulings.md "
                                                               "(line 3) and is neither run nor listed as not run"))
            self.assertEqual(judged([(readers[0].rid, "pin", result)])[0], 1)
            ran, listed_out = reader_files(run_list(copy)), NOT_RUN + ((new, "listed by the pin"),)
            self.assertEqual(code_readers(copy, tokens, ran, listed_out), [])
            self.assertEqual(code_readers(copy, tokens, ran | set([new])), [])
            # the not-run list is held both ways: an entry whose script is run after all is an error row too
            stale = code_readers(copy, tokens, ran | set([new]), listed_out)
            self.assertEqual([reader.missing for reader in stale], [new + " is listed as not run and is run"])
        finally:
            remove_tree(copy)


class BoardIsolationNightlyEveryShapeTest(unittest.TestCase):
    SHAPES = (
        ('$aPy = Join-Path $scriptDir "shape-double.py"', "{kit}/scripts/"),
        ("$bPy = Join-Path -Path $scriptDir -ChildPath 'shape-child.py'", "{kit}/scripts/"),
        ('$cPy = "$scriptDir\\shape-inside.py"', "{kit}/scripts/"),
        ("$dPy = Join-Path $PSScriptRoot 'shape-root.py'", "{kit}/scripts/"),
        ('$ePy = Resolve-KitTool "shape-resolved.py"', "{home}/.claude/tools/"),
    )

    def script(self):
        with open(os.path.join(KIT, *NIGHTLY_SCRIPT.split("/")), encoding="utf-8-sig") as handle:
            return handle.read()

    def check(self, text):
        copy = os.path.join(shared_sandbox().tmp, "nightly-shape-copy.ps1")
        put(copy, text)
        try:
            return nightly_step_check(KIT, copy)
        finally:
            os.remove(copy)

    def test_a_tool_named_in_each_shape_is_found_and_a_commented_one_is_not(self):
        text = self.script()
        before = nightly_tools(text)
        self.assertEqual(len(before), 13)
        for line, road in self.SHAPES:
            name = re.search(r"shape-[a-z]+\.py", line).group(0)
            self.assertEqual(nightly_tools(text + "\n" + line + "\n"), before + [(name, road + name)], line)
            self.assertEqual(nightly_tools(text + "\n  # " + line + "\n"), before, line)

    def test_a_step_without_a_tool_and_a_tool_without_a_step_are_error_rows(self):
        text, sandbox = self.script(), shared_sandbox()
        self.assertEqual((nightly_steps(text), self.check(text)), (13, []))
        self.assertEqual(nightly_steps(text + "\n# $x = Invoke-Step -Name 'commented'\n"), 13)
        for more, steps, tools in (("$x = Invoke-Step -Name 'extra' -ArgLine $argLine", 14, 13),
                                   ("$fPy = Join-Path $scriptDir 'no-step.py'", 13, 14)):
            readers = self.check(text + "\n" + more + "\n")
            self.assertEqual([reader.rid for reader in readers], ["nightly ledger-nightly.ps1"], more)
            result = sandbox.run(readers[0], sandbox.outside_env(readers[0]))
            self.assertEqual((result.verdict, result.detail),
                             ("error", "scripts/ledger-nightly.ps1 holds %d Invoke-Step lines and names %d tools: a step "
                                       "this file reads no tool for, or a tool no step starts" % (steps, tools)))
            self.assertEqual(judged([(readers[0].rid, "pin", result)])[0], 1)


class BoardIsolationLongPathPrefixTest(unittest.TestCase):
    def test_an_open_of_a_decoy_through_the_long_path_prefix_is_a_leak(self):
        sandbox = shared_sandbox()
        body = ("long = chr(92) * 2 + '?' + chr(92) + os.path.abspath(os.path.join(board, 'rulings.md'))\n"
                "print(len(open(long).read()))\n")
        result = sandbox.run(stub_reader(sandbox, "long_path_stub.py", body), sandbox.env_for("lane", False))
        size = len(sandbox.pristine[os.path.join(sandbox.claude, "rulings.md")])
        self.assertEqual((result.verdict, result.detail, result.files, result.stdout.strip()),
                         ("leak", "opens home/.claude/rulings.md", ("home/.claude/rulings.md",), str(size)))


class BoardIsolationLeakRowExitCodeTest(unittest.TestCase):
    def test_a_listed_exit_code_that_changes_and_an_unlisted_one_fail_the_run(self):
        sandbox = shared_sandbox()
        env, rid = sandbox.env_for("lane", False), "stub ending_stub.py"
        leak = ((rid, ("pin",), LACKS, ("home/.claude/rulings.md",), "listed by the pin"),)
        end = lambda code: ((rid, ("pin",), code, "listed by the pin"),)
        body = "import sys\ntext = open(os.path.join(board, 'rulings.md')).read()\nsys.exit(%d)\n"
        three = sandbox.run(stub_reader(sandbox, "ending_stub.py", body % 3), env)
        self.assertEqual((three.verdict, three.ended), ("leak", 3))
        self.assertEqual(judged([(rid, "pin", three)], leaks=leak, ends=end(3))[0], 0)
        code, _, lines = judged([(rid, "pin", three)], leaks=leak)
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].endswith("| listed A | EXIT OFF THE LIST: ended with exit 3, listed none"), lines[0])
        self.assertTrue(lines[-1].endswith("exit codes off the list 1"), lines[-1])
        code, _, lines = judged([(rid, "pin", three)], leaks=leak, ends=end(1))
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].endswith("| listed A | EXIT OFF THE LIST: ended with exit 3, listed exit 1"), lines[0])
        zero = sandbox.run(stub_reader(sandbox, "ending_stub.py", body % 0), env)
        self.assertEqual((zero.verdict, zero.ended, judged([(rid, "pin", zero)], leaks=leak)[0]), ("leak", None, 0))
        code, _, lines = judged([(rid, "pin", zero)], leaks=leak, ends=end(3))
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].endswith("| listed A | EXIT OFF THE LIST: ended with none, listed exit 3"), lines[0])


PINS = (BoardIsolationSandboxHomeTest, BoardIsolationDetectsWriteTest, BoardIsolationDetectsReadTest,
        BoardIsolationRunsEveryWiredHookTest, BoardIsolationUnlistedErrorFailsTest, BoardIsolationRefusedRunFailsTest,
        BoardIsolationListedFileNamedTest, BoardIsolationNoAncestorRecordTest, BoardIsolationStoreKindNotCountedTest,
        BoardIsolationSilentReadIsALeakTest, BoardIsolationListedFilesExactTest, BoardIsolationUntracedNotCleanTest,
        BoardIsolationNightlyListFromScriptTest, BoardIsolationListedErrorGoneFailsTest,
        BoardIsolationListedKeyWithoutRowFailsTest, BoardIsolationChildDropsTraceNotCleanTest,
        BoardIsolationSpawnNamesDecoyIsALeakTest, BoardIsolationReaderFromCodeScanTest,
        BoardIsolationNightlyEveryShapeTest, BoardIsolationLongPathPrefixTest, BoardIsolationLeakRowExitCodeTest)


def main(argv):
    global KIT
    parser = argparse.ArgumentParser(description="Which kit readers, run for a second project, touch the first board.")
    parser.add_argument("--kit", default=KIT, help="the kit root (default: the tree this file sits in)")
    parser.add_argument("--pins-only", action="store_true")
    parser.add_argument("--keep", action="store_true", help="leave the sandboxes on disk and print where")
    args = parser.parse_args(argv)
    KIT = os.path.abspath(args.kit)
    if os.name != "nt":
        print("skipped: the wired commands are Windows shell lines")
        return 0
    if not os.path.isfile(os.path.join(KIT, "claude", "settings.json")):
        print("no claude/settings.json under %s (--kit names the kit root)" % KIT)
        return 2
    try:
        shared_sandbox()  # the first sandbox of the run: a record.json above its root stops everything here
    except SandboxRefused as refusal:
        print("REFUSED TO START: %s" % refusal)
        return 2
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case) for case in PINS)
    try:
        pins = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    finally:
        for sandbox in SHARED:
            sandbox.close()
    if args.pins_only or not pins.wasSuccessful():
        return 0 if pins.wasSuccessful() else 1
    return matrix(KIT, args.keep)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
