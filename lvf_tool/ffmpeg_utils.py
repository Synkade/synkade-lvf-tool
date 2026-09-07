"""
Thin wrappers around the `ffmpeg` / `ffprobe` command-line binaries.

We invoke them as external processes (per spec section 1.2, that's an
explicitly allowed option) rather than depending on an `ffmpeg-python`
wrapper package, which keeps the runtime dependency list to just
"ffmpeg installed on PATH" plus pure-Python packages.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

from .format import Codec
from .laf_format import AudioCodec


class FfmpegNotFoundError(RuntimeError):
    pass


class FfmpegError(RuntimeError):
    pass


def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FfmpegNotFoundError(
            f"'{name}' was not found on PATH. Install FFmpeg and make sure "
            f"'{name}' is available in your terminal/GUI environment."
        )
    return path


def _run(args: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True)
    if proc.returncode != 0:
        raise FfmpegError(
            f"Command failed ({args[0]}): {' '.join(args)}\n"
            f"{proc.stderr.decode('utf-8', 'replace')}"
        )
    return proc


@dataclasses.dataclass
class InputInfo:
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None
    framerate: float | None = None
    video_codec_name: str | None = None
    duration: float = 0.0
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    audio_codec_name: str | None = None

    @property
    def is_audio_only(self) -> bool:
        """True when the file has an audio stream and no video stream."""
        return self.has_audio and not self.has_video


def probe_input(path) -> InputInfo:
    """
    Inspect a media file with ffprobe and report which stream types it has.

    Used both to validate video inputs and to power the "audio-only input"
    auto-detection: if a file has no video stream at all, the tool should
    only offer the audio pipeline.
    """
    ffprobe = _require_binary("ffprobe")
    proc = _run([
        ffprobe, "-v", "error",
        "-show_entries", "stream=index,codec_type,codec_name,width,height,"
                          "r_frame_rate,sample_rate,channels",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ])
    data = json.loads(proc.stdout.decode("utf-8"))
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    info = InputInfo(has_video=False, has_audio=False)
    info.duration = float(fmt.get("duration", 0.0) or 0.0)

    for s in streams:
        if s.get("codec_type") == "video" and not info.has_video:
            info.has_video = True
            info.width = s.get("width")
            info.height = s.get("height")
            info.video_codec_name = s.get("codec_name")
            fr = s.get("r_frame_rate", "0/1")
            info.framerate = _parse_rational(fr)
        elif s.get("codec_type") == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_sample_rate = int(s["sample_rate"]) if s.get("sample_rate") else None
            info.audio_channels = s.get("channels")
            info.audio_codec_name = s.get("codec_name")

    return info


def _parse_rational(value: str) -> float:
    if "/" in value:
        num, den = value.split("/")
        den = float(den)
        return float(num) / den if den else 0.0
    return float(value)


def detect_input_kind(path) -> str:
    """
    Returns one of: "video", "audio_only", "no_media".

    "video" covers files that contain a video stream (even if they also
    have audio - the video pipeline is still the primary one for those).
    "audio_only" is a file with an audio stream and no video stream at
    all - the new case where only the audio options should be enabled.
    """
    try:
        info = probe_input(path)
    except FfmpegError:
        # ffprobe couldn't parse the file as media at all (wrong extension,
        # corrupted file, plain text, etc.) - treat that the same as "no
        # usable stream" rather than crashing the caller.
        return "no_media"
    if info.has_video:
        return "video"
    if info.has_audio:
        return "audio_only"
    return "no_media"


# --- Video encode/decode -----------------------------------------------

_ELEMENTARY_MUX = {
    Codec.H264: "h264",
    Codec.VP9: "ivf",
    Codec.AV1: "ivf",
}


def elementary_stream_suffix(codec: Codec) -> str:
    return {"h264": ".h264", "ivf": ".ivf"}[_ELEMENTARY_MUX[codec]]


def encode_video_elementary(input_path, out_stream_path, *, width: int, height: int,
                             framerate: float, codec: Codec, bitrate_kbps: int) -> None:
    """
    Normalize resolution/framerate, strip audio, and encode to a raw
    elementary/IVF stream ready to be sliced into keyframe-aligned blocks.
    """
    ffmpeg = _require_binary("ffmpeg")
    mux = _ELEMENTARY_MUX[codec]
    args = [
        ffmpeg, "-y", "-i", str(input_path),
        "-an",  # 1.4.b - remove audio from the video output
        "-vf", f"scale={width}:{height},fps={framerate}",
        "-c:v", codec.ffmpeg_name(),
        "-b:v", f"{bitrate_kbps}k",
        "-g", "48",  # keyframe interval; keeps blocks a manageable size
        "-f", mux,
        str(out_stream_path),
    ]
    _run(args)


def get_keyframe_block_offsets(stream_path) -> list[int]:
    """
    Return byte offsets (within stream_path) where each block should start,
    i.e. the byte position of every keyframe packet. The first offset is
    always 0. A block spans from one entry to the next (or EOF for the
    last one) - this becomes the LVF index table (spec 1.3/1.4).
    """
    ffprobe = _require_binary("ffprobe")
    proc = _run([
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "packet=pos,flags",
        "-of", "json",
        str(stream_path),
    ])
    data = json.loads(proc.stdout.decode("utf-8"))
    packets = data.get("packets", [])
    offsets = []
    for p in packets:
        flags = p.get("flags", "")
        if "K" in flags:
            pos = p.get("pos")
            if pos is not None:
                offsets.append(int(pos))
    if not offsets:
        offsets = [0]
    elif offsets[0] != 0:
        offsets[0] = 0
    return offsets


def count_video_frames(stream_path) -> int:
    ffprobe = _require_binary("ffprobe")
    proc = _run([
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-count_packets",
        "-show_entries", "stream=nb_read_packets",
        "-of", "json",
        str(stream_path),
    ])
    data = json.loads(proc.stdout.decode("utf-8"))
    streams = data.get("streams", [])
    if not streams:
        return 0
    return int(streams[0].get("nb_read_packets", 0) or 0)


def remux_elementary_to_mp4(stream_path, codec: Codec, output_mp4_path,
                             framerate: float) -> None:
    """Wrap a decrypted elementary/IVF stream back into a standard mp4 (no audio)."""
    ffmpeg = _require_binary("ffmpeg")
    mux = _ELEMENTARY_MUX[codec]
    args = [
        ffmpeg, "-y",
        "-f", mux, "-r", str(framerate), "-i", str(stream_path),
        "-c:v", "copy",
        "-an",
        str(output_mp4_path),
    ]
    _run(args)


# --- Audio encode/decode -------------------------------------------------

_AUDIO_ENCODER = {
    AudioCodec.FLAC: "flac",
    AudioCodec.OPUS: "libopus",
}


def encode_audio(input_path, codec: AudioCodec, out_encoded_path, *,
                  bitrate_kbps: int | None = None) -> tuple[int, int, float]:
    """
    Extract + encode the audio track of input_path to a standalone
    .flac/.opus file. Returns (sample_rate, channels, duration) read back
    from the produced file so the LAF header reflects reality exactly.
    """
    ffmpeg = _require_binary("ffmpeg")
    args = [ffmpeg, "-y", "-i", str(input_path), "-vn", "-c:a", _AUDIO_ENCODER[codec]]
    if codec == AudioCodec.OPUS:
        args += ["-b:a", f"{bitrate_kbps or 160}k"]
    args.append(str(out_encoded_path))
    _run(args)

    info = probe_input(out_encoded_path)
    return info.audio_sample_rate or 0, info.audio_channels or 0, info.duration


def decode_audio_to_wav(encoded_bytes: bytes, codec: AudioCodec, tmp_in_path,
                         out_wav_path) -> None:
    """Write encoded_bytes to tmp_in_path (with the right suffix already set
    by the caller) then transcode to a standard .wav for playback/preview."""
    ffmpeg = _require_binary("ffmpeg")
    Path(tmp_in_path).write_bytes(encoded_bytes)
    _run([ffmpeg, "-y", "-i", str(tmp_in_path), str(out_wav_path)])
