#!/usr/bin/env python3
"""radar-discover.py: proactive discovery of rising tools, models and releases. Uses no model.

Usage:
  python radar-discover.py [--state DIR] [--out FILE] [--profile FILE] [--prices FILE]
                           [--now YYYY-MM-DD[THH:MM]] [--max-calls N] [--budget-s S] [--since-hours H]

Each run fetches GitHub search (created in 30 days; pushed in 7 days with more than 300 stars) for the
terms of radar-profile.txt, GitHub trending (daily, weekly), HN Algolia (Reddit is off: it refuses reads without an account), Hugging Face
trending models, the platform pricing page and the Claude Code npm version and CHANGELOG. It saves the
stars of every repository seen to <state>/stars-<date>.json, ranks by momentum (stars per day since the
last snapshot of an earlier date; stars over age for a repository younger than 60 days with no earlier
snapshot; the trending gain otherwise) times relevance (weighted profile terms in the description, topics
and README), and writes a "## Discover" section: it replaces that section in <evidence root>/ledger/radar-<date>.md
(EVIDENCE_ROOT, else the folder above this script; the state folder defaults to ledger/radar under it), or
appends it when absent; --out writes the section alone. RADAR_UA sets the User-Agent of the plain fetches;
the default names the script only. The price rows go to --prices (default
radar-prices.json in the --state folder; the copy beside this script is the seed) in the shape radar-flags.py reads: {"models": [{"id", "name",
input, cache_write_5m, cache_write_1h, cache_read, output}]}, USD per million tokens.

A source that fails, times out or returns HTML where JSON was expected is one "source missing" line in
the output and on stdout, never a crash. Bounds: 30 s per fetch, --max-calls GitHub calls (default 400),
--budget-s seconds for the run (default 540). Exit 0 when the section is written with every source read, 1 when
it is written with a source missing, 2 on a bad argument or an unusable profile, 3 when the output cannot be
written. Star snapshots older than 60 days are removed from the state folder.
"""
import argparse, base64, datetime as dt, json, os, re, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
import urllib.error, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = os.environ.get("RADAR_UA") or "radar-discover/1.0 (zero-token radar)"
FETCH_TIMEOUT = 30
YOUNG_DAYS, MIN_GAP_DAYS, REL_CAP, GROUP = 60, 0.25, 12, 5
PRICE_KEYS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
HN_TERMS = ("claude", "anthropic", "coding agent", "LLM agent")
SUBREDDITS = ()  # Reddit refuses reads without an account (HTTP 403); HN stays. Name subreddits to try again
HF_SORTS = ("trendingScore", "trending_score", "likes7d")
PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"
TRENDING_URL = "https://github.com/trending?since="  # sanitize-ok: tracker-github the public trending page, not an org
CHANGELOG_URL = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"


def _http(url, headers):  # replaced in tests; returns (status, content_type, text)
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", ""


def _gh_run(args):  # replaced in tests; returns (exit code, stdout, stderr)
    p = subprocess.run(["gh", "api"] + args, capture_output=True, timeout=FETCH_TIMEOUT)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def _npm_version():  # replaced in tests
    npm = shutil.which("npm")
    if not npm:
        raise FileNotFoundError("npm not on PATH")
    p = subprocess.run([npm, "view", "@anthropic-ai/claude-code", "version"], capture_output=True,
                       timeout=FETCH_TIMEOUT)
    if p.returncode != 0:
        raise RuntimeError("npm exit %d" % p.returncode)
    return p.stdout.decode("utf-8", "replace").strip()


HTTP, GH_RUN, NPM_VERSION = _http, _gh_run, _npm_version


class Ctx:
    def __init__(self, now, max_calls, budget_s):
        self.now, self.max_calls, self.t0 = now, max_calls, time.monotonic()
        self.deadline = self.t0 + budget_s
        self.gh_calls = self.http_calls = 0
        self.missing, self.lock = [], threading.Lock()

    def miss(self, name, reason):
        line = "source missing: %s (%s)" % (name, one_line(reason, 160))
        with self.lock:
            self.missing.append(line)
            print(line, flush=True)

    def over(self, name):
        if time.monotonic() > self.deadline:
            self.miss(name, "time budget reached")
            return True
        return False


