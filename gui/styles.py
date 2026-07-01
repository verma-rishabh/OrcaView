"""
Dark theme QSS stylesheet for the ORCA-Quest GUI.
Apply once via: app.setStyleSheet(DARK_THEME)
"""

DARK_THEME = """
/* ── Base ─────────────────────────────────────────────────────── */
QWidget {
    background-color: #1e1e1e;
    color: #d4d4d4;
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 9pt;
}
QMainWindow, QDialog {
    background-color: #1e1e1e;
}
QSplitter::handle {
    background-color: #2a2a2a;
    border-left: 1px solid #3a3a3a;
    border-right: 1px solid #3a3a3a;
}
QSplitter::handle:hover {
    background-color: #264f78;
}
QSplitter::handle:pressed {
    background-color: #1e3f60;
}

/* ── Buttons ───────────────────────────────────────────────────── */
QPushButton {
    background-color: #3a3a3a;
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 5px 14px;
    color: #d4d4d4;
    min-height: 22px;
}
QPushButton:hover  { background-color: #4a4a4a; border-color: #777; }
QPushButton:pressed { background-color: #2a2a2a; }
QPushButton:disabled { color: #555555; background-color: #2d2d2d; border-color: #3a3a3a; }

/* Preset buttons — prominent */
QPushButton#btn_live_preset {
    background-color: #1a3a5c;
    border-color: #2a6aa0;
    color: #9cd6ff;
    font-weight: bold;
    font-size: 10pt;
    padding: 8px 14px;
}
QPushButton#btn_live_preset:hover { background-color: #1e4a70; }

QPushButton#btn_record_preset {
    background-color: #3a1a1a;
    border-color: #a02a2a;
    color: #ff9cd6;
    font-weight: bold;
    font-size: 10pt;
    padding: 8px 14px;
}
QPushButton#btn_record_preset:hover { background-color: #501e1e; }

/* Action buttons */
QPushButton#btn_start_live {
    background-color: #1a5c2a;
    border-color: #2a9a44;
    color: #9dffb8;
    font-weight: bold;
}
QPushButton#btn_start_live:hover { background-color: #1e7034; }

QPushButton#btn_stop_live {
    background-color: #5c1a1a;
    border-color: #9a2a2a;
    color: #ffb8b8;
    font-weight: bold;
}
QPushButton#btn_stop_live:hover { background-color: #701e1e; }

QPushButton#btn_start_rec {
    background-color: #5c3a1a;
    border-color: #9a6a2a;
    color: #ffd8b8;
    font-weight: bold;
}
QPushButton#btn_start_rec:hover { background-color: #704a1e; }

QPushButton#btn_stop_rec {
    background-color: #5c1a1a;
    border-color: #9a2a2a;
    color: #ffb8b8;
    font-weight: bold;
}
QPushButton#btn_stop_rec:hover { background-color: #701e1e; }

QPushButton#btn_save_settings {
    background-color: #2a2a4a;
    border-color: #4a4a8a;
    color: #b8b8ff;
}
QPushButton#btn_save_settings:hover { background-color: #34345c; }

/* ── Inputs ────────────────────────────────────────────────────── */
QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: #2d2d2d;
    border: 1px solid #555555;
    border-radius: 3px;
    padding: 3px 6px;
    color: #d4d4d4;
    selection-background-color: #264f78;
}
QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border-color: #569cd6;
}
QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {
    color: #555555;
    background-color: #252525;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background-color: #3a3a3a;
    border: none;
    width: 16px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #505050;
}

QComboBox {
    background-color: #2d2d2d;
    border: 1px solid #555555;
    border-radius: 3px;
    padding: 3px 6px;
    color: #d4d4d4;
}
QComboBox:focus { border-color: #569cd6; }
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #2d2d2d;
    border: 1px solid #555555;
    selection-background-color: #264f78;
    color: #d4d4d4;
}

/* ── Sliders ───────────────────────────────────────────────────── */
QSlider::groove:horizontal {
    background: #3a3a3a;
    height: 4px;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #569cd6;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: #79b8e8; }
QSlider::sub-page:horizontal {
    background: #264f78;
    border-radius: 2px;
}

/* ── CheckBox ──────────────────────────────────────────────────── */
QCheckBox {
    spacing: 6px;
    color: #d4d4d4;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #555555;
    border-radius: 3px;
    background-color: #2d2d2d;
}
QCheckBox::indicator:checked {
    background-color: #264f78;
    border-color: #569cd6;
}
QCheckBox::indicator:hover { border-color: #777; }

/* ── GroupBox ──────────────────────────────────────────────────── */
QGroupBox {
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 6px;
    color: #999999;
    font-size: 8pt;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: #888888;
}

/* ── Labels ────────────────────────────────────────────────────── */
QLabel {
    background: transparent;
    color: #d4d4d4;
}
QLabel#lbl_section {
    color: #888888;
    font-size: 8pt;
    text-transform: uppercase;
}

/* ── ScrollBar ─────────────────────────────────────────────────── */
QScrollBar:vertical {
    background: #252525;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #555555;
    border-radius: 5px;
    min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QScrollArea {
    border: none;
    background-color: #1e1e1e;
}
QScrollArea > QWidget > QWidget { background-color: #1e1e1e; }

/* ── StatusBar ─────────────────────────────────────────────────── */
QStatusBar {
    background-color: #252526;
    border-top: 1px solid #3a3a3a;
    color: #888888;
    font-size: 8pt;
}
QStatusBar QLabel {
    padding: 0 8px;
    color: #888888;
}

/* ── ProgressBar ───────────────────────────────────────────────── */
QProgressBar {
    background-color: #2d2d2d;
    border: 1px solid #555555;
    border-radius: 3px;
    text-align: center;
    color: #d4d4d4;
    height: 14px;
}
QProgressBar::chunk {
    background-color: #264f78;
    border-radius: 2px;
}

/* ── Tooltips ──────────────────────────────────────────────────── */
QToolTip {
    background-color: #2d2d2d;
    border: 1px solid #555555;
    color: #d4d4d4;
    padding: 4px;
}
"""
