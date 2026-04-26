from __future__ import annotations
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QMenu
from PySide6.QtCore import Qt
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
}


class RecordGrid(QTableWidget):

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self._record_type: str | None = None
        self._field_fmts: list[FieldFormat] = []
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.DoubleClicked)
        self.itemSelectionChanged.connect(self._on_row_changed)
        self.currentItemChanged.connect(self._on_current_item_changed)
        self.clicked.connect(self._on_cell_clicked)
        self.itemChanged.connect(self._on_cell_changed)

        header = self.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._on_header_menu)

    def load(self, record_type: str) -> None:
        self._record_type = record_type
        self.refresh()

    def refresh(self) -> None:
        if not self._record_type:
            return

        from core.encoding import TesEncoding
        manager = self._main.manager
        fmt     = manager.format_loader.get_record(self._record_type)
        infos   = manager.all_records.get_info_list(self._record_type)
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
                field = rec.fields_map.get(ff.field_name) if rec else None
                text  = field.to_display_str(rec_enc) if field else ""
                item  = QTableWidgetItem(text)
                item.setData(Qt.UserRole, field)
                if not ff.is_edit:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.setItem(row_idx, col_idx, item)
            self.item(row_idx, 0).setData(Qt.UserRole + 1, info)

        self.resizeColumnsToContents()
        self._apply_column_visibility()
        self.blockSignals(False)
        self._main.set_status(self._record_type, len(rows))

        self._main.conflict_grid.load(None)
        self._main.text_panel.set_text("")

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
        info = first_item.data(Qt.UserRole + 1)
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
            self._main.text_panel.set_text(current.text(), current)
        else:
            self._main.text_panel.set_text("")

    def _on_cell_clicked(self, index) -> None:
        """同一セル再クリック時でも TextPanel を更新する（ConflictGrid の read-only 上書き）"""
        item = self.item(index.row(), index.column())
        if item is not None:
            self._main.text_panel.set_text(item.text(), item)

    def _on_cell_changed(self, item: QTableWidgetItem) -> None:
        from core.bytes_util import TesBytes
        from core.encoding import TesEncoding
        field = item.data(Qt.UserRole)
        if field is None:
            return
        enc = field.parent_record.mod_file.encoding if (
            hasattr(field, "parent_record") and field.parent_record and field.parent_record.mod_file
        ) else TesEncoding.CP1252
        new_bytes = TesBytes.from_str(
            item.text(), enc,
            null_terminate=field.field_format and field.field_format.data_type == "zstring"
        )
        field.modify(new_bytes)
        row = self.currentRow()
        if row >= 0:
            fi = self.item(row, 0)
            if fi:
                self._main.conflict_grid.load(fi.data(Qt.UserRole + 1))
