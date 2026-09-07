"""
Decompression pipeline: .lvf -> mp4 (no audio), .laf -> flac/opus/wav.

Implements spec sections 1.5 and 1.9.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from . import ffmpeg_utils
from .crypto import CryptoError, decrypt_block_ctr, decrypt_block_gcm, load_key
from .format import EncryptionMode, LvfHeader, unpack_index_table, derive_block_iv, HEADER_SIZE
from .laf_format import AudioCodec, read_laf


class DecompressionError(RuntimeError):
    pass


def read_lvf_header_and_blocks(lvf_path: str) -> tuple[LvfHeader, list[bytes]]:
    """Reads header + index table (unencrypted) and returns the raw
    (still-encrypted) block byte ranges, per spec 1.5 steps 1-2."""
    data = Path(lvf_path).read_bytes()
    header = LvfHeader.unpack(data)
    offsets, _consumed = unpack_index_table(data, start=HEADER_SIZE)
    bounds = list(zip(offsets, offsets[1:] + [len(data)]))
    blocks = [data[start:end] for start, end in bounds]
    return header, blocks


def decrypt_blocks(header: LvfHeader, blocks: list[bytes], key_or_path: str | None) -> bytes:
    """1.5 step 3: derive each block's IV and decrypt, concatenating the
    plaintext elementary stream back together in order."""
    if header.enc_flag == EncryptionMode.NONE:
        return b"".join(blocks)

    if not key_or_path:
        raise DecompressionError("This .lvf file is encrypted; a --key is required.")
    key = load_key(key_or_path)

    out = bytearray()
    try:
        for i, block in enumerate(blocks):
            iv = derive_block_iv(header.iv_base, i)
            if header.enc_flag == EncryptionMode.AES_256_CTR:
                out += decrypt_block_ctr(key, iv, block)
            elif header.enc_flag == EncryptionMode.AES_256_GCM:
                out += decrypt_block_gcm(key, iv, block)
            else:
                raise DecompressionError(f"Unknown encryption flag: {header.enc_flag}")
    except CryptoError as exc:
        raise DecompressionError(
            f"Decryption failed - the key is likely wrong, or block {i} is corrupted: {exc}"
        ) from exc
    return bytes(out)


def decompress_video(lvf_path: str, output_mp4_path: str, key_or_path: str | None = None) -> None:
    """Full 1.5 pipeline: .lvf -> standard mp4 without audio."""
    header, blocks = read_lvf_header_and_blocks(lvf_path)
    plaintext_stream = decrypt_blocks(header, blocks, key_or_path)

    with tempfile.TemporaryDirectory(prefix="lvf_decompress_") as tmpdir:
        stream_path = Path(tmpdir) / ("elementary" + ffmpeg_utils.elementary_stream_suffix(header.codec))
        stream_path.write_bytes(plaintext_stream)
        ffmpeg_utils.remux_elementary_to_mp4(
            stream_path, header.codec, output_mp4_path, framerate=header.framerate
        )


def read_lvf_frame_count(lvf_path: str, key_or_path: str | None = None) -> int:
    """Used by compressor.py's post-compression self-check (1.4.6): decode
    the file we just wrote and confirm the actual decoded frame count."""
    header, blocks = read_lvf_header_and_blocks(lvf_path)
    plaintext_stream = decrypt_blocks(header, blocks, key_or_path)
    with tempfile.TemporaryDirectory(prefix="lvf_verify_") as tmpdir:
        stream_path = Path(tmpdir) / ("elementary" + ffmpeg_utils.elementary_stream_suffix(header.codec))
        stream_path.write_bytes(plaintext_stream)
        return ffmpeg_utils.count_video_frames(stream_path)


def decompress_audio(laf_path: str, output_path: str, decode_to_wav: bool = False) -> str:
    """
    1.9: autodetect codec from the .laf header and extract to a standard
    file - no user input needed about which codec it is.

    Returns the path actually written (output_path, with its extension
    normalized to match the detected codec unless decode_to_wav is set).
    """
    header, stream = read_laf(laf_path)

    if decode_to_wav:
        with tempfile.TemporaryDirectory(prefix="laf_decode_") as tmpdir:
            tmp_in = Path(tmpdir) / f"in{header.codec.extension()}"
            ffmpeg_utils.decode_audio_to_wav(stream, header.codec, tmp_in, output_path)
        return output_path

    final_path = str(Path(output_path).with_suffix(header.codec.extension())) \
        if Path(output_path).suffix == "" else output_path
    Path(final_path).write_bytes(stream)
    return final_path
