#!/bin/sh
# ask-planner.sh - one stateless call to the planning model, for the hours it is rationed.
#
# Reserve mode. The orchestrator pane normally is the planner. When the planning model's weekly
# meter is nearly spent, the pane switches its own runtime to a cheaper model and keeps planning
# and adjudication on the planning model through short calls like this one: a packet in, a plan
# or a verdict out. No session, no tools, no history, one call per invocation and never a retry.
#
# The call runs from a scratch directory made new for it and removed after it. The CLI always
# loads the user-level instructions and bills them as cache creation, which the smoke measured at
# 2940 tokens; a project CLAUDE.md in the working directory would be added to that on every call.
#
# usage: ask-planner.sh [-o <answer file>] [<packet file>]
#   no packet file, or `-`, reads the packet from stdin
#   exit 0  the answer is on stdout exactly as the model wrote it, one row is in the ledger
#   exit 1  the call failed: the model's own message, or the raw output, is on stderr
#   exit 2  usage: an empty packet, a packet over 65536 bytes, a packet file that cannot be read,
#           a law file that is not there, an answer file that names a directory or sits under one
#           that does not exist, or a ledger that cannot be opened for append. Everything the
#           answer needs is checked before the call: one usage line, no call, no ledger row.
#
# Environment, all optional and all with defaults below: PLANNER_MODEL, PLANNER_EFFORT,
# PLANNER_LAW (planner-law.md beside this script), PLANNER_LEDGER, PLANNER_BIN. The model and the
# effort are environment only on purpose. The planner runs at xhigh or max, and a flag would make
# lowering it a typo away. Every path the tool is given or derives, the packet, the answer file,
# the law and the ledger, is resolved against the directory the caller stood in and in the
# spelling the platform's own programs read, because the call itself runs somewhere else.
#
# Only `result` reaches stdout, so a caller can redirect it into a file and diff it. Everything
# else the CLI reports goes to the ledger row: the token counts, the api duration and the cost.
# stdout comes first, then the answer file, then the ledger row. The call is paid for by then, so
# the answer reaches the caller whatever the bookkeeping does, and a failed write is still loud:
# one line on stderr naming the path, exit 1, and the row goes in anyway with an empty
# answer_file, since the call did happen and the ledger is what records what it cost.
#
# The PowerShell twin beside this file answers the same contract.

LIMIT=65536

MODEL=${PLANNER_MODEL:-claude-fable-5-1}
EFFORT=${PLANNER_EFFORT:-xhigh}
BIN=${PLANNER_BIN:-claude}

# The call runs from an empty scratch folder and the json is read by a native python, so every
# path this tool is given or derives is resolved here, once, against the directory the caller
# stood in. A relative one would otherwise be looked for in the scratch folder, which is how a
# real call on 2026-09-03 lost its law file. On Git Bash `pwd` answers with an msys path that a
# Windows program reads as a drive relative one, so `pwd -W` is asked for first and cygpath gives
# every result its native spelling; a shell with neither is already POSIX and keeps its own.
here=$(pwd -W 2>/dev/null) || here=
[ -n "$here" ] || here=$(pwd)
here=$(printf '%s' "$here" | tr '\\' '/')
case $here in */) here=${here%/} ;; esac
CYGPATH=$(command -v cygpath 2>/dev/null) || CYGPATH=

