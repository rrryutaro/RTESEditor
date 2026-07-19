from __future__ import annotations
from pathlib import Path
from core.encoding import TesEncoding
from tes3.record import Record


ROLE_SOURCE = "source"
ROLE_PATCH = "patch"


class ModFile:
    """1つのESP/ESMファイルを表す（C#のModMain相当）"""

    def __init__(self, path: str | Path, encoding: TesEncoding, is_overwrite: bool = False,
                 is_save: bool = False, is_search_target: bool = True,
                 role: str | None = None):
        self.path = Path(path)
        self.encoding = encoding
        self.role = role or (ROLE_PATCH if is_save else ROLE_SOURCE)
        # 旧APIとの互換用。競合解決は常にロード順で行い、書込可否は role で決める。
        self.is_overwrite = self.role == ROLE_PATCH
        self.is_save = self.role == ROLE_PATCH
        self.is_search_target = is_search_target
        self.header_record: Record | None = None
        self.records: list[Record] = []
        self.record_map: dict[str, list[Record]] = {}  # record_type -> [Record]
        self.last_backup_path: Path | None = None
        self.structure_modified = False

    @property
    def file_name(self) -> str:
        return self.path.name

    @property
    def is_patch(self) -> bool:
        return self.role == ROLE_PATCH

    @property
    def is_read_only(self) -> bool:
        return not self.is_patch

    @property
    def is_dirty(self) -> bool:
        if not self.is_patch:
            return False
        if not self.path.exists():
            return True
        if self.structure_modified:
            return True
        records = [self.header_record, *self.records]
        return any(
            record is not None
            and (
                record.is_modified
                or any(field.is_modified for field in record.fields)
            )
            for record in records
        )

    def add_record(self, record: Record) -> None:
        record.mod_file = self
        if record.record_type == "TES3":
            self.header_record = record
        else:
            self.records.append(record)
            self.record_map.setdefault(record.record_type, []).append(record)
            record.index = len(self.record_map[record.record_type])
            record.is_overwrite_save = self.is_patch

    def save(self) -> int:
        """編集先パッチをDIAL/INFOの親子関係を保って保存する。"""
        if not self.is_patch:
            raise PermissionError(f"参照元ファイルは保存できません: {self.path}")
        from tes3.patch_writer import save_patch
        return save_patch(self)

    def save_diff(self, path: Path | None = None) -> int:
        """互換用。編集先パッチ全体を安全な順序で保存する。"""
        if not self.is_patch:
            raise PermissionError(f"参照元ファイルは保存できません: {self.path}")
        from tes3.patch_writer import save_patch
        return save_patch(self, path)
