from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class TextPanel(QWidget):
    """編集内容を必ずアクティブパッチへ反映する長文テキストパネル。"""

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self._current_field = None
        self._current_record = None
        self._original_text = ""
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._edit = QPlainTextEdit()
        self._edit.setTabStopDistance(32)
        layout.addWidget(self._edit)

        button_row = QHBoxLayout()
        self._apply_btn = QPushButton(self.tr("編集先パッチへ適用"))
        self._cancel_btn = QPushButton(self.tr("キャンセル"))
        self._apply_btn.clicked.connect(self._on_apply)
        self._cancel_btn.clicked.connect(self._on_cancel)
        button_row.addStretch()
        button_row.addWidget(self._apply_btn)
        button_row.addWidget(self._cancel_btn)
        layout.addLayout(button_row)
        self._set_editable(False)

    def set_text(self, text: str, item=None) -> None:
        """参照専用テキストを表示する。item引数は旧呼出しとの互換用。"""
        if item is not None:
            field = item.data(Qt.UserRole)
            record = item.data(Qt.UserRole + 1)
            self.set_record_field(text, field, record)
            return
        self._set_value(text, None, None, False)

    def set_record_field(self, text: str, field, record) -> None:
        field_format = getattr(field, "field_format", None)
        editable = (
            field is not None
            and record is not None
            and field_format is not None
            and field_format.is_edit
            and self._main.manager.active_patch is not None
        )
        self._set_value(text, field, record, editable)

    def set_conflict_cell(self, text: str, field, record) -> None:
        """旧APIとの互換用。"""
        self.set_record_field(text, field, record)

    def _set_value(self, text: str, field, record, editable: bool) -> None:
        self._original_text = text
        self._current_field = field
        self._current_record = record
        self._edit.blockSignals(True)
        self._edit.setPlainText(text)
        self._edit.blockSignals(False)
        self._set_editable(editable)

    def _set_editable(self, editable: bool) -> None:
        self._edit.setReadOnly(not editable)
        self._apply_btn.setEnabled(editable)
        self._cancel_btn.setEnabled(editable)

    def _on_apply(self) -> None:
        if self._current_field is None or self._current_record is None:
            return
        from core.bytes_util import TesBytes

        new_text = self._edit.toPlainText()
        if new_text == self._original_text:
            self._main.statusBar().showMessage(self.tr("変更はありません。"), 3000)
            return

        try:
            field_format = getattr(self._current_field, "field_format", None)
            patch = self._main.manager.active_patch
            if patch is None:
                raise RuntimeError("編集先パッチが指定されていません。")
            # 変換に失敗した場合、オーバーライドを作る前に終了する。
            data = TesBytes.from_str(
                new_text,
                patch.encoding,
                null_terminate=(
                    field_format is not None and field_format.data_type == "zstring"
                ),
            )
            target_field, target_record = self._main.manager.prepare_field_for_edit(
                self._current_record,
                self._current_field,
            )
            self._main.manager.apply_field_data(
                target_field,
                target_record,
                data,
            )
        except (UnicodeEncodeError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, self.tr("編集を適用できません"), str(exc))
            return

        self._original_text = new_text
        self._current_field = target_field
        self._current_record = target_record
        self._main.refresh_after_patch_edit()

    def _on_cancel(self) -> None:
        self._edit.blockSignals(True)
        self._edit.setPlainText(self._original_text)
        self._edit.blockSignals(False)

    def setFont(self, font: QFont) -> None:
        self._edit.setFont(font)