absolute() {
    value=$(printf '%s' "$1" | tr '\\' '/')
    case $value in
        /*|[A-Za-z]:/*) : ;;
        *) value=$here/$value ;;
    esac
    if [ -n "$CYGPATH" ]; then
        # cygpath refuses a path whose parent is a file rather than a folder, and says so on
        # its own stderr. That is a caller error this tool reports itself, in the one usage
        # line it owes, so the complaint is dropped and the spelling it could not convert is
        # kept: it is already usable, and the guard further down is what answers.
        native=$("$CYGPATH" -m "$value" 2>/dev/null) || native=
        [ -z "$native" ] || value=$native
    fi
    printf '%s\n' "$value"
}

# $0 reaches a POSIX shell on Windows as the caller spelled it: relative, a backslash path, or
# both. It is resolved before dirname, which is what makes the law file beside the script findable
# from the scratch directory.
self=$(absolute "$0")
LAW=$(absolute "${PLANNER_LAW:-$(dirname "$self")/planner-law.md}")
LEDGER=$(absolute "${PLANNER_LEDGER:-$HOME/.claude/ledger/planner-calls.csv}")

base=$(absolute "${TMPDIR:-${TMP:-/tmp}}")

# A PATH name stays a PATH name. A spelled out path is resolved, because the call runs elsewhere.
case $BIN in
    */*|*\\*) BIN=$(absolute "$BIN") ;;
esac

usage() {
    echo "usage: ask-planner.sh [-o <answer file>] [<packet file>] (packet 1 to $LIMIT bytes, law file must exist)" >&2
    exit 2
}

fatal() {
    echo "$1" >&2
    exit 1
}

answer=
packet=
while [ $# -gt 0 ]; do
    case $1 in
        -o)
            [ $# -ge 2 ] || usage
            answer=$2
            shift 2
            ;;
        -)
            [ -z "$packet" ] || usage
            packet=-
            shift
            ;;
        -*) usage ;;
        *)
            [ -z "$packet" ] || usage
            packet=$1
            shift
            ;;
    esac
done

[ -z "$answer" ] || answer=$(absolute "$answer")
if [ -n "$packet" ] && [ "$packet" != "-" ]; then
    packet=$(absolute "$packet")
fi

scratch=
work=$base/ask-planner-$$
mkdir -p "$work" || fatal "cannot create the working directory $work"

clean() {
    rm -rf "$work"
    [ -z "$scratch" ] || rm -rf "$scratch"
}
trap clean EXIT
trap 'clean; exit 1' INT TERM

if [ -z "$packet" ] || [ "$packet" = "-" ]; then
    cat > "$work/packet" || usage
else
    [ -f "$packet" ] && [ -r "$packet" ] || usage
    cat "$packet" > "$work/packet" 2>/dev/null || usage
fi

bytes=$(wc -c < "$work/packet" | tr -dc '0-9')
[ -n "$bytes" ] || usage
[ "$bytes" -gt 0 ] || usage
[ "$bytes" -le "$LIMIT" ] || usage
[ -f "$LAW" ] && [ -r "$LAW" ] || usage

# The places the answer has to land are checked before the call, not after, because the call is
# money: a caller who mistyped -o, or a ledger under a path that cannot hold one, finds out for
# the price of nothing. The answer file's folder has to exist already, since a wrong -o is far
# likelier than a deliberate new tree, and the ledger is opened for append here so that a
# permission or a path problem is a usage error rather than a lost answer.
if [ -n "$answer" ]; then
    [ ! -d "$answer" ] || usage
    [ -d "$(dirname "$answer")" ] || usage
fi
mkdir -p "$(dirname "$LEDGER")" 2>/dev/null || usage
[ ! -d "$LEDGER" ] || usage
( : >> "$LEDGER" ) 2>/dev/null || usage

# Before the call, not after: an interpreter that cannot read the answer would waste the call.
# A python3 that is only a store alias answers a version probe with a non-zero code, so the
# candidate has to run, not merely resolve.
PY=
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "" >/dev/null 2>&1; then
        PY=$candidate
        break
    fi
done
[ -n "$PY" ] || fatal "no working python on PATH: ask-planner reads the CLI's json with one"

cat > "$work/parse.py" <<'PARSER'
"""Read the CLI's json envelope, hand the caller the answer, append the ledger row."""
import json
import os
import sys
import time

RAW, STATUS, MODEL, EFFORT, PACKET_BYTES, ANSWER_FILE, LEDGER = sys.argv[1:8]
HEADER = ("time,model,effort,packet_bytes,input,cache_create,cache_read,output,"
          "thinking,duration_ms,cost_usd,answer_file")


