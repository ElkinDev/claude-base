#!/usr/bin/env python3
"""The quota wake decision, with no process around it: two meters in, one verdict out.

`scripts/quota-wake.py` owns the loop, the log, the lock and the panes. This module owns what the
numbers mean: when an account counts as dry, when the week is spent, how long to wait past the
announced reset, when to give up, and the words the woken pane reads. Nothing here touches the
network, Herdr or the clock, so every branch is a plain call in a test.
"""
import math
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import usage_meters as meters  # noqa: E402  (the path above is what makes it importable)

UNKNOWN_WARN = 3           # consecutive unknown passes before one warning line
RESUME_TEXT = (
    "Quota wake for account {account}. The five hour window was at {before} percent at {dry_time} "
    "and reads {after} percent now, at {now_time}. Seven day is at {seven_day} percent against a "
    "cap of {cap} percent. Instructions: resume from your brief; measure before launching anything."
)


def clock(when):
    """A wall clock minute, the way a person reads a log line."""
    return datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M")


def percent(value):
    """A meter reading as a number, or None when the payload sent something that is not one.

    The endpoint decides the shape of this field, and a watcher that runs for weeks has to read a
    number that arrives as the string spelling it and refuse everything else without raising: a
    utilization that cannot be compared is no reading at all, which the caller reads as unknown.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def classify(reading, cap):
    """ok, dry, capped or unknown, from the two meters and the operator's weekly cap."""
    five, seven = percent(reading.get("five_hour")), percent(reading.get("seven_day"))
    if reading.get("error") or five is None:
        return "unknown"
    if cap < 100 and seven is not None and seven >= cap:
        return "capped"
    if five >= 100:
        return "dry"
    return "ok"


def advance(state, reading, now, cfg):
    """One step of an account's state machine. Mutates `state`, returns (verdict, message) with
    verdict in watch, capped, unknown, unknown-warn, wait, retry, give-up, wake."""
    kind = classify(reading, cfg["cap"])
    if kind == "unknown":
        state["unknown_streak"] = streak = state.get("unknown_streak", 0) + 1
        detail = reading.get("error") or "no five hour meter in the payload"
        if streak == UNKNOWN_WARN:
            return "unknown-warn", f"unknown for {streak} passes in a row, keeping the previous state: {detail}"
        return "unknown", f"unknown, keeping the previous state: {detail}"
    state["unknown_streak"] = 0
    five, seven = percent(reading.get("five_hour")), percent(reading.get("seven_day"))
    state["five_hour"], state["seven_day"] = five, seven
    if kind == "capped":
        state["state"], state["due_at"] = "capped", 0
        return "capped", f"capped: seven day at {seven} percent, at or above the cap of {cfg['cap']} percent; five hour {five} percent, no wake"
    if kind == "dry":
        state["state"] = "dry"
        resets = reading.get("resets_at") or ""
        if state.get("due_at"):
            if now > state.get("deadline", 0):
                return give_up(state, resets, cfg)
            return "retry", f"still dry after the announced reset, retrying until {clock(state['deadline'])}"
        if state.get("abandoned") == (resets or "none"):
            return "watch", f"still dry at {five} percent, waiting for a new reset time"
        due = meters.epoch_of(resets)
        state.update({"resets_at": resets, "before": five, "before_seven": seven, "dry_at": now,
                      "due_at": max(now, due + cfg["grace"]) if due else now})
        state["deadline"] = state["due_at"] + cfg["retry_for"]
        announced = resets or "at a time the meter did not announce"
        return "wait", f"dry: five hour at {five} percent, window reopens {announced}, probing again at {clock(state['due_at'])}"
    if state.get("due_at"):
        if five >= cfg["resume_below"]:
            if now > state.get("deadline", 0):
                return give_up(state, reading.get("resets_at") or "", cfg)
            return "retry", f"five hour back at {five} percent, not below the resume threshold of {cfg['resume_below']} percent"
        state.update({"state": "ok", "due_at": 0, "after": five})
        return "wake", f"five hour back at {five} percent after {state.get('before', '?')} percent, seven day {seven} percent"
    state["state"] = "ok"
    return "watch", f"five hour {five} percent, seven day {seven} percent"


def give_up(state, resets, cfg):
    state["due_at"], state["abandoned"] = 0, resets or "none"
    return "give-up", f"still dry, the retry budget of {cfg['retry_for']} seconds after the announced reset is spent, back to watching"


def read_template(prompt_file):
    """(template, note): the operator's own brief when --prompt-file names a readable file, the
    built-in resume text otherwise. A file that cannot be read is a note for the caller to log,
    never a reason to skip the wake the account has already earned."""
    if not prompt_file:
        return RESUME_TEXT, ""
    try:
        with open(prompt_file, encoding="utf-8") as handle:
            return handle.read().strip(), ""
    except Exception as error:
        return RESUME_TEXT, f"--prompt-file not read ({error}), using the built-in text"


def prompt_text(template, cfg, account, state, now):
    """The resume text: what the pane cannot know by itself. A template from --prompt-file gets the
    same fields, so an operator's own brief can carry the numbers too."""
    fields = {"account": account, "before": state.get("before", "?"), "after": state.get("after", "?"),
              "seven_day": state.get("seven_day"), "cap": cfg["cap"], "dry_time": clock(state.get("dry_at", now)),
              "now_time": clock(now), "resets_at": state.get("resets_at", "")}
    try:
        return template.format(**fields)
    except Exception:
        return template
