from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSplitter, QListWidget, QTableWidget, QPlainTextEdit
from PySide6.QtCore import Qt


class DialoguePanel(QWidget):
    """ダイアログ（DIAL/INFO）専用タブパネル"""

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Vertical)

        self._dial_list   = QListWidget()
        self._info_grid   = QTableWidget()
        self._field_grid  = QTableWidget()
        self._text_panel  = QPlainTextEdit()

        splitter.addWidget(self._dial_list)
        splitter.addWidget(self._info_grid)
        splitter.addWidget(self._field_grid)
        splitter.addWidget(self._text_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 3)
        splitter.setStretchFactor(3, 2)

        layout.addWidget(splitter)

    def refresh(self) -> None:
        pass  # TODO: DIAL/INFO読込み
