#!/usr/bin/env python3
"""Turn a meeting or screen recording into one Markdown file an agent can read.

    python scripts/video-digest.py <recording> [--out <dir>]
        [--engine auto|whisper-cli|faster-whisper|openai-whisper|ffmpeg-whisper]
        [--model <name or path>] [--vad-model <ggml vad file>] [--language <code or auto>]
        [--engine-python <python>] [--whisper-cli <path>] [--gpu-device N]
        [--threshold 0.90] [--floor 60] [--frame-edge 1280]
        [--tracks 1,2] [--track-labels me,others] [--min-gap 5] [--threads N] [--prompt TEXT]
        [--ffmpeg <dir>] [--no-frames] [--no-transcript]

A recorder with a mixed track beside one track per side (OBS: track 1 the mix, 2 the microphone,
3 the call) is run as --tracks 2,3 --track-labels me,others.

It writes <out>/<stem>.digest.md and <out>/<stem>.frames/ (default <out>: the recording's
folder). A model that reads images but not video reads the transcript first and opens only the
frames it needs. Standard library only; ffmpeg and ffprobe do the decoding, on PATH or in
--ffmpeg <dir>.

Exit codes: 0 done; 2 bad arguments (a --tracks number past the last audio stream included), or
ffmpeg or ffprobe missing or not startable; 3 no transcription engine found (the message names
what to install), or the engine that was found failed or cannot be started; 4 the recording cannot
be read, or a step timed out (the message names the step).

Frames. ffmpeg decodes one frame a second, scaled to GRAY_SIZE in grayscale, as raw bytes through
a pipe, so no image per second is ever written. A frame is kept when its normalized
cross-correlation with the LAST KEPT frame falls below --threshold, or when --floor seconds passed
since the last kept frame; the first and the last are always kept. A change inside --min-gap
seconds (default 5) of the last kept frame waits: the first frame after the gap that still differs
from the last kept one is kept, so a moving camera tile does not fill the folder with a frame a
second (the floor frame and the last frame may fall inside the gap; --min-gap 0 keeps every
change). Comparing with the previous
second instead misses slow change: a document scrolled a line at a time never differs much from
the second before, but it drifts away from the frame last kept. Two flat frames (no variance) are
the same when their means differ by at most FLAT_MEAN_TOLERANCE grey levels; a flat frame against
a textured one is a change. Each kept second is then written once as a JPEG whose long edge is
the smaller of --frame-edge and the source's long edge, so a frame is never upscaled (fast seek,
-q:v JPEG_QSCALE, legible for shared-document text).

Audio. Each audio stream (-map 0:a:<n-1>; every track number the tool prints or accepts counts from 1) is measured with volumedetect and extracted at
16 kHz mono in the same ffmpeg pass. A stream whose max_volume is below SILENCE_FLOOR_DB is
reported silent, in the Markdown and on stderr, and not transcribed. The peak decides, not the
mean: a mean averages speech over the silence around it (2 s of tone in 65 s of silence read mean
-56.2 dB, max -37.8 dB). Digital silence (lavfi anullsrc) measures -91.0 dB, so -50 dB sits well
above it and below any speech; a track with speech flagged silent means the floor is too high.
--tracks 1,2 (1-based, file order) picks the streams to measure and transcribe, default all.
The label is `speaker` for a single chosen stream; with two or more, the --track-labels values in
the order given, then track<n> (the stream's own number) for any stream past the list.

Engines, tried in this order by --engine auto, each a subprocess:
  whisper-cli     whisper.cpp's whisper-cli, named by --whisper-cli <path> or found on PATH, when
                  --model is a file (a ggml model); -t threads (--threads, default logical cores
                  minus 4, at least 4), --prompt, -dev N when --gpu-device is given, --vad -vm
                  <file> when --vad-model is given (not in region mode); writes SRT.
  faster-whisper  imported by --engine-python (default: this Python), --model a model name;
  openai-whisper  each writes a JSON list of [start, end, text] in seconds. --threads (faster-whisper
                  cpu_threads) is passed only when given, the prompt as initial_prompt.
  ffmpeg-whisper  FFmpeg's whisper filter, when `ffmpeg -filters` lists it and --model is a file;
                  language set, queue=20, vad_model when --vad-model is given, gpu_device when
                  --gpu-device is given; writes SRT. The filter takes no prompt.
The filter comes last: it measured 0.3 to 0.5x real time per stream, with cues up to its 20 s
queue early. The prompt is --prompt as given (--prompt "" sends none); without it, --language en
sends DEFAULT_PROMPTS["en"] and any other language none. The Metadata names the prompt sent.
Segment times are clamped to the recording's duration and the clamped ones counted.

Region mode, when two or more streams are transcribed (one stream takes the path above
unchanged). On a stream that is silent half the time, whisper times run across the pauses: VAD
hands a pause to the next word, and without VAD the word before a pause runs on to the next
speech, so turns come out glued and out of order. So each stream's 16 kHz extraction is read back
in REGION_WINDOW windows: a window is loud when its peak reaches SILENCE_FLOOR_DB, a quiet run of
REGION_MIN_SILENCE or more ends a speech region (a shorter one, such as the pause after a
sentence, stays inside it), and each region keeps REGION_PAD of the recording on both sides. The
regions, joined by REGION_GAP of zeros, are the engine's input, with an offset table from the
joined timeline back to the recording, and the engine runs WITHOUT VAD (the regions removed the
silence). whisper-cli gets -ml 1 -sow and the Python engines word_timestamps=True, so every word
is placed by its start and the text is rebuilt into one segment per region (split where
TURN_SPLIT_SECONDS passed), and a segment that crosses a gap cannot glue two turns; the filter's
segments, which carry no word times, are placed by their start. A word or segment that starts in
a gap goes to the next region when it ends inside it; one that lies only inside a gap is dropped
and counted. The Metadata lists each stream's regions and gap-dropped count.

Echo rule, two or more transcribed streams only: a segment of the first chosen stream is dropped
when it has at least ECHO_MIN_WORDS words, overlaps in time a segment of another stream, and their texts
(lowercased, punctuation removed) match at a difflib ratio of at least ECHO_MIN_RATIO. The first
stream is usually the local microphone, which also hears the remote side; headphones
avoid the echo at the source.

Transcript. Segments of all streams in time order; consecutive segments of one label merge into
one line `[h:mm:ss] label: text`, and a line closes once TURN_SPLIT_SECONDS passed since its
start, so a single-track recording keeps a time index instead of one line for the whole meeting.

Test seams, read only from the environment: VIDEO_DIGEST_FAKE_ENGINE names a JSON file
{"<stream n>": [[start, end, text], ...]} that stands in for the engine under --engine auto (no
model is loaded); its times are on the engine's input, which in region mode is the joined
regions, and a row may carry its words as a fourth item [[start, end, word], ...], which region
mode places one by one like whisper-cli's (rows without words are placed like the filter's
segments, and one stream takes the rows as they are); VIDEO_DIGEST_COMPARE=previous compares each frame with the previous second
instead of the last kept frame, for the test that shows why the tool does not;
VIDEO_DIGEST_STEP_TIMEOUT=<step>=<seconds> replaces that step's timeout (steps: probe, filters,
decode, frame, image-size, audio, engine, engine-probe), so a test can make one step time out.
Inert unless set.

Timeouts, from the recording's duration D in seconds (default_timeout): probes 60 s; audio 60 +
D/2; decode (the gray-frame pass) 120 + 2D; one JPEG write 60 s; a transcription engine 600 + 6D.
A step past its timeout is exit 4 naming the step.

Decoding. The openai-whisper call passes beam_size=5 and best_of=5, the defaults of whisper's
own command line (the library leaves both unset, which decodes greedy); faster-whisper gets
beam_size=5 explicitly.
"""
import argparse
import array
import difflib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from operator import mul

