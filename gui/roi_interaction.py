"""
ROIInteractionHandler — drag-to-move / resize ROI on the live view.

All ROI values are in **sensor pixel coordinates** (0–4096 x 0–2304).
The image_rect passed to set_geometry() represents the full sensor
mapped onto the widget (with zoom/pan).  Hit-testing and drag math
map between widget pixels and sensor pixels using this rect.

Binning-aware alignment is handled by CameraSettings.align_roi().
The SDK coordinate conversion (÷ binning) happens downstream in
camera.py:apply_settings().
"""
from __future__ import annotations

from enum import IntEnum, auto

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen

from core.settings import CameraSettings

SENSOR_W = 4096
SENSOR_H = 2304
_EDGE_PX = 10   # edge hit-test margin in widget pixels


class Zone(IntEnum):
    NONE   = auto()
    MOVE   = auto()
    LEFT   = auto()
    RIGHT  = auto()
    TOP    = auto()
    BOTTOM = auto()
    TL     = auto()
    TR     = auto()
    BL     = auto()
    BR     = auto()


class ROIInteractionHandler:

    def __init__(self):
        self._image_rect = QRect()
        self._roi = (0, 0, SENSOR_W, SENSOR_H)
        self._binning = 1
        self._dragging = False
        self._zone = Zone.NONE
        self._start_pos = QPoint()
        self._start_roi = (0, 0, SENSOR_W, SENSOR_H)
        self._preview_roi: tuple[int, int, int, int] | None = None

    def set_geometry(self, image_rect: QRect,
                     roi: tuple[int, int, int, int],
                     binning: int) -> None:
        self._image_rect = image_rect
        self._roi = roi
        self._binning = binning

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _roi_to_widget(self) -> tuple[float, float, float, float]:
        """Map current ROI from sensor coords to widget pixel coords."""
        ir = self._image_rect
        x, y, w, h = self._roi
        rx = ir.x() + (x / SENSOR_W) * ir.width()
        ry = ir.y() + (y / SENSOR_H) * ir.height()
        rw = (w / SENSOR_W) * ir.width()
        rh = (h / SENSOR_H) * ir.height()
        return rx, ry, rw, rh

    # ------------------------------------------------------------------
    # Hit testing
    # ------------------------------------------------------------------

    def hit_test(self, widget_pos: QPoint) -> Zone:
        ir = self._image_rect
        if ir.width() < 1 or ir.height() < 1:
            return Zone.NONE

        rx, ry, rw, rh = self._roi_to_widget()
        px, py = widget_pos.x(), widget_pos.y()

        # Must be near the ROI rectangle
        expanded = QRectF(rx - _EDGE_PX, ry - _EDGE_PX,
                          rw + 2 * _EDGE_PX, rh + 2 * _EDGE_PX)
        if not expanded.contains(float(px), float(py)):
            return Zone.NONE

        on_left   = abs(px - rx)        <= _EDGE_PX
        on_right  = abs(px - (rx + rw)) <= _EDGE_PX
        on_top    = abs(py - ry)        <= _EDGE_PX
        on_bottom = abs(py - (ry + rh)) <= _EDGE_PX

        if on_left  and on_top:    return Zone.TL
        if on_right and on_top:    return Zone.TR
        if on_left  and on_bottom: return Zone.BL
        if on_right and on_bottom: return Zone.BR
        if on_left:   return Zone.LEFT
        if on_right:  return Zone.RIGHT
        if on_top:    return Zone.TOP
        if on_bottom: return Zone.BOTTOM
        if QRectF(rx, ry, rw, rh).contains(float(px), float(py)):
            return Zone.MOVE
        return Zone.NONE

    @staticmethod
    def cursor_for_zone(zone: Zone):
        return {
            Zone.NONE:   Qt.CursorShape.ArrowCursor,
            Zone.MOVE:   Qt.CursorShape.OpenHandCursor,
            Zone.LEFT:   Qt.CursorShape.SizeHorCursor,
            Zone.RIGHT:  Qt.CursorShape.SizeHorCursor,
            Zone.TOP:    Qt.CursorShape.SizeVerCursor,
            Zone.BOTTOM: Qt.CursorShape.SizeVerCursor,
            Zone.TL:     Qt.CursorShape.SizeFDiagCursor,
            Zone.BR:     Qt.CursorShape.SizeFDiagCursor,
            Zone.TR:     Qt.CursorShape.SizeBDiagCursor,
            Zone.BL:     Qt.CursorShape.SizeBDiagCursor,
        }.get(zone, Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------------------
    # Drag lifecycle
    # ------------------------------------------------------------------

    def begin_drag(self, widget_pos: QPoint, zone: Zone) -> None:
        self._dragging = True
        self._zone = zone
        self._start_pos = QPoint(widget_pos)
        self._start_roi = self._roi
        self._preview_roi = None

    def update_drag(self, widget_pos: QPoint) -> tuple[int, int, int, int]:
        ir = self._image_rect
        if ir.width() < 1 or ir.height() < 1:
            return self._roi

        # Widget pixels → sensor pixels
        sx = SENSOR_W / ir.width()
        sy = SENSOR_H / ir.height()
        dx = (widget_pos.x() - self._start_pos.x()) * sx
        dy = (widget_pos.y() - self._start_pos.y()) * sy

        ox, oy, ow, oh = self._start_roi
        b = self._binning
        min_w = 4 * b
        min_h = 4 * b

        z = self._zone
        if z == Zone.MOVE:
            nx = int(ox + dx)
            ny = int(oy + dy)
            nx = max(0, min(nx, SENSOR_W - ow))
            ny = max(0, min(ny, SENSOR_H - oh))
            result = (nx, ny, ow, oh)
        else:
            left, top, right, bottom = ox, oy, ox + ow, oy + oh
            if z in (Zone.LEFT, Zone.TL, Zone.BL):
                left = int(left + dx)
            if z in (Zone.RIGHT, Zone.TR, Zone.BR):
                right = int(right + dx)
            if z in (Zone.TOP, Zone.TL, Zone.TR):
                top = int(top + dy)
            if z in (Zone.BOTTOM, Zone.BL, Zone.BR):
                bottom = int(bottom + dy)
            left   = max(0, min(left,   SENSOR_W - min_w))
            top    = max(0, min(top,    SENSOR_H - min_h))
            right  = max(min_w, min(right,  SENSOR_W))
            bottom = max(min_h, min(bottom, SENSOR_H))
            if right - left < min_w:
                if z in (Zone.LEFT, Zone.TL, Zone.BL):
                    left = right - min_w
                else:
                    right = left + min_w
            if bottom - top < min_h:
                if z in (Zone.TOP, Zone.TL, Zone.TR):
                    top = bottom - min_h
                else:
                    bottom = top + min_h
            result = (left, top, right - left, bottom - top)

        s = CameraSettings(roi_x=result[0], roi_y=result[1],
                           roi_width=result[2], roi_height=result[3],
                           binning=b)
        s.align_roi()
        aligned = (s.roi_x, s.roi_y, s.roi_width, s.roi_height)
        self._preview_roi = aligned
        return aligned

    def end_drag(self) -> tuple[int, int, int, int] | None:
        if not self._dragging:
            return None
        self._dragging = False
        result = self._preview_roi
        self._preview_roi = None
        self._zone = Zone.NONE
        return result

    @property
    def is_dragging(self) -> bool:
        return self._dragging

    # ------------------------------------------------------------------
    # Ghost preview painting
    # ------------------------------------------------------------------

    def paint_preview(self, painter: QPainter, image_rect: QRect) -> None:
        if not self._dragging or self._preview_roi is None:
            return
        # Map preview ROI from sensor coords to widget coords
        x, y, w, h = self._preview_roi
        px = image_rect.x() + (x / SENSOR_W) * image_rect.width()
        py = image_rect.y() + (y / SENSOR_H) * image_rect.height()
        pw = (w / SENSOR_W) * image_rect.width()
        ph = (h / SENSOR_H) * image_rect.height()

        painter.save()
        pen = QPen(QColor(86, 156, 214, 180), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(86, 156, 214, 30))
        painter.drawRect(QRectF(px, py, pw, ph))
        painter.restore()
