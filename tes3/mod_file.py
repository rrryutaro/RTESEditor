from __future__ import annotations
from pathlib import Path
from core.encoding import TesEncoding
from tes3.record import Record


class ModFile:
    """1つのESP/ESMファイルを表す（C#のModMain相当）"""

    def __init__(self, path: str | Path, encoding: TesEncoding, is_overwrite: bool, is_save: bool,
                 is_search_target: bool = True):
        self.path = Path(path)
        self.encoding = encoding
        self.is_overwrite = is_overwrite
        self.is_save = is_save
        self.is_search_target = is_search_target
        self.header_record: Record | None = None
        self.records: list[Record] = []
        self.record_map: dict[str, list[Record]] = {}  # record_type -> [Record]

    @property
    def file_name(self) -> str:
        return self.path.name

    def add_record(self, record: Record) -> None:
        record.mod_file = self
        if record.record_type == "TES3":
            self.header_record = record
        else:
            self.records.append(record)
            self.record_map.setdefault(record.record_type, []).append(record)
            record.index = len(self.record_map[record.record_type])
            record.is_overwrite_save = True  # 読込済みレコードは全て保存対象

    def save(self) -> None:
        """全レコードを保存する（通常保存）。"""
        buf = bytearray()
        if self.header_record:
            self.header_record.write(buf)
        for record in self.records:
            record.write(buf, overwrite_check=True)
        self.path.write_bytes(bytes(buf))

    def save_diff(self, path: Path | None = None) -> int:
        """今セッションで修正したレコードのみを書き出す（差分保存）。戻り値は出力レコード数。"""
        target = path if path else self.path
        buf = bytearray()
        if self.header_record:
            self.header_record.write(buf)
        count = 0
        for record in self.records:
            if record.is_modified:
                record.write(buf)
                count += 1
        target.write_bytes(bytes(buf))
        return count
