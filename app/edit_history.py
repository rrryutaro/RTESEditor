from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from core.bytes_util import TesBytes


class EditAction(Protocol):
    description: str

    def undo(self) -> None: ...

    def redo(self) -> None: ...


@dataclass
class FieldEditAction:
    field: object
    record: object
    before: bytes
    after: bytes
    before_field_modified: bool
    before_record_modified: bool
    description: str

    def undo(self) -> None:
        self.field.data = TesBytes(self.before)
        self.field.is_modified = self.before_field_modified
        self.record.is_modified = self.before_record_modified

    def redo(self) -> None:
        self.field.data = TesBytes(self.after)
        self.field.is_modified = True
        self.record.is_modified = True
        self.record.is_overwrite_save = True


@dataclass
class CallbackEditAction:
    """レコード／フィールド構造変更を履歴へ載せるための操作。"""

    undo_callback: Callable[[], None]
    redo_callback: Callable[[], None]
    description: str

    def undo(self) -> None:
        self.undo_callback()

    def redo(self) -> None:
        self.redo_callback()


@dataclass
class CompoundEditAction:
    """複数操作をユーザーからは1回の編集として扱う。"""

    actions: list[EditAction]
    description: str

    def undo(self) -> None:
        for action in reversed(self.actions):
            action.undo()

    def redo(self) -> None:
        for action in self.actions:
            action.redo()


class EditHistory:
    """保存までのフィールド編集を保持する軽量Undo/Redo履歴。"""

    def __init__(self, limit: int = 500):
        self._undo: list[EditAction] = []
        self._redo: list[EditAction] = []
        self._limit = limit

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_description(self) -> str:
        return self._undo[-1].description if self._undo else ""

    @property
    def redo_description(self) -> str:
        return self._redo[-1].description if self._redo else ""

    def push(self, action: EditAction) -> None:
        self._undo.append(action)
        if len(self._undo) > self._limit:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self) -> EditAction | None:
        if not self._undo:
            return None
        action = self._undo.pop()
        action.undo()
        self._redo.append(action)
        return action

    def redo(self) -> EditAction | None:
        if not self._redo:
            return None
        action = self._redo.pop()
        action.redo()
        self._undo.append(action)
        return action

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
