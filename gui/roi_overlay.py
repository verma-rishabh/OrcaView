"""
ROI overlay painted on top of the live view.
Draws a semi-transparent rectangle showing the current ROI relative
to the full sensor, scaled to the widget display area.
"""
from __future__ import annotations

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen

# Full sensor dimensions (ORCA-Quest C15550-20UP)
SENSOR_W = 4096
SENSOR_H = 2304


class ROIOverlay:
    """
    Call paint() from LiveViewWidget.paintEvent() after drawing the frame.
    """

    def __init__(self):
        self._roi: tuple[int, int, int, int] = (0, 0, SENSOR_W, SENSOR_H)
        self._visible = True

    def set_roi(self, x: int, y: int, w: int, h: int) -> None:
        self._roi = (x, y, w, h)

    def set_visible(self, visible: bool) -> None:
        self._visible = visible

    def paint(self, painter: QPainter,
              widget_rect: QRect,
              binning: int = 1) -> None:
        """
        widget_rect  — the actual QRect the image is drawn into
                       (already aspect-ratio-corrected by LiveViewWidget)
        binning      — current binning factor; scales sensor coords
        """
        if not self._visible:
            return

        sensor_w = SENSOR_W // binning
        sensor_h = SENSOR_H // binning
        x, y, w, h = self._roi

        # Only draw the overlay if ROI is not the full sensor
        full = (x == 0 and y == 0 and w == sensor_w and h == sensor_h)
        if full:
            return

        # The camera captures only the ROI crop — the displayed image IS the
        # ROI, not the full sensor — so the ROI border maps to the entire
        # image_rect (not a sensor-scaled sub-rectangle).
        rx = widget_rect.x()
        ry = widget_rect.y()
        rw = widget_rect.width()
        rh = widget_rect.height()

        # ROI border
        pen = QPen(QColor("#569cd6"), 1.5, Qt.PenStyle.SolidLine)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rx, ry, rw, rh)

        # Corner label — include sensor offset so the user knows where on the
        # sensor this ROI sits.
        painter.setPen(QColor("#569cd6"))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        label = f"ROI {w}×{h}  @({x},{y})"
        painter.drawText(rx + 4, ry + 14, label)
        painter.restore()