def fail(text):
    text = (text or "").strip()
    sys.stderr.write((text or "the cli returned nothing this tool could read") + "\n")
    sys.exit(1)


def cell(value):
    """One csv field. Fields are never quoted, so a comma inside one becomes a space."""
    if value is None or isinstance(value, bool):
        text = ""
    else:
        text = str(value)
    for character in (",", "\r", "\n", "\t"):
        text = text.replace(character, " ")
    return text


with open(RAW, "rb") as handle:
    raw = handle.read().decode("utf-8", "replace")

try:
    envelope = json.loads(raw)
except ValueError:
    envelope = None
if not isinstance(envelope, dict):
    fail(raw)

result = envelope.get("result")
if (STATUS != "0" or envelope.get("is_error")
        or envelope.get("subtype") != "success" or not isinstance(result, str)):
    fail(result if isinstance(result, str) and result.strip() else raw)

# stdout first, then the answer file, then the row. The call is already paid for by the time any
# of this runs, so the answer reaches the caller before any bookkeeping can fail on it.
sys.stdout.buffer.write(result.encode("utf-8"))
sys.stdout.buffer.flush()

# The folder was there before the call and the file is not created here for the first time by
# luck: if the write fails now, something took the place away mid call, so the row still goes in
# with an empty answer_file and says the call happened and the answer is not on disk.
lost = False
if ANSWER_FILE:
    try:
        with open(ANSWER_FILE, "w", encoding="utf-8", newline="") as handle:
            handle.write(result)
    except OSError as reason:
        sys.stderr.write("cannot write the answer file %s: %s\n" % (ANSWER_FILE, reason))
        ANSWER_FILE = ""
        lost = True

usage = envelope.get("usage") or {}
details = usage.get("output_tokens_details") or {}
row = [time.strftime("%Y-%m-%d %H:%M:%S"), MODEL, EFFORT, PACKET_BYTES,
       usage.get("input_tokens"), usage.get("cache_creation_input_tokens"),
       usage.get("cache_read_input_tokens"), usage.get("output_tokens"),
       details.get("thinking_tokens"), envelope.get("duration_api_ms"),
       envelope.get("total_cost_usd"), ANSWER_FILE]
try:
    fresh = not os.path.exists(LEDGER) or os.path.getsize(LEDGER) == 0
    with open(LEDGER, "a", encoding="utf-8", newline="") as handle:
        if fresh:
            handle.write(HEADER + "\n")
        handle.write(",".join(cell(value) for value in row) + "\n")
except OSError as reason:
    sys.stderr.write("cannot append the ledger row to %s: %s\n" % (LEDGER, reason))
    sys.exit(1)
if lost:
    sys.exit(1)
PARSER

# A fresh directory for each call, empty because it is new, and gone again on every way out. The
# working directory has to hold no project CLAUDE.md, since the CLI would load it and bill it as
# cache creation on every call; emptying a shared folder instead would delete files this tool did
# not create and would give two calls at the same time the same directory.
[ -n "$base" ] || fatal "no temp directory to put the scratch folder in"
parent=$base/planner-cwd
mkdir -p "$parent" || fatal "cannot create the scratch directory $parent"
scratch=$(mktemp -d "$parent/$$-XXXXXX") || fatal "cannot create a scratch directory under $parent"

# stderr is not redirected: whatever the CLI says about itself reaches the caller unchanged.
(
    cd "$scratch" || exit 1
    "$BIN" --print --model "$MODEL" --effort "$EFFORT" --tools "" \
        --no-session-persistence --permission-mode dontAsk --output-format json \
        --system-prompt-file "$LAW" < "$work/packet" > "$work/raw"
)
status=$?

"$PY" "$work/parse.py" "$work/raw" "$status" "$MODEL" "$EFFORT" "$bytes" "$answer" "$LEDGER"
verdict=$?
exit $verdict
