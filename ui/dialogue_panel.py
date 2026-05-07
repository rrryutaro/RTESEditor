from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QCheckBox, QHeaderView, QLabel, QLineEdit, QMenu,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
)
from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QFont
from ui.conflict_grid import ConflictGrid
from ui.text_panel import TextPanel

# DIAL.DATA の uint8 値 → 種別名
_DIAL_TYPE_NAMES = {0: "Regular", 1: "Voice", 2: "Greeting", 3: "Persuasion", 4: "Journal"}
# 表示順（0=Regular, 2=Greeting, 1=Voice, 3=Persuasion, 4=Journal）
_DIAL_TYPE_ORDER = [0, 2, 1, 3, 4]

# INFO テーブルのカラム定義
_INFO_COLS = ["INAM", "Actor", "Race", "Class", "Faction", "Text", "Cond"]


class _DlgConflictGrid(ConflictGrid):
    """DialoguePanel 専用 ConflictGrid。テキスト更新先を dialogue の TextPanel に向ける。"""

    def __init__(self, dlg: "DialoguePanel"):
        super().__init__(dlg._main)
        self._dlg = dlg

    def _on_current_changed(self, current, _previous) -> None:
        if current is None or current.column() == 0:
            return
        field  = current.data(Qt.UserRole)
        record = current.data(Qt.UserRole + 1)
        ff      = getattr(field, "field_format", None)
        is_edit = ff is not None and ff.is_edit
        is_save = (record is not None and
                   record.mod_file is not None and
                   record.mod_file.is_save)
        if field is not None and is_edit and is_save:
            self._dlg._text_panel.set_conflict_cell(current.text(), field, record)
        else:
            self._dlg._text_panel.set_text(current.text())


