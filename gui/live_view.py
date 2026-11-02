"""
LiveViewWidget — displays camera frames using QPainter.

Display model:
    The widget always renders relative to the **full sensor** (4096×2304).
    A frozen background pixmap (captured from live mode) provides spatial
    reference.  The current camera frame (possibly an ROI crop) is composited
    at its correct sensor position on top of the background.

    Zoom (mouse wheel, cursor-centered) and pan (middle-drag) transform the
    full-sensor rectangle within the widget.

Overlays (painted after the image):
    1. ROI rectangle (roi_overlay.py)
    2. Ghost ROI preview during drag (roi_interaction.py)
    3. Cell markers (cell_markers.py)
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, pyqtSignal
from PyQt6.QtGui import QImage, QPainter, QPixmap, QColor
from PyQt6.QtWidgets import QWidget

from gui.roi_overlay import ROIOverlay, SENSOR_W, SENSOR_H
from gui.cell_markers import CellMarkerOverlay
from gui.roi_interaction import ROIInteractionHandler, Zone


_DRAG_THRESHOLD = 5  # pixels before a press becomes a drag


class LiveViewWidget(QWidget):
    roi_drag_update   = pyqtSignal(tuple)
    roi_drag_finished = pyqtSignal(tuple)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap:        QPixmap | None = None
        self._overlay:       ROIOverlay     = ROIOverlay()
        self._markers:       CellMarkerOverlay = CellMarkerOverlay()
        self._roi_handler:   ROIInteractionHandler = ROIInteractionHandler()
        self._binning:       int            = 1
        self._gamma:         float          = 1.0   # display gamma
        self._auto_gamma:    bool           = False
        self._ag_interval:   int            = 5     # recompute every N frames
        self._ag_counter:    int            = 0
        self._ag_lo:         float          = 0.0   # auto-computed percentile range
        self._ag_hi:         float          = 0.0

        # Full-sensor coordinate system
        self._roi_sensor: tuple[int, int, int, int] = (0, 0, SENSOR_W, SENSOR_H)
        self._background_pixmap: QPixmap | None = None
        self._bg_roi: tuple[int, int, int, int] = (0, 0, SENSOR_W, SENSOR_H)
        self._image_rect: QRect = QRect()  # full-sensor rect on widget (after zoom/pan)

        # Zoom & pan
        self._zoom: float = 1.0
        self._pan: QPointF = QPointF(0.0, 0.0)
        self._pan_start: QPoint | None = None
        self._pan_start_offset: QPointF = QPointF(0.0, 0.0)

        # Mouse drag state
        self._press_pos:    QPoint | None = None
        self._is_drag:      bool          = False
        self._pending_zone: Zone          = Zone.NONE

        # Marker drawing drag state (click center, drag to set radius)
        self._marker_center: tuple[float, float] | None = None  # (norm_x, norm_y)
        self._marker_preview_radius: int = 0  # widget pixels

        # Mode toggle
        self._roi_mode: bool = True

        self.setMinimumSize(640, 360)
        self.setStyleSheet("background-color: #111111;")
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_gamma(self, value: float) -> None:
        """Software display gamma (1.0 = linear)."""
        self._gamma = max(0.1, min(4.0, value))

    def set_auto_gamma(self, enabled: bool) -> None:
        self._auto_gamma = enabled
        self._ag_counter = 0
        if not enabled:
            self._ag_lo, self._ag_hi = 0.0, 0.0

    def set_auto_gamma_interval(self, n: int) -> None:
        self._ag_interval = max(1, n)
        self._ag_counter = 0

    def update_frame(self, pixels: np.ndarray) -> None:
        # Auto gamma: periodically compute optimal gamma from percentile range
        if self._auto_gamma:
            self._ag_counter += 1
            if self._ag_counter >= self._ag_interval:
                self._ag_counter = 0
                sample = pixels[::8, ::8]
                lo = float(np.percentile(sample, 0.5))
                hi = float(np.percentile(sample, 99.5))
                if hi > lo:
                    self._ag_lo, self._ag_hi = lo, hi

        # Convert 16-bit to float [0, 1]
        if self._ag_hi > self._ag_lo:
            # Auto: stretch percentile range to [0, 1] first
            frame = np.clip(
                (pixels.astype(np.float32) - self._ag_lo) / (self._ag_hi - self._ag_lo),
                0.0, 1.0,
            )
        else:
            frame = pixels.astype(np.float32) / 65535.0

        # Apply gamma power curve
        gamma = self._gamma
        if abs(gamma - 1.0) > 1e-3:
            np.power(frame, 1.0 / gamma, out=frame)

        arr8 = (frame * 255.0).astype(np.uint8)
        h, w = arr8.shape
        qimg = QImage(arr8.data, w, h, w, QImage.Format.Format_Grayscale8)
        self._pixmap = QPixmap.fromImage(qimg)
        self.update()

    def set_roi(self, x: int, y: int, w: int, h: int) -> None:
        self._overlay.set_roi(x, y, w, h)
        self._roi_sensor = (x, y, w, h)
        self.update()

    def set_binning(self, binning: int) -> None:
        self._binning = binning

    def set_overlay_visible(self, visible: bool) -> None:
        self._overlay.set_visible(visible)
        self.update()

    def set_roi_mode(self, enabled: bool) -> None:
        self._roi_mode = enabled

    def set_marker_radius(self, radius: int) -> None:
        self._markers.set_radius(radius)

    def clear_markers(self) -> None:
        self._markers.clear()
        self.update()

    def capture_background(self) -> None:
        """Freeze the current frame as a background reference."""
        if self._pixmap is not None:
            self._background_pixmap = self._pixmap.copy()
            self._bg_roi = self._roi_sensor

    def clear_background(self) -> None:
        self._background_pixmap = None

    def reset_zoom(self) -> None:
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _compute_image_rect(self) -> QRect:
        """Compute where the full sensor (4096×2304) maps on the widget,
        accounting for zoom and pan."""
        ww, wh = self.width(), self.height()
        # Base: aspect-ratio fit of sensor to widget, centered
        scale = min(ww / SENSOR_W, wh / SENSOR_H)
        bw = SENSOR_W * scale
        bh = SENSOR_H * scale
        bx = (ww - bw) / 2
        by = (wh - bh) / 2
        # Apply zoom around widget center
        cx, cy = ww / 2.0, wh / 2.0
        zw = bw * self._zoom
        zh = bh * self._zoom
        zx = cx - (cx - bx) * self._zoom
        zy = cy - (cy - by) * self._zoom
        # Apply pan
        zx += self._pan.x()
        zy += self._pan.y()
        return QRect(int(zx), int(zy), int(zw), int(zh))

    def _sensor_sub_rect(self, image_rect: QRect,
                         roi: tuple[int, int, int, int]) -> QRectF:
        """Map an ROI (sensor coords) to a widget sub-rect within image_rect."""
        x, y, w, h = roi
        rx = image_rect.x() + (x / SENSOR_W) * image_rect.width()
        ry = image_rect.y() + (y / SENSOR_H) * image_rect.height()
        rw = (w / SENSOR_W) * image_rect.width()
        rh = (h / SENSOR_H) * image_rect.height()
        return QRectF(rx, ry, rw, rh)

    def _widget_to_sensor_norm(self, pos: QPoint) -> tuple[float, float]:
        """Convert widget position to normalized sensor coords (0–1)."""
        ir = self._image_rect
        if ir.width() < 1 or ir.height() < 1:
            return (-1.0, -1.0)
        nx = (pos.x() - ir.x()) / ir.width()
        ny = (pos.y() - ir.y()) / ir.height()
        return (nx, ny)

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_start = event.pos()
            self._pan_start_offset = QPointF(self._pan)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.pos()
            self._is_drag = False
            if self._roi_mode:
                self._pending_zone = self._roi_handler.hit_test(event.pos())
            else:
                self._pending_zone = Zone.NONE
        elif event.button() == Qt.MouseButton.RightButton:
            nx, ny = self._widget_to_sensor_norm(event.pos())
            if 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0:
                if self._markers.remove_nearest(nx, ny, self._image_rect):
                    self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.pos()
        # Middle-button pan
        if self._pan_start is not None:
            delta = pos - self._pan_start
            self._pan = QPointF(
                self._pan_start_offset.x() + delta.x(),
                self._pan_start_offset.y() + delta.y(),
            )
            self.update()
            return
        # Left button held
        if self._press_pos is not None:
            if not self._is_drag:
                delta = pos - self._press_pos
                if abs(delta.x()) > _DRAG_THRESHOLD or abs(delta.y()) > _DRAG_THRESHOLD:
                    if self._pending_zone != Zone.NONE:
                        # ROI mode drag
                        self._is_drag = True
                        self._roi_handler.begin_drag(self._press_pos, self._pending_zone)
                    elif not self._roi_mode:
                        # Marker mode drag — set center and start sizing
                        self._is_drag = True
                        nx, ny = self._widget_to_sensor_norm(self._press_pos)
                        if 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0:
                            self._marker_center = (nx, ny)
            if self._is_drag:
                if self._marker_center is not None:
                    # Compute radius in widget pixels from center to cursor
                    ir = self._image_rect
                    cx_w = ir.x() + self._marker_center[0] * ir.width()
                    cy_w = ir.y() + self._marker_center[1] * ir.height()
                    dx = pos.x() - cx_w
                    dy = pos.y() - cy_w
                    self._marker_preview_radius = max(3, int((dx**2 + dy**2) ** 0.5))
                    self.update()
                else:
                    roi = self._roi_handler.update_drag(pos)
                    self.roi_drag_update.emit(roi)
                    self.update()
        else:
            # No button — update cursor
            if self._roi_mode:
                zone = self._roi_handler.hit_test(pos)
                self.setCursor(ROIInteractionHandler.cursor_for_zone(zone))
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_start = None
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_drag:
                if self._marker_center is not None:
                    # Finish marker drag — convert widget-pixel radius to
                    # normalized (fraction of sensor width) so it scales with zoom
                    nx, ny = self._marker_center
                    ir = self._image_rect
                    radius_norm = max(3, self._marker_preview_radius) / max(1, ir.width())
                    self._markers.add_marker_with_radius(nx, ny, radius_norm)
                    self._marker_center = None
                    self._marker_preview_radius = 0
                    self.update()
                else:
                    roi = self._roi_handler.end_drag()
                    if roi is not None:
                        self.roi_drag_finished.emit(roi)
                    self.update()
            elif self._press_pos is not None and not self._roi_mode:
                # Click without drag — place marker with default radius
                nx, ny = self._widget_to_sensor_norm(event.pos())
                if 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0:
                    self._markers.add_marker(nx, ny)
                    self.update()
            self._press_pos = None
            self._is_drag = False
            self._pending_zone = Zone.NONE
            self._marker_center = None
            self._marker_preview_radius = 0

    def wheelEvent(self, event) -> None:
        # Cursor-centered zoom
        pos = event.position()
        old_zoom = self._zoom
        factor = 1.1
        if event.angleDelta().y() > 0:
            self._zoom = min(50.0, self._zoom * factor)
        else:
            self._zoom = max(0.1, self._zoom / factor)
        # Adjust pan so the sensor point under cursor stays fixed
        ratio = self._zoom / old_zoom
        cx, cy = pos.x(), pos.y()
        wc_x, wc_y = self.width() / 2.0, self.height() / 2.0
        self._pan = QPointF(
            cx - ratio * (cx - wc_x - self._pan.x()) - wc_x,
            cy - ratio * (cy - wc_y - self._pan.y()) - wc_y,
        )
        self.update()

    def leaveEvent(self, event) -> None:
        self.unsetCursor()

    # ------------------------------------------------------------------
    # Qt paint event
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)

        if self._pixmap is None and self._background_pixmap is None:
            painter.fillRect(self.rect(), QColor("#111111"))
            painter.setPen(QColor("#444444"))
            font = painter.font()
            font.setPointSize(12)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Waiting for camera...")
            painter.end()
            return

        # Full-sensor rectangle on widget (with zoom/pan)
        image_rect = self._compute_image_rect()
        self._image_rect = image_rect

        # Dark background
        painter.fillRect(self.rect(), QColor("#111111"))

        # 1. Background image (frozen reference from live mode)
        if self._background_pixmap is not None:
            bg_sub = self._sensor_sub_rect(image_rect, self._bg_roi)
            painter.drawPixmap(bg_sub.toRect(), self._background_pixmap)

        # 2. Current frame at its sensor position
        if self._pixmap is not None:
            frame_sub = self._sensor_sub_rect(image_rect, self._roi_sensor)
            painter.drawPixmap(frame_sub.toRect(), self._pixmap)

        # 3. ROI overlay
        self._overlay.paint(painter, image_rect, self._binning)

        # 4. Update ROI handler geometry for hit-testing
        self._roi_handler.set_geometry(
            image_rect,
            self._roi_sensor,
            self._binning,
        )

        # 5. Ghost ROI preview during drag
        if self._roi_handler.is_dragging:
            self._roi_handler.paint_preview(painter, image_rect)

        # 6. Cell markers (always on top)
        self._markers.paint(painter, image_rect)

        # 7. Marker drag preview (circle being drawn)
        if self._marker_center is not None and self._marker_preview_radius > 0:
            painter.save()
            from PyQt6.QtGui import QPen
            painter.setPen(QPen(QColor("#ff6b6b"), 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            nx, ny = self._marker_center
            cx = image_rect.x() + nx * image_rect.width()
            cy = image_rect.y() + ny * image_rect.height()
            r = self._marker_preview_radius
            painter.drawEllipse(int(cx - r), int(cy - r), r * 2, r * 2)
            painter.restore()

        painter.end()
