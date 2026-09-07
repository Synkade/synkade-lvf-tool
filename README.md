# LVF Tool

Compresses a standard video file (mp4/mov/mkv/…) into **`.lvf`** (*Level
Video Format*), the Dance Game project's own container: video-only (no
audio track), encoded with a standard video codec, wrapped in a custom
container, and encrypted block-by-block with AES-256. It also decompresses
`.lvf` back to a standard `.mp4` for preview/debugging.

The same tool also splits out and compresses the **audio** track into its
own format, **`.laf`** (*Level Audio Format*), as FLAC (lossless) or Opus
(lossy) — see [`FORMAT.md`](FORMAT.md) for the exact byte layout of both
formats.

**Audio-only inputs are supported too:** if you point the tool at a file
that has no video stream at all (an `.mp3`, `.wav`, `.flac`, …), it
auto-detects this and only the audio pipeline runs — the "Compress video"
option is disabled in the GUI, and any `.lvf` output path you may have
given on the CLI is ignored, with a warning.

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/download.html) — `ffmpeg` and `ffprobe` must
  be on your `PATH`. This is a system dependency, not a pip package.
- PySide6 and `cryptography` (installed automatically, see below)

## Install (from source)

```bash
python3 -m venv .venv
source .venv/bin/activate          # .venv\Scripts\activate on Windows
pip install -e .
```

## Usage

### GUI

```bash
lvf-tool
# or: python -m lvf_tool
```

Launches the (dark-theme only) window with **Compress** and **Decompress**
tabs, matching the checkbox/radio-button layout described in the spec.

### CLI

```bash
# Compress a video, with both a .lvf and a lossy .laf audio track
lvf-tool compress input.mp4 output.lvf \
    --resolution 1920x1080 --framerate 30 --codec h264 --bitrate 6000 \
    --key "my-passphrase-or-path-to-keyfile" \
    --audio lossy --audio-output output.laf --audio-bitrate 160

# Compress an audio-only file (mp3/wav/flac/…) — no .lvf is produced,
# the positional output path is not needed:
lvf-tool compress narration.wav --audio lossless --audio-output narration.laf

# Decompress a .lvf back to a standard, silent .mp4
lvf-tool decompress output.lvf preview.mp4 --key "my-passphrase-or-path-to-keyfile"

# Extract a .laf — codec (FLAC/Opus) is auto-detected from its header
lvf-tool decompress-audio output.laf recovered
# add --decode-wav to get a .wav instead of the original FLAC/Opus file
```

Run `lvf-tool <command> --help` for the full flag list.

## Encryption keys

`--key` accepts either:
- a path to a file containing exactly 32 raw bytes (used as-is as the
  AES-256 key), or
- any other string, which is stretched into a 32-byte key with
  PBKDF2-HMAC-SHA256.

Losing the key makes an encrypted `.lvf`/block unrecoverable by design —
there is no backdoor.

## Project layout

```
lvf_tool/
    format.py         .lvf header/index-table (de)serialization
    laf_format.py      .laf header (de)serialization
    crypto.py           AES-256-CTR/GCM wrappers (cryptography package)
    ffmpeg_utils.py     ffmpeg/ffprobe process wrappers, stream detection
    compressor.py        compression pipeline + audio-only input handling
    decompressor.py      decompression pipeline
    cli.py                 argparse CLI
    gui/                  PySide6 GUI (dark theme only)
tests/                    unittest suite (shells out to real ffmpeg)
```

## Running the tests

```bash
python3 -m unittest discover -s tests -v
```

The pipeline tests actually invoke `ffmpeg`/`ffprobe` to generate a small
synthetic clip and run it through the full compress → decompress cycle
(both AES-256-CTR and AES-256-GCM), including the audio-only-input case.
They're skipped automatically if `ffmpeg` isn't on `PATH`.

## Building a standalone executable (Nuitka)

Each OS build must be produced **on that OS** (Nuitka doesn't
cross-compile). Run the matching command on Windows, Linux, and macOS,
then attach the three resulting binaries to the same GitHub release.

```bash
pip install nuitka

# Windows
python -m nuitka --standalone --onefile --enable-plugin=pyside6 ^
    --windows-console-mode=disable --output-filename=lvf-tool.exe ^
    lvf_tool/__main__.py

# Linux
python -m nuitka --standalone --onefile --enable-plugin=pyside6 \
    --output-filename=lvf-tool lvf_tool/__main__.py

# macOS
python -m nuitka --standalone --onefile --enable-plugin=pyside6 \
    --macos-create-app-bundle --output-filename=lvf-tool lvf_tool/__main__.py
```

Note that the built executable still needs `ffmpeg`/`ffprobe` available on
the end user's machine (they aren't bundled by Nuitka automatically).

## License

MIT — see [`LICENSE`](LICENSE).
