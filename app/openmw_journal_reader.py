from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class OpenMwJournalRecord:
    entry_type: int
    topic: str
    info_id: str
    text: str
    day: int = 0
    month: int = 0
    day_of_month: int = 0
    order: int = 0


@dataclass
class OpenMwJournalReadResult:
    records: list[OpenMwJournalRecord] = field(default_factory=list)
    source_path: Path | None = None
    year: int | None = None
    diagnostics: dict[str, object] = field(default_factory=dict)


class OpenMwJournalReader:
    """OpenMW の最新保存データから JOUR レコードを読む軽量リーダー。"""

    def read_latest_save(self) -> OpenMwJournalReadResult:
        save_path = self.find_latest_save()
        if save_path is None:
            return OpenMwJournalReadResult(diagnostics={"status": "save_not_found"})
        return self.read_save(save_path)

    def find_latest_save(self) -> Path | None:
        candidates: list[Path] = []
        home = Path.home()
        roots = [
            home / "Documents" / "My Games" / "OpenMW" / "saves",
            home / "OneDrive" / "Documents" / "My Games" / "OpenMW" / "saves",
            home / "OneDrive" / "ドキュメント" / "My Games" / "OpenMW" / "saves",
        ]
        for root in roots:
            if not root.exists():
                continue
            try:
                candidates.extend(path for path in root.rglob("*.omwsave") if path.is_file())
            except OSError:
                continue
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def read_save(self, path: Path) -> OpenMwJournalReadResult:
        try:
            data = path.read_bytes()
        except OSError as exc:
            return OpenMwJournalReadResult(
                source_path=path,
                diagnostics={"status": "read_failed", "error": str(exc)},
            )

        records: list[OpenMwJournalRecord] = []
        year: int | None = None
        pos = 0
        record_count = 0
        while pos + 16 <= len(data):
            record_type = data[pos:pos + 4].decode("ascii", errors="replace")
            size = self._u32(data, pos + 4)
            record_start = pos + 16
            record_end = record_start + size
            if record_end > len(data) or record_end < record_start:
                break
            if record_type == "JOUR":
                entry = self._read_journal_record(data[record_start:record_end], len(records))
                if entry is not None:
                    records.append(entry)
            elif record_type == "GLOB" and year is None:
                year = self._read_year_global(data[record_start:record_end])
            record_count += 1
            pos = record_end

        return OpenMwJournalReadResult(
            records=records,
            source_path=path,
            year=year,
            diagnostics={
                "status": "ok",
                "record_count": record_count,
                "journal_count": len(records),
                "year": year,
            },
        )

    def _read_journal_record(self, data: bytes, order: int) -> OpenMwJournalRecord | None:
        fields: dict[str, bytes] = {}
        pos = 0
        while pos + 8 <= len(data):
            field_type = data[pos:pos + 4].decode("ascii", errors="replace")
            size = self._u32(data, pos + 4)
            field_start = pos + 8
            field_end = field_start + size
            if field_end > len(data) or field_end < field_start:
                break
            fields[field_type] = data[field_start:field_end]
            pos = field_end

        entry_type = self._i32(fields.get("JETY", b""), default=0)
        if entry_type not in {0, 1, 2}:
            return None
        text = self.decode_save_string(fields.get("TEXT", b"")).strip()
        if not text:
            return None
        return OpenMwJournalRecord(
            entry_type=entry_type,
            topic=self.decode_ref_id(fields.get("YETO", b"")),
            info_id=self.decode_ref_id(fields.get("YEIN", b"")),
            text=text,
            day=self._i32(fields.get("JEDA", b""), default=0),
            month=self._i32(fields.get("JEMO", b""), default=0),
            day_of_month=self._i32(fields.get("JEDM", b""), default=0),
            order=order,
        )

    def _read_year_global(self, data: bytes) -> int | None:
        fields: dict[str, bytes] = {}
        pos = 0
        while pos + 8 <= len(data):
            field_type = data[pos:pos + 4].decode("ascii", errors="replace")
            size = self._u32(data, pos + 4)
            field_start = pos + 8
            field_end = field_start + size
            if field_end > len(data) or field_end < field_start:
                break
            fields[field_type] = data[field_start:field_end]
            pos = field_end
        name = self.decode_ref_id(fields.get("NAME", b"")).strip().casefold()
        if name != "year":
            return None
        raw = fields.get("FLTV", b"")
        if len(raw) < 4:
            return None
        value = struct.unpack_from("<f", raw, 0)[0]
        if value <= 0:
            return None
        return int(round(value))

    @staticmethod
    def decode_save_string(raw: bytes | None) -> str:
        value = (raw or b"").rstrip(b"\x00")
        if not value:
            return ""
        for encoding in ("utf-8", "shift_jis", "cp1252"):
            try:
                return value.decode(encoding, errors="strict")
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")

    @staticmethod
    def decode_ref_id(raw: bytes | None) -> str:
        value = (raw or b"").rstrip(b"\x00")
        if value and value[0] < 0x20:
            value = value[1:]
        return OpenMwJournalReader.decode_save_string(value)

    @staticmethod
    def _u32(data: bytes, offset: int) -> int:
        if offset + 4 > len(data):
            return 0
        return struct.unpack_from("<I", data, offset)[0]

    @staticmethod
    def _i32(data: bytes, *, default: int) -> int:
        if len(data) < 4:
            return default
        return struct.unpack_from("<i", data, 0)[0]
