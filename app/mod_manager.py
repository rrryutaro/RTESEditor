from __future__ import annotations
from pathlib import Path
from core.encoding import TesEncoding
from tes3.reader import Tes3Reader
from tes3.mod_file import ModFile
from tes3.format.format_loader import FormatLoader
from app.record_info import AllRecordInfos


class ModManager:
    """複数Modの読込・統合・競合解決を管理する（C#のModFileFactory + AllRecordInfos生成相当）"""

    def __init__(self):
        self._format_loader = FormatLoader()
        self._format_loader.load()
        self._reader = Tes3Reader(self._format_loader)
        self.mod_files: list[ModFile] = []
        self.all_records = AllRecordInfos()

    @property
    def format_loader(self) -> FormatLoader:
        return self._format_loader

    def load_mod(
        self,
        path: str | Path,
        encoding: TesEncoding = TesEncoding.CP1252,
        is_overwrite: bool = False,
        is_save: bool = False,
        is_search_target: bool = True,
        on_progress: callable = None,
    ) -> ModFile:
        mod = self._reader.load(path, encoding, is_overwrite, is_save, on_progress)
        mod.is_search_target = is_search_target
        self.mod_files.append(mod)
        self._integrate(mod)
        return mod

    def _integrate(self, mod: ModFile) -> None:
        for record in mod.records:
            self.all_records.add_record(record)

    def clear(self) -> None:
        self.mod_files.clear()
        self.all_records = AllRecordInfos()
