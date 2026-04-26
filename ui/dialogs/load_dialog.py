from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QFileDialog,
    QComboBox, QCheckBox, QDialogButtonBox,
)
from PySide6.QtCore import Qt
from core.encoding import TesEncoding


@dataclass
class ModLoadEntry:
    path: Path
    encoding: TesEncoding
    is_overwrite: bool
    is_save: bool
    is_search_target: bool = True


class LoadDialog(QDialog):
    """ESPファイル選択・エンコーディング指定ダイアログ"""

    _COL_NAME    = 0
    _COL_ENC     = 1
    _COL_OVER    = 2
    _COL_SAVE    = 3
    _COL_SEARCH  = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Modファイルを開く"))
        self.resize(760, 400)
        self.selected_entries: list[ModLoadEntry] = []
        self._setup_ui()
        self._restore_last_files()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            self.tr("ファイル名"),
            self.tr("エンコード"),
            self.tr("上書き"),
            self.tr("保存対象"),
            self.tr("検索対象"),
        ])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setMinimumSectionSize(60)
        self._table.setColumnWidth(self._COL_ENC,    100)
        self._table.setColumnWidth(self._COL_OVER,    60)
        self._table.setColumnWidth(self._COL_SAVE,    72)
        self._table.setColumnWidth(self._COL_SEARCH,  72)
        self._table.horizontalHeader().setSectionResizeMode(
            self._COL_NAME,
            self._table.horizontalHeader().ResizeMode.Stretch,
        )
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton(self.tr("ファイルを追加"))
        del_btn = QPushButton(self.tr("削除"))
        add_btn.clicked.connect(self._on_add)
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_add(self) -> None:
        from app.settings import Settings
        last_folder = Settings.instance().get_last_folder()
        paths, _ = QFileDialog.getOpenFileNames(
            self, self.tr("Modファイルを選択"), last_folder,
            self.tr("TES3 Mod Files (*.esp *.esm);;All Files (*)")
        )
        if paths:
            Settings.instance().set_last_folder(str(Path(paths[0]).parent))
            for p in paths:
                self._add_row(Path(p))  # enc=None → 自動判定

    def _add_row(self, path: Path, enc: TesEncoding | None = None,
                 is_overwrite: bool = False, is_save: bool = False,
                 is_search_target: bool = True) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)

        name_item = QTableWidgetItem(path.name)
        name_item.setData(Qt.UserRole, path)
        name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(row, self._COL_NAME, name_item)

        enc_combo = QComboBox()
        resolved = enc if enc is not None else self._detect_encoding(path)
        for e in TesEncoding:
            enc_combo.addItem(e.short_label(), e)
            if e == resolved:
                enc_combo.setCurrentIndex(enc_combo.count() - 1)
        self._table.setCellWidget(row, self._COL_ENC, enc_combo)

        for col, val in [
            (self._COL_OVER,   is_overwrite),
            (self._COL_SAVE,   is_save),
            (self._COL_SEARCH, is_search_target),
        ]:
            chk = QCheckBox()
            chk.setChecked(val)
            chk.setStyleSheet("margin-left: 18px;")
            self._table.setCellWidget(row, col, chk)

    def _restore_last_files(self) -> None:
        from app.settings import Settings
        for entry in Settings.instance().get_last_files():
            path = Path(entry.get("path", ""))
            if not path.exists():
                continue
            enc_val = entry.get("encoding", TesEncoding.CP1252.value)
            enc = next((e for e in TesEncoding if e.value == enc_val), TesEncoding.CP1252)
            self._add_row(
                path,
                enc,
                entry.get("is_overwrite", False),
                entry.get("is_save", False),
                entry.get("is_search_target", True),
            )

    @staticmethod
    def _detect_encoding(path: Path) -> TesEncoding:
        try:
            data = path.read_bytes()[:8192]
            return TesEncoding.detect_from_bytes(data)
        except Exception:
            return TesEncoding.default()

    def _on_delete(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)

    def _on_accept(self) -> None:
        from app.settings import Settings
        self.selected_entries = []
        last_files = []
        for row in range(self._table.rowCount()):
            path             = self._table.item(row, self._COL_NAME).data(Qt.UserRole)
            enc              = self._table.cellWidget(row, self._COL_ENC).currentData()
            is_overwrite     = self._table.cellWidget(row, self._COL_OVER).isChecked()
            is_save          = self._table.cellWidget(row, self._COL_SAVE).isChecked()
            is_search_target = self._table.cellWidget(row, self._COL_SEARCH).isChecked()
            self.selected_entries.append(
                ModLoadEntry(path, enc, is_overwrite, is_save, is_search_target)
            )
            last_files.append({
                "path":             str(path),
                "encoding":         enc.value,
                "is_overwrite":     is_overwrite,
                "is_save":          is_save,
                "is_search_target": is_search_target,
            })
        Settings.instance().set_last_files(last_files)
        self.accept()
