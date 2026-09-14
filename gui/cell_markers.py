"""
CellMarkerOverlay — hollow circle markers placed on the live view.

Markers are stored as normalized coordinates (0.0–1.0) relative to the
full sensor image, so they survive ROI and window resizes.

Radius is stored as a fraction of the sensor width so circles scale
correctly with zoom — they stay locked to the same cells.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPen


SENSOR_W = 4096

_DEFAULT_RADIUS_SENSOR = 12   # default radius in sensor pixels
_HIT_PAD               = 5    # extra widget-pixel padding for hit-test
_COLOR                  = QColor("#ff6b6b")
_PEN_W                  = 1.5


class CellMarkerOverlay:

    def __init__(self):
        # (norm_x, norm_y, radius_norm) — radius_norm is fraction of sensor width
        self._markers: list[tuple[float, float, float]] = []
        self._default_radius_sensor: int = _DEFAULT_RADIUS_SENSOR

    def set_radius(self, radius_sensor_px: int) -> None:
        """Set default radius in sensor pixels (used for click-to-place)."""
        self._default_radius_sensor = max(1, radius_sensor_px)

    def add_marker(self, norm_x: float, norm_y: float) -> None:
        """Place a marker with the current default radius."""
        r_norm = self._default_radius_sensor / SENSOR_W
        self._markers.append((norm_x, norm_y, r_norm))

    def add_marker_with_radius(self, norm_x: float, norm_y: float,
                               radius_norm: float) -> None:
        """Place a marker with a specific normalized radius."""
        self._markers.append((norm_x, norm_y, radius_norm))

    def hit_test(self, norm_x: float, norm_y: float,
                 image_rect: QRect) -> int | None:
        """Return index of marker under (norm_x, norm_y), or None."""
        if not self._markers:
            return None
        best_i = -1
        best_d = float("inf")
        for i, (mx, my, r_norm) in enumerate(self._markers):
            dx = (mx - norm_x) * image_rect.width()
            dy = (my - norm_y) * image_rect.height()
            d = math.hypot(dx, dy)
            if d < best_d:
                best_d = d
                best_i = i
        if best_i < 0:
            return None
        # Hit radius = marker's widget radius + padding
        widget_r = self._markers[best_i][2] * image_rect.width()
        if best_d <= widget_r + _HIT_PAD:
            return best_i
        return None

    def remove_nearest(self, norm_x: float, norm_y: float,
                       image_rect: QRect) -> bool:
        """Remove the marker closest to (norm_x, norm_y) if within hit radius.
        Returns True if a marker was removed."""
        idx = self.hit_test(norm_x, norm_y, image_rect)
        if idx is not None:
            self._markers.pop(idx)
            return True
        return False

    def clear(self) -> None:
        self._markers.clear()

    def paint(self, painter: QPainter, image_rect: QRect) -> None:
        if not self._markers:
            return
        painter.save()
        painter.setPen(QPen(_COLOR, _PEN_W))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for nx, ny, r_norm in self._markers:
            cx = image_rect.x() + nx * image_rect.width()
            cy = image_rect.y() + ny * image_rect.height()
            wr = r_norm * image_rect.width()   # widget-pixel radius, scales with zoom
            painter.drawEllipse(int(cx - wr), int(cy - wr),
                                int(wr * 2), int(wr * 2))
        painter.restore()
