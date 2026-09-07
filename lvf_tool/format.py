"""
LVF container format (.lvf) — Level Video Format.

Layout (see spec section 1.3):

[HEADER] - unencrypted
    magic:          4s   b"LVF1"
    version:        B    format version, starts at 1
    width:          H    uint16
    height:         H    uint16
    framerate:      f    float32
    codec:          B    0=H264, 1=VP9, 2=AV1
    frame_count:    I    uint32
    enc_flag:       B    0=none, 1=AES-256-CTR, 2=AES-256-GCM
    iv_base:        16s  16 raw bytes

[INDEX TABLE] - unencrypted
    entry_count:    I    uint32
    entries[entry_count]:
        offset:     Q    uint64  (absolute byte offset of block in file)

[BLOCKS] - each block encrypted independently
"""
from __future__ import annotations

import struct
import dataclasses
from enum import IntEnum


MAGIC = b"LVF1"
FORMAT_VERSION = 1

# Fixed header layout (everything before the index table).
# < = little-endian, no padding.
_HEADER_STRUCT = struct.Struct("<4sBHHfBIB16s")
HEADER_SIZE = _HEADER_STRUCT.size  # 4+1+2+2+4+1+4+1+16 = 35 bytes

_INDEX_COUNT_STRUCT = struct.Struct("<I")
_INDEX_ENTRY_STRUCT = struct.Struct("<Q")


class Codec(IntEnum):
    H264 = 0
    VP9 = 1
    AV1 = 2

    @classmethod
    def from_name(cls, name: str) -> "Codec":
        return {"h264": cls.H264, "vp9": cls.VP9, "av1": cls.AV1}[name.lower()]

    def ffmpeg_name(self) -> str:
        return {Codec.H264: "libx264", Codec.VP9: "libvpx-vp9", Codec.AV1: "libaom-av1"}[self]


class EncryptionMode(IntEnum):
    NONE = 0
    AES_256_CTR = 1
    AES_256_GCM = 2


class LvfFormatError(ValueError):
    """Raised when a .lvf file is malformed, truncated, or has an unsupported version."""


@dataclasses.dataclass
class LvfHeader:
    width: int
    height: int
    framerate: float
    codec: Codec
    frame_count: int
    enc_flag: EncryptionMode
    iv_base: bytes  # 16 bytes
    version: int = FORMAT_VERSION

    def __post_init__(self):
        if len(self.iv_base) != 16:
            raise ValueError("iv_base must be exactly 16 bytes")

    def pack(self) -> bytes:
        return _HEADER_STRUCT.pack(
            MAGIC,
            self.version,
            self.width,
            self.height,
            float(self.framerate),
            int(self.codec),
            self.frame_count,
            int(self.enc_flag),
            self.iv_base,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "LvfHeader":
        if len(data) < HEADER_SIZE:
            raise LvfFormatError(
                f"Truncated .lvf header: expected {HEADER_SIZE} bytes, got {len(data)}"
            )
        (magic, version, width, height, framerate, codec, frame_count,
         enc_flag, iv_base) = _HEADER_STRUCT.unpack(data[:HEADER_SIZE])
        if magic != MAGIC:
            raise LvfFormatError(f"Bad magic bytes: {magic!r} (expected {MAGIC!r})")
        # Forward-compat: unknown future versions are tolerated as long as the
        # fixed fields we know about still parse; unknown trailing fields (if
        # any get added in a later revision) are simply ignored by this reader.
        try:
            codec_enum = Codec(codec)
        except ValueError:
            # Unknown codec id from a newer version: keep raw value accessible
            # but do not crash - caller can decide what to do.
            codec_enum = codec  # type: ignore[assignment]
        return cls(
            width=width,
            height=height,
            framerate=framerate,
            codec=codec_enum,
            frame_count=frame_count,
            enc_flag=EncryptionMode(enc_flag),
            iv_base=iv_base,
            version=version,
        )


def pack_index_table(block_offsets: list[int]) -> bytes:
    out = bytearray()
    out += _INDEX_COUNT_STRUCT.pack(len(block_offsets))
    for off in block_offsets:
        out += _INDEX_ENTRY_STRUCT.pack(off)
    return bytes(out)


def unpack_index_table(data: bytes, start: int = 0) -> tuple[list[int], int]:
    """Returns (offsets, bytes_consumed)."""
    if len(data) < start + _INDEX_COUNT_STRUCT.size:
        raise LvfFormatError("Truncated .lvf index table (count)")
    (count,) = _INDEX_COUNT_STRUCT.unpack_from(data, start)
    pos = start + _INDEX_COUNT_STRUCT.size
    entry_size = _INDEX_ENTRY_STRUCT.size
    needed = count * entry_size
    if len(data) < pos + needed:
        raise LvfFormatError("Truncated .lvf index table (entries)")
    offsets = [
        _INDEX_ENTRY_STRUCT.unpack_from(data, pos + i * entry_size)[0]
        for i in range(count)
    ]
    return offsets, (pos + needed) - start


def derive_block_iv(iv_base: bytes, block_index: int) -> bytes:
    """
    Derive a per-block 16-byte IV/counter from iv_base and the block index.

    Method: treat iv_base as a big 128-bit integer and XOR it with the block
    index (matches the "IV_base XOR indice_de_bloque" option from the spec).
    """
    base_int = int.from_bytes(iv_base, "big")
    derived = base_int ^ block_index
    return derived.to_bytes(16, "big")
