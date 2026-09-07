"""
Command-line interface for LVF Tool.

    lvf-tool compress <input> [output.lvf]
        --resolution WIDTHxHEIGHT   (defaults to source resolution)
        --framerate N               (defaults to source framerate)
        --codec h264|vp9|av1        (default: h264)
        --bitrate N                 (kbps, default: 4000)
        --enc-mode ctr|gcm          (default: ctr; "none" disables encryption, debug only)
        --key <key-or-passphrase-or-keyfile>
        --audio lossless|lossy|none (default: none)
        --audio-output <output.laf>
        --audio-bitrate N           (kbps, default: 160, lossy only)

    lvf-tool decompress <input.lvf> <output.mp4>
        --key <key-or-passphrase-or-keyfile>

    lvf-tool decompress-audio <input.laf> <output>
        --decode-wav                (decode to .wav instead of keeping FLAC/Opus)

Note on the CLI's "output.lvf" being optional: if the input turns out to be
an audio-only file (no video stream at all - e.g. an mp3), video
compression is skipped automatically and the positional output path, if
given, is ignored. This mirrors the GUI, where the "Compress video"
checkbox gets disabled the moment an audio-only file is loaded.
"""
from __future__ import annotations

import argparse
import sys

from . import ffmpeg_utils
from .compressor import AudioOptions, CompressRequest, VideoOptions, run_compress, CompressionError
from .decompressor import decompress_audio, decompress_video, DecompressionError
from .format import Codec, EncryptionMode


def _parse_resolution(value: str) -> tuple[int, int]:
    try:
        w, h = value.lower().split("x")
        return int(w), int(h)
    except Exception:
        raise argparse.ArgumentTypeError("Resolution must look like WIDTHxHEIGHT, e.g. 1920x1080")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lvf-tool", description="LVF Tool - .lvf / .laf compressor")
    sub = parser.add_subparsers(dest="command", required=True)

    p_compress = sub.add_parser("compress", help="Compress a video (and/or extract audio) input")
    p_compress.add_argument("input")
    p_compress.add_argument("output", nargs="?", default=None,
                             help="Output .lvf path. Optional/ignored for audio-only inputs.")
    p_compress.add_argument("--resolution", type=_parse_resolution, default=None)
    p_compress.add_argument("--framerate", type=float, default=None)
    p_compress.add_argument("--codec", choices=["h264", "vp9", "av1"], default="h264")
    p_compress.add_argument("--bitrate", type=int, default=4000)
    p_compress.add_argument("--enc-mode", choices=["ctr", "gcm", "none"], default="ctr")
    p_compress.add_argument("--key", default="")
    p_compress.add_argument("--audio", choices=["lossless", "lossy", "none"], default="none")
    p_compress.add_argument("--audio-output", default=None)
    p_compress.add_argument("--audio-bitrate", type=int, default=160)
    p_compress.add_argument("--no-verify", action="store_true",
                             help="Skip the post-compression integrity self-check")

    p_decompress = sub.add_parser("decompress", help="Decompress a .lvf back to .mp4 (no audio)")
    p_decompress.add_argument("input")
    p_decompress.add_argument("output")
    p_decompress.add_argument("--key", default="")

    p_decompress_audio = sub.add_parser("decompress-audio", help="Extract a .laf back to FLAC/Opus/WAV")
    p_decompress_audio.add_argument("input")
    p_decompress_audio.add_argument("output")
    p_decompress_audio.add_argument("--decode-wav", action="store_true")

    return parser


def _cmd_compress(args: argparse.Namespace) -> int:
    kind = ffmpeg_utils.detect_input_kind(args.input)

    do_video = args.output is not None and kind != "audio_only"
    if args.output is not None and kind == "audio_only":
        print(
            "[lvf-tool] Audio-only input detected - the output.lvf path you gave "
            "will be ignored, only the audio pipeline will run.",
            file=sys.stderr,
        )

    video_opts = None
    if do_video:
        info = ffmpeg_utils.probe_input(args.input)
        width, height = args.resolution or (info.width, info.height)
        framerate = args.framerate or info.framerate or 30.0
        video_opts = VideoOptions(
            output_path=args.output,
            width=width, height=height, framerate=framerate,
            codec=Codec.from_name(args.codec),
            bitrate_kbps=args.bitrate,
            key=args.key,
            enc_mode=EncryptionMode[f"AES_256_{args.enc_mode.upper()}"] if args.enc_mode != "none"
            else EncryptionMode.NONE,
            verify_after=not args.no_verify,
        )

    do_audio = args.audio != "none" or kind == "audio_only"
    audio_opts = None
    if do_audio:
        mode = args.audio if args.audio != "none" else "lossless"
        audio_output = args.audio_output
        if not audio_output:
            base = args.output or args.input
            audio_output = _default_sibling_path(base, ".laf")
        audio_opts = AudioOptions(output_path=audio_output, mode=mode, bitrate_kbps=args.audio_bitrate)

    request = CompressRequest(
        input_path=args.input,
        do_video=do_video, video_opts=video_opts,
        do_audio=do_audio, audio_opts=audio_opts,
    )

    try:
        result = run_compress(request)
    except CompressionError as exc:
        print(f"[lvf-tool] Error: {exc}", file=sys.stderr)
        return 1

    for w in result.warnings:
        print(f"[lvf-tool] Warning: {w}", file=sys.stderr)
    if result.video_written:
        print(f"[lvf-tool] Video written: {result.video_written}")
    if result.audio_written:
        print(f"[lvf-tool] Audio written: {result.audio_written}")
    return 0


def _default_sibling_path(base_path: str, new_suffix: str) -> str:
    from pathlib import Path
    return str(Path(base_path).with_suffix(new_suffix))


def _cmd_decompress(args: argparse.Namespace) -> int:
    try:
        decompress_video(args.input, args.output, key_or_path=args.key or None)
    except DecompressionError as exc:
        print(f"[lvf-tool] Error: {exc}", file=sys.stderr)
        return 1
    print(f"[lvf-tool] Video written: {args.output}")
    return 0


def _cmd_decompress_audio(args: argparse.Namespace) -> int:
    try:
        final_path = decompress_audio(args.input, args.output, decode_to_wav=args.decode_wav)
    except DecompressionError as exc:
        print(f"[lvf-tool] Error: {exc}", file=sys.stderr)
        return 1
    print(f"[lvf-tool] Audio written: {final_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "compress":
        return _cmd_compress(args)
    if args.command == "decompress":
        return _cmd_decompress(args)
    if args.command == "decompress-audio":
        return _cmd_decompress_audio(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
