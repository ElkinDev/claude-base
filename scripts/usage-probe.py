#!/usr/bin/env python3
"""One-shot read of the account's rate-limit meters, the same data `/usage` shows.

Reads the OAuth token of the active Claude Code profile (CLAUDE_CONFIG_DIR, or ~/.claude when
unset) and asks the usage endpoint once. Prints every meter it returns as `name used% resets_at`;
never prints the token, not even when the endpoint echoes it back in an error body. Exit 1 on
any failure, 2 on a bad argument.

Usage:
    python usage-probe.py            human lines
    python usage-probe.py --csv      one line: time,account,session,weekly_all,scoped_meter,scoped_pct
    python usage-probe.py --json     {"account", "meters": {name: {utilization, resets_at}}, "limits"}

The `--csv` row is what `scripts/ledger-nightly.ps1` appends to the meters log every night, so
its shape is a contract. `--json` is the machine reading `scripts/quota-wake.py` waits on: every
reset time comes out in ISO 8601 with an offset, whatever shape the endpoint used, and `limits`
rides along for the kinds the payload reports only there.
"""
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import usage_meters as meters  # noqa: E402  (the path above is what makes it importable)

FLAGS = ("--csv", "--json", "--help", "-h")


def main(argv):
    unknown = [item for item in argv if item not in FLAGS]
    if unknown:
        print(f"unknown argument: {unknown[0]}")
        print(__doc__.split("Usage:", 1)[1].strip())
        return 2
    if "--help" in argv or "-h" in argv:
        print(__doc__.strip())
        return 0

    token, error = meters.read_token()
    if error:
        print(error)
        return 1
    data, error = meters.fetch(token)
    if error:
        print(error)
        return 1

    account = meters.account_name()
    readings = meters.parse_meters(data)
    limits = meters.parse_limits(data)

    if "--json" in argv:
        print(json.dumps({
            "account": account,
            "meters": {name: {"utilization": used, "resets_at": meters.iso_offset(reset)} for name, (used, reset) in readings.items()},
            "limits": limits,
        }, indent=2))
        return 0
    if "--csv" in argv:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        scoped = [(key, value) for key, value in limits.items() if key not in ("session", "weekly_all")]
        scoped_key, scoped_value = (scoped[0] if scoped else ("", ""))
        print(f"{now},{account},{limits.get('session', '')},{limits.get('weekly_all', '')},{scoped_key},{scoped_value}")
        return 0

    print(f"account: {account}")
    for name, (used, reset) in readings.items():
        print(f"{name}: {used}% used, resets {reset}")
    for name, value in limits.items():
        print(f"limit {name}: {value}% used")
    extra = [key for key, value in data.items() if not (isinstance(value, dict) and "utilization" in value)]
    if extra:
        print("other keys: " + ", ".join(extra))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
