from __future__ import annotations
from dataclasses import dataclass
import re
import struct
from pathlib import Path
from core.bytes_util import TesBytes
from core.encoding import TesEncoding
from tes3.reader import Tes3Reader
from tes3.field import Field
from tes3.mod_file import ModFile, ROLE_PATCH, ROLE_SOURCE
from tes3.record import Record, Tes3RecordGroup
from tes3.format.format_loader import FormatLoader
from app.record_info import AllRecordInfos
from app.edit_history import (
    CallbackEditAction,
    CompoundEditAction,
    EditHistory,
    FieldEditAction,
)


@dataclass(frozen=True)
class PatchIssue:
    severity: str
    message: str
    record: Record | None = None


@dataclass(frozen=True)
class PatchFieldDiff:
    field_name: str
    occurrence: int
    before: str
    after: str


@dataclass(frozen=True)
class _PatchRecordPlacement:
    record: Record
    record_index: int
    type_index: int
    parent_group: Tes3RecordGroup | None
    parent_index: int


_ADDTOPIC_PATTERN = re.compile(r'\baddtopic\s+"([^"]+)"', re.IGNORECASE)


class ModManager:
    """複数Modの読込・統合・競合解決を管理する（C#のModFileFactory + AllRecordInfos生成相当）"""

    def __init__(self):
        self._format_loader = FormatLoader()
        self._format_loader.load()
        self._reader = Tes3Reader(self._format_loader)
        self.mod_files: list[ModFile] = []
        self.all_records = AllRecordInfos()
        self.history = EditHistory()

    @property
    def format_loader(self) -> FormatLoader:
        return self._format_loader

    @property
    def active_patch(self) -> ModFile | None:
        patches = [mod for mod in self.mod_files if mod.is_patch]
        return patches[0] if patches else None

    def load_mod(
        self,
        path: str | Path,
        encoding: TesEncoding = TesEncoding.CP1252,
        is_overwrite: bool = False,
        is_save: bool = False,
        is_search_target: bool = True,
        on_progress: callable = None,
        role: str | None = None,
    ) -> ModFile:
        resolved_role = role or (ROLE_PATCH if is_save else ROLE_SOURCE)
        if resolved_role == ROLE_PATCH and self.active_patch is not None:
            raise ValueError("編集先パッチは1つだけ指定できます。")
        if resolved_role == ROLE_SOURCE and self.active_patch is not None:
            raise ValueError("参照元ファイルは編集先パッチより前に読み込む必要があります。")

        mod = self._reader.load(
            path,
            encoding,
            resolved_role == ROLE_PATCH,
            resolved_role == ROLE_PATCH,
            on_progress,
        )
        mod.role = resolved_role
        mod.is_overwrite = resolved_role == ROLE_PATCH
        mod.is_save = resolved_role == ROLE_PATCH
        mod.is_search_target = is_search_target
        self.mod_files.append(mod)
        self._integrate(mod)
        return mod

    def create_patch(
        self,
        path: str | Path,
        encoding: TesEncoding = TesEncoding.UTF_8,
    ) -> ModFile:
        """最初の参照元ヘッダーを基に、空の編集先ESPをメモリ上に作成する。"""
        if self.active_patch is not None:
            raise ValueError("編集先パッチは1つだけ指定できます。")
        source_header = next(
            (mod.header_record for mod in self.mod_files if mod.header_record is not None),
            None,
        )
        if source_header is None:
            raise ValueError("新規パッチを作るには、先に参照元ESM/ESPを読み込んでください。")

        patch = ModFile(path, encoding, role=ROLE_PATCH)
        header = source_header.clone()
        hedr = header.fields_map.get("HEDR")
        if hedr is None or len(hedr.data.raw()) < 300:
            raise ValueError("参照元のTES3ヘッダーに有効なHEDRフィールドがありません。")

        # 新規ESPにはHEDRのみを引き継ぐ。元ファイルのMAST/DATAは複製しない。
        header.fields = [hedr]
        header.fields_map = {"HEDR": hedr}
        hedr.parent_record = header
        raw = bytearray(hedr.data.raw())
        struct.pack_into("<I", raw, 4, 0)  # ESPフラグ
        raw[8:40] = self._fixed_ascii("RTESEditor", 32)
        raw[40:296] = self._fixed_ascii("RTESEditor correction patch", 256)
        struct.pack_into("<I", raw, 296, 0)
        hedr.data = TesBytes(bytes(raw))
        hedr.is_modified = False
        header.is_modified = False
        patch.add_record(header)

        self.mod_files.append(patch)
        self.ensure_patch_masters()
        return patch

    def _integrate(self, mod: ModFile) -> None:
        for record in mod.records:
            self.all_records.add_record(record)
        self.all_records.build_dialogue_index()

    def prepare_field_for_edit(self, source_record: Record, source_field):
        """参照元を変更せず、編集先パッチの対応フィールドを返す。"""
        if source_field is None:
            raise ValueError("編集対象のフィールドがありません。")
        actual_source = getattr(source_field, "parent_record", None) or source_record
        target_record = self.ensure_override(actual_source)

        occurrence = 0
        found = False
        for field in actual_source.fields:
            if field.field_type != source_field.field_type:
                continue
            if field is source_field:
                found = True
                break
            occurrence += 1
        if not found:
            occurrence = 0

        matches = [
            field for field in target_record.fields
            if field.field_type == source_field.field_type
        ]
        if occurrence >= len(matches):
            raise ValueError(
                f"編集先に {source_field.field_type}[{occurrence}] がありません。"
            )
        return matches[occurrence], target_record

    def apply_field_data(
        self,
        field,
        record: Record,
        new_data: TesBytes,
        description: str | None = None,
    ) -> bool:
        """編集先フィールドを変更し、保存前のUndo/Redo履歴へ登録する。"""
        before = field.data.raw()
        after = new_data.raw()
        if before == after:
            return False
        field_action = FieldEditAction(
            field=field,
            record=record,
            before=before,
            after=after,
            before_field_modified=field.is_modified,
            before_record_modified=record.is_modified,
            description=description or (
                f"{record.record_type} {record.primary_key} / {field.field_type}"
            ),
        )
        field.modify(new_data)
        creation_action = self._take_pending_creation_action(record)
        if creation_action is None:
            action = field_action
        else:
            action = CompoundEditAction(
                [creation_action, field_action],
                field_action.description,
            )
        self.history.push(action)
        return True

    def ensure_override(self, source_record: Record) -> Record:
        """レコードを編集先パッチへコピーし、INFOなら親DIALも必ず用意する。"""
        patch = self.active_patch
        if patch is None:
            raise RuntimeError("編集先パッチが指定されていません。")
        if source_record.mod_file is patch:
            return source_record

        existing = self._find_patch_record(source_record)
        if existing is not None:
            return existing

        if source_record.record_type == "INFO":
            before_structure_modified = patch.structure_modified
            source_group = source_record.parent_group
            source_dial = source_group.parent_record if source_group is not None else None
            if source_dial is None or source_dial.record_type != "DIAL":
                raise ValueError(
                    f"INFO {source_record.primary_key!r} の親DIALを特定できません。"
                )
            existing_dial = self._find_patch_record(source_dial)
            patch_dial = self._ensure_patch_dial(source_dial)
            clone = self._clone_for_patch(source_record, patch)
            group = patch_dial.children_group
            group.records.append(clone)
            clone.parent_group = group
            patch.add_record(clone)
            patch.structure_modified = True
            self.all_records.add_record(clone)
            self.all_records.build_dialogue_index()
            created = [clone]
            if existing_dial is None:
                created.insert(0, patch_dial)
            self._remember_pending_creation(
                clone,
                created,
                before_structure_modified,
            )
            return clone

        before_structure_modified = patch.structure_modified
        clone = self._clone_for_patch(source_record, patch)
        patch.add_record(clone)
        patch.structure_modified = True
        if clone.record_type == "DIAL":
            self._attach_empty_dialogue_group(clone)
        self.all_records.add_record(clone)
        self.all_records.build_dialogue_index()
        self._remember_pending_creation(
            clone,
            [clone],
            before_structure_modified,
        )
        return clone

    def copy_record_as_override(self, source_record: Record) -> Record:
        """明示的なコピー操作を1回のUndo対象として編集先へ追加する。"""
        target = self.ensure_override(source_record)
        action = self._take_pending_creation_action(target)
        if action is not None:
            self.history.push(action)
        return target

    @staticmethod
    def is_record_deleted(record: Record | None) -> bool:
        if record is None:
            return False
        return bool(record.flags & 0x20) or any(
            field.field_type == "DELE" for field in record.fields
        )

    def mark_record_deleted(self, source_record: Record) -> Record:
        """編集先パッチのオーバーライドへTES3標準のDELEを追加する。"""
        if self.is_record_deleted(source_record):
            if source_record.mod_file is self.active_patch:
                return source_record
            raise ValueError("選択した参照元レコードは既に削除扱いです。")

        target = self.ensure_override(source_record)
        patch = self.active_patch
        if patch is None:
            raise RuntimeError("編集先パッチが指定されていません。")
        if self.is_record_deleted(target):
            return target

        creation_action = self._take_pending_creation_action(target)
        before_structure_modified = patch.structure_modified
        before_record_modified = target.is_modified
        before_flags = target.flags
        index = len(target.fields)
        dele = Field(
            "DELE",
            TesBytes((0).to_bytes(4, "little")),
            None,
            parent_record=target,
        )

        def add_dele() -> None:
            if dele not in target.fields:
                target.fields.insert(min(index, len(target.fields)), dele)
            self._rebuild_field_map(target)
            dele.is_modified = True
            target.flags = before_flags & ~0x20
            target.mark_modified()
            patch.structure_modified = True

        def remove_dele() -> None:
            target.fields = [field for field in target.fields if field is not dele]
            self._rebuild_field_map(target)
            dele.is_modified = False
            target.flags = before_flags
            target.is_modified = before_record_modified
            patch.structure_modified = before_structure_modified

        add_dele()
        marker_action = CallbackEditAction(
            remove_dele,
            add_dele,
            f"ゲーム内削除: {target.record_type} {target.primary_key}",
        )
        action = (
            CompoundEditAction(
                [creation_action, marker_action],
                marker_action.description,
            )
            if creation_action is not None
            else marker_action
        )
        self.history.push(action)
        return target

    def restore_record_deleted(self, record: Record) -> bool:
        """編集先パッチにあるDELE／内部削除フラグを取り除く。"""
        patch = self.active_patch
        if patch is None or record.mod_file is not patch:
            raise ValueError("削除指定を解除できるのは編集先パッチのレコードだけです。")
        dele_fields = [field for field in record.fields if field.field_type == "DELE"]
        if not dele_fields and not (record.flags & 0x20):
            return False

        # 削除マーカー以外に実値の変更がなければ、オーバーライド自体を
        # 外す方が安全。既存Modの最小DELEレコードも不完全な形で残さない。
        non_marker_diffs = [
            diff for diff in self.get_patch_field_diffs(record)
            if diff.field_name != "DELE"
        ]
        if all(diff.after == "(フィールドなし)" for diff in non_marker_diffs):
            self.remove_patch_record(record)
            return True

        placements = [
            (field, record.fields.index(field), field.is_modified)
            for field in dele_fields
        ]
        before_structure_modified = patch.structure_modified
        before_record_modified = record.is_modified
        before_flags = record.flags

        def restore_markers() -> None:
            for field, index, was_modified in sorted(
                placements,
                key=lambda value: value[1],
            ):
                if field not in record.fields:
                    record.fields.insert(min(index, len(record.fields)), field)
                field.is_modified = was_modified
            self._rebuild_field_map(record)
            record.flags = before_flags
            record.is_modified = before_record_modified
            patch.structure_modified = before_structure_modified

        def remove_markers() -> None:
            record.fields = [
                field for field in record.fields if field.field_type != "DELE"
            ]
            self._rebuild_field_map(record)
            record.flags &= ~0x20
            record.mark_modified()
            patch.structure_modified = True

        remove_markers()
        self.history.push(CallbackEditAction(
            restore_markers,
            remove_markers,
            f"ゲーム内削除を解除: {record.record_type} {record.primary_key}",
        ))
        return True

    def repair_orphan_patch_infos(self) -> tuple[int, list[str]]:
        """既存パッチの孤立INFOを、同一INAMの参照元から一意に補修する。"""
        patch = self.active_patch
        if patch is None:
            return 0, []
        repaired = 0
        warnings: list[str] = []
        for record in list(patch.records):
            if record.record_type != "INFO" or record.parent_group is not None:
                continue
            info = self.all_records.find_record_info("INFO", record.primary_key)
            candidates = [
                candidate
                for candidate in (info.records if info is not None else [])
                if candidate.mod_file is not patch
                and candidate.parent_group is not None
                and candidate.parent_group.parent_record is not None
            ]
            labels = {
                candidate.parent_group.label.strip().casefold()
                for candidate in candidates
                if candidate.parent_group.label.strip()
            }
            if not candidates or len(labels) != 1:
                warnings.append(
                    f"INFO {record.primary_key!r}: 親DIAL候補を一意に特定できません。"
                    "「編集先パッチ」タブで親DIALを指定してください。"
                )
                continue
            source_dial = candidates[-1].parent_group.parent_record
            patch_dial = self._ensure_patch_dial(source_dial)
            group = patch_dial.children_group
            group.records.append(record)
            record.parent_group = group
            record.is_overwrite_save = True
            record.mark_modified()
            patch_dial.mark_modified()
            patch.structure_modified = True
            repaired += 1
        if repaired:
            self.all_records.build_dialogue_index()
        return repaired, warnings

    def ensure_patch_masters(self) -> int:
        """参照元ファイルを編集先パッチのMAST/DATAへ不足分だけ追加する。"""
        patch = self.active_patch
        if patch is None or patch.header_record is None:
            return 0
        header = patch.header_record
        known: set[str] = set()
        current_master: str | None = None
        for field in header.fields:
            if field.field_type == "MAST":
                current_master = field.data.to_str(patch.encoding).strip()
                if current_master:
                    known.add(current_master.casefold())
            elif field.field_type == "DATA":
                current_master = None

        added = 0
        mast_format = header.record_format.get_field("MAST") if header.record_format else None
        data_format = header.record_format.get_field("DATA") if header.record_format else None
        for source in self.mod_files:
            if source is patch or source.file_name.casefold() in known:
                continue
            size = source.path.stat().st_size if source.path.exists() else 0
            mast = Field(
                "MAST",
                TesBytes.from_str(source.file_name, patch.encoding, null_terminate=True),
                mast_format,
                parent_record=header,
            )
            data = Field(
                "DATA",
                TesBytes(size.to_bytes(8, "little")),
                data_format,
                parent_record=header,
            )
            header.fields.extend([mast, data])
            header.fields_map["MAST"] = mast
            header.fields_map["DATA"] = data
            known.add(source.file_name.casefold())
            added += 1
        if added:
            header.mark_modified()
            patch.structure_modified = True
        return added

    def get_parent_dialogue_candidates(self, info_record: Record) -> list[Record]:
        """同一INFO IDを持つ参照元から、論理的に異なる親DIAL候補を返す。"""
        patch = self.active_patch
        info = self.all_records.find_record_info("INFO", info_record.primary_key)
        by_label: dict[str, Record] = {}
        for candidate in info.records if info is not None else []:
            if candidate.mod_file is patch or candidate.parent_group is None:
                continue
            parent = candidate.parent_group.parent_record
            if parent is None or parent.record_type != "DIAL":
                continue
            label = candidate.parent_group.label.strip().casefold()
            if label:
                by_label[label] = parent
        return list(by_label.values())

    def assign_patch_info_parent(self, info_record: Record, source_dial: Record) -> Record:
        """編集先パッチの孤立INFOへ、選択された参照元DIALを関連付ける。"""
        patch = self.active_patch
        if patch is None or info_record.mod_file is not patch:
            raise ValueError("編集先パッチのINFOではありません。")
        if info_record.record_type != "INFO":
            raise ValueError("親DIALを指定できるのはINFOだけです。")
        if info_record.parent_group is not None:
            info_record.parent_group.records = [
                record for record in info_record.parent_group.records
                if record is not info_record
            ]
        patch_dial = self._ensure_patch_dial(source_dial)
        group = patch_dial.children_group
        if info_record not in group.records:
            group.records.append(info_record)
        info_record.parent_group = group
        info_record.mark_modified()
        patch_dial.mark_modified()
        patch.structure_modified = True
        self.all_records.build_dialogue_index()
        return patch_dial

    def get_patch_field_diffs(self, patch_record: Record) -> list[PatchFieldDiff]:
        """編集先パッチのレコードを、直前の参照元レコードとフィールド単位で比較する。"""
        patch = self.active_patch
        if patch is None or patch_record.mod_file is not patch:
            return []
        source = self._find_source_baseline(patch_record)
        source_fields = self._fields_by_occurrence(source) if source is not None else {}
        patch_fields = self._fields_by_occurrence(patch_record)
        ordered_keys = list(source_fields)
        ordered_keys.extend(key for key in patch_fields if key not in source_fields)
        diffs: list[PatchFieldDiff] = []
        for field_key in ordered_keys:
            source_field = source_fields.get(field_key)
            patch_field = patch_fields.get(field_key)
            if self._fields_equal(
                source_field,
                patch_field,
                source.mod_file.encoding if source is not None else TesEncoding.CP1252,
                patch.encoding,
            ):
                continue
            diffs.append(PatchFieldDiff(
                field_name=field_key[0],
                occurrence=field_key[1],
                before=(
                    self._field_display(source_field, source.mod_file.encoding)
                    if source_field is not None and source is not None
                    else "(フィールドなし)"
                ),
                after=(
                    self._field_display(patch_field, patch.encoding)
                    if patch_field is not None
                    else "(フィールドなし)"
                ),
            ))
        return diffs

    def _find_source_baseline(self, patch_record: Record) -> Record | None:
        remembered = getattr(patch_record, "_source_record", None)
        if remembered is not None:
            return remembered
        patch = self.active_patch
        info = self.all_records.find_record_info(
            patch_record.record_type,
            patch_record.primary_key,
        )
        candidates = [
            record for record in (info.records if info is not None else [])
            if record.mod_file is not patch
        ]
        if patch_record.record_type == "INFO" and patch_record.parent_group is not None:
            parent_label = patch_record.parent_group.label.strip().casefold()
            same_parent = [
                record for record in candidates
                if record.parent_group is not None
                and record.parent_group.label.strip().casefold() == parent_label
            ]
            if same_parent:
                candidates = same_parent
        return candidates[-1] if candidates else None

    @staticmethod
    def _fields_by_occurrence(record: Record | None) -> dict[tuple[str, int], object]:
        if record is None:
            return {}
        counts: dict[str, int] = {}
        result: dict[tuple[str, int], object] = {}
        for field in record.fields:
            occurrence = counts.get(field.field_type, 0)
            counts[field.field_type] = occurrence + 1
            result[(field.field_type, occurrence)] = field
        return result

    @staticmethod
    def _field_display(field, encoding: TesEncoding) -> str:
        try:
            return field.to_display_str(encoding)
        except (IndexError, struct.error, UnicodeError, ValueError):
            return field.data.raw().hex()

    @classmethod
    def _fields_equal(
        cls,
        source_field,
        patch_field,
        source_encoding: TesEncoding,
        patch_encoding: TesEncoding,
    ) -> bool:
        if source_field is None or patch_field is None:
            return source_field is patch_field
        field_format = getattr(source_field, "field_format", None)
        if field_format is not None and field_format.data_type in ("string", "zstring"):
            return (
                cls._field_display(source_field, source_encoding)
                == cls._field_display(patch_field, patch_encoding)
            )
        return source_field.data.raw() == patch_field.data.raw()

    def remove_patch_record(self, record: Record) -> list[Record]:
        """選択したオーバーライドをパッチから除去し、参照元の状態へ戻す。"""
        patch = self.active_patch
        if patch is None or record.mod_file is not patch:
            raise ValueError("編集先パッチのレコードではありません。")
        removing: list[Record] = [record]
        if record.record_type == "DIAL":
            removing.extend([
                candidate for candidate in patch.records
                if candidate.record_type == "INFO"
                and candidate.parent_group is not None
                and candidate.parent_group.parent_record is record
            ])

        # INFOのためだけに自動追加した空DIALは一緒に片付ける。
        if record.record_type == "INFO":
            parent = record.parent_group.parent_record if record.parent_group else None
            if (
                parent is not None
                and parent.mod_file is patch
                and getattr(parent, "_patch_context_only", False)
                and parent.children_group is not None
                and all(
                    child in removing for child in parent.children_group.records
                )
            ):
                removing.append(parent)

        unique_ids = {id(candidate) for candidate in removing}
        ordered_removing = [
            candidate for candidate in patch.records if id(candidate) in unique_ids
        ]
        before_structure_modified = patch.structure_modified
        detach, attach = self._make_record_placement_callbacks(
            ordered_removing,
            detached_structure_modified=True,
            attached_structure_modified=before_structure_modified,
        )
        detach()
        description = (
            f"オーバーライド除去: {record.record_type} {record.primary_key}"
        )
        self.history.push(CallbackEditAction(attach, detach, description))
        return ordered_removing

    def validate_active_patch(self) -> list[PatchIssue]:
        patch = self.active_patch
        if patch is None:
            return [PatchIssue("error", "編集先パッチが指定されていません。")]
        issues: list[PatchIssue] = []

        from tes3.patch_writer import PatchValidationError, ordered_patch_records
        try:
            ordered_patch_records(patch)
        except PatchValidationError as exc:
            issues.extend(PatchIssue("error", line) for line in str(exc).splitlines())

        seen: dict[tuple[str, str], Record] = {}
        for record in patch.records:
            key = (record.record_type, record.primary_key.strip().casefold())
            if key in seen:
                issues.append(PatchIssue(
                    "error",
                    f"重複レコードがあります: {record.record_type} {record.primary_key!r}",
                    record,
                ))
            else:
                seen[key] = record

        known_topics = {
            info.main_record.primary_key.strip().casefold()
            for info in self.all_records.get_info_list("DIAL")
            if info.main_record is not None
            and not self.is_record_deleted(info.main_record)
        }
        for record in patch.records:
            dele_fields = [
                field for field in record.fields if field.field_type == "DELE"
            ]
            if any(len(field.data.raw()) != 4 for field in dele_fields):
                issues.append(PatchIssue(
                    "error",
                    f"DELEフィールドがuint32ではありません: "
                    f"{record.record_type} {record.primary_key!r}",
                    record,
                ))
            if record.flags & 0x20 and not dele_fields:
                issues.append(PatchIssue(
                    "warning",
                    f"内部削除フラグだけが設定されています。DELE形式を推奨します: "
                    f"{record.record_type} {record.primary_key!r}",
                    record,
                ))
            if record.record_type == "DIAL" and self.is_record_deleted(record):
                live_children = [
                    child for child in patch.record_map.get("INFO", [])
                    if child.parent_group is not None
                    and child.parent_group.parent_record is record
                    and not self.is_record_deleted(child)
                ]
                if live_children:
                    issues.append(PatchIssue(
                        "warning",
                        f"削除扱いのDIALに未削除のINFOが {len(live_children)}件あります: "
                        f"{record.primary_key!r}",
                        record,
                    ))
        for record in patch.record_map.get("INFO", []):
            result_field = record.fields_map.get("BNAM")
            if result_field is None:
                continue
            for topic in _ADDTOPIC_PATTERN.findall(
                result_field.to_display_str(patch.encoding)
            ):
                if topic.strip().casefold() not in known_topics:
                    issues.append(PatchIssue(
                        "warning",
                        f"AddTopicの参照先DIALが見つかりません: {topic!r} "
                        f"(INFO {record.primary_key!r})",
                        record,
                    ))

        known_masters = {
            field.data.to_str(patch.encoding).strip().casefold()
            for field in (patch.header_record.fields if patch.header_record else [])
            if field.field_type == "MAST"
        }
        for source in self.mod_files:
            if source is not patch and source.file_name.casefold() not in known_masters:
                issues.append(PatchIssue(
                    "warning",
                    f"参照元がMASTにありません: {source.file_name}",
                ))
        return issues

    @staticmethod
    def _reindex_patch_records(patch: ModFile) -> None:
        for records in patch.record_map.values():
            for index, record in enumerate(records, start=1):
                record.index = index

    @staticmethod
    def _rebuild_field_map(record: Record) -> None:
        record.fields_map = {}
        for field in record.fields:
            record.fields_map[field.field_type] = field

    @staticmethod
    def _remember_pending_creation(
        target: Record,
        created_records: list[Record],
        before_structure_modified: bool,
    ) -> None:
        setattr(
            target,
            "_pending_history_creation",
            (tuple(created_records), before_structure_modified),
        )

    def _take_pending_creation_action(
        self,
        target: Record,
    ) -> CallbackEditAction | None:
        pending = getattr(target, "_pending_history_creation", None)
        if pending is None:
            return None
        delattr(target, "_pending_history_creation")
        created_records, before_structure_modified = pending
        patch = self.active_patch
        if patch is None:
            return None

        detach_created_records, attach_created_records = (
            self._make_record_placement_callbacks(
                list(created_records),
                detached_structure_modified=before_structure_modified,
                attached_structure_modified=True,
            )
        )

        return CallbackEditAction(
            detach_created_records,
            attach_created_records,
            f"オーバーライド作成: {target.record_type} {target.primary_key}",
        )

    def _make_record_placement_callbacks(
        self,
        records_to_place: list[Record],
        *,
        detached_structure_modified: bool,
        attached_structure_modified: bool,
    ):
        patch = self.active_patch
        if patch is None:
            raise RuntimeError("編集先パッチが指定されていません。")
        placements: list[_PatchRecordPlacement] = []
        for record in records_to_place:
            parent = record.parent_group
            placements.append(_PatchRecordPlacement(
                record=record,
                record_index=patch.records.index(record),
                type_index=patch.record_map.get(record.record_type, []).index(record),
                parent_group=parent,
                parent_index=(
                    parent.records.index(record)
                    if parent is not None and record in parent.records
                    else -1
                ),
            ))

        def detach_records() -> None:
            for placement in reversed(placements):
                record = placement.record
                if placement.parent_group is not None:
                    placement.parent_group.records = [
                        child for child in placement.parent_group.records
                        if child is not record
                    ]
                patch.records = [
                    candidate for candidate in patch.records if candidate is not record
                ]
                records = patch.record_map.get(record.record_type, [])
                remaining = [candidate for candidate in records if candidate is not record]
                if remaining:
                    patch.record_map[record.record_type] = remaining
                else:
                    patch.record_map.pop(record.record_type, None)
                self.all_records.delete_record(record)
            self._reindex_patch_records(patch)
            patch.structure_modified = detached_structure_modified
            self.all_records.build_dialogue_index()

        def attach_records() -> None:
            for placement in sorted(placements, key=lambda value: value.record_index):
                record = placement.record
                if record not in patch.records:
                    patch.records.insert(
                        min(placement.record_index, len(patch.records)),
                        record,
                    )
                records = patch.record_map.setdefault(record.record_type, [])
                if record not in records:
                    records.insert(min(placement.type_index, len(records)), record)
                if placement.parent_group is not None:
                    group_records = placement.parent_group.records
                    if record not in group_records:
                        group_records.insert(
                            min(placement.parent_index, len(group_records)),
                            record,
                        )
                    record.parent_group = placement.parent_group
                self.all_records.add_record(record)
            self._reindex_patch_records(patch)
            patch.structure_modified = attached_structure_modified
            self.all_records.build_dialogue_index()

        return detach_records, attach_records

    def _find_patch_record(self, source_record: Record) -> Record | None:
        patch = self.active_patch
        if patch is None:
            return None
        source_key = source_record.primary_key.strip().casefold()
        source_parent = (
            source_record.parent_group.label.strip().casefold()
            if source_record.record_type == "INFO" and source_record.parent_group is not None
            else ""
        )
        for record in reversed(patch.record_map.get(source_record.record_type, [])):
            if record.primary_key.strip().casefold() != source_key:
                continue
            if source_record.record_type != "INFO":
                return record
            parent = record.parent_group.label.strip().casefold() if record.parent_group else ""
            if parent == source_parent:
                return record
        return None

    def _ensure_patch_dial(self, source_dial: Record) -> Record:
        existing = self._find_patch_record(source_dial)
        if existing is not None:
            self._attach_empty_dialogue_group(existing)
            return existing
        patch = self.active_patch
        clone = self._clone_for_patch(source_dial, patch)
        patch.add_record(clone)
        self._attach_empty_dialogue_group(clone)
        setattr(clone, "_patch_context_only", True)
        patch.structure_modified = True
        self.all_records.add_record(clone)
        return clone

    @staticmethod
    def _attach_empty_dialogue_group(record: Record) -> None:
        if record.children_group is not None:
            return
        record.children_group = Tes3RecordGroup(
            group_type=7,
            label=record.primary_key,
            parent_record=record,
            is_synthetic=True,
        )

    @staticmethod
    def _clone_for_patch(source: Record, patch: ModFile) -> Record:
        clone = source.clone()
        source_encoding = (
            source.mod_file.encoding if source.mod_file is not None else TesEncoding.CP1252
        )
        for source_field, target_field in zip(source.fields, clone.fields):
            field_format = getattr(source_field, "field_format", None)
            if field_format is None or field_format.data_type not in ("string", "zstring"):
                target_field.is_modified = False
                continue
            text = source_field.data.to_str(source_encoding)
            target_field.data = TesBytes.from_str(
                text,
                patch.encoding,
                null_terminate=field_format.data_type == "zstring",
            )
            target_field.is_modified = False
        clone.is_modified = False
        clone.is_overwrite_save = True
        clone.parent_group = None
        clone.children_group = None
        setattr(clone, "_source_record", source)
        return clone

    @staticmethod
    def _fixed_ascii(value: str, size: int) -> bytes:
        encoded = value.encode("ascii", errors="replace")[:size]
        return encoded + b"\x00" * (size - len(encoded))

    def clear(self) -> None:
        self.mod_files.clear()
        self.all_records = AllRecordInfos()
        self.history.clear()
