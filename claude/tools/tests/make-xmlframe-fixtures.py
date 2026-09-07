"""Writes the three synthetic uiautomator dumps `test-xmlframe.py` runs against.

    python make-xmlframe-fixtures.py

Nothing here touches a device. The dumps are built from a tree written by hand, with the
attribute set and the attribute order a real `uiautomator dump` emits, so the filter sees
the node shapes it sees in the field, and nothing else: no real package, no real screen,
no real text. Rerun it after editing the tree; the test suite compares against the files,
not against this script, so a regenerated fixture that moves a row reddens the suite until
the expected lines are re-derived by hand.

Three screens, one per shape the filter has a rule for:

  home    a bottom bar whose selected tab has no click target, a scrollable list of rows,
          an embedded control from another package, and system chrome that must be dropped
  form    a text field, a checked row, a segmented control with one option already on,
          and a disabled button that must be flagged rather than offered
  ledger  a drawing surface uiautomator marks as nothing, over a scrollable list of rows
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures", "xmlframe")
PKG = "com.example.app"
PLUGIN = "com.example.plugin"

# The attribute order a real dump writes, which is also the order every node below carries.
ORDER = (
    "index",
    "text",
    "resource-id",
    "class",
    "package",
    "content-desc",
    "checkable",
    "checked",
    "clickable",
    "enabled",
    "focusable",
    "focused",
    "scrollable",
    "long-clickable",
    "password",
    "selected",
    "bounds",
)
FALSE = ("checkable", "checked", "clickable", "focusable", "focused", "scrollable",
         "long-clickable", "password", "selected")


def node(cls, bounds, kids=(), **kw):
    at = {"text": "", "resource-id": "", "class": cls, "package": PKG, "content-desc": "",
          "enabled": "true", "bounds": bounds}
    for name in FALSE:
        at[name] = "false"
    for name, value in kw.items():
        at[name.replace("_", "-")] = value
    return at, list(kids)


def group(bounds, kids=(), **kw):
    return node("android.view.ViewGroup", bounds, kids, **kw)


def text(value, bounds, **kw):
    return node("android.widget.TextView", bounds, (), text=value, **kw)


def escape(value):
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def render(tree, index, out, depth):
    at, kids = tree
    at["index"] = str(index)
    attrs = " ".join('%s="%s"' % (k, escape(at[k])) for k in ORDER)
    pad = "  " * depth
    if not kids:
        out.append("%s<node %s />" % (pad, attrs))
        return
    out.append("%s<node %s>" % (pad, attrs))
    for i, kid in enumerate(kids):
        render(kid, i, out, depth + 1)
    out.append("%s</node>" % pad)


def write(name, tree):
    out = ["<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>", '<hierarchy rotation="0">']
    render(tree, 0, out, 1)
    out.append("</hierarchy>")
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out) + "\n")
    print("%s %d bytes" % (name, os.path.getsize(path)))


def row(label, amount, top):
    """One list row: a clickable group with a name and an amount under it."""
    return group("[0,%d][1080,%d]" % (top, top + 180), [
        text(label, "[40,%d][600,%d]" % (top + 30, top + 90)),
        text(amount, "[800,%d][1040,%d]" % (top + 30, top + 90)),
    ], clickable="true")


def tab(label, left, clickable):
    return group("[%d,2200][%d,2400]" % (left, left + 360),
                 [text(label, "[%d,2260][%d,2340]" % (left + 60, left + 300))],
                 clickable="true" if clickable else "false")


def home():
    return node("android.widget.FrameLayout", "[0,0][1080,2400]", [
        group("[0,0][1080,2400]", [
            text("Overview", "[40,120][500,190]"),
            node("android.widget.ImageButton", "[960,110][1060,200]",
                 resource_id=PKG + ":id/btn_settings", content_desc="Settings",
                 clickable="true", focusable="true"),
            node("android.widget.ImageButton", "[960,900][1060,990]",
                 resource_id=PLUGIN + ":id/zoom_in", content_desc="Zoom in",
                 clickable="true", focusable="true"),
            group("[0,220][1080,2000]", [
                row("Groceries", "12.50", 240),
                row("Transport", "8.00", 420),
                row("Rent and building maintenance charge", "450.00", 600),
            ], resource_id=PKG + ":id/list_items", scrollable="true"),
            group("[0,2200][1080,2400]", [
                tab("Home", 0, False),
                tab("Ledger", 360, True),
                tab("Profile", 720, True),
            ], resource_id=PKG + ":id/bottom_bar"),
        ], resource_id=PKG + ":id/home_root"),
        text("9:41", "[40,20][200,80]", resource_id="com.android.systemui:id/clock",
             package="com.android.systemui", clickable="true"),
        node("android.view.View", "[0,2340][1080,2400]",
             resource_id="android:id/navigationBarBackground", clickable="true"),
    ])


def form():
    return node("android.widget.FrameLayout", "[0,0][1080,2400]", [
        group("[0,0][1080,2400]", [
            text("New entry", "[40,120][500,190]"),
            group("[40,240][1040,360]", [
                group("[40,240][540,360]", [text("Expense", "[90,270][490,330]")]),
                group("[540,240][1040,360]", [text("Income", "[590,270][990,330]")],
                      clickable="true"),
            ], resource_id=PKG + ":id/segments"),
            node("android.widget.EditText", "[40,420][1040,560]", text="12.00",
                 resource_id=PKG + ":id/field_amount", focusable="true"),
            text("Note", "[40,620][300,680]"),
            node("android.widget.EditText", "[40,700][1040,840]",
                 resource_id=PKG + ":id/field_note", focusable="true"),
            group("[40,900][1040,1020]", [text("Repeat monthly", "[90,930][700,990]")],
                  resource_id=PKG + ":id/row_repeat", clickable="true", checkable="true",
                  checked="true"),
            node("android.widget.Button", "[40,1100][540,1220]", text="Save",
                 resource_id=PKG + ":id/btn_save", clickable="true", enabled="false"),
            node("android.widget.Button", "[540,1100][1040,1220]", text="Cancel",
                 resource_id=PKG + ":id/btn_cancel", clickable="true"),
        ], resource_id=PKG + ":id/form_root"),
    ])


def ledger():
    return node("android.widget.FrameLayout", "[0,0][1080,2400]", [
        group("[0,0][1080,2400]", [
            text("Ledger", "[40,120][400,190]"),
            node("android.view.View", "[40,240][1040,900]",
                 resource_id=PKG + ":id/chart_canvas"),
            group("[0,940][1080,2200]", [
                row("Coffee", "3.20", 960),
                row("Books", "24.90", 1140),
                row("Fuel", "60.00", 1320),
                row("Rent", "450.00", 1500),
            ], resource_id=PKG + ":id/list_rows", scrollable="true"),
            node("android.widget.Button", "[40,2240][1040,2360]", text="Export",
                 resource_id=PKG + ":id/btn_export", clickable="true"),
        ], resource_id=PKG + ":id/ledger_root"),
    ])


def main():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    write("home.xml", home())
    write("form.xml", form())
    write("ledger.xml", ledger())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
