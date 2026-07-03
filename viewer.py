#!/usr/bin/env python3
"""
DCAM Recording Viewer
Plays BigTIFF recordings from Hamamatsu ORCA-Quest camera.

Loads a single <stem>.tif written by WriterThread.
Session metadata (ROI, FPS, exposure, …) is stored as JSON in page 0's
ImageDescription.  Every page also carries its own timestamp and frame_index
in its ImageDescription JSON.

Frames are read on-demand via tifffile page access (random seek, no full
load into RAM).
"""

from __future__ import annotations
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import tifffile
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QDoubleSpinBox, QComboBox, QFileDialog,
    QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

# ---------------------------------------------------------------------------
# Speed table — tuned for recordings at ~1000 fps
# ---------------------------------------------------------------------------
SPEED_OPTIONS: list[tuple[str, float]] = [
    ("0.001×",  0.001),
    ("0.005×",  0.005),
    ("0.01×",   0.01),
    ("0.025×",  0.025),
    ("0.05×",   0.05),
    ("0.1×",    0.1),
    ("0.25×",   0.25),
    ("0.5×",    0.5),
    ("1×",      1.0),
    ("2×",      2.0),
    ("5×",      5.0),
    ("10×",     10.0),
    ("25×",     25.0),
    ("50×",     50.0),
]
DEFAULT_SPEED_IDX = 8       # "1×"
TIMER_INTERVAL_MS = 16      # ≈ 60 Hz display refresh