GRAY_SIZE = (256, 144)
FLAT_MEAN_TOLERANCE = 2.0
JPEG_QSCALE = 3
SILENCE_FLOOR_DB = -50.0
ECHO_MIN_WORDS = 4
ECHO_MIN_RATIO = 0.8
TURN_SPLIT_SECONDS = 60.0
# Region mode. A 10 ms window resolves a word's onset well inside the 1.0 s acceptance. A quiet
# run of 1.0 s or more ends a region: the pauses inside one speaker's sentence measured up to
# 0.82 s on the spec's sample, and the pause between two turns on one stream is longer. 0.25 s of
# the recording on each side keeps a soft onset or a trailing consonant that peaks under the floor.
# 1.0 s of zeros between regions keeps them apart for the engine while adding little to decode.
REGION_WINDOW = 0.01
REGION_MIN_SILENCE = 1.0
REGION_PAD = 0.25
REGION_GAP = 1.0
DEFAULT_PROMPTS = {"en": "Hello, everyone. Thanks for joining the meeting."}
EXCERPT_WORDS = 12
PROBE_TIMEOUT = 60
ENGINE_PROBE_TIMEOUT = 180
DEFAULT_MIN_GAP = 5.0
FAKE_ENV = "VIDEO_DIGEST_FAKE_ENGINE"
COMPARE_ENV = "VIDEO_DIGEST_COMPARE"
STEP_TIMEOUT_ENV = "VIDEO_DIGEST_STEP_TIMEOUT"
MIN_THREADS = 4
EXE = ".exe" if os.name == "nt" else ""
FRAME_NAME = re.compile(r"^\d{4}_\d+-\d\d-\d\d\.jpg$")
ENGINES = ("whisper-cli", "faster-whisper", "openai-whisper", "ffmpeg-whisper")
INSTALL = {
    "ffmpeg-whisper": "an FFmpeg 8.0 or later build with the whisper filter (ffmpeg -filters lists"
                      " it), with --model <ggml model file>",
    "whisper-cli": "whisper.cpp's whisper-cli on PATH or named by --whisper-cli <path>, with --model"
                   " <ggml model file>",
    "faster-whisper": "pip install faster-whisper into --engine-python, with --model <name, such as"
                      " large-v3>",
    "openai-whisper": "pip install openai-whisper into --engine-python, with --model <name, such as"
                      " large-v3>",
}
PYTHON_MODULE = {"faster-whisper": "faster_whisper", "openai-whisper": "whisper"}
PYTHON_SNIPPET = {
    "faster-whisper": (
        "import json, sys\n"
        "from faster_whisper import WhisperModel\n"
        "wav, model, language, out, threads, prompt, words = sys.argv[1:8]\n"
        "load = {'cpu_threads': int(threads)} if int(threads) > 0 else {}\n"
        "ask = {'initial_prompt': prompt} if prompt else {}\n"
        "if words == '1':\n"
        "    ask['word_timestamps'] = True\n"
        "segments, _ = WhisperModel(model, **load).transcribe(wav, language=None if language == 'auto' else language, beam_size=5, **ask)\n"
        "rows = []\n"
        "for s in segments:\n"
        "    found = (getattr(s, 'words', None) or []) if words == '1' else []\n"
        "    rows += [[w.start, w.end, w.word] for w in found] or [[s.start, s.end, s.text]]\n"
        "with open(out, 'w', encoding='utf-8') as handle:\n"
        "    json.dump(rows, handle)\n"),
    "openai-whisper": (
        "import json, sys\n"
        "import whisper\n"
        "wav, model, language, out, threads, prompt, words = sys.argv[1:8]\n"
        "ask = {'initial_prompt': prompt} if prompt else {}\n"
        "if words == '1':\n"
        "    ask['word_timestamps'] = True\n"
        "result = whisper.load_model(model).transcribe(wav, language=None if language == 'auto' else language, beam_size=5, best_of=5, **ask)\n"
        "rows = []\n"
        "for s in result['segments']:\n"
        "    found = (s.get('words') or []) if words == '1' else []\n"
        "    rows += [[w['start'], w['end'], w['word']] for w in found] or [[s['start'], s['end'], s['text']]]\n"
        "with open(out, 'w', encoding='utf-8') as handle:\n"
        "    json.dump(rows, handle)\n"),
}


