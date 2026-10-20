#!/usr/bin/env python3
"""Tests for scripts/video-digest.py on synthetic clips that ffmpeg generates in a temp folder.

No real recording and no model: the transcription engine is replaced by the fake the tool reads
from VIDEO_DIGEST_FAKE_ENGINE (fixed segments per audio stream, optionally with their words), by a
fake whisper-cli on PATH or named by --whisper-cli, by a fake ffmpeg first on PATH that lists the
whisper filter and answers it while every other call goes to the real ffmpeg, or by fake
faster_whisper and whisper modules on PYTHONPATH, so each adapter's command line and output parser
run for real while nothing is downloaded. The three-stream order test speaks its sentences with
Windows SAPI (System.Speech) and is skipped where that is missing. VIDEO_DIGEST_TOOL points the
suite at another copy of the tool, which is how a mutant is shown to turn a pin red.

Run:
    python scripts/tests/test-video-digest.py
"""
import array
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TOOL = os.environ.get("VIDEO_DIGEST_TOOL") or os.path.join(ROOT, "scripts", "video-digest.py")
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
TIMEOUT = 240
SEAMS = ("VIDEO_DIGEST_FAKE_ENGINE", "VIDEO_DIGEST_COMPARE", "VIDEO_DIGEST_STEP_TIMEOUT")
HEADING = re.compile(r"^### \[(\d+):(\d\d):(\d\d)\]$", re.MULTILINE)
LINE = re.compile(r"^\[(\d+:\d\d:\d\d)\] ([^:]+): (.*)$")


def setUpModule():
    if not (FFMPEG and FFPROBE):
        raise RuntimeError("these tests need ffmpeg and ffprobe on PATH")


def load_tool():
    spec = importlib.util.spec_from_file_location("video_digest_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ffmpeg(*args):
    res = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-y"] + list(args),
                         capture_output=True, text=True, timeout=TIMEOUT)
    if res.returncode != 0:
        raise AssertionError("ffmpeg failed: %s" % res.stderr)


def box_slide(x, seconds, size="640x360", width=213, rate=2):
    height = size.split("x")[1]
    return ("color=c=black:s=%s:r=%d:d=%d,drawbox=x=%d:y=0:w=%d:h=%s:color=white:t=fill"
            % (size, rate, seconds, x, width, height))


def make_clip(folder, name, video, audio=(), seconds=10):
    """A Matroska clip: one video stream from a lavfi graph, one audio stream per lavfi source."""
    path = os.path.join(folder, name)
    args = ["-f", "lavfi", "-i", video]
    for source in audio:
        args += ["-f", "lavfi", "-i", source]
    args += ["-map", "0:v"]
    for index in range(len(audio)):
        args += ["-map", "%d:a" % (index + 1)]
    args += ["-c:v", "mpeg4", "-q:v", "4"]
    if audio:
        args += ["-c:a", "pcm_s16le"]
    ffmpeg(*(args + ["-t", str(seconds), path]))
    return path


def run_tool(clip, *extra, env=None):
    full = {k: v for k, v in os.environ.items() if k not in SEAMS}
    full.update(env or {})
    return subprocess.run([sys.executable, TOOL, clip] + list(extra), capture_output=True,
                          text=True, timeout=TIMEOUT, env=full)


def digest_of(clip):
    with open(os.path.splitext(clip)[0] + ".digest.md", encoding="utf-8") as handle:
        return handle.read()


def kept_seconds(text):
    return [int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in HEADING.findall(text)]


def transcript_lines(text):
    section = text.split("## Transcript", 1)[1].split("## Keyframes", 1)[0]
    found = [LINE.match(line) for line in section.splitlines()]
    return [match.groups() for match in found if match]


def frame_blocks(text):
    """(seconds, the text under the heading) per keyframe."""
    section = text.split("## Keyframes", 1)[1]
    parts = HEADING.split(section)
    return [(int(parts[i]) * 3600 + int(parts[i + 1]) * 60 + int(parts[i + 2]), parts[i + 3])
            for i in range(1, len(parts), 4)]


