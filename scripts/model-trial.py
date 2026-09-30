"""Model trial reader: agents of one definition, split by the model their own turns record, priced per model, per lane.

A trial runs one agent definition on a cheaper model and judges it against the same definition on the model it ran
before. This reader counts, for agents of one agentType started inside a local window:
- the model, read from assistant records that carry a usage block, "<synthetic>" skipped and a "[1m]" suffix
  stripped; an agent whose turns name two models is reported apart, never priced;
- tokens deduped by message id (the record with the largest output wins), in five buckets: input, cache write 5 m,
  cache write 1 h, cache read, output;
- the price of those buckets at the model's own list price (PRICES, dollars per million tokens, or --price);
- the lane tag of each agent from its meta.json description ("Lane <tag> ..." or a leading tag word), and per tag
  the review files in the reviews folder that name it (<tag>-...-YYYY-MM-DD.md dated inside the window) and their
  disposition, the first BLOCK or CLEAR word in the file's first six lines (a review written by hand puts a title
  first and the disposition under it).
A lane is pure when every implementing agent it had in the window (implementer or implementer-light) is of the
judged type. The quality number is blocked lanes over pure lanes: a pure lane counts once if any of its reviews in
the window is a BLOCK, so one stubborn lane weighs one. With --baseline-share p0 the reader also prints the binomial
chance of that many blocked lanes or more at p0, the noise a revert line reads. Cost per lane is over all reviewed
lanes. A tag is a lane only when some review file carries it; other agents are priced apart. It reads meta.json and
agent jsonl files only, never a main-thread transcript.

    python model-trial.py --since 2026-09-25 --until 2026-09-29 [--type implementer-light] [--baseline-share 0.24]
                          [--projects DIR] [--reviews DIR] [--price MODEL=IN,CW5M,CW1H,CR,OUT] [--row]

--projects is a Claude Code projects folder or one project folder under it, searched for */subagents/*.meta.json at
any depth (default: the projects folder of CLAUDE_CONFIG_DIR, else ~/.claude). --reviews defaults to the reviews
folder of the evidence root: EVIDENCE_ROOT, else {repo_parent}/evidence of the current directory. PRICES holds list
prices (platform.claude.com/docs/en/about-claude/pricing) and --price adds or replaces one model's row; a model with
no row is counted, never priced. Exit 0; 2 on a bad argument, a reviews folder that does not exist, or when no meta.json is found.
"""
import argparse
import collections
import datetime
import glob
import json
import math
import os
import re
import sys

PROJECTS = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude"),
                        "projects")
# dollars per million tokens: input, cache write 5 m, cache write 1 h, cache read, output
PRICES = {
    "claude-sonnet-5-5": (2.0, 2.5, 4.0, 0.20, 10.0),
    "claude-opus-5-5": (4.0, 5.0, 8.0, 0.20, 20.0),
}
IMPLEMENTING = ("implementer", "implementer-light")
TAG_RE = re.compile(r"^(?:lane\s+)?([a-z][a-z0-9]{2,9})\b", re.I)
DISPOSITION_RE = re.compile(r"\b(BLOCK|CLEAR)\b")


def local_day(ts):
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()


def read_agent(path):
    """First timestamp, the set of recorded models, and the deduped buckets of one agent jsonl."""
    first, models, best = None, set(), {}
    with open(path, encoding="utf-8", errors="replace") as h:
        for line in h:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if first is None and d.get("timestamp"):
                first = d["timestamp"]
            msg = d.get("message") or {}
            usage = msg.get("usage")
            if d.get("type") != "assistant" or not isinstance(usage, dict):
                continue
            model = (msg.get("model") or "").replace("[1m]", "")
            if not model or model == "<synthetic>":
                continue
            models.add(model)
            cc = usage.get("cache_creation") or {}
            w1h = cc.get("ephemeral_1h_input_tokens")
            w5m = cc.get("ephemeral_5m_input_tokens")
            wall = usage.get("cache_creation_input_tokens") or 0
            if w1h is None and w5m is None:
                w1h, w5m = wall, 0  # no split recorded: read as the 1 h TTL Claude Code sessions write
            row = (usage.get("input_tokens") or 0, w5m or 0, w1h or 0,
                   usage.get("cache_read_input_tokens") or 0, usage.get("output_tokens") or 0)
            key = msg.get("id") or id(d)
            if key not in best or row[4] >= best[key][4]:
                best[key] = row
    buckets = [sum(r[i] for r in best.values()) for i in range(5)]
    return first, models, buckets


