"""
Custom status bar showing: mode indicator, live FPS, frame timestamp, dropped frames.
"""
from __future__ import annotations
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QStatusBar, QLabel


_MODE_COLORS = {
    "IDLE":      "#888888",
    "LIVE":      "#4ec94e",
    "RECORDING": "#e05c5c",
}


class CameraStatusBar(QStatusBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizeGripEnabled(False)

        self._mode_label      = QLabel("● IDLE")
        self._fps_label       = QLabel("FPS: —")
        self._ts_label        = QLabel("TS: —")
        self._dropped_label   = QLabel("Dropped: 0")
        self._info_label      = QLabel("")

        for lbl in (self._mode_label, self._fps_label,
                    self._ts_label, self._dropped_label):
            lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            self.addPermanentWidget(lbl)

        self.addWidget(self._info_label)
        self.set_mode("IDLE")

    # ------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        color = _MODE_COLORS.get(mode, "#888888")
        self._mode_label.setText(f"● {mode}")
        self._mode_label.setStyleSheet(
            f"color: {color}; font-weight: bold; padding: 0 12px;"
        )

    def update_stats(self, fps: float, timestamp: float, dropped: int) -> None:
        self._fps_label.setText(f"FPS: {fps:>7.1f}")
        if timestamp > 0:
            dt = datetime.fromtimestamp(timestamp)
            self._ts_label.setText(
                f"TS: {dt.strftime('%H:%M:%S')}.{dt.microsecond // 1000:03d}"
            )
        self._dropped_label.setText(
            f"Dropped: {dropped}"
            if dropped == 0
            else f"⚠ Dropped: {dropped}"
        )
        if dropped > 0:
            self._dropped_label.setStyleSheet("color: #e05c5c; padding: 0 8px;")
        else:
            self._dropped_label.setStyleSheet("color: #888888; padding: 0 8px;")

    def set_info(self, text: str) -> None:
        self._info_label.setText(text)
