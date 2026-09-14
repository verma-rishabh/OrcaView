"""
ROI overlay painted on top of the live view.
Draws a semi-transparent rectangle showing the current ROI relative
to the full sensor, scaled to the widget display area.

The widget_rect passed to paint() represents the full sensor (4096×2304)
mapped onto the widget.  The ROI is drawn as a sub-rect within it.
"""
from __future__ import annotations

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen

# Full sensor dimensions (ORCA-Quest C15550-20UP)
SENSOR_W = 4096
SENSOR_H = 2304


class ROIOverlay:

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
        widget_rect  — the QRect where the full sensor maps on the widget
                       (already zoom/pan adjusted by LiveViewWidget)
        """
        if not self._visible:
            return

        x, y, w, h = self._roi

        # Only draw the overlay if ROI is not the full sensor
        if x == 0 and y == 0 and w == SENSOR_W and h == SENSOR_H:
            return

        # Map ROI from sensor coords to widget coords
        rx = widget_rect.x() + (x / SENSOR_W) * widget_rect.width()
        ry = widget_rect.y() + (y / SENSOR_H) * widget_rect.height()
        rw = (w / SENSOR_W) * widget_rect.width()
        rh = (h / SENSOR_H) * widget_rect.height()

        # ROI border
        painter.save()
        pen = QPen(QColor("#569cd6"), 1.5, Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(rx, ry, rw, rh))

        # Corner label
        painter.setPen(QColor("#569cd6"))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        label = f"ROI {w}\u00d7{h}  @({x},{y})"
        painter.drawText(int(rx) + 4, int(ry) + 14, label)
        painter.restore()
