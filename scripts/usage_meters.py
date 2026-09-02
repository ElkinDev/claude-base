#!/usr/bin/env python3
"""The rate-limit meters of one Claude Code profile: the token, the endpoint, the account name.

Shared by `scripts/usage-probe.py`, which prints one reading, and `scripts/quota-wake.py`,
which waits for a five hour window to reopen. Standard library only, no model call, no state
on disk. A change of endpoint or header touches this file and nothing else.

The OAuth token lives in `.credentials.json` inside the profile directory (`CLAUDE_CONFIG_DIR`,
or `~/.claude` when it is unset). It is sent once as a bearer header and is never returned,
printed or logged: every text handed back by this module is scrubbed of it first, including the
body of an error response, which is the one place a server could echo it back.
"""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
BETA = "oauth-2025-04-20"
USER_AGENT = "claude-code-usage-probe/1"
CREDENTIALS = ".credentials.json"
ACCOUNTS_DIR = ".claude-accounts"
DEFAULT_ACCOUNT = "default"
# the meter names of the two windows, most specific first, with the `limits` kinds that carry
# the same number when the payload reports no meter of that name
FIVE_HOUR = ("five_hour", "session")
SEVEN_DAY = ("seven_day", "weekly_all")


def home():
    return os.path.expanduser("~")


def default_config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home(), ".claude")


def config_dir_for(account, accounts_dir=""):
    """The profile directory of a named account: `~/.claude` for the default account, which
    cannot move, and `<accounts_dir>/<name>` for every other, which is the layout the account
    switcher writes."""
    if not account or account == DEFAULT_ACCOUNT:
        return os.path.join(home(), ".claude")
    return os.path.join(accounts_dir or os.path.join(home(), ACCOUNTS_DIR), account)


def account_name(config_dir=None):
    """The name of the account a profile directory holds: the directory name when there is one,
    otherwise the local part of the address recorded in `~/.claude.json`, otherwise `default`."""
    if config_dir is None:
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    name = DEFAULT_ACCOUNT
    try:
        with open(os.path.join(home(), ".claude.json"), encoding="utf-8") as handle:
            text = handle.read()
        index = text.find('"emailAddress"')
        if index >= 0:
            name = text[index:].split('"')[3].split("@")[0]
    except Exception:
        pass
    if config_dir:
        name = os.path.basename(config_dir.rstrip("/" + chr(92)))
    return name


def scrub(text, token):
    """The same text with the token replaced, so no caller can print or log it by accident."""
    if token and token in text:
        return text.replace(token, "<token>")
    return text


def read_token(config_dir=None):
    """(token, error). The error is a sentence for a human and never carries the token."""
    path = os.path.join(config_dir or default_config_dir(), CREDENTIALS)
    try:
        with open(path, encoding="utf-8") as handle:
            cred = json.load(handle)
    except Exception as error:
        return "", f"cannot read credentials: {error}"
    token = (cred.get("claudeAiOauth") or {}).get("accessToken")
    if not token:
        return "", "no OAuth token in the profile"
    return token, ""


def fetch(token, timeout=20, url=ENDPOINT):
    """(payload, error). One request, one bearer header, no retry: the caller owns the cadence."""
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": BETA,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response), ""
    except urllib.error.HTTPError as error:
        try:
            body = error.read()
        except Exception:
            body = b""
        if token:
            body = body.replace(token.encode("utf-8", "replace"), b"<token>")
        return None, f"HTTP {error.code}: {body[:300].decode(errors='replace')}"
    except Exception as error:
        return None, scrub(f"request failed: {error}", token)


def parse_meters(data):
    """{name: (utilization, resets_at)} for every top-level object carrying a utilization,
    in the order the payload lists them."""
    out = {}
    for key, value in (data or {}).items():
        if isinstance(value, dict) and "utilization" in value:
            out[key] = (value.get("utilization"), value.get("resets_at"))
    return out


def parse_limits(data):
    """{kind: percent} from the `limits` array; a scoped weekly limit is named after its model."""
    out = {}
    for limit in (data or {}).get("limits") or []:
        kind = limit.get("kind")
        if kind == "weekly_scoped":
            model = ((limit.get("scope") or {}).get("model") or {}).get("display_name") or "scoped"
            out[f"weekly_{model.lower()}"] = limit.get("percent")
        elif kind:
            out[kind] = limit.get("percent")
    return out


def iso_offset(value):
    """The reset time as ISO 8601 with an offset. A bare time is read as UTC, an epoch number
    is converted, anything unparseable is handed back untouched."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    text = str(value).strip()
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        stamp = datetime.fromisoformat(candidate)
    except ValueError:
        return text
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.isoformat()


def epoch_of(value):
    """The reset time as a POSIX timestamp, 0.0 when it cannot be read."""
    text = iso_offset(value)
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def pick(meters, limits, names):
    """(utilization, resets_at) for the first of `names` the payload knows: an exact meter, then
    the shortest meter whose name starts with it, then the limit kind of the same name."""
    for name in names:
        if name in meters:
            return meters[name]
    for name in names:
        hits = sorted((key for key in meters if key.startswith(name)), key=len)
        if hits:
            return meters[hits[0]]
    for name in names:
        if name in limits:
            return limits[name], ""
    return None, ""


def summarize(account, data):
    """The reading `quota-wake.py` decides on: both meters, the five hour reset, no exceptions."""
    meters, limits = parse_meters(data), parse_limits(data)
    five, resets = pick(meters, limits, FIVE_HOUR)
    seven, seven_resets = pick(meters, limits, SEVEN_DAY)
    return {
        "account": account,
        "five_hour": five,
        "seven_day": seven,
        "resets_at": iso_offset(resets),
        "seven_day_resets_at": iso_offset(seven_resets),
        "meters": meters,
        "limits": limits,
        "error": "",
    }


def empty_reading(account, error=""):
    """A reading with no meters in it: the shape every failure path hands back."""
    return {"account": account, "five_hour": None, "seven_day": None, "resets_at": "",
            "seven_day_resets_at": "", "meters": {}, "limits": {}, "error": error}


def read_snapshot(config_dir=None, timeout=20):
    """One reading of one profile. Never raises: a failure comes back in `error`, with both
    meters at None, which the loop reads as the `unknown` state. That promise covers the shape
    of the payload as well, because valid JSON that is not the object this module expects is a
    shape change at the endpoint, which is exactly what an unattended watcher has to survive."""
    account = account_name(config_dir)
    token, error = read_token(config_dir)
    if error:
        return empty_reading(account, error)
    data, error = fetch(token, timeout)
    if error:
        return empty_reading(account, error)
    try:
        return summarize(account, data)
    except Exception as error:
        return empty_reading(account, scrub(f"payload not understood: {error}", token))
