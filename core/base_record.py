from __future__ import annotations
from abc import ABC, abstractmethod
from core.bytes_util import TesBytes
from core.encoding import TesEncoding


class BaseField(ABC):
    """フィールド基底クラス"""

    def __init__(self, field_type: str, data: TesBytes):
        self.field_type = field_type
        self.data = data

    @property
    def total_size(self) -> int:
        """フィールドタイプ(4) + サイズ(4) + データ"""
        return 8 + len(self.data)

    @abstractmethod
    def to_display_str(self, encoding: TesEncoding) -> str: ...

    @abstractmethod
    def write(self, buffer: bytearray) -> None: ...


class BaseRecord(ABC):
    """レコード基底クラス"""

    def __init__(self, record_type: str):
        self.record_type = record_type
        self.flags: int = 0
        self.fields: list[BaseField] = []
        self.fields_map: dict[str, BaseField] = {}

    def add_field(self, field: BaseField) -> None:
        self.fields.append(field)
        self.fields_map[field.field_type] = field

    @abstractmethod
    def write(self, buffer: bytearray) -> None: ...