def fake_engine(folder, rows):
    path = os.path.join(folder, "fake-engine.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle)
    return {"VIDEO_DIGEST_FAKE_ENGINE": path}


class ClipTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vdg-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def ok(self, res):
        self.assertEqual(res.returncode, 0, res.stderr)


class VideoDigestSlideChangeTest(ClipTest):
    def test_keeps_each_slide_change_and_both_ends(self):
        slides = "%s[a];%s[b];%s[c];[a][b][c]concat=n=3:v=1:a=0[out0]" % (
            box_slide(0, 20), box_slide(213, 20), box_slide(426, 20))
        clip = make_clip(self.dir, "slides.mkv", slides, seconds=60)
        self.ok(run_tool(clip, "--no-transcript"))
        kept = kept_seconds(digest_of(clip))
        self.assertEqual(len(kept), 4, kept)
        self.assertEqual(kept[0], 0, kept)
        self.assertGreaterEqual(kept[-1], 58, kept)
        for change in (20, 40):
            self.assertTrue(any(abs(when - change) <= 2 for when in kept), (change, kept))
        frames = [f for f in os.listdir(os.path.join(self.dir, "slides.frames")) if f.endswith(".jpg")]
        self.assertEqual(len(frames), 4, frames)


class VideoDigestSlowDriftTest(ClipTest):
    def test_the_last_kept_rule_catches_drift_the_previous_frame_misses(self):
        # A 200 px white bar crosses a 640 px black frame at 1 px a second: about 0.99 against
        # the second before, below 0.90 against a frame kept some 14 seconds earlier.
        drift = ("color=c=black:s=640x360:r=2:d=180[bg];color=c=white:s=200x360:r=2:d=180[bar];"
                 "[bg][bar]overlay=x=20+t:y=0[out0]")
        clip = make_clip(self.dir, "drift.mkv", drift, seconds=180)
        self.ok(run_tool(clip, "--no-transcript", "--floor", "1000"))
        last_kept = len(kept_seconds(digest_of(clip)))
        self.ok(run_tool(clip, "--no-transcript", "--floor", "1000",
                         env={"VIDEO_DIGEST_COMPARE": "previous"}))
        previous_only = len(kept_seconds(digest_of(clip)))
        self.assertGreater(last_kept, 2)
        self.assertLess(last_kept, 18)
        self.assertEqual(previous_only, 2)
        self.assertLess(previous_only, last_kept)


class VideoDigestFloorTest(ClipTest):
    def test_a_static_clip_keeps_a_frame_every_floor_seconds(self):
        clip = make_clip(self.dir, "static.mkv", "smptebars=s=640x360:r=1:d=150", seconds=150)
        self.ok(run_tool(clip, "--no-transcript", "--floor", "30"))
        kept = kept_seconds(digest_of(clip))
        self.assertEqual(kept[0], 0, kept)
        self.assertGreaterEqual(kept[-1], 148, kept)
        self.assertLessEqual(max(b - a for a, b in zip(kept, kept[1:])), 30, kept)
        self.assertEqual(len(kept), 6, kept)


class VideoDigestSilentTrackTest(ClipTest):
    def test_a_silent_stream_is_reported_and_not_transcribed(self):
        clip = make_clip(self.dir, "tracks.mkv", "color=c=gray:s=160x90:r=1:d=6",
                         audio=("sine=frequency=440:sample_rate=16000", "anullsrc=r=16000:cl=mono"),
                         seconds=6)
        env = fake_engine(self.dir, {"0": [[0.5, 2.0, "words from the tone side"]],
                                     "1": [[0.5, 2.0, "ghost words from the silent side"]]})
        res = run_tool(clip, env=env)
        self.ok(res)
        self.assertIn("audio track 2 is silent: check the recorder's track settings", res.stderr)
        self.assertNotIn("audio track 1 is silent", res.stderr)
        text = digest_of(clip)
        self.assertIn("- Silent tracks: audio track 2 (track2)\n", text)
        self.assertEqual(transcript_lines(text), [("0:00:00", "track1", "words from the tone side")])


class VideoDigestEchoRuleTest(ClipTest):
    def test_only_long_overlapping_matching_first_stream_segments_are_dropped(self):
        clip = make_clip(self.dir, "echo.mkv", "color=c=gray:s=160x90:r=1:d=45",
                         audio=("sine=frequency=440:sample_rate=16000",
                                "sine=frequency=660:sample_rate=16000"), seconds=45)
        rows = {
            "0": [[10.0, 13.0, "Let us review the budget"],   # 5 words, overlaps, matches: dropped
                  [16.0, 18.0, "open the next slide"],        # 4 words, overlaps, matches: dropped
                  [20.0, 21.0, "yes ok"],                     # 2 words: kept
                  [24.0, 25.0, "sounds good thanks"],         # 3 words: kept
                  [30.0, 33.0, "we should ship it today"],    # matches, no overlap: kept
                  [36.0, 38.0, "I disagree with that plan"]],  # overlaps, no match: kept
            "1": [[10.5, 13.5, "let us review the budget."],
                  [16.2, 18.2, "Open the next slide."],
                  [20.0, 21.0, "yes ok"],
                  [24.0, 25.0, "sounds good thanks"],
                  [36.0, 38.0, "the numbers look fine to me"],
                  [40.0, 43.0, "we should ship it today"]],
        }
        res = run_tool(clip, "--track-labels", "me,others", "--no-frames",
                       env=fake_engine(self.dir, rows))
        self.ok(res)
        text = digest_of(clip)
        self.assertEqual(transcript_lines(text), [
            ("0:00:10", "others", "let us review the budget. Open the next slide."),
            ("0:00:20", "me", "yes ok"),
            ("0:00:20", "others", "yes ok"),
            ("0:00:24", "me", "sounds good thanks"),
            ("0:00:24", "others", "sounds good thanks"),
            ("0:00:30", "me", "we should ship it today I disagree with that plan"),
            ("0:00:36", "others", "the numbers look fine to me we should ship it today"),
        ])
        self.assertIn("- Echo segments dropped: 2\n", text)


class VideoDigestMarkdownTest(ClipTest):
    def test_sections_merged_turns_frame_index_and_clamp(self):
        slides = "%s[a];%s[b];[a][b]concat=n=2:v=1:a=0[out0]" % (
            box_slide(0, 35, "160x90", 53, 1), box_slide(106, 35, "160x90", 53, 1))
        clip = make_clip(self.dir, "talk.mkv", slides,
                         audio=("sine=frequency=440:sample_rate=16000",), seconds=70)
        rows = {"0": [[0.0, 1.5, "hello there"], [1.5, 3.0, "second part"],
                      [61.0, 62.0, "a new minute"], [65.0, 75.0, "tail runs past the end"]]}
        self.ok(run_tool(clip, env=fake_engine(self.dir, rows)))
        text = digest_of(clip)
        top = text.split("## Metadata", 1)[0]
        self.assertIn("Read the Transcript first. Open a frame only where the screen matters", top)
        self.assertIn("24 tokens (160x90)", top)
        self.assertLess(text.index("## Metadata"), text.index("## Transcript"))
        self.assertLess(text.index("## Transcript"), text.index("## Keyframes"))
        for line in ("- Duration: 0:01:10", "- Resolution: 160x90", "- Audio streams: 1 (1: speaker)",
                     "- Engine: fake", "- Model: none", "- Language: auto",
                     "- Frames: 3 (threshold 0.90, floor 60 s, min gap 5 s, long edge 1280 px)",
                     "- Silent tracks: none", "- Echo segments dropped: 0", "- Clamped segments: 1"):
            self.assertIn("\n" + line, text)
        lines = transcript_lines(text)
        self.assertEqual(lines, [("0:00:00", "speaker", "hello there second part"),
                                 ("0:01:01", "speaker", "a new minute tail runs past the end")])
        blocks = frame_blocks(text)
        self.assertEqual([when for when, _ in blocks][:2], [0, 35])
        self.assertEqual(len(blocks), 3)
        for when, body in blocks:
            named = re.search(r"^Transcript line: \[(\d+:\d\d:\d\d)\] (\w+): (.*)$", body, re.MULTILINE)
            self.assertIsNotNone(named, body)
            expected = lines[0] if when < 61 else lines[1]
            self.assertEqual(named.groups(), expected, when)
            link = re.search(r"\]\(<(talk\.frames/[^>]+\.jpg)>\)", body)
            self.assertTrue(link and os.path.isfile(os.path.join(self.dir, link.group(1))), body)
        first = sorted(os.listdir(os.path.join(self.dir, "talk.frames")))[0]
        size = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=width,height", "-of",
                               "csv=p=0", os.path.join(self.dir, "talk.frames", first)],
                              capture_output=True, text=True, timeout=TIMEOUT).stdout.strip()
        self.assertEqual(size, "160,90")  # a 160x90 source is not upscaled to --frame-edge

    def test_clamp_brings_every_time_inside_the_duration(self):
        tool = load_tool()
        self.assertEqual(tool.clamp([(5.0, 12.0, " a  b "), (9.0, 11.0, "c"), (1.0, 2.0, "d")], 8.0),
                         ([(5.0, 8.0, "a b"), (8.0, 8.0, "c"), (1.0, 2.0, "d")], 2))


class VideoDigestNoEngineTest(ClipTest):
    def test_exit_3_names_what_to_install(self):
        clip = make_clip(self.dir, "tone.mkv", "color=c=gray:s=160x90:r=1:d=4",
                         audio=("sine=frequency=440:sample_rate=16000",), seconds=4)
        empty = os.path.join(self.dir, "empty-path")
        os.makedirs(empty)
        res = run_tool(clip, "--ffmpeg", os.path.dirname(FFMPEG), "--model", "large-v3",
                       "--engine-python", os.path.join(self.dir, "no-python"), env={"PATH": empty})
        self.assertEqual(res.returncode, 3, res.stderr)
        for needle in ("no transcription engine found", "whisper filter", "whisper-cli on PATH",
                       "pip install faster-whisper", "pip install openai-whisper", "--no-transcript"):
            self.assertIn(needle, res.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "tone.digest.md")))


FAKE_CLI = """import json, os, sys
args = sys.argv[1:]
wav = args[args.index("-f") + 1]
assert os.path.getsize(wav) > 0, wav
with open(args[args.index("-of") + 1] + ".srt", "w") as handle:
    handle.write("1\\n00:00:01,250 --> 00:00:02,500\\nfrom the cli\\n\\n"
                 "2\\n00:00:02,500 --> 00:00:03,000\\nsecond cue\\n")
with open(os.environ["FAKE_CLI_LOG"], "w") as handle:
    json.dump(args, handle)
"""

FAKE_RECORD = """import json, os, time
def record(kw):
    if os.environ.get("FAKE_SLEEP"):
        time.sleep(float(os.environ["FAKE_SLEEP"]))
    if os.environ.get("FAKE_KW_LOG"):
        with open(os.environ["FAKE_KW_LOG"], "w") as handle:
            json.dump(kw, handle)
"""

FAKE_FASTER = FAKE_RECORD + """import os
class Segment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text
class WhisperModel:
    def __init__(self, name):
        self.name = name
    def transcribe(self, wav, language=None, **kw):
        assert os.path.getsize(wav) > 0, wav
        record(kw)
        return iter([Segment(0.5, 1.5, " fast %s %s" % (self.name, language))]), None
"""

FAKE_OPENAI = FAKE_RECORD + """import os
class Model:
    def __init__(self, name):
        self.name = name
    def transcribe(self, wav, language=None, **kw):
        assert os.path.getsize(wav) > 0, wav
        record(kw)
        return {"segments": [{"start": 0.5, "end": 1.5, "text": " open %s %s" % (self.name, language)}]}
def load_model(name):
    return Model(name)
"""