class DigestError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def clock(seconds):
    whole = int(seconds)
    return "%d:%02d:%02d" % (whole // 3600, whole % 3600 // 60, whole % 60)


def tail(raw, limit=600):
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    text = text.strip()
    return text[-limit:] if text else "(no message)"


def step_timeout(step, default):
    """The step's timeout; VIDEO_DIGEST_STEP_TIMEOUT=<step>=<seconds> replaces it (test seam)."""
    name, _, seconds = os.environ.get(STEP_TIMEOUT_ENV, "").partition("=")
    if name == step and number(seconds) is not None:
        return number(seconds)
    return default


def default_timeout(step, duration):
    """The default timeout in seconds of a step for a recording of `duration` seconds."""
    return {"audio": 60 + duration / 2, "decode": 120 + 2 * duration,
            "engine": 600 + 6 * duration, "engine-probe": ENGINE_PROBE_TIMEOUT}.get(step, PROBE_TIMEOUT)


def run(cmd, step, timeout=None, env=None, engine=None):
    """subprocess.run with the failures that would be tracebacks mapped to coded exits: a timeout
    is exit 4 naming the step, a binary that cannot start is exit 3 naming the engine or exit 2."""
    limit = step_timeout(step, timeout)
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=limit,
                              env=env)
    except subprocess.TimeoutExpired:
        raise DigestError(4, "step %s timed out after %g s" % (step, limit))
    except OSError as error:
        if engine:
            raise DigestError(3, "transcription engine %s cannot be started: %s" % (engine, error))
        raise DigestError(2, "cannot start %s: %s" % (os.path.basename(str(cmd[0])), error))


def find_ffmpeg(folder):
    names = ("ffmpeg", "ffprobe")
    if folder:
        found = [os.path.join(folder, name + EXE) for name in names]
        found = [path if os.path.isfile(path) else None for path in found]
    else:
        found = [shutil.which(name) for name in names]
    if not all(found):
        raise DigestError(2, "ffmpeg and ffprobe are needed: put them on PATH or pass --ffmpeg"
                             " <dir> (https://ffmpeg.org/download.html)")
    return found[0], found[1]


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def probe(ffprobe, path):
    if not os.path.isfile(path):
        raise DigestError(4, "cannot read the recording: %s is not a file" % path)
    res = run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
              "probe", timeout=PROBE_TIMEOUT)
    if res.returncode != 0:
        raise DigestError(4, "cannot read the recording: " + tail(res.stderr))
    try:
        data = json.loads(res.stdout.decode("utf-8", "replace"))
    except ValueError:
        raise DigestError(4, "cannot read the recording: ffprobe returned no JSON")
    streams = data.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    pictures = [i for i, s in enumerate(video)
                if not (s.get("disposition") or {}).get("attached_pic")]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    durations = [number((data.get("format") or {}).get("duration"))]
    durations += [number(s.get("duration")) for s in streams]
    durations = [d for d in durations if d and d > 0]
    if not durations or not (pictures or audio):
        raise DigestError(4, "cannot read the recording: no duration, or no video and no audio"
                             " stream")
    info = {"duration": durations[0], "audio": len(audio), "video": None, "size": None}
    if pictures:
        stream = video[pictures[0]]
        info["video"] = pictures[0]
        info["size"] = (stream.get("width"), stream.get("height"))
    return info


class Gray:
    """One small grayscale frame with the sums the correlation needs, computed once."""
    __slots__ = ("pixels", "total", "squares")

    def __init__(self, pixels):
        self.pixels = pixels
        self.total = sum(pixels)
        self.squares = sum(map(mul, pixels, pixels))


def similarity(a, b):
    """Zero-mean normalized cross-correlation of two frames of the same size, -1 to 1."""
    n = len(a.pixels)
    var_a = n * a.squares - a.total * a.total
    var_b = n * b.squares - b.total * b.total
    if var_a <= 0 or var_b <= 0:
        if var_a <= 0 and var_b <= 0:
            return 1.0 if abs(a.total - b.total) <= FLAT_MEAN_TOLERANCE * n else 0.0
        return 0.0
    cross = n * sum(map(mul, a.pixels, b.pixels)) - a.total * b.total
    return cross / math.sqrt(var_a * var_b)


def select_keyframes(frames, threshold, floor, compare="last", min_gap=0.0):
    """The seconds to keep from (second, pixels) pairs in time order."""
    kept = []
    reference = previous = None
    last_seen = None
    for when, pixels in frames:
        frame = Gray(pixels)
        if reference is None:
            kept.append(when)
            reference = frame
        else:
            base = previous if compare == "previous" else reference
            since = when - kept[-1]
            if since >= floor or (since >= min_gap and similarity(base, frame) < threshold):
                kept.append(when)
                reference = frame
        previous = frame
        last_seen = when
    if last_seen is not None and kept[-1] != last_seen:
        kept.append(last_seen)
    return kept


def gray_frames(ffmpeg, path, video_index, timeout=None):
    """One raw grayscale frame a second, read from ffmpeg's stdout; nothing touches the disk."""
    width, height = GRAY_SIZE
    size = width * height
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-i", path, "-map", "0:v:%d" % video_index,
           "-an", "-sn", "-vf", "fps=1,scale=%d:%d:flags=area,format=gray" % (width, height),
           "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    with tempfile.TemporaryFile() as errors:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors)
        except OSError as error:
            raise DigestError(2, "cannot start ffmpeg: %s" % error)
        limit = step_timeout("decode", timeout)
        expired = []

        def expire():
            expired.append(True)
            proc.kill()

        timer = threading.Timer(limit, expire) if limit else None
        if timer:
            timer.start()
        second = 0
        try:
            while True:
                chunk = proc.stdout.read(size)
                if len(chunk) < size:
                    break
                yield second, chunk
                second += 1
        finally:
            if timer:
                timer.cancel()
            proc.stdout.close()
            proc.wait()
        if expired:
            raise DigestError(4, "step decode timed out after %g s" % limit)
        if proc.returncode != 0:
            errors.seek(0)
            raise DigestError(4, "cannot decode the video: " + tail(errors.read()))