class DialoguePanel(QWidget):
    """ダイアログ（DIAL/INFO）専用タブパネル"""

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self._current_dial_key: str | None = None
        self._current_actor: str | None = None  # None = 全アクター（ONAM小文字キー）
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI構築
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        # ── 最外スプリッター（アクターパネル | 右コンテンツ）────────────
        self._outer_h_splitter = QSplitter(Qt.Horizontal)

        # ── 左: アクターパネル ─────────────────────────────────────────
        actor_widget = QWidget()
        actor_layout = QVBoxLayout(actor_widget)
        actor_layout.setContentsMargins(0, 0, 4, 0)

        self._actor_search = QLineEdit()
        self._actor_search.setPlaceholderText(self.tr("絞り込み..."))
        self._actor_search.textChanged.connect(self._on_actor_search_changed)
        actor_layout.addWidget(self._actor_search)

        self._actor_tree = QTreeWidget()
        self._actor_tree.setHeaderHidden(True)
        self._actor_tree.currentItemChanged.connect(self._on_actor_selected)
        actor_layout.addWidget(self._actor_tree, 1)

        self._outer_h_splitter.addWidget(actor_widget)

        # ── 右: コンテンツ ─────────────────────────────────────────────
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # フィルタ行
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(self.tr("種別:")))
        # 種別ごとの ON/OFF チェックボックス（表示順: _DIAL_TYPE_ORDER）
        self._type_checks: dict[int, QCheckBox] = {}
        for type_id in _DIAL_TYPE_ORDER:
            chk = QCheckBox(_DIAL_TYPE_NAMES[type_id])
            chk.setChecked(True)
            chk.toggled.connect(self._on_filter_changed)
            filter_row.addWidget(chk)
            self._type_checks[type_id] = chk
        filter_row.addStretch(1)
        filter_row.addWidget(QLabel(self.tr("DIAL検索:")))
        self._dial_search_box = QLineEdit()
        self._dial_search_box.setPlaceholderText(self.tr("トピック名..."))
        self._dial_search_box.setMaximumWidth(150)
        self._dial_search_box.textChanged.connect(self._on_dial_search_changed)
        filter_row.addWidget(self._dial_search_box)
        filter_row.addSpacing(8)
        filter_row.addWidget(QLabel(self.tr("INFO検索:")))
        self._info_search_box = QLineEdit()
        self._info_search_box.setPlaceholderText(self.tr("応答テキスト..."))
        self._info_search_box.setMaximumWidth(150)
        self._info_search_box.textChanged.connect(self._on_info_search_changed)
        filter_row.addWidget(self._info_search_box)
        filter_row.addSpacing(8)
        filter_row.addWidget(QLabel(self.tr("全体検索:")))
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Ctrl+D")
        self._search_box.setMaximumWidth(150)
        self._search_box.textChanged.connect(self._on_search_changed)
        filter_row.addWidget(self._search_box)
        right_layout.addLayout(filter_row)

        # ── メイン縦スプリッター（中段エリア | TextPanel）────────────────
        self._main_v_splitter = QSplitter(Qt.Vertical)

        # 中段: 横スプリッター（DIAL列 | INFO列）
        self._mid_h_splitter = QSplitter(Qt.Horizontal)

        # ── DIAL列: 縦スプリッター（DIALツリー | DIAL ConflictGrid）────
        self._dial_v_splitter = QSplitter(Qt.Vertical)

        self._dial_tree = QTreeWidget()
        self._dial_tree.setHeaderLabels(
            [self.tr("トピック (DIAL)"), self.tr("種別"), self.tr("応答数")]
        )
        self._dial_tree.setRootIsDecorated(True)
        self._dial_tree.setColumnWidth(0, 200)
        self._dial_tree.setColumnWidth(1, 70)
        self._dial_tree.currentItemChanged.connect(self._on_dial_selected)

        self._dial_conflict_grid = _DlgConflictGrid(self)

        self._dial_v_splitter.addWidget(self._dial_tree)
        self._dial_v_splitter.addWidget(self._dial_conflict_grid)
        self._dial_v_splitter.setStretchFactor(0, 3)
        self._dial_v_splitter.setStretchFactor(1, 2)

        # ── INFO列: 縦スプリッター（INFOテーブル | INFO ConflictGrid）──
        self._info_v_splitter = QSplitter(Qt.Vertical)

        self._info_table = QTableWidget()
        self._info_table.setColumnCount(len(_INFO_COLS))
        self._info_table.setHorizontalHeaderLabels(_INFO_COLS)
        self._info_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._info_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._info_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self._info_table.currentItemChanged.connect(self._on_info_selected)
        self._info_table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self._info_table.horizontalHeader().customContextMenuRequested.connect(
            self._on_info_header_menu
        )

        self._info_conflict_grid = _DlgConflictGrid(self)

        self._info_v_splitter.addWidget(self._info_table)
        self._info_v_splitter.addWidget(self._info_conflict_grid)
        self._info_v_splitter.setStretchFactor(0, 3)
        self._info_v_splitter.setStretchFactor(1, 2)

        self._mid_h_splitter.addWidget(self._dial_v_splitter)
        self._mid_h_splitter.addWidget(self._info_v_splitter)
        self._mid_h_splitter.setStretchFactor(0, 2)
        self._mid_h_splitter.setStretchFactor(1, 5)

        # TextPanel（最下部）
        self._text_panel = TextPanel(self._main)

        self._main_v_splitter.addWidget(self._mid_h_splitter)
        self._main_v_splitter.addWidget(self._text_panel)
        self._main_v_splitter.setStretchFactor(0, 8)
        self._main_v_splitter.setStretchFactor(1, 2)

        right_layout.addWidget(self._main_v_splitter, 1)
        self._outer_h_splitter.addWidget(right_widget)
        self._outer_h_splitter.setStretchFactor(0, 1)
        self._outer_h_splitter.setStretchFactor(1, 5)

        root.addWidget(self._outer_h_splitter, 1)

        # スプリッター状態復元
        self._restore_splitter_states()
        # INFOテーブル カラム表示復元
        self._apply_info_column_visibility()

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """タブアクティブ時に呼ばれる。アクターパネルと DIAL ツリーを再構築する。"""
        self._rebuild_actor_panel()
        self._rebuild_dial_tree()

    def setFont(self, font: QFont) -> None:
        self._actor_tree.setFont(font)
        self._dial_tree.setFont(font)
        self._dial_conflict_grid.setFont(font)
        self._info_table.setFont(font)
        self._info_conflict_grid.setFont(font)
        self._text_panel.setFont(font)

    # ------------------------------------------------------------------
    # アクターパネル再構築
    # ------------------------------------------------------------------

    def _rebuild_actor_panel(self) -> None:
        prev_actor = self._current_actor  # ONAM小文字キー or None
        self._actor_tree.blockSignals(True)
        self._actor_tree.clear()

        all_item = QTreeWidgetItem([self.tr("(全アクター)")])
        all_item.setData(0, Qt.UserRole, None)
        self._actor_tree.addTopLevelItem(all_item)

        all_records = self._main.manager.all_records
        # {onam_key: (display_name, count)}
        actor_counts = all_records.get_actor_info_counts()
        filter_text = self._actor_search.text().lower()

        for onam_key, (display_name, count) in actor_counts.items():
            if filter_text and filter_text not in display_name.lower():
                continue
            item = QTreeWidgetItem([f"{display_name}  ({count})"])
            item.setData(0, Qt.UserRole, onam_key)  # ONAM小文字キーを格納
            self._actor_tree.addTopLevelItem(item)

        # 以前の選択を復元
        restored = False
        if prev_actor:
            for i in range(self._actor_tree.topLevelItemCount()):
                it = self._actor_tree.topLevelItem(i)
                if it.data(0, Qt.UserRole) == prev_actor:
                    self._actor_tree.setCurrentItem(it)
                    restored = True
                    break
        if not restored:
            self._actor_tree.setCurrentItem(all_item)
            self._current_actor = None

        self._actor_tree.blockSignals(False)

    # ------------------------------------------------------------------
    # DIAL ツリー再構築
    # ------------------------------------------------------------------

    def _rebuild_dial_tree(self) -> None:
        self._dial_tree.blockSignals(True)
        self._dial_tree.clear()

        all_records = self._main.manager.all_records
        # 有効な種別IDのセットを構築
        enabled_types: set[int] = {
            tid for tid, chk in self._type_checks.items() if chk.isChecked()
        }
        npc_filter  = self._current_actor   # ONAM小文字キー or None
        dial_search = self._dial_search_box.text().lower()   # DIAL名絞り込み
        search_text = self._search_box.text().lower()         # 全体検索（INFO内容）

        # アクター選択時は NPC_ 静的属性・配置セルを取得
        npc_attrs: dict = {}
        npc_cells: list[str] = []
        if npc_filter:
            npc_attrs = all_records.get_npc_attributes(npc_filter) or {}
            npc_cells = all_records.get_npc_cells(npc_filter)

        # DIAL RecordInfo を種別ごとに集める
        # groups: type_id -> [(dial_key, dial_display, type_name, count)]
        groups: dict[int, list[tuple[str, str, str, int]]] = {}
        for dial_ri in all_records.get_info_list("DIAL"):
            # エイリアスDIAL（旧NAME）はスキップ — 正規DIALのみ表示
            if all_records.get_canonical_dial_key(dial_ri.key) != dial_ri.key:
                continue

            main = dial_ri.main_record
            if not main:
                continue
            enc       = main.mod_file.encoding if main.mod_file else None
            from core.encoding import TesEncoding
            enc       = enc or TesEncoding.CP1252
            dial_key  = dial_ri.key
            # 表示名は main_record の NAME フィールド値（最終読み込みのエンコーディング）
            dial_display = self._field_str(main, "NAME", enc) or dial_key
            type_id   = self._get_dial_type(main)
            type_name = _DIAL_TYPE_NAMES.get(type_id, f"Unknown({type_id})")

            # 種別フィルタ（チェックボックス ON/OFF）
            if type_id not in enabled_types:
                continue

            # DIAL名 絞り込み
            if dial_search and dial_search not in dial_display.lower() and dial_search not in dial_key.lower():
                continue

            children = all_records.get_dial_children(dial_key)

            # アクターフィルタ（DIAL表示判定: OpenMW filter.search() 静的近似）
            if npc_filter and not self._has_applicable_info(children, npc_filter, npc_attrs, npc_cells):
                continue

            # 全体検索フィルタ（INFO 内容で絞り込み）
            if search_text and not self._has_search_match(children, search_text):
                continue

            # 件数 = INFO テーブルに表示される数
            if npc_filter:
                display_count = sum(
                    1 for c in children
                    if self._info_applicable_for_npc(c, npc_filter, npc_attrs, npc_cells)
                )
            else:
                display_count = len(children)

            groups.setdefault(type_id, []).append(
                (dial_key, dial_display, type_name, display_count)
            )

        # 種別順にグループヘッダーを追加
        bold = QFont()
        bold.setBold(True)
        for type_id in _DIAL_TYPE_ORDER:
            entries = groups.get(type_id)
            if not entries:
                continue
            type_name = _DIAL_TYPE_NAMES.get(type_id, "Unknown")
            grp_item = QTreeWidgetItem([f"[{type_name}]", "", ""])
            grp_item.setFont(0, bold)
            grp_item.setData(0, Qt.UserRole, None)  # グループ行
            self._dial_tree.addTopLevelItem(grp_item)

            for dial_key, dial_display, tname, count in sorted(
                entries, key=lambda x: x[1]  # 表示名でソート
            ):
                child_item = QTreeWidgetItem([dial_display, tname, str(count)])
                child_item.setData(0, Qt.UserRole, dial_key)  # キーはデータ検索用
                grp_item.addChild(child_item)

        self._dial_tree.expandAll()
        self._dial_tree.blockSignals(False)

        # INFO テーブルを更新（選択中のDIALがある場合）
        if self._current_dial_key:
            self._load_info_table(self._current_dial_key)

    # ------------------------------------------------------------------
    # INFO テーブル
    # ------------------------------------------------------------------

    def _load_info_table(self, dial_key: str) -> None:
        self._current_dial_key = dial_key
        self._info_search_box.blockSignals(True)
        self._info_search_box.clear()
        self._info_search_box.blockSignals(False)
        all_records  = self._main.manager.all_records
        npc_filter   = self._current_actor   # ONAM小文字キー or None
        children     = all_records.get_dial_children(dial_key)

        # アクター選択時は NPC_ 静的属性・配置セルを取得
        npc_attrs: dict = {}
        npc_cells: list[str] = []
        if npc_filter:
            npc_attrs = all_records.get_npc_attributes(npc_filter) or {}
            npc_cells = all_records.get_npc_cells(npc_filter)

        self._info_table.blockSignals(True)
        self._info_table.setRowCount(0)

        for info_ri in children:
            main = info_ri.main_record
            if not main:
                continue
            from core.encoding import TesEncoding
            enc = main.mod_file.encoding if main.mod_file else TesEncoding.CP1252

            onam = self._field_str(main, "ONAM", enc)
            rnam = self._field_str(main, "RNAM", enc)
            cnam = self._field_str(main, "CNAM", enc)
            fnam = self._field_str(main, "FNAM", enc)
            name = self._field_str(main, "NAME", enc)
            scvr = sum(1 for f in main.fields if f.field_type == "SCVR")

            # アクターフィルタ（OpenMW filter.search() 静的近似）
            # 検索テキストはDIALツリー絞り込み専用のためINFO行には適用しない
            if npc_filter and not self._info_applicable_for_npc(info_ri, npc_filter, npc_attrs, npc_cells):
                continue

            row = self._info_table.rowCount()
            self._info_table.insertRow(row)
            values = [info_ri.key, onam, rnam, cnam, fnam, name, str(scvr)]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, info_ri)
                if col == 5:
                    item.setToolTip(text)
                self._info_table.setItem(row, col, item)

        self._info_table.blockSignals(False)
        self._info_table.resizeColumnsToContents()
        self._clamp_column_widths(self._info_table)
        self._apply_info_column_visibility()
        self._info_conflict_grid.load(None)
        self._text_panel.set_text("")

    # ------------------------------------------------------------------
    # INFO テーブル カラム表示設定
    # ------------------------------------------------------------------

    def _apply_info_column_visibility(self) -> None:
        from app.settings import Settings
        saved = Settings.instance().get_info_table_columns()
        for i in range(len(_INFO_COLS)):
            if saved is not None:
                hidden = i not in saved
            else:
                hidden = False  # デフォルト: 全列表示
            self._info_table.setColumnHidden(i, hidden)

    def _on_info_header_menu(self, pos) -> None:
        menu = QMenu(self)
        for i, col_name in enumerate(_INFO_COLS):
            action = menu.addAction(col_name)
            action.setCheckable(True)
            action.setChecked(not self._info_table.isColumnHidden(i))
            action.toggled.connect(
                lambda checked, idx=i: self._set_info_column_visible(idx, checked)
            )
        menu.exec(self._info_table.horizontalHeader().mapToGlobal(pos))

    def _set_info_column_visible(self, col_idx: int, visible: bool) -> None:
        self._info_table.setColumnHidden(col_idx, not visible)
        self._save_info_column_settings()

    def _save_info_column_settings(self) -> None:
        from app.settings import Settings
        visible = [i for i in range(len(_INFO_COLS)) if not self._info_table.isColumnHidden(i)]
        Settings.instance().set_info_table_columns(visible)

    # ------------------------------------------------------------------
    # スプリッター状態の保存と復元
    # ------------------------------------------------------------------

    def _restore_splitter_states(self) -> None:
        from app.settings import Settings
        s = Settings.instance()
        for key, splitter in [
            ("dialogue_outer_h", self._outer_h_splitter),
            ("dialogue_main_v",  self._main_v_splitter),
            ("dialogue_h",       self._mid_h_splitter),
            ("dialogue_dial_v",  self._dial_v_splitter),
            ("dialogue_info_v",  self._info_v_splitter),
        ]:
            enc = s.get_splitter_state(key)
            if enc:
                splitter.restoreState(QByteArray.fromBase64(enc.encode("ascii")))

    def save_splitter_states(self) -> None:
        """closeEvent から呼ばれる。"""
        from app.settings import Settings
        s = Settings.instance()
        for key, splitter in [
            ("dialogue_outer_h", self._outer_h_splitter),
            ("dialogue_main_v",  self._main_v_splitter),
            ("dialogue_h",       self._mid_h_splitter),
            ("dialogue_dial_v",  self._dial_v_splitter),
            ("dialogue_info_v",  self._info_v_splitter),
        ]:
            s.set_splitter_state(key, splitter.saveState().toBase64().data().decode("ascii"))

    # ------------------------------------------------------------------
    # スロット
    # ------------------------------------------------------------------

    def _on_actor_selected(self, current: QTreeWidgetItem, _previous) -> None:
        if current is None:
            return
        self._current_actor = current.data(0, Qt.UserRole)  # ONAM小文字キー or None
        self._rebuild_dial_tree()

    def _on_actor_search_changed(self) -> None:
        self._rebuild_actor_panel()

    def _on_dial_selected(self, current: QTreeWidgetItem, _previous) -> None:
        if current is None:
            return
        dial_key = current.data(0, Qt.UserRole)
        if dial_key is None:
            return  # グループヘッダー行

        # INFOテーブルを更新
        self._load_info_table(dial_key)

        # DIAL ConflictGrid を更新（エイリアス含む全DIALレコードを統合して表示）
        all_records = self._main.manager.all_records
        dial_ri = all_records.get_dial_record_info_with_aliases(dial_key)
        if dial_ri:
            fmt = self._main.manager.format_loader.get_record("DIAL")
            field_fmts = [f for f in (fmt.fields if fmt else []) if f.is_show]
            self._dial_conflict_grid.load(dial_ri, field_fmts=field_fmts)
        else:
            self._dial_conflict_grid.load(None)

    def _on_info_selected(self, current, _previous) -> None:
        if current is None:
            self._info_conflict_grid.load(None)
            self._text_panel.set_text("")
            return
        item = self._info_table.item(current.row(), 0)
        if item is None:
            return
        info_ri = item.data(Qt.UserRole)
        if info_ri is None:
            return

        # INFO フォーマットを取得して ConflictGrid に渡す
        fmt = self._main.manager.format_loader.get_record("INFO")
        field_fmts = [f for f in (fmt.fields if fmt else []) if f.is_show]
        self._info_conflict_grid.load(info_ri, field_fmts=field_fmts)

        # TextPanel には NAME（応答テキスト）を表示
        main = info_ri.main_record
        if main:
            from core.encoding import TesEncoding
            enc = main.mod_file.encoding if main.mod_file else TesEncoding.CP1252
            self._text_panel.set_text(self._field_str(main, "NAME", enc))
        else:
            self._text_panel.set_text("")

    def _on_filter_changed(self) -> None:
        self._rebuild_dial_tree()

    def _on_dial_search_changed(self) -> None:
        self._rebuild_dial_tree()

    def _on_search_changed(self) -> None:
        self._rebuild_dial_tree()

    def _on_info_search_changed(self) -> None:
        """INFO 検索ボックスのテキスト変更時: INFO テーブルの行を表示/非表示。"""
        text = self._info_search_box.text().lower()
        for row in range(self._info_table.rowCount()):
            if not text:
                self._info_table.setRowHidden(row, False)
                continue
            # 全カラムのテキストを検索
            match = False
            for col in range(self._info_table.columnCount()):
                item = self._info_table.item(row, col)
                if item and text in item.text().lower():
                    match = True
                    break
            self._info_table.setRowHidden(row, not match)

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp_column_widths(table: QTableWidget) -> None:
        """各カラム幅をビューポート幅以下にクランプする。"""
        max_w = table.viewport().width()
        if max_w <= 0:
            return
        for i in range(table.columnCount()):
            if table.columnWidth(i) > max_w:
                table.setColumnWidth(i, max_w)

    @staticmethod
    def _get_dial_type(record) -> int:
        """DIAL レコードの DATA フィールドから種別 uint8 を返す。"""
        data_field = record.fields_map.get("DATA")
        if data_field:
            raw = data_field.data.raw()
            if raw:
                return raw[0]
        return 0

    @staticmethod
    def _field_str(record, field_name: str, encoding) -> str:
        field = record.fields_map.get(field_name)
        return field.to_display_str(encoding) if field else ""

    @staticmethod
    def _info_matches_npc(info_ri, npc_key: str, npc_attrs: dict) -> bool:
        """INFO が NPC に「固有または属性グループ向け」の応答として適用されるか判定する
        （厳格版 / strict）。

        表示する条件:
        1. ONAM = npc_key                   → NPCへの固有応答
        2. ONAM 空 かつ RNAM/CNAM/FNAM/Gender の
           少なくとも1つが非ワイルドカードで NPC属性に一致
                                            → NPC属性グループへの応答

        表示しない条件:
        - ONAM = 他NPC ID                   → 別NPCへの応答
        - ONAM 空 かつ RNAM/CNAM/FNAM/Gender がすべてワイルドカード
                                            → 全NPC共通フォールバック（汎用応答）

        動的条件（ANAM/DNAM/mRank/mPCrank/SCVR）はスキップ。
        将来課題：セーブデータ/プロセスアタッチで動的情報を取得して評価。
        """
        main = info_ri.main_record
        if not main:
            return False
        from core.encoding import TesEncoding
        enc = main.mod_file.encoding if main.mod_file else TesEncoding.CP1252

        # ONAM を確認
        onam = main.fields_map.get("ONAM")
        onam_str = onam.to_display_str(enc).strip().lower() if onam else ""

        if onam_str:
            # ONAM に値がある場合 → NPC ID と一致する場合のみ表示
            return onam_str == npc_key

        # ONAM が空/なし → 属性グループ向けINFOかを確認
        # 各フィールドの値を取得
        rnam = main.fields_map.get("RNAM")
        cnam = main.fields_map.get("CNAM")
        fnam = main.fields_map.get("FNAM")
        data_field = main.fields_map.get("DATA")

        rnam_str = rnam.to_display_str(enc).strip() if rnam else ""
        cnam_str = cnam.to_display_str(enc).strip() if cnam else ""
        fnam_str = fnam.to_display_str(enc).strip() if fnam else ""

        gender = 0xFF  # デフォルト = 不問
        if data_field is not None:
            raw = data_field.data.raw()
            if len(raw) >= 10:
                gender = raw[9]

        # すべてワイルドカード = 全NPC共通フォールバック → 表示しない（strict）
        if not rnam_str and not cnam_str and not fnam_str and gender == 0xFF:
            return False

        # 少なくとも1つ条件あり → 設定されたすべての条件がNPC属性と一致するか確認
        if rnam_str and rnam_str.lower() != npc_attrs.get("rnam", "").lower():
            return False
        if cnam_str and cnam_str.lower() != npc_attrs.get("cnam", "").lower():
            return False
        if fnam_str:
            npc_faction = npc_attrs.get("faction", "")
            if fnam_str.upper() == "FFFF":
                if npc_faction:              # NPCがファクション所属 → 不一致
                    return False
            elif fnam_str.lower() != npc_faction.lower():
                return False
        if gender != 0xFF:
            npc_is_female = npc_attrs.get("is_female", False)
            if gender == 0 and npc_is_female:
                return False                 # 男性専用、NPCは女性
            if gender == 1 and not npc_is_female:
                return False                 # 女性専用、NPCは男性

        return True

    @staticmethod
    def _info_applicable_for_npc(info_ri, npc_key: str, npc_attrs: dict,
                                 npc_cells: list[str]) -> bool:
        """INFO が NPC に適用されうるか判定する（OpenMW filter.search() 準拠）。

        OpenMW の DIAL トピック表示判定を静的に近似する:
        1. testActor — ONAM/RNAM/CNAM/FNAM/Gender を NPC 属性と照合
        2. ONAM 固有 INFO → NPC ID 一致のみ（NPC専用ダイアログ）
        3. 属性グループ（非ワイルドカード）→ testActor + ANAM
        4. ワイルドカード INFO → 追加の静的評価:
           a. ANAM（プレイヤーセル条件）→ NPC配置セルで代替評価
           b. DNAM（プレイヤーファクション）→ 評価不能のためスキップ
           c. PCRank → 評価不能のためスキップ
           d. 動的 SCVR（Journal/Global/Local/Item/Dead/Function）
              → ANAM セル照合済みなら通過扱い、ANAM なしなら除外
           e. 静的 SCVR（NotId/NotFaction/NotClass/NotRace）→ NPC属性で評価
           f. NotCell/NotLocal → 完全評価不能のため通過扱い
        """
        main = info_ri.main_record
        if not main:
            return False
        from core.encoding import TesEncoding
        enc = main.mod_file.encoding if main.mod_file else TesEncoding.CP1252

        # --- testActor ---
        onam = main.fields_map.get("ONAM")
        onam_str = onam.to_display_str(enc).strip().lower() if onam else ""

        if onam_str:
            # ONAM 固有 INFO → NPC ID 一致のみ（NPC専用ダイアログは場所不問で表示）
            return onam_str == npc_key

        # ONAM なし → 属性条件を確認
        rnam = main.fields_map.get("RNAM")
        cnam = main.fields_map.get("CNAM")
        fnam = main.fields_map.get("FNAM")
        data_field = main.fields_map.get("DATA")

        rnam_str = rnam.to_display_str(enc).strip() if rnam else ""
        cnam_str = cnam.to_display_str(enc).strip() if cnam else ""
        fnam_str = fnam.to_display_str(enc).strip() if fnam else ""

        gender = 0xFF
        raw = b""
        if data_field is not None:
            raw = data_field.data.raw()
            if len(raw) >= 10:
                gender = raw[9]

        # 属性条件チェック（設定されたフィールドのみ）
        if rnam_str and rnam_str.lower() != npc_attrs.get("rnam", "").lower():
            return False
        if cnam_str and cnam_str.lower() != npc_attrs.get("cnam", "").lower():
            return False
        if fnam_str:
            npc_faction = npc_attrs.get("faction", "")
            if fnam_str.upper() == "FFFF":
                if npc_faction:
                    return False
            elif fnam_str.lower() != npc_faction.lower():
                return False
        if gender != 0xFF:
            npc_is_female = npc_attrs.get("is_female", False)
            if gender == 0 and npc_is_female:
                return False
            if gender == 1 and not npc_is_female:
                return False

        # 非ワイルドカード（属性条件が1つ以上設定）→ testActor + ANAM で判定
        # ANAM が設定されている場合は NPC 配置セルと照合（場所限定 INFO の除外）
        if rnam_str or cnam_str or fnam_str or gender != 0xFF:
            anam = main.fields_map.get("ANAM")
            anam_str = anam.to_display_str(enc).strip() if anam else ""
            if anam_str:
                if not any(anam_str.lower() == c.lower() for c in npc_cells):
                    return False
            return True

        # --- ワイルドカード INFO: ランタイム条件の静的評価 ---

        # ANAM チェック（testPlayer のセル条件 → NPC配置セルで代替）
        anam = main.fields_map.get("ANAM")
        anam_str = anam.to_display_str(enc).strip() if anam else ""
        if anam_str:
            if not any(anam_str.lower() == c.lower() for c in npc_cells):
                return False

        # DNAM チェック（プレイヤーファクション — 不明のためスキップ）
        dnam = main.fields_map.get("DNAM")
        if dnam and dnam.to_display_str(enc).strip():
            return False

        # PCRank チェック（プレイヤーランク — 不明のためスキップ）
        if len(raw) >= 11:
            pcrank = raw[10]
            if pcrank != 0xFF and pcrank != 0:
                return False

        # SCVR チェック ---
        # ANAM がセル照合を通過した場合、動的 SCVR は「条件成立の可能性あり」として通過扱い。
        # ANAM がない場合、動的 SCVR は評価不能のため除外（場所制限なしでは誤表示が多すぎる）。
        # SCVR type byte: '1'=Function, '2'=Global, '3'=Local, '4'=Journal,
        #   '5'=Item, '6'=Dead → 動的（ランタイム状態依存）
        # '7'=NotId, '8'=NotFaction, '9'=NotClass, 'A'=NotRace → 静的
        # 'B'=NotCell, 'C'=NotLocal → 完全評価不能（通過扱い）
        has_location_restriction = bool(anam_str)
        for f in main.fields:
            if f.field_type != "SCVR":
                continue
            scvr_raw = f.data.raw()
            if len(scvr_raw) < 5:
                continue
            stype = scvr_raw[1]
            # 動的 SCVR (types '1'-'6' = 0x31-0x36)
            if 0x31 <= stype <= 0x36:
                if has_location_restriction:
                    continue   # ANAM で場所制限済み → 動的条件は通過扱い
                return False   # 場所制限なし → 評価不能、除外
            # 静的 Not* SCVR → NPC属性で評価
            varname = ""
            if len(scvr_raw) > 5:
                varname = scvr_raw[5:].decode("ascii", errors="replace") \
                              .rstrip("\x00").lower()
            if stype == 0x37:      # NotId
                if varname == npc_key:
                    return False
            elif stype == 0x38:    # NotFaction
                if varname == npc_attrs.get("faction", "").lower():
                    return False
            elif stype == 0x39:    # NotClass
                if varname == npc_attrs.get("cnam", "").lower():
                    return False
            elif stype == 0x41:    # NotRace ('A' = 0x41)
                if varname == npc_attrs.get("rnam", "").lower():
                    return False
            # 'B'=NotCell (0x42), 'C'=NotLocal (0x43): 通過扱い

        return True

    @staticmethod
    def _has_matching_info(children, npc_key: str, npc_attrs: dict) -> bool:
        """NPC固有・グループ固有応答のみを確認する（strict）。
        汎用フォールバック（全条件ワイルドカード）は含まない。"""
        return any(
            DialoguePanel._info_matches_npc(info_ri, npc_key, npc_attrs)
            for info_ri in children
        )

    @staticmethod
    def _has_applicable_info(children, npc_key: str, npc_attrs: dict,
                             npc_cells: list[str]) -> bool:
        """DIAL 表示判定用: OpenMW filter.search() の静的近似。
        testActor + セル条件 + 静的 SCVR 評価で NPC が応答しうるトピックを判定。"""
        return any(
            DialoguePanel._info_applicable_for_npc(
                info_ri, npc_key, npc_attrs, npc_cells)
            for info_ri in children
        )

    @staticmethod
    def _has_search_match(children, search_text: str) -> bool:
        """children のいずれかの INFO の NAME または ONAM に search_text が含まれるか確認する。"""
        from core.encoding import TesEncoding
        for info_ri in children:
            main = info_ri.main_record
            if not main:
                continue
            enc  = main.mod_file.encoding if main.mod_file else TesEncoding.CP1252
            name = main.fields_map.get("NAME")
            onam = main.fields_map.get("ONAM")
            name_str = name.to_display_str(enc).lower() if name else ""
            onam_str = onam.to_display_str(enc).lower() if onam else ""
            if search_text in name_str or search_text in onam_str:
                return True
        return False