class AdapterBase(ClipTest):
    def setUp(self):
        super().setUp()
        self.clip = make_clip(self.dir, "voice.mkv", "color=c=gray:s=160x90:r=1:d=5",
                              audio=("sine=frequency=440:sample_rate=16000",), seconds=5)

    def fake_cli(self):
        """(env, model, log): a fake whisper-cli first on PATH that records its argv."""
        bin_dir = os.path.join(self.dir, "bin")
        os.makedirs(bin_dir)
        script = os.path.join(bin_dir, "fake_cli.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(FAKE_CLI)
        if os.name == "nt":
            with open(os.path.join(bin_dir, "whisper-cli.bat"), "w") as handle:
                handle.write('@"%s" "%s" %%*\n' % (sys.executable, script))
        else:
            wrapper = os.path.join(bin_dir, "whisper-cli")
            with open(wrapper, "w") as handle:
                handle.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, script))
            os.chmod(wrapper, 0o755)
        model = os.path.join(self.dir, "ggml-fake.bin")
        with open(model, "wb") as handle:
            handle.write(b"lmgg")
        log = os.path.join(self.dir, "cli-args.json")
        env = {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""), "FAKE_CLI_LOG": log}
        return env, model, log

    def python_engines(self):
        fakes = os.path.join(self.dir, "fakes")
        for package, body in (("faster_whisper", FAKE_FASTER), ("whisper", FAKE_OPENAI)):
            os.makedirs(os.path.join(fakes, package))
            with open(os.path.join(fakes, package, "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write(body)
        return {"PYTHONPATH": fakes}


class VideoDigestEngineAdapterTest(AdapterBase):
    def test_whisper_cli_command_line_and_srt(self):
        env, model, log = self.fake_cli()
        self.ok(run_tool(self.clip, "--engine", "whisper-cli", "--model", model, "--language", "es",
                         "--no-frames", env=env))
        text = digest_of(self.clip)
        self.assertEqual(transcript_lines(text), [("0:00:01", "speaker", "from the cli second cue")])
        self.assertIn("\n- Engine: whisper-cli\n", text)
        with open(log, encoding="utf-8") as handle:
            args = json.load(handle)
        self.assertEqual(args[args.index("-m") + 1], model)
        self.assertEqual(args[args.index("-l") + 1], "es")
        self.assertIn("-osrt", args)

    def test_python_engines_and_the_auto_order(self):
        env = self.python_engines()
        cases = ((("--engine", "faster-whisper", "--model", "tiny"), "faster-whisper", "fast tiny None"),
                 (("--engine", "openai-whisper", "--model", "base", "--language", "de"),
                  "openai-whisper", "open base de"),
                 (("--model", "small"), "faster-whisper", "fast small None"))
        for extra, engine, said in cases:
            self.ok(run_tool(self.clip, "--no-frames", *extra, env=env))
            text = digest_of(self.clip)
            self.assertIn("\n- Engine: %s\n" % engine, text)
            self.assertEqual(transcript_lines(text), [("0:00:00", "speaker", said)])


class OpenaiWhisperBeamTest(AdapterBase):
    def test_openai_whisper_gets_the_command_line_decoding_defaults(self):
        log = os.path.join(self.dir, "kw.json")
        env = dict(self.python_engines(), FAKE_KW_LOG=log)
        self.ok(run_tool(self.clip, "--no-frames", "--engine", "openai-whisper", "--model", "base",
                         env=env))
        with open(log, encoding="utf-8") as handle:
            kw = json.load(handle)
        self.assertEqual((kw.get("beam_size"), kw.get("best_of")), (5, 5), kw)


class FasterWhisperBeamTest(AdapterBase):
    def test_faster_whisper_gets_beam_size_5_explicitly(self):
        log = os.path.join(self.dir, "kw.json")
        env = dict(self.python_engines(), FAKE_KW_LOG=log)
        self.ok(run_tool(self.clip, "--no-frames", "--engine", "faster-whisper", "--model", "tiny",
                         env=env))
        with open(log, encoding="utf-8") as handle:
            kw = json.load(handle)
        self.assertEqual(kw.get("beam_size"), 5, kw)


class VideoDigestDefaultTimeoutTest(AdapterBase):
    def test_the_default_timeouts_follow_the_duration(self):
        tool = load_tool()
        d = 100
        self.assertEqual(
            {step: tool.default_timeout(step, d) for step in
             ("probe", "filters", "engine-probe", "image-size", "frame", "audio", "decode", "engine")},
            {"probe": 60, "filters": 60, "engine-probe": 180, "image-size": 60, "frame": 60,
             "audio": 110, "decode": 320, "engine": 1200})

    def test_a_hung_engine_still_exits_4_naming_the_step(self):
        env = dict(self.python_engines(), FAKE_SLEEP="30", VIDEO_DIGEST_STEP_TIMEOUT="engine=2")
        res = run_tool(self.clip, "--no-frames", "--engine", "faster-whisper", "--model", "tiny",
                       env=env)
        self.assertEqual(res.returncode, 4, res.stderr)
        self.assertEqual(res.stderr.strip().splitlines(),
                         ["video-digest: step engine timed out after 2 s"])

    def test_a_slow_engine_import_is_reported_as_slow_not_missing(self):
        env = dict(self.python_engines(), VIDEO_DIGEST_STEP_TIMEOUT="engine-probe=0.01")
        res = run_tool(self.clip, "--no-frames", "--engine", "openai-whisper", "--model", "tiny",
                       env=env)
        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertIn("whisper did not import in", res.stderr)
        self.assertIn("within 0.01 s", res.stderr)

    def test_a_hung_gray_frame_decode_exits_4_naming_the_step(self):
        res = run_tool(self.clip, "--no-transcript",
                       env={"VIDEO_DIGEST_STEP_TIMEOUT": "decode=0.01"})
        self.assertEqual(res.returncode, 4, res.stderr)
        self.assertEqual(res.stderr.strip().splitlines(),
                         ["video-digest: step decode timed out after 0.01 s"])


class VideoDigestMinGapTest(ClipTest):
    def test_changes_closer_than_the_gap_wait_and_zero_restores_every_change(self):
        noise = "nullsrc=s=160x90:r=1:d=30,geq=lum='random(1)*255':cb=128:cr=128"
        clip = make_clip(self.dir, "noise.mkv", noise, seconds=30)
        self.ok(run_tool(clip, "--no-transcript"))
        kept = kept_seconds(digest_of(clip))
        self.assertEqual(kept[0], 0, kept)
        self.assertGreaterEqual(kept[-1], 28, kept)
        self.assertGreaterEqual(min(b - a for a, b in zip(kept[:-1], kept[1:-1])), 5, kept)
        self.assertLessEqual(len(kept), 8, kept)
        self.ok(run_tool(clip, "--no-transcript", "--min-gap", "0"))
        self.assertGreater(len(kept_seconds(digest_of(clip))), 20)

    def test_the_gap_defers_a_change_to_the_first_frame_after_it(self):
        tool = load_tool()
        size = tool.GRAY_SIZE[0] * tool.GRAY_SIZE[1]
        a = bytes([10]) * size
        b = bytes([200]) * size
        frames = list(enumerate([a, b, b, b, b, b, b, b]))
        self.assertEqual(tool.select_keyframes(frames, 0.9, 1000, min_gap=5), [0, 5, 7])
        self.assertEqual(tool.select_keyframes(frames, 0.9, 1000, min_gap=0), [0, 1, 7])


class VideoDigestOneBasedTracksTest(ClipTest):
    def test_a_silent_second_stream_is_track_2_everywhere(self):
        clip = make_clip(self.dir, "tracks.mkv", "color=c=gray:s=160x90:r=1:d=4",
                         audio=("sine=frequency=440:sample_rate=16000", "anullsrc=r=16000:cl=mono"),
                         seconds=4)
        res = run_tool(clip, "--no-frames", env=fake_engine(self.dir, {"0": [[0.5, 2.0, "tone"]]}))
        self.ok(res)
        self.assertIn("audio track 2 is silent", res.stderr)
        self.assertNotIn("audio track 1 is silent", res.stderr)
        text = digest_of(clip)
        self.assertIn("\n- Audio streams: 2 (1: track1, 2: track2)\n", text)
        self.assertIn("\n- Silent tracks: audio track 2 (track2)\n", text)


class WhisperCliArgsTest(AdapterBase):
    def argv(self, *extra):
        env, model, log = self.fake_cli()
        self.ok(run_tool(self.clip, "--engine", "whisper-cli", "--model", model, "--no-frames",
                         *extra, env=env))
        with open(log, encoding="utf-8") as handle:
            return json.load(handle)

    def test_default_threads_and_no_prompt_or_vad_unless_given(self):
        tool = load_tool()
        args = self.argv()
        self.assertEqual(args[args.index("-t") + 1], str(tool.default_threads()))
        for flag in ("--prompt", "--vad", "-vm"):
            self.assertNotIn(flag, args)

    def test_explicit_threads_prompt_and_vad_model(self):
        vad = os.path.join(self.dir, "ggml-vad.bin")
        with open(vad, "wb") as handle:
            handle.write(b"lmgg")
        prompt = "Hello, this is a meeting. Names: Ana, Bo."
        args = self.argv("--threads", "16", "--prompt", prompt, "--vad-model", vad)
        self.assertEqual(args[args.index("-t") + 1], "16")
        self.assertEqual(args[args.index("--prompt") + 1], prompt)
        at = args.index("--vad")
        self.assertEqual(args[at + 1:at + 3], ["-vm", vad])

    def test_default_threads_is_cores_minus_four_and_at_least_four(self):
        tool = load_tool()
        self.assertEqual(tool.default_threads(), max(4, (os.cpu_count() or 8) - 4))

    def test_python_engines_get_threads_and_prompt_only_when_given(self):
        # The fake modules take neither keyword: passing them by default would raise.
        env = self.python_engines()
        self.ok(run_tool(self.clip, "--no-frames", "--engine", "faster-whisper", "--model", "tiny",
                         env=env))


class VideoDigestQuietSpeechTest(ClipTest):
    def test_a_short_quiet_tone_in_long_silence_is_transcribed_and_pure_silence_is_not(self):
        # 2 s at -28 dB in 65 s: peak about -46 dB, mean about -64 dB (below the old mean floor).
        quiet = "sine=frequency=440:sample_rate=16000:duration=2,volume=-28dB,apad=whole_dur=65"
        clip = make_clip(self.dir, "quiet.mkv", "color=c=gray:s=160x90:r=1:d=65",
                         audio=(quiet, "anullsrc=r=16000:cl=mono"), seconds=65)
        rows = {"0": [[0.2, 1.8, "a quiet remark"]], "1": [[0.2, 1.8, "ghost"]]}
        res = run_tool(clip, "--no-frames", env=fake_engine(self.dir, rows))
        self.ok(res)
        self.assertNotIn("audio track 1 is silent", res.stderr)
        self.assertIn("audio track 2 is silent", res.stderr)
        self.assertEqual(transcript_lines(digest_of(clip)), [("0:00:00", "track1", "a quiet remark")])


class VideoDigestNoUpscaleTest(ClipTest):
    def test_a_640x360_clip_keeps_640x360_frames_and_the_token_note_says_so(self):
        clip = make_clip(self.dir, "small.mkv", "smptebars=s=640x360:r=1:d=4", seconds=4)
        self.ok(run_tool(clip, "--no-transcript"))
        self.assertIn("299 tokens (640x360)", digest_of(clip))
        for name in os.listdir(os.path.join(self.dir, "small.frames")):
            size = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=width,height",
                                   "-of", "csv=p=0", os.path.join(self.dir, "small.frames", name)],
                                  capture_output=True, text=True, timeout=TIMEOUT).stdout.strip()
            self.assertEqual(size, "640,360")
        self.ok(run_tool(clip, "--no-transcript", "--frame-edge", "320"))
        self.assertIn("(320x180)", digest_of(clip))


