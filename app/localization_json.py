from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.mod_manager import ModManager
from app.record_fields import get_field_occurrence, iter_field_occurrences
from core.bytes_util import TesBytes
from core.encoding import TesEncoding
from tes3.field import Field
from tes3.record import Record


_FORMAT_NAME = "RTESEditor TES3 Localization JSON"
_FORMAT_VERSION = 1

# Human-facing fields. ID/path/reference fields are intentionally excluded.
_LOCALIZABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "GMST": ("STRV",),
    "CLAS": ("FNAM", "DESC"),
    "FACT": ("FNAM", "RNAM"),
    "RACE": ("FNAM", "DESC"),
    "SKIL": ("DESC",),
    "MGEF": ("DESC",),
    "REGN": ("FNAM",),
    "BSGN": ("FNAM", "DESC"),
    "DOOR": ("FNAM",),
    "MISC": ("FNAM",),
    "WEAP": ("FNAM",),
    "CONT": ("FNAM",),
    "SPEL": ("FNAM",),
    "CREA": ("FNAM",),
    "LIGH": ("FNAM",),
    "NPC_": ("FNAM",),
    "ARMO": ("FNAM",),
    "CLOT": ("FNAM",),
    "REPA": ("FNAM",),
    "ACTI": ("FNAM",),
    "APPA": ("FNAM",),
    "LOCK": ("FNAM",),
    "PROB": ("FNAM",),
    "INGR": ("FNAM",),
    "BOOK": ("FNAM", "TEXT"),
    "ALCH": ("FNAM",),
    "CELL": ("NAME",),
    "INFO": ("NAME",),
}

_SCRIPT_STRING_FIELDS: dict[str, tuple[str, ...]] = {
    "INFO": ("BNAM",),
    "SCPT": ("SCTX",),
}


@dataclass(frozen=True)
class StringLiteral:
    index: int
    start: int
    end: int
    text: str


@dataclass
class LocalizationImportResult:
    updated: int = 0
    skipped: int = 0
    warnings: list[str] | None = None

    def add_warning(self, message: str) -> None:
        if self.warnings is None:
            self.warnings = []
        self.warnings.append(message)


def export_localization_json(manager: ModManager, path: str | Path) -> int:
    """Export localizable TES3 text to a structured JSON file."""
    entries: list[dict[str, Any]] = []
    for record in _iter_main_records(manager):
        enc = _record_encoding(record)
        for field_name in _LOCALIZABLE_FIELDS.get(record.record_type, ()):
            for field_index, field in _iter_localizable_field_occurrences(record, field_name):
                source = field.to_display_str(enc)
                if not source:
                    continue
                entries.append(
                    _make_field_entry(record, field_name, field_index, source)
                )

        for field_name in _SCRIPT_STRING_FIELDS.get(record.record_type, ()):
            for field_index, field in iter_field_occurrences(record, field_name):
                source = field.to_display_str(enc)
                for literal in _iter_translatable_script_literals(
                    record.record_type,
                    field_name,
                    source,
                ):
                    if not literal.text:
                        continue
                    entries.append(
                        _make_script_string_entry(
                            record,
                            field_name,
                            field_index,
                            literal.index,
                            literal.text,
                        )
                    )

    data = {
        "format": _FORMAT_NAME,
        "version": _FORMAT_VERSION,
        "encoding": TesEncoding.UTF_8.value,
        "entries": entries,
    }
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(entries)


def import_localization_json(
    manager: ModManager,
    path: str | Path,
    target_encoding: TesEncoding = TesEncoding.UTF_8,
) -> LocalizationImportResult:
    """Import translated JSON entries and write them into loaded records."""
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    entries = raw.get("entries", [])
    result = LocalizationImportResult(warnings=[])
    cell_name_translations = _collect_cell_name_translations(entries)

    script_groups: dict[tuple[str, str, str, int], list[dict[str, Any]]] = {}
    for entry in entries:
        entry_type = entry.get("type")
        if entry_type == "field":
            _import_field_entry(manager, entry, target_encoding, result)
        elif entry_type in ("script_string", "result_string"):
            key = (
                str(entry.get("record_type", "")),
                str(entry.get("record_key", "")),
                str(entry.get("field", "")),
                int(entry.get("field_index", 0) or 0),
            )
            script_groups.setdefault(key, []).append(entry)
        else:
            result.skipped += 1

    for key, group_entries in script_groups.items():
        _import_script_string_entries(manager, key, group_entries, target_encoding, result)

    _apply_cell_name_reference_updates(
        manager,
        cell_name_translations,
        target_encoding,
        result,
    )

    for mod in manager.mod_files:
        if any(record.is_modified for record in mod.records):
            mod.encoding = target_encoding

    return result


