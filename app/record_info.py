from __future__ import annotations
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
        """競合解決後のメインレコード（最後に読み込んだOverwrite対象）"""
        overwrite = [r for r in self.records if r.mod_file and r.mod_file.is_overwrite]
        return overwrite[-1] if overwrite else (self.records[-1] if self.records else None)

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
        return all(any(r.find(term, encoding) for r in records) for term in terms)

    def write(self, buffer: bytearray, mod_file) -> None:
        for record in self.records:
            if record.mod_file is mod_file:
                record.write(buffer, overwrite_check=True)


class AllRecordInfos:
    """全レコード種別・全RecordInfoのコンテナ（C#のAllRecordInfos相当）"""

    def __init__(self):
        # record_type -> {key -> RecordInfo}
        self._data: dict[str, dict[str, RecordInfo]] = {}
        # ダイアログ用インデックス（build_dialogue_index()で構築）
        self._info_to_dial: dict[str, str] = {}           # INFO INAM -> 親DIAL NAME(正規)
        self._dial_to_infos: dict[str, list[str]] = {}    # DIAL NAME(正規) -> 子INFO INAMリスト
        self._npc_to_infos: dict[str, list[str]] = {}     # NPC ID(小文字) -> INFO INAMリスト
        self._dial_canonical: dict[str, str] = {}         # 旧DIAL NAME -> 正規DIAL NAME
        # NPC静的属性インデックス（build_dialogue_index()で構築）
        # npc_id(小文字) -> {rnam, cnam, faction, is_female}
        self._npc_attrs: dict[str, dict] = {}

    def add_record(self, record: Record) -> None:
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

    def contains_key(self, record_type: str, key: str) -> bool:
        return key in self._data.get(record_type, {})

    def delete_record(self, record: Record) -> None:
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
        self._npc_attrs = {}
        self._npc_cells: dict[str, list[str]] = {}   # npc_id(小文字) -> セル名リスト

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
            anam_field = main.fields_map.get("ANAM")  # NPC_のANAM = ファクションID
            flag_field = main.fields_map.get("FLAG")
            rnam    = rnam_field.to_display_str(enc).strip() if rnam_field else ""
            cnam    = cnam_field.to_display_str(enc).strip() if cnam_field else ""
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

        # NPC→セル配置インデックスを構築（CELL内のForm Referenceを走査）
        npc_id_set = set(self._npc_attrs.keys())
        for cell_ri in self.get_info_list("CELL"):
            for rec in cell_ri.records:
                enc = rec.mod_file.encoding if rec.mod_file else TesEncoding.CP1252
                # 最初のNAMEフィールド = セル名（FRMR前）
                cell_name = ""
                for f in rec.fields:
                    if f.field_type == "NAME":
                        cell_name = f.to_display_str(enc).strip()
                        break
                    if f.field_type == "FRMR":
                        break
                # FRMR + NAME ペアを走査してNPC配置を検出
                fields = rec.fields
                i = 0
                while i < len(fields):
                    f = fields[i]
                    if f.field_type == "FRMR" and i + 1 < len(fields):
                        nxt = fields[i + 1]
                        if nxt.field_type == "NAME":
                            obj_id = nxt.to_display_str(enc).strip().lower()
                            if obj_id in npc_id_set:
                                if obj_id not in self._npc_cells:
                                    self._npc_cells[obj_id] = []
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
        レコードは is_overwrite=False（ESM等）を先に、True（ESP等）を後に並べる。"""
        canonical = self.get_canonical_dial_key(dial_key)
        dial_dict = self._data.get("DIAL", {})

        canonical_ri = dial_dict.get(canonical)
        alias_keys = [k for k, v in self._dial_canonical.items()
                      if self.get_canonical_dial_key(k) == canonical]
        alias_ris = [dial_dict[k] for k in alias_keys if k in dial_dict]

        if not alias_ris:
            return canonical_ri  # エイリアスなし：そのまま返す

        # 統合 RecordInfo を生成（全レコードを is_overwrite 順で並べる）
        merged = RecordInfo(canonical)
        all_recs = []
        for ri in alias_ris:
            all_recs.extend(ri.records)
        if canonical_ri:
            all_recs.extend(canonical_ri.records)
        # is_overwrite=False（ESM系）→ True（ESP系）の順に並べる
        all_recs.sort(key=lambda r: (r.mod_file.is_overwrite if r.mod_file else False))
        merged.records = all_recs
        return merged

    def get_dial_children(self, dial_key: str) -> list[RecordInfo]:
        """指定DIALに属するINFO RecordInfoを順序付きで返す。エイリアスキーも受け付ける。"""
        canonical = self.get_canonical_dial_key(dial_key)
        keys = self._dial_to_infos.get(canonical, [])
        info_dict = self._data.get("INFO", {})
        return [info_dict[k] for k in keys if k in info_dict]

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

    def get_npc_cells(self, npc_id: str) -> list[str]:
        """NPC IDが配置されているセル名リストを返す。
        CELL レコードの Form Reference から抽出。
        npc_id は大文字小文字を区別しない。"""
        return self._npc_cells.get(npc_id.lower(), [])

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