class VideoDigestModelBasenameTest(ClipTest):
    def test_the_metadata_shows_the_model_file_name_and_no_folder(self):
        clip = make_clip(self.dir, "voice.mkv", "color=c=gray:s=160x90:r=1:d=3",
                         audio=("sine=frequency=440:sample_rate=16000",), seconds=3)
        folder = os.path.join(self.dir, "privatefolder", "sub")
        res = run_tool(clip, "--no-frames", "--model", os.path.join(folder, "ggml-x.bin"),
                       "--vad-model", os.path.join(folder, "ggml-vad.bin"),
                       env=fake_engine(self.dir, {"0": [[0.0, 1.0, "hi there"]]}))
        self.ok(res)
        text = digest_of(clip)
        self.assertIn("\n- Model: ggml-x.bin\n", text)
        self.assertIn("\n- VAD model: ggml-vad.bin\n", text)
        self.assertNotIn("privatefolder", text)
        self.assertNotIn(self.dir, text)


class VideoDigestTimeoutExitTest(ClipTest):
    def test_a_timed_out_step_exits_4_with_one_line_and_no_traceback(self):
        clip = make_clip(self.dir, "voice.mkv", "color=c=gray:s=160x90:r=1:d=3",
                         audio=("sine=frequency=440:sample_rate=16000",), seconds=3)
        res = run_tool(clip, "--no-frames", env={"VIDEO_DIGEST_STEP_TIMEOUT": "audio=0.01"})
        self.assertEqual(res.returncode, 4, res.stderr)
        self.assertNotIn("Traceback", res.stderr)
        self.assertEqual(res.stderr.strip().splitlines(),
                         ["video-digest: step audio timed out after 0.01 s"])

    def test_an_engine_python_that_cannot_start_is_exit_3_naming_the_engine(self):
        tool = load_tool()
        engine = tool.PythonWhisper("faster-whisper", os.path.join(self.dir, "no-python"), "tiny",
                                    "auto", FFMPEG, None, None)
        with self.assertRaises(tool.DigestError) as caught:
            engine.transcribe(os.path.join(self.dir, "x.wav"), 0, self.dir)
        self.assertEqual(caught.exception.code, 3)
        self.assertIn("faster-whisper", str(caught.exception))


class VideoDigestTrackChoiceTest(ClipTest):
    def test_only_the_chosen_streams_are_transcribed_with_the_given_labels(self):
        clip = make_clip(self.dir, "three.mkv", "color=c=gray:s=160x90:r=1:d=5",
                         audio=("sine=frequency=330:sample_rate=16000",
                                "sine=frequency=440:sample_rate=16000",
                                "sine=frequency=660:sample_rate=16000"), seconds=5)
        rows = {"0": [[0.5, 1.5, "mixed track words"]], "1": [[0.5, 1.5, "my own words"]],
                "2": [[2.0, 3.0, "their words"]]}
        env = fake_engine(self.dir, rows)
        res = run_tool(clip, "--no-frames", "--tracks", "2,3", "--track-labels", "me,others", env=env)
        self.ok(res)
        text = digest_of(clip)
        self.assertEqual(transcript_lines(text), [("0:00:00", "me", "my own words"),
                                                  ("0:00:02", "others", "their words")])
        self.assertNotIn("mixed track", text)
        self.assertIn("\n- Audio streams: 3 (2: me, 3: others)\n", text)
        res = run_tool(clip, "--no-frames", "--tracks", "4", env=env)
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("--tracks 4", res.stderr)
        res = run_tool(clip, "--no-frames", "--tracks", "0,x", env=env)
        self.assertEqual(res.returncode, 2, res.stderr)


FAKE_BROKEN = """class Segment:
    def __init__(self):
        self.start, self.end, self.text = "soon", 1.0, "unparsable start"
class WhisperModel:
    def __init__(self, name):
        pass
    def transcribe(self, wav, language=None, **kw):
        return iter([Segment()]), None
"""


class VideoDigestEngineFailureTest(ClipTest):
    def test_output_an_engine_wrote_that_cannot_be_parsed_exits_3(self):
        clip = make_clip(self.dir, "voice.mkv", "color=c=gray:s=160x90:r=1:d=3",
                         audio=("sine=frequency=440:sample_rate=16000",), seconds=3)
        fakes = os.path.join(self.dir, "fakes", "faster_whisper")
        os.makedirs(fakes)
        with open(os.path.join(fakes, "__init__.py"), "w", encoding="utf-8") as handle:
            handle.write(FAKE_BROKEN)
        res = run_tool(clip, "--engine", "faster-whisper", "--model", "tiny", "--no-frames",
                       env={"PYTHONPATH": os.path.dirname(fakes)})
        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertIn("faster-whisper wrote output that cannot be parsed", res.stderr)


class VideoDigestStreamsAndExitCodesTest(ClipTest):
    def no_engine(self):
        empty = os.path.join(self.dir, "empty-path")
        os.makedirs(empty, exist_ok=True)
        return (["--ffmpeg", os.path.dirname(FFMPEG), "--engine-python",
                 os.path.join(self.dir, "no-python")], {"PATH": empty})

    def test_audio_only_has_no_frames_and_a_transcript(self):
        clip = os.path.join(self.dir, "voice.mka")
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000", "-t", "4",
               "-c:a", "pcm_s16le", clip)
        self.ok(run_tool(clip, env=fake_engine(self.dir, {"0": [[1.0, 2.0, "only a voice"]]})))
        text = digest_of(clip)
        self.assertIn("\n- Resolution: no video stream\n", text)
        self.assertIn("\n- Frames: no video stream\n", text)
        self.assertEqual(text.split("## Keyframes", 1)[1].strip(), "no video stream")
        self.assertEqual(transcript_lines(text), [("0:00:01", "speaker", "only a voice")])

    def test_no_engine_is_needed_when_nothing_is_to_be_transcribed(self):
        extra, env = self.no_engine()
        mute = make_clip(self.dir, "mute.mkv", "color=c=gray:s=160x90:r=1:d=3", seconds=3)
        self.ok(run_tool(mute, *extra, env=env))
        self.assertIn("## Transcript\n\nno audio stream\n", digest_of(mute))
        silent = make_clip(self.dir, "silent.mkv", "color=c=gray:s=160x90:r=1:d=3",
                           audio=("anullsrc=r=16000:cl=mono",), seconds=3)
        res = run_tool(silent, *extra, env=env)
        self.ok(res)
        self.assertIn("audio track 1 is silent: check the recorder's track settings", res.stderr)
        self.assertIn("## Transcript\n\nevery audio track is silent\n", digest_of(silent))

    def test_exit_4_for_a_file_that_is_not_a_recording(self):
        clip = os.path.join(self.dir, "notes.mp4")
        with open(clip, "w", encoding="utf-8") as handle:
            handle.write("not a video\n")
        res = run_tool(clip, "--no-transcript")
        self.assertEqual(res.returncode, 4, res.stderr)
        self.assertIn("cannot read the recording", res.stderr)
        res = run_tool(os.path.join(self.dir, "missing.mp4"), "--no-transcript")
        self.assertEqual(res.returncode, 4, res.stderr)

    def test_exit_2_for_missing_ffmpeg_or_a_bad_argument(self):
        clip = make_clip(self.dir, "short.mkv", "color=c=gray:s=160x90:r=1:d=2", seconds=2)
        empty = os.path.join(self.dir, "no-ffmpeg")
        os.makedirs(empty)
        res = run_tool(clip, "--ffmpeg", empty, "--no-transcript")
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("ffmpeg and ffprobe are needed", res.stderr)
        res = run_tool(clip, "--threshold", "1.5", "--no-transcript")
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("--threshold must be above 0 and at most 1", res.stderr)


