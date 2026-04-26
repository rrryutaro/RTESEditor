from __future__ import annotations
import json
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class FieldFormat:
    field_name: str
    description: str = ""
    data_type: str = "bytes"
    is_show: bool = True
    is_edit: bool = True
    is_export: bool = False
    is_import: bool = False


@dataclass
class RecordFormat:
    record_name: str
    description: str = ""
    is_show: bool = True
    is_edit: bool = True
    is_export: bool = False
    is_import: bool = False
    unique_key_field: str = ""
    fields: list[FieldFormat] = field(default_factory=list)
    _fields_map: dict[str, FieldFormat] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self):
        self._fields_map = {f.field_name: f for f in self.fields}

    def get_field(self, field_name: str) -> FieldFormat | None:
        return self._fields_map.get(field_name)


def _collect_fields(raw_fields: list[dict]) -> list[FieldFormat]:
    """GroupFields を含むフィールド定義をフラットなリストに展開する"""
    result: list[FieldFormat] = []
    for f in raw_fields:
        group = f.get("GroupFields")
        if group:
            result.extend(_collect_fields(group))
            continue
        name = f.get("FieldName", "")
        if not name:
            continue
        result.append(FieldFormat(
            field_name  = name,
            description = f.get("Description", ""),
            data_type   = f.get("DataType", "bytes"),
            is_show     = f.get("IsShow", False),
            is_edit     = f.get("IsEdit", True),
            is_export   = f.get("IsExport", False),
            is_import   = f.get("IsImport", False),
        ))
    return result


class FormatLoader:
    """tes3_format.json を読み込みレコード・フィールドフォーマットを提供する"""

    def __init__(self):
        self._records: dict[str, RecordFormat] = {}

    def load(self, path: str | Path | None = None) -> None:
        if path is None:
            path = Path(__file__).parent / "tes3_format.json"
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        for r in data.get("Records", []):
            fields = _collect_fields(r.get("Fields", []))
            rec = RecordFormat(
                record_name      = r["RecordName"],
                description      = r.get("Description", ""),
                is_show          = r.get("IsShow", True),
                is_edit          = r.get("IsEdit", True),
                is_export        = r.get("IsExport", False),
                is_import        = r.get("IsImport", False),
                unique_key_field = r.get("UniqueKeyFieldName", ""),
                fields           = fields,
            )
            self._records[rec.record_name] = rec

    def get_record(self, record_name: str) -> RecordFormat | None:
        return self._records.get(record_name)

    @property
    def all_records(self) -> list[RecordFormat]:
        return list(self._records.values())
