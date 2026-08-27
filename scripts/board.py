"""The board: one small state file, one rendered page, no model needed to update it.

board.json holds the state, board.md is what the owner reads, and every command
that changes the state re-renders the page. Editing the board with a text tool
means reading it first; this replaces that read with a command.

    python board.py show
    python board.py set <lane> <field> <value>     branch tip status next owner_action
    python board.py add-landing <lane> "<text>"    keeps the last three per lane
    python board.py remove <lane>
    python board.py note "<text>"                  keeps the last ten day notes

An unknown lane is created by set and by add-landing. All times come from the
machine clock, never from a value typed into the command.

Paths: CLAUDE_BOARD_DIR holds board.json and board.md, CLAUDE_LANDINGS_FILE and
CLAUDE_LEDGER_DIR are only pointed at from the rendered page.
"""
import json
import os
import sys
from datetime import datetime

# Every path comes from the environment. Without CLAUDE_BOARD_DIR the board
# lives in the current directory, so a board is never written to another
# project's folder by accident. No lanes are seeded here: seeding is a
# matter of running `board.py set` once per lane.
DEFAULT_BOARD_DIR = ""
DEFAULT_LANDINGS = ""
DEFAULT_LEDGER = ""

FIELDS = ("branch", "tip", "status", "next", "owner_action")
HEADINGS = ("lane", "branch", "tip", "status", "next", "owner action", "last landing")
LANDINGS_PER_LANE = 3
NOTES_KEPT = 10
CELL_LIMIT = 120


def board_dir():
    return os.environ.get("CLAUDE_BOARD_DIR") or DEFAULT_BOARD_DIR or os.getcwd()


def state_path():
    return os.path.join(board_dir(), "board.json")


def render_path():
    return os.path.join(board_dir(), "board.md")


def landings_path():
    return (
        os.environ.get("CLAUDE_LANDINGS_FILE")
        or DEFAULT_LANDINGS
        or os.path.join(board_dir(), "landings.md")
    )


def ledger_dir():
    return (
        os.environ.get("CLAUDE_LEDGER_DIR")
        or DEFAULT_LEDGER
        or os.path.join(board_dir(), "ledger")
    )


def ledger_path():
    return os.path.join(ledger_dir(), "ledger.md")


def compare_path():
    return os.path.join(ledger_dir(), "compare.md")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def cell(text):
    text = " ".join(str(text or "").replace("|", "/").split())
    if not text:
        return "-"
    if len(text) > CELL_LIMIT:
        text = text[: CELL_LIMIT - 3].rstrip() + "..."
    return text


def load():
    path = state_path()
    if not os.path.isfile(path):
        return {"lanes": {}, "notes": []}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"lanes": {}, "notes": []}
    if not isinstance(data, dict):
        return {"lanes": {}, "notes": []}
    data.setdefault("lanes", {})
    data.setdefault("notes", [])
    return data


def save(data):
    data["updated"] = now()
    folder = board_dir()
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    with open(state_path(), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    text = render(data)
    with open(render_path(), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return text


def lane_of(data, name):
    lane = data["lanes"].get(name)
    if lane is None:
        lane = {field: "" for field in FIELDS}
        lane["landings"] = []
        data["lanes"][name] = lane
    lane.setdefault("landings", [])
    for field in FIELDS:
        lane.setdefault(field, "")
    return lane


def last_landing(lane):
    landings = lane.get("landings") or []
    if not landings:
        return ""
    entry = landings[-1]
    return "%s %s" % (entry.get("time", ""), entry.get("text", ""))


def render(data):
    lines = ["# Board", "", "Rendered %s." % now(), ""]
    lines.append("| " + " | ".join(HEADINGS) + " |")
    lines.append("| " + " | ".join("---" for _ in HEADINGS) + " |")
    lanes = data.get("lanes") or {}
    if lanes:
        for name, lane in lanes.items():
            row = [
                cell(name),
                cell(lane.get("branch")),
                cell(lane.get("tip")),
                cell(lane.get("status")),
                cell(lane.get("next")),
                cell(lane.get("owner_action")),
                cell(last_landing(lane)),
            ]
            lines.append("| " + " | ".join(row) + " |")
    else:
        lines.append("| " + " | ".join("-" for _ in HEADINGS) + " |")

    lines += ["", "## Day notes", ""]
    notes = data.get("notes") or []
    if notes:
        for note in reversed(notes):
            lines.append("- %s %s" % (note.get("time", ""), note.get("text", "")))
    else:
        lines.append("- none")

    lines += [
        "",
        "Landings land in `%s`. The token ledger is `%s`. Whether the current "
        "way of working is better or worse than the window it is measured "
        "against, with the deltas and what is left to optimize, is `%s`."
        % (landings_path(), ledger_path(), compare_path()),
        "",
    ]
    return "\n".join(lines)


def usage(message=""):
    if message:
        sys.stderr.write(message + "\n")
    sys.stderr.write(__doc__)
    return 2


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(argv) < 2:
        return usage()
    command = argv[1]
    args = argv[2:]
    data = load()

    if command == "show":
        sys.stdout.write(render(data))
        return 0

    if command == "set":
        if len(args) < 3:
            return usage("set needs a lane, a field and a value.")
        name, field, value = args[0], args[1].replace("-", "_"), " ".join(args[2:])
        if field not in FIELDS:
            return usage("Unknown field %r. Fields: %s." % (args[1], ", ".join(FIELDS)))
        lane_of(data, name)[field] = value
        save(data)
        print("%s %s = %s" % (name, field, value))
        return 0

    if command == "add-landing":
        if len(args) < 2:
            return usage("add-landing needs a lane and a text.")
        name, text = args[0], " ".join(args[1:])
        lane = lane_of(data, name)
        lane["landings"].append({"time": now(), "text": text})
        lane["landings"] = lane["landings"][-LANDINGS_PER_LANE:]
        save(data)
        print("%s landed: %s" % (name, text))
        return 0

    if command == "remove":
        if len(args) < 1:
            return usage("remove needs a lane.")
        name = args[0]
        if name not in data["lanes"]:
            sys.stderr.write("No lane named %r on the board.\n" % name)
            return 1
        del data["lanes"][name]
        save(data)
        print("removed %s" % name)
        return 0

    if command == "note":
        if len(args) < 1:
            return usage("note needs a text.")
        text = " ".join(args)
        data["notes"].append({"time": now(), "text": text})
        data["notes"] = data["notes"][-NOTES_KEPT:]
        save(data)
        print("noted: %s" % text)
        return 0

    return usage("Unknown command %r." % command)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