def frame_edge(wanted, size):
    """The long edge of a kept frame: the requested one, never above the source's own."""
    longest = max([v for v in (size or ()) if isinstance(v, int)] or [wanted])
    return min(wanted, longest)


def write_frames(ffmpeg, path, video_index, times, edge, folder):
    os.makedirs(folder, exist_ok=True)
    for name in os.listdir(folder):
        if FRAME_NAME.match(name):
            os.remove(os.path.join(folder, name))
    scale = "scale=%d:%d:force_original_aspect_ratio=decrease:force_divisible_by=2" % (edge, edge)
    names = []
    for index, when in enumerate(times, 1):
        name = "%04d_%s.jpg" % (index, clock(when).replace(":", "-"))
        target = os.path.join(folder, name)
        res = None
        for seek in (when, max(0.0, when - 1.0)):
            res = run([ffmpeg, "-v", "error", "-nostdin", "-y", "-ss", "%.3f" % seek, "-i", path,
                       "-map", "0:v:%d" % video_index, "-frames:v", "1", "-vf", scale,
                       "-q:v", str(JPEG_QSCALE), "-update", "1", target], "frame",
                      timeout=PROBE_TIMEOUT)
            if res.returncode == 0 and os.path.isfile(target) and os.path.getsize(target) > 0:
                break
        else:
            raise DigestError(4, "cannot write the frame at %s: %s" % (clock(when), tail(res.stderr)))
        names.append(name)
    return names


def image_size(ffprobe, image):
    res = run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height", "-of", "csv=p=0", image], "image-size",
              timeout=PROBE_TIMEOUT)
    found = re.match(rb"\s*(\d+),(\d+)", res.stdout)
    if res.returncode != 0 or not found:
        raise DigestError(4, "cannot read the size of %s: %s" % (image, tail(res.stderr)))
    return int(found.group(1)), int(found.group(2))


MAX_VOLUME = re.compile(rb"max_volume:\s*(-?inf|-?[0-9.]+) dB")


def audio_pass(ffmpeg, path, n, wav, timeout=None):
    """The peak volume of audio stream n in dB; writes it as 16 kHz mono to wav when one is named."""
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", path, "-map", "0:a:%d" % n,
           "-vn", "-sn", "-af", "volumedetect"]
    cmd += ["-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", wav] if wav else ["-f", "null", "-"]
    res = run(cmd, "audio", timeout=timeout)
    found = MAX_VOLUME.search(res.stderr)
    if res.returncode != 0 or not found:
        raise DigestError(4, "cannot read audio track %d: %s" % (n + 1, tail(res.stderr)))
    value = found.group(1)
    return float("-inf") if value.endswith(b"inf") else float(value)


def filter_escape(value):
    """A value for an option of a filter inside an -af graph: escaped for the option parser,
    then for the graph parser (ffmpeg-filters, Notes on filtergraph escaping)."""
    if os.name == "nt":
        value = value.replace("\\", "/")
    option = re.sub(r"([\\':])", r"\\\1", value)
    return re.sub(r"([\\'\[\],;])", r"\\\1", option)


SRT_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


def parse_srt(text):
    def seconds(h, m, s, ms):
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0

    segments = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            found = SRT_TIME.search(line)
            if found:
                parts = found.groups()
                body = " ".join(part.strip() for part in lines[index + 1:] if part.strip())
                if body:
                    segments.append((seconds(*parts[:4]), seconds(*parts[4:]), body))
                break
    return segments


def read_output(engine, path, res, parse):
    if res.returncode != 0 or not os.path.isfile(path):
        raise DigestError(3, "transcription engine %s failed: %s" % (engine, tail(res.stderr)))
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    try:
        return parse(text)
    except (ValueError, IndexError, TypeError) as error:
        raise DigestError(3, "transcription engine %s wrote output that cannot be parsed: %s"
                             % (engine, error))


def parse_rows(text):
    return [(float(row[0]), float(row[1]), str(row[2])) for row in json.loads(text)]


class FfmpegWhisper:
    name = "ffmpeg-whisper"
    timeout = None

    def __init__(self, ffmpeg, model, language, vad_model, gpu_device):
        self.ffmpeg, self.model, self.language, self.vad_model = ffmpeg, model, language, vad_model
        self.gpu_device = gpu_device

    def region_units(self, wav, n, work):
        """Region mode: the filter's segments without VAD, placed by their start."""
        return self.transcribe(wav, n, work, region=True), False

    def transcribe(self, wav, n, work, region=False):
        out = os.path.join(work, "whisper-%d.srt" % n)
        options = ["model=" + filter_escape(self.model), "language=" + filter_escape(self.language),
                   "queue=20", "destination=" + filter_escape(out), "format=srt"]
        if self.gpu_device is not None:
            options.append("gpu_device=%d" % self.gpu_device)
        if self.vad_model and not region:
            options.append("vad_model=" + filter_escape(self.vad_model))
        res = run([self.ffmpeg, "-v", "error", "-nostdin", "-i", wav,
                   "-af", "whisper=" + ":".join(options), "-f", "null", "-"], "engine",
                  timeout=self.timeout, engine=self.name)
        return read_output(self.name, out, res, parse_srt)


class WhisperCli:
    name = "whisper-cli"
    timeout = None

    def __init__(self, binary, model, language, threads, prompt, vad_model, gpu_device=None):
        self.binary, self.model, self.language = binary, model, language
        self.threads, self.prompt, self.vad_model = threads, prompt, vad_model
        self.gpu_device = gpu_device

    def region_units(self, wav, n, work):
        """Region mode: one SRT cue per word (-ml 1 -sow), without VAD."""
        return self.transcribe(wav, n, work, region=True), True

    def transcribe(self, wav, n, work, region=False):
        base = os.path.join(work, "whisper-%d" % n)
        cmd = [self.binary, "-m", self.model, "-f", wav, "-l", self.language, "-t",
               str(self.threads), "-osrt", "-of", base, "-np"]
        if self.prompt:
            cmd += ["--prompt", self.prompt]
        if self.gpu_device is not None:
            cmd += ["-dev", str(self.gpu_device)]  # whisper.cpp examples/cli/cli.cpp: -dev, the GPU device id
        if region:
            cmd += ["-ml", "1", "-sow"]
        elif self.vad_model:
            cmd += ["--vad", "-vm", self.vad_model]
        res = run(cmd, "engine", timeout=self.timeout, engine=self.name)
        return read_output(self.name, base + ".srt", res, parse_srt)


