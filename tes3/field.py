from __future__ import annotations
import struct
from core.bytes_util import TesBytes
from core.base_record import BaseField
from core.encoding import TesEncoding


class Field(BaseField):
    """TES3フィールド"""

    def __init__(self, field_type: str, data: TesBytes, field_format=None, parent_record=None):
        super().__init__(field_type, data)
        self.field_format = field_format
        self.parent_record = parent_record  # 所属Recordへの参照（エンコーディング解決用）
        self.is_modified = False

    def to_display_str(self, encoding: TesEncoding = TesEncoding.CP1252) -> str:
        if self.field_format is None:
            return self.data.raw().hex()
        dtype = self.field_format.data_type
        match dtype:
            case "int8":    return str(self.data.to_int8())
            case "int16":   return str(self.data.to_int16())
            case "int32":   return str(self.data.to_int32())
            case "uint16":  return str(self.data.to_uint16())
            case "uint32":  return str(self.data.to_uint32())
            case "float32": return str(self.data.to_float32())
            case "string" | "zstring":
                return self.data.to_str(encoding)
            case _:
                return self.data.raw().hex()

    def modify(self, new_data: TesBytes) -> None:
        self.data = new_data
        self.is_modified = True
        if self.parent_record:
            self.parent_record.mark_modified()

    def write(self, buffer: bytearray) -> None:
        raw = self.data.raw()
        buffer += self.field_type.encode("ascii")
        buffer += struct.pack("<I", len(raw))
        buffer += raw

    def clone(self, parent_record) -> "Field":
        f = Field(self.field_type, TesBytes(self.data.raw()), self.field_format, parent_record=parent_record)
        f.is_modified = self.is_modified
        return f
