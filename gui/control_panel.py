"""
ControlPanel — left-side settings panel.

Sections (top → bottom):
  1. Preset buttons (Live Mode / Record Mode)
  2. Exposure & FPS
  3. Image (gamma, ROI, binning, frame bundle)
  4. Recording (save path, num frames)
  5. Advanced (readout speed, trigger delay)
  6. Action buttons (Start/Stop Live | Start/Stop Record)
  7. Save current settings button
"""
from __future__ import annotations
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QPushButton, QLabel, QSpinBox,
    QDoubleSpinBox, QCheckBox, QComboBox, QLineEdit,
    QSlider, QFileDialog, QScrollArea, QSizePolicy, QProgressBar,
)

from core.settings import CameraSettings


class ControlPanel(QWidget):
    # ── Signals emitted to MainWindow ─────────────────────────────
    load_live_preset   = pyqtSignal()
    load_record_preset = pyqtSignal()
    save_settings      = pyqtSignal()

    start_live    = pyqtSignal()
    stop_live     = pyqtSignal()
    start_record  = pyqtSignal()
    stop_record   = pyqtSignal()

    settings_changed = pyqtSignal(object)   # CameraSettings

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(320)
        self.setMaximumWidth(480)

        # Scroll area so the panel works on small screens
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        self._layout = QVBoxLayout(inner)
        self._layout.setSpacing(10)
        self._layout.setContentsMargins(10, 10, 10, 10)

        self._build_presets()
        self._build_exposure()
        self._build_image()
        self._build_recording()
        self._build_advanced()
        self._build_actions()
        self._layout.addStretch()

        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._wire_signals()

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_presets(self):
        box = QGroupBox("Presets")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)
        lay.setContentsMargins(8, 12, 8, 8)

        self.btn_live_preset   = QPushButton("Live Mode")
        self.btn_record_preset = QPushButton("Record Mode")
        self.btn_live_preset.setObjectName("btn_live_preset")
        self.btn_record_preset.setObjectName("btn_record_preset")
        self.btn_live_preset.setToolTip(
            "Load live_mode.json: full sensor, high exposure, no frame bundle")
        self.btn_record_preset.setToolTip(
            "Load record_mode.json: 4096×256 ROI, 1000 fps, frame bundle")

        lay.addWidget(self.btn_live_preset)
        lay.addWidget(self.btn_record_preset)
        self._layout.addWidget(box)

    def _build_exposure(self):
        box = QGroupBox("Exposure / FPS")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(6)
        form.setHorizontalSpacing(10)

        # Exposure
        self.spin_exposure = QDoubleSpinBox()
        self.spin_exposure.setRange(1.0, 1_000_000.0)
        self.spin_exposure.setDecimals(1)
        self.spin_exposure.setSuffix(" µs")
        self.spin_exposure.setToolTip("Exposure time in microseconds")
        form.addRow("Exposure:", self.spin_exposure)

        # FPS target
        fps_row = QHBoxLayout()
        self.chk_fps = QCheckBox("Enable")
        self.chk_fps.setToolTip(
            "When enabled, exposure is auto-calculated from FPS target.\n"
            "Requires applying settings to query camera readout time.")
        self.spin_fps = QDoubleSpinBox()
        self.spin_fps.setRange(0.1, 10_000.0)
        self.spin_fps.setDecimals(1)
        self.spin_fps.setSuffix(" fps")
        self.spin_fps.setEnabled(False)
        fps_row.addWidget(self.chk_fps)
        fps_row.addWidget(self.spin_fps)
        form.addRow("FPS Target:", fps_row)

        self.lbl_readout_info = QLabel("Readout time: —")
        self.lbl_readout_info.setObjectName("lbl_section")
        form.addRow("", self.lbl_readout_info)

        self._layout.addWidget(box)

    def _build_image(self):
        box = QGroupBox("Image")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(6)
        form.setHorizontalSpacing(10)

        # Gamma
        gamma_row = QHBoxLayout()
        gamma_row.setSpacing(6)
        self.slider_gamma = QSlider(Qt.Orientation.Horizontal)
        self.slider_gamma.setRange(10, 400)   # 0.10 – 4.00
        self.slider_gamma.setValue(100)
        self.lbl_gamma = QLabel("1.00")
        self.lbl_gamma.setMinimumWidth(36)
        self.chk_auto_gamma = QCheckBox("Auto")
        gamma_row.addWidget(self.slider_gamma, stretch=1)
        gamma_row.addWidget(self.lbl_gamma)
        gamma_row.addWidget(self.chk_auto_gamma)
        form.addRow("Gamma:", gamma_row)

        # ROI — one field per row so each spinbox has full width
        self.spin_roi_x = QSpinBox()
        self.spin_roi_x.setRange(0, 4092)
        self.spin_roi_x.setToolTip("Horizontal offset (multiple of 4)")
        form.addRow("ROI  X:", self.spin_roi_x)

        self.spin_roi_y = QSpinBox()
        self.spin_roi_y.setRange(0, 2302)
        self.spin_roi_y.setToolTip("Vertical offset (multiple of 2)")
        form.addRow("ROI  Y:", self.spin_roi_y)

        self.spin_roi_w = QSpinBox()
        self.spin_roi_w.setRange(4, 4096)
        self.spin_roi_w.setValue(4096)
        self.spin_roi_w.setToolTip("Width in pixels (multiple of 4)")
        form.addRow("ROI  W:", self.spin_roi_w)

        self.spin_roi_h = QSpinBox()
        self.spin_roi_h.setRange(2, 2304)
        self.spin_roi_h.setValue(2304)
        self.spin_roi_h.setToolTip("Height in pixels (multiple of 2)  —  256 rows ≈ 1000 fps")
        form.addRow("ROI  H:", self.spin_roi_h)

        # Center ROI vertically helper button
        self.btn_center_roi = QPushButton("Center vertically on sensor")
        self.btn_center_roi.setToolTip("Sets Y offset so ROI is centred on the sensor")
        form.addRow("", self.btn_center_roi)

        # Binning
        self.combo_binning = QComboBox()
        self.combo_binning.addItems(["1×1", "2×2", "4×4"])
        self.combo_binning.setToolTip(
            "Pixel binning. After binning, ROI coords are in binned pixels.")
        form.addRow("Binning:", self.combo_binning)

        # Frame bundle
        self.spin_bundle = QSpinBox()
        self.spin_bundle.setRange(1, 256)
        self.spin_bundle.setToolTip(
            "Frame bundle (FRAMEBUNDLE_NUMBER): groups N frames per DMA transfer.\n"
            "Increases throughput at high FPS. Use ≥2 for 1000+ fps.")
        form.addRow("Frame Bundle:", self.spin_bundle)

        self._layout.addWidget(box)

    def _build_recording(self):
        box = QGroupBox("Recording")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(6)
        form.setHorizontalSpacing(10)

        # Save path
        path_row = QHBoxLayout()
        self.edit_save_path = QLineEdit()
        self.edit_save_path.setPlaceholderText("C:/data")
        self.btn_browse = QPushButton("…")
        self.btn_browse.setFixedWidth(28)
        self.btn_browse.setToolTip("Browse for save folder")
        path_row.addWidget(self.edit_save_path)
        path_row.addWidget(self.btn_browse)
        form.addRow("Save path:", path_row)

        # Num frames
        self.spin_num_frames = QSpinBox()
        self.spin_num_frames.setRange(1, 10_000_000)
        self.spin_num_frames.setSingleStep(1000)
        self.spin_num_frames.setToolTip("Number of frames to record")
        form.addRow("Frames:", self.spin_num_frames)

        # RAM buffer
        self.spin_ram_buffer = QSpinBox()
        self.spin_ram_buffer.setRange(100, 50_000)
        self.spin_ram_buffer.setSingleStep(500)
        self.spin_ram_buffer.setToolTip(
            "Ring buffer size in frames (pre-allocated in RAM).\n"
            "At 4096×256 uint16: 2000 frames ≈ 4 GB")
        form.addRow("RAM buffer:", self.spin_ram_buffer)

        # Progress bar (shown during recording)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        form.addRow("Progress:", self.progress_bar)

        self._layout.addWidget(box)

    def _build_advanced(self):
        box = QGroupBox("Advanced")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(6)
        form.setHorizontalSpacing(10)

        self.combo_readout = QComboBox()
        self.combo_readout.addItems(["Fastest", "Standard"])
        self.combo_readout.setToolTip("Sensor readout speed")
        form.addRow("Readout:", self.combo_readout)

        self.spin_trig_delay = QDoubleSpinBox()
        self.spin_trig_delay.setRange(0.0, 10.0)
        self.spin_trig_delay.setDecimals(4)
        self.spin_trig_delay.setSuffix(" s")
        self.spin_trig_delay.setToolTip("Trigger delay (internal trigger, seconds)")
        form.addRow("Trig delay:", self.spin_trig_delay)

        self._layout.addWidget(box)

    def _build_actions(self):
        box = QGroupBox("Control")
        lay = QVBoxLayout(box)
        lay.setSpacing(6)

        live_row = QHBoxLayout()
        self.btn_start_live = QPushButton("▶ Start Live")
        self.btn_stop_live  = QPushButton("■ Stop Live")
        self.btn_start_live.setObjectName("btn_start_live")
        self.btn_stop_live.setObjectName("btn_stop_live")
        self.btn_stop_live.setEnabled(False)
        live_row.addWidget(self.btn_start_live)
        live_row.addWidget(self.btn_stop_live)

        rec_row = QHBoxLayout()
        self.btn_start_rec = QPushButton("⏺ Record")
        self.btn_stop_rec  = QPushButton("■ Stop Rec")
        self.btn_start_rec.setObjectName("btn_start_rec")
        self.btn_stop_rec.setObjectName("btn_stop_rec")
        self.btn_stop_rec.setEnabled(False)
        rec_row.addWidget(self.btn_start_rec)
        rec_row.addWidget(self.btn_stop_rec)

        self.btn_save_settings = QPushButton("💾 Save Settings")
        self.btn_save_settings.setObjectName("btn_save_settings")
        self.btn_save_settings.setToolTip(
            "Save current settings to the last-loaded preset file")

        lay.addLayout(live_row)
        lay.addLayout(rec_row)
        lay.addWidget(self.btn_save_settings)
        self._layout.addWidget(box)

    # ------------------------------------------------------------------
    # Internal signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self):
        self.btn_live_preset.clicked.connect(self.load_live_preset)
        self.btn_record_preset.clicked.connect(self.load_record_preset)
        self.btn_save_settings.clicked.connect(self.save_settings)

        self.btn_start_live.clicked.connect(self.start_live)
        self.btn_stop_live.clicked.connect(self.stop_live)
        self.btn_start_rec.clicked.connect(self.start_record)
        self.btn_stop_rec.clicked.connect(self.stop_record)

        self.btn_browse.clicked.connect(self._browse_save_path)
        self.btn_center_roi.clicked.connect(self._center_roi_vertically)

        self.chk_fps.toggled.connect(self._on_fps_toggled)
        self.slider_gamma.valueChanged.connect(
            lambda v: self.lbl_gamma.setText(f"{v / 100:.2f}"))
        self.chk_auto_gamma.toggled.connect(
            lambda on: self.slider_gamma.setEnabled(not on))

    def _on_fps_toggled(self, checked: bool) -> None:
        self.spin_fps.setEnabled(checked)
        self.spin_exposure.setEnabled(not checked)

    def _browse_save_path(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select save folder",
            self.edit_save_path.text() or os.path.expanduser("~"))
        if path:
            self.edit_save_path.setText(path)

    def _center_roi_vertically(self) -> None:
        h = self.spin_roi_h.value()
        sensor_h = 2304 // max(1, [1, 2, 4][self.combo_binning.currentIndex()])
        y = (sensor_h - h) // 2
        y = (y // 2) * 2   # align to multiple of 2
        self.spin_roi_y.setValue(y)

    # ------------------------------------------------------------------
    # Public API — called by MainWindow
    # ------------------------------------------------------------------

    def populate(self, s: CameraSettings) -> None:
        """Push a CameraSettings object into all controls."""
        self.spin_exposure.setValue(s.exposure_us)

        if s.fps_target is not None:
            self.chk_fps.setChecked(True)
            self.spin_fps.setValue(s.fps_target)
        else:
            self.chk_fps.setChecked(False)

        self.slider_gamma.setValue(int(s.gamma * 100))
        self.chk_auto_gamma.setChecked(s.auto_gamma)

        self.spin_roi_x.setValue(s.roi_x)
        self.spin_roi_y.setValue(s.roi_y)
        self.spin_roi_w.setValue(s.roi_width)
        self.spin_roi_h.setValue(s.roi_height)

        binning_idx = {1: 0, 2: 1, 4: 2}.get(s.binning, 0)
        self.combo_binning.setCurrentIndex(binning_idx)

        self.spin_bundle.setValue(s.frame_bundle)
        self.edit_save_path.setText(s.save_path)
        self.spin_num_frames.setValue(s.num_frames)
        self.spin_ram_buffer.setValue(s.ram_buffer_frames)
        self.spin_trig_delay.setValue(s.trigger_delay)

        readout_idx = max(0, min(1, s.readout_speed - 1))
        self.combo_readout.setCurrentIndex(readout_idx)

    def collect(self) -> CameraSettings:
        """Read all controls and return a CameraSettings object."""
        binning = [1, 2, 4][self.combo_binning.currentIndex()]
        fps_target = self.spin_fps.value() if self.chk_fps.isChecked() else None

        s = CameraSettings(
            exposure_us      = self.spin_exposure.value(),
            fps_target       = fps_target,
            gamma            = self.slider_gamma.value() / 100.0,
            auto_gamma       = self.chk_auto_gamma.isChecked(),
            roi_x            = self.spin_roi_x.value(),
            roi_y            = self.spin_roi_y.value(),
            roi_width        = self.spin_roi_w.value(),
            roi_height       = self.spin_roi_h.value(),
            binning          = binning,
            frame_bundle     = self.spin_bundle.value(),
            save_path        = self.edit_save_path.text() or "C:/data",
            num_frames       = self.spin_num_frames.value(),
            ram_buffer_frames= self.spin_ram_buffer.value(),
            trigger_delay    = self.spin_trig_delay.value(),
            readout_speed    = self.combo_readout.currentIndex() + 1,
        )
        s.align_roi()
        return s

    def set_readout_info(self, readout_us: float, actual_fps: float) -> None:
        self.lbl_readout_info.setText(
            f"Readout: {readout_us:.0f} µs | Max: {actual_fps:.1f} fps"
        )

    # ── State management ───────────────────────────────────────────

    def set_live_running(self, running: bool) -> None:
        self.btn_start_live.setEnabled(not running)
        self.btn_stop_live.setEnabled(running)
        self.btn_start_rec.setEnabled(not running)
        self._set_settings_editable(not running)

    def set_recording(self, recording: bool, target: int = 0) -> None:
        self.btn_start_rec.setEnabled(not recording)
        self.btn_stop_rec.setEnabled(recording)
        self.progress_bar.setVisible(recording)
        if recording:
            self.progress_bar.setRange(0, target)
            self.progress_bar.setValue(0)

    def update_progress(self, written: int, target: int) -> None:
        self.progress_bar.setRange(0, target)
        self.progress_bar.setValue(written)

    def _set_settings_editable(self, editable: bool) -> None:
        for w in (self.spin_exposure, self.spin_fps, self.chk_fps,
                  self.slider_gamma, self.chk_auto_gamma,
                  self.spin_roi_x, self.spin_roi_y,
                  self.spin_roi_w, self.spin_roi_h,
                  self.combo_binning, self.spin_bundle,
                  self.combo_readout, self.spin_trig_delay):
            w.setEnabled(editable)
        if editable:
            self._on_fps_toggled(self.chk_fps.isChecked())
            self.chk_auto_gamma.toggled.emit(self.chk_auto_gamma.isChecked())
