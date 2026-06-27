from __future__ import annotations
import sys
import json
from pathlib import Path


def _get_app_dir() -> Path:
    """設定ファイルを置くディレクトリを返す。
    EXE実行時  : EXEと同じフォルダ（dist/RTESEditor/）
    Python実行時: スクリプトルートの dist/RTESEditor/ ← EXE出力先と同じ場所
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # app/settings.py → parent = app/ → parent = project_root
    project_root = Path(__file__).resolve().parent.parent
    return project_root / "dist" / "RTESEditor"


_APP_DIR = _get_app_dir()
_APP_DIR.mkdir(parents=True, exist_ok=True)
_SETTINGS_PATH = _APP_DIR / "settings.json"


class Settings:
    """アプリケーション設定の読込・保存（シングルトン）"""

    _instance: "Settings | None" = None

    def __init__(self):
        self._data: dict = {}
        self._load()

    @classmethod
    def instance(cls) -> "Settings":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load(self) -> None:
        if _SETTINGS_PATH.exists():
            try:
                self._data = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def save(self) -> None:
        _SETTINGS_PATH.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # --- 列表示設定 ---

    def get_visible_columns(self, record_type: str) -> list[str] | None:
        """None = 設定なし（フォーマットの IsShow を使用）"""
        return self._data.get("column_visibility", {}).get(record_type)

    def set_visible_columns(self, record_type: str, columns: list[str]) -> None:
        self._data.setdefault("column_visibility", {})[record_type] = columns
        self.save()

    # --- テーマ設定 ---

    def get_theme(self) -> str:
        return self._data.get("theme", "standard")

    def set_theme(self, theme: str) -> None:
        self._data["theme"] = theme
        self.save()

    # --- フォント設定 ---

    def get_font_family(self) -> str:
        return self._data.get("font_family", "")

    def get_font_size(self) -> int:
        return self._data.get("font_size", 0)

    def set_font(self, family: str, size: int) -> None:
        self._data["font_family"] = family
        self._data["font_size"] = size
        self.save()

    # --- 最終フォルダ ---

    def get_last_folder(self) -> str:
        return self._data.get("last_folder", "")

    def set_last_folder(self, folder: str) -> None:
        self._data["last_folder"] = folder
        self.save()

    # --- 前回開いたファイルリスト ---

    def get_last_files(self) -> list[dict]:
        """[{path, encoding, is_overwrite, is_save, is_search_target}, ...]"""
        return self._data.get("last_files", [])

    def set_last_files(self, entries: list[dict]) -> None:
        self._data["last_files"] = entries
        self.save()

    # --- ウィンドウ位置・サイズ ---

    def get_geometry(self) -> str:
        """QMainWindow.saveGeometry() を base64 エンコードした文字列。未設定時は空文字。"""
        return self._data.get("geometry", "")

    def set_geometry(self, encoded: str) -> None:
        self._data["geometry"] = encoded
        self.save()

    # --- スプリッター状態 ---

    def get_splitter_state(self, key: str) -> str:
        """QSplitter.saveState() を base64 エンコードした文字列。未設定時は空文字。"""
        return self._data.get("splitter_states", {}).get(key, "")

    def set_splitter_state(self, key: str, encoded: str) -> None:
        self._data.setdefault("splitter_states", {})[key] = encoded
        self.save()

    # --- ダイアログタブ INFOテーブル カラム表示設定 ---

    def get_info_table_columns(self) -> list[int] | None:
        """None = 設定なし（全列表示）。リストは表示する列インデックス。"""
        return self._data.get("info_table_columns")

    def set_info_table_columns(self, visible_indices: list[int]) -> None:
        self._data["info_table_columns"] = visible_indices
        self.save()

    # --- 読み上げ設定 ---

    def get_tts_enabled(self) -> bool:
        return bool(self._data.get("tts_enabled", False))

    def set_tts_enabled(self, value: bool) -> None:
        self._data["tts_enabled"] = bool(value)
        self.save()

    def get_tts_engine(self) -> str:
        engine = self._data.get("tts_engine", "sapi5")
        return "voicevox" if engine == "voicevox" else "sapi5"

    def set_tts_engine(self, value: str) -> None:
        self._data["tts_engine"] = "voicevox" if value == "voicevox" else "sapi5"
        self.save()

    def get_tts_voice(self) -> str:
        return self._data.get("tts_voice", "")

    def set_tts_voice(self, desc: str) -> None:
        self._data["tts_voice"] = desc or ""
        self.save()

    def get_tts_vv_speaker(self) -> int:
        try:
            return int(self._data.get("tts_vv_speaker", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def set_tts_vv_speaker(self, value: int) -> None:
        try:
            self._data["tts_vv_speaker"] = int(value)
        except (TypeError, ValueError):
            self._data["tts_vv_speaker"] = 0
        self.save()

    def get_tts_rate(self) -> int:
        try:
            return int(self._data.get("tts_rate", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def set_tts_rate(self, value: int) -> None:
        self._data["tts_rate"] = max(-10, min(10, int(value)))
        self.save()

    def get_tts_volume(self) -> int:
        try:
            value = int(self._data.get("tts_volume", 100))
        except (TypeError, ValueError):
            value = 100
        return max(0, min(100, value))

    def set_tts_volume(self, value: int) -> None:
        self._data["tts_volume"] = max(0, min(100, int(value)))
        self.save()

    def get_tts_interrupt(self) -> bool:
        return bool(self._data.get("tts_interrupt", True))

    def set_tts_interrupt(self, value: bool) -> None:
        self._data["tts_interrupt"] = bool(value)
        self.save()

    def get_tts_voicevox_dictionary(self) -> list[dict]:
        value = self._data.get("tts_voicevox_dictionary", {})
        if isinstance(value, dict):
            entries = value.get("entries", [])
        else:
            entries = value
        if not isinstance(entries, list):
            return []
        return [entry for entry in (self._normalize_voicevox_dict_entry(e) for e in entries) if entry]

    def set_tts_voicevox_dictionary(self, entries: list[dict]) -> None:
        normalized = [
            entry
            for entry in (self._normalize_voicevox_dict_entry(e) for e in entries)
            if entry
        ]
        self._data["tts_voicevox_dictionary"] = {
            "version": 1,
            "entries": normalized,
        }
        self.save()

    @staticmethod
    def _normalize_voicevox_dict_entry(entry: object) -> dict | None:
        if not isinstance(entry, dict):
            return None
        surface = str(entry.get("surface", "") or "").strip()
        pronunciation = str(entry.get("pronunciation", "") or "").strip()
        if not surface and not pronunciation:
            return None
        try:
            accent_type = int(entry.get("accent_type", 1) or 1)
        except (TypeError, ValueError):
            accent_type = 1
        try:
            priority = int(entry.get("priority", 5) or 5)
        except (TypeError, ValueError):
            priority = 5
        word_type = str(entry.get("word_type", "PROPER_NOUN") or "PROPER_NOUN")
        if word_type not in {"PROPER_NOUN", "COMMON_NOUN", "VERB", "ADJECTIVE", "SUFFIX"}:
            word_type = "PROPER_NOUN"
        return {
            "surface": surface,
            "pronunciation": pronunciation,
            "accent_type": max(0, min(99, accent_type)),
            "word_type": word_type,
            "priority": max(0, min(10, priority)),
            "voicevox_uuid": str(entry.get("voicevox_uuid", "") or "").strip(),
        }

    def get_journal_speak_date(self) -> bool:
        return bool(self._data.get("journal_speak_date", True))

    def set_journal_speak_date(self, value: bool) -> None:
        self._data["journal_speak_date"] = bool(value)
        self.save()

    def get_journal_speak_quest_title_in_date_view(self) -> bool:
        return bool(self._data.get("journal_speak_quest_title_in_date_view", True))

    def set_journal_speak_quest_title_in_date_view(self, value: bool) -> None:
        self._data["journal_speak_quest_title_in_date_view"] = bool(value)
        self.save()

    def get_journal_view_mode(self) -> str:
        value = self._data.get("journal_view_mode", "date")
        return "quest" if value == "quest" else "date"

    def set_journal_view_mode(self, value: str) -> None:
        self._data["journal_view_mode"] = "quest" if value == "quest" else "date"
        self.save()

    def get_journal_sort_order(self) -> str:
        value = self._data.get("journal_sort_order", "asc")
        return "desc" if value == "desc" else "asc"

    def set_journal_sort_order(self, value: str) -> None:
        self._data["journal_sort_order"] = "desc" if value == "desc" else "asc"
        self.save()

    def get_conversation_font_size(self) -> int:
        try:
            value = int(self._data.get("conversation_font_size", 13))
        except (TypeError, ValueError):
            value = 13
        return max(10, min(28, value))

    def set_conversation_font_size(self, value: int) -> None:
        self._data["conversation_font_size"] = max(10, min(28, int(value)))
        self.save()

    def get_journal_date_font_size(self) -> int:
        try:
            value = int(self._data.get("journal_date_font_size", 13))
        except (TypeError, ValueError):
            value = 13
        return max(10, min(28, value))

    def set_journal_date_font_size(self, value: int) -> None:
        self._data["journal_date_font_size"] = max(10, min(28, int(value)))
        self.save()

    def get_journal_title_font_size(self) -> int:
        try:
            value = int(self._data.get("journal_title_font_size", 13))
        except (TypeError, ValueError):
            value = 13
        return max(10, min(28, value))

    def set_journal_title_font_size(self, value: int) -> None:
        self._data["journal_title_font_size"] = max(10, min(28, int(value)))
        self.save()

    def get_journal_body_font_size(self) -> int:
        try:
            value = int(self._data.get("journal_body_font_size", 13))
        except (TypeError, ValueError):
            value = 13
        return max(10, min(28, value))

    def set_journal_body_font_size(self, value: int) -> None:
        self._data["journal_body_font_size"] = max(10, min(28, int(value)))
        self.save()

    def get_conversation_poll_interval_ms(self) -> int:
        try:
            value = int(self._data.get("conversation_poll_interval_ms", 150))
        except (TypeError, ValueError):
            value = 150
        return max(50, min(5000, value))

    def set_conversation_poll_interval_ms(self, value: int) -> None:
        self._data["conversation_poll_interval_ms"] = max(50, min(5000, int(value)))
        self.save()

    # --- 本表示設定 ---

    def get_book_font_size(self) -> int:
        try:
            value = int(self._data.get("book_font_size", 13))
        except (TypeError, ValueError):
            value = 13
        return max(10, min(28, value))

    def set_book_font_size(self, value: int) -> None:
        self._data["book_font_size"] = max(10, min(28, int(value)))
        self.save()

    def get_book_line_height_percent(self) -> int:
        try:
            value = int(self._data.get("book_line_height_percent", 122))
        except (TypeError, ValueError):
            value = 122
        return max(100, min(180, value))

    def set_book_line_height_percent(self, value: int) -> None:
        self._data["book_line_height_percent"] = max(100, min(180, int(value)))
        self.save()