class PythonWhisper:
    timeout = None

    def __init__(self, name, python, model, language, ffmpeg, threads, prompt):
        self.name, self.python, self.model, self.language = name, python, model, language
        self.threads, self.prompt = threads or 0, prompt or ""
        self.env = dict(os.environ)
        self.env["PATH"] = os.path.dirname(ffmpeg) + os.pathsep + self.env.get("PATH", "")

    def region_units(self, wav, n, work):
        """Region mode: word_timestamps=True, one row per word."""
        return self.transcribe(wav, n, work, region=True), True

    def transcribe(self, wav, n, work, region=False):
        out = os.path.join(work, "segments-%d.json" % n)
        res = run([self.python, "-c", PYTHON_SNIPPET[self.name], wav, self.model, self.language, out,
                   str(self.threads), self.prompt, "1" if region else "0"], "engine",
                  timeout=self.timeout, env=self.env, engine=self.name)
        return read_output(self.name, out, res, parse_rows)


class FakeEngine:
    """The test seam: fixed segments per stream from a JSON file, after the real extraction."""
    name = "fake"

    def __init__(self, path):
        with open(path, encoding="utf-8") as handle:
            self.rows = json.load(handle)

    def transcribe(self, wav, n, work):
        if not os.path.isfile(wav) or os.path.getsize(wav) == 0:
            raise DigestError(3, "fake engine: no extracted audio for track %d" % n)
        return parse_rows(json.dumps(self.rows.get(str(n), [])))

    def region_units(self, wav, n, work):
        """The stream's words when any row carries them (a row without words is one unit),
        else its rows, placed like the filter's segments."""
        rows = self.transcribe(wav, n, work)
        given = self.rows.get(str(n), [])
        if not any(len(row) > 3 for row in given):
            return rows, False
        units = []
        for row, plain in zip(given, rows):
            units += parse_rows(json.dumps(row[3])) if len(row) > 3 else [plain]
        return units, True


def ffmpeg_lists_whisper(ffmpeg):
    res = run([ffmpeg, "-hide_banner", "-filters"], "filters", timeout=PROBE_TIMEOUT)
    return re.search(rb"^\s*\S+\s+whisper\s", res.stdout, re.MULTILINE) is not None


def python_imports(python, module):
    """(imports, reason): a timeout is a reason of its own, since a cold import that loads torch can
    take minutes and is not the same as a missing package."""
    try:
        res = run([python, "-c", "import " + module], "engine-probe", timeout=ENGINE_PROBE_TIMEOUT)
    except DigestError as error:
        if error.code == 4:
            return False, ("%s did not import in %s within %g s (a cold start that loads torch can be"
                           " slow): run again" % (module, python,
                                                  step_timeout("engine-probe", ENGINE_PROBE_TIMEOUT)))
        return False, None
    return res.returncode == 0, None


def default_threads():
    """whisper-cli's own default is 4; the work laptop measured 16 of 20 cores as the fast setting."""
    return max(MIN_THREADS, (os.cpu_count() or 2 * MIN_THREADS) - 4)


def try_engine(name, args, ffmpeg):
    """(engine, None) when usable, else (None, why not when it is installed but cannot run)."""
    model = args.model
    if name == "ffmpeg-whisper":
        if not ffmpeg_lists_whisper(ffmpeg):
            return None, None
        if not (model and os.path.isfile(model)):
            return None, "ffmpeg lists the whisper filter, but --model is not a ggml model file"
        return FfmpegWhisper(ffmpeg, model, args.language, args.vad_model, args.gpu_device), None
    if name == "whisper-cli":
        binary = args.whisper_cli or shutil.which("whisper-cli")
        if not binary:
            return None, None
        if not (model and os.path.isfile(model)):
            return None, "whisper-cli %s, but --model is not a ggml model file" % (
                "is named by --whisper-cli" if args.whisper_cli else "is on PATH")
        return WhisperCli(binary, model, args.language, args.threads or default_threads(),
                          engine_prompt(args), args.vad_model, args.gpu_device), None
    imports, reason = python_imports(args.engine_python, PYTHON_MODULE[name])
    if not imports:
        return None, reason
    if not model:
        return None, "%s imports in %s, but no --model names a model" % (name, args.engine_python)
    return PythonWhisper(name, args.engine_python, model, args.language, ffmpeg, args.threads,
                         engine_prompt(args)), None


def engine_prompt(args):
    """The prompt the engine gets: --prompt as given ("" sends none), else the language's default."""
    if args.prompt is not None:
        return args.prompt or None
    return DEFAULT_PROMPTS.get(args.language)


def shown_prompt(engine, args):
    if isinstance(engine, FfmpegWhisper):
        return "none (the whisper filter takes no prompt)"
    return engine_prompt(args) or "none"


def resolve_engine(args, ffmpeg):
    fake = os.environ.get(FAKE_ENV)
    if fake and args.engine == "auto":
        return FakeEngine(fake)
    names = ENGINES if args.engine == "auto" else (args.engine,)
    notes = []
    for name in names:
        engine, note = try_engine(name, args, ffmpeg)
        if engine:
            return engine
        if note:
            notes.append(note)
    lines = ["no transcription engine found. Install one of:"]
    lines += ["  %s: %s" % (name, INSTALL[name]) for name in names]
    lines += ["Found, not usable: " + note for note in notes]
    lines.append("Or pass --no-transcript for the frames alone.")
    raise DigestError(3, "\n".join(lines))


def words(text):
    return re.findall(r"\w+", text.lower())


def overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