def one_line(s, n=120):
    s = re.sub(r"\s+", " ", str(s)).strip().replace("|", "/")
    return s if len(s) <= n else s[: n - 3] + "..."


def looks_html(ctype, body):
    return "html" in (ctype or "").lower() or body.lstrip()[:15].lower().startswith(("<!doctype", "<html"))


def fetch_text(ctx, name, url, headers=None, want="json"):
    """Returns the body (want='text' or 'html') or parsed JSON; None plus one missing line on failure."""
    if ctx.over(name):
        return None
    ctx.http_calls += 1
    try:
        status, ctype, body = HTTP(url, headers)
    except Exception as e:  # timeout, DNS, reset: one line, never a crash
        ctx.miss(name, "%s: %s" % (type(e).__name__, e))
        return None
    if status != 200:
        ctx.miss(name, "HTTP %s%s" % (status, ", HTML" if looks_html(ctype, body) else ""))
        return None
    if want != "html" and looks_html(ctype, body):
        ctx.miss(name, "HTML where %s was expected" % want)
        return None
    if want != "json":
        return body
    try:
        return json.loads(body)
    except ValueError:
        ctx.miss(name, "malformed JSON")
        return None


def gh(ctx, name, path, params=(), accept=None, quiet=False):
    if ctx.over(name):
        return None
    with ctx.lock:
        capped = ctx.gh_calls >= ctx.max_calls
        ctx.gh_calls += 0 if capped else 1
    if capped:
        ctx.miss(name, "GitHub call cap %d reached" % ctx.max_calls)
        return None
    args = ["-X", "GET", path] + [a for k, v in params for a in ("-f", "%s=%s" % (k, v))]
    if accept:
        args += ["-H", "Accept: " + accept]
    try:
        code, out, err = GH_RUN(args)
    except Exception as e:
        ctx.miss(name, "%s: %s" % (type(e).__name__, e))
        return None
    if code != 0:
        if not quiet:
            ctx.miss(name, (err.strip().splitlines() or ["gh exit %d" % code])[-1])
        return None
    try:
        return json.loads(out)
    except ValueError:
        ctx.miss(name, "malformed JSON from gh")
        return None


# ---------- profile, relevance, momentum ----------

def load_profile(path):
    terms, bad, seen = [], [], set()
    for n, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\d+(?:\.\d+)?)\s+(\S.*)$", line)
        if not m or not 0 < float(m.group(1)) <= 5:
            bad.append("profile line %d ignored: %s" % (n, one_line(line, 60)))
            continue
        key = " ".join(re.split(r"[-\s_.]+", m.group(2).strip().lower()))
        if key in seen:  # a repeated term would count its weight twice and waste an OR slot
            bad.append("profile line %d ignored: repeated term %s" % (n, one_line(m.group(2), 60)))
            continue
        seen.add(key)
        terms.append((float(m.group(1)), m.group(2).strip()))
    return terms, bad


def term_rx(term):
    parts = [re.escape(p) for p in re.split(r"[-\s_.]+", term.strip().lower()) if p]
    return re.compile(r"(?<![a-z0-9])" + r"[-_\s.]?".join(parts) + r"(?![a-z0-9])")


def relevance(text, profile):
    low = (text or "").lower()
    hit = [(w, t) for w, t in profile if term_rx(t).search(low)]
    return min(sum(w for w, _ in hit), REL_CAP), [t for _, t in hit]


def velocity(stars_now, now, created=None, prev=None, trend_gain=None, trend_days=1):
    """Stars per day and its basis. prev = (stars, when) from the newest snapshot of an earlier date."""
    if prev is not None:
        days = (now - prev[1]).total_seconds() / 86400
        if days >= MIN_GAP_DAYS:
            return (stars_now - prev[0]) / days, "snapshot"
    if created is not None:
        age = (now - created).total_seconds() / 86400
        if age < YOUNG_DAYS:
            return stars_now / max(age, 1.0), "bootstrap"
    if trend_gain is not None:
        return trend_gain / trend_days, "trending"
    return 0.0, "no baseline"


def parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


# ---------- parsers ----------

