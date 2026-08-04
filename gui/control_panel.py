"""
ControlPanel — left-side settings panel.

Sections (top → bottom):
  1. Preset buttons (Live Mode / Record Mode)
  2. Exposure & FPS
  3. Image (gamma, ROI, binning, frame bundle)
  4. Recording (save path, trigger source)
  5. Action buttons (Start/Stop Record)
  6. Save current settings button
"""
from __future__ import annotations
import os

from PyQt6.QtCore import Qt, QEvent, QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QPushButton, QLabel, QSpinBox,
    QDoubleSpinBox, QCheckBox, QComboBox, QLineEdit,
    QSlider, QFileDialog, QScrollArea,
)


class _NoScroll(QObject):
    """Event filter that eats wheel events so spinboxes/combos don't change on scroll."""
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel:
            return True   # consume — do not forward
        return False

from core.settings import CameraSettings


class ControlPanel(QWidget):
    # ── Signals emitted to MainWindow ─────────────────────────────
    load_live_preset   = pyqtSignal()
    load_record_preset = pyqtSignal()
    save_settings      = pyqtSignal()

    start_record  = pyqtSignal()
    stop_record   = pyqtSignal()

    settings_changed   = pyqtSignal(object)   # CameraSettings
    auto_contrast_changed = pyqtSignal(bool)

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

        self._last_readout_sec = 0.0   # cached from last camera query

        self._build_presets()
        self._build_exposure()
        self._build_image()
        self._build_recording()
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

        self.lbl_readout_info = QLabel("Readout: — µs | Max: — fps")
        form.addRow("", self.lbl_readout_info)

        self.lbl_camera_info = QLabel("Frame: — | RAM: —")
        self.lbl_camera_info.setStyleSheet("color: #aaa; font-size: 11px;")
        form.addRow("", self.lbl_camera_info)

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

        # Trigger source for recording
        self.combo_trig_source = QComboBox()
        self.combo_trig_source.addItems(["Internal (manual)", "External start trigger"])
        self.combo_trig_source.setToolTip(
            "Internal: start/stop via buttons.\n"
            "External: camera waits for a TTL pulse on the trigger input, then\n"
            "free-runs until the Stop button is pressed.")
        form.addRow("Trigger:", self.combo_trig_source)

        # Trigger edge polarity
        self.combo_trig_polarity = QComboBox()
        self.combo_trig_polarity.addItems(["Rising edge", "Falling edge"])
        self.combo_trig_polarity.setToolTip(
            "Which edge of the TTL pulse starts acquisition.\n"
            "Rising = low→high, Falling = high→low.")
        self.combo_trig_polarity.setEnabled(False)
        form.addRow("Edge:", self.combo_trig_polarity)

        # Recording duration
        self.spin_duration_ms = QSpinBox()
        self.spin_duration_ms.setRange(0, 3_600_000)
        self.spin_duration_ms.setSuffix(" ms")
        self.spin_duration_ms.setValue(10000)
        self.spin_duration_ms.setToolTip(
            "Recording duration in milliseconds.\n"
            "0 = run until Stop button is pressed.")
        form.addRow("Duration:", self.spin_duration_ms)

        self._layout.addWidget(box)

    def _build_actions(self):
        box = QGroupBox("Control")
        lay = QVBoxLayout(box)
        lay.setSpacing(6)

        rec_row = QHBoxLayout()
        self.btn_start_rec = QPushButton("⏺ Record")
        self.btn_stop_rec  = QPushButton("■ Stop Rec")
        self.btn_start_rec.setObjectName("btn_start_rec")
        self.btn_stop_rec.setObjectName("btn_stop_rec")
        self.btn_stop_rec.hide()
        rec_row.addWidget(self.btn_start_rec)
        rec_row.addWidget(self.btn_stop_rec)

        self.btn_save_settings = QPushButton("💾 Save Settings")
        self.btn_save_settings.setObjectName("btn_save_settings")
        self.btn_save_settings.setToolTip(
            "Save current settings to the last-loaded preset file")

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

        self.btn_start_rec.clicked.connect(self.start_record)
        self.btn_stop_rec.clicked.connect(self.stop_record)

        self.btn_browse.clicked.connect(self._browse_save_path)
        self.btn_center_roi.clicked.connect(self._center_roi_vertically)

        self.chk_fps.toggled.connect(self._on_fps_toggled)
        self.chk_fps.toggled.connect(self._update_exposure_from_fps)
        self.chk_fps.toggled.connect(lambda: self.settings_changed.emit(self.collect()))
        self.slider_gamma.valueChanged.connect(
            lambda v: self.lbl_gamma.setText(f"{v / 100:.2f}"))
        self.chk_auto_gamma.toggled.connect(
            lambda on: self.slider_gamma.setEnabled(not on))
        self.chk_auto_gamma.toggled.connect(self.auto_contrast_changed)

        # Apply settings on Enter (editingFinished) for all camera-affecting spinboxes
        def _emit():
            self.settings_changed.emit(self.collect())

        for w in (self.spin_exposure,
                  self.spin_roi_x, self.spin_roi_y, self.spin_roi_w, self.spin_roi_h,
                  self.spin_bundle):
            w.editingFinished.connect(_emit)

        self.spin_fps.editingFinished.connect(self._update_exposure_from_fps)
        self.spin_fps.editingFinished.connect(_emit)

        # Combo changes apply immediately
        self.combo_binning.currentIndexChanged.connect(lambda _: _emit())
        self.combo_trig_source.currentIndexChanged.connect(self._on_trig_source_changed)

        # Disable scroll-wheel value changes on all spinboxes and combo boxes
        self._no_scroll = _NoScroll(self)
        for w in (
            self.spin_exposure, self.spin_fps,
            self.spin_roi_x, self.spin_roi_y, self.spin_roi_w, self.spin_roi_h,
            self.spin_bundle, self.spin_duration_ms,
            self.combo_binning, self.combo_trig_source, self.combo_trig_polarity,
        ):
            w.installEventFilter(self._no_scroll)

    def _on_fps_toggled(self, checked: bool) -> None:
        self.spin_fps.setEnabled(checked)
        self.spin_exposure.setEnabled(not checked)

    def _browse_save_path(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select save folder",
            self.edit_save_path.text() or os.path.expanduser("~"))
        if path:
            self.edit_save_path.setText(path)

    def _on_trig_source_changed(self, idx: int) -> None:
        is_external = (idx == 1)
        self.combo_trig_polarity.setEnabled(is_external)
        self.btn_start_rec.setText("⏺ Arm" if is_external else "⏺ Record")
        self.settings_changed.emit(self.collect())

    def _center_roi_vertically(self) -> None:
        h = self.spin_roi_h.value()
        sensor_h = 2304   # ROI is always in sensor pixel coordinates
        y = (sensor_h - h) // 2
        y = (y // 2) * 2   # align to multiple of 2
        self.spin_roi_y.setValue(y)

    # ------------------------------------------------------------------
    # Public API — called by MainWindow
    # ------------------------------------------------------------------

    def populate(self, s: CameraSettings) -> None:
        """Push a CameraSettings object into all controls."""
        # Block signals that would fire settings_changed or exposure recompute
        _block = (self.chk_fps, self.combo_binning, self.combo_trig_source, self.combo_trig_polarity)
        for w in _block:
            w.blockSignals(True)

        self.spin_exposure.setValue(s.exposure_us)

        if s.fps_target is not None:
            self.chk_fps.setChecked(True)
            self.spin_fps.setValue(s.fps_target)
            self.spin_fps.setEnabled(True)
            self.spin_exposure.setEnabled(False)
        else:
            self.chk_fps.setChecked(False)
            self.spin_fps.setEnabled(False)
            self.spin_exposure.setEnabled(True)

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
        # trigger_source: 1=Internal, 2=External → combo index 0/1
        self.combo_trig_source.setCurrentIndex(0 if s.trigger_source == 1 else 1)
        is_external = (s.trigger_source == 2)
        # polarity: 2=Positive(rising)→index 0, 1=Negative(falling)→index 1
        self.combo_trig_polarity.setCurrentIndex(0 if s.trigger_polarity == 2 else 1)
        self.combo_trig_polarity.setEnabled(is_external)
        self.spin_duration_ms.setValue(s.record_duration_ms)
        self.btn_start_rec.setText("⏺ Arm" if is_external else "⏺ Record")

        for w in _block:
            w.blockSignals(False)

    def collect(self) -> CameraSettings:
        """Read all controls and return a CameraSettings object."""
        binning = [1, 2, 4][self.combo_binning.currentIndex()]
        fps_target = self.spin_fps.value() if self.chk_fps.isChecked() else None
        # combo index 0 → Internal (1), index 1 → External (2)
        trig_src  = 1 if self.combo_trig_source.currentIndex() == 0 else 2
        # External trigger uses START mode (one pulse starts free-run sequence)
        trig_mode = 6 if trig_src == 2 else 1
        # combo index 0 → Positive/rising (2), index 1 → Negative/falling (1)
        trig_pol  = 2 if self.combo_trig_polarity.currentIndex() == 0 else 1

        duration_ms = self.spin_duration_ms.value()
        s = CameraSettings(
            exposure_us         = self.spin_exposure.value(),
            fps_target          = fps_target,
            gamma               = self.slider_gamma.value() / 100.0,
            auto_gamma          = self.chk_auto_gamma.isChecked(),
            roi_x               = self.spin_roi_x.value(),
            roi_y               = self.spin_roi_y.value(),
            roi_width           = self.spin_roi_w.value(),
            roi_height          = self.spin_roi_h.value(),
            binning             = binning,
            frame_bundle        = self.spin_bundle.value(),
            save_path           = self.edit_save_path.text() or "C:/data",
            trigger_source      = trig_src,
            trigger_mode        = trig_mode,
            trigger_polarity    = trig_pol,
            record_duration_ms  = duration_ms,
        )
        s.align_roi()
        return s

    def set_camera_info(self, frame_w: int, sub_h: int, binning: int,
                        bundle: int, ram_mb: float) -> None:
        """frame_w/sub_h are the actual camera-reported per-sub-frame dimensions."""
        self.lbl_camera_info.setText(
            f"Frame: {frame_w}×{sub_h} px  bin {binning}×{binning}  "
            f"bundle ×{bundle}  |  RAM: {ram_mb:.0f} MB"
        )

    def set_readout_info(self, readout_us: float, actual_fps: float) -> None:
        self._last_readout_sec = readout_us / 1e6
        self.lbl_readout_info.setText(
            f"Readout: {readout_us:.0f} µs | Max: {actual_fps:.1f} fps"
        )
        # Recompute exposure now that we have an accurate readout time
        self._update_exposure_from_fps()

    def _update_exposure_from_fps(self) -> None:
        """If FPS mode is active and readout time is known, update the exposure spinbox."""
        if not self.chk_fps.isChecked() or self._last_readout_sec <= 0:
            return
        from core.settings import fps_to_exposure_us
        exp = fps_to_exposure_us(self.spin_fps.value(), self._last_readout_sec)
        self.spin_exposure.blockSignals(True)
        self.spin_exposure.setValue(round(exp, 1))
        self.spin_exposure.blockSignals(False)

    # ── State management ───────────────────────────────────────────

    def set_live_running(self, running: bool) -> None:
        pass  # record button state is managed exclusively by set_recording()

    def set_recording(self, recording: bool) -> None:
        self.btn_start_rec.setVisible(not recording)
        self.btn_stop_rec.setVisible(recording)