def echo_rule(segments, order, first_chosen=0):
    """Drops first-stream segments the other streams also carry; returns (segments, dropped).
    Only when the first chosen stream was transcribed: it is the local microphone."""
    if len(order) < 2 or order[0] != first_chosen:
        return segments, 0
    first = order[0]
    others = [seg for n in order[1:] for seg in segments[n]]
    kept, dropped = [], 0
    for seg in segments[first]:
        mine = " ".join(words(seg[2]))
        if len(seg[2].split()) >= ECHO_MIN_WORDS and any(
                overlaps(seg, other)
                and difflib.SequenceMatcher(None, mine, " ".join(words(other[2]))).ratio()
                >= ECHO_MIN_RATIO
                for other in others):
            dropped += 1
            continue
        kept.append(seg)
    result = dict(segments)
    result[first] = kept
    return result, dropped


def clamp(segments, duration):
    clamped = 0
    result = []
    for start, end, text in segments:
        text = " ".join(text.split())
        if not text:
            continue
        if end > duration or start > duration:
            clamped += 1
        start = min(max(start, 0.0), duration)
        end = min(max(end, start), duration)
        result.append((start, end, text))
    return result, clamped


def wav_loudness(path):
    """(rate, frames, window, loud) of a 16-bit mono WAV: loud[i] says whether window i (REGION_WINDOW
    long, `window` samples) has a sample whose level reaches SILENCE_FLOOR_DB."""
    limit = 32768.0 * 10 ** (SILENCE_FLOOR_DB / 20)
    with wave.open(path, "rb") as handle:
        if handle.getsampwidth() != 2 or handle.getnchannels() != 1:
            raise DigestError(4, "cannot read %s: not 16-bit mono" % os.path.basename(path))
        rate, frames = handle.getframerate(), handle.getnframes()
        window = max(1, int(round(rate * REGION_WINDOW)))
        loud = []
        while True:
            chunk = handle.readframes(window * 1000)
            if not chunk:
                break
            samples = array.array("h", chunk)
            if sys.byteorder == "big":
                samples.byteswap()
            for at in range(0, len(samples), window):
                part = samples[at:at + window]
                loud.append(max(part) >= limit or -min(part) >= limit)
    return rate, frames, window, loud


def speech_regions(loud, window, frames, rate):
    """Speech regions of one stream as (first sample, end sample): loud windows joined across quiet
    runs shorter than REGION_MIN_SILENCE, each widened by REGION_PAD on both sides, clamped to the
    stream, and merged where two widened regions meet."""
    shortest_quiet = REGION_MIN_SILENCE * rate
    pad = int(round(REGION_PAD * rate))
    spans = []
    for index, flag in enumerate(loud):
        if not flag:
            continue
        start, end = index * window, min(frames, (index + 1) * window)
        if spans and start - spans[-1][1] < shortest_quiet:
            spans[-1][1] = end
        else:
            spans.append([start, end])
    regions = []
    for start, end in spans:
        start, end = max(0, start - pad), min(frames, end + pad)
        if regions and start <= regions[-1][1]:
            regions[-1][1] = max(regions[-1][1], end)
        else:
            regions.append([start, end])
    return [tuple(region) for region in regions]


def offset_table(regions, rate):
    """[(engine start, recording start, length)] in seconds for regions joined by REGION_GAP."""
    gap = int(round(REGION_GAP * rate))
    table, at = [], 0
    for start, end in regions:
        table.append((at / rate, start / rate, (end - start) / rate))
        at += end - start + gap
    return table


def join_regions(wav, regions, target):
    """Writes the regions of wav to target, REGION_GAP of zeros between two; returns offset_table."""
    with wave.open(wav, "rb") as source, wave.open(target, "wb") as out:
        rate = source.getframerate()
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        gap = bytes(2 * int(round(REGION_GAP * rate)))
        for index, (start, end) in enumerate(regions):
            if index:
                out.writeframes(gap)
            source.setpos(start)
            left = end - start
            while left > 0:
                chunk = source.readframes(min(left, 1 << 20))
                if not chunk:
                    raise DigestError(4, "cannot read %s past sample %d" % (os.path.basename(wav), end - left))
                out.writeframes(chunk)
                left -= len(chunk) // 2
    return offset_table(regions, rate)


def map_units(units, table, by_word):
    """Engine units (start, end, text) on the joined timeline, back to the recording: (segments,
    dropped). A unit is placed by its start; one that starts in a gap goes to the next region when it
    ends inside it, and one that lies only inside a gap is dropped and counted. Ends stop at their
    region's end. Words (by_word) are rebuilt into one segment per region, a new one once
    TURN_SPLIT_SECONDS passed since its start; segments stay one each."""
    placed, dropped = [], 0
    for start, end, text in units:
        start = max(start, 0.0)
        index = None
        for number, (joined, recorded, length) in enumerate(table):
            if start < joined + length or number == len(table) - 1:
                index = number
                break
        joined, recorded, length = table[index]
        if start < joined:
            if end <= joined:
                dropped += 1
                continue
            at = recorded
        else:
            at = recorded + min(start - joined, length)
        finish = max(at, recorded + min(max(end - joined, 0.0), length))
        placed.append((index, at, finish, text))
    if not by_word:
        return [(at, finish, text) for _, at, finish, text in placed], dropped
    pieces = []
    for index, at, finish, text in placed:
        if pieces and pieces[-1][0] == index and at - pieces[-1][1] < TURN_SPLIT_SECONDS:
            pieces[-1][2] = max(pieces[-1][2], finish)
            pieces[-1][3].append(text)
        else:
            pieces.append([index, at, finish, [text]])
    return [(at, finish, " ".join(" ".join(texts).split())) for _, at, finish, texts in pieces], dropped


def plural(count, word):
    return "%d %s%s" % (count, word, "" if count == 1 else "s")


def turns(segments, labels):
    """Merged lines (start, label, text) from {stream: [(start, end, text)]}."""
    ordered = sorted((seg[0], n, seg[2]) for n, segs in segments.items() for seg in segs)
    lines = []
    for start, n, text in ordered:
        label = labels[n]
        if lines and lines[-1][1] == label and start - lines[-1][0] < TURN_SPLIT_SECONDS:
            lines[-1] = (lines[-1][0], label, lines[-1][2] + " " + text)
        else:
            lines.append((start, label, text))
    return lines


