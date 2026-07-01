"""
Entry point for the ORCA-Quest C15550-20UP camera GUI.

Run from the project root:
    python main.py

Requirements: PyQt6, numpy
Hardware:     DCAM-SDK4 installed, camera connected via CoaXPress
"""
import sys
import os

# Ensure project root is on the path regardless of CWD
sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from gui.main_window import MainWindow
from gui.styles import DARK_THEME


def main() -> None:
    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("ORCA-Quest Controller")
    app.setOrganizationName("Lab")
    app.setStyleSheet(DARK_THEME)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
