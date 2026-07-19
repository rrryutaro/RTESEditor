from __future__ import annotations
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPalette
from app.record_info import RecordInfo
from app.record_fields import get_display_field


class ConflictGrid(QTableWidget):
    """同一レコードの複数Mod間での競合を表示するサブグリッド"""

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.currentItemChanged.connect(self._on_current_changed)

    def load(self, info: RecordInfo | None, field_fmts: list | None = None) -> None:
        """info を複数Mod間で比較表示する。
        field_fmts を指定するとRecordGridの可視列ではなくその列定義を使う（DialoguePanel用）。"""
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

        if field_fmts is None:
            # RecordGrid の可視列と同期する。先頭列はファイル名用に追加する。
            rec_grid = self._main.record_grid
            field_fmts = [
                ff for i, ff in enumerate(rec_grid._field_fmts)
                if not rec_grid.isColumnHidden(i)
            ]
            if not field_fmts:
                field_fmts = list(fmt.fields if fmt else [])

        headers = [self.tr("ファイル名")] + [f.field_name for f in field_fmts]
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.setRowCount(len(info.records))

        for row_idx, record in enumerate(info.records):
            mod_name = record.mod_file.file_name if record.mod_file else ""
            rec_enc  = record.mod_file.encoding if record.mod_file else TesEncoding.CP1252
            name_item = QTableWidgetItem(mod_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setBackground(QBrush(self.palette().color(QPalette.Button)))
            name_item.setForeground(QBrush(self.palette().color(QPalette.ButtonText)))
            name_item.setData(Qt.UserRole + 1, record)  # 所属 Record
            if manager.is_record_deleted(record):
                name_item.setForeground(QBrush(QColor("darkred")))
                name_item.setToolTip(self.tr("ゲーム内削除扱い"))
            self.setItem(row_idx, 0, name_item)
            for col_idx, ff in enumerate(field_fmts):
                field = get_display_field(record, ff.field_name)
                text  = field.to_display_str(rec_enc) if field else ""
                item  = QTableWidgetItem(text)
                item.setData(Qt.UserRole,     field)   # Field オブジェクト
                item.setData(Qt.UserRole + 1, record)  # 所属 Record
                if manager.is_record_deleted(record):
                    item.setForeground(QBrush(QColor("darkred")))
                    font = item.font()
                    font.setStrikeOut(True)
                    item.setFont(font)
                self.setItem(row_idx, col_idx + 1, item)

        self.resizeColumnsToContents()
        self.blockSignals(False)

    def _on_current_changed(self, current, _previous) -> None:
        if current is None:
            return
        if current.column() == 0:
            return
        field  = current.data(Qt.UserRole)
        record = current.data(Qt.UserRole + 1)

        ff      = getattr(field, "field_format", None)
        is_edit = ff is not None and ff.is_edit
        has_patch = self._main.manager.active_patch is not None

        if field is not None and record is not None and is_edit and has_patch:
            # 参照元は直接変更せず、適用時に編集先パッチへコピーする。
            self._main.text_panel.set_record_field(current.text(), field, record)
        else:
            # 参照専用
            self._main.text_panel.set_text(current.text())
