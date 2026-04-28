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
