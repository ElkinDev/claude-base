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

   A recorder that keeps a mixed track beside the split ones (OBS with three audio tracks, say)
   is run as `--tracks 2,3 --track-labels me,others`: stream numbers from 1, in file order, labels
   in the order given. Headphones avoid the echo of the remote side in the microphone track. For
   whisper-cli, `--threads N` (default: logical cores minus 4, at least 4) sets the CPU threads,
   `--prompt "<text>"` primes punctuation and casing, and `--vad-model <ggml file>` turns on its
   voice activity detection.
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

`--engine auto` tries, in order: FFmpeg's whisper filter and whisper.cpp's `whisper-cli` (both
take `--model` as a ggml model file), then faster-whisper and openai-whisper in `--engine-python`
(`--model` as a model name such as `large-v3`). The model is always the user's choice; a small
model costs accuracy (Whisper tiny differed from large-v3 on about 15 percent of the words of one
meeting).

- 0: done; the digest path is printed.
- 2: bad arguments (a `--tracks` number past the last audio stream), or ffmpeg and ffprobe not
  found (pass `--ffmpeg <dir>`).
- 3: no transcription engine, or the engine failed; the message names what to install. Report it,
  or rerun with `--no-transcript`.
- 4: the recording cannot be read, or a step timed out (the message names it).

Frames are kept when the picture differs from the last kept frame (`--threshold`, default 0.90),
and at least every `--floor` seconds (default 60), so a document scrolled slowly for half an hour
still yields a frame a minute.
