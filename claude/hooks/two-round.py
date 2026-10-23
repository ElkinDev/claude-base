"""PostToolUse hook for Write and Edit: name the two-round rule when a second BLOCK review of a piece is saved.

The two-round rule: after the second BLOCK on one piece, the piece is not resent as it is. Two BLOCKs in a row
usually mean the piece rests on a wrong premise that a third patch round will not find, so a diagnosis names
that premise, the piece is rewritten under it, and the next round is bounded to the rewrite. A periodic waste
check, if the project has one, finds the skipped diagnoses afterwards; this hook says it at the moment the
second BLOCK review is saved.

The file shapes it reads, in a folder named `reviews` (any case):
- a review is `[review-]<piece>[-r<round>]-<YYYY-MM-DD>.md`; its verdict is the first BLOCK, CLEAR, PASS or
  FAIL word in its first 40 lines, and BLOCK or FAIL counts as a block;
- a diagnosis is `[review-]diagnosis-<name>-<YYYY-MM-DD>.md`; it covers the piece when <name> is the piece,
  either one is the other with a prefix and a dash in front, or the piece is named on its first line.
Only files changed in the last 7 days count, and only reviews newer than the newest diagnosis of the piece.
The notice is due when at least two of those reviews are blocks and the newest review is one of them.

One notice per (folder, piece, newest diagnosis, N) as additionalContext. State in two-round-state.json,
pruned at 14 days; one log line per notice in two-round.log. TWO_ROUND_DIR moves both (tests and probes);
by default they sit next to this file. It applies to main sessions and subagents alike.

It never blocks: every path exits 0 and prints nothing except a notice.
"""
import glob
import json
import os
import re
import sys
import time

WINDOW_DAYS = 7                 # files older than this are not read
BLOCKS_DUE = 2                  # blocks since the last diagnosis that make the notice due
STATE_DAYS = 14
REVIEW_NAME = re.compile(r"^(?:review-)?(?P<diag>diagnosis-)?(?P<piece>.+?)(?:-r(?P<round>\d+))?-(?P<day>\d{4}-\d\d-\d\d)\.md$")
VERDICT_WORD = re.compile(r"\b(BLOCK|CLEAR|PASS|FAIL)\b")
NOTICE = ("Two-round rule: %s has %d BLOCK reviews since its last diagnosis (%s). After a second BLOCK a piece is "
          "not resent as it is. Before round %d, write diagnosis-%s-%s.md in this reviews folder naming the wrong "
          "premise, then rewrite under it and bound the next round to the rewrite.")


def base_dir():
    return os.environ.get("TWO_ROUND_DIR") or os.path.dirname(os.path.abspath(__file__))


def verdict(path):
    """The first verdict word in the first 40 lines, or None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _, line in zip(range(40), fh):
                m = VERDICT_WORD.search(line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def head_line(path):
    """The first line of a file, or an empty string."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.readline()
    except OSError:
        return ""


def diag_matches(name, piece, head):
    """True when a diagnosis covers the piece: same name, a prefix dropped on either side, or the piece named on
    the diagnosis's first line."""
    return bool(name == piece or piece.endswith("-" + name) or name.endswith("-" + piece) or
                re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(piece), head))


def piece_state(folder, piece, now):
    """(newest diagnosis mtime, [(mtime, path, verdict)] of the piece's reviews after it)."""
    reviews, diag = [], 0.0
    for path in glob.glob(os.path.join(folder, "*.md")):
        m = REVIEW_NAME.match(os.path.basename(path))
        if not m:
            continue
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        if mt < now - WINDOW_DAYS * 86400:
            continue
        if m.group("diag"):
            if diag_matches(m.group("piece"), piece, head_line(path)):
                diag = max(diag, mt)
        elif m.group("piece") == piece:
            reviews.append((mt, path.replace("\\", "/")))
    verdicts = [(mt, p, verdict(p)) for mt, p in sorted(reviews) if mt > diag]
    return diag, verdicts


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def save_state(path, state):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())   # parallel hooks never share a temp file
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except Exception:
        pass


def log_notice(session, folder, piece, n):
    try:
        line = "%s\t%s\t%s\t%s\t%d\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), session[:8], folder, piece, n)
        with open(os.path.join(base_dir(), "two-round.log"), "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def run(data):
    if not isinstance(data, dict) or data.get("tool_name") not in ("Write", "Edit"):
        return None
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("file_path")
    if not isinstance(raw, str) or not raw:
        return None
    path = raw.replace("\\", "/")
    folder, name = path.rsplit("/", 1) if "/" in path else ("", path)
    if folder.rsplit("/", 1)[-1].lower() != "reviews":
        return None
    m = REVIEW_NAME.match(name)
    if not m or m.group("diag") or not os.path.isfile(path):
        return None
    piece = m.group("piece")
    now = time.time()
    diag, verdicts = piece_state(folder, piece, now)
    blocks = [p for mt, p, v in verdicts if v in ("BLOCK", "FAIL")]
    if len(blocks) < BLOCKS_DUE or verdicts[-1][2] not in ("BLOCK", "FAIL"):
        return None
    n = len(blocks)
    key = "%s|%s|%.0f|%d" % (folder.replace("\\", "/").lower(), piece.lower(), diag, n)  # any path case is one piece
    state_path = os.path.join(base_dir(), "two-round-state.json")
    state = load_state(state_path)
    if key in state:
        return None
    state = {k: v for k, v in state.items() if isinstance(v, (int, float)) and v >= now - STATE_DAYS * 86400}
    state[key] = now
    save_state(state_path, state)
    log_notice(str(data.get("session_id") or ""), folder, piece, n)
    names = ", ".join(p.rsplit("/", 1)[-1] for p in blocks)
    text = NOTICE % (piece, n, names, n + 1, piece, time.strftime("%Y-%m-%d"))
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}


def main():
    try:
        raw = sys.stdin.buffer.read()
        if b"reviews" not in raw.lower():  # a write outside a reviews folder costs no parse
            return 0
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
    except Exception:
        return 0
    try:
        out = run(data)
        if out:
            sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
