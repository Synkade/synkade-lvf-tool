"""
LVF Tool - main GUI window.

Compress tab follows spec 1.10: two independent checkboxes (video / audio).
Decompress tab follows spec 1.10: single-choice radio buttons (video / audio).

New behavior (added after the original spec): the moment an input file is
selected, it's probed with ffprobe. If it turns out to have no video
stream at all (audio-only, e.g. an mp3/wav/flac dropped in by mistake or
on purpose), the "Compress video" checkbox is unchecked, disabled, and
replaced with an explanatory hint - only the audio options stay usable.
This mirrors the same rule compressor.plan_and_validate() enforces at the
logic layer, so the CLI can't bypass it either.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QRadioButton,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .. import ffmpeg_utils
from ..compressor import AudioOptions, CompressRequest, VideoOptions, run_compress
from ..decompressor import decompress_audio, decompress_video
from ..format import Codec, EncryptionMode
from .theme import apply_dark_theme
from .worker import Worker

WINDOW_TITLE = "LVF Tool"


def _hint_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "hint")
    label.setWordWrap(True)
    return label


class CompressTab(QWidget):
    def __init__(self):
        super().__init__()
        self._detected_kind: str | None = None
        self._probe_worker: Worker | None = None
        self._compress_worker: Worker | None = None
        self._build_ui()

    # -- UI construction ---------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)

        # Input file picker
        input_row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Select a video or audio file…")
        self.input_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse_input)
        input_row.addWidget(QLabel("Input file:"))
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(browse_btn)
        root.addLayout(input_row)

        self.detection_label = _hint_label("")
        root.addWidget(self.detection_label)

        # Video checkbox + options
        self.video_checkbox = QCheckBox("Compress video → generates .lvf")
        self.video_checkbox.toggled.connect(self._update_enabled_state)
        root.addWidget(self.video_checkbox)
        self.video_group = self._build_video_group()
        root.addWidget(self.video_group)

        # Audio checkbox + options
        self.audio_checkbox = QCheckBox("Compress audio → generates its own audio file")
        self.audio_checkbox.toggled.connect(self._update_enabled_state)
        root.addWidget(self.audio_checkbox)
        self.audio_group = self._build_audio_group()
        root.addWidget(self.audio_group)

        # Action row
        action_row = QHBoxLayout()
        self.compress_btn = QPushButton("Compress")
        self.compress_btn.setObjectName("primary")
        self.compress_btn.clicked.connect(self._on_compress_clicked)
        action_row.addStretch(1)
        action_row.addWidget(self.compress_btn)
        root.addLayout(action_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        root.addWidget(self.result_label)

        root.addStretch(1)
        self._update_enabled_state()

    def _build_video_group(self) -> QGroupBox:
        group = QGroupBox("Video options")
        form = QFormLayout(group)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(16, 7680)
        self.width_spin.setValue(1920)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(16, 4320)
        self.height_spin.setValue(1080)
        res_row = QHBoxLayout()
        res_row.addWidget(self.width_spin)
        res_row.addWidget(QLabel("x"))
        res_row.addWidget(self.height_spin)
        form.addRow("Resolution:", res_row)

        self.framerate_spin = QDoubleSpinBox()
        self.framerate_spin.setRange(1.0, 240.0)
        self.framerate_spin.setValue(30.0)
        form.addRow("Framerate:", self.framerate_spin)

        self.codec_combo = QComboBox()
        self.codec_combo.addItems(["H.264", "VP9", "AV1"])
        form.addRow("Codec:", self.codec_combo)

        self.bitrate_spin = QSpinBox()
        self.bitrate_spin.setRange(100, 100_000)
        self.bitrate_spin.setValue(4000)
        self.bitrate_spin.setSuffix(" kbps")
        form.addRow("Bitrate:", self.bitrate_spin)

        self.enc_combo = QComboBox()
        self.enc_combo.addItems(["AES-256-CTR", "AES-256-GCM", "None (debug only)"])
        form.addRow("Encryption:", self.enc_combo)

        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("Passphrase or path to a 32-byte key file")
        key_browse = QPushButton("Key file…")
        key_browse.clicked.connect(self._on_browse_key_file)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(key_browse)
        form.addRow("Key:", key_row)

        out_row = QHBoxLayout()
        self.video_output_edit = QLineEdit()
        out_browse = QPushButton("Save as…")
        out_browse.clicked.connect(self._on_browse_video_output)
        out_row.addWidget(self.video_output_edit, 1)
        out_row.addWidget(out_browse)
        form.addRow("Output (.lvf):", out_row)

        return group

    def _build_audio_group(self) -> QGroupBox:
        group = QGroupBox("Audio options")
        layout = QVBoxLayout(group)

        radio_row = QHBoxLayout()
        self.lossless_radio = QRadioButton("Lossless (FLAC)")
        self.lossy_radio = QRadioButton("Lossy (Opus)")
        self.lossless_radio.setChecked(True)
        self.audio_mode_group = QButtonGroup(self)
        self.audio_mode_group.addButton(self.lossless_radio)
        self.audio_mode_group.addButton(self.lossy_radio)
        radio_row.addWidget(self.lossless_radio)
        radio_row.addWidget(self.lossy_radio)
        layout.addLayout(radio_row)

        self.audio_bitrate_spin = QSpinBox()
        self.audio_bitrate_spin.setRange(64, 320)
        self.audio_bitrate_spin.setValue(160)
        self.audio_bitrate_spin.setSuffix(" kbps")
        bitrate_row = QHBoxLayout()
        bitrate_row.addWidget(QLabel("Opus bitrate:"))
        bitrate_row.addWidget(self.audio_bitrate_spin)
        bitrate_row.addStretch(1)
        layout.addLayout(bitrate_row)

        self.audio_warning_label = _hint_label("")
        layout.addWidget(self.audio_warning_label)

        out_row = QHBoxLayout()
        self.audio_output_edit = QLineEdit()
        out_browse = QPushButton("Save as…")
        out_browse.clicked.connect(self._on_browse_audio_output)
        out_row.addWidget(QLabel("Output (.laf):"))
        out_row.addWidget(self.audio_output_edit, 1)
        out_row.addWidget(out_browse)
        layout.addLayout(out_row)

        self.lossless_radio.toggled.connect(self._update_audio_warning)
        self.audio_bitrate_spin.setEnabled(False)
        self._update_audio_warning()

        return group

    # -- Behavior ------------------------------------------------------

    def _update_audio_warning(self):
        if self.lossless_radio.isChecked():
            self.audio_warning_label.setText("Highest quality, lossless, larger file.")
            self.audio_bitrate_spin.setEnabled(False)
        else:
            self.audio_warning_label.setText(
                "Smaller file, lossy compression (usually imperceptible from 128kbps up)."
            )
            self.audio_bitrate_spin.setEnabled(True)

    def _on_browse_input(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select input file", "",
            "Media files (*.mp4 *.mov *.mkv *.avi *.wav *.mp3 *.flac *.ogg *.m4a);;All files (*)",
        )
        if not path:
            return
        self.input_edit.setText(path)
        self.result_label.setText("")
        self._detected_kind = None
        self.detection_label.setText("Detecting input type…")
        self._probe_worker = Worker(ffmpeg_utils.probe_input, path)
        self._probe_worker.succeeded.connect(self._on_probe_done)
        self._probe_worker.failed.connect(self._on_probe_failed)
        self._probe_worker.start()

    def _on_probe_done(self, info):
        if info.has_video:
            self._detected_kind = "video"
            self.detection_label.setText(
                f"Detected: video ({info.video_codec_name}, {info.width}x{info.height}, "
                f"{info.framerate:.2f} fps"
                + (f", + audio track" if info.has_audio else "") + ")"
            )
            self.width_spin.setValue(info.width or 1920)
            self.height_spin.setValue(info.height or 1080)
            if info.framerate:
                self.framerate_spin.setValue(info.framerate)
        elif info.has_audio:
            self._detected_kind = "audio_only"
            self.detection_label.setText(
                "Detected: audio-only input - video compression has been disabled."
            )
        else:
            self._detected_kind = "no_media"
            self.detection_label.setText("This file doesn't look like a usable video or audio file.")
        self._update_enabled_state()

    def _on_probe_failed(self, message: str):
        self._detected_kind = "no_media"
        self.detection_label.setText(f"Couldn't read this file: {message}")
        self._update_enabled_state()

    def _update_enabled_state(self):
        is_audio_only = self._detected_kind == "audio_only"
        is_no_media = self._detected_kind == "no_media"

        # The core rule this whole method exists for: an audio-only input
        # disables video compression entirely, it's not just unchecked.
        self.video_checkbox.setEnabled(not is_audio_only and not is_no_media)
        if is_audio_only and self.video_checkbox.isChecked():
            self.video_checkbox.setChecked(False)
        self.video_group.setVisible(self.video_checkbox.isChecked() and self.video_checkbox.isEnabled())

        self.audio_checkbox.setEnabled(not is_no_media)
        if is_audio_only and not self.audio_checkbox.isChecked():
            self.audio_checkbox.setChecked(True)  # only sensible option left
        self.audio_group.setVisible(self.audio_checkbox.isChecked() and self.audio_checkbox.isEnabled())

    def _on_browse_key_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select key file")
        if path:
            self.key_edit.setText(path)

    def _on_browse_video_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save .lvf as", "", "LVF files (*.lvf)")
        if path:
            self.video_output_edit.setText(path)

    def _on_browse_audio_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save .laf as", "", "LAF files (*.laf)")
        if path:
            self.audio_output_edit.setText(path)

    def _on_compress_clicked(self):
        input_path = self.input_edit.text().strip()
        if not input_path:
            QMessageBox.warning(self, WINDOW_TITLE, "Choose an input file first.")
            return

        do_video = self.video_checkbox.isChecked() and self.video_checkbox.isEnabled()
        do_audio = self.audio_checkbox.isChecked() and self.audio_checkbox.isEnabled()
        if not do_video and not do_audio:
            QMessageBox.warning(self, WINDOW_TITLE, "Enable at least one of video/audio compression.")
            return

        video_opts = None
        if do_video:
            if not self.video_output_edit.text().strip():
                QMessageBox.warning(self, WINDOW_TITLE, "Choose an output path for the .lvf file.")
                return
            enc_index = self.enc_combo.currentIndex()
            enc_mode = [EncryptionMode.AES_256_CTR, EncryptionMode.AES_256_GCM, EncryptionMode.NONE][enc_index]
            if enc_mode != EncryptionMode.NONE and not self.key_edit.text().strip():
                QMessageBox.warning(self, WINDOW_TITLE, "An encryption key is required.")
                return
            codec = [Codec.H264, Codec.VP9, Codec.AV1][self.codec_combo.currentIndex()]
            video_opts = VideoOptions(
                output_path=self.video_output_edit.text().strip(),
                width=self.width_spin.value(), height=self.height_spin.value(),
                framerate=self.framerate_spin.value(), codec=codec,
                bitrate_kbps=self.bitrate_spin.value(),
                key=self.key_edit.text().strip(), enc_mode=enc_mode,
            )

        audio_opts = None
        if do_audio:
            if not self.audio_output_edit.text().strip():
                QMessageBox.warning(self, WINDOW_TITLE, "Choose an output path for the .laf file.")
                return
            mode = "lossless" if self.lossless_radio.isChecked() else "lossy"
            audio_opts = AudioOptions(
                output_path=self.audio_output_edit.text().strip(),
                mode=mode, bitrate_kbps=self.audio_bitrate_spin.value(),
            )

        request = CompressRequest(
            input_path=input_path, do_video=do_video, video_opts=video_opts,
            do_audio=do_audio, audio_opts=audio_opts,
        )

        self._set_busy(True)
        self._compress_worker = Worker(run_compress, request)
        self._compress_worker.succeeded.connect(self._on_compress_done)
        self._compress_worker.failed.connect(self._on_compress_failed)
        self._compress_worker.start()

    def _set_busy(self, busy: bool):
        self.compress_btn.setEnabled(not busy)
        self.progress.setVisible(busy)

    def _on_compress_done(self, result):
        self._set_busy(False)
        lines = []
        for w in result.warnings:
            lines.append(f"⚠ {w}")
        if result.video_written:
            lines.append(f"Video written: {result.video_written}")
        if result.audio_written:
            lines.append(f"Audio written: {result.audio_written}")
        self.result_label.setProperty("role", "success")
        self.result_label.setText("\n".join(lines) or "Done.")
        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)

    def _on_compress_failed(self, message: str):
        self._set_busy(False)
        self.result_label.setProperty("role", "error")
        self.result_label.setText(f"Error: {message}")
        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)


class DecompressTab(QWidget):
    def __init__(self):
        super().__init__()
        self._worker: Worker | None = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        input_row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Select a .lvf or .laf file…")
        self.input_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse_input)
        input_row.addWidget(QLabel("Input file:"))
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(browse_btn)
        root.addLayout(input_row)

        # Single-choice radio buttons, per spec 1.10.
        self.video_radio = QRadioButton("Video → extract to standard .mp4 (no audio)")
        self.audio_radio = QRadioButton("Audio → extract to its original format (codec auto-detected)")
        self.video_radio.setChecked(True)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self.video_radio)
        mode_group.addButton(self.audio_radio)
        self.video_radio.toggled.connect(self._update_visible_group)
        root.addWidget(self.video_radio)
        root.addWidget(self.audio_radio)

        self.video_group = self._build_video_group()
        root.addWidget(self.video_group)
        self.audio_group = self._build_audio_group()
        root.addWidget(self.audio_group)

        action_row = QHBoxLayout()
        self.decompress_btn = QPushButton("Decompress")
        self.decompress_btn.setObjectName("primary")
        self.decompress_btn.clicked.connect(self._on_decompress_clicked)
        action_row.addStretch(1)
        action_row.addWidget(self.decompress_btn)
        root.addLayout(action_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        root.addWidget(self.result_label)

        root.addStretch(1)
        self._update_visible_group()

    def _build_video_group(self) -> QGroupBox:
        group = QGroupBox("Video decompression")
        form = QFormLayout(group)
        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("Passphrase or path to a 32-byte key file")
        key_browse = QPushButton("Key file…")
        key_browse.clicked.connect(lambda: self._browse_key_into(self.key_edit))
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(key_browse)
        form.addRow("Key:", key_row)

        out_row = QHBoxLayout()
        self.video_output_edit = QLineEdit()
        out_browse = QPushButton("Save as…")
        out_browse.clicked.connect(self._on_browse_video_output)
        out_row.addWidget(self.video_output_edit, 1)
        out_row.addWidget(out_browse)
        form.addRow("Output (.mp4):", out_row)
        return group

    def _build_audio_group(self) -> QGroupBox:
        group = QGroupBox("Audio decompression")
        form = QFormLayout(group)

        self.wav_checkbox = QCheckBox("Decode to .wav instead of the original codec")
        form.addRow(self.wav_checkbox)

        out_row = QHBoxLayout()
        self.audio_output_edit = QLineEdit()
        out_browse = QPushButton("Save as…")
        out_browse.clicked.connect(self._on_browse_audio_output)
        out_row.addWidget(self.audio_output_edit, 1)
        out_row.addWidget(out_browse)
        form.addRow("Output:", out_row)
        return group

    def _update_visible_group(self):
        self.video_group.setVisible(self.video_radio.isChecked())
        self.audio_group.setVisible(self.audio_radio.isChecked())

    def _browse_key_into(self, line_edit: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(self, "Select key file")
        if path:
            line_edit.setText(path)

    def _on_browse_input(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select .lvf or .laf file", "", "LVF/LAF files (*.lvf *.laf);;All files (*)"
        )
        if path:
            self.input_edit.setText(path)
            if path.lower().endswith(".laf"):
                self.audio_radio.setChecked(True)
            elif path.lower().endswith(".lvf"):
                self.video_radio.setChecked(True)

    def _on_browse_video_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save .mp4 as", "", "MP4 files (*.mp4)")
        if path:
            self.video_output_edit.setText(path)

    def _on_browse_audio_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save audio as")
        if path:
            self.audio_output_edit.setText(path)

    def _on_decompress_clicked(self):
        input_path = self.input_edit.text().strip()
        if not input_path:
            QMessageBox.warning(self, WINDOW_TITLE, "Choose an input file first.")
            return

        self._set_busy(True)
        if self.video_radio.isChecked():
            output = self.video_output_edit.text().strip()
            if not output:
                self._set_busy(False)
                QMessageBox.warning(self, WINDOW_TITLE, "Choose an output .mp4 path.")
                return
            self._worker = Worker(decompress_video, input_path, output,
                                   key_or_path=self.key_edit.text().strip() or None)
        else:
            output = self.audio_output_edit.text().strip()
            if not output:
                self._set_busy(False)
                QMessageBox.warning(self, WINDOW_TITLE, "Choose an output path.")
                return
            self._worker = Worker(decompress_audio, input_path, output,
                                   decode_to_wav=self.wav_checkbox.isChecked())

        self._worker.succeeded.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _set_busy(self, busy: bool):
        self.decompress_btn.setEnabled(not busy)
        self.progress.setVisible(busy)

    def _on_done(self, result):
        self._set_busy(False)
        path = result if isinstance(result, str) else (
            self.video_output_edit.text() if self.video_radio.isChecked() else self.audio_output_edit.text()
        )
        self.result_label.setProperty("role", "success")
        self.result_label.setText(f"Done: {path}")
        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)

    def _on_failed(self, message: str):
        self._set_busy(False)
        self.result_label.setProperty("role", "error")
        self.result_label.setText(f"Error: {message}")
        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(720, 640)

        tabs = QTabWidget()
        tabs.addTab(CompressTab(), "Compress")
        tabs.addTab(DecompressTab(), "Decompress")
        self.setCentralWidget(tabs)


def run_gui() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(WINDOW_TITLE)
    apply_dark_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run_gui())
