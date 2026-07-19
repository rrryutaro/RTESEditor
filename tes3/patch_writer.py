from __future__ import annotations

import os
import shutil
import struct
import tempfile
from pathlib import Path

from core.bytes_util import TesBytes


class PatchValidationError(ValueError):
    """OpenMWが解釈できない編集先パッチを保存しようとした場合のエラー。"""


def ordered_patch_records(mod_file) -> list:
    """DIALの直後に、そのDIALに属するINFOを並べた保存順を返す。"""
    record_ids = {id(record) for record in mod_file.records}
    children: dict[int, list] = {}
    errors: list[str] = []

    for record in mod_file.records:
        if record.record_type != "INFO":
            continue
        group = record.parent_group
        parent = group.parent_record if group is not None else None
        if parent is None or parent.record_type != "DIAL":
            errors.append(f"INFO {record.primary_key!r} に親DIALがありません。")
            continue
        if id(parent) not in record_ids or parent.mod_file is not mod_file:
            errors.append(
                f"INFO {record.primary_key!r} の親DIAL {group.label!r} が編集先パッチにありません。"
            )
            continue
        children.setdefault(id(parent), []).append(record)

    if errors:
        raise PatchValidationError("\n".join(errors))

    ordered: list = []
    written_infos: set[int] = set()
    for record in mod_file.records:
        if record.record_type == "INFO":
            continue
        ordered.append(record)
        if record.record_type == "DIAL":
            for info in children.get(id(record), []):
                ordered.append(info)
                written_infos.add(id(info))

    missing = [
        record.primary_key
        for record in mod_file.records
        if record.record_type == "INFO" and id(record) not in written_infos
    ]
    if missing:
        raise PatchValidationError(
            "保存順を構築できないINFOがあります: " + ", ".join(repr(key) for key in missing)
        )
    return ordered


def build_patch_bytes(mod_file) -> tuple[bytes, int]:
    if mod_file.header_record is None:
        raise PatchValidationError("編集先パッチにTES3ヘッダーがありません。")

    records = ordered_patch_records(mod_file)
    _set_header_record_count(mod_file.header_record, len(records))
    buffer = bytearray()
    mod_file.header_record.write(buffer)
    for record in records:
        record.write(buffer)
    return bytes(buffer), len(records)


def save_patch(mod_file, path: str | Path | None = None) -> int:
    target = Path(path) if path is not None else mod_file.path
    data, count = build_patch_bytes(mod_file)
    if not target.parent.exists():
        raise OSError(f"保存先フォルダーがありません: {target.parent}")

    temporary: Path | None = None
    backup: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

        if target.exists():
            backup = target.with_name(target.name + ".bak")
            shutil.copy2(target, backup)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()

    mod_file.last_backup_path = backup
    return count


def _set_header_record_count(header, record_count: int) -> None:
    hedr = header.fields_map.get("HEDR")
    if hedr is None:
        raise PatchValidationError("TES3ヘッダーにHEDRフィールドがありません。")
    raw = bytearray(hedr.data.raw())
    if len(raw) < 300:
        raise PatchValidationError("TES3 HEDRフィールドが300バイト未満です。")
    struct.pack_into("<I", raw, 296, record_count)
    # ヘッダーも編集先パッチの所有物なので、元ESPを書き換えることはない。
    hedr.modify(TesBytes(bytes(raw)))
