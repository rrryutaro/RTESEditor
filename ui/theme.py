from __future__ import annotations
from PySide6.QtWidgets import QApplication

_DARK_QSS = """
QWidget {
    background-color: #1C2E3F;
    color: #C9D1E0;
}
QMainWindow, QDialog {
    background-color: #0D1B2A;
}
QMenuBar {
    background-color: #1C2E3F;
    color: #C9D1E0;
}
QMenuBar::item:selected {
    background-color: #2E4057;
}
QMenu {
    background-color: #1C2E3F;
    color: #C9D1E0;
    border: 1px solid #2E4057;
}
QMenu::item:selected {
    background-color: #2E4057;
}
QToolBar {
    background-color: #1C2E3F;
    border: none;
}
QStatusBar {
    background-color: #1C2E3F;
    color: #C9D1E0;
}
QTabWidget::pane {
    border: 1px solid #2E4057;
}
QTabBar::tab {
    background-color: #1C2E3F;
    color: #C9D1E0;
    padding: 4px 10px;
    border: 1px solid #2E4057;
}
QTabBar::tab:selected {
    background-color: #2E4057;
}
QSplitter::handle {
    background-color: #2E4057;
}
QTreeWidget {
    background-color: #0D1B2A;
    alternate-background-color: #1C2E3F;
    border: 1px solid #2E4057;
}
QTreeWidget::item:selected {
    background-color: #2E4057;
}
QTableWidget {
    background-color: #0D1B2A;
    alternate-background-color: #132233;
    gridline-color: #2E4057;
    border: 1px solid #2E4057;
}
QTableWidget::item:selected {
    background-color: #2E4057;
}
QHeaderView::section {
    background-color: #1C2E3F;
    color: #C9D1E0;
    border: 1px solid #2E4057;
    padding: 2px 4px;
}
QLineEdit, QPlainTextEdit, QTextEdit {
    background-color: #0D1B2A;
    color: #C9D1E0;
    border: 1px solid #2E4057;
    selection-background-color: #3A506B;
}
QPushButton {
    background-color: #2E4057;
    color: #C9D1E0;
    border: 1px solid #4A6080;
    padding: 4px 10px;
    border-radius: 3px;
}
QPushButton:hover {
    background-color: #3A506B;
}
QPushButton:pressed {
    background-color: #1C2E3F;
}
QComboBox {
    background-color: #0D1B2A;
    color: #C9D1E0;
    border: 1px solid #2E4057;
    padding: 2px 4px;
}
QComboBox QAbstractItemView {
    background-color: #1C2E3F;
    color: #C9D1E0;
    selection-background-color: #2E4057;
}
QCheckBox {
    color: #C9D1E0;
}
QCheckBox::indicator {
    border: 1px solid #4A6080;
    background-color: #0D1B2A;
}
QCheckBox::indicator:checked {
    background-color: #3A506B;
}
QScrollBar:vertical {
    background-color: #1C2E3F;
    width: 12px;
}
QScrollBar::handle:vertical {
    background-color: #2E4057;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background-color: #3A506B;
}
QScrollBar:horizontal {
    background-color: #1C2E3F;
    height: 12px;
}
QScrollBar::handle:horizontal {
    background-color: #2E4057;
    min-width: 20px;
}
QLabel {
    background-color: transparent;
}
QDialogButtonBox QPushButton {
    min-width: 60px;
}
"""


def apply_theme(app: QApplication, theme: str) -> None:
    app.setStyle("Fusion")
    if theme == "dark":
        app.setStyleSheet(_DARK_QSS)
    else:
        app.setStyleSheet("")
