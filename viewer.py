#!/usr/bin/env python3
"""
DCAM Recording Viewer
Plays BigTIFF recordings from Hamamatsu ORCA-Quest camera.

Loads a single <stem>.tif written by WriterThread.
Session metadata (ROI, FPS, exposure, ...) is stored as JSON in page 0's
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
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt6.QtGui import (
    QFont, QImage, QPixmap, QPainter, QPen, QColor, QBrush,
)
from PyQt6.QtWidgets import (
    QApplication, QDoubleSpinBox, QComboBox, QFileDialog,
    QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# ---------------------------------------------------------------------------
# Speed table -- tuned for recordings at ~1000 fps
# ---------------------------------------------------------------------------
SPEED_OPTIONS: list[tuple[str, float]] = [
    ("0.001x",  0.001),
    ("0.005x",  0.005),
    ("0.01x",   0.01),
    ("0.025x",  0.025),
    ("0.05x",   0.05),
    ("0.1x",    0.1),
    ("0.25x",   0.25),
    ("0.5x",    0.5),
    ("1x",      1.0),
    ("2x",      2.0),
    ("5x",      5.0),
    ("10x",     10.0),
    ("25x",     25.0),
    ("50x",     50.0),
]
DEFAULT_SPEED_IDX = 8       # "1x"
TIMER_INTERVAL_MS = 16      # ~ 60 Hz display refresh


# ---------------------------------------------------------------------------
# Image display widget -- scales with window while preserving aspect ratio
# ---------------------------------------------------------------------------
class ImageWidget(QLabel):
    def __init__(self, viewer: "RecordingViewer | None" = None) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background: #0d0d0d;")
        self._pm: QPixmap | None = None
        self._scaled_pm: QPixmap | None = None
        self._viewer = viewer

        # Coordinate mapping: set after each rescale
        self._img_offset_x: float = 0.0  # px offset of image within widget
        self._img_offset_y: float = 0.0
        self._img_scale: float = 1.0      # widget px per image px
        self._img_w: int = 0
        self._img_h: int = 0

        # Zoom & pan state
        self._zoom: float = 1.0           # 1.0 = fit-to-widget
        self._pan_x: float = 0.0          # pan offset in widget pixels
        self._pan_y: float = 0.0
        self._panning: bool = False
        self._pan_start: QPointF | None = None
        self._pan_start_offset: tuple[float, float] = (0.0, 0.0)

        # Measurement overlay
        self._measure_pts: list[QPointF] = []  # image-space points (0 or 2)
        self._measure_dist_um: float | None = None

        # ROI overlay
        self._roi_rect: QRectF | None = None   # image-space rect
        self._roi_drag_start: QPointF | None = None  # widget-space

        # Mouse tracking for overlays
        self.setMouseTracking(True)

    def load_pixmap(self, pm: QPixmap) -> None:
        self._pm = pm
        self._img_w = pm.width()
        self._img_h = pm.height()
        self._rescale()

    def reset_zoom(self) -> None:
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._rescale()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._pm is None:
            return
        ww, wh = self.width(), self.height()
        # Base scale: fit image to widget preserving aspect ratio
        base_sx = ww / self._img_w if self._img_w > 0 else 1.0
        base_sy = wh / self._img_h if self._img_h > 0 else 1.0
        base_scale = min(base_sx, base_sy)
        self._img_scale = base_scale * self._zoom

        sw = int(self._img_w * self._img_scale)
        sh = int(self._img_h * self._img_scale)

        # Centre + pan offset
        self._img_offset_x = (ww - sw) / 2.0 + self._pan_x
        self._img_offset_y = (wh - sh) / 2.0 + self._pan_y

        scaled = self._pm.scaled(
            sw, sh,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # We draw everything ourselves in paintEvent, clear the QLabel pixmap
        super().setPixmap(QPixmap())  # clear base label
        self._scaled_pm = scaled
        self.update()

    # -- Coordinate helpers --------------------------------------------------
    def widget_to_image(self, wx: float, wy: float) -> QPointF | None:
        """Convert widget coords to image pixel coords. Returns None if outside."""
        ix = (wx - self._img_offset_x) / self._img_scale
        iy = (wy - self._img_offset_y) / self._img_scale
        if 0 <= ix < self._img_w and 0 <= iy < self._img_h:
            return QPointF(ix, iy)
        return None

    def image_to_widget(self, pt: QPointF) -> QPointF:
        """Convert image pixel coords to widget coords."""
        return QPointF(
            pt.x() * self._img_scale + self._img_offset_x,
            pt.y() * self._img_scale + self._img_offset_y,
        )

    # -- Wheel event: scroll = zoom centred on cursor -------------------------
    def wheelEvent(self, event) -> None:  # type: ignore[override]
        delta = event.angleDelta().y()
        steps = delta / 120.0

        mx, my = event.position().x(), event.position().y()
        # Image coord under cursor before zoom
        ix = (mx - self._img_offset_x) / self._img_scale
        iy = (my - self._img_offset_y) / self._img_scale

        factor = 1.15 ** steps
        self._zoom = max(0.1, min(50.0, self._zoom * factor))

        # Compute new scale and set pan so same image pixel stays under cursor
        ww, wh = self.width(), self.height()
        base_sx = ww / self._img_w if self._img_w > 0 else 1.0
        base_sy = wh / self._img_h if self._img_h > 0 else 1.0
        new_scale = min(base_sx, base_sy) * self._zoom
        self._pan_x = mx - ix * new_scale - (ww - self._img_w * new_scale) / 2.0
        self._pan_y = my - iy * new_scale - (wh - self._img_h * new_scale) / 2.0
        self._rescale()
        event.accept()

    # -- Mouse events --------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        pos = event.position()

        # Right-click or middle-click: start panning
        if event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._panning = True
            self._pan_start = QPointF(pos.x(), pos.y())
            self._pan_start_offset = (self._pan_x, self._pan_y)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if self._viewer is None:
            return
        img_pt = self.widget_to_image(pos.x(), pos.y())

        # Measurement mode
        if self._viewer._measure_active and img_pt is not None:
            if len(self._measure_pts) < 2:
                self._measure_pts.append(img_pt)
            else:
                # Start fresh measurement
                self._measure_pts = [img_pt]
                self._measure_dist_um = None
            if len(self._measure_pts) == 2:
                res = self._viewer._resolution.value()
                dx = self._measure_pts[1].x() - self._measure_pts[0].x()
                dy = self._measure_pts[1].y() - self._measure_pts[0].y()
                dist_px = (dx * dx + dy * dy) ** 0.5
                self._measure_dist_um = dist_px * res
            self.update()
            return

        # ROI mode
        if self._viewer._roi_active and img_pt is not None:
            self._roi_drag_start = QPointF(pos.x(), pos.y())
            self._roi_rect = None
            self.update()
            return

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        pos = event.position()

        # Panning
        if self._panning and self._pan_start is not None:
            dx = pos.x() - self._pan_start.x()
            dy = pos.y() - self._pan_start.y()
            self._pan_x = self._pan_start_offset[0] + dx
            self._pan_y = self._pan_start_offset[1] + dy
            self._rescale()
            return

        if self._viewer is None:
            return
        # ROI drag
        if self._viewer._roi_active and self._roi_drag_start is not None:
            start_img = self.widget_to_image(
                self._roi_drag_start.x(), self._roi_drag_start.y()
            )
            end_img = self.widget_to_image(pos.x(), pos.y())
            if start_img is not None and end_img is not None:
                x0 = min(start_img.x(), end_img.x())
                y0 = min(start_img.y(), end_img.y())
                x1 = max(start_img.x(), end_img.x())
                y1 = max(start_img.y(), end_img.y())
                self._roi_rect = QRectF(x0, y0, x1 - x0, y1 - y0)
                self.update()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._panning:
            self._panning = False
            self._pan_start = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self._viewer is None:
            return
        if self._viewer._roi_active and self._roi_drag_start is not None:
            self._roi_drag_start = None
            if self._roi_rect is not None:
                self._viewer._compute_region_activity()

    # -- Paint overlays ------------------------------------------------------
    def paintEvent(self, event) -> None:  # type: ignore[override]
        # Fill background
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0d0d0d"))

        # Draw zoomed/panned image
        if hasattr(self, "_scaled_pm") and self._scaled_pm is not None:
            painter.drawPixmap(
                int(self._img_offset_x), int(self._img_offset_y), self._scaled_pm
            )

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw measurement line
        if len(self._measure_pts) == 2:
            p1 = self.image_to_widget(self._measure_pts[0])
            p2 = self.image_to_widget(self._measure_pts[1])
            pen = QPen(QColor(0, 255, 100), 2)
            painter.setPen(pen)
            painter.drawLine(p1, p2)
            # Draw endpoints
            painter.setBrush(QBrush(QColor(0, 255, 100)))
            painter.drawEllipse(p1, 4, 4)
            painter.drawEllipse(p2, 4, 4)
            # Draw distance label
            if self._measure_dist_um is not None:
                mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2 - 10)
                painter.setPen(QPen(QColor(255, 255, 255)))
                font = QFont("Monospace", 10)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(mid, f"{self._measure_dist_um:.2f} um")
        elif len(self._measure_pts) == 1:
            p1 = self.image_to_widget(self._measure_pts[0])
            painter.setBrush(QBrush(QColor(0, 255, 100)))
            painter.setPen(QPen(QColor(0, 255, 100), 2))
            painter.drawEllipse(p1, 4, 4)

        # Draw ROI rectangle
        if self._roi_rect is not None:
            tl = self.image_to_widget(QPointF(self._roi_rect.x(), self._roi_rect.y()))
            br = self.image_to_widget(
                QPointF(
                    self._roi_rect.x() + self._roi_rect.width(),
                    self._roi_rect.y() + self._roi_rect.height(),
                )
            )
            pen = QPen(QColor(255, 200, 0), 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor(255, 200, 0, 40)))
            painter.drawRect(QRectF(tl, br))

        painter.end()


# ---------------------------------------------------------------------------
# Mean image popup window
# ---------------------------------------------------------------------------
class MeanImageWindow(QMainWindow):
    def __init__(self, img_f32: np.ndarray, title: str = "Mean Image") -> None:
        super().__init__()
        self.setWindowTitle(title)
        self.resize(800, 650)
        self._raw = img_f32  # float32 mean image

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(6, 6, 6, 6)

        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._img_label.setStyleSheet("background: #0d0d0d;")
        layout.addWidget(self._img_label, 1)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Gamma:"))
        self._gamma = QDoubleSpinBox()
        self._gamma.setRange(0.1, 8.0)
        self._gamma.setSingleStep(0.1)
        self._gamma.setValue(1.0)
        self._gamma.setFixedWidth(68)
        self._gamma.valueChanged.connect(self._redraw)
        ctrl.addWidget(self._gamma)

        self._auto_btn = QPushButton("Auto")
        self._auto_btn.setFixedWidth(46)
        self._auto_btn.clicked.connect(self._auto_levels)
        ctrl.addWidget(self._auto_btn)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        self._lo = float(np.percentile(self._raw, 1))
        self._hi = float(np.percentile(self._raw, 99))
        self._redraw()

    def _auto_levels(self) -> None:
        self._lo = float(np.percentile(self._raw, 1))
        self._hi = float(np.percentile(self._raw, 99))
        self._redraw()

    def _redraw(self) -> None:
        frame = self._raw.astype(np.float32)
        lo, hi = self._lo, self._hi
        if hi <= lo:
            hi = lo + 1.0
        frame = (frame - lo) / (hi - lo)
        np.clip(frame, 0.0, 1.0, out=frame)
        gamma = self._gamma.value()
        if abs(gamma - 1.0) > 1e-3:
            np.power(frame, 1.0 / gamma, out=frame)
        img8 = np.ascontiguousarray((frame * 255.0).astype(np.uint8))
        h, w = img8.shape
        qimg = QImage(img8.data, w, h, img8.strides[0], QImage.Format.Format_Grayscale8)
        pm = QPixmap.fromImage(qimg)
        scaled = pm.scaled(
            self._img_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._img_label.setPixmap(scaled)


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

        # Folder mode
        self._folder_files: list[Path] = []
        self._folder_idx: int = 0

        # Display levels
        self._lo: float = 0.0
        self._hi: float = 65535.0

        # Playback state
        self.current_frame: int = 0
        self._accum: float = 0.0
        self.playing: bool = False
        self.speed: float = 1.0

        # Tool modes
        self._measure_active: bool = False
        self._roi_active: bool = False

        # Keep popup windows alive
        self._popup_windows: list[QMainWindow] = []

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

        # -- File row -------------------------------------------------------
        file_row = QHBoxLayout()
        self._open_btn = QPushButton("Open .tif file...")
        self._open_btn.setFixedHeight(30)
        self._open_btn.clicked.connect(self._open_dialog)

        self._open_folder_btn = QPushButton("Open folder...")
        self._open_folder_btn.setFixedHeight(30)
        self._open_folder_btn.clicked.connect(self._open_folder_dialog)

        self._file_lbl = QLabel("No file loaded")
        self._file_lbl.setStyleSheet("color: #888; font-size: 12px;")
        file_row.addWidget(self._open_btn)
        file_row.addWidget(self._open_folder_btn)
        file_row.addWidget(self._file_lbl, 1)
        root.addLayout(file_row)

        # -- Folder navigation bar (hidden by default) ----------------------
        self._folder_bar = QHBoxLayout()
        self._prev_file_btn = QPushButton("\u25c0 Prev File")
        self._prev_file_btn.setFixedHeight(28)
        self._prev_file_btn.clicked.connect(self._prev_file)
        self._next_file_btn = QPushButton("Next File \u25b6")
        self._next_file_btn.setFixedHeight(28)
        self._next_file_btn.clicked.connect(self._next_file)
        self._folder_lbl = QLabel("")
        self._folder_lbl.setStyleSheet("color: #aaa; font-size: 12px;")
        self._mean_btn = QPushButton("Generate Mean Image")
        self._mean_btn.setFixedHeight(28)
        self._mean_btn.clicked.connect(self._generate_mean_image)

        self._folder_bar.addWidget(self._prev_file_btn)
        self._folder_bar.addWidget(self._folder_lbl)
        self._folder_bar.addWidget(self._next_file_btn)
        self._folder_bar.addSpacing(16)
        self._folder_bar.addWidget(self._mean_btn)
        self._folder_bar.addStretch()

        self._folder_bar_widget = QWidget()
        self._folder_bar_widget.setLayout(self._folder_bar)
        self._folder_bar_widget.setVisible(False)
        root.addWidget(self._folder_bar_widget)

        # -- Image -----------------------------------------------------------
        self._img = ImageWidget(viewer=self)
        root.addWidget(self._img, 1)

        # -- Display controls row -------------------------------------------
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
            "Auto-contrast: set display range to 1st-99th percentile of current frame")
        self._auto_btn.clicked.connect(self._auto_levels)
        ctrl_row.addWidget(self._auto_btn)

        ctrl_row.addSpacing(16)

        # -- Measurement controls -------------------------------------------
        ctrl_row.addWidget(QLabel("Res (um/px):"))
        self._resolution = QDoubleSpinBox()
        self._resolution.setRange(0.001, 1000.0)
        self._resolution.setSingleStep(0.01)
        self._resolution.setValue(1.0)
        self._resolution.setDecimals(3)
        self._resolution.setFixedWidth(80)
        self._resolution.setToolTip("Pixel size in micrometers for distance measurement")
        ctrl_row.addWidget(self._resolution)

        self._measure_btn = QPushButton("Measure")
        self._measure_btn.setCheckable(True)
        self._measure_btn.setFixedWidth(72)
        self._measure_btn.setToolTip(
            "Toggle measurement mode: click two points on the image to measure distance"
        )
        self._measure_btn.toggled.connect(self._on_measure_toggled)
        ctrl_row.addWidget(self._measure_btn)

        # -- ROI activity controls ------------------------------------------
        self._roi_btn = QPushButton("Select Region")
        self._roi_btn.setCheckable(True)
        self._roi_btn.setFixedWidth(100)
        self._roi_btn.setToolTip(
            "Toggle region selection: click-drag a rectangle to plot mean intensity vs frame"
        )
        self._roi_btn.toggled.connect(self._on_roi_toggled)
        ctrl_row.addWidget(self._roi_btn)

        ctrl_row.addSpacing(16)

        self._reset_zoom_btn = QPushButton("Reset Zoom")
        self._reset_zoom_btn.setFixedWidth(88)
        self._reset_zoom_btn.setToolTip("Reset zoom to fit (scroll to zoom, right-drag to pan)")
        self._reset_zoom_btn.clicked.connect(lambda: self._img.reset_zoom())
        ctrl_row.addWidget(self._reset_zoom_btn)

        ctrl_row.addStretch()
        root.addLayout(ctrl_row)

        # -- Slider ----------------------------------------------------------
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(0)
        self._slider.valueChanged.connect(self._on_slider)
        root.addWidget(self._slider)

        # -- Bottom bar ------------------------------------------------------
        bot = QHBoxLayout()

        self._prev_btn = QPushButton("\u25c0")
        self._prev_btn.setFixedSize(36, 30)
        self._prev_btn.setEnabled(False)
        self._prev_btn.clicked.connect(self._prev_frame)
        bot.addWidget(self._prev_btn)

        self._play_btn = QPushButton("\u25b6  Play")
        self._play_btn.setFixedSize(88, 30)
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._toggle_play)
        bot.addWidget(self._play_btn)

        self._next_btn = QPushButton("\u25b6")
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

        self._fps_lbl   = QLabel("-- fps")
        self._frame_lbl = QLabel("Frame: --")
        self._time_lbl  = QLabel("Time: --")
        for lbl in (self._fps_lbl, self._frame_lbl, self._time_lbl):
            lbl.setFont(mono)

        bot.addWidget(self._fps_lbl)
        bot.addSpacing(16)
        bot.addWidget(self._frame_lbl)
        bot.addSpacing(16)
        bot.addWidget(self._time_lbl)

        root.addLayout(bot)

        # -- Embedded activity plot (hidden until ROI computed) ---------------
        self._plot_widget = QWidget()
        plot_layout = QVBoxLayout(self._plot_widget)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setSpacing(2)

        plot_header = QHBoxLayout()
        self._plot_title = QLabel("ROI Activity")
        self._plot_title.setStyleSheet("color: #ccc; font-weight: bold; font-size: 12px;")
        plot_header.addWidget(self._plot_title)
        plot_header.addStretch()
        self._plot_save_btn = QPushButton("Save Plot")
        self._plot_save_btn.setFixedHeight(22)
        self._plot_save_btn.clicked.connect(self._save_plot)
        plot_header.addWidget(self._plot_save_btn)
        self._plot_close_btn = QPushButton("Close Plot")
        self._plot_close_btn.setFixedHeight(22)
        self._plot_close_btn.clicked.connect(lambda: self._plot_widget.setVisible(False))
        plot_header.addWidget(self._plot_close_btn)
        plot_layout.addLayout(plot_header)

        self._plot_figure = Figure(figsize=(9, 2.5), dpi=100)
        self._plot_figure.set_facecolor("#1a1a1a")
        self._plot_canvas = FigureCanvas(self._plot_figure)
        self._plot_canvas.setMinimumHeight(160)
        self._plot_canvas.setMaximumHeight(250)
        plot_layout.addWidget(self._plot_canvas)

        self._plot_widget.setVisible(False)
        root.addWidget(self._plot_widget)

    # ------------------------------------------------------------------
    # Tool mode toggles
    # ------------------------------------------------------------------
    def _on_measure_toggled(self, checked: bool) -> None:
        self._measure_active = checked
        if checked:
            # Deactivate ROI mode
            self._roi_btn.setChecked(False)
            self._img._measure_pts.clear()
            self._img._measure_dist_um = None
            self._img.update()
        else:
            self._img._measure_pts.clear()
            self._img._measure_dist_um = None
            self._img.update()

    def _on_roi_toggled(self, checked: bool) -> None:
        self._roi_active = checked
        if checked:
            # Deactivate measure mode
            self._measure_btn.setChecked(False)
            self._img._roi_rect = None
            self._img.update()
        else:
            self._img._roi_rect = None
            self._img.update()

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------
    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open TIFF recording", "",
            "TIFF recordings (*.tif *.tiff);;All files (*)",
        )
        if path:
            self._folder_files = []
            self._folder_idx = 0
            self._folder_bar_widget.setVisible(False)
            self._load_file(Path(path))

    def _open_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open folder with TIFFs", "")
        if not folder:
            return
        folder_path = Path(folder)
        tif_files = sorted(
            [f for f in folder_path.iterdir()
             if f.suffix.lower() in (".tif", ".tiff") and f.is_file()]
        )
        if not tif_files:
            self._file_lbl.setText("No .tif/.tiff files found in folder")
            return
        self._folder_files = tif_files
        self._folder_idx = 0
        self._folder_bar_widget.setVisible(True)
        self._update_folder_label()
        self._load_file(tif_files[0])

    def _prev_file(self) -> None:
        if not self._folder_files:
            return
        self._folder_idx = max(0, self._folder_idx - 1)
        self._update_folder_label()
        self._load_file(self._folder_files[self._folder_idx])

    def _next_file(self) -> None:
        if not self._folder_files:
            return
        self._folder_idx = min(len(self._folder_files) - 1, self._folder_idx + 1)
        self._update_folder_label()
        self._load_file(self._folder_files[self._folder_idx])

    def _update_folder_label(self) -> None:
        n = len(self._folder_files)
        name = self._folder_files[self._folder_idx].name if n > 0 else ""
        self._folder_lbl.setText(f"File {self._folder_idx + 1} / {n}:  {name}")
        self._prev_file_btn.setEnabled(self._folder_idx > 0)
        self._next_file_btn.setEnabled(self._folder_idx < n - 1)

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

        # -- Session metadata from page 0 ImageDescription -----------------
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
        frame_bundle = int(actual.get("frame_bundle", requested.get("frame_bundle", 1)))

        # -- Per-page timestamps and frame indices --------------------------
        timestamps   = np.zeros(n_frames, dtype=np.float64)
        frame_indices = np.zeros(n_frames, dtype=np.int32)

        sub_period = 1.0 / rec_fps if rec_fps > 0 else 0.0
        for i, page in enumerate(pages):
            try:
                d = json.loads(page.description)
                bundle_ts        = float(d.get("timestamp",    0.0))
                frame_num        = int(  d.get("frame_number", i))
                sub_idx          = frame_num % max(1, frame_bundle)
                timestamps[i]    = bundle_ts + sub_idx * sub_period
                frame_indices[i] = int(d.get("frame_index", i))
            except Exception:
                timestamps[i]    = 0.0
                frame_indices[i] = i

        # -- Commit state ---------------------------------------------------
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
            f"{w}x{h} px",
            f"{rec_fps:.1f} fps",
            f"{exposure_us:.1f} us exp" if isinstance(exposure_us, (int, float)) else f"{exposure_us} us exp",
        ]
        if readout_us is not None:
            info_parts.append(f"{readout_us:.1f} us readout")
        if binning and binning != 1:
            info_parts.append(f"{binning}x{binning} bin")
        if frame_bundle and frame_bundle > 1:
            info_parts.append(f"bundle {frame_bundle}")
        self._file_lbl.setText("   ".join(info_parts))

        self._update_fps_label()
        self._auto_levels()

    # ------------------------------------------------------------------
    # Mean image generation
    # ------------------------------------------------------------------
    def _generate_mean_image(self) -> None:
        if not self._folder_files:
            return
        # Compute mean of frame 0 from every TIFF in the folder
        accum: np.ndarray | None = None
        count = 0
        for fpath in self._folder_files:
            try:
                with tifffile.TiffFile(str(fpath)) as tf:
                    pages = [p for p in tf.pages if p.size > 0]
                    if not pages:
                        continue
                    raw = pages[0].asarray()
                    if raw.ndim == 3:
                        raw = raw.reshape(-1, raw.shape[-1])
                    frame = raw.astype(np.float64)
                    if accum is None:
                        accum = frame
                    else:
                        if accum.shape == frame.shape:
                            accum += frame
                    count += 1
            except Exception:
                continue

        if accum is None or count == 0:
            return
        mean_img = (accum / count).astype(np.float32)
        win = MeanImageWindow(
            mean_img,
            title=f"Mean Image ({count} files, frame 0 each)",
        )
        win.show()
        self._popup_windows.append(win)

    # ------------------------------------------------------------------
    # Region activity computation
    # ------------------------------------------------------------------
    def _compute_region_activity(self) -> None:
        if self._tif is None or self._img._roi_rect is None:
            return
        rect = self._img._roi_rect
        x0 = max(0, int(rect.x()))
        y0 = max(0, int(rect.y()))
        x1 = int(rect.x() + rect.width())
        y1 = int(rect.y() + rect.height())
        if x1 <= x0 or y1 <= y0:
            return

        intensities = np.zeros(self.n_frames, dtype=np.float64)
        for i, page in enumerate(self._pages):
            raw = page.asarray()
            if raw.ndim == 3:
                raw = raw.reshape(-1, raw.shape[-1])
            roi_data = raw[y0:y1, x0:x1]
            intensities[i] = roi_data.mean()

        # Plot in the embedded canvas
        self._plot_figure.clear()
        ax = self._plot_figure.add_subplot(111)
        time_ms = np.arange(self.n_frames) / self.rec_fps * 1000.0
        ax.plot(time_ms, intensities, linewidth=0.8, color="#4fc3f7")
        ax.set_xlabel("Time (ms)", fontsize=9, color="#aaa")
        ax.set_ylabel("Mean intensity", fontsize=9, color="#aaa")
        ax.set_title(f"ROI [{x0}:{x1}, {y0}:{y1}]", fontsize=10, color="#ccc")
        ax.set_facecolor("#1a1a1a")
        ax.tick_params(colors="#888", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#444")
        self._plot_figure.tight_layout()
        self._plot_canvas.draw()
        self._plot_title.setText(f"ROI Activity  [{x0}:{x1}, {y0}:{y1}]")
        self._plot_widget.setVisible(True)

    def _save_plot(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Plot", "roi_activity.png",
            "PNG (*.png);;SVG (*.svg);;PDF (*.pdf);;All files (*)",
        )
        if path:
            self._plot_figure.savefig(path, dpi=150, facecolor=self._plot_figure.get_facecolor())

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
        self._play_btn.setText("\u23f8  Pause")
        self._accum = float(self.current_frame)
        self._timer.start()

    def _pause(self) -> None:
        self.playing = False
        self._timer.stop()
        self._play_btn.setText("\u25b6  Play")

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

        # -- Status labels ---------------------------------------------------
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
        # Close popup windows
        for win in self._popup_windows:
            win.close()
        self._popup_windows.clear()
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1e1e1e;
    color: #d4d4d4;
}
QLabel {
    color: #d4d4d4;
}
QPushButton {
    background-color: #2d2d2d;
    color: #d4d4d4;
    border: 1px solid #3f3f3f;
    border-radius: 3px;
    padding: 3px 10px;
}
QPushButton:hover {
    background-color: #3a3a3a;
}
QPushButton:pressed {
    background-color: #4a4a4a;
}
QPushButton:checked {
    background-color: #264f78;
    border-color: #4fc3f7;
}
QPushButton:disabled {
    background-color: #252525;
    color: #555;
    border-color: #333;
}
QSlider::groove:horizontal {
    background: #333;
    height: 6px;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #4fc3f7;
    width: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #264f78;
    border-radius: 3px;
}
QComboBox {
    background-color: #2d2d2d;
    color: #d4d4d4;
    border: 1px solid #3f3f3f;
    border-radius: 3px;
    padding: 2px 6px;
}
QComboBox::drop-down {
    border: none;
}
QComboBox QAbstractItemView {
    background-color: #2d2d2d;
    color: #d4d4d4;
    selection-background-color: #264f78;
}
QDoubleSpinBox {
    background-color: #2d2d2d;
    color: #d4d4d4;
    border: 1px solid #3f3f3f;
    border-radius: 3px;
    padding: 1px 4px;
}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background-color: #333;
    border: none;
    width: 16px;
}
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #444;
}
QToolTip {
    background-color: #2d2d2d;
    color: #d4d4d4;
    border: 1px solid #3f3f3f;
}
"""


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_STYLESHEET)
    win = RecordingViewer()
    win.show()
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.suffix in (".tif", ".tiff") and p.exists():
            win._load_file(p)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
