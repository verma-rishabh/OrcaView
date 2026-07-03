"""
LiveViewWidget — displays camera frames using QPainter.

Rendering pipeline:
    uint16 numpy (H×W) → right-shift 8 → uint8 → QImage (Grayscale8)
    → QPixmap.fromImage() [copies, so numpy array is safe to reuse]
    → QPainter.drawPixmap (scaled, keeping aspect ratio)
    → ROIOverlay painted on top

Frame updates are driven by a QTimer in MainWindow (not by the acquisition
thread directly) to decouple display rate from camera rate.
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, QRect, pyqtSignal
from PyQt6.QtGui import QImage, QPainter, QPixmap, QColor
from PyQt6.QtWidgets import QWidget

from gui.roi_overlay import ROIOverlay


class LiveViewWidget(QWidget):
    # Emitted after the one-shot contrast capture fires, so the checkbox unchecks.
    auto_contrast_done = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap:        QPixmap | None = None
        self._overlay:       ROIOverlay     = ROIOverlay()
        self._binning:       int            = 1
        self._capture_next:  bool           = False
        self._ac_lo:         float          = 0.0
        self._ac_hi:         float          = 0.0   # 0 == no stretch applied yet
        self.setMinimumSize(640, 360)
        self.setStyleSheet("background-color: #111111;")

    # ------------------------------------------------------------------
    # Called by MainWindow's display timer
    # ------------------------------------------------------------------

    def set_auto_contrast(self, enabled: bool) -> None:
        """Schedule a one-shot contrast capture on the next frame."""
        self._capture_next = enabled

    def update_frame(self, pixels: np.ndarray) -> None:
        """
        pixels : uint16 numpy array, shape (H, W).
        Normal mode  : right-shift-8 (fast, no per-pixel division).
        After Auto   : stretch using lo/hi captured from the trigger frame.
          Subsamples 1-in-8 pixels for the percentile so it stays fast on
          large frames, then applies the stretch to every subsequent frame.
        QPixmap.fromImage() makes an internal copy so 'pixels' can be
        discarded after this call returns.
        """
        if self._capture_next:
            sample = pixels[::8, ::8]
            lo = float(np.percentile(sample, 0.5))
            hi = float(np.percentile(sample, 99.5))
            if hi > lo:
                self._ac_lo, self._ac_hi = lo, hi
            self._capture_next = False
            self.auto_contrast_done.emit()

        if self._ac_hi > self._ac_lo:
            arr8 = np.clip(
                (pixels.astype(np.float32) - self._ac_lo) * (255.0 / (self._ac_hi - self._ac_lo)),
                0, 255,
            ).astype(np.uint8)
        else:
            arr8 = (pixels >> 8).astype(np.uint8)
        h, w = arr8.shape
        # Pass raw buffer to QImage — stride = w (one byte per pixel)
        qimg = QImage(arr8.data, w, h, w, QImage.Format.Format_Grayscale8)
        # fromImage() copies the data, making the pixmap independent
        self._pixmap = QPixmap.fromImage(qimg)
        self.update()   # schedules a paintEvent

    def set_roi(self, x: int, y: int, w: int, h: int) -> None:
        self._overlay.set_roi(x, y, w, h)
        self.update()

    def set_binning(self, binning: int) -> None:
        self._binning = binning

    def set_overlay_visible(self, visible: bool) -> None:
        self._overlay.set_visible(visible)
        self.update()

    # ------------------------------------------------------------------
    # Qt paint event
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)

        if self._pixmap is None:
            # No frame yet — draw placeholder
            painter.fillRect(self.rect(), QColor("#111111"))
            painter.setPen(QColor("#444444"))
            font = painter.font()
            font.setPointSize(12)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Waiting for camera…")
            painter.end()
            return

        # Scale pixmap to widget, preserving aspect ratio, centred
        scaled = self._pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        x_off = (self.width()  - scaled.width())  // 2
        y_off = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x_off, y_off, scaled)

        # Paint ROI overlay on the area the image occupies
        image_rect = QRect(x_off, y_off, scaled.width(), scaled.height())
        self._overlay.paint(painter, image_rect, self._binning)

        painter.end()
