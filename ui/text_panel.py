from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QPushButton,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont


class TextPanel(QWidget):
    """長文テキスト編集パネル（適用/キャンセルボタン付き）"""

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self._current_item   = None   # RecordGrid の QTableWidgetItem
        self._current_field  = None   # ConflictGrid 経由の Field オブジェクト
        self._current_record = None   # ConflictGrid 経由の Record オブジェクト
        self._original_text  = ""
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._edit = QPlainTextEdit()
        self._edit.setTabStopDistance(32)
        layout.addWidget(self._edit)

        btn_row = QHBoxLayout()
        self._apply_btn  = QPushButton(self.tr("適用"))
        self._cancel_btn = QPushButton(self.tr("キャンセル"))
        self._apply_btn.clicked.connect(self._on_apply)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addStretch()
        btn_row.addWidget(self._apply_btn)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

        self._set_editable(False)

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def set_text(self, text: str, item=None) -> None:
        """RecordGrid セル用。item が None または読み取り専用の場合は read-only になる。"""
        self._original_text  = text
        self._current_item   = item
        self._current_field  = None
        self._current_record = None
        self._edit.blockSignals(True)
        self._edit.setPlainText(text)
        self._edit.blockSignals(False)
        self._set_editable(self._is_editable(item))

    def set_conflict_cell(self, text: str, field, record) -> None:
        """ConflictGrid の編集可能セル用（Field オブジェクトを直接変更する）。"""
        self._original_text  = text
        self._current_item   = None
        self._current_field  = field
        self._current_record = record
        self._edit.blockSignals(True)
        self._edit.setPlainText(text)
        self._edit.blockSignals(False)
        self._set_editable(True)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _is_editable(self, item) -> bool:
        if item is None:
            return False
        field = item.data(Qt.UserRole)
        if field is None:
            return False
        ff = getattr(field, "field_format", None)
        return ff is not None and ff.is_edit

    def _set_editable(self, editable: bool) -> None:
        self._edit.setReadOnly(not editable)
        self._apply_btn.setEnabled(editable)
        self._cancel_btn.setEnabled(editable)

    def _on_apply(self) -> None:
        new_text = self._edit.toPlainText()

        if self._current_item is not None:
            # RecordGrid 経由: セルのテキストを変更 → itemChanged → _on_cell_changed
            self._current_item.setText(new_text)
            self._original_text = new_text

        elif self._current_field is not None:
            # ConflictGrid 経由: Field を直接変更
            from core.bytes_util import TesBytes
            from core.encoding import TesEncoding
            ff  = getattr(self._current_field, "field_format", None)
            enc = (self._current_record.mod_file.encoding
                   if self._current_record and self._current_record.mod_file
                   else TesEncoding.CP1252)
            null_terminate = ff is not None and ff.data_type == "zstring"
            new_bytes = TesBytes.from_str(new_text, enc, null_terminate=null_terminate)
            self._current_field.modify(new_bytes)
            self._original_text = new_text

            # RecordGrid の表示を更新（main_record が変わった場合）
            self._update_record_grid_after_conflict_edit(new_text)

    def _update_record_grid_after_conflict_edit(self, new_text: str) -> None:
        """ConflictGrid 編集後に RecordGrid と ConflictGrid の表示を同期する。"""
        rec_grid = self._main.record_grid
        row = rec_grid.currentRow()
        if row < 0:
            return
        first_item = rec_grid.item(row, 0)
        if not first_item:
            return
        info = first_item.data(Qt.UserRole + 1)
        if not info:
            return

        # main_record がこのレコードなら RecordGrid のセルも更新
        if info.main_record is self._current_record:
            field_type = self._current_field.field_type
            from core.encoding import TesEncoding
            enc = (self._current_record.mod_file.encoding
                   if self._current_record.mod_file else TesEncoding.CP1252)
            for col, ff in enumerate(rec_grid._field_fmts):
                if ff.field_name == field_type and not rec_grid.isColumnHidden(col):
                    rec_grid.blockSignals(True)
                    cell = rec_grid.item(row, col)
                    if cell:
                        cell.setText(self._current_field.to_display_str(enc))
                    rec_grid.blockSignals(False)
                    break

        # ConflictGrid を再ロード
        self._main.conflict_grid.load(info)

    def _on_cancel(self) -> None:
        self._edit.blockSignals(True)
        self._edit.setPlainText(self._original_text)
        self._edit.blockSignals(False)

    def setFont(self, font: QFont) -> None:
        self._edit.setFont(font)
