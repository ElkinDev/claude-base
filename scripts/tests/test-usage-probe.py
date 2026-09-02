#!/usr/bin/env python3
"""Tests for usage-probe.py: the --json shape, the account resolution, the token that never
leaves the process, and byte identity of the human lines and --csv against the reference
implementation this port came from.

The endpoint is never called. `urllib.request.urlopen` is replaced by a fake that answers with
the recorded payload in `fixtures/usage/usage-payload.json`, and the profile is a throwaway
home with a credentials file carrying a dummy token.

The reference implementation is private and is not part of this repository. Point
USAGE_PROBE_REFERENCE at a copy of it to run the parity class; without the variable that class
is skipped and everything else still runs.

Run:
    python test-usage-probe.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixtures", "usage", "usage-payload.json")
REFERENCE = os.environ.get("USAGE_PROBE_REFERENCE", "")
TOKEN = "dummy-oauth-value-000111222333"
CLOCK = datetime(2026, 3, 4, 12, 0, 0)
HUMAN = (
    "account: acct-a\n"
    "five_hour: 100% used, resets 2026-03-04T18:00:00Z\n"
    "seven_day: 61% used, resets 2026-03-09T09:00:00Z\n"
    "seven_day_opus: 12% used, resets 2026-03-09T09:00:00Z\n"
    "limit session: 100% used\n"
    "limit weekly_all: 61% used\n"
    "limit weekly_opus: 12% used\n"
    "other keys: limits, plan\n"
)
CSV = "2026-03-04 12:00,acct-a,100,61,weekly_opus,12\n"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load("usage_probe", os.path.join(SCRIPTS, "usage-probe.py"))
meters = probe.meters
PAYLOAD = json.load(open(FIXTURE, encoding="utf-8"))


class FakeResponse:
    """What urlopen returns: a context manager json.load can read once."""

    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def read(self, *_):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FrozenClock:
    @classmethod
    def now(cls):
        return CLOCK


@contextlib.contextmanager
def endpoint(payload=None, failure=None):
    original = urllib.request.urlopen

    def fake(request, timeout=None):
        if failure is not None:
            raise failure
        return FakeResponse(payload)

    urllib.request.urlopen = fake
    try:
        yield
    finally:
        urllib.request.urlopen = original


@contextlib.contextmanager
def profile(home, config_dir=None, token=TOKEN, email=None):
    """A throwaway home carrying a credentials file, with CLAUDE_CONFIG_DIR where asked."""
    target = config_dir or os.path.join(home, ".claude")
    os.makedirs(target, exist_ok=True)
    if token is not None:
        with open(os.path.join(target, ".credentials.json"), "w", encoding="utf-8") as handle:
            json.dump({"claudeAiOauth": {"accessToken": token}}, handle)
    if email:
        with open(os.path.join(home, ".claude.json"), "w", encoding="utf-8") as handle:
            json.dump({"emailAddress": email}, handle)
    keys = ("USERPROFILE", "HOME", "CLAUDE_CONFIG_DIR")
    saved = {key: os.environ.get(key) for key in keys}
    os.environ["USERPROFILE"] = home
    os.environ["HOME"] = home
    os.environ.pop("CLAUDE_CONFIG_DIR", None)
    if config_dir:
        os.environ["CLAUDE_CONFIG_DIR"] = config_dir
    try:
        yield
    finally:
        for key, value in saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value


def run(module, argv):
    """One run of a probe module with a frozen clock, capturing stdout and stderr."""
    saved = module.datetime
    module.datetime = FrozenClock
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.main(list(argv))
    finally:
        module.datetime = saved
    return code, out.getvalue(), err.getvalue()


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usage-probe-")
        self.account_dir = os.path.join(self.tmp, ".claude-accounts", "acct-a")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_human_lines_name_every_meter_and_limit(self):
        with profile(self.tmp, self.account_dir), endpoint(PAYLOAD):
            code, out, _ = run(probe, [])
        self.assertEqual(code, 0)
        self.assertEqual(out, HUMAN)

    def test_csv_is_the_row_the_nightly_ledger_appends(self):
        with profile(self.tmp, self.account_dir), endpoint(PAYLOAD):
            code, out, _ = run(probe, ["--csv"])
        self.assertEqual(code, 0)
        self.assertEqual(out, CSV)

    def test_json_maps_every_meter_to_utilization_and_an_iso_reset(self):
        with profile(self.tmp, self.account_dir), endpoint(PAYLOAD):
            code, out, _ = run(probe, ["--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["account"], "acct-a")
        self.assertEqual(sorted(payload["meters"]), ["five_hour", "seven_day", "seven_day_opus"])
        self.assertEqual(payload["meters"]["five_hour"], {"utilization": 100, "resets_at": "2026-03-04T18:00:00+00:00"})
        self.assertEqual(payload["meters"]["seven_day"]["utilization"], 61)
        self.assertEqual(payload["limits"], {"session": 100, "weekly_all": 61, "weekly_opus": 12})

    def test_json_normalizes_a_naive_time_and_an_epoch_to_an_offset(self):
        payload = {"five_hour": {"utilization": 4, "resets_at": "2026-03-04T18:00:00"}, "seven_day": {"utilization": 5, "resets_at": 1772650800}}
        with profile(self.tmp, self.account_dir), endpoint(payload):
            _, out, _ = run(probe, ["--json"])
        meters_out = json.loads(out)["meters"]
        self.assertTrue(meters_out["five_hour"]["resets_at"].endswith("+00:00"))
        self.assertTrue(meters_out["seven_day"]["resets_at"].endswith("+00:00"))
        self.assertTrue(meters_out["seven_day"]["resets_at"].startswith("2026-"))

    def test_account_comes_from_the_config_directory_name(self):
        with profile(self.tmp, self.account_dir, email="operator@example.com"):
            self.assertEqual(meters.account_name(), "acct-a")

    def test_account_falls_back_to_the_local_part_of_the_address(self):
        with profile(self.tmp, email="operator@example.com"):
            self.assertEqual(meters.account_name(), "operator")

    def test_account_is_default_without_a_config_directory_or_an_address(self):
        with profile(self.tmp):
            self.assertEqual(meters.account_name(), "default")

    def test_the_token_stays_out_of_a_successful_run(self):
        with profile(self.tmp, self.account_dir), endpoint(PAYLOAD):
            for argv in ([], ["--csv"], ["--json"]):
                code, out, err = run(probe, argv)
                self.assertEqual(code, 0)
                self.assertNotIn(TOKEN, out)
                self.assertNotIn(TOKEN, err)

    def test_the_token_stays_out_of_an_endpoint_error(self):
        body = json.dumps({"error": "rate_limit", "echo": TOKEN}).encode("utf-8")
        failure = urllib.error.HTTPError("https://example.com/usage", 429, "Too Many Requests", {}, io.BytesIO(body))
        with profile(self.tmp, self.account_dir), endpoint(failure=failure):
            code, out, err = run(probe, [])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", out)
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(TOKEN, err)

    def test_a_transport_failure_is_one_line_and_exit_one(self):
        with profile(self.tmp, self.account_dir), endpoint(failure=urllib.error.URLError("no route")):
            code, out, _ = run(probe, [])
        self.assertEqual(code, 1)
        self.assertIn("request failed", out)

    def test_a_profile_without_credentials_says_so(self):
        with profile(self.tmp, self.account_dir, token=None):
            code, out, _ = run(probe, [])
        self.assertEqual(code, 1)
        self.assertIn("cannot read credentials", out)
        self.assertNotIn(TOKEN, out)

    def test_a_profile_without_a_token_says_so(self):
        os.makedirs(self.account_dir, exist_ok=True)
        with open(os.path.join(self.account_dir, ".credentials.json"), "w", encoding="utf-8") as handle:
            handle.write("{}")
        with profile(self.tmp, self.account_dir, token=None):
            code, out, _ = run(probe, [])
        self.assertEqual(code, 1)
        self.assertEqual(out, "no OAuth token in the profile\n")

    def test_an_unknown_argument_is_an_argument_error(self):
        with profile(self.tmp, self.account_dir):
            code, out, _ = run(probe, ["--everything"])
        self.assertEqual(code, 2)
        self.assertIn("--everything", out)


class SnapshotTest(unittest.TestCase):
    """What quota-wake.py reads: both meters and the reset time, never raising."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usage-snapshot-")
        self.account_dir = os.path.join(self.tmp, ".claude-accounts", "acct-a")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_snapshot_carries_both_meters_and_the_five_hour_reset(self):
        with profile(self.tmp, self.account_dir), endpoint(PAYLOAD):
            reading = meters.read_snapshot(self.account_dir)
        self.assertEqual(reading["account"], "acct-a")
        self.assertEqual(reading["five_hour"], 100)
        self.assertEqual(reading["seven_day"], 61)
        self.assertEqual(reading["resets_at"], "2026-03-04T18:00:00+00:00")
        self.assertEqual(reading["error"], "")

    def test_snapshot_falls_back_to_the_limit_kinds_when_there_is_no_meter(self):
        payload = {"limits": [{"kind": "session", "percent": 100}, {"kind": "weekly_all", "percent": 70}]}
        with profile(self.tmp, self.account_dir), endpoint(payload):
            reading = meters.read_snapshot(self.account_dir)
        self.assertEqual((reading["five_hour"], reading["seven_day"]), (100, 70))

    def test_snapshot_reports_an_error_instead_of_raising(self):
        with profile(self.tmp, self.account_dir), endpoint(failure=urllib.error.URLError("down")):
            reading = meters.read_snapshot(self.account_dir)
        self.assertIsNone(reading["five_hour"])
        self.assertIn("request failed", reading["error"])
        self.assertNotIn(TOKEN, reading["error"])

    def test_a_payload_of_the_wrong_shape_is_an_error_and_never_a_raise(self):
        """Valid JSON that is not the object this module expects is a shape change at the endpoint,
        and the docstring promises the loop survives it: both meters None, one sentence in error."""
        for name, payload in (("a JSON array", [{"five_hour": {"utilization": 1}}]), ("a JSON string", "nope"),
                              ("a JSON number", 7), ("limits that are not objects", {"limits": ["session"]}),
                              ("limits that are not a list", {"limits": {"session": 100}})):
            with self.subTest(payload=name):
                with profile(self.tmp, self.account_dir), endpoint(payload):
                    reading = meters.read_snapshot(self.account_dir)
                self.assertEqual((reading["five_hour"], reading["seven_day"]), (None, None))
                self.assertIn("payload not understood", reading["error"])
                self.assertNotIn(TOKEN, reading["error"])

    def test_a_utilization_that_arrives_as_a_string_is_read_not_rejected(self):
        """The reader hands the field on as it came; reading it as a number is the decision core's
        job, and refusing it here would throw away a meter the endpoint did send."""
        payload = {"five_hour": {"utilization": "100", "resets_at": "2026-03-04T18:00:00Z"}}
        with profile(self.tmp, self.account_dir), endpoint(payload):
            reading = meters.read_snapshot(self.account_dir)
        self.assertEqual(reading["five_hour"], "100")
        self.assertEqual(reading["error"], "")

    def test_epoch_of_reads_both_offsets_and_a_bare_time(self):
        self.assertEqual(meters.epoch_of("2026-03-04T18:00:00Z"), meters.epoch_of("2026-03-04T18:00:00+00:00"))
        self.assertEqual(meters.epoch_of("2026-03-04T18:00:00"), meters.epoch_of("2026-03-04T18:00:00Z"))
        self.assertEqual(meters.epoch_of("2026-03-04T19:00:00+01:00"), meters.epoch_of("2026-03-04T18:00:00Z"))
        self.assertEqual(meters.epoch_of(""), 0.0)
        self.assertEqual(meters.epoch_of("not a time"), 0.0)