def parse_trending(html):
    rows = []
    for block in html.split('class="Box-row"')[1:]:
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([\w.-]+/[\w.-]+)"', block)
        if not m:
            continue
        name = m.group(1)
        st = re.search(r'href="/%s/stargazers"[^>]*>(?:\s|<svg.*?</svg>)*([\d,]+)\s*</a>' % re.escape(name),
                       block, re.S)
        gain = re.search(r"([\d,]+)\s+stars\s+(today|this week|this month)", block)
        desc = re.search(r'<p class="[^"]*color-fg-muted[^"]*">(.*?)</p>', block, re.S)
        rows.append({"name": name, "stars": int(st.group(1).replace(",", "")) if st else None,
                     "gain": int(gain.group(1).replace(",", "")) if gain else None,
                     "desc": one_line(re.sub(r"<[^>]+>", "", desc.group(1))) if desc else ""})
    return rows


def model_id(cell):
    name = re.sub(r"\s*\(.*$", "", re.sub(r"<[^>]+>", "", cell)).strip()
    return "-".join(re.split(r"[\s.]+", name.lower())), name


def parse_pricing(md):
    rows, on = {}, False
    for line in md.splitlines():
        if line.startswith("| Model") and "Base input" in line:
            on = True
            continue
        if on and not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not on or len(cells) < 6 or cells[0].startswith(":") or cells[0].startswith("-"):
            continue
        mid, name = model_id(cells[0])
        vals = [re.search(r"\$([\d.]+)", c) for c in cells[1:6]]
        if mid.startswith("claude-") and all(vals):
            rows[mid] = dict(zip(PRICE_KEYS, (float(v.group(1)) for v in vals)), name=name)
    return rows


def parse_changelog(md):
    out, cur = [], None
    for line in md.splitlines():
        m = re.match(r"^##\s+(\d+(?:\.\d+)+)\s*$", line)
        if m:
            cur = (m.group(1), [])
            out.append(cur)
        elif cur and line.strip().startswith("- "):
            cur[1].append(line.strip()[2:])
    return out


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


# ---------- state ----------

