from __future__ import annotations
import struct
from core.encoding import TesEncoding
from tes3.record import Record


class RecordInfo:
    """同一レコード（同一キー）の複数Mod間での情報を保持する"""

    def __init__(self, key: str):
        self.key = key
        self.records: list[Record] = []
        self.is_modified = False
        self.is_overwrite = False
        self.parent: RecordInfo | None = None

    @property
    def main_record(self) -> Record | None:
        """競合解決後のメインレコード（最後に読み込んだレコード）。"""
        return self.records[-1] if self.records else None

    def add_record(self, record: Record) -> None:
        self.records.append(record)

    def find(self, search_text: str, encoding: TesEncoding | None = None) -> bool:
        terms = search_text.split()
        if not terms:
            return True

        records = [
            r
            for r in self.records
            if not r.mod_file or r.mod_file.is_search_target
        ]
        return all(
            any(self._record_find(r, term, encoding) for r in records)
            for term in terms
        )

    @staticmethod
    def _record_find(record: Record, term: str, encoding: TesEncoding | None = None) -> bool:
        if record.find(term, encoding):
            return True
        overrides = getattr(record, "_display_field_overrides", None)
        if not overrides:
            return False
        for field in overrides.values():
            enc = (
                field.parent_record.mod_file.encoding
                if field.parent_record and field.parent_record.mod_file
                else encoding or TesEncoding.CP1252
            )
            if term in field.to_display_str(enc):
                return True
        return False

    def write(self, buffer: bytearray, mod_file) -> None:
        for record in self.records:
            if record.mod_file is mod_file:
                record.write(buffer, overwrite_check=True)