def track_labels(given, chosen):
    """{stream index (from 0): label} for the chosen streams, the names in the order given."""
    if len(chosen) == 1:
        return {chosen[0]: "speaker"}
    names = [part.strip() for part in (given or "").split(",") if part.strip()]
    return {n: names[i] if i < len(names) else "track%d" % (n + 1) for i, n in enumerate(chosen)}


def parse_tracks(text):
    """The 0-based stream numbers from a 1-based, comma separated list, or None for all."""
    if text is None:
        return None
    try:
        numbers = [int(part) for part in text.split(",")]
    except ValueError:
        numbers = []
    if not numbers or min(numbers) < 1 or len(set(numbers)) != len(numbers):
        raise ValueError("--tracks takes distinct stream numbers from 1, such as 1,2")
    return [n - 1 for n in numbers]


def shown_model(value):
    """A model for the Markdown: the file name of a path, never the folders around it."""
    if value and (os.path.isabs(value) or "\\" in value or os.path.exists(value)):
        return re.split(r"[\\/]", value.rstrip("\\/"))[-1]
    return value or "none"


def line_text(line):
    return "[%s] %s: %s" % (clock(line[0]), line[1], line[2])


def excerpt(line):
    parts = line[2].split()
    short = " ".join(parts[:EXCERPT_WORDS]) + (" ..." if len(parts) > EXCERPT_WORDS else "")
    return "[%s] %s: %s" % (clock(line[0]), line[1], short)


def frame_line(when, lines):
    """The transcript line a frame falls in: the last line that started at or before it."""
    if not lines:
        return "none"
    current = [line for line in lines if line[0] <= when]
    if current:
        return excerpt(current[-1])
    return "none yet, next " + excerpt(lines[0])


def markdown(recording, info, args, state):
    lines_out = []
    tokens = state.get("frame_tokens")
    note = "Read the Transcript first. Open a frame only where the screen matters"
    if tokens:
        note += ": each frame costs about %s tokens (%dx%d), a transcript line a few dozen." % (
            format(tokens[0], ","), tokens[1], tokens[2])
    else:
        note += "."
    lines_out += ["# Video digest: %s" % os.path.basename(recording), "", note, "", "## Metadata", ""]
    size = info["size"]
    labels = state["labels"]
    silent = state["silent"]
    meta = [
        ("Recording", os.path.basename(recording)),
        ("Duration", "%s (%.1f s)" % (clock(info["duration"]), info["duration"])),
        ("Resolution", "%sx%s" % size if size else "no video stream"),
        ("Audio streams", "%d (%s)" % (info["audio"], ", ".join(
            "%d: %s" % (n + 1, label) for n, label in labels.items())) if info["audio"] else "none"),
        ("Engine", state["engine"]),
        ("Model", state["model"]),
        ("VAD model", shown_model(args.vad_model)),
        ("Language", args.language),
        ("Prompt", state["prompt"]),
        ("Frames", state["frames_note"]),
        ("Silent tracks", ", ".join("audio track %d (%s)" % (n + 1, labels[n]) for n in silent)
         if silent else "none"),
        ("Echo segments dropped", str(state["dropped"])),
        ("Clamped segments", str(state["clamped"])),
    ]
    if not args.vad_model:
        meta = [pair for pair in meta if pair[0] != "VAD model"]
    elif state["regions"]:
        meta = [(key, value + " (ignored in region mode)" if key == "VAD model" else value)
                for key, value in meta]
    if state["regions"]:
        meta.append(("Region mode", "; ".join(
            "audio track %d (%s): %s, %d gap-dropped" % (n + 1, labels[n], plural(count, "region"), dropped)
            for n, (count, dropped) in state["regions"].items())))
    lines_out += ["- %s: %s" % pair for pair in meta]
    lines_out += ["", "## Transcript", ""]
    if state["transcript_note"]:
        lines_out.append(state["transcript_note"])
    else:
        lines_out += [line_text(line) for line in state["lines"]] or ["(no speech found)"]
    lines_out += ["", "## Keyframes", ""]
    if state["frames"] is None:
        lines_out.append(state["frames_note"])
    folder = os.path.basename(state["folder"])
    for when, name in state["frames"] or []:
        lines_out += ["### [%s]" % clock(when), "",
                      "![frame at %s](<%s/%s>)" % (clock(when), folder, name), "",
                      "Transcript line: %s" % frame_line(when, state["lines"]), ""]
    return "\n".join(lines_out).rstrip("\n") + "\n"


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="video-digest.py",
        description="Turn a recording into one Markdown file: transcript, keyframes, an index.")
    parser.add_argument("recording")
    parser.add_argument("--out", help="output folder (default: beside the recording)")
    parser.add_argument("--engine", default="auto", choices=("auto",) + ENGINES)
    parser.add_argument("--model", help="a ggml model file, or a model name for the Python engines")
    parser.add_argument("--vad-model", help="a ggml VAD model file for the ffmpeg-whisper and"
                                            " whisper-cli engines")
    parser.add_argument("--language", default="auto", help="a language code, or auto")
    parser.add_argument("--engine-python", default=sys.executable,
                        help="the Python that runs faster-whisper or openai-whisper")
    parser.add_argument("--whisper-cli", help="the whisper-cli binary to run (default: whisper-cli on"
                                              " PATH)")
    parser.add_argument("--gpu-device", type=int, help="the GPU whisper-cli (-dev) and the ffmpeg-whisper"
                                                       " filter (gpu_device) use; not passed when absent")
    parser.add_argument("--threshold", type=float, default=0.90)
    parser.add_argument("--floor", type=float, default=60.0, help="seconds between kept frames, at most")
    parser.add_argument("--min-gap", type=float, default=DEFAULT_MIN_GAP,
                        help="seconds between kept frames, at least, except the floor frame and the"
                        " last (default 5; 0 keeps every change)")
    parser.add_argument("--frame-edge", type=int, default=1280, help="long edge of a kept frame in px")
    parser.add_argument("--tracks", help="1-based audio stream numbers to transcribe, comma"
                                         " separated (default: all)")
    parser.add_argument("--track-labels", help="labels for the chosen streams, in the order given,"
                                               " comma separated")
    parser.add_argument("--threads", type=int, help="CPU threads for the engine (default for"
                        " whisper-cli: logical cores minus 4, at least 4)")
    parser.add_argument("--prompt", help="text that primes punctuation and casing (default for"
                        " --language en: %s; \"\" for none)" % DEFAULT_PROMPTS["en"])
    parser.add_argument("--ffmpeg", help="the folder holding ffmpeg and ffprobe")
    parser.add_argument("--no-frames", action="store_true")
    parser.add_argument("--no-transcript", action="store_true")
    args = parser.parse_args(argv)
    if not 0.0 < args.threshold <= 1.0:
        parser.error("--threshold must be above 0 and at most 1")
    if args.floor <= 0:
        parser.error("--floor must be above 0")
    if args.min_gap < 0:
        parser.error("--min-gap must be 0 or more")
    if args.frame_edge < 16:
        parser.error("--frame-edge must be at least 16")
    if args.threads is not None and args.threads < 1:
        parser.error("--threads must be at least 1")
    if args.gpu_device is not None and args.gpu_device < 0:
        parser.error("--gpu-device must be 0 or more")
    if args.whisper_cli is not None and not os.path.isfile(args.whisper_cli):
        parser.error("--whisper-cli %s is not a file" % args.whisper_cli)
    try:
        args.chosen = parse_tracks(args.tracks)
    except ValueError as error:
        parser.error(str(error))
    return args


