from __future__ import annotations
import struct
from core.bytes_util import TesBytes
from core.base_record import BaseRecord
from core.encoding import TesEncoding
from tes3.field import Field


class Record(BaseRecord):
    """TES3レコード基底クラス"""

    def __init__(self, record_type: str, size: int, reserved: int, flags: int, record_format=None):
        super().__init__(record_type)
        self.size = size
        self.reserved = reserved
        self.flags = flags
        self.record_format = record_format
        self.is_modified = False
        self.is_overwrite_save = False
        self.mod_file = None   # 所属ModFileへの参照
        self.index = 0

    @property
    def primary_key(self) -> str:
        if self.record_format is None or not self.record_format.unique_key_field:
            return str(self.index)
        field_name = self.record_format.unique_key_field
        if field_name in self.fields_map:
            field = self.fields_map[field_name]
            enc = self.mod_file.encoding if self.mod_file else TesEncoding.CP1252
            return field.to_display_str(enc)
        return ""

    def mark_modified(self) -> None:
        self.is_modified = True
        self.is_overwrite_save = True

    def recalc_size(self) -> int:
        self.size = sum(f.total_size for f in self.fields)
        return self.size

    def write(self, buffer: bytearray, overwrite_check: bool = False) -> None:
        if overwrite_check and not self.is_overwrite_save:
            return
        self.recalc_size()
        buffer += self.record_type.encode("ascii")
        buffer += struct.pack("<I", self.size)
        buffer += struct.pack("<I", self.reserved)
        buffer += struct.pack("<I", self.flags)
        for field in self.fields:
            field.write(buffer)

    def clone(self) -> "Record":
        r = Record(self.record_type, self.size, self.reserved, self.flags, self.record_format)
        r.index = self.index
        r.is_modified = self.is_modified
        r.is_overwrite_save = self.is_overwrite_save
        for field in self.fields:
            cloned = field.clone(r)
            r.fields.append(cloned)
            r.fields_map[cloned.field_type] = cloned
        return r

    def find(self, search_text: str, encoding: TesEncoding | None = None) -> bool:
        enc = encoding or (self.mod_file.encoding if self.mod_file else TesEncoding.CP1252)
        return any(
            search_text in f.to_display_str(enc)
            for f in self.fields
            if f.field_format is not None
        )
