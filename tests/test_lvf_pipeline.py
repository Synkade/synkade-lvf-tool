"""
End-to-end tests for the LVF Tool pipeline (stdlib unittest - no extra
test-runner dependency required). These shell out to ffmpeg/ffprobe to
generate synthetic fixtures and to actually encode/decode media, so the
whole module is skipped if ffmpeg isn't on PATH.

Run with:  python3 -m unittest discover -s tests -v
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lvf_tool import ffmpeg_utils
from lvf_tool.compressor import (
    AudioOptions, CompressRequest, CompressionError, VideoOptions, run_compress,
)
from lvf_tool.decompressor import DecompressionError, decompress_audio, decompress_video
from lvf_tool.format import Codec, EncryptionMode, LvfHeader, derive_block_iv, pack_index_table, unpack_index_table
from lvf_tool.laf_format import AudioCodec, LafHeader

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


class FormatRoundtripTests(unittest.TestCase):
    """Pure struct-packing tests - no ffmpeg or crypto keys involved."""

    def test_lvf_header_roundtrip(self):
        header = LvfHeader(
            width=1920, height=1080, framerate=60.0, codec=Codec.H264,
            frame_count=1000, enc_flag=EncryptionMode.AES_256_CTR, iv_base=b"0" * 16,
        )
        restored = LvfHeader.unpack(header.pack())
        self.assertEqual(restored, header)

    def test_lvf_header_framerate_is_float32_precision(self):
        # framerate is stored as float32 (per spec) - a value like 59.94
        # is expected to lose some precision on the round trip, so this
        # is checked with an explicit tolerance rather than exact equality.
        header = LvfHeader(
            width=640, height=480, framerate=59.94, codec=Codec.VP9,
            frame_count=10, enc_flag=EncryptionMode.NONE, iv_base=b"\x00" * 16,
        )
        restored = LvfHeader.unpack(header.pack())
        self.assertAlmostEqual(restored.framerate, 59.94, places=3)

    def test_index_table_roundtrip(self):
        offsets = [100, 250, 4000, 999999]
        packed = pack_index_table(offsets)
        restored, consumed = unpack_index_table(packed)
        self.assertEqual(restored, offsets)
        self.assertEqual(consumed, len(packed))

    def test_block_iv_derivation_is_stable_and_distinct(self):
        base = b"\x01" * 16
        iv0 = derive_block_iv(base, 0)
        iv1 = derive_block_iv(base, 1)
        self.assertEqual(iv0, base)  # XOR with 0 is a no-op
        self.assertNotEqual(iv0, iv1)
        self.assertEqual(len(iv0), 16)
        self.assertEqual(len(iv1), 16)

    def test_laf_header_roundtrip(self):
        header = LafHeader(codec=AudioCodec.OPUS, sample_rate=48000, channels=2,
                            duration=12.5, stream_size=4096)
        self.assertEqual(LafHeader.unpack(header.pack()), header)


@unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg not on PATH")
class PipelineTests(unittest.TestCase):
    """Real end-to-end tests: actually invoke ffmpeg/ffprobe and AES."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="lvf_tests_"))
        cls.sample_video = cls.tmp / "sample.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(cls.sample_video), "-loglevel", "error",
        ], check=True)

        cls.sample_audio_only = cls.tmp / "sample.wav"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=1",
            str(cls.sample_audio_only), "-loglevel", "error",
        ], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_compress_decompress_video_ctr(self):
        lvf_path = self.tmp / "out_ctr.lvf"
        req = CompressRequest(
            input_path=str(self.sample_video), do_video=True,
            video_opts=VideoOptions(
                output_path=str(lvf_path), width=320, height=240, framerate=30,
                codec=Codec.H264, bitrate_kbps=600, key="test-key-123",
                enc_mode=EncryptionMode.AES_256_CTR,
            ),
        )
        result = run_compress(req)
        self.assertEqual(result.video_written, str(lvf_path))
        self.assertTrue(lvf_path.exists() and lvf_path.stat().st_size > 0)

        out_mp4 = self.tmp / "decoded_ctr.mp4"
        decompress_video(str(lvf_path), str(out_mp4), key_or_path="test-key-123")
        self.assertTrue(out_mp4.exists())

        info = ffmpeg_utils.probe_input(out_mp4)
        self.assertTrue(info.has_video)
        self.assertFalse(info.has_audio)  # audio must never survive into .lvf
        self.assertEqual((info.width, info.height), (320, 240))

    def test_compress_decompress_video_gcm(self):
        lvf_path = self.tmp / "out_gcm.lvf"
        req = CompressRequest(
            input_path=str(self.sample_video), do_video=True,
            video_opts=VideoOptions(
                output_path=str(lvf_path), width=320, height=240, framerate=30,
                codec=Codec.H264, bitrate_kbps=600, key="another-key",
                enc_mode=EncryptionMode.AES_256_GCM,
            ),
        )
        run_compress(req)
        out_mp4 = self.tmp / "decoded_gcm.mp4"
        decompress_video(str(lvf_path), str(out_mp4), key_or_path="another-key")
        self.assertTrue(out_mp4.exists())

    def test_wrong_key_fails_loudly(self):
        lvf_path = self.tmp / "out_wrongkey.lvf"
        run_compress(CompressRequest(
            input_path=str(self.sample_video), do_video=True,
            video_opts=VideoOptions(output_path=str(lvf_path), width=320, height=240,
                                     framerate=30, key="right-key",
                                     enc_mode=EncryptionMode.AES_256_GCM),
        ))
        with self.assertRaises(DecompressionError):
            decompress_video(str(lvf_path), str(self.tmp / "bad.mp4"), key_or_path="wrong-key")

    def test_audio_split_lossless_and_lossy(self):
        laf_lossless = self.tmp / "audio_lossless.laf"
        run_compress(CompressRequest(
            input_path=str(self.sample_video), do_audio=True,
            audio_opts=AudioOptions(output_path=str(laf_lossless), mode="lossless"),
        ))
        final = decompress_audio(str(laf_lossless), str(self.tmp / "recovered"))
        self.assertTrue(final.endswith(".flac") and Path(final).exists())

        laf_lossy = self.tmp / "audio_lossy.laf"
        run_compress(CompressRequest(
            input_path=str(self.sample_video), do_audio=True,
            audio_opts=AudioOptions(output_path=str(laf_lossy), mode="lossy", bitrate_kbps=96),
        ))
        final2 = decompress_audio(str(laf_lossy), str(self.tmp / "recovered2"))
        self.assertTrue(final2.endswith(".opus") and Path(final2).exists())

    def test_audio_only_input_forces_video_off(self):
        """The new requirement: an audio-only input must never produce a
        .lvf, even if a video output was explicitly requested."""
        lvf_path = self.tmp / "should_never_exist.lvf"
        laf_path = self.tmp / "audio_only_out.laf"
        req = CompressRequest(
            input_path=str(self.sample_audio_only),
            do_video=True,  # deliberately asking for video too
            video_opts=VideoOptions(output_path=str(lvf_path), width=320, height=240, framerate=30),
            do_audio=True,
            audio_opts=AudioOptions(output_path=str(laf_path), mode="lossless"),
        )
        result = run_compress(req)
        self.assertEqual(result.input_kind, "audio_only")
        self.assertIsNone(result.video_written)
        self.assertFalse(lvf_path.exists())
        self.assertEqual(result.audio_written, str(laf_path))
        self.assertTrue(any("skipped" in w.lower() for w in result.warnings))

    def test_audio_only_input_without_audio_option_raises(self):
        req = CompressRequest(input_path=str(self.sample_audio_only), do_video=False, do_audio=False)
        with self.assertRaises(CompressionError):
            run_compress(req)

    def test_no_media_input_raises(self):
        empty_txt = self.tmp / "not_media.txt"
        empty_txt.write_text("hello")
        req = CompressRequest(input_path=str(empty_txt), do_audio=True,
                               audio_opts=AudioOptions(output_path=str(self.tmp / "x.laf")))
        with self.assertRaises(CompressionError):
            run_compress(req)


if __name__ == "__main__":
    unittest.main()
