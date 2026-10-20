#!/usr/bin/env python3
"""Tests for scripts/video-digest.py on synthetic clips that ffmpeg generates in a temp folder.

No real recording and no model: the transcription engine is replaced by the fake the tool reads
from VIDEO_DIGEST_FAKE_ENGINE (fixed segments per audio stream), by a fake whisper-cli on PATH, or
by fake faster_whisper and whisper modules on PYTHONPATH, so each adapter's command line and
output parser run for real while nothing is downloaded. VIDEO_DIGEST_TOOL points the suite at
another copy of the tool, which is how a mutant is shown to turn a pin red.

Run:
    python scripts/tests/test-video-digest.py
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TOOL = os.environ.get("VIDEO_DIGEST_TOOL") or os.path.join(ROOT, "scripts", "video-digest.py")
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
TIMEOUT = 240
SEAMS = ("VIDEO_DIGEST_FAKE_ENGINE", "VIDEO_DIGEST_COMPARE")
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
        self.assertIn("audio track 1 is silent: check the recorder's track settings", res.stderr)
        self.assertNotIn("audio track 0 is silent", res.stderr)
        text = digest_of(clip)
        self.assertIn("- Silent tracks: audio track 1 (track2)\n", text)
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
        self.assertIn("1,196 tokens (1280x720)", top)
        self.assertLess(text.index("## Metadata"), text.index("## Transcript"))
        self.assertLess(text.index("## Transcript"), text.index("## Keyframes"))
        for line in ("- Duration: 0:01:10", "- Resolution: 160x90", "- Audio streams: 1 (0: speaker)",
                     "- Engine: fake", "- Model: none", "- Language: auto",
                     "- Frames: 3 (threshold 0.90, floor 60 s, long edge 1280 px)",
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
        self.assertEqual(size, "1280,720")

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

FAKE_FASTER = """import os
class Segment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text
class WhisperModel:
    def __init__(self, name):
        self.name = name
    def transcribe(self, wav, language=None):
        assert os.path.getsize(wav) > 0, wav
        return iter([Segment(0.5, 1.5, " fast %s %s" % (self.name, language))]), None
"""

FAKE_OPENAI = """import os
class Model:
    def __init__(self, name):
        self.name = name
    def transcribe(self, wav, language=None):
        assert os.path.getsize(wav) > 0, wav
        return {"segments": [{"start": 0.5, "end": 1.5, "text": " open %s %s" % (self.name, language)}]}
def load_model(name):
    return Model(name)
"""


class VideoDigestEngineAdapterTest(ClipTest):
    def setUp(self):
        super().setUp()
        self.clip = make_clip(self.dir, "voice.mkv", "color=c=gray:s=160x90:r=1:d=5",
                              audio=("sine=frequency=440:sample_rate=16000",), seconds=5)

    def test_whisper_cli_command_line_and_srt(self):
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

    def python_engines(self):
        fakes = os.path.join(self.dir, "fakes")
        for package, body in (("faster_whisper", FAKE_FASTER), ("whisper", FAKE_OPENAI)):
            os.makedirs(os.path.join(fakes, package))
            with open(os.path.join(fakes, package, "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write(body)
        return {"PYTHONPATH": fakes}

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


FAKE_BROKEN = """class Segment:
    def __init__(self):
        self.start, self.end, self.text = "soon", 1.0, "unparsable start"
class WhisperModel:
    def __init__(self, name):
        pass
    def transcribe(self, wav, language=None):
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
        self.assertIn("audio track 0 is silent: check the recorder's track settings", res.stderr)
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


if __name__ == "__main__":
    unittest.main()
