---
name: video-digest
description: Turn a video or screen recording into one Markdown digest (timestamped transcript, keyframes, an index from frames to transcript lines) and read that instead of the video. Use when a task hands over a meeting recording, a screen capture or any video file (.mp4, .mkv, .mov, .webm, .avi).
---

# Video Digest

Claude reads images, not video. Handed a recording, read its digest: a text file with the
transcript and a short list of keyframes, made on this machine with no agent in the loop.

## When

A task hands over a video or a screen recording, or points at one: a meeting, a demo, a bug
reproduction, a walkthrough. Not for a still image or a GIF, which you can open directly.

## How

1. Run the tool once per recording:

   ```
   python <kit>/scripts/video-digest.py <recording> --model <model> [--language <code>]
   ```

   For a recording with one audio track per side (a local microphone and the remote call), add
   `--track-labels me,others` in stream order. `--no-transcript` gives the frames alone and needs no
   engine; `--no-frames` gives the transcript alone.

   OBS with three audio tracks (1 the mix you heard, 2 your microphone, 3 the call) is run as
   `--tracks 2,3 --track-labels me,others`: stream numbers from 1, in file order, labels in order.

   With two or more streams the tool runs in region mode: it cuts each stream to its speech, joins
   the pieces with short gaps of silence, runs the engine on that without VAD and maps every word
   back by its start, so the turns of a stream that is silent half the time keep their order and
   their times. The Metadata lists each stream's regions and the text dropped because it fell only
   in a gap. One stream takes the plain path. Headphones avoid the echo of the remote side in the
   microphone track.

   `--threads N` (default: logical cores minus 4, at least 4) sets whisper-cli's CPU threads.
   `--prompt "<text>"` primes punctuation and casing; with `--language en` and no `--prompt` a
   short meeting greeting is sent, and `--prompt ""` sends none (the Metadata names the prompt).
   `--vad-model <ggml file>` turns on voice activity detection for a single stream.
   `--whisper-cli <path>` names the binary when it is not on PATH, and `--gpu-device N` picks the
   GPU of FFmpeg's whisper filter.
2. Read `<stem>.digest.md` beside the recording (or in `--out <dir>`): the Metadata block, then
   the whole Transcript.
3. Open a frame only for a moment the screen matters: what was shown, a number on a slide, an
   error on screen. Each `### [h:mm:ss]` keyframe heading names the transcript line it falls in,
   so pick frames from the transcript, never page through all of them.
4. If the Metadata lists a silent track, say so to the user: the recorder probably did not
   capture that side, and its speech is missing from the transcript.

## What it costs

A frame is `ceil(w/28) x ceil(h/28)` visual tokens: 1,196 at 1280x720, the default long edge; a smaller source is never upscaled (the digest's
first line gives the figure for its own frames). A 29-minute meeting transcript is about
5,300 tokens. Reading the transcript and opening three frames costs less than opening ten frames
blind.

## Privacy

The audio is transcribed on this machine and stays local. The Markdown and every frame you open
go to the model provider, so a recording that must not leave the machine is not read this way.

## Engines and exit codes

`--engine auto` tries, in order: whisper.cpp's `whisper-cli` (`--model` as a ggml model file),
faster-whisper and openai-whisper in `--engine-python` (`--model` as a model name such as
`large-v3`), then FFmpeg's whisper filter (a ggml model file; it ran at 0.3 to 0.5x real time per
stream, its cues can start up to 20 s early, and it takes no prompt). The model is always the
user's choice; a small model costs accuracy (Whisper tiny differed from large-v3 on about 15
percent of the words of one meeting).

- 0: done; the digest path is printed.
- 2: bad arguments (a `--tracks` number past the last audio stream), or ffmpeg and ffprobe not
  found (pass `--ffmpeg <dir>`).
- 3: no transcription engine, or the engine failed; the message names what to install. Report it,
  or rerun with `--no-transcript`.
- 4: the recording cannot be read, or a step timed out (the message names it).

Frames are kept when the picture differs from the last kept frame (`--threshold`, default 0.90),
and at least every `--floor` seconds (default 60), so a document scrolled slowly for half an hour
still yields a frame a minute. A change inside `--min-gap` seconds (default 5) of the last kept
frame waits for the first frame after the gap that still differs, so a moving camera tile does not
fill the folder; `--min-gap 0` keeps every change. Track numbers in the digest, on stderr and in
`--tracks` all count from 1.
