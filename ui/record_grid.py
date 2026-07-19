from __future__ import annotations
from PySide6.QtWidgets import QMessageBox, QTableWidget, QTableWidgetItem, QMenu
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from app.record_fields import get_display_field
from tes3.format.format_loader import FieldFormat

# C# RTesEdit の TargetRecordInfo.AllFields (KeyFields + EditFields) と同等のデフォルト表示列
_DEFAULT_COLUMNS: dict[str, list[str]] = {
    "DIAL": ["NAME"],
    "INFO": ["INAM", "NAME", "BNAM"],
    "GMST": ["NAME", "STRV"],
    "RACE": ["NAME", "FNAM", "NPCS", "DESC"],
    "CLAS": ["NAME", "FNAM", "DESC"],
    "SKIL": ["INDX", "DESC"],
    "BSGN": ["NAME", "FNAM", "DESC"],
    "BOOK": ["NAME", "FNAM", "TEXT"],
    "NPC_": ["NAME", "FNAM"],
    "ALCH": ["NAME", "FNAM"],
    "APPA": ["NAME", "FNAM"],
    "ARMO": ["NAME", "FNAM"],
    "WEAP": ["NAME", "FNAM"],
    "SPEL": ["NAME", "FNAM"],
    "MISC": ["NAME", "FNAM"],
    "REPA": ["NAME", "FNAM"],
    "CLOT": ["NAME", "FNAM"],
    "INGR": ["NAME", "FNAM"],
    "LOCK": ["NAME", "FNAM"],
    "PROB": ["NAME", "FNAM"],
    "CELL": ["NAME"],
}