class VideoDigestFilterEscapeTest(ClipTest):
    def test_a_path_with_a_drive_colon_and_graph_characters_survives_both_levels(self):
        folder = os.path.join(self.dir, "a'b,c [d];e")
        os.makedirs(folder)
        target = os.path.join(folder, "meta.txt")
        graph = "astats=metadata=1:reset=1,ametadata=mode=print:file=" + load_tool().filter_escape(target)
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-af", graph, "-f", "null", "-")
        self.assertTrue(os.path.isfile(target))
        self.assertGreater(os.path.getsize(target), 0)


class VideoDigestSelectionUnitTest(unittest.TestCase):
    def test_flat_frames_compare_by_mean_and_a_flat_frame_against_texture_is_a_change(self):
        tool = load_tool()
        size = tool.GRAY_SIZE[0] * tool.GRAY_SIZE[1]
        flat = bytes([100]) * size
        near = bytes([101]) * size
        far = bytes([200]) * size
        texture = bytes(range(256)) * (size // 256) + bytes(size % 256)
        frames = list(enumerate([flat, near, far, far, texture, texture]))
        self.assertEqual(tool.select_keyframes(frames, 0.9, 1000), [0, 2, 4, 5])


# Region mode: two or more transcribed streams.

DEFAULT_PROMPT = "Hello, everyone. Thanks for joining the meeting."


def tone_bursts(*spans):
    """A lavfi source: a 440 Hz tone at half scale inside each (start, end) span, silence elsewhere."""
    gate = "+".join("between(t,%g,%g)" % span for span in spans)
    return "aevalsrc='0.5*sin(2*PI*440*t)*(%s)':s=16000" % gate


# Stream 1 sounds at 1-3 s and 8-10 s, stream 2 at 4.5-6.5 s and 10.5-11.5 s. With REGION_PAD
# 0.25 s and REGION_GAP 1.0 s, the engine input of stream 1 holds 0.75-3.25 s at 0.0-2.5 s, a gap
# at 2.5-3.5 s, and 7.75-10.25 s at 3.5-6.0 s; stream 2 holds 4.25-6.75 s at 0.0-2.5 s and
# 10.25-11.75 s at 3.5-5.0 s.
TWO_SPEAKERS = (tone_bursts((1, 3), (8, 10)), tone_bursts((4.5, 6.5), (10.5, 11.5)))


def install_fake(folder, name, body):
    """A fake executable `name` in folder that runs body with this Python; returns its path."""
    os.makedirs(folder, exist_ok=True)
    script = os.path.join(folder, name + "_fake.py")
    with open(script, "w", encoding="utf-8") as handle:
        handle.write(body)
    if os.name == "nt":
        wrapper = os.path.join(folder, name + ".bat")
        with open(wrapper, "w") as handle:
            handle.write('@"%s" "%s" %%*\n' % (sys.executable, script))
    else:
        wrapper = os.path.join(folder, name)
        with open(wrapper, "w") as handle:
            handle.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, script))
        os.chmod(wrapper, 0o755)
    return wrapper


def ggml_file(folder, name):
    path = os.path.join(folder, name)
    with open(path, "wb") as handle:
        handle.write(b"lmgg")
    return path


SRT_STAMP = '''
def stamp(t):
    ms = int(round(t * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def write_srt(path, cues):
    with open(path, "w", encoding="utf-8") as out:
        for n, (start, end, text) in enumerate(cues, 1):
            out.write("%d\\n%s --> %s\\n %s\\n\\n" % (n, stamp(start), stamp(end), text))
'''

# A whisper-cli that logs its argv and the length of the WAV it was given, per stream
# (FAKE_CLI_LOG-<stream from 0>). With FAKE_CLI_SCRIPT ({"<stream>": [[sentence, seconds], ...]})
# it hears its input: each sentence starts at the first 10 ms window with a peak of -50 dBFS or more
# from 0.2 s after the previous one ended and lasts its seconds (a pause inside a sentence can be
# longer than one between two), each word gets an even share of it, and the times are bent the way
# the spec measured whisper-cli: with --vad the pause before a sentence goes to its first word,
# without it the word before a pause runs on to the next speech; without -ml it writes one
# segment per 30 s decoding window. Otherwise it writes FAKE_CLI_CUES {"<stream>": [[s, e, text]]}.
FAKE_CLI_WORDS = SRT_STAMP + r'''
import array, json, os, sys, wave
args = sys.argv[1:]
wav = args[args.index("-f") + 1]
base = args[args.index("-of") + 1]
stream = base.rsplit("-", 1)[1]
with wave.open(wav, "rb") as handle:
    rate = handle.getframerate()
    samples = array.array("h", handle.readframes(handle.getnframes()))
seconds = len(samples) / rate
with open("%s-%s" % (os.environ["FAKE_CLI_LOG"], stream), "w", encoding="utf-8") as log:
    json.dump({"args": args, "seconds": seconds}, log)


def sentences_heard(lengths):
    step = rate // 100
    loud = [max(map(abs, samples[i:i + step])) >= 104 for i in range(0, len(samples), step)]
    spans, at = [], 0
    for length in lengths:
        onset = next((i for i in range(at, len(loud)) if loud[i]), None)
        if onset is None:
            sys.exit("fake whisper-cli: heard %d of %d sentences" % (len(spans), len(lengths)))
        start = onset * step / rate
        spans.append((start, start + length))
        at = int((start + length + 0.2) * rate / step)
    return spans


script = os.environ.get("FAKE_CLI_SCRIPT")
if script:
    with open(script, encoding="utf-8") as handle:
        said = json.load(handle)[stream]
    spans = sentences_heard([seconds for _, seconds in said])
    words = []
    for k, ((start, end), (sentence, _)) in enumerate(zip(spans, said)):
        parts = sentence.split()
        size = (end - start) / len(parts)
        row = [[start + m * size, start + (m + 1) * size, part] for m, part in enumerate(parts)]
        if "--vad" in args:
            row[0][0] = spans[k - 1][1] if k else 0.0
        else:
            row[-1][1] = spans[k + 1][0] if k + 1 < len(spans) else seconds
        words += row
    if "-ml" in args:
        cues = words
    else:
        cues = []
        for start, end, part in words:
            if cues and int(start // 30) == int(cues[-1][0] // 30):
                cues[-1] = [cues[-1][0], end, cues[-1][2] + " " + part]
            else:
                cues.append([start, end, part])
else:
    cues = json.loads(os.environ.get("FAKE_CLI_CUES", "{}")).get(stream, [[1.25, 2.5, "from the cli"]])
write_srt(base + ".srt", cues)
'''

# An ffmpeg that lists the whisper filter and answers it: the filter's options go to
# FAKE_FFMPEG_LOG-<stream from 0> and FAKE_FILTER_CUES {"<stream>": [[s, e, text]]} to its SRT
# destination. Every other call runs FAKE_REAL_FFMPEG.
FAKE_FFMPEG = SRT_STAMP + r'''
import json, os, re, subprocess, sys
real = os.environ["FAKE_REAL_FFMPEG"]
args = sys.argv[1:]
if "-filters" in args:
    res = subprocess.run([real] + args, stdout=subprocess.PIPE)
    sys.stdout.buffer.write(res.stdout + b" ... whisper           A->A       Transcribe audio.\n")
    sys.exit(res.returncode)
graph = [arg for arg in args if arg.startswith("whisper=")]
if not graph:
    sys.exit(subprocess.run([real] + args).returncode)
options = {}
for part in re.split(r"(?<!\\):", graph[0][len("whisper="):]):
    name, _, value = part.partition("=")
    options[name] = re.sub(r"\\(.)", r"\1", re.sub(r"\\(.)", r"\1", value))
stream = re.search(r"whisper-(\d+)\.srt$", options["destination"]).group(1)
with open("%s-%s" % (os.environ["FAKE_FFMPEG_LOG"], stream), "w", encoding="utf-8") as log:
    json.dump(options, log)
cues = json.loads(os.environ.get("FAKE_FILTER_CUES", "{}")).get(stream, [[0.5, 1.5, "from the filter"]])
write_srt(options["destination"], cues)
'''