def read_json(ctx, path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (ValueError, OSError) as e:
        ctx.miss("state " + Path(path).name, "unreadable, ignored: %s" % type(e).__name__)
        return default


def write_json(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp%d" % os.getpid())
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    for i in range(5):  # Windows refuses a replace while another process reads the target
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 4:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.2)


def snap_get(repos, name):
    e = repos.get(name) if isinstance(repos, dict) else None
    try:
        return int(e["stars"]), parse_time(e["at"])
    except (TypeError, KeyError, ValueError):
        return None


def stage(ctx, name, fn, default, *args):
    """Runs one stage; an unexpected error is one missing line, never a crash."""
    try:
        return fn(ctx, *args)
    except Exception as e:
        ctx.miss(name, "internal error %s: %s" % (type(e).__name__, e))
        return default


def load_snapshots(ctx, state):
    snaps = []
    for p in sorted(Path(state).glob("stars-*.json")):
        m = re.fullmatch(r"stars-(\d{4}-\d{2}-\d{2})\.json", p.name)
        if m and m.group(1) < (ctx.now.date() - dt.timedelta(days=60)).isoformat():
            try:
                p.unlink()
            except OSError as e:
                ctx.miss("snapshot prune", "%s: %s" % (p.name, e))
            continue
        if m:
            d = read_json(ctx, p, {})
            snaps.append((m.group(1), d.get("repos", {}) if isinstance(d, dict) else {}))
    return snaps


# ---------- sources ----------

def search_repos(ctx, profile, pool):
    day30 = (ctx.now - dt.timedelta(days=30)).date().isoformat()
    day7 = (ctx.now - dt.timedelta(days=7)).date().isoformat()
    for i in range(0, len(profile), GROUP):
        terms = " OR ".join('"%s"' % t if " " in t else t for _, t in profile[i:i + GROUP])
        for kind, qual in (("created", "created:>=%s" % day30), ("pushed", "pushed:>=%s stars:>300" % day7)):
            d = gh(ctx, "github search %s group %d" % (kind, i // GROUP + 1), "search/repositories",
                   [("q", "%s %s" % (terms, qual)), ("sort", "stars"), ("order", "desc"), ("per_page", "100")])
            for it in (d or {}).get("items", []):
                add_repo(pool, it["full_name"], stars=it.get("stargazers_count"), created=it.get("created_at"),
                         pushed=it.get("pushed_at"), desc=it.get("description") or "",
                         topics=it.get("topics") or [], src="search-" + kind)


def add_repo(pool, name, src, **kw):
    r = pool.setdefault(name, {"name": name, "src": set(), "desc": "", "topics": []})
    r["src"].add(src)
    for k, v in kw.items():
        if v not in (None, "", []) and (k != "desc" or not r.get("desc")):
            r[k] = v


def trending(ctx, pool):
    for since, days in (("daily", 1), ("weekly", 7)):
        html = fetch_text(ctx, "github trending " + since, TRENDING_URL + since, want="html")
        rows = parse_trending(html) if html else []
        if html is not None and not rows:
            ctx.miss("github trending " + since, "no repository rows parsed")
        for r in rows:
            add_repo(pool, r["name"], src="trending-" + since, stars=r["stars"], desc=r["desc"])
            if r["gain"] is not None and "gain_days" not in pool[r["name"]]:
                pool[r["name"]].update(gain=r["gain"], gain_days=days)


def social(ctx, profile, since_hours):
    since = int(ctx.now.timestamp()) - since_hours * 3600
    rx = [term_rx(t) for _, t in profile] + [term_rx(t) for t in HN_TERMS]
    items = {}
    for term in HN_TERMS:
        url = ("https://hn.algolia.com/api/v1/search?tags=story&query=%s&numericFilters=created_at_i>%d,points>40"
               % (urllib.parse.quote(term), since))
        for h in (fetch_text(ctx, "HN " + term, url) or {}).get("hits", []):
            items[("hn", h.get("objectID"))] = ("HN", h.get("points") or 0, h.get("title") or "",
                                                 "https://news.ycombinator.com/item?id=%s" % h.get("objectID"),
                                                 h.get("url") or "")
    for sub in SUBREDDITS:
        d = fetch_text(ctx, "reddit r/" + sub, "https://www.reddit.com/r/%s/top.json?t=day&limit=25" % sub)
        for c in ((d or {}).get("data") or {}).get("children", []):
            p = c.get("data", {})
            items[("rd", p.get("id"))] = ("r/" + sub, p.get("score") or 0, p.get("title") or "",
                                          "https://www.reddit.com" + (p.get("permalink") or ""), p.get("url") or "")
    keep = [v for v in items.values() if any(r.search((v[2] + " " + v[4]).lower()) for r in rx)]
    return sorted(keep, key=lambda v: -v[1])[:5]


def hf_models(ctx):
    for sort in HF_SORTS:
        d = fetch_text(ctx, "Hugging Face sort=" + sort, "https://huggingface.co/api/models?sort=%s&limit=20" % sort)
        if isinstance(d, list) and d:
            return sort, d
    return None, []


# ---------- main ----------

def evidence_root():
    return Path(os.environ.get("EVIDENCE_ROOT") or HERE.parent)


def parse_now(s):
    if s is None:
        return dt.datetime.now().astimezone()
    s = s.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?", s):
        raise ValueError("--now wants YYYY-MM-DD or YYYY-MM-DDTHH:MM, got %r" % s)
    return dt.datetime.fromisoformat(s).astimezone()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", default=str(evidence_root() / "ledger" / "radar"))
    ap.add_argument("--out")
    ap.add_argument("--profile", default=str(HERE / "radar-profile.txt"))
    ap.add_argument("--prices", default=None)  # None: <state>/radar-prices.json, so the seed beside it never changes
    ap.add_argument("--now")
    ap.add_argument("--max-calls", type=int, default=400)
    ap.add_argument("--budget-s", type=float, default=540)
    ap.add_argument("--since-hours", type=int, default=48)
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        for k in ("state", "profile", "prices") + (("out",) if a.out is not None else ()):
            if not str(getattr(a, k)).strip():
                raise ValueError("--%s is empty" % k)
        if a.max_calls < 1 or a.budget_s <= 0 or a.since_hours < 1:
            raise ValueError("--max-calls, --budget-s and --since-hours must be positive")
        profile, bad = load_profile(a.profile)
    except (ValueError, OSError) as e:
        print("radar-discover: %s" % e, file=sys.stderr)
        return 2
    for b in bad:
        print(b)
    if not profile:
        print("radar-discover: no usable term in %s" % a.profile, file=sys.stderr)
        return 2
    ctx = Ctx(now, a.max_calls, a.budget_s)
    state = Path(a.state)
    try:
        state.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print("radar-discover: state folder: %s" % e, file=sys.stderr)
        return 3
    today = now.date().isoformat()
    lines = stage(ctx, "releases", release_lines, [], state,
                  Path(a.prices) if a.prices is not None else Path(a.state) / "radar-prices.json")
    pool = {}
    stage(ctx, "github search", search_repos, None, profile, pool)
    stage(ctx, "github trending", trending, None, pool)
    top, cut15, cut40 = stage(ctx, "ranking", rank, ([], 0.0, 0.0), state, pool, profile, today)
    soc = stage(ctx, "HN", social, [], profile, a.since_hours)
    wall = time.monotonic() - ctx.t0
    sec = render(now, ctx, wall, lines, top, soc)
    stage(ctx, "state last-run.json", lambda c, d: write_json(state / "last-run.json", d), None, {"at": now.isoformat(), "gh_calls": ctx.gh_calls, "http_calls": ctx.http_calls,
                                         "wall_s": round(wall, 1), "cut15": cut15, "cut40": cut40,
                                         "pool": len(pool), "missing": len(ctx.missing)})
    try:
        out = Path(a.out) if a.out else evidence_root() / "ledger" / ("radar-%s.md" % today)
        write_section(out, sec, alone=bool(a.out))
    except OSError as e:
        print("radar-discover: output: %s" % e, file=sys.stderr)
        return 3
    print("radar-discover: %s, %d repositories seen, %d GitHub calls, %d fetches, %.0f s, %d sources missing"
          % (out, len(pool), ctx.gh_calls, ctx.http_calls, wall, len(ctx.missing)))
    return 1 if ctx.missing else 0


def release_lines(ctx, state, prices_path):
    lines = []
    md = fetch_text(ctx, "pricing page", PRICING_URL, want="markdown")
    rows = parse_pricing(md) if md else {}
    if md is not None and len(rows) < 3:
        ctx.miss("pricing page", "%d price rows parsed, snapshot kept" % len(rows))
    if len(rows) >= 3:
        prev = read_json(ctx, state / "prices-last.json", None)
        prev = prev if isinstance(prev, dict) else None
        if prev is None:
            lines.append("Pricing baseline recorded: %d model rows (first snapshot, no diff)." % len(rows))
        for mid, r in rows.items():
            old = (prev or {}).get(mid)
            old = old if isinstance(old, dict) else None
            if prev is not None and old is None:
                lines.append("NEW model price row: %s (%s): %s per MTok" % (mid, r["name"], price_str(r)))
            elif old:
                ch = ["%s %g to %g" % (k, old.get(k), r[k]) for k in PRICE_KEYS if old.get(k) != r[k]]
                if ch:
                    lines.append("Price change: %s: %s" % (mid, ", ".join(ch)))
        for mid in set(prev or {}) - set(rows):
            lines.append("Price row gone: %s" % mid)
        write_json(state / "prices-last.json", rows)
        write_json(prices_path, prices_doc(rows, ctx.now))
    try:
        ctx.http_calls += 1
        npm = NPM_VERSION()
    except Exception as e:
        npm = None
        ctx.miss("npm view @anthropic-ai/claude-code", "%s: %s" % (type(e).__name__, e))
    log = fetch_text(ctx, "Claude Code CHANGELOG", CHANGELOG_URL, want="markdown")
    entries = parse_changelog(log) if log else []
    if log is not None and not entries:
        ctx.miss("Claude Code CHANGELOG", "no version headings parsed")
    last = read_json(ctx, state / "claude-code-last.json", {})
    last = last.get("version") if isinstance(last, dict) else None
    new = [e for e in entries if vtuple(e[0]) > vtuple(last)] if last else entries[:1]
    for ver, items in new[:10]:
        lines.append("Claude Code %s%s%s:" % (ver, " (npm latest %s)" % npm if npm else "",
                                              "" if last else ", first snapshot"))
        lines += ["  - " + one_line(x, 200) for x in items[:40]]
        if len(items) > 40:
            lines.append("  - (%d more lines in the CHANGELOG)" % (len(items) - 40))
    top_ver = max([e[0] for e in entries] + ([last] if last else []), key=vtuple, default=None)
    if npm and top_ver and vtuple(npm) > vtuple(top_ver):
        lines.append("Claude Code npm latest %s, not yet in the CHANGELOG (top %s); listed when its heading appears."
                     % (npm, top_ver))
    if top_ver:
        write_json(state / "claude-code-last.json", {"version": top_ver})
    sort, models = hf_models(ctx)
    if sort:
        seen = read_json(ctx, state / "hf-last.json", [])
        seen = set(x for x in seen if isinstance(x, str)) if isinstance(seen, list) else set()
        fresh = [m for m in models[:10] if m.get("id") not in seen]
        lines.append("Hugging Face trending (sort=%s worked), %d new in the top 10%s" %
                     (sort, len(fresh), ":" if fresh else "."))
        lines += ["  - %s: %s, trending %s, likes %s, created %s" % (m.get("id"), m.get("pipeline_tag") or "no task",
                  m.get("trendingScore", "?"), m.get("likes", "?"), (m.get("createdAt") or "?")[:10]) for m in fresh]
        write_json(state / "hf-last.json", sorted(seen | {m.get("id") for m in models[:20]}))
    return lines


def prices_doc(rows, now):
    """The shape radar-flags.py reads (json["models"], each with id and the five buckets), page order kept."""
    return {"source": "%s (Model pricing table, read %s by radar-discover.py)" % (PRICING_URL, now.date().isoformat()),
            "unit": "USD per million tokens", "buckets": list(PRICE_KEYS),
            "note": "Refreshed by radar-discover.py from the page rows; ids are the page names lowercased, "
                    "spaces and dots as hyphens.",
            "models": [dict(name=r["name"], id=mid, **{k: r[k] for k in PRICE_KEYS}) for mid, r in rows.items()]}


def price_str(r):
    return ", ".join("%s %g" % (k, r[k]) for k in PRICE_KEYS)


def rank(ctx, state, pool, profile, today):
    snaps = load_snapshots(ctx, state)
    earlier = [s for s in snaps if s[0] < today]
    week = (dt.date.fromisoformat(today) - dt.timedelta(days=6)).isoformat()
    week_floor = (dt.date.fromisoformat(today) - dt.timedelta(days=8)).isoformat()
    for r in pool.values():
        prev = next((g for g in (snap_get(s[1], r["name"]) for s in reversed(earlier)) if g), None)
        old = next((g for g in (snap_get(s[1], r["name"]) for s in reversed(earlier) if week_floor <= s[0] <= week) if g), None)
        r["d7"] = None if old is None or r.get("stars") is None else r["stars"] - old[0]
        r["prev"] = prev
        set_velocity(ctx, r)
    top40 = sorted(pool.values(), key=lambda r: -r["vel"])[:40]
    with ThreadPoolExecutor(8) as ex:  # 8 gh processes at a time: the README step is the slow one
        list(ex.map(lambda r: enrich(ctx, pool, r, profile), top40))
    top = sorted([r for r in top40 if r["score"] > 0], key=lambda r: -r["score"])[:15]
    with ThreadPoolExecutor(8) as ex:
        for r, mv in zip(top, ex.map(lambda r: moving(ctx, r), top)):
            r["moving"] = mv
    snap_path = state / ("stars-%s.json" % today)
    cur = read_json(ctx, snap_path, {})
    repos = cur.get("repos", {}) if isinstance(cur, dict) and isinstance(cur.get("repos"), dict) else {}
    for r in pool.values():
        if r.get("stars") is not None:
            repos[r["name"]] = {"stars": r["stars"], "at": ctx.now.isoformat()}
    try:  # a snapshot that cannot be written costs tomorrow's baseline, never today's ranking
        write_json(snap_path, {"repos": repos})
    except OSError as e:
        ctx.miss("state " + snap_path.name, "not written: %s" % type(e).__name__)
    cut15 = top[-1]["score"] if len(top) == 15 else 0.0
    cut40 = top40[-1]["vel"] if len(top40) == 40 else 0.0
    return top, cut15, cut40


def enrich(ctx, pool, r, profile):
    """Meta for a trending-only repository, then README relevance. A failure scores 0, one line."""
    r["rel"], r["terms"], r["score"] = 0, [], 0.0
    try:
        if "created" not in r:  # trending-only: fill created, pushed, topics
            m = gh(ctx, "repo meta " + r["name"], "repos/" + r["name"])
            if m:
                add_repo(pool, r["name"], src="meta", created=m.get("created_at"), pushed=m.get("pushed_at"),
                         topics=m.get("topics") or [], desc=m.get("description") or "")
                r["stars"] = m.get("stargazers_count", r.get("stars"))
                set_velocity(ctx, r)
        rd = gh(ctx, "readme " + r["name"], "repos/%s/readme" % r["name"], quiet=True)
        txt = ""
        if rd and rd.get("content"):
            txt = base64.b64decode(rd["content"]).decode("utf-8", "replace")[:4000]
        text = " ".join([r["name"], r.get("desc", ""), " ".join(r.get("topics", [])), txt])
        r["rel"], r["terms"] = relevance(text, profile)
        r["score"] = r["vel"] * r["rel"]
    except Exception as e:
        ctx.miss("relevance " + r["name"], "%s: %s" % (type(e).__name__, e))


def set_velocity(ctx, r):
    r["vel"], r["basis"] = velocity(r.get("stars") or 0, ctx.now, parse_time(r.get("created")), r["prev"],
                                    r.get("gain"), r.get("gain_days", 1))


def moving(ctx, r):
    wk = ctx.now - dt.timedelta(days=7)
    p = parse_time(r.get("pushed"))
    if p and p >= wk:
        return "push " + p.date().isoformat()
    rel = gh(ctx, "release " + r["name"], "repos/%s/releases/latest" % r["name"], quiet=True)
    t = parse_time((rel or {}).get("published_at"))
    return "release " + t.date().isoformat() if t and t >= wk else "no"


def render(now, ctx, wall, lines, top, soc):
    o = ["## Discover (radar-discover.py, %s, %d GitHub calls, %d fetches, %.0f s)" %
         (now.strftime("%Y-%m-%d %H:%M"), ctx.gh_calls, ctx.http_calls, wall), "",
         "### Models, prices and releases", ""]
    o += ["- " + x if not x.startswith("  ") else x for x in lines] or ["- nothing new."]
    o += ["", "### Repositories by momentum times relevance", "",
          "| # | repository | stars | stars/day | basis | 7-day delta | moving | terms | description |",
          "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(top, 1):
        o.append("| %d | %s | %s | %.0f | %s | %s | %s | %s | %s |" % (
            i, r["name"], r.get("stars"), r["vel"], r["basis"], "n/a" if r["d7"] is None else r["d7"],
            r["moving"], ", ".join(r["terms"]), one_line(r.get("desc") or "", 110)))
    if not top:
        o.append("| - | no repository scored above 0 | | | | | | | |")
    o += ["", "### HN, top 5 by points with a profile term", ""]
    o += ["%d. %s, %s points: %s (%s)" % (i, s[0], s[1], one_line(s[2], 110), s[3]) for i, s in enumerate(soc, 1)]
    if not soc:
        o.append("- none.")
    o += ["", "### Sources missing", ""] + (["- " + m for m in ctx.missing] or ["- none."])
    return "\n".join(o) + "\n"


def write_section(out, sec, alone):
    out.parent.mkdir(parents=True, exist_ok=True)
    if alone or not out.exists():
        out.write_text(sec, encoding="utf-8", newline="\n")
        return
    txt = out.read_bytes().decode("utf-8", "replace")
    crlf = "\r\n" in txt
    body = txt.replace("\r\n", "\n")
    rx = re.compile(r"^## Discover\b.*?(?=^## (?!#)|\Z)", re.M | re.S)
    body = rx.sub(lambda _: sec + "\n", body, count=1) if rx.search(body) else body.rstrip("\n") + "\n\n" + sec
    out.write_bytes((body.replace("\n", "\r\n") if crlf else body).encode("utf-8"))


if __name__ == "__main__":
    sys.exit(main())