class RecordGrid(QTableWidget):

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self._record_type: str | None = None
        self._field_fmts: list[FieldFormat] = []
        self.setSelectionBehavior(QTableWidget.SelectRows)
        # 編集はTextPanel経由で編集先パッチにコピーしてから行う。
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.itemSelectionChanged.connect(self._on_row_changed)
        self.currentItemChanged.connect(self._on_current_item_changed)
        self.clicked.connect(self._on_cell_clicked)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_record_menu)

        header = self.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._on_header_menu)

    def load(self, record_type: str) -> None:
        self._record_type = record_type
        self.refresh()

    def clear_project(self) -> None:
        self._record_type = None
        self._field_fmts = []
        self.clear()
        self.setRowCount(0)
        self.setColumnCount(0)
        self._main.conflict_grid.load(None)
        self._main.text_panel.set_text("")

    def refresh(self) -> None:
        if not self._record_type:
            return

        from core.encoding import TesEncoding
        manager = self._main.manager
        fmt     = manager.format_loader.get_record(self._record_type)
        infos   = manager.all_records.get_visible_info_list(self._record_type)
        search  = self._main.search_text

        self._field_fmts = fmt.fields if fmt else []
        headers = [f.field_name for f in self._field_fmts]

        self.blockSignals(True)
        self.clear()
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)

        rows = [i for i in infos if not search or i.find(search)]
        self.setRowCount(len(rows))

        for row_idx, info in enumerate(rows):
            rec = info.main_record
            rec_enc = rec.mod_file.encoding if (rec and rec.mod_file) else TesEncoding.CP1252
            for col_idx, ff in enumerate(self._field_fmts):
                field = get_display_field(rec, ff.field_name) if rec else None
                text  = field.to_display_str(rec_enc) if field else ""
                item  = QTableWidgetItem(text)
                item.setData(Qt.UserRole, field)
                item.setData(Qt.UserRole + 1, rec)
                item.setData(Qt.UserRole + 2, info)
                if manager.is_record_deleted(rec):
                    item.setForeground(QBrush(QColor("darkred")))
                    font = item.font()
                    font.setStrikeOut(True)
                    item.setFont(font)
                    item.setToolTip(self.tr("編集先パッチによりゲーム内削除扱い"))
                self.setItem(row_idx, col_idx, item)

        self.resizeColumnsToContents()
        self._clamp_column_widths()
        self._apply_column_visibility()
        self.blockSignals(False)
        self._main.set_status(self._record_type, len(rows))

        self._main.conflict_grid.load(None)
        self._main.text_panel.set_text("")

    # ------------------------------------------------------------------
    # 列幅クランプ
    # ------------------------------------------------------------------

    def _clamp_column_widths(self) -> None:
        """各カラム幅をビューポート幅以下にクランプする。"""
        max_w = self.viewport().width()
        if max_w <= 0:
            return
        for i in range(self.columnCount()):
            if self.columnWidth(i) > max_w:
                self.setColumnWidth(i, max_w)

    # ------------------------------------------------------------------
    # 列表示設定
    # ------------------------------------------------------------------

    def _apply_column_visibility(self) -> None:
        from app.settings import Settings
        saved = Settings.instance().get_visible_columns(self._record_type)
        for i, ff in enumerate(self._field_fmts):
            if saved is not None:
                hidden = ff.field_name not in saved
            elif self._record_type in _DEFAULT_COLUMNS:
                hidden = ff.field_name not in _DEFAULT_COLUMNS[self._record_type]
            else:
                fmt = self._main.manager.format_loader.get_record(self._record_type)
                key_field = fmt.unique_key_field if fmt else ""
                hidden = not (ff.field_name == key_field or ff.is_edit)
            self.setColumnHidden(i, hidden)

    def _on_header_menu(self, pos) -> None:
        if not self._field_fmts:
            return
        menu = QMenu(self)
        for i, ff in enumerate(self._field_fmts):
            action = menu.addAction(ff.field_name)
            action.setCheckable(True)
            action.setChecked(not self.isColumnHidden(i))
            action.toggled.connect(
                lambda checked, idx=i: self._set_column_visible(idx, checked)
            )
        menu.exec(self.horizontalHeader().mapToGlobal(pos))

    def _set_column_visible(self, col_idx: int, visible: bool) -> None:
        self.setColumnHidden(col_idx, not visible)
        self._save_column_settings()

    def _save_column_settings(self) -> None:
        from app.settings import Settings
        visible = [
            self._field_fmts[i].field_name
            for i in range(len(self._field_fmts))
            if not self.isColumnHidden(i)
        ]
        Settings.instance().set_visible_columns(self._record_type, visible)

    # ------------------------------------------------------------------
    # イベント
    # ------------------------------------------------------------------

    def _cell_is_editable(self, item: QTableWidgetItem | None) -> bool:
        """アイテムが編集可能かどうか（field_format.is_edit で判定）"""
        if item is None:
            return False
        field = item.data(Qt.UserRole)
        if field is None:
            return False
        ff = getattr(field, "field_format", None)
        return ff is not None and ff.is_edit

    def _on_row_changed(self) -> None:
        """行（レコード）選択変更時: ConflictGrid を更新し、編集可能列へ移動する"""
        row = self.currentRow()
        if row < 0:
            self._main.conflict_grid.load(None)
            self._main.text_panel.set_text("")
            return
        first_item = self.item(row, 0)
        if not first_item:
            return
        info = first_item.data(Qt.UserRole + 2)

        # DIAL レコードはエイリアス（ローカライズ名）を含む全バージョンを統合して表示する
        # （tes3jp 等でローカライズ DIAL が異なるキーで登録される場合の英語名参照用）
        if self._record_type == "DIAL" and info is not None:
            merged = self._main.manager.all_records.get_dial_record_info_with_aliases(info.key)
            if merged is not None:
                info = merged

        self._main.conflict_grid.load(info)

        # 現在のセルが編集不可なら最初の可視・編集可能列へ自動移動
        if not self._cell_is_editable(self.currentItem()):
            for col in range(self.columnCount()):
                if self.isColumnHidden(col):
                    continue
                item = self.item(row, col)
                if self._cell_is_editable(item):
                    self.setCurrentCell(row, col)
                    break

    def _on_current_item_changed(self, current: QTableWidgetItem, _previous) -> None:
        """フォーカスセル変更時: TextPanel を更新する"""
        if current is not None:
            field = current.data(Qt.UserRole)
            record = current.data(Qt.UserRole + 1)
            self._main.text_panel.set_record_field(current.text(), field, record)
        else:
            self._main.text_panel.set_text("")

    def _on_cell_clicked(self, index) -> None:
        """同一セル再クリック時でも TextPanel を更新する（ConflictGrid の read-only 上書き）"""
        item = self.item(index.row(), index.column())
        if item is not None:
            field = item.data(Qt.UserRole)
            record = item.data(Qt.UserRole + 1)
            self._main.text_panel.set_record_field(item.text(), field, record)

    def _on_record_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        self.setCurrentCell(item.row(), item.column())
        record = item.data(Qt.UserRole + 1)
        if record is None:
            return

        manager = self._main.manager
        patch = manager.active_patch
        menu = QMenu(self)
        copy_action = menu.addAction(self.tr("編集先パッチへオーバーライドをコピー"))
        copy_action.setEnabled(patch is not None and record.mod_file is not patch)
        deleted = manager.is_record_deleted(record)
        delete_action = menu.addAction(
            self.tr("ゲーム内削除を解除")
            if deleted and record.mod_file is patch
            else self.tr("ゲーム内で削除扱い")
        )
        delete_action.setEnabled(
            patch is not None and (not deleted or record.mod_file is patch)
        )
        selected = menu.exec(self.viewport().mapToGlobal(pos))
        if selected is copy_action:
            try:
                manager.copy_record_as_override(record)
            except (RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, self.tr("コピーできません"), str(exc))
                return
            self._main.refresh_after_patch_edit()
            return
        if selected is not delete_action:
            return

        answer = QMessageBox.question(
            self,
            self.tr("ゲーム内削除の変更"),
            (
                self.tr("編集先パッチからゲーム内削除指定を解除します。")
                if deleted
                else self.tr("編集先パッチでこのレコードをゲーム内削除扱いにします。")
            )
            + self.tr("\n参照元ファイルは変更されません。\n\n{0} {1}").format(
                record.record_type,
                record.primary_key,
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            if deleted:
                manager.restore_record_deleted(record)
            else:
                manager.mark_record_deleted(record)
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, self.tr("変更できません"), str(exc))
            return
        self._main.refresh_after_patch_edit()
