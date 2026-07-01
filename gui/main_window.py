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
        self._live_running  = False
        self._rec_running   = False
        self._total_dropped = 0

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
        splitter.setSizes([360, 1040])
        splitter.setHandleWidth(4)
        self.setCentralWidget(splitter)

        # ── Display timer (GUI thread) ───────────────────────────────
        self._display_timer = QTimer(self)
        self._display_timer.setInterval(_DISPLAY_INTERVAL_MS)
        self._display_timer.timeout.connect(self._refresh_display)

        # ── Wire panel signals ───────────────────────────────────────
        self._panel.load_live_preset.connect(self._load_live_preset)
        self._panel.load_record_preset.connect(self._load_record_preset)
        self._panel.save_settings.connect(self._save_settings)
        self._panel.start_live.connect(self._start_live)
        self._panel.stop_live.connect(self._stop_live)
        self._panel.start_record.connect(self._start_record)
        self._panel.stop_record.connect(self._stop_record)

        # ── Open camera on startup ───────────────────────────────────
        self._open_camera()
        self._panel.populate(self._settings)

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
        if self._live_running or self._rec_running:
            QMessageBox.warning(self, "Busy",
                                "Stop capture before changing presets.")
            return
        try:
            s = CameraSettings.from_json(path)
        except Exception as exc:
            QMessageBox.critical(self, "Load Error",
                                 f"Could not load {path}:\n{exc}")
            return

        # If fps_target is set, resolve exposure after applying ROI to camera
        if self._camera and s.fps_target:
            try:
                # Apply ROI/binning first to get accurate readout time
                self._camera.apply_settings(s)
                rt = self._camera.get_readout_time()
                s.resolve_exposure(rt)
                actual_fps = self._camera.get_actual_fps()
                self._panel.set_readout_info(rt * 1e6, actual_fps)
            except RuntimeError:
                pass   # camera may not be open; use stored exposure_us

        self._settings = s
        self._last_preset_path = path
        self._panel.populate(s)

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
        self._settings = s

        try:
            self._camera.apply_settings(s)
            rt         = self._camera.get_readout_time()
            actual_fps = self._camera.get_actual_fps()
            self._panel.set_readout_info(rt * 1e6, actual_fps)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Settings Error", str(exc))
            return

        # Allocate ring buffer
        frame_shape = (s.roi_height, s.roi_width)
        self._ring_buf = RingBuffer(
            capacity=min(s.ram_buffer_frames, 500),  # live: smaller buffer
            frame_shape=frame_shape,
        )

        # Update live view ROI overlay
        self._live_view.set_roi(s.roi_x, s.roi_y, s.roi_width, s.roi_height)
        self._live_view.set_binning(s.binning)

        # Start acquisition thread
        self._fps_calc.reset()
        self._acq = AcquisitionThread(self._camera, self._ring_buf, self._fps_calc)
        self._acq.error_occurred.connect(self._on_acq_error)
        self._acq.stats_updated.connect(self._on_stats_updated)

        try:
            self._camera.start_capture(n_buffer_frames=100)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Capture Error", str(exc))
            return

        self._acq.start()
        self._display_timer.start()
        self._live_running = True
        self._total_dropped = 0
        self._panel.set_live_running(True)
        self._status_bar.set_mode("LIVE")

    def _stop_live(self) -> None:
        if not self._live_running:
            return
        self._display_timer.stop()
        if self._acq:
            self._acq.request_stop()
            self._acq.wait(3000)
            self._acq = None
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

        frame_shape = (s.roi_height, s.roi_width)
        self._ring_buf = RingBuffer(
            capacity=s.ram_buffer_frames,
            frame_shape=frame_shape,
        )
        self._ring_buf.reset_read_pointer()

        self._live_view.set_roi(s.roi_x, s.roi_y, s.roi_width, s.roi_height)
        self._live_view.set_binning(s.binning)

        # Start acquisition
        self._fps_calc.reset()
        self._acq = AcquisitionThread(self._camera, self._ring_buf, self._fps_calc)
        self._acq.error_occurred.connect(self._on_acq_error)
        self._acq.stats_updated.connect(self._on_stats_updated)

        # Start writer
        self._writer = WriterThread(self._ring_buf, s)
        self._writer.progress_updated.connect(self._on_record_progress)
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

        self._live_running  = True
        self._rec_running   = True
        self._total_dropped = 0

        self._panel.set_live_running(True)
        self._panel.set_recording(True, target=s.num_frames)
        self._status_bar.set_mode("RECORDING")

    def _stop_record(self) -> None:
        self._display_timer.stop()

        if self._writer:
            self._writer.request_stop()
            self._writer.wait(10_000)
            self._writer = None

        if self._acq:
            self._acq.request_stop()
            self._acq.wait(3000)
            self._acq = None

        if self._camera:
            self._camera.stop_capture()

        self._live_running = False
        self._rec_running  = False

        self._panel.set_live_running(False)
        self._panel.set_recording(False)
        self._status_bar.set_mode("IDLE")

    # ------------------------------------------------------------------
    # Display refresh (GUI thread, 60 Hz)
    # ------------------------------------------------------------------

    def _refresh_display(self) -> None:
        if self._ring_buf is None:
            return
        frame = self._ring_buf.peek_latest()
        if frame is not None:
            self._live_view.update_frame(frame.pixels)
            self._status_bar.update_stats(
                fps       = self._fps_calc.fps,
                timestamp = frame.timestamp,
                dropped   = self._total_dropped,
            )

    # ------------------------------------------------------------------
    # Signals from threads
    # ------------------------------------------------------------------

    def _on_stats_updated(self, total: int, lost: int) -> None:
        self._total_dropped = lost

    def _on_acq_error(self, msg: str) -> None:
        self._stop_live() if not self._rec_running else self._stop_record()
        QMessageBox.critical(self, "Acquisition Error", msg)

    def _on_record_progress(self, written: int, target: int) -> None:
        self._panel.update_progress(written, target)
        if written >= target:
            self._stop_record()

    def _on_record_complete(self, stem: str) -> None:
        self._status_bar.set_info(f"Saved → {stem}.raw")

    def _on_write_error(self, msg: str) -> None:
        self._stop_record()
        QMessageBox.critical(self, "Write Error", msg)

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Safe shutdown: stop threads → release camera → dcamapi_uninit."""
        if self._rec_running:
            self._stop_record()
        elif self._live_running:
            self._stop_live()
        self._close_camera()
        event.accept()
