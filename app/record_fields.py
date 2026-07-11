from __future__ import annotations

from collections.abc import Iterator

from tes3.field import Field
from tes3.record import Record


def iter_field_occurrences(record: Record, field_name: str) -> Iterator[tuple[int, Field]]:
    index = 0
    for field in record.fields:
        if field.field_type != field_name:
            continue
        yield index, field
        index += 1


def get_field_occurrence(record: Record, field_name: str, field_index: int) -> Field | None:
    for index, field in iter_field_occurrences(record, field_name):
        if index == field_index:
            return field
    return None


def get_display_field(record: Record, field_name: str) -> Field | None:
    overrides = getattr(record, "_display_field_overrides", None)
    if overrides is not None and field_name in overrides:
        return overrides[field_name]
    if record.record_type == "CELL" and field_name == "NAME":
        return get_field_occurrence(record, field_name, 0)
    return record.fields_map.get(field_name)