def region_pass(engine, wav, n, work, state):
    """Region mode for one stream: its speech regions joined into the engine's input, the engine's
    units mapped back to the recording; records (regions, gap-dropped) in state["regions"][n]."""
    rate, frames, window, loud = wav_loudness(wav)
    regions = speech_regions(loud, window, frames, rate)
    if not regions:
        state["regions"][n] = (0, 0)
        return []
    joined = os.path.join(work, "regions-%d.wav" % n)
    table = join_regions(wav, regions, joined)
    units, by_word = engine.region_units(joined, n, work)
    found, dropped = map_units(units, table, by_word)
    state["regions"][n] = (len(regions), dropped)
    return found


def digest(args):
    ffmpeg, ffprobe = find_ffmpeg(args.ffmpeg)
    recording = os.path.abspath(args.recording)
    info = probe(ffprobe, recording)
    out = os.path.abspath(args.out) if args.out else os.path.dirname(recording)
    stem = os.path.splitext(os.path.basename(recording))[0]
    os.makedirs(out, exist_ok=True)
    chosen = list(range(info["audio"])) if args.chosen is None else args.chosen
    if chosen and max(chosen) >= info["audio"]:
        raise DigestError(2, "--tracks %s: the recording has %d audio stream(s)"
                             % (args.tracks, info["audio"]))
    labels = track_labels(args.track_labels, chosen)
    state = {"labels": labels, "silent": [], "dropped": 0, "clamped": 0, "lines": [],
             "engine": "none", "model": "none", "prompt": "none", "regions": {},
             "transcript_note": None, "folder": os.path.join(out, stem + ".frames"), "frames": None}
    want_text = not args.no_transcript
    segments = {}
    with tempfile.TemporaryDirectory(prefix="video-digest-") as work:
        wavs = {}
        for n in chosen:
            wav = os.path.join(work, "track-%d.wav" % n) if want_text else None
            level = audio_pass(ffmpeg, recording, n, wav, default_timeout("audio", info["duration"]))
            if level < SILENCE_FLOOR_DB:
                state["silent"].append(n)
                print("audio track %d is silent: check the recorder's track settings" % (n + 1),
                      file=sys.stderr)
            elif wav:
                wavs[n] = wav
        if wavs:
            engine = resolve_engine(args, ffmpeg)
            engine.timeout = default_timeout("engine", info["duration"])
            state["engine"] = engine.name
            state["model"] = shown_model(args.model)
            state["prompt"] = shown_prompt(engine, args)
            for n, wav in wavs.items():
                if len(wavs) < 2:
                    found = engine.transcribe(wav, n, work)
                else:
                    found = region_pass(engine, wav, n, work, state)
                segments[n], clamped = clamp(found, info["duration"])
                state["clamped"] += clamped
    if args.no_transcript:
        state["transcript_note"] = "skipped (--no-transcript)"
    elif not info["audio"]:
        state["transcript_note"] = "no audio stream"
    elif not segments:
        state["transcript_note"] = "every audio track is silent"
    else:
        segments, state["dropped"] = echo_rule(
            segments, [n for n in chosen if n in segments], chosen[0])
        state["lines"] = turns(segments, labels)
    settings = "threshold %.2f, floor %g s, min gap %g s, long edge %d px" % (
        args.threshold, args.floor, args.min_gap, args.frame_edge)
    if args.no_frames:
        state["frames_note"] = "skipped (--no-frames)"
    elif info["video"] is None:
        state["frames_note"] = "no video stream"
    else:
        compare = "previous" if os.environ.get(COMPARE_ENV) == "previous" else "last"
        edge = frame_edge(args.frame_edge, info["size"])
        frames = gray_frames(ffmpeg, recording, info["video"],
                             default_timeout("decode", info["duration"]))
        kept = select_keyframes(frames, args.threshold, args.floor, compare, args.min_gap)
        if not kept:
            raise DigestError(4, "cannot read the recording: the video stream decoded no frame")
        names = write_frames(ffmpeg, recording, info["video"], kept, edge, state["folder"])
        state["frames"] = list(zip(kept, names))
        state["frames_note"] = "%d (%s)" % (len(kept), settings)
        width, height = image_size(ffprobe, os.path.join(state["folder"], names[0]))
        state["frame_tokens"] = (math.ceil(width / 28) * math.ceil(height / 28), width, height)
    target = os.path.join(out, stem + ".digest.md")
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown(recording, info, args, state))
    return target


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        target = digest(args)
    except DigestError as error:
        print("video-digest: " + str(error), file=sys.stderr)
        return error.code
    except subprocess.TimeoutExpired as error:
        print("video-digest: a step timed out: %s" % tail(str(error.cmd)[:200]), file=sys.stderr)
        return 4
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
