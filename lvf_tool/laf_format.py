"""
LAF container format (.laf) — Level Audio Format.

Unencrypted container (see spec section 1.8):

[HEADER]
    magic:          4s   b"LAF1"
    codec:          B    0=FLAC, 1=Opus
    sample_rate:    I    uint32
    channels:       B
    duration:       f    float32 (seconds)
    stream_size:    I    uint32 (bytes of the audio stream that follow)

[STREAM]
    raw bytes of the already-encoded FLAC or Opus file, untouched.
"""
from __future__ import annotations

import dataclasses
import struct
from enum import IntEnum


MAGIC = b"LAF1"

_HEADER_STRUCT = struct.Struct("<4sBIBfI")
HEADER_SIZE = _HEADER_STRUCT.size


class AudioCodec(IntEnum):
    FLAC = 0
    OPUS = 1

    @classmethod
    def from_name(cls, name: str) -> "AudioCodec":
        return {"flac": cls.FLAC, "opus": cls.OPUS}[name.lower()]

    def extension(self) -> str:
        return {AudioCodec.FLAC: ".flac", AudioCodec.OPUS: ".opus"}[self]


class LafFormatError(ValueError):
    pass


@dataclasses.dataclass
class LafHeader:
    codec: AudioCodec
    sample_rate: int
    channels: int
    duration: float
    stream_size: int

    def pack(self) -> bytes:
        return _HEADER_STRUCT.pack(
            MAGIC,
            int(self.codec),
            self.sample_rate,
            self.channels,
            float(self.duration),
            self.stream_size,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "LafHeader":
        if len(data) < HEADER_SIZE:
            raise LafFormatError(
                f"Truncated .laf header: expected {HEADER_SIZE} bytes, got {len(data)}"
            )
        magic, codec, sample_rate, channels, duration, stream_size = _HEADER_STRUCT.unpack(
            data[:HEADER_SIZE]
        )
        if magic != MAGIC:
            raise LafFormatError(f"Bad magic bytes: {magic!r} (expected {MAGIC!r})")
        return cls(
            codec=AudioCodec(codec),
            sample_rate=sample_rate,
            channels=channels,
            duration=duration,
            stream_size=stream_size,
        )


def write_laf(path, codec: AudioCodec, sample_rate: int, channels: int,
              duration: float, encoded_stream: bytes) -> None:
    header = LafHeader(
        codec=codec,
        sample_rate=sample_rate,
        channels=channels,
        duration=duration,
        stream_size=len(encoded_stream),
    )
    with open(path, "wb") as f:
        f.write(header.pack())
        f.write(encoded_stream)


def read_laf(path) -> tuple[LafHeader, bytes]:
    with open(path, "rb") as f:
        data = f.read()
    header = LafHeader.unpack(data)
    stream = data[HEADER_SIZE:HEADER_SIZE + header.stream_size]
    if len(stream) != header.stream_size:
        raise LafFormatError("Truncated .laf audio stream")
    return header, stream
