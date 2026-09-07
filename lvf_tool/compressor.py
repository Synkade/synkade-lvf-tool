"""
Compression pipeline: input file (video or audio-only) -> .lvf and/or .laf.

Implements spec sections 1.4 (video), 1.7 (audio split-out), plus the added
requirement: if the input file has no video stream at all ("audio-only"
input, e.g. the user drags in an .mp3/.wav/.flac), the video pipeline is
never run, regardless of what was requested - only the audio options are
honored. This is the same rule the GUI enforces visually (see
gui/main_window.py) by disabling the "Compress video" checkbox in that
case; this module enforces it again at the logic layer so the CLI and any
other caller can't bypass it.
"""
from __future__ import annotations

import dataclasses
import os
import tempfile
from pathlib import Path

from . import ffmpeg_utils
from .crypto import encrypt_block_ctr, encrypt_block_gcm, load_key
from .format import Codec, EncryptionMode, LvfHeader, pack_index_table, derive_block_iv, HEADER_SIZE
from .laf_format import AudioCodec, write_laf


@dataclasses.dataclass
class VideoOptions:
    output_path: str
    width: int
    height: int
    framerate: float
    codec: Codec = Codec.H264
    bitrate_kbps: int = 4000
    key: str = ""
    enc_mode: EncryptionMode = EncryptionMode.AES_256_CTR
    verify_after: bool = True


AUDIO_WARNING_TEXT = {
    "lossless": "Mayor calidad, sin pérdidas, archivo más pesado.",
    "lossy": "Archivo más pequeño, compresión con pérdidas (generalmente "
             "imperceptible a partir de 128kbps).",
}


@dataclasses.dataclass
class AudioOptions:
    output_path: str
    mode: str = "lossless"  # "lossless" | "lossy" | "none"
    bitrate_kbps: int = 160  # only used for lossy/Opus


@dataclasses.dataclass
class CompressRequest:
    input_path: str
    do_video: bool = False
    video_opts: VideoOptions | None = None
    do_audio: bool = False
    audio_opts: AudioOptions | None = None


@dataclasses.dataclass
class CompressResult:
    input_kind: str  # "video" | "audio_only"
    video_written: str | None = None
    audio_written: str | None = None
    warnings: list[str] = dataclasses.field(default_factory=list)


class CompressionError(RuntimeError):
    pass


def plan_and_validate(request: CompressRequest) -> tuple[CompressRequest, list[str]]:
    """
    Inspects the input file and adjusts/validates the request:
      - "no_media" input -> raises CompressionError.
      - "audio_only" input -> forces do_video=False (with a warning if the
        caller had asked for video), and requires do_audio to be True.
      - "video" input -> request is left as given (both options are
        independent checkboxes per spec 1.10).
    """
    warnings: list[str] = []
    kind = ffmpeg_utils.detect_input_kind(request.input_path)

    if kind == "no_media":
        raise CompressionError(
            f"'{request.input_path}' has neither a video nor an audio stream - "
            "nothing to compress."
        )

    if kind == "audio_only":
        if request.do_video:
            warnings.append(
                "Audio-only input detected: video compression was requested but "
                "has been skipped, since there is no video stream to encode."
            )
        request = dataclasses.replace(request, do_video=False, video_opts=None)
        if not request.do_audio:
            raise CompressionError(
                "Audio-only input detected, but no audio output was requested. "
                "Enable the audio option to process this file."
            )

    return request, warnings


def run_compress(request: CompressRequest) -> CompressResult:
    request, warnings = plan_and_validate(request)
    kind = ffmpeg_utils.detect_input_kind(request.input_path)
    result = CompressResult(input_kind=kind, warnings=warnings)

    if request.do_video:
        assert request.video_opts is not None
        compress_video(request.input_path, request.video_opts)
        result.video_written = request.video_opts.output_path

    if request.do_audio:
        assert request.audio_opts is not None
        if request.audio_opts.mode != "none":
            compress_audio(request.input_path, request.audio_opts)
            result.audio_written = request.audio_opts.output_path

    return result


