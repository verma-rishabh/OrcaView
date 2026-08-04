"""
MainWindow — root widget; owns all threads and wires signals.

Lifecycle:
  open_camera() → apply_settings() → start_live() / start_record() → stop_* → close_camera()

Threading:
  AcquisitionThread  — runs dcamwait_start hot loop
  WriterThread       — drains ring buffer to disk (only during recording)
  QTimer (16 ms)     — drives display updates from main thread
"""
from __future__ import annotations
import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout,
    QMessageBox, QLabel, QSplitter,
    QToolBar, QComboBox, QPushButton,
)

from dcam.camera import DCAMCamera
from core.ring_buffer import RingBuffer
from core.acquisition_thread import AcquisitionThread
from core.writer_thread import WriterThread
from core.settings import CameraSettings
from core.fps_calculator import FPSCalculator

from gui.control_panel import ControlPanel
from gui.live_view import LiveViewWidget
from gui.status_bar import CameraStatusBar

_CONFIG_DIR   = os.path.join(os.path.dirname(__file__), "..", "config")
_LIVE_JSON    = os.path.join(_CONFIG_DIR, "live_mode.json")
_RECORD_JSON  = os.path.join(_CONFIG_DIR, "record_mode.json")

_DISPLAY_INTERVAL_MS = 16   # ~60 Hz display refresh


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ORCA-Quest C15550-20UP Controller")
        self.resize(1400, 900)

        # ── State ────────────────────────────────────────────────────
        self._settings: CameraSettings   = CameraSettings()
        self._camera:   DCAMCamera | None = None
        self._ring_buf: RingBuffer | None = None
        self._acq:      AcquisitionThread | None = None
        self._writer:   WriterThread | None      = None
        self._fps_calc: FPSCalculator = FPSCalculator()
        self._last_preset_path: str   = _LIVE_JSON
        self._live_running   = False
        self._rec_running    = False
        self._total_dropped  = 0
        self._single_frame_h = 0   # height of one un-bundled frame for display

        # ── Widgets ──────────────────────────────────────────────────
        self._panel     = ControlPanel()
        self._live_view = LiveViewWidget()
        self._status_bar = CameraStatusBar()
        self.setStatusBar(self._status_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._panel)
        splitter.addWidget(self._live_view)
        splitter.setStretchFactor(0, 0)   # panel: fixed preference
        splitter.setStretchFactor(1, 1)   # live view: takes all extra space
        splitter.setSizes([480, 920])
        splitter.setHandleWidth(4)
        self.setCentralWidget(splitter)

        # ── Display timer (GUI thread) ───────────────────────────────
        self._display_timer = QTimer(self)
        self._display_timer.setInterval(_DISPLAY_INTERVAL_MS)
        self._display_timer.timeout.connect(self._refresh_display)

        # ── Auto-stop timer (starts when external trigger fires) ─────
        self._auto_stop_timer = QTimer(self)
        self._auto_stop_timer.setSingleShot(True)
        self._auto_stop_timer.timeout.connect(self._stop_record)
        self._record_duration_ms = 0  # set by _start_record

        # ── Camera selector toolbar ──────────────────────────────────
        self._build_camera_toolbar()

        # ── Wire panel signals ───────────────────────────────────────
        self._panel.load_live_preset.connect(self._load_live_preset)
        self._panel.load_record_preset.connect(self._load_record_preset)
        self._panel.save_settings.connect(self._save_settings)
        self._panel.start_record.connect(self._start_record)
        self._panel.stop_record.connect(self._stop_record)
        self._panel.settings_changed.connect(self._on_panel_settings_changed)
        self._panel.auto_contrast_changed.connect(self._live_view.set_auto_contrast)
        self._live_view.auto_contrast_done.connect(self._on_auto_contrast_done)

        # ── Open camera on startup and begin live immediately ────────
        self._open_camera()
        self._panel.populate(self._settings)
        self._start_live()

    # ------------------------------------------------------------------
    # Camera toolbar
    # ------------------------------------------------------------------

    def _build_camera_toolbar(self) -> None:
        tb = QToolBar("Camera", self)
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addWidget(QLabel("Camera: "))

        self._combo_camera = QComboBox()
        self._combo_camera.setMinimumWidth(280)
        self._combo_camera.setToolTip("Select camera device")
        tb.addWidget(self._combo_camera)

        self._btn_refresh_cameras = QPushButton("↺")
        self._btn_refresh_cameras.setFixedWidth(28)
        self._btn_refresh_cameras.setToolTip("Refresh camera list")
        self._btn_refresh_cameras.clicked.connect(self._refresh_camera_list)
        tb.addWidget(self._btn_refresh_cameras)

        self._refresh_camera_list()
        self._combo_camera.currentIndexChanged.connect(self._on_camera_selected)

    def _refresh_camera_list(self) -> None:
        self._combo_camera.blockSignals(True)
        self._combo_camera.clear()
        cameras = DCAMCamera.enumerate_cameras()
        if cameras:
            for idx, model, serial in cameras:
                self._combo_camera.addItem(f"[{idx}] {model}  SN:{serial}", userData=idx)
            # Pre-select whichever index is currently open
            if self._camera is not None:
                self._combo_camera.setCurrentIndex(self._camera._index)
        else:
            self._combo_camera.addItem("No cameras found", userData=-1)
        self._combo_camera.blockSignals(False)

    def _on_camera_selected(self, combo_index: int) -> None:
        device_index = self._combo_camera.itemData(combo_index)
        if device_index is None or device_index < 0:
            return
        if self._camera is not None and self._camera._index == device_index:
            return
        if self._live_running or self._rec_running:
            QMessageBox.warning(self, "Busy",
                                "Stop capture before switching cameras.")
            # Restore combo to current camera
            if self._camera is not None:
                self._combo_camera.blockSignals(True)
                self._combo_camera.setCurrentIndex(self._camera._index)
                self._combo_camera.blockSignals(False)
            return
        self._stop_live()
        self._close_camera()
        self._camera = None
        try:
            cam = DCAMCamera(device_index=device_index)
            cam.open()
            self._camera = cam
            model  = cam.get_model()
            serial = cam.get_serial()
            self._status_bar.set_info(f"{model}  SN: {serial}")
            self._start_live()
        except RuntimeError as exc:
            QMessageBox.critical(self, "Camera Error",
                                 f"Failed to open camera {device_index}:\n\n{exc}")

    # ------------------------------------------------------------------
    # Camera lifecycle
    # ------------------------------------------------------------------

    def _open_camera(self) -> None:
        try:
            cam = DCAMCamera(device_index=0)
            cam.open()
            self._camera = cam
            model  = cam.get_model()
            serial = cam.get_serial()
            self._status_bar.set_info(f"{model}  SN: {serial}")
            # Sync toolbar selection to device 0
            self._combo_camera.blockSignals(True)
            self._combo_camera.setCurrentIndex(0)
            self._combo_camera.blockSignals(False)
        except RuntimeError as exc:
            QMessageBox.critical(
                self, "Camera Error",
                f"Failed to open camera:\n\n{exc}\n\n"
                "Ensure DCAM-SDK4 is installed and the camera is connected "
                "via CoaXPress."
            )

    def _close_camera(self) -> None:
        if self._camera:
            self._camera.close()
            self._camera = None

    # ------------------------------------------------------------------
    # Preset loading
    # ------------------------------------------------------------------

    def _load_live_preset(self) -> None:
        self._load_preset(_LIVE_JSON)

    def _load_record_preset(self) -> None:
        self._load_preset(_RECORD_JSON)

    def _load_preset(self, path: str) -> None:
        if self._rec_running:
            QMessageBox.warning(self, "Busy",
                                "Stop recording before changing presets.")
            return
        try:
            s = CameraSettings.from_json(path)
        except Exception as exc:
            QMessageBox.critical(self, "Load Error",
                                 f"Could not load {path}:\n{exc}")
            return

        self._settings = s
        self._last_preset_path = path
        self._panel.populate(s)

        # Restart live with the new settings
        self._stop_live()
        self._start_live()

    def _save_settings(self) -> None:
        s = self._panel.collect()
        try:
            s.to_json(self._last_preset_path)
            self._status_bar.set_info(
                f"Settings saved → {os.path.basename(self._last_preset_path)}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))

    # ------------------------------------------------------------------
    # Live mode
    # ------------------------------------------------------------------

    def _start_live(self) -> None:
        if not self._camera:
            QMessageBox.warning(self, "No Camera", "Camera is not connected.")
            return
        if self._live_running:
            return

        s = self._panel.collect()
        s.align_roi()
        # Live mode always uses internal trigger so the camera free-runs.
        s.trigger_source = 1
        s.trigger_mode   = 1
        self._settings = s

        try:
            self._camera.apply_settings(s)
            rt         = self._camera.get_readout_time()
            actual_fps = self._camera.get_actual_fps()
            self._panel.set_readout_info(rt * 1e6, actual_fps)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Settings Error", str(exc))
            return

        # IMAGE_HEIGHT/WIDTH = one sub-frame. Ring buffer slot holds all bundle sub-frames.
        sub_h, sub_w   = self._camera.get_image_shape()
        actual_binning = self._camera.get_binning()
        actual_bundle  = self._camera.get_frame_bundle()
        frame_shape    = (sub_h * actual_bundle, sub_w)
        self._single_frame_h = sub_h  # crop to first sub-frame for live display

        capacity = 500   # live: small buffer, just for display
        ram_mb = capacity * frame_shape[0] * frame_shape[1] * 2 / 1024**2
        self._panel.set_camera_info(sub_w, sub_h, actual_binning, actual_bundle, ram_mb)
        self._ring_buf = RingBuffer(
            capacity=capacity,
            frame_shape=frame_shape,
        )

        # Update live view ROI overlay
        self._live_view.set_roi(s.roi_x, s.roi_y, s.roi_width, s.roi_height)
        self._live_view.set_binning(s.binning)

        # Start acquisition thread
        self._fps_calc.reset()
        self._acq = AcquisitionThread(self._camera, self._ring_buf, self._fps_calc,
                                      frame_bundle=s.frame_bundle)
        self._acq.error_occurred.connect(self._on_acq_error)
        self._acq.stats_updated.connect(self._on_stats_updated)

        try:
            self._camera.start_capture(n_buffer_frames=100)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Capture Error", str(exc))
            self._acq = None  # created above but never started; drop it cleanly
            return

        self._acq.start()
        self._display_timer.start()
        self._live_running = True
        self._total_dropped = 0
        self._panel.set_live_running(True)
        self._status_bar.set_mode("LIVE")

    def _stop_acq(self) -> None:
        """Disconnect signals, request stop, and wait for the acquisition thread."""
        if self._acq is None:
            return
        # Disconnect before waiting so a slow exit can't emit stale error signals
        # that would kill a subsequently-started live session.
        for sig in (self._acq.error_occurred, self._acq.stats_updated,
                    self._acq.trigger_fired):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._acq.request_stop()
        self._acq.wait(3000)
        self._acq = None

    def _stop_live(self) -> None:
        if not self._live_running:
            return
        self._display_timer.stop()
        self._stop_acq()
        if self._camera:
            self._camera.stop_capture()
        self._live_running = False
        self._panel.set_live_running(False)
        self._status_bar.set_mode("IDLE")

    # ------------------------------------------------------------------
    # Record mode
    # ------------------------------------------------------------------

    def _start_record(self) -> None:
        if not self._camera:
            QMessageBox.warning(self, "No Camera", "Camera is not connected.")
            return

        s = self._panel.collect()
        s.align_roi()
        self._settings = s

        if not s.save_path or not os.path.isdir(
                os.path.dirname(os.path.abspath(s.save_path)) or "."):
            # Try to create
            try:
                os.makedirs(s.save_path, exist_ok=True)
            except Exception as exc:
                QMessageBox.critical(self, "Path Error",
                                     f"Cannot create save path:\n{exc}")
                return

        if self._live_running:
            # Stop live first, then restart in record mode
            self._stop_live()

        try:
            self._camera.apply_settings(s)
            rt = self._camera.get_readout_time()
            if s.fps_target:
                s.resolve_exposure(rt)
                self._camera.apply_settings(s)   # re-apply with resolved exposure
            actual_fps = self._camera.get_actual_fps()
            self._panel.set_readout_info(rt * 1e6, actual_fps)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Settings Error", str(exc))
            return

        # Total sub-frames to record.
        bundle = max(1, s.frame_bundle)
        duration_ms = s.record_duration_ms if s.record_duration_ms > 0 else 10_000
        num_frames = max(1, int((s.fps_target or actual_fps) * duration_ms / 1000))

        # IMAGE_HEIGHT/WIDTH = one sub-frame. Ring buffer slot holds all bundle sub-frames.
        sub_h, sub_w   = self._camera.get_image_shape()
        actual_binning = self._camera.get_binning()
        actual_bundle  = self._camera.get_frame_bundle()
        frame_shape    = (sub_h * actual_bundle, sub_w)
        self._single_frame_h = sub_h  # crop to first sub-frame for live display

        # Size the ring buffer to hold the entire experiment plus 10% headroom
        # so a slightly-higher-than-expected frame rate doesn't cause overwrites.
        # Each DMA slot holds `bundle` sub-frames stacked vertically.
        dma_frames = (num_frames + bundle - 1) // bundle
        capacity = int(dma_frames * 1.1) + 1
        ram_mb = capacity * frame_shape[0] * frame_shape[1] * 2 / 1024**2
        self._panel.set_camera_info(sub_w, sub_h, actual_binning, actual_bundle, ram_mb)

        self._ring_buf = RingBuffer(
            capacity=capacity,
            frame_shape=frame_shape,
        )
        self._ring_buf.reset_read_pointer()

        self._live_view.set_roi(s.roi_x, s.roi_y, s.roi_width, s.roi_height)
        self._live_view.set_binning(s.binning)

        # Start acquisition
        self._fps_calc.reset()
        is_ext_trig = (s.trigger_source == 2)
        self._acq = AcquisitionThread(self._camera, self._ring_buf, self._fps_calc,
                                      frame_bundle=s.frame_bundle,
                                      external_trigger=is_ext_trig)
        self._acq.error_occurred.connect(self._on_acq_error)
        self._acq.stats_updated.connect(self._on_stats_updated)

        # When the external trigger fires (first frame arrives),
        # start the auto-stop timer.
        if is_ext_trig and s.record_duration_ms > 0:
            self._record_duration_ms = s.record_duration_ms
            self._acq.trigger_fired.connect(self._on_trigger_fired)

        # Start writer
        actual = {
            "fps":          actual_fps,
            "exposure_us":  self._camera.get_exposure_us(),
            "readout_us":   rt * 1e6,
            "binning":      actual_binning,
            "frame_bundle": actual_bundle,
            "image_height": sub_h,
            "image_width":  sub_w,
        }
        self._writer = WriterThread(self._ring_buf, s, num_frames=num_frames, actual=actual)
        self._writer.write_complete.connect(self._on_record_complete)
        self._writer.error_occurred.connect(self._on_write_error)

        try:
            self._camera.start_capture(n_buffer_frames=200)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Capture Error", str(exc))
            return

        self._acq.start()
        self._writer.start()
        self._display_timer.start()

        self._live_running        = True
        self._rec_running         = True
        self._total_dropped       = 0

        self._panel.set_live_running(True)
        self._panel.set_recording(True)
        if is_ext_trig and s.record_duration_ms > 0:
            self._status_bar.set_mode("ARMED — waiting for trigger")
        else:
            self._status_bar.set_mode("RECORDING")

    def _on_trigger_fired(self) -> None:
        """Called when the first frame arrives in external trigger mode.
        Starts the auto-stop countdown from this moment."""
        self._auto_stop_timer.start(self._record_duration_ms)
        self._status_bar.set_mode("RECORDING")

    def _stop_record(self) -> None:
        if not self._rec_running:
            return

        self._auto_stop_timer.stop()

        # Stop acquisition first so no new frames enter the ring buffer.
        self._stop_acq()

        if self._camera:
            self._camera.stop_capture()

        # Ask the writer to drain whatever is left in the ring buffer, then
        # emit write_complete.  _on_record_complete handles the final teardown.
        if self._writer:
            self._writer.request_drain_stop()

        self._status_bar.set_mode("DRAINING BUFFER...")

    # ------------------------------------------------------------------
    # Display refresh (GUI thread, 60 Hz)
    # ------------------------------------------------------------------

    def _refresh_display(self) -> None:
        if self._ring_buf is None:
            return
        frame = self._ring_buf.peek_latest()
        if frame is not None:
            pixels = frame.pixels
            if self._single_frame_h and self._single_frame_h < pixels.shape[0]:
                pixels = pixels[:self._single_frame_h]
            self._live_view.update_frame(pixels)
            self._status_bar.update_stats(
                fps       = self._fps_calc.fps,
                timestamp = frame.timestamp,
                dropped   = self._total_dropped,
            )

    # ------------------------------------------------------------------
    # Signals from threads
    # ------------------------------------------------------------------

    def _on_auto_contrast_done(self) -> None:
        self._panel.chk_auto_gamma.blockSignals(True)
        self._panel.chk_auto_gamma.setChecked(False)
        self._panel.chk_auto_gamma.blockSignals(False)

    def _on_stats_updated(self, total: int, lost: int) -> None:
        self._total_dropped = lost

    def _on_acq_error(self, msg: str) -> None:
        self._stop_live() if not self._rec_running else self._stop_record()
        QMessageBox.critical(self, "Acquisition Error", msg)

    def _on_record_complete(self, stem: str, frames: int, duration_s: float) -> None:
        fps     = frames / duration_s if duration_s > 0 else 0.0
        dropped = self._total_dropped

        # --- Teardown (handles both natural completion and manual drain-stop) ---
        self._display_timer.stop()
        self._auto_stop_timer.stop()

        # Natural completion: acq thread is still running → stop it and the
        # camera.  If _stop_record() already ran, _acq is None and the camera
        # is already stopped — skip to avoid a double dcambuf_release which
        # corrupts SDK state.
        if self._acq is not None:
            self._stop_acq()
            if self._camera:
                self._camera.stop_capture()

        # Writer thread just emitted this signal, so it has exited its run().
        # wait() returns immediately; we just need to release the object.
        if self._writer:
            self._writer.wait(5000)
            self._writer = None

        self._live_running = False
        self._rec_running  = False
        self._panel.set_live_running(False)
        self._panel.set_recording(False)

        self._status_bar.set_recording_complete(
            path=stem, frames=frames, duration_s=duration_s,
            fps=fps, dropped=dropped,
        )

        # Return to live view
        self._start_live()

        QMessageBox.information(
            self, "Recording Complete",
            f"All frames written to disk. Safe to close.\n\n"
            f"File:      {os.path.basename(stem)}\n"
            f"Path:      {os.path.dirname(stem)}\n"
            f"Frames:    {frames}\n"
            f"Duration:  {duration_s:.2f} s\n"
            f"Avg FPS:   {fps:.1f}\n"
            f"Dropped:   {dropped}",
        )

    def _on_panel_settings_changed(self, s: CameraSettings) -> None:
        """Re-apply camera settings immediately (live mode only; ignored during recording)."""
        if self._rec_running:
            return
        if self._live_running:
            self._stop_live()
            self._start_live()

    def _on_write_error(self, msg: str) -> None:
        # Writer failed — tear down immediately without draining.
        self._display_timer.stop()
        self._stop_acq()
        if self._camera:
            self._camera.stop_capture()
        if self._writer:
            self._writer.request_stop()
            self._writer.wait(5000)
            self._writer = None
        self._live_running = False
        self._rec_running  = False
        self._panel.set_live_running(False)
        self._panel.set_recording(False)
        self._status_bar.set_mode("IDLE")
        QMessageBox.critical(self, "Write Error", msg)
        self._start_live()

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Safe shutdown: stop threads → release camera → dcamapi_uninit."""
        if self._rec_running:
            # Stop acq and trigger a buffer drain; then wait for writer to finish.
            self._stop_record()
            if self._writer:
                self._writer.wait(300_000)  # up to 5 min to drain on close
                self._writer = None
        elif self._live_running:
            self._stop_live()
        self._close_camera()
        event.accept()
