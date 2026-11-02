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
    load_custom_preset = pyqtSignal(str)   # path to JSON file
    save_settings      = pyqtSignal()

    start_record  = pyqtSignal()
    stop_record   = pyqtSignal()

    settings_changed   = pyqtSignal(object)   # CameraSettings
    auto_gamma_changed  = pyqtSignal(bool)
    gamma_changed       = pyqtSignal(float)    # 0.10–4.00
    ac_interval_changed = pyqtSignal(int)
    reset_markers = pyqtSignal()
    roi_mode_changed = pyqtSignal(bool)
    reset_zoom = pyqtSignal()
    stop_burst = pyqtSignal()

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

        # Live row
        live_row = QHBoxLayout()
        live_row.setSpacing(4)
        self.btn_live_preset = QPushButton("Live Mode")
        self.btn_live_preset.setObjectName("btn_live_preset")
        self.btn_live_preset.setToolTip(
            "Load live_mode.json: full sensor, high exposure, no frame bundle")
        self.btn_live_browse = QPushButton("...")
        self.btn_live_browse.setObjectName("btn_preset_browse")
        self.btn_live_browse.setFixedWidth(30)
        self.btn_live_browse.setToolTip("Load a custom JSON preset for live mode")
        live_row.addWidget(self.btn_live_preset, stretch=1)
        live_row.addWidget(self.btn_live_browse)
        lay.addLayout(live_row)

        # Record row
        rec_row = QHBoxLayout()
        rec_row.setSpacing(4)
        self.btn_record_preset = QPushButton("Record Mode")
        self.btn_record_preset.setObjectName("btn_record_preset")
        self.btn_record_preset.setToolTip(
            "Load record_mode.json: 4096×256 ROI, 1000 fps, frame bundle")
        self.btn_record_browse = QPushButton("...")
        self.btn_record_browse.setObjectName("btn_preset_browse")
        self.btn_record_browse.setFixedWidth(30)
        self.btn_record_browse.setToolTip("Load a custom JSON preset for record mode")
        rec_row.addWidget(self.btn_record_preset, stretch=1)
        rec_row.addWidget(self.btn_record_browse)
        lay.addLayout(rec_row)

        self._layout.addWidget(box)

    def _build_exposure(self):
        box = QGroupBox("Exposure / FPS")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(6)
        form.setHorizontalSpacing(10)

        # Exposure
        exp_row = QHBoxLayout()
        exp_row.setSpacing(6)
        self.slider_exposure = QSlider(Qt.Orientation.Horizontal)
        self.slider_exposure.setRange(0, 1000)  # mapped logarithmically
        self.slider_exposure.setValue(500)
        self.slider_exposure.setToolTip("Exposure time (logarithmic scale)")
        self.spin_exposure = QDoubleSpinBox()
        self.spin_exposure.setRange(1.0, 1_000_000.0)
        self.spin_exposure.setDecimals(1)
        self.spin_exposure.setSuffix(" µs")
        self.spin_exposure.setToolTip("Exposure time in microseconds")
        self.spin_exposure.setFixedWidth(110)
        exp_row.addWidget(self.slider_exposure, stretch=1)
        exp_row.addWidget(self.spin_exposure)
        form.addRow("Exposure:", exp_row)

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

        # Gamma (display only — software power curve)
        gamma_row = QHBoxLayout()
        gamma_row.setSpacing(6)
        self.slider_gamma = QSlider(Qt.Orientation.Horizontal)
        self.slider_gamma.setRange(10, 400)   # 0.10 – 4.00
        self.slider_gamma.setValue(100)
        self.lbl_gamma = QLabel("1.00")
        self.lbl_gamma.setMinimumWidth(36)
        self.chk_auto_gamma = QCheckBox("Auto")
        self.chk_auto_gamma.setToolTip("Continuously auto-adjust gamma (display only)")
        gamma_row.addWidget(self.slider_gamma, stretch=1)
        gamma_row.addWidget(self.lbl_gamma)
        gamma_row.addWidget(self.chk_auto_gamma)
        form.addRow("Gamma:", gamma_row)

        # Auto gamma interval
        ac_interval_row = QHBoxLayout()
        ac_interval_row.setSpacing(6)
        self.spin_ac_interval = QSpinBox()
        self.spin_ac_interval.setRange(1, 100)
        self.spin_ac_interval.setValue(5)
        self.spin_ac_interval.setToolTip("Recalculate auto gamma every N frames")
        ac_interval_row.addWidget(self.spin_ac_interval)
        ac_interval_row.addWidget(QLabel("frames"))
        ac_interval_row.addStretch()
        form.addRow("", ac_interval_row)

        # ROI — one field per row so each spinbox has full width
        self.spin_roi_x = QSpinBox()
        self.spin_roi_x.setRange(0, 4092)
        self.spin_roi_x.setToolTip("Horizontal offset (multiple of 4)")
        form.addRow("ROI  X:", self.spin_roi_x)

        self.spin_roi_y = QSpinBox()
        self.spin_roi_y.setRange(0, 2300)
        self.spin_roi_y.setToolTip("Vertical offset (multiple of 4)")
        form.addRow("ROI  Y:", self.spin_roi_y)

        self.spin_roi_w = QSpinBox()
        self.spin_roi_w.setRange(4, 4096)
        self.spin_roi_w.setValue(4096)
        self.spin_roi_w.setToolTip("Width in pixels (multiple of 4)")
        form.addRow("ROI  W:", self.spin_roi_w)

        self.spin_roi_h = QSpinBox()
        self.spin_roi_h.setRange(4, 2304)
        self.spin_roi_h.setValue(2304)
        self.spin_roi_h.setToolTip("Height in pixels (multiple of 4)  —  256 rows ≈ 1000 fps")
        form.addRow("ROI  H:", self.spin_roi_h)

        # Reset cell markers
        self.btn_reset_markers = QPushButton("Reset Markers")
        self.btn_reset_markers.setToolTip("Remove all cell markers from the live view")
        form.addRow("", self.btn_reset_markers)

        # ROI edit mode toggle
        self.chk_roi_mode = QCheckBox("ROI Edit Mode")
        self.chk_roi_mode.setChecked(False)
        self.chk_roi_mode.setToolTip(
            "ON: mouse drags edit the ROI rectangle.\n"
            "OFF: clicks place cell markers.")
        form.addRow("", self.chk_roi_mode)

        # Reset zoom
        self.btn_reset_zoom = QPushButton("Reset Zoom")
        self.btn_reset_zoom.setToolTip("Reset zoom and pan to fit-to-window")
        form.addRow("", self.btn_reset_zoom)

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
        self._recording_box = box
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

        # File name prefix
        self.edit_file_prefix = QLineEdit()
        self.edit_file_prefix.setPlaceholderText("(optional prefix)")
        self.edit_file_prefix.setToolTip(
            "Prepended to the recording filename.\n"
            "Leave empty for default 'recording_YYYYMMDD_HHMMSS'.")
        form.addRow("File prefix:", self.edit_file_prefix)

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

        # Burst mode (repeat trigger)
        burst_row = QHBoxLayout()
        self.chk_burst_mode = QCheckBox("Burst")
        self.chk_burst_mode.setToolTip(
            "Auto re-arm after each recording.\n"
            "Saves numbered files in a session subfolder.")
        self.chk_burst_mode.setEnabled(False)
        self.lbl_burst_count = QLabel("Saved: 0")
        self.lbl_burst_count.setStyleSheet("color: #aaa; font-size: 11px;")
        self.lbl_burst_count.hide()
        burst_row.addWidget(self.chk_burst_mode)
        burst_row.addWidget(self.lbl_burst_count)
        form.addRow("", burst_row)

        self.btn_stop_burst = QPushButton("Stop Burst")
        self.btn_stop_burst.setObjectName("btn_stop_burst")
        self.btn_stop_burst.hide()
        form.addRow("", self.btn_stop_burst)

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
        self._actions_box = box
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

        lay.addLayout(rec_row)
        self._layout.addWidget(box)

        # Save button — always visible (not inside recording group)
        self.btn_save_settings = QPushButton("💾 Save Settings")
        self.btn_save_settings.setObjectName("btn_save_settings")
        self.btn_save_settings.setToolTip(
            "Save current settings to a JSON file")
        self._layout.addWidget(self.btn_save_settings)

    # ------------------------------------------------------------------
    # Internal signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self):
        self.btn_live_preset.clicked.connect(self.load_live_preset)
        self.btn_record_preset.clicked.connect(self.load_record_preset)
        self.btn_live_browse.clicked.connect(lambda: self._browse_preset("live"))
        self.btn_record_browse.clicked.connect(lambda: self._browse_preset("record"))
        self.btn_save_settings.clicked.connect(self.save_settings)

        self.btn_start_rec.clicked.connect(self.start_record)
        self.btn_stop_rec.clicked.connect(self.stop_record)

        self.btn_browse.clicked.connect(self._browse_save_path)
        self.btn_reset_markers.clicked.connect(self.reset_markers)
        self.chk_roi_mode.toggled.connect(lambda on: self.roi_mode_changed.emit(not on))
        self.btn_reset_zoom.clicked.connect(self.reset_zoom)
        self.btn_stop_burst.clicked.connect(self.stop_burst)

        # Exposure slider ↔ spinbox sync (logarithmic)
        import math
        _EXP_MIN_LOG = math.log10(1.0)       # 1 µs
        _EXP_MAX_LOG = math.log10(1_000_000)  # 1 s
        _EXP_RANGE   = 1000                   # slider steps

        def _exp_slider_to_us(v: int) -> float:
            return 10 ** (_EXP_MIN_LOG + (v / _EXP_RANGE) * (_EXP_MAX_LOG - _EXP_MIN_LOG))

        def _exp_us_to_slider(us: float) -> int:
            us = max(1.0, min(1_000_000.0, us))
            return int(((math.log10(us) - _EXP_MIN_LOG) / (_EXP_MAX_LOG - _EXP_MIN_LOG)) * _EXP_RANGE)

        self._syncing_exposure = False

        def _on_exp_slider(v: int):
            if self._syncing_exposure:
                return
            self._syncing_exposure = True
            self.spin_exposure.setValue(round(_exp_slider_to_us(v), 1))
            self._syncing_exposure = False

        def _on_exp_spin():
            if self._syncing_exposure:
                return
            self._syncing_exposure = True
            self.slider_exposure.setValue(_exp_us_to_slider(self.spin_exposure.value()))
            self._syncing_exposure = False

        self.slider_exposure.valueChanged.connect(_on_exp_slider)
        self.spin_exposure.valueChanged.connect(lambda _: _on_exp_spin())
        self.slider_exposure.sliderReleased.connect(lambda: _emit())
        self.slider_exposure.valueChanged.connect(
            lambda _: _emit() if not self.slider_exposure.isSliderDown() else None)
        # Store for use in populate
        self._exp_us_to_slider = _exp_us_to_slider

        self.slider_gamma.valueChanged.connect(
            lambda v: self.lbl_gamma.setText(f"{v / 100:.2f}"))
        self.slider_gamma.valueChanged.connect(
            lambda v: self.gamma_changed.emit(v / 100.0))
        self.chk_auto_gamma.toggled.connect(self.auto_gamma_changed)
        self.spin_ac_interval.valueChanged.connect(self.ac_interval_changed)

        # Apply settings on Enter (editingFinished) for all camera-affecting spinboxes
        def _emit():
            self.settings_changed.emit(self.collect())

        for w in (self.spin_exposure,
                  self.spin_roi_x, self.spin_roi_y, self.spin_roi_w, self.spin_roi_h,
                  self.spin_bundle):
            w.editingFinished.connect(_emit)

        # Combo changes apply immediately
        self.combo_binning.currentIndexChanged.connect(lambda _: _emit())
        self.combo_trig_source.currentIndexChanged.connect(self._on_trig_source_changed)

        # Disable scroll-wheel value changes on all spinboxes and combo boxes
        self._no_scroll = _NoScroll(self)
        for w in (
            self.spin_exposure,
            self.spin_roi_x, self.spin_roi_y, self.spin_roi_w, self.spin_roi_h,
            self.spin_bundle, self.spin_duration_ms,
            self.combo_binning, self.combo_trig_source, self.combo_trig_polarity,
        ):
            w.installEventFilter(self._no_scroll)

    def _browse_preset(self, mode: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select {mode} preset",
            os.path.join(os.path.dirname(__file__), "..", "config"),
            "JSON files (*.json)")
        if path:
            self.load_custom_preset.emit(path)

    def set_active_preset(self, mode: str) -> None:
        """Dim the inactive preset button, highlight the active one."""
        is_live = (mode == "live")
        self.btn_live_preset.setProperty("active", is_live)
        self.btn_record_preset.setProperty("active", not is_live)
        # Force style refresh
        self.btn_live_preset.style().unpolish(self.btn_live_preset)
        self.btn_live_preset.style().polish(self.btn_live_preset)
        self.btn_record_preset.style().unpolish(self.btn_record_preset)
        self.btn_record_preset.style().polish(self.btn_record_preset)

    def _browse_save_path(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select save folder",
            self.edit_save_path.text() or os.path.expanduser("~"))
        if path:
            self.edit_save_path.setText(path)

    def _on_trig_source_changed(self, idx: int) -> None:
        is_external = (idx == 1)
        self.combo_trig_polarity.setEnabled(is_external)
        self.chk_burst_mode.setEnabled(is_external)
        if not is_external:
            self.chk_burst_mode.setChecked(False)
        self.btn_start_rec.setText("⏺ Arm" if is_external else "⏺ Record")
        self.settings_changed.emit(self.collect())

    # ------------------------------------------------------------------
    # Public API — called by MainWindow
    # ------------------------------------------------------------------

    def populate(self, s: CameraSettings) -> None:
        """Push a CameraSettings object into all controls."""
        # Block signals that would fire settings_changed or exposure recompute
        _block = (self.combo_binning, self.combo_trig_source,
                  self.combo_trig_polarity, self.chk_burst_mode,
                  self.slider_exposure)
        for w in _block:
            w.blockSignals(True)

        self.spin_exposure.setValue(s.exposure_us)
        self.slider_exposure.setValue(self._exp_us_to_slider(s.exposure_us))

        self.spin_roi_x.setValue(s.roi_x)
        self.spin_roi_y.setValue(s.roi_y)
        self.spin_roi_w.setValue(s.roi_width)
        self.spin_roi_h.setValue(s.roi_height)

        binning_idx = {1: 0, 2: 1, 4: 2}.get(s.binning, 0)
        self.combo_binning.setCurrentIndex(binning_idx)

        self.spin_bundle.setValue(s.frame_bundle)
        self.edit_save_path.setText(s.save_path)
        self.edit_file_prefix.setText(s.file_prefix)
        # trigger_source: 1=Internal, 2=External → combo index 0/1
        self.combo_trig_source.setCurrentIndex(0 if s.trigger_source == 1 else 1)
        is_external = (s.trigger_source == 2)
        # polarity: 2=Positive(rising)→index 0, 1=Negative(falling)→index 1
        self.combo_trig_polarity.setCurrentIndex(0 if s.trigger_polarity == 2 else 1)
        self.combo_trig_polarity.setEnabled(is_external)
        self.spin_duration_ms.setValue(s.record_duration_ms)
        self.chk_burst_mode.setChecked(s.burst_mode)
        self.chk_burst_mode.setEnabled(is_external)
        self.btn_start_rec.setText("⏺ Arm" if is_external else "⏺ Record")

        for w in _block:
            w.blockSignals(False)

    def collect(self) -> CameraSettings:
        """Read all controls and return a CameraSettings object."""
        binning = [1, 2, 4][self.combo_binning.currentIndex()]
        # combo index 0 → Internal (1), index 1 → External (2)
        trig_src  = 1 if self.combo_trig_source.currentIndex() == 0 else 2
        # External trigger uses START mode (one pulse starts free-run sequence)
        trig_mode = 6 if trig_src == 2 else 1
        # combo index 0 → Positive/rising (2), index 1 → Negative/falling (1)
        trig_pol  = 2 if self.combo_trig_polarity.currentIndex() == 0 else 1

        duration_ms = self.spin_duration_ms.value()
        s = CameraSettings(
            exposure_us         = self.spin_exposure.value(),
            roi_x               = self.spin_roi_x.value(),
            roi_y               = self.spin_roi_y.value(),
            roi_width           = self.spin_roi_w.value(),
            roi_height          = self.spin_roi_h.value(),
            binning             = binning,
            frame_bundle        = self.spin_bundle.value(),
            save_path           = self.edit_save_path.text() or "C:/data",
            file_prefix         = self.edit_file_prefix.text().strip(),
            trigger_source      = trig_src,
            trigger_mode        = trig_mode,
            trigger_polarity    = trig_pol,
            record_duration_ms  = duration_ms,
            burst_mode          = self.chk_burst_mode.isChecked(),
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
        self.lbl_readout_info.setText(
            f"Readout: {readout_us:.0f} µs | Max: {actual_fps:.1f} fps"
        )

    # ── State management ───────────────────────────────────────────

    def set_live_running(self, running: bool) -> None:
        """Show/hide recording controls based on whether we are in live-only mode."""
        # During recording, set_recording(True) is also called, which shows the stop button.
        # Here we just hide the recording group and action box when in pure live mode.
        pass

    def set_record_controls_visible(self, visible: bool) -> None:
        """Show or hide the Recording group and Record button."""
        self._recording_box.setVisible(visible)
        self._actions_box.setVisible(visible)

    def set_recording(self, recording: bool) -> None:
        self.btn_start_rec.setVisible(not recording)
        self.btn_stop_rec.setVisible(recording)

    def set_burst_active(self, active: bool) -> None:
        self.lbl_burst_count.setVisible(active)
        self.btn_stop_burst.setVisible(active)

    def set_burst_count(self, n: int) -> None:
        self.lbl_burst_count.setText(f"Saved: {n}")

    def set_roi_preview(self, x: int, y: int, w: int, h: int) -> None:
        """Update ROI spinboxes without triggering settings_changed."""
        for spin in (self.spin_roi_x, self.spin_roi_y,
                     self.spin_roi_w, self.spin_roi_h):
            spin.blockSignals(True)
        self.spin_roi_x.setValue(x)
        self.spin_roi_y.setValue(y)
        self.spin_roi_w.setValue(w)
        self.spin_roi_h.setValue(h)
        for spin in (self.spin_roi_x, self.spin_roi_y,
                     self.spin_roi_w, self.spin_roi_h):
            spin.blockSignals(False)