FAKE_FASTER_WORDS = FAKE_RECORD + """
class Word:
    def __init__(self, start, end, word):
        self.start, self.end, self.word = start, end, word
class Segment:
    def __init__(self, words):
        self.start, self.end, self.text, self.words = 0.5, 4.0, " alpha beta gamma", words
class WhisperModel:
    def __init__(self, name):
        pass
    def transcribe(self, wav, language=None, **kw):
        record(kw)
        words = [Word(0.5, 1.0, " alpha"), Word(1.2, 1.8, " beta"), Word(3.6, 4.0, " gamma")]
        return iter([Segment(words if kw.get("word_timestamps") else None)]), None
"""

FAKE_OPENAI_WORDS = FAKE_RECORD + """
class Model:
    def transcribe(self, wav, language=None, **kw):
        record(kw)
        segment = {"start": 0.5, "end": 4.0, "text": " alpha beta gamma"}
        if kw.get("word_timestamps"):
            segment["words"] = [{"word": " alpha", "start": 0.5, "end": 1.0},
                                {"word": " beta", "start": 1.2, "end": 1.8},
                                {"word": " gamma", "start": 3.6, "end": 4.0}]
        return {"segments": [segment]}
def load_model(name):
    return Model()
"""


class RegionBase(ClipTest):
    def setUp(self):
        super().setUp()
        self.pair = make_clip(self.dir, "pair.mkv", "color=c=gray:s=160x90:r=1:d=12",
                              audio=TWO_SPEAKERS, seconds=12)
        self.single = make_clip(self.dir, "single.mkv", "color=c=gray:s=160x90:r=1:d=12",
                                audio=TWO_SPEAKERS[:1], seconds=12)
        self.model = ggml_file(self.dir, "ggml-fake.bin")
        self.vad = ggml_file(self.dir, "ggml-vad.bin")

    def digest(self, clip, *extra, env=None):
        res = run_tool(clip, "--no-frames", "--track-labels", "me,others", *extra, env=env)
        self.ok(res)
        text = digest_of(clip)
        return text, transcript_lines(text)

    def word_cli(self, on_path=True, **extra):
        """(env, wrapper, log prefix) for FAKE_CLI_WORDS, on PATH unless on_path is False."""
        folder = os.path.join(self.dir, "cli-bin")
        wrapper = install_fake(folder, "whisper-cli", FAKE_CLI_WORDS)
        log = os.path.join(self.dir, "cli")
        env = {"FAKE_CLI_LOG": log}
        if on_path:
            env["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
        env.update(extra)
        return env, wrapper, log

    def fake_ffmpeg(self, **extra):
        folder = os.path.join(self.dir, "ffmpeg-bin")
        install_fake(folder, "ffmpeg", FAKE_FFMPEG)
        log = os.path.join(self.dir, "filter")
        env = {"PATH": folder + os.pathsep + os.environ.get("PATH", ""), "FAKE_REAL_FFMPEG": FFMPEG,
               "FAKE_FFMPEG_LOG": log}
        env.update(extra)
        return env, log

    def logged(self, prefix, stream):
        with open("%s-%d" % (prefix, stream), encoding="utf-8") as handle:
            return json.load(handle)


class VideoDigestRegionMappingTest(RegionBase):
    def test_words_map_through_the_offset_table_and_a_gap_word_is_dropped_and_counted(self):
        rows = {"0": [[0.2, 4.6, "alpha beta, hum gamma delta.",
                       [[0.2, 1.0, "alpha"], [1.2, 1.8, "beta,"], [2.7, 3.2, "hum"],
                        [3.3, 3.8, "gamma"], [4.1, 4.6, "delta."]]]],
                "1": [[0.3, 2.0, "others say this",
                       [[0.3, 0.8, "others"], [0.9, 1.4, "say"], [1.5, 2.0, "this"]]]]}
        text, lines = self.digest(self.pair, env=fake_engine(self.dir, rows))
        # The segment crossing the gap is split into its two regions: alpha 0.2 s is 0.95 s,
        # gamma starts in the gap and ends in region 2 (7.75 s); hum lies only in the gap.
        self.assertEqual(lines, [("0:00:00", "me", "alpha beta,"),
                                 ("0:00:04", "others", "others say this"),
                                 ("0:00:07", "me", "gamma delta.")])
        self.assertIn("\n- Region mode: audio track 1 (me): 2 regions, 1 gap-dropped; audio track 2"
                      " (others): 2 regions, 0 gap-dropped\n", text)

    def test_the_merge_and_the_echo_rule_work_on_the_rebuilt_region_lines(self):
        clip = make_clip(self.dir, "echo.mkv", "color=c=gray:s=160x90:r=1:d=12",
                         audio=(tone_bursts((1, 3), (5, 6), (8, 10)), tone_bursts((1, 3))), seconds=12)
        mine = [[0.3, 0.6, "let"], [0.7, 1.0, "us"], [1.1, 1.4, "review"], [1.5, 1.8, "the"],
                [1.9, 2.2, "budget"], [3.7, 4.2, "sounds"], [4.3, 4.8, "good"], [6.3, 6.8, "thanks"],
                [6.9, 7.2, "a"], [7.3, 7.5, "lot"]]
        theirs = [[0.4, 0.7, "Let"], [0.8, 1.1, "us"], [1.2, 1.5, "review"], [1.6, 1.9, "the"],
                  [2.0, 2.3, "budget."]]
        rows = {"0": [[0.3, 7.5, " ".join(w[2] for w in mine), mine]],
                "1": [[0.4, 2.3, " ".join(w[2] for w in theirs), theirs]]}
        text, lines = self.digest(clip, env=fake_engine(self.dir, rows))
        # Region 1 of stream 1 echoes stream 2 and is dropped; regions 2 and 3 of stream 1 follow
        # each other with no other label between, so the existing merge makes them one line.
        self.assertEqual(lines, [("0:00:01", "others", "Let us review the budget."),
                                 ("0:00:04", "me", "sounds good thanks a lot")])
        self.assertIn("\n- Echo segments dropped: 1\n", text)
        self.assertIn("\n- Region mode: audio track 1 (me): 3 regions, 0 gap-dropped; audio track 2"
                      " (others): 1 region, 0 gap-dropped\n", text)


class VideoDigestSingleStreamRegressionTest(RegionBase):
    def test_one_stream_keeps_the_exact_transcript_of_the_main_tool(self):
        rows = {"0": [[0.5, 4.6, "alpha beta, hum gamma delta.",
                       [[0.5, 1.0, "alpha"], [1.2, 1.8, "beta,"], [2.7, 3.2, "hum"],
                        [3.3, 3.8, "gamma"], [4.1, 4.6, "delta."]]],
                      [5.0, 5.5, "in the quiet"], [8.2, 9.0, "omega"], [70.0, 71.0, "a new minute"],
                      [71.5, 75.0, "tail runs past the end"]]}
        clip = make_clip(self.dir, "long.mkv", "color=c=gray:s=160x90:r=1:d=72",
                         audio=(tone_bursts((1, 3), (8, 10), (69, 71)),), seconds=72)
        res = run_tool(clip, "--no-frames", env=fake_engine(self.dir, rows))
        self.ok(res)
        text = digest_of(clip)
        section = text.split("## Transcript", 1)[1].split("## Keyframes", 1)[0]
        self.assertEqual(section, "\n\n[0:00:00] speaker: alpha beta, hum gamma delta. in the quiet"
                                  " omega\n[0:01:10] speaker: a new minute tail runs past the end\n\n")
        self.assertNotIn("Region mode", text)


class VideoDigestRegionUnitTest(unittest.TestCase):
    def rounded(self, rows):
        return [tuple(round(v, 6) if isinstance(v, float) else v for v in row) for row in rows]

    def test_regions_join_quiet_under_a_second_pad_and_clamp(self):
        tool = load_tool()
        loud = [False] * 600
        for first, last in ((100, 300), (399, 450), (550, 600)):
            loud[first:last] = [True] * (last - first)
        # 0.99 s of quiet stays inside a region, 1.00 s ends it; the last pad stops at the end.
        self.assertEqual(tool.speech_regions(loud, 160, 96000, 16000), [(12000, 76000), (84000, 96000)])
        self.assertEqual(self.rounded(tool.offset_table([(12000, 76000), (84000, 96000)], 16000)),
                         [(0.0, 0.75, 4.0), (5.0, 5.25, 0.75)])

    def test_units_map_by_start_and_words_rebuild_one_segment_per_region(self):
        tool = load_tool()
        table = [(0.0, 0.75, 2.5), (3.5, 7.75, 2.5)]
        units = [(0.2, 1.0, "alpha"), (1.2, 1.8, "beta,"), (2.7, 3.2, "hum"), (3.3, 3.8, "gamma"),
                 (4.1, 4.6, "delta."), (5.9, 6.4, "tail")]
        words, dropped = tool.map_units(units, table, True)
        self.assertEqual((self.rounded(words), dropped),
                         ([(0.95, 2.55, "alpha beta,"), (7.75, 10.25, "gamma delta. tail")], 1))
        segments, dropped = tool.map_units(units, table, False)
        self.assertEqual((self.rounded(segments), dropped),
                         ([(0.95, 1.75, "alpha"), (1.95, 2.55, "beta,"), (7.75, 8.05, "gamma"),
                           (8.35, 8.85, "delta."), (10.15, 10.25, "tail")], 1))
        long_words = [(0.0, 1.0, "one"), (30.0, 31.0, "two"), (61.0, 62.0, "three"), (100.0, 101.0, "four")]
        pieces, _ = tool.map_units(long_words, [(0.0, 10.0, 130.0)], True)
        self.assertEqual(self.rounded(pieces), [(10.0, 41.0, "one two"), (71.0, 111.0, "three four")])


class WhisperCliRegionArgsTest(RegionBase):
    def test_two_streams_ask_for_word_times_on_the_joined_regions_without_vad(self):
        cues = {"0": [[0.2, 1.0, "alpha"], [2.7, 3.2, "hum"], [3.3, 3.8, "gamma"]],
                "1": [[0.3, 0.8, "others"]]}
        env, _, log = self.word_cli(FAKE_CLI_CUES=json.dumps(cues))
        text, lines = self.digest(self.pair, "--engine", "whisper-cli", "--model", self.model,
                                  "--vad-model", self.vad, "--prompt", "Names: Ana.", "--threads", "6",
                                  env=env)
        self.assertEqual(lines, [("0:00:00", "me", "alpha"), ("0:00:04", "others", "others"),
                                 ("0:00:07", "me", "gamma")])
        for stream, seconds in ((0, 6.0), (1, 5.0)):
            got = self.logged(log, stream)
            args = got["args"]
            self.assertAlmostEqual(got["seconds"], seconds, places=3)
            self.assertEqual(args[args.index("-ml") + 1:args.index("-ml") + 3], ["1", "-sow"])
            self.assertNotIn("--vad", args)
            self.assertNotIn("-vm", args)
            self.assertEqual(args[args.index("--prompt") + 1], "Names: Ana.")
            self.assertEqual(args[args.index("-t") + 1], "6")
        self.assertIn("\n- Region mode: audio track 1 (me): 2 regions, 1 gap-dropped; audio track 2"
                      " (others): 2 regions, 0 gap-dropped\n", text)

    def test_one_stream_keeps_vad_and_segment_times(self):
        env, _, log = self.word_cli()
        text, lines = self.digest(self.single, "--engine", "whisper-cli", "--model", self.model,
                                  "--vad-model", self.vad, env=env)
        self.assertEqual(lines, [("0:00:01", "speaker", "from the cli")])
        got = self.logged(log, 0)
        self.assertAlmostEqual(got["seconds"], 12.0, places=3)
        self.assertNotIn("-ml", got["args"])
        self.assertNotIn("-sow", got["args"])
        at = got["args"].index("--vad")
        self.assertEqual(got["args"][at + 1:at + 3], ["-vm", self.vad])


class PythonEngineRegionWordsTest(RegionBase):
    def test_python_engines_get_word_timestamps_in_region_mode_only(self):
        fakes = os.path.join(self.dir, "fakes")
        for package, body in (("faster_whisper", FAKE_FASTER_WORDS), ("whisper", FAKE_OPENAI_WORDS)):
            os.makedirs(os.path.join(fakes, package))
            with open(os.path.join(fakes, package, "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write(body)
        kw_log = os.path.join(self.dir, "kw.json")
        env = {"PYTHONPATH": fakes, "FAKE_KW_LOG": kw_log}
        for engine in ("faster-whisper", "openai-whisper"):
            _, lines = self.digest(self.pair, "--engine", engine, "--model", "tiny", env=env)
            self.assertEqual(lines, [("0:00:01", "me", "alpha beta"), ("0:00:04", "others", "alpha beta"),
                                     ("0:00:07", "me", "gamma"), ("0:00:10", "others", "gamma")], engine)
            with open(kw_log, encoding="utf-8") as handle:
                self.assertIs(json.load(handle).get("word_timestamps"), True, engine)
            _, lines = self.digest(self.single, "--engine", engine, "--model", "tiny", env=env)
            self.assertEqual(lines, [("0:00:00", "speaker", "alpha beta gamma")], engine)
            with open(kw_log, encoding="utf-8") as handle:
                self.assertNotIn("word_timestamps", json.load(handle), engine)


class FfmpegWhisperRegionTest(RegionBase):
    def test_the_filter_maps_by_segment_start_without_vad_and_drops_gap_text(self):
        cues = {"0": [[0.2, 1.4, "first words"], [2.6, 3.0, "Thank you."], [3.3, 4.4, "second words"]],
                "1": [[0.3, 1.9, "their words"]]}
        env, log = self.fake_ffmpeg(FAKE_FILTER_CUES=json.dumps(cues))
        text, lines = self.digest(self.pair, "--engine", "ffmpeg-whisper", "--model", self.model,
                                  "--vad-model", self.vad, env=env)
        self.assertEqual(lines, [("0:00:00", "me", "first words"), ("0:00:04", "others", "their words"),
                                 ("0:00:07", "me", "second words")])
        self.assertIn("\n- Region mode: audio track 1 (me): 2 regions, 1 gap-dropped; audio track 2"
                      " (others): 2 regions, 0 gap-dropped\n", text)
        for stream in (0, 1):
            self.assertNotIn("vad_model", self.logged(log, stream))
        self.digest(self.single, "--engine", "ffmpeg-whisper", "--model", self.model,
                    "--vad-model", self.vad, env=env)
        self.assertIn("vad_model", self.logged(log, 0))


class VideoDigestEngineChoiceTest(RegionBase):
    def test_auto_tries_whisper_cli_then_faster_then_openai_then_the_filter(self):
        cli_env, _, _ = self.word_cli()
        filter_env, _ = self.fake_ffmpeg()
        fakes = os.path.join(self.dir, "fakes")
        for package, body in (("faster_whisper", FAKE_FASTER), ("whisper", FAKE_OPENAI)):
            os.makedirs(os.path.join(fakes, package))
            with open(os.path.join(fakes, package, "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write(body)
        only_openai = os.path.join(self.dir, "only-openai")
        os.makedirs(os.path.join(only_openai, "faster_whisper"))
        with open(os.path.join(only_openai, "faster_whisper", "__init__.py"), "w") as handle:
            handle.write("raise ImportError('not installed')\n")
        shutil.copytree(os.path.join(fakes, "whisper"), os.path.join(only_openai, "whisper"))
        both = dict(filter_env, PATH=os.path.join(self.dir, "cli-bin") + os.pathsep + filter_env["PATH"],
                    FAKE_CLI_LOG=cli_env["FAKE_CLI_LOG"])
        no_python = os.path.join(self.dir, "no-python")
        cases = ((dict(both, PYTHONPATH=fakes), (), "whisper-cli"),
                 (dict(filter_env, PYTHONPATH=fakes), (), "faster-whisper"),
                 (dict(filter_env, PYTHONPATH=only_openai), (), "openai-whisper"),
                 (filter_env, ("--engine-python", no_python), "ffmpeg-whisper"))
        for env, extra, engine in cases:
            text, _ = self.digest(self.single, "--model", self.model, *extra, env=env)
            self.assertIn("\n- Engine: %s\n" % engine, text)

    def test_the_explicit_whisper_cli_path_is_used_and_a_missing_one_is_exit_2(self):
        env, wrapper, log = self.word_cli(on_path=False)
        for extra in (("--engine", "whisper-cli"), ()):
            text, lines = self.digest(self.single, "--whisper-cli", wrapper, "--model", self.model,
                                      *extra, env=env)
            self.assertIn("\n- Engine: whisper-cli\n", text)
            self.assertEqual(lines, [("0:00:01", "speaker", "from the cli")])
        res = run_tool(self.single, "--no-frames", "--whisper-cli", os.path.join(self.dir, "none.exe"),
                       "--model", self.model)
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("--whisper-cli", res.stderr)

    def test_english_gets_the_default_prompt_unless_another_or_an_empty_one_is_given(self):
        env, _, log = self.word_cli()
        cases = ((("--language", "en"), DEFAULT_PROMPT), (("--language", "es"), None),
                 (("--language", "en", "--prompt", ""), None),
                 (("--language", "en", "--prompt", "Names: Bo."), "Names: Bo."))
        for extra, prompt in cases:
            text, _ = self.digest(self.single, "--engine", "whisper-cli", "--model", self.model,
                                  *extra, env=env)
            args = self.logged(log, 0)["args"]
            if prompt is None:
                self.assertNotIn("--prompt", args, extra)
                self.assertIn("\n- Prompt: none\n", text)
            else:
                self.assertIn("--prompt", args, extra)
                self.assertEqual(args[args.index("--prompt") + 1], prompt, extra)
                self.assertIn("\n- Prompt: %s\n" % prompt, text)
        fakes = os.path.join(self.dir, "fakes")
        os.makedirs(os.path.join(fakes, "whisper"))
        with open(os.path.join(fakes, "whisper", "__init__.py"), "w", encoding="utf-8") as handle:
            handle.write(FAKE_OPENAI)
        kw_log = os.path.join(self.dir, "kw.json")
        self.digest(self.single, "--engine", "openai-whisper", "--model", "tiny", "--language", "en",
                    env={"PYTHONPATH": fakes, "FAKE_KW_LOG": kw_log})
        with open(kw_log, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle).get("initial_prompt"), DEFAULT_PROMPT)

    def test_gpu_device_reaches_the_filter_only_when_given(self):
        env, log = self.fake_ffmpeg()
        text, _ = self.digest(self.single, "--engine", "ffmpeg-whisper", "--model", self.model,
                              "--language", "en", "--gpu-device", "1", env=env)
        self.assertEqual(self.logged(log, 0).get("gpu_device"), "1")
        self.assertIn("\n- Prompt: none (the whisper filter takes no prompt)\n", text)
        self.digest(self.single, "--engine", "ffmpeg-whisper", "--model", self.model, env=env)
        self.assertNotIn("gpu_device", self.logged(log, 0))


# The three-stream sample of the multi-track spec: (seconds, stream from 1, sentence).
SAMPLE_SECONDS = 33
SAMPLE = (
    (0.0, 2, "Good morning everyone, thanks for joining the weekly status call."),
    (6.0, 3, "Good morning. I have an update on the database migration."),
    (12.0, 2, "Great, please go ahead and share the details."),
    (16.0, 3, "The migration finished last night and all of the tests passed."),
    (21.0, 2, "Excellent news."),
    (23.0, 2, "Let us schedule the release for Thursday."),
    (27.0, 3, "Thursday works for me."),
    (29.0, 3, "I will send the notes after the call."),
)
QUIET_PEAK = 104  # a 16-bit sample at -50 dBFS, the tool's SILENCE_FLOOR_DB
SPEAK_PS1 = r'''param([string]$Jobs)
Add-Type -AssemblyName System.Speech
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($job in (Get-Content -Raw -Path $Jobs | ConvertFrom-Json)) {
    $voice.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]$job.gender)
    $voice.SetOutputToWaveFile($job.path, $format)
    $voice.Speak($job.text)
    $voice.SetOutputToNull()
}
$voice.Dispose()
'''


def powershell_speech():
    """The Windows PowerShell that loads System.Speech, or None."""
    shell = shutil.which("powershell") if os.name == "nt" else None
    if not shell:
        return None
    res = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
                          "Add-Type -AssemblyName System.Speech"], capture_output=True, timeout=TIMEOUT)
    return shell if res.returncode == 0 else None


def make_three_track_sample(folder, shell):
    """sample-3track.mp4 in folder: SAMPLE_SECONDS long, a video stream and three AAC streams (1 the
    mix, 2 a male voice, 3 a female one), each SAMPLE sentence trimmed to its sound and starting at
    its time. Returns the path and the seconds each sentence lasts."""
    jobs = [{"gender": "Male" if stream == 2 else "Female", "text": text,
             "path": os.path.join(folder, "say-%d.wav" % index)}
            for index, (_, stream, text) in enumerate(SAMPLE)]
    jobs_path = os.path.join(folder, "speak.json")
    with open(jobs_path, "w", encoding="utf-8") as handle:
        json.dump(jobs, handle)
    script = os.path.join(folder, "speak.ps1")
    with open(script, "w", encoding="ascii") as handle:
        handle.write(SPEAK_PS1)
    res = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                          "-File", script, "-Jobs", jobs_path], capture_output=True, text=True,
                         timeout=TIMEOUT)
    if res.returncode != 0:
        raise AssertionError("System.Speech did not speak: " + res.stderr[-400:])
    rate = 16000
    tracks = {stream: array.array("h", bytes(2 * SAMPLE_SECONDS * rate)) for stream in (2, 3)}
    lengths = []
    for (when, stream, text), job in zip(SAMPLE, jobs):
        with wave.open(job["path"], "rb") as handle:
            said = array.array("h", handle.readframes(handle.getnframes()))
        loud = [i for i, value in enumerate(said) if abs(value) >= QUIET_PEAK]
        said = said[loud[0]:loud[-1] + 1]
        later = [w for w, s, _ in SAMPLE if s == stream and w > when]
        limit = int(round(((min(later) - 0.3) if later else SAMPLE_SECONDS) * rate))
        at = int(round(when * rate))
        if at + len(said) > limit:
            raise AssertionError("the sentence at %g s runs %.2f s, into the next one" % (when, len(said) / rate))
        tracks[stream][at:at + len(said)] = said
        lengths.append(len(said) / rate)
    mix = array.array("h", (max(-32768, min(32767, a + b)) for a, b in zip(tracks[2], tracks[3])))
    paths = []
    for name, data in (("mix", mix), ("a", tracks[2]), ("b", tracks[3])):
        path = os.path.join(folder, "track-%s.wav" % name)
        with wave.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(data.tobytes())
        paths.append(path)
    target = os.path.join(folder, "sample-3track.mp4")
    ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=320x180:r=5:d=%d" % SAMPLE_SECONDS, "-i", paths[0],
           "-i", paths[1], "-i", paths[2], "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:a",
           "-c:v", "mpeg4", "-q:v", "4", "-c:a", "aac", "-ar", "48000", "-b:a", "128k",
           "-t", str(SAMPLE_SECONDS), target)
    return target, lengths


