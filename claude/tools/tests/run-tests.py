"""Tests for record.py. No network, no real record, nothing outside a temporary directory.

Every case builds its files in a fresh temporary directory and runs the CLI the way a
caller runs it: as a subprocess with the payload on stdin. HOME and USERPROFILE are
pointed at that directory too, so a ~ in a config path can never reach the real one.
Every assertion about a file compares the whole file as bytes. One case imports the CLI
instead, because a roll back that fails itself cannot be staged from the outside.

    python run-tests.py
"""
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RECORD = os.path.join(os.path.dirname(HERE), "record.py")
EM_DASH = "\u2014"  # written as an escape: the character itself belongs in no file here


def clean_env(tmp, **extra):
    env = os.environ.copy()
    for name in ("RECORD_CONFIG", "HOMEDRIVE", "HOMEPATH"):
        env.pop(name, None)
    env["HOME"] = tmp
    env["USERPROFILE"] = tmp
    for name, value in extra.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def run(args, tmp, payload=None, cwd=None, **env):
    if not isinstance(payload, bytes):
        payload = (payload or "").encode("utf-8")
    return subprocess.run(
        [sys.executable, RECORD] + args,
        input=payload,
        capture_output=True,
        env=clean_env(tmp, **env),
        cwd=cwd or tmp,
        timeout=60,
    )


def out(done):
    return done.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def err(done):
    return done.stderr.decode("utf-8", "replace").replace("\r\n", "\n")


def put(path, data):
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def raw(path):
    with open(path, "rb") as handle:
        return handle.read()


def config(tmp, targets, name="record.json", where=None):
    path = os.path.join(where or tmp, name)
    put(path, json.dumps({"targets": targets}).encode("utf-8"))
    return path


# ---------------------------------------------------------------- appends

