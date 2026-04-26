from __future__ import annotations
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
from PySide6.QtGui import QFont
from app.record_info import AllRecordInfos
from tes3.format.format_loader import FormatLoader


class TreePanel(QTreeWidget):

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main = main_window
        self.setHeaderHidden(True)
        self.itemSelectionChanged.connect(self._on_select)

    def build(self, all_records: AllRecordInfos, fmt: FormatLoader) -> None:
        self.clear()
        for record_type in all_records.get_record_types():
            rec_fmt = fmt.get_record(record_type)
            if rec_fmt and not rec_fmt.is_show:
                continue
            infos = all_records.get_info_list(record_type)
            label = rec_fmt.description if rec_fmt else record_type
            item = QTreeWidgetItem([f"{label} ({len(infos)})"])
            item.setData(0, 256, record_type)  # Qt.UserRole = 256
            if rec_fmt and not rec_fmt.is_edit:
                font = item.font(0)
                font.setWeight(QFont.Normal)
                item.setFont(0, font)
            self.addTopLevelItem(item)

    @property
    def selected_record_type(self) -> str | None:
        items = self.selectedItems()
        return items[0].data(0, 256) if items else None

    def _on_select(self) -> None:
        rtype = self.selected_record_type
        if rtype:
            self._main.record_grid.load(rtype)
