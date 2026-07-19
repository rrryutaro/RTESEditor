from __future__ import annotations
from PySide6.QtWidgets import (
    QMainWindow, QSplitter, QTabWidget, QWidget,
    QVBoxLayout, QHBoxLayout, QLineEdit, QStatusBar,
    QMenuBar, QMenu, QLabel, QProgressBar, QApplication,
)
from PySide6.QtCore import Qt, QCoreApplication, QByteArray
from app.mod_manager import ModManager
from app.record_labels import record_type_label
from ui.tree_panel import TreePanel
from ui.record_grid import RecordGrid
from ui.conflict_grid import ConflictGrid
from ui.text_panel import TextPanel
from ui.dialogue_panel import DialoguePanel
from ui.patch_panel import PatchPanel


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self._manager = ModManager()
        self._setup_ui()
        self._setup_menu()
        self._setup_shortcuts()
        self.setWindowTitle("RTESEditor")
        self._restore_font()
        self._restore_geometry()

    # ------------------------------------------------------------------
    # UI構築
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)

        # タブ（共通 / ダイアログ）をルートに配置
        self._tabs = QTabWidget()
        root.addWidget(self._tabs)

        # ----------------------------------------------------------------
        # 共通タブ
        # ----------------------------------------------------------------
        common_widget = QWidget()
        common_layout = QVBoxLayout(common_widget)
        common_layout.setContentsMargins(4, 4, 4, 4)

        # 検索バー（共通タブ内）
        search_bar = QHBoxLayout()
        search_bar.addWidget(QLabel(self.tr("検索:")))
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText(self.tr("Ctrl+F"))
        self._search_box.returnPressed.connect(self._on_search)
        search_bar.addWidget(self._search_box)
        common_layout.addLayout(search_bar)

        # 横スプリッター（ツリー | レコード領域）
        self._common_h_splitter = QSplitter(Qt.Horizontal)

        self._tree = TreePanel(self)
        self._common_h_splitter.addWidget(self._tree)
        self._common_h_splitter.setStretchFactor(0, 1)

        # 縦スプリッター（RecordGrid / ConflictGrid / TextPanel）
        self._common_v_splitter = QSplitter(Qt.Vertical)
        self._record_grid   = RecordGrid(self)
        self._conflict_grid = ConflictGrid(self)
        self._text_panel    = TextPanel(self)
        self._common_v_splitter.addWidget(self._record_grid)
        self._common_v_splitter.addWidget(self._conflict_grid)
        self._common_v_splitter.addWidget(self._text_panel)
        self._common_v_splitter.setStretchFactor(0, 5)
        self._common_v_splitter.setStretchFactor(1, 3)
        self._common_v_splitter.setStretchFactor(2, 2)

        self._common_h_splitter.addWidget(self._common_v_splitter)
        self._common_h_splitter.setStretchFactor(1, 4)

        common_layout.addWidget(self._common_h_splitter, 1)
        self._tabs.addTab(common_widget, self.tr("共通"))

        # ----------------------------------------------------------------
        # ダイアログタブ
        # ----------------------------------------------------------------
        self._dialogue_panel = DialoguePanel(self)
        self._tabs.addTab(self._dialogue_panel, self.tr("ダイアログ"))

        self._patch_panel = PatchPanel(self)
        self._tabs.addTab(self._patch_panel, self.tr("編集先パッチ"))

        self._tabs.currentChanged.connect(self._on_tab_changed)

        # ステータスバー
        self._status_record = QLabel()
        self._status_count  = QLabel()
        self._status_patch = QLabel(self.tr("編集先: なし（参照のみ）"))
        self._progress_bar  = QProgressBar()
        self._progress_bar.setFixedHeight(16)
        self._progress_bar.setMaximumWidth(300)
        self._progress_bar.setVisible(False)
        status_bar = QStatusBar()
        status_bar.addWidget(self._status_record)
        status_bar.addWidget(self._progress_bar, 1)
        status_bar.addPermanentWidget(self._status_patch)
        status_bar.addPermanentWidget(self._status_count)
        ver = QCoreApplication.applicationVersion()
        if ver:
            ver_label = QLabel(f" v{ver} ")
            ver_label.setStyleSheet("color: gray;")
            status_bar.addPermanentWidget(ver_label)
        self.setStatusBar(status_bar)

    def _setup_menu(self) -> None:
        mb = self.menuBar()

        file_menu: QMenu = mb.addMenu(self.tr("ファイル"))
        file_menu.addAction(self.tr("開く"), self._on_open)
        file_menu.addAction(self.tr("保存"), self._on_save)
        file_menu.addAction(self.tr("名前を付けて保存"), self._on_save_as)
        file_menu.addSeparator()
        file_menu.addAction(self.tr("エクスポート (TSV)"), self._on_export)
        file_menu.addAction(self.tr("インポート (TSV)"), self._on_import)
        file_menu.addAction(self.tr("エクスポート (ローカライズJSON)"), self._on_export_localization_json)
        file_menu.addAction(self.tr("インポート (ローカライズJSON)"), self._on_import_localization_json)

        edit_menu: QMenu = mb.addMenu(self.tr("編集"))
        self._undo_action = edit_menu.addAction(self.tr("元に戻す"), self._on_undo)
        self._redo_action = edit_menu.addAction(self.tr("やり直す"), self._on_redo)
        self._undo_action.setShortcut("Ctrl+Z")
        self._redo_action.setShortcut("Ctrl+Y")
        self._update_history_actions()

        view_menu: QMenu = mb.addMenu(self.tr("表示"))
        view_menu.addAction(self.tr("フォント設定"), self._on_font_setting)
        view_menu.addSeparator()
        theme_menu: QMenu = view_menu.addMenu(self.tr("テーマ"))
        theme_menu.addAction(self.tr("標準"), lambda: self._on_set_theme("standard"))
        theme_menu.addAction(self.tr("ダーク"), lambda: self._on_set_theme("dark"))
        view_menu.addSeparator()
        self._topmost_action = view_menu.addAction(self.tr("常に最前面"))
        self._topmost_action.setCheckable(True)
        self._topmost_action.toggled.connect(self._on_topmost_toggled)

    def _setup_shortcuts(self) -> None:
        from PySide6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence("Ctrl+F"), self, self._search_box.setFocus)
        QShortcut(QKeySequence("Ctrl+R"), self, self._record_grid.setFocus)

    # ------------------------------------------------------------------
    # プロパティ（子ウィジェットからアクセス用）
    # ------------------------------------------------------------------

    @property
    def manager(self) -> ModManager:
        return self._manager

    @property
    def tree(self) -> TreePanel:
        return self._tree

    @property
    def record_grid(self) -> RecordGrid:
        return self._record_grid

    @property
    def conflict_grid(self) -> ConflictGrid:
        return self._conflict_grid

    @property
    def text_panel(self) -> TextPanel:
        return self._text_panel

    @property
    def dialogue_panel(self) -> DialoguePanel:
        return self._dialogue_panel

    @property
    def search_text(self) -> str:
        return self._search_box.text()

    # ------------------------------------------------------------------
    # スロット
    # ------------------------------------------------------------------

    def _on_open(self) -> None:
        from ui.dialogs.load_dialog import LoadDialog
        dlg = LoadDialog(self)
        if not dlg.exec():
            return
        entries = dlg.selected_entries
        if not entries:
            return
        if not self._confirm_discard_changes():
            return

        # 全ファイルを一時Managerへ読み込み、成功した場合だけ表示中の
        # プロジェクトと入れ替える。途中失敗で現在の表示を失わない。
        candidate_manager = ModManager()

        total = len(entries)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)

        try:
            for i, entry in enumerate(entries):
                label = entry.path.name
                self._progress_bar.setFormat(f"{label}  ({i + 1}/{total})  %p%")
                last_pct = [-1]

                def _make_cb(bar, pct_state):
                    def _cb(pos, length):
                        if length <= 0:
                            return
                        pct = int(pos * 100 / length)
                        if pct != pct_state[0]:
                            pct_state[0] = pct
                            bar.setValue(pct)
                            QApplication.processEvents()
                    return _cb

                if (
                    entry.is_save
                    and entry.create_if_missing
                    and not entry.path.exists()
                ):
                    candidate_manager.create_patch(entry.path, entry.encoding)
                else:
                    candidate_manager.load_mod(
                        entry.path,
                        entry.encoding,
                        is_search_target=entry.is_search_target,
                        on_progress=_make_cb(self._progress_bar, last_pct),
                        role=entry.role,
                    )
        except (OSError, RuntimeError, ValueError) as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                self.tr("読込エラー"),
                self.tr(
                    "新しいプロジェクトを読み込めなかったため、現在の表示を維持しました。\n\n"
                ) + str(exc),
            )
            return
        finally:
            self._progress_bar.setVisible(False)

        repaired, warnings = candidate_manager.repair_orphan_patch_infos()
        added_masters = candidate_manager.ensure_patch_masters()
        self._manager = candidate_manager
        self._record_grid.clear_project()
        self._dialogue_panel.refresh()
        self._tree.build(self._manager.all_records, self._manager.format_loader)
        self._patch_panel.refresh()
        self._update_history_actions()
        patch = self._manager.active_patch
        self._status_patch.setText(
            self.tr("編集先: {0}").format(patch.file_name)
            if patch is not None
            else self.tr("編集先: なし（参照のみ）")
        )
        if repaired or warnings or added_masters:
            from PySide6.QtWidgets import QMessageBox
            message = ""
            if repaired:
                message += self.tr(
                    "既存パッチ内の孤立INFO {0}件に親DIALを復元しました。\n"
                    "保存するとOpenMWで解釈できる並びへ修正されます。"
                ).format(repaired)
            if added_masters:
                if message:
                    message += "\n\n"
                message += self.tr(
                    "編集先パッチの依存元（MAST）を {0}件追加しました。"
                ).format(added_masters)
            if warnings:
                if message:
                    message += "\n\n"
                message += "\n".join(warnings[:10])
            QMessageBox.information(self, self.tr("編集先パッチの確認"), message)

    def _on_save(self) -> None:
        self._save_active_patch(ask_confirmation=True)

    def _save_active_patch(self, *, ask_confirmation: bool) -> bool:
        from PySide6.QtWidgets import QMessageBox
        patch = self._manager.active_patch
        if patch is None:
            QMessageBox.information(
                self,
                self.tr("編集先パッチがありません"),
                self.tr("「開く」で既存または新規の編集先パッチを指定してください。"),
            )
            return False
        if not self._confirm_patch_validation():
            return False
        if ask_confirmation:
            answer = QMessageBox.question(
                self, self.tr("保存の確認"),
                self.tr(
                    "編集先パッチだけを保存します。参照元ESM/ESPは変更しません。\n\n  {0}\n\n"
                    "よろしいですか？"
                ).format(patch.file_name),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if answer != QMessageBox.Yes:
                return False
        try:
            count = patch.save()
            self._mark_patch_clean(patch)
            self._manager.history.clear()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, self.tr("保存エラー"), str(exc))
            return False
        QMessageBox.information(
            self,
            self.tr("保存完了"),
            self._save_complete_message(patch, count),
        )
        self._patch_panel.refresh()
        self._update_history_actions()
        return True

    def _on_save_as(self) -> None:
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        patch = self._manager.active_patch
        if patch is None:
            QMessageBox.information(
                self, self.tr("情報"),
                self.tr("編集先パッチがありません。\n「開く」で編集先パッチを指定してください。")
            )
            return
        if not self._confirm_patch_validation():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("名前を付けて保存"),
            str(patch.path),
            self.tr("TES3 Plugin (*.esp);;All Files (*)")
        )
        if path:
            try:
                from tes3.patch_writer import save_patch
                count = save_patch(patch, Path(path))
                patch.path = Path(path)
                self._mark_patch_clean(patch)
                self._manager.history.clear()
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, self.tr("保存エラー"), str(exc))
                return
            self._status_patch.setText(self.tr("編集先: {0}").format(patch.file_name))
            QMessageBox.information(
                self,
                self.tr("保存完了"),
                self._save_complete_message(patch, count),
            )
            self._patch_panel.refresh()
            self._update_history_actions()

    @staticmethod
    def _mark_patch_clean(patch) -> None:
        for record in [patch.header_record, *patch.records]:
            if record is None:
                continue
            record.is_modified = False
            for field in record.fields:
                field.is_modified = False
        patch.structure_modified = False

    def _confirm_patch_validation(self) -> bool:
        from PySide6.QtWidgets import QMessageBox

        issues = self._manager.validate_active_patch()
        errors = [issue.message for issue in issues if issue.severity == "error"]
        warnings = [issue.message for issue in issues if issue.severity == "warning"]
        if errors:
            message = "\n".join(errors[:20])
            if len(errors) > 20:
                message += self.tr("\nほか {0}件").format(len(errors) - 20)
            QMessageBox.critical(
                self,
                self.tr("パッチを保存できません"),
                self.tr("次のエラーを修正してください。\n\n") + message,
            )
            return False
        if warnings:
            message = "\n".join(warnings[:20])
            if len(warnings) > 20:
                message += self.tr("\nほか {0}件").format(len(warnings) - 20)
            answer = QMessageBox.question(
                self,
                self.tr("パッチ検証の警告"),
                self.tr("次の警告があります。保存を続行しますか？\n\n") + message,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            return answer == QMessageBox.Yes
        return True

    def _save_complete_message(self, patch, count: int) -> str:
        message = self.tr("{0}件のレコードを編集先パッチへ保存しました。").format(count)
        if patch.last_backup_path is not None:
            message += self.tr("\n\n保存前のファイルを次へバックアップしました。\n{0}").format(
                patch.last_backup_path
            )
        return message

    def refresh_after_patch_edit(self) -> None:
        """コピー・アズ・オーバーライド後に全表示を再同期する。"""
        self._record_grid.refresh()
        self._dialogue_panel.refresh()
        self._patch_panel.refresh()
        self._update_history_actions()
        patch = self._manager.active_patch
        if patch is not None:
            self.statusBar().showMessage(
                self.tr("{0} に編集内容を追加しました。").format(patch.file_name),
                5000,
            )

    def _on_undo(self) -> None:
        action = self._manager.history.undo()
        if action is None:
            return
        self.refresh_after_patch_edit()
        self.statusBar().showMessage(
            self.tr("元に戻しました: {0}").format(action.description),
            5000,
        )

    def _on_redo(self) -> None:
        action = self._manager.history.redo()
        if action is None:
            return
        self.refresh_after_patch_edit()
        self.statusBar().showMessage(
            self.tr("やり直しました: {0}").format(action.description),
            5000,
        )

    def _update_history_actions(self) -> None:
        if not hasattr(self, "_undo_action"):
            return
        history = self._manager.history
        undo_text = self.tr("元に戻す")
        redo_text = self.tr("やり直す")
        if history.undo_description:
            undo_text += f": {history.undo_description}"
        if history.redo_description:
            redo_text += f": {history.redo_description}"
        self._undo_action.setText(undo_text)
        self._redo_action.setText(redo_text)
        self._undo_action.setEnabled(history.can_undo)
        self._redo_action.setEnabled(history.can_redo)

    def _on_export(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from app.export_import import export_tsv
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("エクスポート"),
            "", self.tr("TSV ファイル (*.tsv);;すべてのファイル (*)")
        )
        if not path:
            return
        count = export_tsv(self._manager, path)
        QMessageBox.information(self, self.tr("エクスポート完了"), f"{count} 件を書き出しました。")

    def _on_import(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from app.export_import import import_tsv
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("インポート"),
            "", self.tr("TSV ファイル (*.tsv);;すべてのファイル (*)")
        )
        if not path:
            return
        try:
            count = import_tsv(self._manager, path)
        except (UnicodeEncodeError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, self.tr("インポートエラー"), str(exc))
            return
        self.refresh_after_patch_edit()
        QMessageBox.information(self, self.tr("インポート完了"), f"{count} 件を更新しました。")

    def _on_export_localization_json(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from app.localization_json import export_localization_json
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("ローカライズJSONエクスポート"),
            "", self.tr("JSON ファイル (*.json);;すべてのファイル (*)")
        )
        if not path:
            return
        count = export_localization_json(self._manager, path)
        QMessageBox.information(
            self,
            self.tr("エクスポート完了"),
            f"{count} 件を書き出しました。"
        )

    def _on_import_localization_json(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from app.localization_json import import_localization_json
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("ローカライズJSONインポート"),
            "", self.tr("JSON ファイル (*.json);;すべてのファイル (*)")
        )
        if not path:
            return
        try:
            result = import_localization_json(self._manager, path)
        except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, self.tr("インポートエラー"), str(exc))
            return
        self.refresh_after_patch_edit()
        message = (
            f"{result.updated} 件を更新しました。\n"
            f"{result.skipped} 件をスキップしました。"
        )
        warnings = result.warnings or []
        if warnings:
            preview = "\n".join(warnings[:10])
            if len(warnings) > 10:
                preview += f"\n...他 {len(warnings) - 10} 件"
            message += "\n\n警告:\n" + preview
        QMessageBox.information(self, self.tr("インポート完了"), message)

    def _on_font_setting(self) -> None:
        from PySide6.QtWidgets import QFontDialog
        from PySide6.QtGui import QFont
        from app.settings import Settings
        s = Settings.instance()
        family = s.get_font_family()
        size   = s.get_font_size()
        current = QFont(family, size) if family else QFont()
        ok, font = QFontDialog.getFont(current, self)
        if ok:
            s.set_font(font.family(), font.pointSize())
            self._apply_font(font)

    def _restore_font(self) -> None:
        from PySide6.QtGui import QFont
        from app.settings import Settings
        s = Settings.instance()
        family = s.get_font_family()
        size   = s.get_font_size()
        if family:
            self._apply_font(QFont(family, size if size > 0 else 10))

    def _apply_font(self, font) -> None:
        self._record_grid.setFont(font)
        self._conflict_grid.setFont(font)
        self._text_panel.setFont(font)
        self._dialogue_panel.setFont(font)
        self._patch_panel.setFont(font)

    def _on_topmost_toggled(self, checked: bool) -> None:
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def _on_set_theme(self, theme: str) -> None:
        from app.settings import Settings
        from ui.theme import apply_theme
        from PySide6.QtWidgets import QApplication
        Settings.instance().set_theme(theme)
        apply_theme(QApplication.instance(), theme)

    def _on_tab_changed(self, index: int) -> None:
        if self._tabs.widget(index) is self._dialogue_panel:
            self._dialogue_panel.refresh()
        elif self._tabs.widget(index) is self._patch_panel:
            self._patch_panel.refresh()

    def _on_search(self) -> None:
        self._record_grid.refresh()

    def set_status(self, record_name: str, count: int) -> None:
        self._status_record.setText(f"{record_type_label(record_name)}:")
        self._status_count.setText(str(count))

    # ------------------------------------------------------------------
    # ウィンドウ位置・サイズ・スプリッターの保存と復元
    # ------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        from app.settings import Settings
        s = Settings.instance()
        encoded = s.get_geometry()
        if encoded:
            self.restoreGeometry(QByteArray.fromBase64(encoded.encode("ascii")))
        else:
            self.resize(1200, 800)
        # スプリッター状態復元
        for key, splitter in [
            ("common_h", self._common_h_splitter),
            ("common_v", self._common_v_splitter),
        ]:
            enc = s.get_splitter_state(key)
            if enc:
                splitter.restoreState(QByteArray.fromBase64(enc.encode("ascii")))

    def closeEvent(self, event) -> None:
        if not self._confirm_discard_changes():
            event.ignore()
            return
        from app.settings import Settings
        s = Settings.instance()
        s.set_geometry(self.saveGeometry().toBase64().data().decode("ascii"))
        s.set_splitter_state("common_h",
            self._common_h_splitter.saveState().toBase64().data().decode("ascii"))
        s.set_splitter_state("common_v",
            self._common_v_splitter.saveState().toBase64().data().decode("ascii"))
        self._dialogue_panel.save_splitter_states()
        super().closeEvent(event)

    def _confirm_discard_changes(self) -> bool:
        patch = self._manager.active_patch
        if patch is None or not patch.is_dirty:
            return True
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(self.tr("未保存の変更"))
        box.setText(
            self.tr("編集先パッチ「{0}」に未保存の変更があります。").format(
                patch.file_name
            )
        )
        box.setInformativeText(self.tr("終了または開き直す前に保存しますか？"))
        box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Save)
        answer = box.exec()
        if answer == QMessageBox.Save:
            return self._save_active_patch(ask_confirmation=False)
        return answer == QMessageBox.Discard