def _collect_cell_name_translations(entries: list[dict[str, Any]]) -> dict[str, str]:
    translations: dict[str, str] = {}
    for entry in entries:
        if (
            entry.get("type") != "field"
            or entry.get("record_type") != "CELL"
            or entry.get("field") != "NAME"
            or int(entry.get("field_index", 0) or 0) != 0
        ):
            continue
        source = str(entry.get("source", ""))
        translation = str(entry.get("translation", ""))
        if source and translation and source != translation:
            translations[source] = translation
    return translations


def _apply_cell_name_reference_updates(
    manager: ModManager,
    translations: dict[str, str],
    target_encoding: TesEncoding,
    result: LocalizationImportResult,
) -> None:
    if not translations:
        return

    for record in _iter_main_records(manager):
        if record.record_type == "CELL":
            for field in record.fields:
                if field.field_type != "DNAM":
                    continue
                source = _field_as_zstring(field, _record_encoding(record))
                translation = translations.get(source)
                if translation is None:
                    continue
                _write_zstring_field(record, field, translation, target_encoding)
                result.updated += 1
        elif record.record_type == "PGRD":
            for _field_index, field in iter_field_occurrences(record, "NAME"):
                source = _field_as_zstring(field, _record_encoding(record))
                translation = translations.get(source)
                if translation is None:
                    continue
                _write_zstring_field(record, field, translation, target_encoding)
                result.updated += 1


def _iter_main_records(manager: ModManager):
    for record_type in manager.all_records.get_record_types():
        for info in manager.all_records.get_info_list(record_type):
            record = info.main_record
            if record is not None:
                yield record


def _record_encoding(record: Record) -> TesEncoding:
    return record.mod_file.encoding if record.mod_file else TesEncoding.CP1252


def _iter_localizable_field_occurrences(record: Record, field_name: str):
    if record.record_type == "CELL" and field_name == "NAME":
        field = get_field_occurrence(record, field_name, 0)
        if field is not None:
            yield 0, field
        return
    yield from iter_field_occurrences(record, field_name)


def _is_localizable_field_occurrence(
    record: Record,
    field_name: str,
    field_index: int,
) -> bool:
    if field_name not in _LOCALIZABLE_FIELDS.get(record.record_type, ()):
        return False
    if record.record_type == "CELL" and field_name == "NAME":
        return field_index == 0
    return True


def _make_field_entry(
    record: Record,
    field_name: str,
    field_index: int,
    source: str,
) -> dict[str, Any]:
    entry = {
        "id": _entry_id("field", record, field_name, field_index),
        "type": "field",
        "record_type": record.record_type,
        "record_key": record.primary_key,
        "field": field_name,
        "field_index": field_index,
        "source": source,
        "translation": "",
    }
    context = _record_context(record)
    if context:
        entry["context"] = context
    return entry


def _make_script_string_entry(
    record: Record,
    field_name: str,
    field_index: int,
    string_index: int,
    source: str,
) -> dict[str, Any]:
    entry_type = "result_string" if record.record_type == "INFO" else "script_string"
    entry = {
        "id": _entry_id(entry_type, record, field_name, field_index, string_index),
        "type": entry_type,
        "record_type": record.record_type,
        "record_key": record.primary_key,
        "field": field_name,
        "field_index": field_index,
        "string_index": string_index,
        "source": source,
        "translation": "",
    }
    context = _record_context(record)
    if context:
        entry["context"] = context
    return entry


