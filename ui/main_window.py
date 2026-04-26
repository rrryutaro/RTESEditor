from __future__ import annotations
from PySide6.QtWidgets import (
    QMainWindow, QSplitter, QTabWidget, QWidget,
    QVBoxLayout, QHBoxLayout, QLineEdit, QStatusBar,
    QMenuBar, QMenu, QLabel,
)
from PySide6.QtCore import Qt, QCoreApplication
from app.mod_manager import ModManager
from ui.tree_panel import TreePanel
from ui.record_grid import RecordGrid
from ui.conflict_grid import ConflictGrid
from ui.text_panel import TextPanel


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self._manager = ModManager()
        self._setup_ui()
        self._setup_menu()
        self._setup_shortcuts()
        self.setWindowTitle("RTESEditor")
        self._restore_font()

    # ------------------------------------------------------------------
    # UI構築
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)

        # 検索バー
        search_bar = QHBoxLayout()
        search_bar.addWidget(QLabel(self.tr("検索:")))
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText(self.tr("Ctrl+F"))
        self._search_box.returnPressed.connect(self._on_search)
        search_bar.addWidget(self._search_box)
        root.addLayout(search_bar)

        # メインスプリッター（ツリー | タブ）
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        self._tree = TreePanel(self)
        splitter.addWidget(self._tree)
        splitter.setStretchFactor(0, 1)

        # タブ（Common / Dialogue）
        self._tabs = QTabWidget()
        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(1, 4)

        # --- Common タブ ---
        common_widget = QWidget()
        common_layout = QVBoxLayout(common_widget)
        common_layout.setContentsMargins(0, 0, 0, 0)
        v_splitter = QSplitter(Qt.Vertical)
        self._record_grid  = RecordGrid(self)
        self._conflict_grid = ConflictGrid(self)
        self._text_panel   = TextPanel(self)
        v_splitter.addWidget(self._record_grid)
        v_splitter.addWidget(self._conflict_grid)
        v_splitter.addWidget(self._text_panel)
        v_splitter.setStretchFactor(0, 5)
        v_splitter.setStretchFactor(1, 3)
        v_splitter.setStretchFactor(2, 2)
        common_layout.addWidget(v_splitter)
        self._tabs.addTab(common_widget, self.tr("共通"))

        self._dialogue_panel = None  # TODO: DIAL/INFO実装後に追加

        self._tabs.currentChanged.connect(self._on_tab_changed)

        # ステータスバー
        self._status_record = QLabel()
        self._status_count  = QLabel()
        status_bar = QStatusBar()
        status_bar.addWidget(self._status_record)
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
        file_menu.addAction(self.tr("修正箇所のみ書き出し"), self._on_save_diff)
        file_menu.addSeparator()
        file_menu.addAction(self.tr("エクスポート (TSV)"), self._on_export)
        file_menu.addAction(self.tr("インポート (TSV)"), self._on_import)

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
    def search_text(self) -> str:
        return self._search_box.text()

    # ------------------------------------------------------------------
    # スロット
    # ------------------------------------------------------------------

    def _on_open(self) -> None:
        from ui.dialogs.load_dialog import LoadDialog
        dlg = LoadDialog(self)
        if dlg.exec():
            self._manager.clear()
            self._tree.clear()
            for entry in dlg.selected_entries:
                self._manager.load_mod(
                    entry.path, entry.encoding,
                    entry.is_overwrite, entry.is_save,
                    entry.is_search_target,
                )
            self._tree.build(self._manager.all_records, self._manager.format_loader)

    def _on_save(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        save_mods = [m for m in self._manager.mod_files if m.is_save]
        if not save_mods:
            return
        names = "\n".join(f"  {m.file_name}" for m in save_mods)
        ret = QMessageBox.question(
            self, self.tr("保存の確認"),
            self.tr("以下のファイルを上書き保存します。よろしいですか？\n\n") + names,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return
        for mod in save_mods:
            mod.save()

    def _on_save_as(self) -> None:
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        save_mods = [m for m in self._manager.mod_files if m.is_save]
        if not save_mods:
            QMessageBox.information(
                self, self.tr("情報"),
                self.tr("保存対象のファイルがありません。\nファイルを開く際に「保存対象」にチェックを入れてください。")
            )
            return
        mod = save_mods[0]
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("名前を付けて保存"),
            str(mod.path),
            self.tr("TES3 Mod Files (*.esp *.esm);;All Files (*)")
        )
        if path:
            mod.path = Path(path)
            mod.save()

    def _on_save_diff(self) -> None:
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        all_modified = [
            r for m in self._manager.mod_files for r in m.records if r.is_modified
        ]
        if not all_modified:
            QMessageBox.information(self, self.tr("情報"), self.tr("修正済みのレコードがありません。"))
            return
        # ファイルダイアログの初期パスは is_save な Mod のパスを優先
        save_mods = [m for m in self._manager.mod_files if m.is_save]
        default_path = str(save_mods[0].path) if save_mods else ""
        # ヘッダ書き出しに使う Mod（is_save 優先、なければ最初の Mod）
        header_mod = save_mods[0] if save_mods else self._manager.mod_files[0]
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("修正箇所のみ書き出し"),
            default_path,
            self.tr("TES3 Mod Files (*.esp *.esm);;All Files (*)")
        )
        if path:
            count = self._save_diff_merge(header_mod, all_modified, Path(path))
            QMessageBox.information(
                self, self.tr("書き出し完了"),
                f"{count} 件のレコードを書き出しました。"
            )

    def _save_diff_merge(self, header_mod, modified_records: list, target) -> int:
        from pathlib import Path
        from tes3.reader import Tes3Reader
        target = Path(target)
        existing: dict[tuple, object] = {}
        if target.exists():
            try:
                reader = Tes3Reader(self._manager.format_loader)
                exist_mod = reader.load(target, header_mod.encoding, False, False)
                for rec in exist_mod.records:
                    existing[(rec.record_type, rec.primary_key)] = rec
            except Exception:
                pass
        new_mods = {(r.record_type, r.primary_key): r for r in modified_records}
        existing.update(new_mods)
        buf = bytearray()
        if header_mod.header_record:
            header_mod.header_record.write(buf)
        for rec in existing.values():
            rec.write(buf)
        target.write_bytes(bytes(buf))
        return len(new_mods)

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
        count = import_tsv(self._manager, path)
        self._record_grid.refresh()
        QMessageBox.information(self, self.tr("インポート完了"), f"{count} 件を更新しました。")


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
        pass  # 将来ダイアログタブ追加時に実装

    def _on_search(self) -> None:
        self._record_grid.refresh()

    def set_status(self, record_name: str, count: int) -> None:
        self._status_record.setText(f"{record_name}:")
        self._status_count.setText(str(count))
