from __future__ import annotations
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
from PySide6.QtCore import Qt
from app.record_info import RecordInfo


class ConflictGrid(QTableWidget):
    """同一レコードの複数Mod間での競合を表示するサブグリッド"""

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.currentItemChanged.connect(self._on_current_changed)

    def load(self, info: RecordInfo | None) -> None:
        self.blockSignals(True)
        self.clear()
        self.setRowCount(0)
        self.setColumnCount(0)
        if not info:
            self.blockSignals(False)
            return

        from core.encoding import TesEncoding
        manager = self._main.manager

        record_type = info.records[0].record_type if info.records else ""
        fmt         = manager.format_loader.get_record(record_type)
        key_field   = fmt.unique_key_field if fmt else ""

        # RecordGrid の可視列と同期（一意キーは行ヘッダとして使用済みのため除外）
        rec_grid = self._main.record_grid
        field_fmts = [
            ff for i, ff in enumerate(rec_grid._field_fmts)
            if not rec_grid.isColumnHidden(i) and ff.field_name != key_field
        ]
        if not field_fmts:
            field_fmts = [f for f in (fmt.fields if fmt else []) if f.field_name != key_field]

        headers = [f.field_name for f in field_fmts]
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.setRowCount(len(info.records))

        for row_idx, record in enumerate(info.records):
            mod_name = record.mod_file.file_name if record.mod_file else ""
            rec_enc  = record.mod_file.encoding if record.mod_file else TesEncoding.CP1252
            self.setVerticalHeaderItem(row_idx, QTableWidgetItem(mod_name))
            for col_idx, ff in enumerate(field_fmts):
                field = record.fields_map.get(ff.field_name)
                text  = field.to_display_str(rec_enc) if field else ""
                item  = QTableWidgetItem(text)
                item.setData(Qt.UserRole,     field)   # Field オブジェクト
                item.setData(Qt.UserRole + 1, record)  # 所属 Record
                self.setItem(row_idx, col_idx, item)

        self.resizeColumnsToContents()
        self.blockSignals(False)

    def _on_current_changed(self, current, _previous) -> None:
        if current is None:
            return
        field  = current.data(Qt.UserRole)
        record = current.data(Qt.UserRole + 1)

        ff      = getattr(field, "field_format", None)
        is_edit = ff is not None and ff.is_edit
        is_save = (record is not None and
                   record.mod_file is not None and
                   record.mod_file.is_save)

        if field is not None and is_edit and is_save:
            # 保存対象ファイルの編集可能フィールド → TextPanel を編集モードで開く
            self._main.text_panel.set_conflict_cell(current.text(), field, record)
        else:
            # 参照専用
            self._main.text_panel.set_text(current.text())