def price(model, buckets):
    p = PRICES.get(model)
    return None if p is None else sum(b * q for b, q in zip(buckets, p)) / 1e6


def tag_of(description):
    m = TAG_RE.match((description or "").strip())
    return m.group(1).lower() if m else None


def reviews_for(tags, since, until, folder):
    """Per tag [review files, BLOCK files, files with no disposition word in their first six lines]."""
    out = collections.defaultdict(lambda: [0, 0, 0])
    for f in glob.glob(os.path.join(folder, "*.md")):
        name = os.path.basename(f)
        m = re.match(r"^([a-z][a-z0-9]{2,9})(?:-[a-z0-9]+)*-(\d{4}-\d{2}-\d{2})\.md$", name)
        if not m or m.group(1) not in tags:
            continue
        day = datetime.date.fromisoformat(m.group(2))
        if not (since <= day <= until):
            continue
        with open(f, encoding="utf-8", errors="replace") as h:
            head = [h.readline() for _ in range(6)]
        found = next((d.group(1) for d in map(DISPOSITION_RE.search, head) if d), None)
        out[m.group(1)][0] += 1
        out[m.group(1)][1] += found == "BLOCK"
        out[m.group(1)][2] += found is None
    return out


def default_reviews():
    root = os.environ.get("EVIDENCE_ROOT") or os.path.join(os.path.dirname(os.path.abspath(".")), "evidence")
    return os.path.join(root, "reviews")


def price_row(value):
    """An argparse type: MODEL=IN,CW5M,CW1H,CR,OUT, five dollar prices per million tokens, none negative."""
    model, sep, rest = value.partition("=")
    try:
        row = tuple(float(x) for x in rest.split(","))
    except ValueError:
        row = ()
    if not model.strip() or not sep or len(row) != 5 or min(row) < 0:
        raise argparse.ArgumentTypeError("--price reads MODEL=IN,CW5M,CW1H,CR,OUT: %r" % value)
    return model.strip(), row