@unittest.skipUnless(REFERENCE and os.path.isfile(REFERENCE), "set USAGE_PROBE_REFERENCE to the reference implementation")
class ParityTest(unittest.TestCase):
    """The port answers the reference byte for byte on the same payload, which is the contract
    scripts/ledger-nightly.ps1 depends on."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usage-parity-")
        self.account_dir = os.path.join(self.tmp, ".claude-accounts", "acct-a")
        self.reference = load("reference_probe", REFERENCE)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def both(self, argv, payload=None, failure=None):
        out = []
        for module in (self.reference, probe):
            with profile(self.tmp, self.account_dir), endpoint(payload, failure):
                out.append(run(module, argv))
        return out

    def test_human_lines_are_byte_identical(self):
        (code_a, out_a, _), (code_b, out_b, _) = self.both([], PAYLOAD)
        self.assertEqual(out_a.encode("utf-8"), out_b.encode("utf-8"))
        self.assertEqual((code_a, code_b), (0, 0))

    def test_csv_is_byte_identical(self):
        (_, out_a, _), (_, out_b, _) = self.both(["--csv"], PAYLOAD)
        self.assertEqual(out_a.encode("utf-8"), out_b.encode("utf-8"))
        self.assertEqual(out_b, CSV)

    def test_the_credential_failure_line_is_byte_identical(self):
        out = []
        for module in (self.reference, probe):
            with profile(self.tmp, self.account_dir, token=None):
                out.append(run(module, [])[1])
        self.assertEqual(out[0].encode("utf-8"), out[1].encode("utf-8"))

    def test_an_endpoint_error_without_a_token_in_it_is_byte_identical(self):
        out = []
        for module in (self.reference, probe):
            failure = urllib.error.HTTPError("https://example.com/usage", 429, "Too Many Requests", {}, io.BytesIO(b'{"error":"rate_limit"}'))
            with profile(self.tmp, self.account_dir), endpoint(failure=failure):
                out.append(run(module, [])[1])
        self.assertEqual(out[0].encode("utf-8"), out[1].encode("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
