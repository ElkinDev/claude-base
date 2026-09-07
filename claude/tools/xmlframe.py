"""xmlframe: one compact frame from one raw uiautomator XML dump.

An agent driving a device on-screen has three ways to see it. A screenshot costs a couple
of thousand tokens and cannot be searched. The raw `uiautomator dump` costs five to sixteen
thousand tokens of nested attributes, most of them layout. This filter keeps what an agent
needs in order to act, which is the label, the resource id, the enabled and checked state
and a tap coordinate, and drops the rest: a few hundred tokens, in the region of a
twentieth of the dump it replaces, and the ratio widens as the screen gets busier.

Standard library only, python 3, no device and no adb: it reads a file already on disk.
Run it beside every dump a collector pulls and the frame is there when a later step needs
to tap something, at no extra time under whatever lock the collector holds.

    python xmlframe.py --format text ui.xml
    python xmlframe.py --format json ui.xml
    python xmlframe.py --package com.example.app ui.xml

Row grammar, one line per element, about 60 characters:

    <n> <label> [#resource-id] [!off] [*on|*sel] [~scroll(k)] [_type] [~draw] @x,y

A row that starts with "=" instead of a number is state, not an action: it is the option
already selected, which a declarative UI toolkit publishes without a click target. The
optional last line, "read ...", carries the screen text that belongs to no row, so the
numbers on the screen are not lost with the containers.

Resource ids are shortened against one package prefix, `--package`. With no option the
prefix is read from the dump itself, as the most frequent prefix before the colon, so
nothing about any application is baked in here. An id from another package keeps its
package, so a host id and an embedded library id are never confused, and an empty
`--package` shortens nothing at all, leaving every id as the dump wrote it.

Exit codes: 0 and the frame on stdout; 2 and one line on stderr when the dump is missing
or will not parse, or when the arguments are wrong. Nothing here fails in silence.
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

NEWLINE = chr(10)
BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

# Packages that own the status bar, the gesture bar and the keyboard. Their nodes are
# system chrome, not application surface, and a bench never taps them.
SYSTEM_PACKAGES = ("com.android.systemui", "com.google.android.inputmethod")

# Framework decoration ids that carry no control.
SYSTEM_IDS = ("android:id/statusBarBackground", "android:id/navigationBarBackground")

TEXT_CLASSES = ("EditText", "AutoCompleteTextView", "SearchView")

ROW_WIDTH = 60
LABEL_MAX = 28
LABEL_MIN = 14
READ_MAX = 48
# a leaf View with an id covering at least this share of the screen is a drawing or
# gesture surface rather than a decoration
SURFACE_SHARE = 20


def parse_bounds(raw):
    m = BOUNDS_RE.match(raw or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    return x1, y1, x2, y2


def area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


def centre(b):
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


def truthy(node, name):
    return node.get(name) == "true"


def collapse(s):
    return " ".join((s or "").split())


def resolve_package(nodes):
    """The package prefix to strip from resource ids, read from the dump.

    Every resource id in a dump is `<package>:id/<name>`, and one package owns almost all
    of them: the application under test. Counting the prefixes and taking the most frequent
    finds it without naming it, which is the point, because a tool that hard codes one
    package works for one project. Ties keep the prefix seen first, so the answer does not
    depend on dictionary order.
    """
    counts, order = {}, []
    for node in nodes:
        rid = node.get("resource-id", "")
        if ":" not in rid:
            continue
        head = rid.split(":", 1)[0]
        if head not in counts:
            counts[head] = 0
            order.append(head)
        counts[head] += 1
    if not order:
        return ""
    return max(order, key=lambda head: (counts[head], -order.index(head)))


def short_id(raw, prefix=""):
    """Short form of a resource id, against one package prefix.

    `com.example.app:id/btn_save` with that prefix is `btn_save`. An id from any other
    package keeps its package, `com.example.plugin:zoom_in`, because two libraries in one
    screen can each publish a `list` and a bare name would address the wrong one. An empty
    prefix is the escape hatch: nothing is shortened and the id stays as the dump wrote it,
    which is also the honest answer when no prefix could be resolved at all.
    """
    if not raw or not prefix:
        return raw
    if raw.startswith(prefix + ":"):
        rest = raw[len(prefix) + 1:]
        return rest[3:] if rest.startswith("id/") else rest
    if ":id/" in raw:
        head, name = raw.split(":id/", 1)
        return head + ":" + name if head else name
    if "/" in raw:
        return raw.rsplit("/", 1)[1]
    return raw


def is_text_field(node):
    cls = node.get("class", "")
    return any(cls.endswith(t) for t in TEXT_CLASSES)


def is_actionable(node):
    """A node an agent can act on.

    clickable, long-clickable, checkable and scrollable are the four verbs uiautomator
    publishes. A focusable text field is the fifth, because typing is an action even where
    the field is not marked clickable.
    """
    return (
        truthy(node, "clickable")
        or truthy(node, "long-clickable")
        or truthy(node, "checkable")
        or truthy(node, "scrollable")
        or (is_text_field(node) and truthy(node, "focusable"))
    )


def own_label(node):
    for attr in ("text", "content-desc"):
        v = (node.get(attr) or "").strip()
        if v:
            return v
    return ""


def is_name(v):
    """Two word characters or more.

    A one character label is an alphabet index letter and a label with no letter or digit
    is an icon glyph. Both sit first in document order inside a row of a declarative UI,
    ahead of the name that identifies it, so taking the first text blindly labels a row by
    its index letter or by the emoji on its chip. An index letter offered as an action is a
    phantom: a tap target that does nothing.
    """
    return sum(1 for c in v if c.isalnum()) >= 2


def build_index(root, parents, children, depth, d=0, parent=None):
    parents[id(root)] = parent
    depth[id(root)] = d
    kids = [c for c in list(root) if c.tag == "node"]
    children[id(root)] = kids
    for k in kids:
        build_index(k, parents, children, depth, d + 1, root)


def descendant_labels(node, children, limit=6):
    """Texts under the node, in document order, shallowest first."""
    out = []
    stack = list(children.get(id(node), []))
    while stack and len(out) < limit:
        cur = stack.pop(0)
        v = collapse(own_label(cur))
        if v:
            out.append(v)
        stack = list(children.get(id(cur), [])) + stack
    return out


def descendant_label(node, children):
    vals = descendant_labels(node, children)
    for v in vals:
        if is_name(v):
            return v
    return vals[0] if vals else ""


def sibling_label(node, parents, children):
    """Nearest text among the siblings of the node, previous first."""
    parent = parents.get(id(node))
    if parent is None:
        return ""
    sibs = children.get(id(parent), [])
    if node not in sibs:
        return ""
    i = sibs.index(node)
    for step in range(1, len(sibs)):
        for j in (i - step, i + step):
            if 0 <= j < len(sibs) and sibs[j] is not node:
                v = collapse(own_label(sibs[j])) or descendant_label(sibs[j], children)
                if v:
                    return v
    return ""


def count_descendants(node, children):
    n = 0
    stack = list(children.get(id(node), []))
    while stack:
        cur = stack.pop()
        n += 1
        stack.extend(children.get(id(cur), []))
    return n


def has_actionable(node, children):
    stack = [node]
    while stack:
        cur = stack.pop()
        if is_actionable(cur):
            return True
        stack.extend(children.get(id(cur), []))
    return False


def blank_row(b, rid, label, kind):
    return {
        "bounds": b,
        "rid": rid,
        "label": label,
        "label_source": kind,
        "kind": kind,
        "depth": 0,
        "scrollable": False,
        "enabled": True,
        "checked": False,
        "selected": kind == "peer",
        "textfield": False,
        "value": "",
        "descendants": 0,
    }


def dedupe_bounds(rows):
    """One row per rectangle.

    A click target in a declarative UI usually appears twice, once on the semantics wrapper
    and once on the view under it, with identical bounds; keeping both offers the same tap
    twice. The survivor is the one carrying the most identity: a resource id first, then a
    real label, then the deepest node.
    """
    best = {}
    for r in rows:
        key = r["bounds"]
        prev = best.get(key)
        if prev is None:
            best[key] = r
            continue
        score = (1 if r["rid"] else 0, 0 if r["label_source"] == "class" else 1, r["depth"])
        pscore = (1 if prev["rid"] else 0, 0 if prev["label_source"] == "class" else 1, prev["depth"])
        keep = r if score > pscore else prev
        keep["scrollable"] = prev["scrollable"] or r["scrollable"]
        best[key] = keep
    return list(best.values())


def selected_peers(flat, children, taken, prefix):
    """The option that is already on.

    A segmented control and a bottom navigation bar publish the selected option as a group
    with no click target inside it, while every other option in the same group is clickable.
    Nothing else marks it: the node carries selected="false" and checkable="false". So the
    rule is structural, not attribute based. Inside a container whose children are peers of
    similar area, exactly one of which holds no actionable node, that child is the one
    currently on. `tests/fixtures/xmlframe/form.xml` holds the shape: two options of equal
    size, the right one clickable and the left one not, and the left one is the option the
    screen shows selected. Without this rule the frame says nothing about the current state
    and an agent taps the option that is already active.
    """
    out = []
    for parent in flat:
        kids = children.get(id(parent), [])
        if len(kids) < 2:
            continue
        boxes = [parse_bounds(k.get("bounds")) for k in kids]
        if any(b is None or area(b) <= 0 for b in boxes):
            continue
        areas = sorted(area(b) for b in boxes)
        med = areas[len(areas) // 2]
        if med <= 0 or areas[0] * 3 < med or areas[-1] > med * 3:
            continue
        acts = [has_actionable(k, children) for k in kids]
        if not any(acts) or all(acts):
            continue
        if sum(1 for a in acts if not a) > 1:
            continue
        for k, b, a in zip(kids, boxes, acts):
            if a or b in taken:
                continue
            label = collapse(own_label(k)) or descendant_label(k, children)
            if not label or len(label) > 40:
                continue
            out.append(blank_row(b, short_id(k.get("resource-id", ""), prefix), label, "peer"))
            taken.add(b)
    return out


def surfaces(flat, children, size, taken, prefix):
    """A custom drawing or gesture surface.

    uiautomator marks a canvas neither clickable nor scrollable, so the actionable test
    misses it, and yet it is the whole point of the screen. A leaf View with a resource id
    covering at least a twentieth of the screen is that surface.
    `tests/fixtures/xmlframe/ledger.xml` holds the shape: an `android.view.View` with an
    id, no text, no content description, no child, and a quarter of the screen. Raw
    uiautomator sees the node and a naive filter drops it.
    """
    out = []
    limit = max(1, size[0] * size[1] // SURFACE_SHARE)
    for node in flat:
        if is_actionable(node) or children.get(id(node), []):
            continue
        rid = short_id(node.get("resource-id", ""), prefix)
        if not rid or not truthy(node, "enabled"):
            continue
        if not node.get("class", "").endswith(".View"):
            continue
        b = parse_bounds(node.get("bounds"))
        if b is None or area(b) < limit or b in taken:
            continue
        out.append(blank_row(b, rid, collapse(own_label(node)) or rid, "surface"))
        taken.add(b)
    return out


def collect(xml_path, prefix=None):
    """Rows, screen size, package and read line for one dump.

    `prefix` is the package whose resource ids are shortened; None resolves it from the
    dump, and "" shortens nothing, leaving every id as the dump wrote it.
    """
    hierarchy = ET.parse(xml_path).getroot()
    roots = [c for c in list(hierarchy) if c.tag == "node"]
    if not roots:
        return [], (0, 0), "", []

    parents, children, depth = {}, {}, {}
    for r in roots:
        build_index(r, parents, children, depth)

    screen = parse_bounds(roots[0].get("bounds")) or (0, 0, 0, 0)
    size = (screen[2], screen[3])
    package = roots[0].get("package", "")

    flat = []

    def walk(node):
        flat.append(node)
        for k in children.get(id(node), []):
            walk(k)

    for r in roots:
        walk(r)

    if prefix is None:
        prefix = resolve_package(flat)

    kept = []
    for node in flat:
        if not is_actionable(node):
            continue
        if node.get("package", "").startswith(SYSTEM_PACKAGES):
            continue
        if node.get("resource-id", "") in SYSTEM_IDS:
            continue
        b = parse_bounds(node.get("bounds"))
        if b is None or b[2] <= b[0] or b[3] <= b[1]:
            continue
        rid = short_id(node.get("resource-id", ""), prefix)
        label, source = collapse(own_label(node)), "own"
        if not label:
            label, source = descendant_label(node, children), "child"
        if not label:
            label, source = sibling_label(node, parents, children), "sibling"
        if not label and rid:
            label, source = rid, "rid"
        if not label:
            label = "%s [%d,%d]" % (node.get("class", "?").rsplit(".", 1)[-1], b[0], b[1])
            source = "class"
        kept.append(
            {
                "bounds": b,
                "rid": rid,
                "label": label,
                "label_source": source,
                "kind": "action",
                "depth": depth.get(id(node), 0),
                "scrollable": truthy(node, "scrollable"),
                "enabled": truthy(node, "enabled"),
                "checked": truthy(node, "checked"),
                "selected": truthy(node, "selected"),
                "textfield": is_text_field(node),
                "value": collapse(node.get("text") or ""),
                "descendants": count_descendants(node, children),
            }
        )

    kept = dedupe_bounds(kept)
    taken = set(r["bounds"] for r in kept)
    kept += selected_peers(flat, children, taken, prefix)
    kept += surfaces(flat, children, size, taken, prefix)
    kept.sort(key=lambda r: (r["bounds"][1], r["bounds"][0]))

    used = set(r["label"] for r in kept)
    reads = []
    for node in flat:
        if is_actionable(node) or children.get(id(node), []):
            continue
        if node.get("package", "").startswith(SYSTEM_PACKAGES):
            continue
        v = collapse(own_label(node))
        if not v or v in used:
            continue
        b = parse_bounds(node.get("bounds"))
        if b is None or area(b) <= 0:
            continue
        v = v if len(v) <= READ_MAX else v[: READ_MAX - 2] + ".."
        if v not in reads:
            reads.append(v)
    return kept, size, package, reads


def fit(label, used):
    """Trim a label so the row reads as one menu line.

    The row is capped at about 60 characters. The resource id is never trimmed, because it
    is the only stable handle a later step can address, so a long id eats into the label,
    and the label keeps a floor of 14 characters.
    """
    room = max(LABEL_MIN, min(LABEL_MAX, ROW_WIDTH - used))
    if len(label) <= room:
        return label
    return label[: room - 2] + ".."


def tail_of(r):
    tail = ""
    if r["rid"]:
        tail += " #" + r["rid"]
    if not r["enabled"]:
        tail += " !off"
    if r["checked"]:
        tail += " *on"
    elif r["selected"]:
        tail += " *sel"
    if r["scrollable"]:
        tail += " ~scroll(%d)" % r["descendants"]
    if r["textfield"]:
        tail += " _type"
    if r["kind"] == "surface":
        tail += " ~draw"
    return tail


def text_frame(rows, size, package, reads):
    acts = sum(1 for r in rows if r["kind"] != "peer")
    out = ["frame %s %dx%d %d actions" % (package, size[0], size[1], acts)]
    n = 0
    for r in rows:
        cx, cy = centre(r["bounds"])
        tail = tail_of(r)
        pos = " @%d,%d" % (cx, cy)
        if r["kind"] == "peer":
            head = "= "
        else:
            n += 1
            head = "%d " % n
        out.append(head + fit(r["label"], len(head) + len(tail) + len(pos)) + tail + pos)
    if reads:
        out.append("read " + " | ".join(reads))
    return NEWLINE.join(out) + NEWLINE


def json_frame(rows, size, package, reads):
    actions, state = [], []
    n = 0
    for r in rows:
        cx, cy = centre(r["bounds"])
        item = {"label": r["label"], "x": cx, "y": cy}
        if r["rid"]:
            item["id"] = r["rid"]
        if not r["enabled"]:
            item["enabled"] = False
        if r["checked"]:
            item["checked"] = True
        if r["selected"]:
            item["selected"] = True
        if r["scrollable"]:
            item["scroll"] = r["descendants"]
        if r["textfield"]:
            item["type"] = True
            item["value"] = r["value"]
        if r["kind"] == "surface":
            item["surface"] = True
        if r["kind"] == "peer":
            state.append(item)
        else:
            n += 1
            item["n"] = n
            actions.append(item)
    return json.dumps(
        {
            "package": package,
            "w": size[0],
            "h": size[1],
            "actions": actions,
            "state": state,
            "read": reads,
        },
        ensure_ascii=False,
    )


def main(argv=None):
    # Both streams are redirected into files beside the dump by any collector worth the
    # name, so both carry the same newline on every platform instead of the one the
    # platform would translate to.
    try:
        sys.stdout.reconfigure(encoding="utf-8", newline=NEWLINE)
        sys.stderr.reconfigure(encoding="utf-8", newline=NEWLINE)
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="compact frame from a uiautomator XML dump")
    ap.add_argument("xml", help="path to one uiautomator hierarchy dump")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument(
        "--package",
        default=None,
        help="package prefix stripped from resource ids; the default is the most frequent"
        " prefix in the dump, and an empty value leaves every id whole",
    )
    args = ap.parse_args(argv)
    if not os.path.isfile(args.xml):
        sys.stderr.write("xmlframe: no dump at %s%s" % (args.xml, NEWLINE))
        return 2
    try:
        rows, size, package, reads = collect(args.xml, args.package)
    except ET.ParseError as error:
        sys.stderr.write("xmlframe: %s will not parse: %s%s" % (args.xml, error, NEWLINE))
        return 2
    except OSError as error:
        sys.stderr.write("xmlframe: %s cannot be read: %s%s" % (args.xml, error, NEWLINE))
        return 2
    if args.format == "text":
        sys.stdout.write(text_frame(rows, size, package, reads))
    else:
        sys.stdout.write(json_frame(rows, size, package, reads) + NEWLINE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