def binom_tail(k, n, p):
    """P(X >= k) for X binomial(n, p)."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", required=True, help="local date, inclusive")
    ap.add_argument("--until", required=True, help="local date, inclusive")
    ap.add_argument("--type", default="implementer-light")
    ap.add_argument("--projects", default=PROJECTS)
    ap.add_argument("--reviews", default=None, help="review files folder; default <evidence root>/reviews")
    ap.add_argument("--price", action="append", default=[], type=price_row, help="MODEL=IN,CW5M,CW1H,CR,OUT")
    ap.add_argument("--baseline-share", type=float, default=None, help="blocked-lane share of the baseline, 0..1")
    ap.add_argument("--row", action="store_true")
    a = ap.parse_args(argv)
    a.reviews = a.reviews or default_reviews()
    PRICES.update(dict(a.price))
    try:
        since, until = datetime.date.fromisoformat(a.since), datetime.date.fromisoformat(a.until)
    except ValueError as e:
        print("model-trial: bad date: %s" % e, file=sys.stderr)
        return 2
    if a.baseline_share is not None and not 0 < a.baseline_share < 1:
        print("model-trial: --baseline-share must be between 0 and 1", file=sys.stderr)
        return 2
    if until < since:
        print("model-trial: --until is before --since", file=sys.stderr)
        return 2
    if not os.path.isdir(a.reviews):  # a wrong folder would read as no reviewed lane, never as a refusal
        print("model-trial: no reviews folder %s" % a.reviews, file=sys.stderr)
        return 2
    metas = glob.glob(os.path.join(a.projects, "**", "subagents", "*.meta.json"), recursive=True)
    if not metas:
        print("model-trial: no meta.json under %s" % a.projects, file=sys.stderr)
        return 2
    agents, lane_types, untagged, mixed, unread = [], collections.defaultdict(set), 0, 0, 0
    for meta in metas:
        try:
            with open(meta, encoding="utf-8") as h:
                info = json.load(h)
        except (ValueError, OSError):
            unread += 1
            continue
        if not isinstance(info, dict):
            unread += 1
            continue
        kind = info.get("agentType")
        if kind not in IMPLEMENTING and kind != a.type:
            continue
        path = meta[: -len(".meta.json")] + ".jsonl"
        if not os.path.exists(path):
            unread += 1
            continue
        try:
            first, models, buckets = read_agent(path)
        except OSError:
            unread += 1
            continue
        try:
            day = local_day(first) if first is not None else None
        except (TypeError, ValueError):
            unread += 1
            continue
        if day is None or not (since <= day <= until):
            continue
        tag = tag_of(info.get("description"))
        if tag:
            lane_types[tag].add(kind)
        if kind != a.type:
            continue
        if tag is None:
            untagged += 1
        if len(models) != 1:
            mixed += 1
            continue
        agents.append((next(iter(models)), tag, buckets))
    by_model = collections.defaultdict(list)
    for model, tag, buckets in agents:
        by_model[model].append((tag, buckets))
    # a tag is a lane only when some review file, of any date, carries it: a merge agent or a one-off job has a tag
    # word but no review, so it is priced apart and never divides per lane
    reviewed = {os.path.basename(f).split("-")[0] for f in glob.glob(os.path.join(a.reviews, "*.md"))}
    tags_all = {t for _, t, _ in agents if t in reviewed}
    revs = reviews_for(tags_all, since, until, a.reviews)
    lines = []
    for model in sorted(by_model):
        rows = [(t, b) for t, b in by_model[model] if t in reviewed]
        apart = [b for t, b in by_model[model] if t not in reviewed]
        lanes = {t for t, _ in rows}
        total = [sum(b[i] for _, b in rows) for i in range(5)]
        cost = price(model, total)
        apart_cost = price(model, [sum(b[i] for b in apart) for i in range(5)])
        pure = {t for t in lanes if lane_types[t] <= {a.type}}
        pb = sum(revs[t][1] for t in pure)
        pr = sum(revs[t][0] for t in pure)
        blocked = sum(1 for t in pure if revs[t][1])
        nodisp = sum(revs[t][2] for t in pure)
        # the same buckets at every priced row, so the cost line of a trial is read from the tool, not by hand
        at_rows = ", ".join("at %s $%.2f per lane" % (other, price(other, total) / len(lanes))
                            for other in sorted(PRICES) if other != model and lanes)
        noise = "" if a.baseline_share is None or not pure else ", P(>= %d of %d at %.2f) %.3f" % (
            blocked, len(pure), a.baseline_share, binom_tail(blocked, len(pure), a.baseline_share))
        lines.append("%s %s %s..%s: agents %d, lanes %d (%.2f agents per lane), pure lanes %d; tokens in %d, cw5m %d, "
                     "cw1h %d, cr %d, out %d; cost %s, per lane %s; pure-lane review files %d, BLOCK %d, no disposition %d; blocked lanes %d of %d (%s)%s" % (
                         a.type, model, since, until, len(rows), len(lanes), len(rows) / max(1, len(lanes)), len(pure),
                         *total, "-" if cost is None else "$%.2f" % cost,
                         "-" if cost is None or not lanes else "$%.2f (%s)" % (cost / len(lanes), at_rows), pr, pb, nodisp,
                         blocked,
                         len(pure), "%.2f" % (blocked / len(pure)) if pure else "-", noise)
                     + "; outside a reviewed lane %d agents, cost %s" % (
                         len(apart), "-" if apart_cost is None else "$%.2f" % apart_cost))
    tail = "untagged %d, mixed-model agents %d (not priced), unreadable %d, meta files %d" % (
        untagged, mixed, unread, len(metas))
    if a.row:
        print(" | ".join(lines + [tail]))
    else:
        print("\n".join(lines + [tail]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