def compress_video(input_path: str, opts: VideoOptions) -> None:
    """Implements spec 1.4: mp4/mov/... -> .lvf"""
    key = load_key(opts.key) if opts.key else None
    if opts.enc_mode != EncryptionMode.NONE and key is None:
        raise CompressionError("An encryption key is required unless enc_mode is NONE.")

    with tempfile.TemporaryDirectory(prefix="lvf_compress_") as tmpdir:
        stream_path = Path(tmpdir) / ("elementary" + ffmpeg_utils.elementary_stream_suffix(opts.codec))

        # 1.4.2: FFmpeg normalizes resolution/framerate, strips audio, encodes.
        ffmpeg_utils.encode_video_elementary(
            input_path, stream_path,
            width=opts.width, height=opts.height, framerate=opts.framerate,
            codec=opts.codec, bitrate_kbps=opts.bitrate_kbps,
        )

        # 1.4.3: split into keyframe-aligned blocks.
        block_starts = ffmpeg_utils.get_keyframe_block_offsets(stream_path)
        frame_count = ffmpeg_utils.count_video_frames(stream_path)
        stream_bytes = stream_path.read_bytes()
        block_bounds = list(zip(block_starts, block_starts[1:] + [len(stream_bytes)]))

        iv_base = os.urandom(16)
        encrypted_blocks: list[bytes] = []
        for i, (start, end) in enumerate(block_bounds):
            plaintext = stream_bytes[start:end]
            if opts.enc_mode == EncryptionMode.NONE:
                encrypted_blocks.append(plaintext)
            else:
                iv = derive_block_iv(iv_base, i)
                if opts.enc_mode == EncryptionMode.AES_256_CTR:
                    encrypted_blocks.append(encrypt_block_ctr(key, iv, plaintext))
                else:
                    encrypted_blocks.append(encrypt_block_gcm(key, iv, plaintext))

        header = LvfHeader(
            width=opts.width, height=opts.height, framerate=opts.framerate,
            codec=opts.codec, frame_count=frame_count,
            enc_flag=opts.enc_mode, iv_base=iv_base,
        )

        # 1.4.5: compute final byte offsets (header + index table + running
        # offset through the block list) then write header+index+blocks.
        header_bytes = header.pack()
        n_blocks = len(encrypted_blocks)
        index_table_size = 4 + n_blocks * 8  # matches pack_index_table's layout
        offsets = []
        running = HEADER_SIZE + index_table_size
        for b in encrypted_blocks:
            offsets.append(running)
            running += len(b)

        with open(opts.output_path, "wb") as f:
            f.write(header_bytes)
            f.write(pack_index_table(offsets))
            for b in encrypted_blocks:
                f.write(b)

    if opts.verify_after:
        _verify_video(opts.output_path, opts.key, expected_frame_count=header.frame_count)


def _verify_video(lvf_path: str, key_or_path: str, expected_frame_count: int) -> None:
    """1.4.6: run the decompression path over the freshly-written file and
    check the frame count matches, as a cheap integrity smoke test."""
    from .decompressor import read_lvf_frame_count  # local import, avoids a cycle
    actual = read_lvf_frame_count(lvf_path, key_or_path)
    if actual != expected_frame_count:
        raise CompressionError(
            f"Post-compression verification failed for '{lvf_path}': "
            f"expected {expected_frame_count} frames, decoded {actual}."
        )


def compress_audio(input_path: str, opts: AudioOptions) -> None:
    """Implements spec 1.7/1.8: extract + encode audio -> .laf"""
    if opts.mode not in ("lossless", "lossy"):
        raise CompressionError(f"Unknown audio mode: {opts.mode!r}")

    codec = AudioCodec.FLAC if opts.mode == "lossless" else AudioCodec.OPUS
    with tempfile.TemporaryDirectory(prefix="laf_compress_") as tmpdir:
        encoded_path = Path(tmpdir) / f"audio{codec.extension()}"
        sample_rate, channels, duration = ffmpeg_utils.encode_audio(
            input_path, codec, encoded_path,
            bitrate_kbps=opts.bitrate_kbps if opts.mode == "lossy" else None,
        )
        encoded_bytes = encoded_path.read_bytes()

    write_laf(opts.output_path, codec, sample_rate, channels, duration, encoded_bytes)