def test_an_append_keeps_an_lf_file(tmp):
    page = put(os.path.join(tmp, "lf.md"), b"one\ntwo\n")
    done = run(["add", page], tmp, "| w1 | merged |\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"one\ntwo\n| w1 | merged |\n", raw(page)
    assert out(done).strip().endswith("+1 lines (LF)"), out(done)


def test_an_append_keeps_a_crlf_file(tmp):
    page = put(os.path.join(tmp, "crlf.md"), b"one\r\ntwo\r\n")
    done = run(["add", page], tmp, "| w1 | merged |\nsecond line\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"one\r\ntwo\r\n| w1 | merged |\r\nsecond line\r\n", raw(page)
    assert out(done).strip().endswith("+2 lines (CRLF)"), out(done)


def test_an_append_keeps_a_bom_and_crlf_file(tmp):
    page = put(os.path.join(tmp, "bom.md"), b"\xef\xbb\xbfalpha\r\n")
    done = run(["add", page], tmp, "beta\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"\xef\xbb\xbfalpha\r\nbeta\r\n", raw(page)


def test_an_append_follows_the_majority_of_a_mixed_file(tmp):
    page = put(os.path.join(tmp, "mixed.md"), b"one\r\ntwo\r\nthree\n")
    done = run(["add", page], tmp, "four\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"one\r\ntwo\r\nthree\nfour\r\n", raw(page)


def test_an_append_adds_the_missing_final_newline(tmp):
    page = put(os.path.join(tmp, "tail.md"), b"one\ntwo")
    done = run(["add", page], tmp, "three")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"one\ntwo\nthree\n", raw(page)


def test_the_default_key_is_the_first_payload_line(tmp):
    page = put(os.path.join(tmp, "keys.md"), b"| w1 | merged |\n")
    done = run(["add", page], tmp, "| w1 | merged |\nwith a second line\n")
    assert done.returncode == 3, done.returncode
    assert "already recorded" in err(done), err(done)
    assert raw(page) == b"| w1 | merged |\n", "a refusal writes nothing"


def test_an_explicit_key_decides_on_its_own(tmp):
    page = put(os.path.join(tmp, "keys.md"), b"round 7 closed\n")
    done = run(["add", page, "--key", "round 8"], tmp, "round 7 closed again\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"round 7 closed\nround 7 closed again\n", raw(page)
    again = run(["add", page, "--key", "round 8"], tmp, "round 8 is under way\n")
    assert again.returncode == 0, "the key is not in the file yet"
    third = run(["add", page, "--key", "round 8"], tmp, "round 8 again\n")
    assert third.returncode == 3, third.returncode


def test_a_repeated_append_is_refused(tmp):
    page = put(os.path.join(tmp, "twice.md"), b"header\n")
    first = run(["add", page], tmp, "| w2 | gate CLEAR |\n")
    assert first.returncode == 0, err(first)
    landed = raw(page)
    second = run(["add", page], tmp, "| w2 | gate CLEAR |\n")
    assert second.returncode == 3, second.returncode
    assert raw(page) == landed, "the second run must change nothing"


def test_an_em_dash_is_refused(tmp):
    page = put(os.path.join(tmp, "dash.md"), b"header\n")
    done = run(["add", page], tmp, "a record %s with the wrong dash\n" % EM_DASH)
    assert done.returncode == 2, done.returncode
    assert "em-dash" in err(done), err(done)
    assert raw(page) == b"header\n", "nothing is written"


def test_a_payload_that_is_not_utf8_is_refused(tmp):
    """PowerShell 5.1 pipes cp1252 by default, so this arrives sooner or later."""
    page = put(os.path.join(tmp, "bytes.md"), b"header\n")
    done = run(["add", page], tmp, b"caf\xe9 in cp1252\n")
    assert done.returncode == 2, done.returncode
    assert "the payload is not UTF-8" in err(done), err(done)
    assert raw(page) == b"header\n", raw(page)


def test_a_leading_bom_on_the_payload_is_never_content(tmp):
    """PowerShell 5.1 Out-File -Encoding utf8 always writes one, so it must not land."""
    page = put(os.path.join(tmp, "landings.md"), b"header\n")
    done = run(["add", page], tmp, b"\xef\xbb\xbf| w6 | merged |\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"header\n| w6 | merged |\n", "the BOM bytes are not the record"
    other = put(os.path.join(tmp, "second.md"), b"header\n")
    plain = run(["add", other], tmp, "| w6 | merged |\n")
    assert plain.returncode == 0, err(plain)
    landed = raw(other)
    again = run(["add", other], tmp, b"\xef\xbb\xbf| w6 | merged |\n")
    assert again.returncode == 3, again.returncode
    assert "already recorded" in err(again), err(again)
    assert raw(other) == landed, "a heredoc row and the same row from a file are one row"


def test_a_payload_of_utf16_bytes_is_refused(tmp):
    """UTF-16 with no BOM is not invalid UTF-8, it is NUL bytes, and those are no record."""
    page = put(os.path.join(tmp, "bytes.md"), b"header\n")
    done = run(["add", page], tmp, "| w7 | merged |\n".encode("utf-16-le"))
    assert done.returncode == 2, done.returncode
    assert "NUL byte" in err(done), err(done)
    assert raw(page) == b"header\n", raw(page)


def test_a_target_that_is_not_utf8_is_refused(tmp):
    page = put(os.path.join(tmp, "bytes.md"), b"caf\xe9\n")
    done = run(["add", page], tmp, "a record\n")
    assert done.returncode == 2, done.returncode
    assert "the file is not UTF-8" in err(done), err(done)
    assert raw(page) == b"caf\xe9\n", raw(page)


def test_an_empty_key_option_is_a_usage_error(tmp):
    page = put(os.path.join(tmp, "keys.md"), b"header\n")
    done = run(["add", page, "--key", ""], tmp, "| w5 | merged |\n")
    assert done.returncode == 1, done.returncode
    assert "--key needs text" in err(done), err(done)
    assert raw(page) == b"header\n", raw(page)


def test_a_missing_target_is_refused(tmp):
    page = os.path.join(tmp, "not-there.md")
    done = run(["add", page], tmp, "a record\n")
    assert done.returncode == 2, done.returncode
    assert "no such file" in err(done), err(done)
    assert not os.path.exists(page), "a target is never created"


def test_an_empty_payload_is_refused(tmp):
    page = put(os.path.join(tmp, "empty.md"), b"header\n")
    done = run(["add", page], tmp, "\n\n")
    assert done.returncode == 2, done.returncode
    assert raw(page) == b"header\n", raw(page)


def test_require_lets_an_ordered_record_through(tmp):
    page = put(os.path.join(tmp, "order.md"), b"review CLEAR for w3\n")
    done = run(["add", page, "--require", "review CLEAR for w3"], tmp, "merge of w3\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"review CLEAR for w3\nmerge of w3\n", raw(page)


def test_require_refuses_a_record_that_is_out_of_order(tmp):
    page = put(os.path.join(tmp, "order.md"), b"nothing reviewed yet\n")
    done = run(["add", page, "--require", "review CLEAR for w3"], tmp, "merge of w3\n")
    assert done.returncode == 4, done.returncode
    assert "required text" in err(done), err(done)
    assert raw(page) == b"nothing reviewed yet\n", raw(page)


def test_a_blank_directive_in_an_add_payload_is_refused(tmp):
    """The directives mean in add what they mean in a round block, the refusal included."""
    page = put(os.path.join(tmp, "order.md"), b"review CLEAR for w3\n")
    for payload in ("require:\nmerge of w3\n", "key:\nmerge of w3\n"):
        done = run(["add", page], tmp, payload)
        assert done.returncode == 2, done.returncode
        assert "directive needs text" in err(done), err(done)
        assert raw(page) == b"review CLEAR for w3\n", raw(page)


def test_a_key_directive_in_an_add_payload_is_taken_not_appended(tmp):
    page = put(os.path.join(tmp, "landings.md"), b"header\n")
    done = run(["add", page], tmp, "key: w3 merged\n| w3 merged | 19:40 |\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"header\n| w3 merged | 19:40 |\n", "the directive is not a row"
    again = run(["add", page], tmp, "key: w3 merged\n| w3 merged | 19:41 |\n")
    assert again.returncode == 3, again.returncode
    assert raw(page) == b"header\n| w3 merged | 19:40 |\n", raw(page)


def test_a_key_option_and_a_key_directive_together_are_a_usage_error(tmp):
    page = put(os.path.join(tmp, "landings.md"), b"header\n")
    done = run(["add", page, "--key", "w4 merged"], tmp, "key: w3 merged\n| w3 |\n")
    assert done.returncode == 1, done.returncode
    assert "--key and a key: directive together" in err(done), err(done)
    assert raw(page) == b"header\n", raw(page)


def test_a_require_directive_and_a_require_option_add_up(tmp):
    payload = "require: gate CLEAR for w3\nmerge of w3\n"
    option = ["--require", "review CLEAR for w3"]
    page = put(os.path.join(tmp, "order.md"), b"review CLEAR for w3\n")
    done = run(["add", page] + option, tmp, payload)
    assert done.returncode == 4, done.returncode
    assert raw(page) == b"review CLEAR for w3\n", "the directive text is not in the file"
    put(page, b"gate CLEAR for w3\n")
    done = run(["add", page] + option, tmp, payload)
    assert done.returncode == 4, done.returncode
    assert raw(page) == b"gate CLEAR for w3\n", "the option text is not in the file"
    put(page, b"review CLEAR for w3\ngate CLEAR for w3\n")
    done = run(["add", page] + option, tmp, payload)
    assert done.returncode == 0, err(done)
    assert raw(page) == b"review CLEAR for w3\ngate CLEAR for w3\nmerge of w3\n", raw(page)


def test_an_empty_require_option_is_a_usage_error(tmp):
    """Blank text is in every file, so a blank --require would pass on anything."""
    page = put(os.path.join(tmp, "order.md"), b"nothing reviewed yet\n")
    for blank in ("", "  "):
        done = run(["add", page, "--require", blank], tmp, "merge of w3\n")
        assert done.returncode == 1, done.returncode
        assert "--require needs text" in err(done), err(done)
        assert raw(page) == b"nothing reviewed yet\n", raw(page)


# ---------------------------------------------------------------- amend

def test_amend_appends_the_suffix_to_one_line(tmp):
    page = put(os.path.join(tmp, "ledger.md"), b"w1 open\r\nw2 open\r\n")
    done = run(["amend", page], tmp, "w2 => closed 19:40\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"w1 open\r\nw2 open closed 19:40\r\n", raw(page)
    assert out(done).strip().endswith("1 lines amended"), out(done)


def test_amend_refuses_an_ambiguous_anchor(tmp):
    page = put(os.path.join(tmp, "ledger.md"), b"w1 open\nw1 again\n")
    done = run(["amend", page], tmp, "w1 => closed\n")
    assert done.returncode == 4, done.returncode
    assert "ambiguous" in err(done), err(done)
    assert raw(page) == b"w1 open\nw1 again\n", raw(page)


def test_amend_refuses_an_anchor_that_is_not_there(tmp):
    page = put(os.path.join(tmp, "ledger.md"), b"w1 open\n")
    done = run(["amend", page], tmp, "w9 => closed\n")
    assert done.returncode == 4, done.returncode
    assert "not found" in err(done), err(done)
    assert raw(page) == b"w1 open\n", raw(page)


def test_amend_refuses_a_suffix_that_is_already_there(tmp):
    page = put(os.path.join(tmp, "ledger.md"), b"w1 open closed 19:40\n")
    done = run(["amend", page], tmp, "w1 => closed 19:40\n")
    assert done.returncode == 3, done.returncode
    assert raw(page) == b"w1 open closed 19:40\n", raw(page)


def test_amend_writes_every_line_or_none(tmp):
    page = put(os.path.join(tmp, "ledger.md"), b"w1 open\nw2 open\n")
    done = run(["amend", page], tmp, "w1 => closed\nw9 => closed\n")
    assert done.returncode == 4, done.returncode
    assert raw(page) == b"w1 open\nw2 open\n", "the first edit must not land alone"


# ---------------------------------------------------------------- swap

def test_swap_replaces_the_one_occurrence(tmp):
    page = put(os.path.join(tmp, "plan.md"), b"step 1 pending\r\nstep 2 pending\r\n")
    done = run(["swap", page], tmp, "@old\nstep 1 pending\n@new\nstep 1 done\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"step 1 done\r\nstep 2 pending\r\n", raw(page)
    assert out(done).strip().endswith("swapped 1 occurrence"), out(done)


def test_swap_replaces_a_block_of_lines(tmp):
    page = put(os.path.join(tmp, "plan.md"), b"head\nold one\nold two\ntail\n")
    done = run(["swap", page], tmp, "@old\nold one\nold two\n@new\nfresh\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"head\nfresh\ntail\n", raw(page)


def test_swap_refuses_two_occurrences(tmp):
    page = put(os.path.join(tmp, "plan.md"), b"pending\npending\n")
    done = run(["swap", page], tmp, "@old\npending\n@new\ndone\n")
    assert done.returncode == 4, done.returncode
    assert "ambiguous" in err(done), err(done)
    assert raw(page) == b"pending\npending\n", raw(page)


def test_swap_refuses_text_that_is_not_there(tmp):
    page = put(os.path.join(tmp, "plan.md"), b"step 1 pending\n")
    done = run(["swap", page], tmp, "@old\nstep 4 pending\n@new\nstep 4 done\n")
    assert done.returncode == 4, done.returncode
    assert "not found" in err(done), err(done)
    assert raw(page) == b"step 1 pending\n", raw(page)


def test_swap_refuses_a_swap_that_already_happened(tmp):
    page = put(os.path.join(tmp, "plan.md"), b"step 1 done\n")
    done = run(["swap", page], tmp, "@old\nstep 1 pending\n@new\nstep 1 done\n")
    assert done.returncode == 3, done.returncode
    assert "already recorded" in err(done), err(done)
    assert raw(page) == b"step 1 done\n", raw(page)


def test_swap_matches_whole_lines_only(tmp):
    """A substring match would turn step 1 into step 9 inside step 12."""
    page = put(os.path.join(tmp, "plan.md"), b"step 12 pending\n")
    done = run(["swap", page], tmp, "@old\nstep 1\n@new\nstep 9\n")
    assert done.returncode == 4, done.returncode
    assert "not found" in err(done), err(done)
    assert raw(page) == b"step 12 pending\n", raw(page)


def test_swap_refuses_an_empty_block(tmp):
    page = put(os.path.join(tmp, "plan.md"), b"step 1 pending\n")
    for payload in ("@old\nstep 1 pending\n@new\n", "@old\n@new\nstep 1 done\n"):
        done = run(["swap", page], tmp, payload)
        assert done.returncode == 2, done.returncode
        assert "block is empty" in err(done), err(done)
        assert raw(page) == b"step 1 pending\n", raw(page)


def test_swap_refuses_a_payload_without_its_markers(tmp):
    page = put(os.path.join(tmp, "plan.md"), b"step 1 pending\n")
    done = run(["swap", page], tmp, "step 1 pending\nstep 1 done\n")
    assert done.returncode == 2, done.returncode
    assert raw(page) == b"step 1 pending\n", raw(page)


# ---------------------------------------------------------------- round

def test_a_round_writes_every_block(tmp):
    landings = put(os.path.join(tmp, "landings.md"), b"| time | note |\n")
    ledger = put(os.path.join(tmp, "ledger.md"), b"w1 open\r\n")
    plan = put(os.path.join(tmp, "plan.md"), b"step 1 pending\n")
    payload = (
        "@%s\n"
        "key: w1 merged\n"
        "| 19:40 | w1 merged |\n"
        "@amend %s\n"
        "w1 => closed 19:40\n"
        "@swap %s\n"
        "@old\nstep 1 pending\n@new\nstep 1 done\n"
    ) % (landings, ledger, plan)
    done = run(["round"], tmp, payload)
    assert done.returncode == 0, err(done)
    assert raw(landings) == b"| time | note |\n| 19:40 | w1 merged |\n", raw(landings)
    assert raw(ledger) == b"w1 open closed 19:40\r\n", raw(ledger)
    assert raw(plan) == b"step 1 done\n", raw(plan)
    assert len(out(done).strip().split("\n")) == 3, out(done)


def test_a_failing_block_writes_nothing_anywhere(tmp):
    landings = put(os.path.join(tmp, "landings.md"), b"| time | note |\n")
    ledger = put(os.path.join(tmp, "ledger.md"), b"w1 open\n")
    payload = "@%s\n| 19:40 | w1 merged |\n@amend %s\nw9 => closed\n" % (landings, ledger)
    done = run(["round"], tmp, payload)
    assert done.returncode == 4, done.returncode
    assert raw(landings) == b"| time | note |\n", "the first block must not land"
    assert raw(ledger) == b"w1 open\n", raw(ledger)


def test_a_round_directive_carries_the_ordering_guard(tmp):
    landings = put(os.path.join(tmp, "landings.md"), b"| time | note |\n")
    payload = "@%s\nrequire: review CLEAR\n| 19:41 | merge |\n" % landings
    done = run(["round"], tmp, payload)
    assert done.returncode == 4, done.returncode
    assert raw(landings) == b"| time | note |\n", raw(landings)


def test_a_read_only_target_stops_the_round_before_any_write(tmp):
    """All or nothing has to hold through the write phase, not only through validation."""
    first = put(os.path.join(tmp, "first.md"), b"header\n")
    second = put(os.path.join(tmp, "second.md"), b"header\n")
    os.chmod(second, stat.S_IREAD)
    payload = "@%s\nline one\n@%s\nline two\n" % (first, second)
    done = run(["round"], tmp, payload)
    assert done.returncode == 2, done.returncode
    assert "cannot write" in err(done), err(done)
    assert raw(first) == b"header\n", "the writable target must not be written either"
    assert raw(second) == b"header\n", raw(second)
    os.chmod(second, stat.S_IWRITE)
    again = run(["round"], tmp, payload)
    assert again.returncode == 0, err(again)
    assert raw(first) == b"header\nline one\n", raw(first)
    assert raw(second) == b"header\nline two\n", raw(second)


def test_an_empty_key_directive_is_refused_every_time(tmp):
    page = put(os.path.join(tmp, "landings.md"), b"header\n")
    payload = "@%s\nkey:\n| w5 | merged |\n" % page
    for _ in range(2):
        done = run(["round"], tmp, payload)
        assert done.returncode == 2, done.returncode
        assert "key: directive needs text" in err(done), err(done)
        assert raw(page) == b"header\n", raw(page)


def test_two_blocks_may_share_one_target(tmp):
    landings = put(os.path.join(tmp, "landings.md"), b"header\n")
    payload = "@%s\nfirst\n@%s\nsecond\n" % (landings, landings)
    done = run(["round"], tmp, payload)
    assert done.returncode == 0, err(done)
    assert raw(landings) == b"header\nfirst\nsecond\n", raw(landings)


def test_one_file_is_one_target_however_it_is_spelled(tmp):
    """Two spellings of one path are one target, so the second block sees the first."""
    page = put(os.path.join(tmp, "landings.md"), b"header\n")
    if os.path.normcase("A") != "A":
        other = page.upper()
    else:
        other = os.path.join(tmp, "link.md")
        try:
            os.symlink(page, other)
        except (AttributeError, NotImplementedError, OSError) as reason:
            print("note  no second spelling available here: %s" % reason)
            return
    payload = "@%s\nfirst\n@%s\nsecond\n" % (page, other)
    done = run(["round"], tmp, payload)
    assert done.returncode == 0, err(done)
    lines = out(done).strip().split("\n")
    assert len(lines) == 2, out(done)
    assert lines[0].startswith(page + ":") and lines[1].startswith(other + ":"), out(done)
    assert raw(page) == b"header\nfirst\nsecond\n", raw(page)


def test_a_link_and_the_file_behind_it_are_one_target(tmp):
    """A junction, or a directory symlink, is the same file behind another spelling."""
    room = os.path.join(tmp, "room")
    os.makedirs(room)
    page = put(os.path.join(room, "landings.md"), b"header\n")
    link = os.path.join(tmp, "link")
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, room],
                              capture_output=True, timeout=60)
        ok, why = made.returncode == 0, made.stderr.decode("utf-8", "replace").strip()
    else:
        ok, why = True, ""
        try:
            os.symlink(room, link)
        except (AttributeError, NotImplementedError, OSError) as reason:
            ok, why = False, str(reason)
    if not ok:
        print("note  no link could be made here: %s" % why)
        return
    payload = "@%s\nfirst\n@%s\nsecond\n" % (os.path.join(link, "landings.md"), page)
    done = run(["round"], tmp, payload)
    assert done.returncode == 0, err(done)
    assert len(out(done).strip().split("\n")) == 2, out(done)
    assert raw(page) == b"header\nfirst\nsecond\n", raw(page)


def test_a_write_that_fails_after_the_probe_rolls_the_others_back(tmp):
    """A handle held on the third target passes the r+b probe and blocks the rename."""
    pages = [put(os.path.join(tmp, "p%d.md" % i), b"header %d\n" % i) for i in (1, 2, 3)]
    before = [raw(page) for page in pages]
    payload = "".join("@%s\nline %d\n" % (page, i) for i, page in enumerate(pages))
    held = open(pages[2], "rb")
    try:
        done = run(["round"], tmp, payload)
    finally:
        held.close()
    names_the_third = True
    if done.returncode == 0:  # a held handle does not block a rename here
        names_the_third = False
        for page, data in zip(pages, before):
            put(page, data)
        os.chmod(tmp, 0o500)  # no temporary file can be created, so the first write fails
        try:
            done = run(["round"], tmp, payload)
        finally:
            os.chmod(tmp, 0o700)
        if done.returncode == 0:
            print("note  neither a held handle nor a read only directory blocks a write here")
            return
    assert done.returncode == 2, err(done)
    assert "write failed" in err(done), err(done)
    if names_the_third:
        assert "ERROR %s: write failed:" % pages[2] in err(done), err(done)
    for page, data in zip(pages, before):
        assert raw(page) == data, raw(page)
    left = [name for name in os.listdir(tmp) if name.startswith(".record-")]
    assert left == [], left


def test_a_roll_back_that_fails_names_the_file_it_left_changed(tmp):
    """In process, because a write that fails and a roll back that fails too need a stub."""
    tools = os.path.dirname(HERE)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # no .pyc beside record.py: only tmp is written
    try:
        import record
    finally:
        sys.dont_write_bytecode = bytecode

    first = put(os.path.join(tmp, "first.md"), b"header\n")
    second = put(os.path.join(tmp, "second.md"), b"header\n")
    payload = "@%s\nline one\n@%s\nline two\n" % (first, second)
    honest = record.write_bytes
    calls = []

    def once_and_then_never(path, data):
        calls.append(path)
        if len(calls) > 1:
            raise OSError(13, "Access is denied")
        return honest(path, data)

    keep = (sys.stdin, sys.stdout, sys.stderr)
    record.write_bytes = once_and_then_never
    sys.stdin = io.TextIOWrapper(io.BytesIO(payload.encode("utf-8")), encoding="utf-8")
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        code = record.main(["round"])
        said = sys.stderr.getvalue()
    finally:
        record.write_bytes = honest
        sys.stdin, sys.stdout, sys.stderr = keep
    assert code == 2, code
    assert "write failed" in said, said
    assert "left changed, the roll back failed too" in said, said
    assert calls == [first, second, first], calls
    assert raw(first) == b"header\nline one\n", "the file the roll back could not undo"
    assert raw(second) == b"header\n", raw(second)


# ---------------------------------------------------------------- config and show

def test_a_name_resolves_through_the_config_option(tmp):
    os.makedirs(os.path.join(tmp, "board"))
    page = put(os.path.join(tmp, "board", "landings.md"), b"header\n")
    where = config(tmp, {"landings": "board/landings.md"}, name="named.json")
    done = run(["add", "landings", "--config", where], tmp, "| w4 | merged |\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"header\n| w4 | merged |\n", raw(page)
    assert out(done).startswith("landings: "), "the name is echoed, not the path"


def test_a_name_resolves_through_the_environment(tmp):
    page = put(os.path.join(tmp, "log.md"), b"header\n")
    where = config(tmp, {"plan": "log.md"}, name="env.json")
    done = run(["add", "plan"], tmp, "a line\n", RECORD_CONFIG=where)
    assert done.returncode == 0, err(done)
    assert raw(page) == b"header\na line\n", raw(page)


def test_a_name_resolves_by_walking_up_from_a_subdirectory(tmp):
    page = put(os.path.join(tmp, "log.md"), b"header\n")
    config(tmp, {"plan": "log.md"})
    deep = os.path.join(tmp, "one", "two")
    os.makedirs(deep)
    done = run(["add", "plan"], tmp, "a line\n", cwd=deep)
    assert done.returncode == 0, err(done)
    assert raw(page) == b"header\na line\n", raw(page)


def test_a_bare_name_falls_back_to_a_file_in_the_current_directory(tmp):
    page = put(os.path.join(tmp, "n.md"), b"header\n")
    done = run(["add", "n.md"], tmp, "a record\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"header\na record\n", raw(page)


def test_a_config_that_is_not_there_only_matters_to_a_name(tmp):
    """The config is read on the first name, so a path never pays for it."""
    page = put(os.path.join(tmp, "log.md"), b"header\n")
    missing = os.path.join(tmp, "nowhere.json")
    done = run(["add", page, "--config", missing], tmp, "a record\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"header\na record\n", raw(page)
    named = run(["add", "plan", "--config", missing], tmp, "a record\n")
    assert named.returncode == 1, named.returncode
    assert "nowhere.json" in err(named), err(named)


def test_a_name_without_a_config_is_a_usage_error(tmp):
    done = run(["add", "plan"], tmp, "a line\n", cwd=tmp)
    assert done.returncode == 1, done.returncode
    assert "record.json" in err(done), err(done)


def test_a_tilde_in_the_config_expands(tmp):
    """HOME and USERPROFILE point at the temporary directory, so ~ lands inside it."""
    page = put(os.path.join(tmp, "home-log.md"), b"header\n")
    where = config(tmp, {"plan": "~/home-log.md"}, name="tilde.json")
    done = run(["add", "plan", "--config", where], tmp, "a line\n")
    assert done.returncode == 0, err(done)
    assert raw(page) == b"header\na line\n", raw(page)


def test_show_without_a_target_lists_the_config(tmp):
    put(os.path.join(tmp, "log.md"), b"one\r\ntwo\r\n")
    where = config(tmp, {"plan": "log.md", "gone": "missing.md"}, name="show.json")
    done = run(["show", "--config", where], tmp)
    assert done.returncode == 0, err(done)
    lines = out(done).strip().split("\n")
    assert lines[0].split() == ["name", "path", "exists", "bytes", "eol", "bom"], lines
    row = [line for line in lines if line.startswith("plan")][0].split()
    assert row[2:] == ["yes", "10", "CRLF", "no"], row
    assert [line for line in lines if line.startswith("gone")][0].split()[2] == "no", lines


def test_show_with_a_target_prints_the_tail(tmp):
    page = put(os.path.join(tmp, "log.md"), b"one\ntwo\nthree\nfour\n")
    done = run(["show", page, "--tail", "2"], tmp)
    assert done.returncode == 0, err(done)
    assert out(done).strip().split("\n") == ["three", "four"], out(done)
    assert raw(page) == b"one\ntwo\nthree\nfour\n", "show never writes"


def test_the_cli_compiles(tmp):
    done = subprocess.run(
        [sys.executable, "-c",
         "import py_compile, sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)",
         RECORD, os.path.join(tmp, "record.pyc")],
        capture_output=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")


def scrub(tmp):
    """A case that failed may leave a read only file behind, and Windows will not
    delete one, so every file is made writable before the directory goes."""
    for root, _, files in os.walk(tmp):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IREAD | stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    passed, failed = 0, []
    for test in tests:
        tmp = tempfile.mkdtemp(prefix="record-tests-")
        try:
            test(tmp)
            passed += 1
            print("pass  %s" % test.__name__)
        except Exception as error:
            failed.append((test.__name__, error))
            print("FAIL  %s: %s" % (test.__name__, error))
        finally:
            scrub(tmp)
    print("")
    print("%d passed, %d failed, %d total" % (passed, len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