class VideoDigestMultitrackOrderTest(RegionBase):
    # (expected start in seconds, label, the SAMPLE sentences the line holds)
    EXPECTED = ((0, "me", (0,)), (6, "others", (1,)), (12, "me", (2,)), (16, "others", (3,)),
                (21, "me", (4, 5)), (27, "others", (6, 7)))

    def test_turns_come_in_spoken_order_each_within_a_second_of_its_onset(self):
        shell = powershell_speech()
        if not shell:
            self.skipTest("needs Windows PowerShell with System.Speech")
        sample, lengths = make_three_track_sample(self.dir, shell)
        script = os.path.join(self.dir, "said.json")
        with open(script, "w", encoding="utf-8") as handle:
            json.dump({str(stream - 1): [[text, length] for (_, s, text), length in zip(SAMPLE, lengths)
                                         if s == stream] for stream in (2, 3)}, handle)
        env, _, _ = self.word_cli(FAKE_CLI_SCRIPT=script)
        res = run_tool(sample, "--engine", "whisper-cli", "--model", self.model, "--vad-model",
                       self.vad, "--language", "en", "--tracks", "2,3", "--track-labels", "me,others",
                       "--no-frames", env=env)
        self.ok(res)
        lines = transcript_lines(digest_of(sample))
        self.assertEqual([(label, text) for _, label, text in lines],
                         [(label, " ".join(SAMPLE[i][2] for i in held)) for _, label, held in self.EXPECTED])
        for (clock, _, _), (when, _, _) in zip(lines, self.EXPECTED):
            h, m, s = (int(part) for part in clock.split(":"))
            self.assertLessEqual(abs(h * 3600 + m * 60 + s - when), 1.0, (clock, when))


if __name__ == "__main__":
    unittest.main()
