from __future__ import annotations
from pathlib import Path
from core.encoding import TesEncoding
from app.record_info import AllRecordInfos
from app.mod_manager import ModManager


_HEADER = "RecordType\tKey\tFieldName\tText\n"


def export_tsv(manager: ModManager, path: str | Path) -> int:
    """IsExport フィールドの文字列を TSV に書き出す。戻り値は出力行数。"""
    lines: list[str] = [_HEADER]
    for rtype in manager.all_records.get_record_types():
        fmt = manager.format_loader.get_record(rtype)
        export_fields = [f for f in (fmt.fields if fmt else []) if f.is_export]
        if not export_fields:
            continue
        for info in manager.all_records.get_info_list(rtype):
            rec = info.main_record
            if not rec:
                continue
            enc = rec.mod_file.encoding if rec.mod_file else TesEncoding.CP1252
            key = info.key
            for ff in export_fields:
                field = rec.fields_map.get(ff.field_name)
                if not field:
                    continue
                text = field.to_display_str(enc)
                if not text:
                    continue
                safe = text.replace("\t", " ").replace("\n", "\\n").replace("\r", "")
                lines.append(f"{rtype}\t{key}\t{ff.field_name}\t{safe}\n")

    Path(path).write_text("".join(lines), encoding="utf-8")
    return len(lines) - 1  # ヘッダ除く


def import_tsv(manager: ModManager, path: str | Path) -> int:
    """TSV からテキストを読み込んでレコードに反映する。戻り値は更新フィールド数。"""
    from core.bytes_util import TesBytes
    updated = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("RecordType\t"):
            continue
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        rtype, key, fname, text = parts
        text = text.replace("\\n", "\n")

        info = manager.all_records.get_infos(rtype).get(key)
        if not info:
            continue
        rec = info.main_record
        if not rec:
            continue

        fmt = manager.format_loader.get_record(rtype)
        ff  = fmt.get_field(fname) if fmt else None
        if ff is None or not ff.is_import:
            continue

        field = rec.fields_map.get(fname)
        if not field:
            continue

        enc = rec.mod_file.encoding if rec.mod_file else TesEncoding.CP1252
        new_bytes = TesBytes.from_str(text, enc, null_terminate=(ff.data_type == "zstring"))
        field.modify(new_bytes)
        updated += 1

    return updated