# ---------------------------------------------------------------------------
# Image display widget — scales with window while preserving aspect ratio
# ---------------------------------------------------------------------------
class ImageWidget(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background: #0d0d0d;")
        self._pm: QPixmap | None = None

    def load_pixmap(self, pm: QPixmap) -> None:
        self._pm = pm
        self._rescale()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._pm is not None:
            scaled = self._pm.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            super().setPixmap(scaled)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class RecordingViewer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DCAM Recording Viewer")
        self.resize(1280, 820)

        # Recording data (set by _load_file)
        self._tif: tifffile.TiffFile | None = None
        self._pages: list = []  # filtered non-empty pages
        self.timestamps: np.ndarray | None = None   # float64, one per page
        self.frame_indices: np.ndarray | None = None # int32, one per page
        self.meta: dict = {}
        self.actual: dict = {}
        self.n_frames: int = 0
        self.rec_fps: float = 1000.0

        # Display levels
        self._lo: float = 0.0
        self._hi: float = 65535.0

        # Playback state
        self.current_frame: int = 0
        self._accum: float = 0.0
        self.playing: bool = False
        self.speed: float = 1.0

        self._timer = QTimer(self)
        self._timer.setInterval(TIMER_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root_widget = QWidget()
        self.setCentralWidget(root_widget)
        root = QVBoxLayout(root_widget)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # ── File row ──────────────────────────────────────────────────
        file_row = QHBoxLayout()
        self._open_btn = QPushButton("Open .tif file…")
        self._open_btn.setFixedHeight(30)
        self._open_btn.clicked.connect(self._open_dialog)
        self._file_lbl = QLabel("No file loaded")
        self._file_lbl.setStyleSheet("color: #888; font-size: 12px;")
        file_row.addWidget(self._open_btn)
        file_row.addWidget(self._file_lbl, 1)
        root.addLayout(file_row)

        # ── Image ─────────────────────────────────────────────────────
        self._img = ImageWidget()
        root.addWidget(self._img, 1)

        # ── Display controls row ──────────────────────────────────────
        ctrl_row = QHBoxLayout()

        ctrl_row.addWidget(QLabel("Gamma:"))
        self._gamma = QDoubleSpinBox()
        self._gamma.setRange(0.1, 8.0)
        self._gamma.setSingleStep(0.1)
        self._gamma.setValue(1.0)
        self._gamma.setFixedWidth(68)
        self._gamma.setToolTip("Gamma correction (display only).\n"
                                "< 1 brightens shadows, > 1 compresses highlights.")
        self._gamma.valueChanged.connect(self._redraw)
        ctrl_row.addWidget(self._gamma)

        self._auto_btn = QPushButton("Auto")
        self._auto_btn.setFixedWidth(46)
        self._auto_btn.setToolTip(
            "Auto-contrast: set display range to 1st–99th percentile of current frame")
        self._auto_btn.clicked.connect(self._auto_levels)
        ctrl_row.addWidget(self._auto_btn)

        ctrl_row.addStretch()
        root.addLayout(ctrl_row)

        # ── Slider ────────────────────────────────────────────────────
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(0)
        self._slider.valueChanged.connect(self._on_slider)
        root.addWidget(self._slider)

        # ── Bottom bar ────────────────────────────────────────────────
        bot = QHBoxLayout()

        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedSize(36, 30)
        self._prev_btn.setEnabled(False)
        self._prev_btn.clicked.connect(self._prev_frame)
        bot.addWidget(self._prev_btn)

        self._play_btn = QPushButton("▶  Play")
        self._play_btn.setFixedSize(88, 30)
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._toggle_play)
        bot.addWidget(self._play_btn)

        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedSize(36, 30)
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self._next_frame)
        bot.addWidget(self._next_btn)

        bot.addSpacing(8)
        bot.addWidget(QLabel("Speed:"))
        self._speed_box = QComboBox()
        for lbl, _ in SPEED_OPTIONS:
            self._speed_box.addItem(lbl)
        self._speed_box.setCurrentIndex(DEFAULT_SPEED_IDX)
        self._speed_box.setFixedWidth(78)
        self._speed_box.currentIndexChanged.connect(self._on_speed_change)
        bot.addWidget(self._speed_box)

        bot.addStretch()

        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        mono.setPointSize(10)

        self._fps_lbl   = QLabel("— fps")
        self._frame_lbl = QLabel("Frame: —")
        self._time_lbl  = QLabel("Time: —")
        for lbl in (self._fps_lbl, self._frame_lbl, self._time_lbl):
            lbl.setFont(mono)

        bot.addWidget(self._fps_lbl)
        bot.addSpacing(16)
        bot.addWidget(self._frame_lbl)
        bot.addSpacing(16)
        bot.addWidget(self._time_lbl)

        root.addLayout(bot)

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------
    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open TIFF recording", "",
            "TIFF recordings (*.tif *.tiff);;All files (*)",
        )
        if path:
            self._load_file(Path(path))

    def _load_file(self, path: Path) -> None:
        self._pause()

        # Close previously open file
        if self._tif is not None:
            self._tif.close()
            self._tif = None
        self._pages = []

        try:
            tif = tifffile.TiffFile(str(path))
        except Exception as exc:
            self._file_lbl.setText(f"Failed to open: {exc}")
            return

        all_pages = tif.pages
        # Filter out empty pages (written by buggy writer with un-binned sub_h)
        pages = [p for p in all_pages if p.size > 0]
        n_frames = len(pages)
        if n_frames == 0:
            self._file_lbl.setText("File contains no pages.")
            tif.close()
            return

        # ── Session metadata from page 0 ImageDescription ─────────────
        meta: dict = {}
        try:
            desc0 = pages[0].description
            if desc0:
                meta = json.loads(desc0)
        except Exception:
            pass

        # New files have nested "actual" / "requested"; old files are flat.
        actual    = meta.get("actual", {})
        requested = meta.get("requested", meta)   # fall back to flat meta for old files

        rec_fps = float(actual.get("fps") or requested.get("fps_target") or 1000.0)
        roi = requested.get("roi", {})
        w = int(actual.get("image_width",  roi.get("width",  pages[0].shape[1] if pages[0].ndim >= 2 else 0)))
        h = int(actual.get("image_height", roi.get("height", pages[0].shape[0] if pages[0].ndim >= 1 else 0)))

        # ── Per-page timestamps and frame indices ──────────────────────
        # Parse all page ImageDescriptions up-front (IFDs already loaded by tifffile).
        timestamps   = np.zeros(n_frames, dtype=np.float64)
        frame_indices = np.zeros(n_frames, dtype=np.int32)

        sub_period = 1.0 / rec_fps if rec_fps > 0 else 0.0
        for i, page in enumerate(pages):
            try:
                d = json.loads(page.description)
                bundle_ts        = float(d.get("timestamp",    0.0))
                frame_num        = int(  d.get("frame_number", i))
                sub_idx          = frame_num % max(1, frame_bundle)
                # Interpolate sub-frame time within the bundle.
                timestamps[i]    = bundle_ts + sub_idx * sub_period
                frame_indices[i] = int(d.get("frame_index", i))
            except Exception:
                timestamps[i]    = 0.0
                frame_indices[i] = i

        # ── Commit state ───────────────────────────────────────────────
        self._tif          = tif
        self._pages        = pages
        self.meta          = meta
        self.actual        = actual
        self.rec_fps       = rec_fps
        self.timestamps    = timestamps
        self.frame_indices = frame_indices
        self.n_frames      = n_frames
        self.current_frame = 0
        self._accum        = 0.0

        self._slider.setMaximum(n_frames - 1)
        self._slider.setValue(0)
        self._prev_btn.setEnabled(True)
        self._play_btn.setEnabled(True)
        self._next_btn.setEnabled(True)

        exposure_us  = actual.get("exposure_us",  requested.get("exposure_us", "?"))
        readout_us   = actual.get("readout_us")
        binning      = actual.get("binning",      requested.get("binning", 1))
        frame_bundle = actual.get("frame_bundle", requested.get("frame_bundle", 1))

        info_parts = [
            f"{path.name}",
            f"{n_frames} frames",
            f"{w}×{h} px",
            f"{rec_fps:.1f} fps",
            f"{exposure_us:.1f} µs exp" if isinstance(exposure_us, (int, float)) else f"{exposure_us} µs exp",
        ]
        if readout_us is not None:
            info_parts.append(f"{readout_us:.1f} µs readout")
        if binning and binning != 1:
            info_parts.append(f"{binning}×{binning} bin")
        if frame_bundle and frame_bundle > 1:
            info_parts.append(f"bundle {frame_bundle}")
        self._file_lbl.setText("   ".join(info_parts))

        self._update_fps_label()
        self._auto_levels()

    # ------------------------------------------------------------------
    # Playback control
    # ------------------------------------------------------------------
    def _prev_frame(self) -> None:
        if self._tif is None:
            return
        self._pause()
        self._slider.setValue(max(0, self.current_frame - 1))

    def _next_frame(self) -> None:
        if self._tif is None:
            return
        self._pause()
        self._slider.setValue(min(self.n_frames - 1, self.current_frame + 1))

    def _toggle_play(self) -> None:
        if self.playing:
            self._pause()
        else:
            self._play()

    def _play(self) -> None:
        if self._tif is None:
            return
        self.playing = True
        self._play_btn.setText("⏸  Pause")
        self._accum = float(self.current_frame)
        self._timer.start()

    def _pause(self) -> None:
        self.playing = False
        self._timer.stop()
        self._play_btn.setText("▶  Play")

    def _on_tick(self) -> None:
        frames_per_tick = self.speed * self.rec_fps * (TIMER_INTERVAL_MS / 1000.0)
        self._accum += frames_per_tick
        idx = int(self._accum)
        if idx >= self.n_frames:
            idx = self.n_frames - 1
            self._accum = float(idx)
            self._pause()
        self.current_frame = idx
        self._slider.blockSignals(True)
        self._slider.setValue(idx)
        self._slider.blockSignals(False)
        self._show_frame(idx)

    def _on_slider(self, value: int) -> None:
        self.current_frame = value
        self._accum = float(value)
        self._show_frame(value)

    def _on_speed_change(self, idx: int) -> None:
        self.speed = SPEED_OPTIONS[idx][1]
        self._update_fps_label()

    def _auto_levels(self) -> None:
        if self._tif is None:
            return
        raw = self._pages[self.current_frame].asarray()
        if raw.ndim == 3:
            raw = raw.reshape(-1, raw.shape[-1])
        frame = raw
        self._lo = float(np.percentile(frame, 1))
        self._hi = float(np.percentile(frame, 99))
        self._redraw()

    def _update_fps_label(self) -> None:
        if self._tif is None:
            return
        self._fps_lbl.setText(f"{self.speed * self.rec_fps:.1f} fps")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _redraw(self) -> None:
        if self._tif is not None:
            self._show_frame(self.current_frame)

    def _show_frame(self, idx: int) -> None:
        if self._tif is None:
            return

        raw = self._pages[idx].asarray()
        if raw.ndim == 3:
            raw = raw.reshape(-1, raw.shape[-1])
        elif raw.ndim != 2:
            return
        frame = raw.astype(np.float32)

        lo  = self._lo
        hi  = self._hi if self._hi > self._lo else self._lo + 1.0
        frame = (frame - lo) / (hi - lo)
        np.clip(frame, 0.0, 1.0, out=frame)

        gamma = self._gamma.value()
        if abs(gamma - 1.0) > 1e-3:
            np.power(frame, 1.0 / gamma, out=frame)

        img8 = np.ascontiguousarray((frame * 255.0).astype(np.uint8))
        h, w = img8.shape
        qimg = QImage(img8.data, w, h, img8.strides[0], QImage.Format.Format_Grayscale8)
        self._img.load_pixmap(QPixmap.fromImage(qimg))

        # ── Status labels ──────────────────────────────────────────────
        cam_idx = int(self.frame_indices[idx]) if self.frame_indices is not None else idx
        self._frame_lbl.setText(
            f"Frame {idx + 1:>6} / {self.n_frames}   [cam #{cam_idx}]"
        )

        if self.timestamps is not None and self.timestamps[0] != 0.0:
            t  = self.timestamps[idx]
            t0 = self.timestamps[0]
            elapsed_ms = (t - t0) * 1000.0
            wall = datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-3]
            self._time_lbl.setText(f"T + {elapsed_ms:>10.3f} ms   ({wall})")
        else:
            elapsed_ms = idx / self.rec_fps * 1000.0
            self._time_lbl.setText(f"T + {elapsed_ms:>10.3f} ms")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._pause()
        if self._tif is not None:
            self._tif.close()
            self._tif = None
        self._pages = []
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = RecordingViewer()
    win.show()
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.suffix in (".tif", ".tiff") and p.exists():
            win._load_file(p)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
