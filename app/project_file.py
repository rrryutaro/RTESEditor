from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


PROJECT_FORMAT = "RTESEditor Project"
PROJECT_VERSION = 1
_ENCODINGS = {"cp1252", "shift_jis", "utf-8"}


def serialize_project(entries: list[dict], project_path: str | Path) -> str:
    project = Path(project_path)
    base = project.parent.absolute()
    serialized_entries: list[dict] = []
    for entry in entries:
        source_path = Path(entry["path"]).absolute()
        try:
            stored_path = Path(os.path.relpath(source_path, base)).as_posix()
            path_kind = "relative"
        except ValueError:
            stored_path = str(source_path)
            path_kind = "absolute"
        serialized_entries.append({
            "path": stored_path,
            "path_kind": path_kind,
            "encoding": str(entry["encoding"]),
            "role": str(entry["role"]),
            "is_search_target": bool(entry.get("is_search_target", True)),
            "create_if_missing": bool(entry.get("create_if_missing", False)),
        })
    data = {
        "format": PROJECT_FORMAT,
        "version": PROJECT_VERSION,
        "entries": serialized_entries,
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def parse_project(text: str, project_path: str | Path) -> list[dict]:
    raw = json.loads(text)
    if raw.get("format") != PROJECT_FORMAT:
        raise ValueError("RTESEditorプロジェクトファイルではありません。")
    if raw.get("version") != PROJECT_VERSION:
        raise ValueError(f"未対応のプロジェクトバージョンです: {raw.get('version')}")
    raw_entries = raw.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("プロジェクトにentries配列がありません。")

    base = Path(project_path).parent.absolute()
    entries: list[dict] = []
    patch_count = 0
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("プロジェクト内のエントリ形式が不正です。")
        stored = str(raw_entry.get("path", "")).strip()
        role = str(raw_entry.get("role", "source"))
        encoding = str(raw_entry.get("encoding", "cp1252"))
        if not stored:
            raise ValueError("パスが空のプロジェクトエントリがあります。")
        if role not in ("source", "patch"):
            raise ValueError(f"不明なファイル役割です: {role}")
        if encoding not in _ENCODINGS:
            raise ValueError(f"不明なエンコードです: {encoding}")
        if role == "patch":
            patch_count += 1
        stored_path = Path(stored)
        if raw_entry.get("path_kind") == "absolute" or stored_path.is_absolute():
            resolved = stored_path
        else:
            resolved = base / stored_path
        entries.append({
            "path": str(resolved.absolute()),
            "encoding": encoding,
            "role": role,
            "is_search_target": bool(raw_entry.get("is_search_target", True)),
            # 旧プロジェクトでは安全側へ倒し、欠落パッチを勝手に新規作成しない。
            "create_if_missing": bool(raw_entry.get("create_if_missing", False)),
        })
    if patch_count > 1:
        raise ValueError("編集先パッチが複数指定されています。")
    return entries


def load_project_file(path: str | Path) -> list[dict]:
    target = Path(path)
    return parse_project(target.read_text(encoding="utf-8-sig"), target)


def save_project_file(path: str | Path, entries: list[dict]) -> None:
    target = Path(path)
    if not target.parent.exists():
        raise OSError(f"保存先フォルダーがありません: {target.parent}")
    text = serialize_project(entries, target)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
