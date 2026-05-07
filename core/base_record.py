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


class BaseRecordGroup(ABC):
    """GRUP相当。TES4+では実GRUP、TES3ではReader段階で仮想合成される。"""

    def __init__(
        self,
        group_type: int,
        label: str,
        parent_record: "BaseRecord | None" = None,
        is_synthetic: bool = False,
    ):
        self.group_type = group_type
        self.label = label
        self.parent_record = parent_record
        self.records: list["BaseRecord"] = []
        self.is_synthetic = is_synthetic


class BaseRecord(ABC):
    """レコード基底クラス"""

    def __init__(self, record_type: str):
        self.record_type = record_type
        self.flags: int = 0
        self.fields: list[BaseField] = []
        self.fields_map: dict[str, BaseField] = {}
        self.parent_group: "BaseRecordGroup | None" = None
        self.children_group: "BaseRecordGroup | None" = None

    def add_field(self, field: BaseField) -> None:
        self.fields.append(field)
        self.fields_map[field.field_type] = field

    @abstractmethod
    def write(self, buffer: bytearray) -> None: ...
