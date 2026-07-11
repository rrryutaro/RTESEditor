from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QFileDialog,
    QComboBox, QCheckBox, QDialogButtonBox, QInputDialog, QMessageBox,
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

        pattern_row = QHBoxLayout()
        pattern_row.addWidget(QLabel(self.tr("パターン:")))
        self._pattern_combo = QComboBox()
        self._load_pattern_btn = QPushButton(self.tr("読込"))
        self._save_pattern_btn = QPushButton(self.tr("現在を登録"))
        self._delete_pattern_btn = QPushButton(self.tr("削除"))
        self._load_pattern_btn.clicked.connect(self._on_load_pattern)
        self._save_pattern_btn.clicked.connect(self._on_save_pattern)
        self._delete_pattern_btn.clicked.connect(self._on_delete_pattern)
        pattern_row.addWidget(self._pattern_combo, 1)
        pattern_row.addWidget(self._load_pattern_btn)
        pattern_row.addWidget(self._save_pattern_btn)
        pattern_row.addWidget(self._delete_pattern_btn)
        layout.addLayout(pattern_row)

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
        up_btn = QPushButton(self.tr("上へ"))
        down_btn = QPushButton(self.tr("下へ"))
        add_btn.clicked.connect(self._on_add)
        del_btn.clicked.connect(self._on_delete)
        up_btn.clicked.connect(lambda: self._move_current_row(-1))
        down_btn.clicked.connect(lambda: self._move_current_row(1))
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(up_btn)
        btn_row.addWidget(down_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_patterns()

    def _on_add(self) -> None:
        from app.settings import Settings
        last_folder = Settings.instance().get_last_folder()
        paths, _ = QFileDialog.getOpenFileNames(
            self, self.tr("Modファイルを選択"), last_folder,
            self.tr("TES3/OpenMW Files (*.esp *.esm *.omwsave *.ess);;All Files (*)")
        )
        if paths:
            Settings.instance().set_last_folder(str(Path(paths[0]).parent))
            for p in paths:
                self._add_row(Path(p))  # enc=None → 自動判定

    def _add_row(self, path: Path, enc: TesEncoding | None = None,
                 is_overwrite: bool = False, is_save: bool = False,
                 is_search_target: bool = True) -> None:
        self._insert_row(
            self._table.rowCount(),
            path,
            enc,
            is_overwrite,
            is_save,
            is_search_target,
        )

    def _insert_row(self, row: int, path: Path, enc: TesEncoding | None = None,
                    is_overwrite: bool = False, is_save: bool = False,
                    is_search_target: bool = True) -> None:
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
        self._load_entries(Settings.instance().get_last_files(), append=True)

    def _load_entries(self, entries: list[dict], *, append: bool = False) -> None:
        if not append:
            self._table.setRowCount(0)

        for entry in entries:
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

    def _current_entries_as_dicts(self) -> list[dict]:
        entries: list[dict] = []
        for row in range(self._table.rowCount()):
            path             = self._table.item(row, self._COL_NAME).data(Qt.UserRole)
            enc              = self._table.cellWidget(row, self._COL_ENC).currentData()
            is_overwrite     = self._table.cellWidget(row, self._COL_OVER).isChecked()
            is_save          = self._table.cellWidget(row, self._COL_SAVE).isChecked()
            is_search_target = self._table.cellWidget(row, self._COL_SEARCH).isChecked()
            entries.append({
                "path":             str(path),
                "encoding":         enc.value,
                "is_overwrite":     is_overwrite,
                "is_save":          is_save,
                "is_search_target": is_search_target,
            })
        return entries

    def _refresh_patterns(self, select_name: str | None = None) -> None:
        from app.settings import Settings
        self._pattern_combo.clear()
        for pattern in Settings.instance().get_load_patterns():
            self._pattern_combo.addItem(pattern["name"], pattern)
        if select_name:
            index = self._pattern_combo.findText(select_name)
            if index >= 0:
                self._pattern_combo.setCurrentIndex(index)
        has_patterns = self._pattern_combo.count() > 0
        self._load_pattern_btn.setEnabled(has_patterns)
        self._delete_pattern_btn.setEnabled(has_patterns)

    def _on_load_pattern(self) -> None:
        pattern = self._pattern_combo.currentData()
        if not isinstance(pattern, dict):
            return
        entries = pattern.get("entries", [])
        if isinstance(entries, list):
            self._load_entries(entries)

    def _on_save_pattern(self) -> None:
        from app.settings import Settings
        current_name = self._pattern_combo.currentText()
        name, ok = QInputDialog.getText(
            self,
            self.tr("パターン登録"),
            self.tr("パターン名:"),
            QLineEdit.Normal,
            current_name,
        )
        name = name.strip()
        if not ok or not name:
            return

        entries = self._current_entries_as_dicts()
        patterns = Settings.instance().get_load_patterns()
        for pattern in patterns:
            if pattern["name"] == name:
                pattern["entries"] = entries
                break
        else:
            patterns.append({"name": name, "entries": entries})
        Settings.instance().set_load_patterns(patterns)
        self._refresh_patterns(name)

    def _on_delete_pattern(self) -> None:
        from app.settings import Settings
        pattern = self._pattern_combo.currentData()
        if not isinstance(pattern, dict):
            return
        name = pattern.get("name", "")
        ret = QMessageBox.question(
            self,
            self.tr("パターン削除"),
            self.tr("パターン「{0}」を削除しますか？").format(name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        patterns = [
            item
            for item in Settings.instance().get_load_patterns()
            if item.get("name") != name
        ]
        Settings.instance().set_load_patterns(patterns)
        self._refresh_patterns()

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

    def _move_current_row(self, direction: int) -> None:
        row = self._table.currentRow()
        target = row + direction
        if row < 0 or target < 0 or target >= self._table.rowCount():
            return

        path = self._table.item(row, self._COL_NAME).data(Qt.UserRole)
        enc = self._table.cellWidget(row, self._COL_ENC).currentData()
        is_overwrite = self._table.cellWidget(row, self._COL_OVER).isChecked()
        is_save = self._table.cellWidget(row, self._COL_SAVE).isChecked()
        is_search_target = self._table.cellWidget(row, self._COL_SEARCH).isChecked()

        self._table.removeRow(row)
        self._insert_row(target, path, enc, is_overwrite, is_save, is_search_target)
        self._table.selectRow(target)
        self._table.setCurrentCell(target, self._COL_NAME)

    def _on_accept(self) -> None:
        from app.settings import Settings
        self.selected_entries = []
        last_files = self._current_entries_as_dicts()
        for entry in last_files:
            path             = Path(entry["path"])
            enc              = next(
                (e for e in TesEncoding if e.value == entry["encoding"]),
                TesEncoding.CP1252,
            )
            is_overwrite     = bool(entry["is_overwrite"])
            is_save          = bool(entry["is_save"])
            is_search_target = bool(entry["is_search_target"])
            self.selected_entries.append(
                ModLoadEntry(path, enc, is_overwrite, is_save, is_search_target)
            )
        Settings.instance().set_last_files(last_files)
        self.accept()
