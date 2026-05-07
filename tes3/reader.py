from __future__ import annotations
import logging
from pathlib import Path
from core.file_reader import BinaryFileReader
from core.encoding import TesEncoding
from tes3.field import Field
from tes3.record import Record, Tes3RecordGroup
from tes3.mod_file import ModFile
from tes3.format.format_loader import FormatLoader
from core.bytes_util import TesBytes

_log = logging.getLogger(__name__)


class Tes3Reader:
    """TES3（Morrowind）ESP/ESMファイルのバイナリリーダー"""

    def __init__(self, format_loader: FormatLoader):
        self._fmt = format_loader

    def load(
        self,
        path: str | Path,
        encoding: TesEncoding,
        is_overwrite: bool,
        is_save: bool,
        on_progress: callable = None,
    ) -> ModFile:
        mod = ModFile(path, encoding, is_overwrite, is_save)
        reader = BinaryFileReader(path)

        current_dial_group: Tes3RecordGroup | None = None

        while not reader.eof:
            record = self._read_record(reader, mod)
            if record:
                if record.record_type == "DIAL":
                    # NAMEフィールドからラベルを直接取得（mod_file未設定のためエンコーディング指定）
                    name_field = record.fields_map.get("NAME")
                    label = name_field.data.to_str(encoding) if name_field else ""
                    current_dial_group = Tes3RecordGroup(
                        group_type=7,
                        label=label,
                        parent_record=record,
                        is_synthetic=True,
                    )
                    record.children_group = current_dial_group
                elif record.record_type == "INFO":
                    if current_dial_group is not None:
                        current_dial_group.records.append(record)
                        record.parent_group = current_dial_group
                    else:
                        _log.warning("Orphan INFO record: no preceding DIAL in %s", path)
                else:
                    current_dial_group = None
                mod.add_record(record)
            if on_progress:
                on_progress(reader.position, reader.length)

        return mod

    def _read_record(self, reader: BinaryFileReader, mod: ModFile) -> Record | None:
        if reader.length - reader.position < 16:
            return None

        record_type = reader.read_str(4)
        size        = reader.read_uint32()
        reserved    = reader.read_uint32()
        flags       = reader.read_uint32()

        record_format = self._fmt.get_record(record_type)
        record = Record(record_type, size, reserved, flags, record_format)

        remaining = size
        while remaining > 0:
            field = self._read_field(reader, record)
            record.add_field(field)
            remaining -= field.total_size

        return record

    def _read_field(self, reader: BinaryFileReader, record: Record) -> Field:
        field_type   = reader.read_str(4)
        field_size   = reader.read_uint32()
        data         = reader.read_bytes(field_size)

        field_format = None
        if record.record_format:
            field_format = record.record_format.get_field(field_type)

        return Field(field_type, data, field_format, parent_record=record)
