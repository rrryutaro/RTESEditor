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
        """[{path, encoding, role, is_search_target, create_if_missing}, ...]。"""
        return self._data.get("last_files", [])

    def set_last_files(self, entries: list[dict]) -> None:
        self._data["last_files"] = entries
        self.save()

    # --- Mod読み込みパターン ---

    def get_load_patterns(self) -> list[dict]:
        patterns = self._data.get("load_patterns", [])
        if not isinstance(patterns, list):
            return []

        result: list[dict] = []
        for pattern in patterns:
            if not isinstance(pattern, dict):
                continue
            name = str(pattern.get("name", "")).strip()
            entries = pattern.get("entries", [])
            if name and isinstance(entries, list):
                result.append({"name": name, "entries": entries})
        return result

    def set_load_patterns(self, patterns: list[dict]) -> None:
        self._data["load_patterns"] = patterns
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