def _entry_id(
    entry_type: str,
    record: Record,
    field_name: str,
    field_index: int,
    string_index: int | None = None,
) -> str:
    parts = [
        entry_type,
        record.mod_file.file_name if record.mod_file else "",
        record.record_type,
        record.primary_key,
        field_name,
        str(field_index),
        "" if string_index is None else str(string_index),
    ]
    digest = hashlib.sha1("\0".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{record.record_type}:{field_name}:{digest}"


def _record_context(record: Record) -> dict[str, Any]:
    context: dict[str, Any] = {}
    if record.mod_file:
        context["file"] = record.mod_file.file_name
    if record.record_type == "INFO" and record.parent_group is not None:
        context["dialogue_topic"] = record.parent_group.label
    if record.record_type == "SCPT":
        script_name = _script_name(record)
        if script_name:
            context["script"] = script_name
    return context


def _script_name(record: Record) -> str:
    field = record.fields_map.get("SCHD")
    if field is None:
        return ""
    raw = field.data.raw()[:32]
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def _import_field_entry(
    manager: ModManager,
    entry: dict[str, Any],
    target_encoding: TesEncoding,
    result: LocalizationImportResult,
) -> None:
    translation = str(entry.get("translation", ""))
    if not translation:
        result.skipped += 1
        return

    record = _find_record(manager, entry)
    if record is None:
        result.skipped += 1
        result.add_warning(_missing_record_message(entry))
        return

    field_name = str(entry.get("field", ""))
    field_index = int(entry.get("field_index", 0) or 0)
    if not _is_localizable_field_occurrence(record, field_name, field_index):
        result.skipped += 1
        result.add_warning(
            f"{record.record_type}:{record.primary_key} の {field_name}[{field_index}] "
            "はローカライズJSONの対象外です。"
        )
        return

    field = get_field_occurrence(record, field_name, field_index)
    if field is None:
        result.skipped += 1
        result.add_warning(
            f"{record.record_type}:{record.primary_key} に {field_name}[{field_index}] がありません。"
        )
        return

    _write_text_field(record, field, translation, target_encoding)
    result.updated += 1


def _import_script_string_entries(
    manager: ModManager,
    key: tuple[str, str, str, int],
    entries: list[dict[str, Any]],
    target_encoding: TesEncoding,
    result: LocalizationImportResult,
) -> None:
    record_type, record_key, field_name, field_index = key
    probe = {"record_type": record_type, "record_key": record_key}
    record = _find_record(manager, probe)
    if record is None:
        result.skipped += len(entries)
        result.add_warning(_missing_record_message(probe))
        return

    field = get_field_occurrence(record, field_name, field_index)
    if field is None:
        result.skipped += len(entries)
        result.add_warning(
            f"{record.record_type}:{record.primary_key} に {field_name}[{field_index}] がありません。"
        )
        return

    current = field.to_display_str(_record_encoding(record))
    literals = list(_iter_translatable_script_literals(record.record_type, field_name, current))
    replacements: dict[int, str] = {}
    for entry in entries:
        translation = str(entry.get("translation", ""))
        if not translation:
            result.skipped += 1
            continue
        if '"' in translation:
            result.skipped += 1
            result.add_warning(
                f"{record.record_type}:{record.primary_key} の {field_name}[{field_index}] "
                f"引用文字列 {entry.get('string_index', 0)} は ASCII のダブルクォートを含むためスキップしました。"
            )
            continue
        index = int(entry.get("string_index", 0) or 0)
        if index < 0 or index >= len(literals):
            result.skipped += 1
            result.add_warning(
                f"{record.record_type}:{record.primary_key} の {field_name}[{field_index}] に "
                f"引用文字列 {index} がありません。"
            )
            continue
        replacements[index] = translation

    if not replacements:
        return

    updated = _replace_string_literals(current, literals, replacements)
    _write_text_field(record, field, updated, target_encoding)
    result.updated += len(replacements)


def _find_record(manager: ModManager, entry: dict[str, Any]) -> Record | None:
    record_type = str(entry.get("record_type", ""))
    record_key = str(entry.get("record_key", ""))
    info = manager.all_records.find_record_info(record_type, record_key)
    return info.main_record if info else None


def _write_text_field(
    record: Record,
    field: Field,
    value: str,
    target_encoding: TesEncoding,
) -> None:
    ff = field.field_format or (record.record_format.get_field(field.field_type) if record.record_format else None)
    null_terminate = bool(ff and ff.data_type == "zstring")
    field.modify(TesBytes.from_str(value, target_encoding, null_terminate=null_terminate))


def _write_zstring_field(
    record: Record,
    field: Field,
    value: str,
    target_encoding: TesEncoding,
) -> None:
    field.modify(TesBytes.from_str(value, target_encoding, null_terminate=True))


def _field_as_zstring(field: Field, encoding: TesEncoding) -> str:
    return field.data.to_str(encoding)


def _missing_record_message(entry: dict[str, Any]) -> str:
    return (
        f"{entry.get('record_type', '')}:{entry.get('record_key', '')} "
        "に対応するレコードが見つかりません。"
    )


def _iter_string_literals(text: str):
    index = 0
    i = 0
    while i < len(text):
        if text[i] != '"':
            i += 1
            continue
        i += 1
        start = i
        while i < len(text):
            if text[i] == "\\" and i + 1 < len(text):
                i += 2
                continue
            if text[i] == '"':
                yield StringLiteral(index, start, i, text[start:i])
                index += 1
                i += 1
                break
            i += 1
        else:
            break


def _iter_translatable_script_literals(
    record_type: str,
    field_name: str,
    text: str,
):
    if (record_type, field_name) in {("INFO", "BNAM"), ("SCPT", "SCTX")}:
        yield from _iter_info_result_display_literals(text)
        return
    yield from _iter_string_literals(text)


def _iter_info_result_display_literals(text: str):
    display_commands = {"messagebox", "choice"}
    index = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        command = (
            line.lstrip().split(None, 1)[0].rstrip(",").casefold()
            if line.strip()
            else ""
        )
        if command in display_commands:
            for literal in _iter_string_literals(line):
                yield StringLiteral(
                    index=index,
                    start=offset + literal.start,
                    end=offset + literal.end,
                    text=literal.text,
                )
                index += 1
        offset += len(line)


def _replace_string_literals(
    text: str,
    literals: list[StringLiteral],
    replacements: dict[int, str],
) -> str:
    result = text
    for literal in reversed(literals):
        if literal.index not in replacements:
            continue
        result = (
            result[:literal.start]
            + replacements[literal.index]
            + result[literal.end:]
        )
    return result
