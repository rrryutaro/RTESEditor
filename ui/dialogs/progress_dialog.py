from __future__ import annotations
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar
from PySide6.QtCore import Qt


class ProgressDialog(QDialog):

    def __init__(self, parent=None, title: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.ApplicationModal)
        self.setFixedSize(400, 100)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._label = QLabel()
        self._bar   = QProgressBar()
        self._bar.setRange(0, 100)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)

    def set_label(self, text: str) -> None:
        self._label.setText(text)

    def set_value(self, current: int, total: int) -> None:
        if total > 0:
            self._bar.setValue(int(current / total * 100))
