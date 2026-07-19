from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.encoding import TesEncoding
from tes3.mod_file import ROLE_PATCH, ROLE_SOURCE


@dataclass
class ModLoadEntry:
    path: Path
    encoding: TesEncoding
    role: str = ROLE_SOURCE
    is_search_target: bool = True
    create_if_missing: bool = False

    @property
    def is_overwrite(self) -> bool:
        return self.role == ROLE_PATCH

    @property
    def is_save(self) -> bool:
        return self.role == ROLE_PATCH


class LoadDialog(QDialog):
    """参照元と単一の編集先パッチを役割で指定するダイアログ。"""

    _COL_NAME = 0
    _COL_ENC = 1
    _COL_ROLE = 2
    _COL_STATUS = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Modプロジェクトを開く"))
        self.resize(860, 460)
        self.selected_entries: list[ModLoadEntry] = []
        self._setup_ui()
        self._restore_last_files()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        project_row = QHBoxLayout()
        project_row.addWidget(QLabel(self.tr("プロジェクト: ")))
        self._pattern_combo = QComboBox()
        self._load_pattern_btn = QPushButton(self.tr("読込"))
        self._save_pattern_btn = QPushButton(self.tr("現在を登録・更新"))
        self._delete_pattern_btn = QPushButton(self.tr("削除"))
        self._load_pattern_btn.clicked.connect(self._on_load_pattern)
        self._save_pattern_btn.clicked.connect(self._on_save_pattern)
        self._delete_pattern_btn.clicked.connect(self._on_delete_pattern)
        project_row.addWidget(self._pattern_combo, 1)
        project_row.addWidget(self._load_pattern_btn)
        project_row.addWidget(self._save_pattern_btn)
        project_row.addWidget(self._delete_pattern_btn)
        layout.addLayout(project_row)

        project_file_row = QHBoxLayout()
        project_file_row.addWidget(QLabel(self.tr("プロジェクトファイル: ")))
        open_project_btn = QPushButton(self.tr(".rteprojを開く"))
        save_project_btn = QPushButton(self.tr("現在を.rteprojへ保存"))
        open_project_btn.clicked.connect(self._on_open_project_file)
        save_project_btn.clicked.connect(self._on_save_project_file)
        project_file_row.addWidget(open_project_btn)
        project_file_row.addWidget(save_project_btn)
        project_file_row.addStretch()
        layout.addLayout(project_file_row)

        explanation = QLabel(self.tr(
            "参照元は常に読み取り専用です。編集内容は1つの「編集先パッチ」にコピーされ、"
            "そのパッチだけを保存します。編集先パッチはロード順の最後になります。"
        ))
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels([
            self.tr("ファイル名"),
            self.tr("エンコード"),
            self.tr("役割"),
            self.tr("状態"),
        ])
        self._table.horizontalHeader().setMinimumSectionSize(80)
        self._table.setColumnWidth(self._COL_ENC, 110)
        self._table.setColumnWidth(self._COL_ROLE, 180)
        self._table.setColumnWidth(self._COL_STATUS, 120)
        self._table.horizontalHeader().setSectionResizeMode(
            self._COL_NAME,
            self._table.horizontalHeader().ResizeMode.Stretch,
        )
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_source_btn = QPushButton(self.tr("参照元を追加"))
        add_patch_btn = QPushButton(self.tr("既存パッチを追加"))
        new_patch_btn = QPushButton(self.tr("新規パッチを指定"))
        relocate_btn = QPushButton(self.tr("選択ファイルの場所を変更"))
        del_btn = QPushButton(self.tr("削除"))
        up_btn = QPushButton(self.tr("上へ"))
        down_btn = QPushButton(self.tr("下へ"))
        add_source_btn.clicked.connect(self._on_add_source)
        add_patch_btn.clicked.connect(self._on_add_existing_patch)
        new_patch_btn.clicked.connect(self._on_new_patch)
        relocate_btn.clicked.connect(self._on_relocate)
        del_btn.clicked.connect(self._on_delete)
        up_btn.clicked.connect(lambda: self._move_current_row(-1))
        down_btn.clicked.connect(lambda: self._move_current_row(1))
        btn_row.addWidget(add_source_btn)
        btn_row.addWidget(add_patch_btn)
        btn_row.addWidget(new_patch_btn)
        btn_row.addWidget(relocate_btn)
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

    def _on_add_source(self) -> None:
        from app.settings import Settings

        last_folder = Settings.instance().get_last_folder()
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            self.tr("参照元ESM/ESPを選択"),
            last_folder,
            self.tr("TES3 Mod Files (*.esp *.esm);;All Files (*)"),
        )
        if not paths:
            return
        Settings.instance().set_last_folder(str(Path(paths[0]).parent))
        for path in paths:
            self._add_row(Path(path), role=ROLE_SOURCE)

    def _on_add_existing_patch(self) -> None:
        from app.settings import Settings

        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("更新する既存パッチを選択"),
            Settings.instance().get_last_folder(),
            self.tr("TES3 Mod Files (*.esp);;All Files (*)"),
        )
        if path:
            target = Path(path)
            Settings.instance().set_last_folder(str(target.parent))
            self._clear_patch_roles()
            self._add_row(target, role=ROLE_PATCH, create_if_missing=False)

    def _on_new_patch(self) -> None:
        from app.settings import Settings

        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("新規編集先パッチを指定"),
            Settings.instance().get_last_folder(),
            self.tr("TES3 Plugin (*.esp);;All Files (*)"),
        )
        if path:
            target = Path(path)
            Settings.instance().set_last_folder(str(target.parent))
            self._clear_patch_roles()
            self._add_row(
                target,
                TesEncoding.UTF_8,
                ROLE_PATCH,
                create_if_missing=True,
            )

    def _add_row(
        self,
        path: Path,
        enc: TesEncoding | None = None,
        role: str = ROLE_SOURCE,
        create_if_missing: bool = False,
    ) -> None:
        path_key = str(path.absolute()).casefold()
        for row in range(self._table.rowCount()):
            existing = self._table.item(row, self._COL_NAME).data(Qt.UserRole)
            if str(Path(existing).absolute()).casefold() != path_key:
                continue
            if enc is not None:
                combo = self._table.cellWidget(row, self._COL_ENC)
                index = combo.findData(enc)
                if index >= 0:
                    combo.setCurrentIndex(index)
            if role == ROLE_PATCH:
                combo = self._table.cellWidget(row, self._COL_ROLE)
                combo.setCurrentIndex(combo.findData(ROLE_PATCH))
                self._table.item(row, self._COL_NAME).setData(
                    Qt.UserRole + 1,
                    create_if_missing,
                )
                self._refresh_row_status(row)
            return
        self._insert_row(
            self._table.rowCount(),
            path,
            enc,
            role,
            create_if_missing,
        )

    def _insert_row(
        self,
        row: int,
        path: Path,
        enc: TesEncoding | None = None,
        role: str = ROLE_SOURCE,
        create_if_missing: bool = False,
    ) -> None:
        self._table.insertRow(row)

        name_item = QTableWidgetItem(path.name)
        name_item.setData(Qt.UserRole, path)
        name_item.setData(Qt.UserRole + 1, create_if_missing)
        name_item.setToolTip(str(path))
        name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(row, self._COL_NAME, name_item)

        enc_combo = QComboBox()
        resolved = enc if enc is not None else self._detect_encoding(path)
        for value in TesEncoding:
            enc_combo.addItem(value.short_label(), value)
            if value == resolved:
                enc_combo.setCurrentIndex(enc_combo.count() - 1)
        self._table.setCellWidget(row, self._COL_ENC, enc_combo)

        role_combo = QComboBox()
        role_combo.addItem(self.tr("参照元（読取専用）"), ROLE_SOURCE)
        role_combo.addItem(self.tr("編集先パッチ"), ROLE_PATCH)
        role_combo.setCurrentIndex(1 if role == ROLE_PATCH else 0)
        role_combo.currentIndexChanged.connect(self._refresh_all_statuses)
        self._table.setCellWidget(row, self._COL_ROLE, role_combo)

        status_item = QTableWidgetItem()
        status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(row, self._COL_STATUS, status_item)
        self._refresh_row_status(row)

    def _clear_patch_roles(self) -> None:
        for row in range(self._table.rowCount()):
            combo = self._table.cellWidget(row, self._COL_ROLE)
            combo.setCurrentIndex(combo.findData(ROLE_SOURCE))

    def _restore_last_files(self) -> None:
        from app.settings import Settings

        self._load_entries(Settings.instance().get_last_files(), append=True)

    def _load_entries(self, entries: list[dict], *, append: bool = False) -> None:
        if not append:
            self._table.setRowCount(0)
        for entry in entries:
            path = Path(entry.get("path", ""))
            role = entry.get("role")
            if role not in (ROLE_SOURCE, ROLE_PATCH):
                role = ROLE_PATCH if entry.get("is_save", False) else ROLE_SOURCE
            enc_value = entry.get("encoding", TesEncoding.CP1252.value)
            enc = next(
                (value for value in TesEncoding if value.value == enc_value),
                TesEncoding.CP1252,
            )
            self._add_row(
                path,
                enc,
                role,
                bool(entry.get("create_if_missing", False)),
            )

    def _current_entries_as_dicts(self) -> list[dict]:
        entries: list[dict] = []
        for row in range(self._table.rowCount()):
            path = self._table.item(row, self._COL_NAME).data(Qt.UserRole)
            encoding = self._table.cellWidget(row, self._COL_ENC).currentData()
            role = self._table.cellWidget(row, self._COL_ROLE).currentData()
            create_if_missing = bool(
                self._table.item(row, self._COL_NAME).data(Qt.UserRole + 1)
            )
            entries.append({
                "path": str(path),
                "encoding": encoding.value,
                "role": role,
                "is_search_target": True,
                "create_if_missing": create_if_missing,
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
        if isinstance(pattern, dict) and isinstance(pattern.get("entries"), list):
            self._load_entries(pattern["entries"])

    def _on_save_pattern(self) -> None:
        from app.settings import Settings

        name, ok = QInputDialog.getText(
            self,
            self.tr("プロジェクト登録"),
            self.tr("プロジェクト名:"),
            QLineEdit.Normal,
            self._pattern_combo.currentText(),
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
        answer = QMessageBox.question(
            self,
            self.tr("プロジェクト削除"),
            self.tr("プロジェクト「{0}」を削除しますか？").format(name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        patterns = [
            item for item in Settings.instance().get_load_patterns()
            if item.get("name") != name
        ]
        Settings.instance().set_load_patterns(patterns)
        self._refresh_patterns()

    def _on_open_project_file(self) -> None:
        from app.project_file import load_project_file
        from app.settings import Settings

        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("RTESEditorプロジェクトを開く"),
            Settings.instance().get_last_folder(),
            self.tr("RTESEditor Project (*.rteproj);;All Files (*)"),
        )
        if not path:
            return
        try:
            entries = load_project_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, self.tr("プロジェクト読込エラー"), str(exc))
            return
        Settings.instance().set_last_folder(str(Path(path).parent))
        missing_entries = [
            entry["path"] for entry in entries
            if not Path(entry["path"]).exists()
            and (
                entry.get("role") == ROLE_SOURCE
                or not entry.get("create_if_missing", False)
            )
        ]
        self._load_entries(entries)
        if missing_entries:
            QMessageBox.warning(
                self,
                self.tr("ファイルが見つかりません"),
                self.tr(
                    "次のファイルを赤く表示しました。行を選択して「選択ファイルの場所を変更」"
                    "から割り当て直してください。\n\n"
                )
                + "\n".join(missing_entries[:20]),
            )

    def _on_save_project_file(self) -> None:
        from app.project_file import save_project_file
        from app.settings import Settings

        entries = self._current_entries_as_dicts()
        patches = [entry for entry in entries if entry["role"] == ROLE_PATCH]
        if len(patches) > 1:
            QMessageBox.warning(
                self,
                self.tr("編集先パッチの重複"),
                self.tr("編集先パッチは1つだけ指定してください。"),
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("RTESEditorプロジェクトを保存"),
            Settings.instance().get_last_folder(),
            self.tr("RTESEditor Project (*.rteproj);;All Files (*)"),
        )
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".rteproj")
        try:
            save_project_file(target, entries)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, self.tr("プロジェクト保存エラー"), str(exc))
            return
        Settings.instance().set_last_folder(str(target.parent))
        QMessageBox.information(
            self,
            self.tr("プロジェクト保存完了"),
            self.tr("プロジェクトを保存しました。\n{0}").format(target),
        )

    @staticmethod
    def _detect_encoding(path: Path) -> TesEncoding:
        try:
            return TesEncoding.detect_from_bytes(path.read_bytes()[:8192])
        except Exception:
            return TesEncoding.default()

    def _on_delete(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)

    def _on_relocate(self) -> None:
        from app.settings import Settings

        row = self._table.currentRow()
        if row < 0:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("置き換えるESM/ESPを選択"),
            Settings.instance().get_last_folder(),
            self.tr("TES3 Mod Files (*.esp *.esm);;All Files (*)"),
        )
        if not path:
            return
        target = Path(path)
        Settings.instance().set_last_folder(str(target.parent))
        self._set_row_path(row, target, create_if_missing=False)

    def _set_row_path(
        self,
        row: int,
        path: Path,
        *,
        create_if_missing: bool,
    ) -> None:
        item = self._table.item(row, self._COL_NAME)
        item.setText(path.name)
        item.setData(Qt.UserRole, path)
        item.setToolTip(str(path))
        item.setData(Qt.UserRole + 1, create_if_missing)
        detected = self._detect_encoding(path)
        combo = self._table.cellWidget(row, self._COL_ENC)
        index = combo.findData(detected)
        if index >= 0:
            combo.setCurrentIndex(index)
        self._refresh_row_status(row)

    def _refresh_all_statuses(self, *_args) -> None:
        for row in range(self._table.rowCount()):
            self._refresh_row_status(row)

    def _refresh_row_status(self, row: int) -> None:
        name_item = self._table.item(row, self._COL_NAME)
        status_item = self._table.item(row, self._COL_STATUS)
        role_combo = self._table.cellWidget(row, self._COL_ROLE)
        if name_item is None or status_item is None or role_combo is None:
            return
        path = Path(name_item.data(Qt.UserRole))
        role = role_combo.currentData()
        create_if_missing = bool(name_item.data(Qt.UserRole + 1))
        if path.exists():
            text = self.tr("確認済み")
            color = self.palette().text().color()
        elif role == ROLE_PATCH and create_if_missing:
            text = self.tr("新規作成予定")
            color = self.palette().text().color()
        else:
            text = self.tr("見つかりません")
            color = QColor("red")
        status_item.setText(text)
        status_item.setForeground(QBrush(color))

    def _move_current_row(self, direction: int) -> None:
        row = self._table.currentRow()
        target = row + direction
        if row < 0 or target < 0 or target >= self._table.rowCount():
            return
        path = self._table.item(row, self._COL_NAME).data(Qt.UserRole)
        create_if_missing = bool(
            self._table.item(row, self._COL_NAME).data(Qt.UserRole + 1)
        )
        encoding = self._table.cellWidget(row, self._COL_ENC).currentData()
        role = self._table.cellWidget(row, self._COL_ROLE).currentData()
        self._table.removeRow(row)
        self._insert_row(
            target,
            path,
            encoding,
            role,
            create_if_missing,
        )
        self._table.selectRow(target)
        self._table.setCurrentCell(target, self._COL_NAME)

    def _on_accept(self) -> None:
        from app.settings import Settings

        entries = self._current_entries_as_dicts()
        missing_entries = [
            entry["path"] for entry in entries
            if not Path(entry["path"]).exists()
            and (
                entry["role"] == ROLE_SOURCE
                or not entry.get("create_if_missing", False)
            )
        ]
        if missing_entries:
            QMessageBox.warning(
                self,
                self.tr("ファイルが見つかりません"),
                self.tr(
                    "赤く表示されたファイルの場所を変更するか、一覧から除去してください。\n\n"
                ) + "\n".join(missing_entries[:20]),
            )
            return
        patches = [entry for entry in entries if entry["role"] == ROLE_PATCH]
        if len(patches) > 1:
            QMessageBox.warning(
                self,
                self.tr("編集先パッチの重複"),
                self.tr("編集先パッチは1つだけ指定してください。"),
            )
            return
        # 編集先は必ず最後にロードする。表示順設定にも同じ順序を保存する。
        entries = [entry for entry in entries if entry["role"] == ROLE_SOURCE] + patches
        self.selected_entries = [
            ModLoadEntry(
                Path(entry["path"]),
                next(
                    (value for value in TesEncoding if value.value == entry["encoding"]),
                    TesEncoding.CP1252,
                ),
                entry["role"],
                bool(entry.get("is_search_target", True)),
                bool(entry.get("create_if_missing", False)),
            )
            for entry in entries
        ]
        Settings.instance().set_last_files(entries)
        self.accept()
