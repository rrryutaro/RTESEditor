from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class PatchPanel(QWidget):
    """アクティブパッチの内容確認・検証・除去・関連付けを行うタブ。"""

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._summary = QLabel(self.tr("編集先パッチは指定されていません。"))
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        splitter = QSplitter(Qt.Vertical)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            self.tr("種別"),
            self.tr("レコードID"),
            self.tr("親DIAL"),
            self.tr("状態"),
            self.tr("編集先ファイル"),
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.horizontalHeader().setStretchLastSection(True)
        splitter.addWidget(self._table)

        self._diff_table = QTableWidget(0, 4)
        self._diff_table.setHorizontalHeaderLabels([
            self.tr("フィールド"),
            self.tr("出現位置"),
            self.tr("参照元"),
            self.tr("編集先パッチ"),
        ])
        self._diff_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._diff_table.horizontalHeader().setStretchLastSection(True)
        splitter.addWidget(self._diff_table)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        button_row = QHBoxLayout()
        refresh_button = QPushButton(self.tr("更新"))
        validate_button = QPushButton(self.tr("パッチを検証"))
        self._assign_button = QPushButton(self.tr("親DIALを指定"))
        self._delete_in_game_button = QPushButton(self.tr("ゲーム内で削除扱い"))
        self._remove_button = QPushButton(self.tr("選択したオーバーライドを除去"))
        refresh_button.clicked.connect(self.refresh)
        validate_button.clicked.connect(self._on_validate)
        self._assign_button.clicked.connect(self._on_assign_parent)
        self._delete_in_game_button.clicked.connect(self._on_toggle_deleted)
        self._remove_button.clicked.connect(self._on_remove)
        button_row.addWidget(refresh_button)
        button_row.addWidget(validate_button)
        button_row.addWidget(self._assign_button)
        button_row.addWidget(self._delete_in_game_button)
        button_row.addWidget(self._remove_button)
        button_row.addStretch()
        layout.addLayout(button_row)
        self._update_button_state()

    def refresh(self) -> None:
        patch = self._main.manager.active_patch
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        if patch is None:
            self._summary.setText(self.tr("編集先パッチは指定されていません。"))
            self._table.blockSignals(False)
            self._diff_table.setRowCount(0)
            self._update_button_state()
            return

        dirty = self.tr("未保存") if patch.is_dirty else self.tr("保存済み")
        self._summary.setText(
            self.tr("編集先: {0}　レコード数: {1}　状態: {2}").format(
                patch.path,
                len(patch.records),
                dirty,
            )
        )
        self._table.setRowCount(len(patch.records))
        for row, record in enumerate(patch.records):
            parent = record.parent_group.label if record.parent_group is not None else ""
            modified = record.is_modified or any(field.is_modified for field in record.fields)
            if record.record_type == "INFO" and record.parent_group is None:
                state = self.tr("エラー: 親DIALなし")
            elif self._main.manager.is_record_deleted(record):
                state = self.tr("ゲーム内削除")
            elif getattr(record, "_patch_context_only", False):
                state = self.tr("INFO用の親DIAL")
            elif modified:
                state = self.tr("変更済み")
            else:
                state = self.tr("既存オーバーライド")
            values = [record.record_type, record.primary_key, parent, state, patch.file_name]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, record)
                if record.record_type == "INFO" and record.parent_group is None:
                    item.setForeground(QBrush(QColor("red")))
                elif self._main.manager.is_record_deleted(record):
                    item.setForeground(QBrush(QColor("darkred")))
                self._table.setItem(row, column, item)

        self._table.resizeColumnsToContents()
        self._table.blockSignals(False)
        self._update_button_state()
        self._refresh_diffs()

    def _selected_record(self):
        row = self._table.currentRow()
        item = self._table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item is not None else None

    def _update_button_state(self) -> None:
        record = self._selected_record()
        self._remove_button.setEnabled(record is not None)
        self._delete_in_game_button.setEnabled(record is not None)
        self._delete_in_game_button.setText(
            self.tr("ゲーム内削除を解除")
            if self._main.manager.is_record_deleted(record)
            else self.tr("ゲーム内で削除扱い")
        )
        self._assign_button.setEnabled(
            record is not None
            and record.record_type == "INFO"
            and record.parent_group is None
        )

    def _on_toggle_deleted(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        deleted = self._main.manager.is_record_deleted(record)
        action_text = (
            self.tr("ゲーム内での削除指定を解除します。")
            if deleted
            else self.tr("このレコードをゲーム内で削除扱いにします。")
        )
        answer = QMessageBox.question(
            self,
            self.tr("ゲーム内削除の変更"),
            action_text
            + self.tr(
                "\n参照元ファイルは変更されません。\n\n{0} {1}"
            ).format(record.record_type, record.primary_key),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            if deleted:
                self._main.manager.restore_record_deleted(record)
            else:
                self._main.manager.mark_record_deleted(record)
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, self.tr("変更できません"), str(exc))
            return
        self._main.refresh_after_patch_edit()

    def _on_selection_changed(self) -> None:
        self._update_button_state()
        self._refresh_diffs()

    def _refresh_diffs(self) -> None:
        record = self._selected_record()
        self._diff_table.setRowCount(0)
        if record is None:
            return
        diffs = self._main.manager.get_patch_field_diffs(record)
        if not diffs:
            self._diff_table.setRowCount(1)
            self._diff_table.setItem(0, 0, QTableWidgetItem(self.tr("(フィールド差分なし)")))
            return
        self._diff_table.setRowCount(len(diffs))
        for row, diff in enumerate(diffs):
            values = [diff.field_name, str(diff.occurrence + 1), diff.before, diff.after]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self._diff_table.setItem(row, column, item)
        self._diff_table.resizeColumnsToContents()

    def _on_remove(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        child_count = 0
        if record.record_type == "DIAL":
            child_count = sum(
                1 for candidate in self._main.manager.active_patch.records
                if candidate.record_type == "INFO"
                and candidate.parent_group is not None
                and candidate.parent_group.parent_record is record
            )
        detail = ""
        if child_count:
            detail = self.tr("\nこのDIALに属するINFO {0}件も除去されます。").format(child_count)
        answer = QMessageBox.question(
            self,
            self.tr("オーバーライドの除去"),
            self.tr(
                "編集先パッチから次のレコードを除去します。\n"
                "参照元ファイルは変更されません。\n\n{0} {1}{2}"
            ).format(record.record_type, record.primary_key, detail),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            removed = self._main.manager.remove_patch_record(record)
        except ValueError as exc:
            QMessageBox.warning(self, self.tr("除去できません"), str(exc))
            return
        self._main.refresh_after_patch_edit()
        self._main.statusBar().showMessage(
            self.tr("編集先パッチから {0}件を除去しました。").format(len(removed)),
            5000,
        )

    def _on_assign_parent(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        candidates = self._main.manager.get_parent_dialogue_candidates(record)
        if not candidates:
            QMessageBox.information(
                self,
                self.tr("親DIAL候補なし"),
                self.tr("同じINFO IDを持つ参照元から親DIAL候補を取得できませんでした。"),
            )
            return
        labels = [
            f"{candidate.primary_key}  [{candidate.mod_file.file_name}]"
            for candidate in candidates
        ]
        selected, ok = QInputDialog.getItem(
            self,
            self.tr("親DIALを指定"),
            self.tr("INFO {0} の親DIAL:").format(record.primary_key),
            labels,
            0,
            False,
        )
        if not ok:
            return
        self._main.manager.assign_patch_info_parent(
            record,
            candidates[labels.index(selected)],
        )
        self._main.refresh_after_patch_edit()

    def _on_validate(self) -> None:
        issues = self._main.manager.validate_active_patch()
        if not issues:
            QMessageBox.information(
                self,
                self.tr("パッチ検証"),
                self.tr("問題は見つかりませんでした。"),
            )
            return
        lines = [
            f"[{self.tr('エラー') if issue.severity == 'error' else self.tr('警告')}] "
            f"{issue.message}"
            for issue in issues[:50]
        ]
        if len(issues) > 50:
            lines.append(self.tr("ほか {0}件").format(len(issues) - 50))
        QMessageBox.warning(self, self.tr("パッチ検証"), "\n".join(lines))

    def setFont(self, font: QFont) -> None:
        self._summary.setFont(font)
        self._table.setFont(font)
        self._diff_table.setFont(font)