class AllRecordInfos:
    """全レコード種別・全RecordInfoのコンテナ（C#のAllRecordInfos相当）"""

    def __init__(self):
        # record_type -> {key -> RecordInfo}
        self._data: dict[str, dict[str, RecordInfo]] = {}
        self._records_in_load_order: list[Record] = []
        # ダイアログ用インデックス（build_dialogue_index()で構築）
        self._info_to_dial: dict[str, str] = {}           # INFO INAM -> 親DIAL NAME(正規)
        self._dial_to_infos: dict[str, list[str]] = {}    # DIAL NAME(正規) -> 子INFO INAMリスト
        self._npc_to_infos: dict[str, list[str]] = {}     # NPC ID(小文字) -> INFO INAMリスト
        self._dial_canonical: dict[str, str] = {}         # 旧DIAL NAME -> 正規DIAL NAME
        self._npc_name_to_infos: dict[str, list[RecordInfo]] = {}
        # NPC静的属性インデックス（build_dialogue_index()で構築）
        # npc_id(小文字) -> {rnam, cnam, faction, is_female}
        self._npc_attrs: dict[str, dict] = {}

    def add_record(self, record: Record) -> None:
        self._records_in_load_order.append(record)
        rtype = record.record_type
        key   = record.primary_key
        if rtype not in self._data:
            self._data[rtype] = {}
        if key not in self._data[rtype]:
            self._data[rtype][key] = RecordInfo(key)
        self._data[rtype][key].add_record(record)

    def get_record_types(self) -> list[str]:
        return list(self._data.keys())

    def get_infos(self, record_type: str) -> dict[str, RecordInfo]:
        return self._data.get(record_type, {})

    def get_info_list(self, record_type: str) -> list[RecordInfo]:
        return list(self._data.get(record_type, {}).values())

    def get_visible_info_list(self, record_type: str) -> list[RecordInfo]:
        infos = self.get_info_list(record_type)
        if record_type != "CELL":
            return infos
        return [
            info
            for info in infos
            if not self._is_hidden_cell_localization_target(info)
        ]

    @staticmethod
    def _is_hidden_cell_localization_target(info: RecordInfo) -> bool:
        main = info.main_record
        return bool(main is not None and getattr(main, "_hide_as_cell_localization_target", False))

    def find_record_info(self, record_type: str, key: str) -> RecordInfo | None:
        """レコードIDから最終ロード状態の RecordInfo を探す。"""
        value = key.strip()
        if not value:
            return None
        records = self._data.get(record_type, {})
        target = value.casefold()
        candidates = [
            info
            for existing_key, info in records.items()
            if existing_key.strip().casefold() == target
        ]
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        load_order = {id(record): index for index, record in enumerate(self._records_in_load_order)}

        def latest_index(info: RecordInfo) -> int:
            return max((load_order.get(id(record), -1) for record in info.records), default=-1)

        return max(candidates, key=latest_index)

    def contains_key(self, record_type: str, key: str) -> bool:
        return key in self._data.get(record_type, {})

    def delete_record(self, record: Record) -> None:
        self._records_in_load_order = [r for r in self._records_in_load_order if r is not record]
        rtype = record.record_type
        key   = record.primary_key
        if rtype in self._data and key in self._data[rtype]:
            info = self._data[rtype][key]
            info.records = [r for r in info.records if r is not record]
            if not info.records:
                del self._data[rtype][key]

    # ------------------------------------------------------------------
    # ダイアログ用インデックス
    # ------------------------------------------------------------------

    def build_dialogue_index(self) -> None:
        """DIAL-INFO・NPC-INFO の親子インデックスを再構築する。
        ModManager._integrate() から全Mod読込後に呼ぶ。

        ローカライズ対応:
        TES3ではDIALのNAMEがIDを兼ねるため、ローカライズESPが別NAMEのDIALを
        新規作成することがある（例: "Emperor's Spy" → "皇帝の使者"）。
        同一INFOが複数DIALに属する場合、main_recordのparent_group.labelを正規キーとし、
        旧NAMEのDIALをエイリアスとして扱う。
        """
        self._info_to_dial = {}
        self._dial_to_infos = {}
        self._npc_to_infos = {}
        self._dial_canonical = {}
        self._npc_name_to_infos = {}
        self._npc_attrs = {}
        self._npc_cells: dict[str, list[str]] = {}   # npc_id(小文字) -> セル名リスト
        self._build_cell_localization_aliases()

        # NPC_ 静的属性インデックスを構築
        for npc_ri in self.get_info_list("NPC_"):
            main = npc_ri.main_record
            if not main:
                continue
            enc = main.mod_file.encoding if main.mod_file else TesEncoding.CP1252
            name_field = main.fields_map.get("NAME")
            if not name_field:
                continue
            npc_id = name_field.to_display_str(enc).strip().lower()
            if not npc_id:
                continue
            rnam_field = main.fields_map.get("RNAM")
            cnam_field = main.fields_map.get("CNAM")
            fnam_field = main.fields_map.get("FNAM")
            anam_field = main.fields_map.get("ANAM")  # NPC_のANAM = ファクションID
            flag_field = main.fields_map.get("FLAG")
            rnam    = rnam_field.to_display_str(enc).strip() if rnam_field else ""
            cnam    = cnam_field.to_display_str(enc).strip() if cnam_field else ""
            fnam    = fnam_field.to_display_str(enc).strip() if fnam_field else ""
            faction = anam_field.to_display_str(enc).strip() if anam_field else ""
            is_female = False
            if flag_field:
                flag_val  = flag_field.data.to_uint32()
                is_female = bool(flag_val & 0x0001)
            self._npc_attrs[npc_id] = {
                "rnam":      rnam,
                "cnam":      cnam,
                "faction":   faction,
                "is_female": is_female,
            }
            for lookup in {npc_id.casefold(), fnam.casefold()}:
                if lookup:
                    self._npc_name_to_infos.setdefault(lookup, []).append(npc_ri)

        # NPC→セル配置インデックスを構築（CELL内のForm Referenceを走査）
        # CELL はユニークキーがなく、RecordInfo上では別Modの同一序数CELLが
        # 同じ箱に混ざるため、表示用の配置は実ロード順リストから作る。
        npc_id_set = set(self._npc_attrs.keys())
        for rec in self._records_in_load_order:
            if rec.record_type != "CELL":
                continue
            enc = rec.mod_file.encoding if rec.mod_file else TesEncoding.CP1252
            cell_name = self._cell_display_name(rec, enc)
            fields = rec.fields
            i = 0
            while i < len(fields):
                f = fields[i]
                if f.field_type == "FRMR" and i + 1 < len(fields):
                    nxt = fields[i + 1]
                    if nxt.field_type == "NAME":
                        obj_id = nxt.to_display_str(enc).strip().lower()
                        if obj_id in npc_id_set and cell_name:
                            self._npc_cells.setdefault(obj_id, [])
                            if cell_name not in self._npc_cells[obj_id]:
                                self._npc_cells[obj_id].append(cell_name)
                    i += 2
                else:
                    i += 1

        for info_ri in self.get_info_list("INFO"):
            info_key = info_ri.key

            # 全レコードの parent_group.label を収集（重複排除・順序保持）
            all_parents: list[str] = []
            for rec in info_ri.records:
                if rec.parent_group is not None:
                    lbl = rec.parent_group.label
                    if lbl and lbl not in all_parents:
                        all_parents.append(lbl)

            if not all_parents:
                continue

            # 正規DIALキー = main_record の parent_group.label（最終読み込み優先）
            main = info_ri.main_record
            if main and main.parent_group and main.parent_group.label:
                canonical_dial = main.parent_group.label
            else:
                canonical_dial = all_parents[0]

            # 旧NAMEを正規NAMEへのエイリアスとして登録
            for alt in all_parents:
                if alt != canonical_dial and alt not in self._dial_canonical:
                    self._dial_canonical[alt] = canonical_dial

            # 正規キーでインデックス構築
            self._info_to_dial[info_key] = canonical_dial
            if canonical_dial not in self._dial_to_infos:
                self._dial_to_infos[canonical_dial] = []
            if info_key not in self._dial_to_infos[canonical_dial]:
                self._dial_to_infos[canonical_dial].append(info_key)

            # ONAM（アクターID）からNPC→INFOインデックスを構築
            if main:
                onam = main.fields_map.get("ONAM")
                if onam:
                    enc = main.mod_file.encoding if main.mod_file else TesEncoding.CP1252
                    npc_id = onam.to_display_str(enc).strip().lower()
                    if npc_id:
                        self._npc_to_infos.setdefault(npc_id, []).append(info_key)

        # ------------------------------------------------------------------
        # ポスト処理: エイリアスキー下のINFOを正規キーに統合する
        # ------------------------------------------------------------------
        # ESM専用INFO（日本語ESPで上書きされていないもの）は main_record.parent_group.label が
        # エイリアスDIAL名（例: "latest rumors"）になるため、メインループでは
        # エイリアスキーのまま _dial_to_infos に登録される。
        # ここで _dial_canonical を使って全エントリを正規キーに統合する。
        remapped: dict[str, list[str]] = {}
        for dial_key, info_keys in self._dial_to_infos.items():
            canonical = self.get_canonical_dial_key(dial_key)
            if canonical not in remapped:
                remapped[canonical] = []
            for k in info_keys:
                if k not in remapped[canonical]:
                    remapped[canonical].append(k)
        self._dial_to_infos = remapped

        # _info_to_dial も正規化する
        for info_key in list(self._info_to_dial.keys()):
            self._info_to_dial[info_key] = self.get_canonical_dial_key(
                self._info_to_dial[info_key]
            )

    @staticmethod
    def _cell_display_name(record: Record, enc: TesEncoding) -> str:
        cell_name = ""
        region_name = ""
        grid_x = None
        grid_y = None
        for field in record.fields:
            if field.field_type in ("FRMR", "MVRF", "NAM0"):
                break
            if field.field_type == "NAME" and not cell_name:
                cell_name = field.to_display_str(enc).strip()
            elif field.field_type == "RGNN" and not region_name:
                region_name = field.to_display_str(enc).strip()
            elif field.field_type == "DATA":
                raw = field.data.raw()
                if len(raw) >= 12:
                    try:
                        grid_x, grid_y = struct.unpack_from("<ii", raw, 4)
                    except struct.error:
                        grid_x = grid_y = None
        if cell_name:
            return cell_name
        if region_name and grid_x is not None and grid_y is not None:
            return f"{region_name} ({grid_x}, {grid_y})"
        if region_name:
            return region_name
        if grid_x is not None and grid_y is not None:
            return f"外部セル ({grid_x}, {grid_y})"
        return ""

    def get_canonical_dial_key(self, dial_key: str) -> str:
        """エイリアスDIALキーを正規キーに変換する。正規キーはそのまま返す。"""
        visited: set[str] = set()
        current = dial_key
        while current in self._dial_canonical and current not in visited:
            visited.add(current)
            current = self._dial_canonical[current]
        return current

    def get_dial_record_info_with_aliases(self, dial_key: str) -> "RecordInfo | None":
        """DIALキー（正規またはエイリアス）に対して、
        エイリアスを含む全DIALレコードを統合した RecordInfo を返す。
        DIAL ConflictGrid 表示用。
        レコードは実際のロード順に並べる。"""
        canonical = self.get_canonical_dial_key(dial_key)
        dial_dict = self._data.get("DIAL", {})

        canonical_ri = dial_dict.get(canonical)
        alias_keys = [k for k, v in self._dial_canonical.items()
                      if self.get_canonical_dial_key(k) == canonical]
        alias_ris = [dial_dict[k] for k in alias_keys if k in dial_dict]

        if not alias_ris:
            return canonical_ri  # エイリアスなし：そのまま返す

        # 統合 RecordInfo を生成（実際のロード順で並べる）
        merged = RecordInfo(canonical)
        all_recs = []
        for ri in alias_ris:
            all_recs.extend(ri.records)
        if canonical_ri:
            all_recs.extend(canonical_ri.records)
        load_order = {id(record): index for index, record in enumerate(self._records_in_load_order)}
        all_recs.sort(key=lambda record: load_order.get(id(record), -1))
        merged.records = all_recs
        return merged

    def get_dial_children(self, dial_key: str) -> list[RecordInfo]:
        """指定DIALに属するINFO RecordInfoを順序付きで返す。エイリアスキーも受け付ける。"""
        canonical = self.get_canonical_dial_key(dial_key)
        keys = self._dial_to_infos.get(canonical, [])
        info_dict = self._data.get("INFO", {})
        return [info_dict[k] for k in keys if k in info_dict]

    def get_dial_key_for_info(self, info_key: str) -> str:
        """INFO INAM から親DIALキーを返す。見つからなければ空文字。"""
        value = info_key.strip()
        if not value:
            return ""
        dial_key = self._info_to_dial.get(value)
        if dial_key:
            return dial_key
        target = value.casefold()
        for key, candidate in self._info_to_dial.items():
            if key.strip().casefold() == target:
                return candidate
        return ""

    def get_infos_by_actor(self, npc_id: str) -> list[RecordInfo]:
        """ONAM が npc_id（大文字小文字を区別しない）に一致するINFOを返す。"""
        keys = self._npc_to_infos.get(npc_id.lower(), [])
        info_dict = self._data.get("INFO", {})
        return [info_dict[k] for k in keys if k in info_dict]

    def get_npc_ids_in_info(self) -> list[str]:
        """INFO.ONAM に登場するNPC IDの一覧を返す（ソート済み）。"""
        return sorted(self._npc_to_infos.keys())

    def get_npc_attributes(self, npc_id: str) -> dict | None:
        """NPC IDに対応するNPC_の静的属性を返す。
        戻り値: {rnam: str, cnam: str, faction: str, is_female: bool}
        見つからない場合は None。
        npc_id は大文字小文字を区別しない。"""
        return self._npc_attrs.get(npc_id.lower())

    def find_npc_infos_by_name_or_id(self, value: str) -> list[RecordInfo]:
        """NPCの表示名またはIDからNPC_ RecordInfoを返す。"""
        key = value.strip().casefold()
        if not key:
            return []
        return list(self._npc_name_to_infos.get(key, []))

    def get_npc_cells(self, npc_id: str) -> list[str]:
        """NPC IDが配置されているセル名リストを返す。
        CELL レコードの Form Reference から抽出。
        npc_id は大文字小文字を区別しない。"""
        return list(reversed(self._npc_cells.get(npc_id.lower(), [])))

    def get_actor_info_counts(self) -> dict[str, tuple[str, int]]:
        """アクター(ONAM)ごとのINFO件数を返す。
        戻り値: {onam_key(小文字): (display_name, count)}
        display_name は NPC_.main_record.FNAM を優先し、存在しない場合は ONAM 文字列を使用。
        ソートは display_name 昇順。"""
        # NPC_ の FNAM 逆引き辞書を構築（大文字小文字無視）
        npc_fnam: dict[str, str] = {}   # npc_id(小文字) -> fnam_display
        for npc_ri in self.get_info_list("NPC_"):
            main = npc_ri.main_record
            if not main:
                continue
            enc = main.mod_file.encoding if main.mod_file else TesEncoding.CP1252
            name_field = main.fields_map.get("NAME")
            fnam_field = main.fields_map.get("FNAM")
            if name_field and fnam_field:
                npc_id = name_field.to_display_str(enc).strip().lower()
                fnam   = fnam_field.to_display_str(enc).strip()
                if npc_id and fnam:
                    npc_fnam[npc_id] = fnam

        counts: dict[str, int] = {}   # onam_key -> count
        names:  dict[str, str] = {}   # onam_key -> display_name
        for info_ri in self.get_info_list("INFO"):
            main = info_ri.main_record
            if not main:
                continue
            onam = main.fields_map.get("ONAM")
            if not onam:
                continue
            enc = main.mod_file.encoding if main.mod_file else TesEncoding.CP1252
            onam_str = onam.to_display_str(enc).strip()
            if not onam_str:
                continue
            key = onam_str.lower()
            # NPC_.main_record.FNAM を優先、なければ ONAM 文字列にフォールバック
            display = npc_fnam.get(key, onam_str)
            names[key]  = display
            counts[key] = counts.get(key, 0) + 1
        return {k: (names[k], counts[k]) for k in sorted(counts, key=lambda k: names[k])}

    # ------------------------------------------------------------------
    # CELL ローカライズ表示
    # ------------------------------------------------------------------

    def _build_cell_localization_aliases(self) -> None:
        for record in self._records_in_load_order:
            if hasattr(record, "_display_field_overrides"):
                delattr(record, "_display_field_overrides")
            if hasattr(record, "_hide_as_cell_localization_target"):
                delattr(record, "_hide_as_cell_localization_target")

        seen_names: set[str] = set()
        by_mod: dict[object, list[Record]] = {}
        for record in self._records_in_load_order:
            if record.record_type != "CELL":
                continue
            by_mod.setdefault(record.mod_file, []).append(record)

        for records in by_mod.values():
            groups: dict[tuple[tuple[str, bytes], ...], list[tuple[Record, str]]] = {}
            for record in records:
                name = self._cell_first_name(record)
                if not name:
                    continue
                groups.setdefault(self._cell_localization_signature(record), []).append(
                    (record, name)
                )

            for grouped in groups.values():
                unique_names = {name for _record, name in grouped}
                if len(unique_names) < 2:
                    continue

                targets = [
                    (record, name)
                    for record, name in grouped
                    if name.casefold() not in seen_names
                ]
                if not targets:
                    continue
                target_record, _target_name = targets[-1]
                target_field = self._cell_first_name_field(target_record)
                if target_field is None:
                    continue
                setattr(target_record, "_hide_as_cell_localization_target", True)

                for record, name in grouped:
                    if record is target_record:
                        continue
                    if name.casefold() not in seen_names:
                        continue
                    overrides = getattr(record, "_display_field_overrides", None)
                    if overrides is None:
                        overrides = {}
                        setattr(record, "_display_field_overrides", overrides)
                    overrides["NAME"] = target_field

            for record in records:
                name = self._cell_first_name(record)
                if name:
                    seen_names.add(name.casefold())

    @staticmethod
    def _cell_first_name_field(record: Record):
        for field in record.fields:
            if field.field_type == "NAME":
                return field
        return None

    def _cell_first_name(self, record: Record) -> str:
        field = self._cell_first_name_field(record)
        if field is None:
            return ""
        enc = record.mod_file.encoding if record.mod_file else TesEncoding.CP1252
        return field.to_display_str(enc).strip()

    @staticmethod
    def _cell_localization_signature(record: Record) -> tuple[tuple[str, bytes], ...]:
        result: list[tuple[str, bytes]] = []
        first_name_done = False
        for field in record.fields:
            if field.field_type == "NAME" and not first_name_done:
                first_name_done = True
                continue

            raw = field.data.raw()
            if field.field_type in ("FRMR", "MVRF") and len(raw) >= 4:
                local_id = int.from_bytes(raw[:4], "little") & 0x00FFFFFF
                raw = local_id.to_bytes(4, "little") + raw[4:]
            result.append((field.field_type, raw))
        return tuple(result)
