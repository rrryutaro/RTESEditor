from __future__ import annotations

import html
import re
import struct
import threading
import traceback
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app import voicevox_client
from app.openmw_conversation_reader import (
    OpenMwConversationReader,
    OpenMwConversationResult,
    OpenMwDialogueChoice,
    OpenMwDialogueResponse,
)
from app.openmw_book_reader import OpenMwBookReader, OpenMwDisplayedBookResult
from app.openmw_journal_reader import OpenMwJournalReader, OpenMwJournalRecord
from app.settings import Settings
from app.tts_service import TTSService, _log_tts_error
from core.encoding import TesEncoding


_TTS_RATE_PRESETS = [
    ("遅い", -4),
    ("やや遅い", -2),
    ("標準", 0),
    ("やや速い", 2),
    ("速い", 4),
]
_VOICEVOX_WORD_TYPES = [
    ("固有名詞", "PROPER_NOUN"),
    ("普通名詞", "COMMON_NOUN"),
    ("動詞", "VERB"),
    ("形容詞", "ADJECTIVE"),
    ("語尾", "SUFFIX"),
]

_ITEM_RECORD_TYPES = (
    "ALCH",
    "APPA",
    "ARMO",
    "BOOK",
    "CLOT",
    "INGR",
    "LIGH",
    "LOCK",
    "MISC",
    "PROB",
    "REPA",
    "WEAP",
)

_JOURNAL_MONTH_GMST_IDS = (
    "sMonthMorningstar",
    "sMonthSunsdawn",
    "sMonthFirstseed",
    "sMonthRainshand",
    "sMonthSecondseed",
    "sMonthMidyear",
    "sMonthSunsheight",
    "sMonthLastseed",
    "sMonthHearthfire",
    "sMonthFrostfall",
    "sMonthSunsdusk",
    "sMonthEveningstar",
)

_JOURNAL_MONTH_FALLBACK_NAMES = (
    "暁星の月",
    "薄明の月",
    "蒔種の月",
    "恵雨の月",
    "栽培の月",
    "真央の月",
    "陽高の月",
    "収穫の月",
    "炉火の月",
    "降霜の月",
    "黄昏の月",
    "星霜の月",
)


@dataclass(frozen=True)
class _FormattedDialogueText:
    plain: str
    rich: str


@dataclass(frozen=True)
class _ReadableEntry:
    title: str
    text: str
    key: str = ""
    rich_text: str = ""
    raw_text: str = ""
    items: tuple[dict[str, str], ...] = ()


class _ResponseFrame(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        event.accept()


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        event.accept()


class _ConversationFetchSignals(QObject):
    finished = Signal(object, int)
    failed = Signal(str, int)


class _BookFetchSignals(QObject):
    finished = Signal(object, int)
    failed = Signal(str, int)


class _BookSpeechSignals(QObject):
    segment_started = Signal(int, int)
    finished = Signal(int, bool)


class _VoicevoxDictionarySignals(QObject):
    fetched = Signal(object, str, int)
    registered = Signal(object, str, int)


class ConversationPanel(QWidget):
    """OpenMW の実行中会話履歴を表示するタブ。"""

    def __init__(self, main_window, tts: TTSService):
        super().__init__(main_window)
        self._main = main_window
        self._tts = tts
        self._settings = Settings.instance()
        self._reader = OpenMwConversationReader()
        self._book_reader = OpenMwBookReader()
        self._journal_reader = OpenMwJournalReader()
        self._result: OpenMwConversationResult | None = None
        self._fetch_signals = _ConversationFetchSignals()
        self._fetch_signals.finished.connect(self._on_fetch_finished, Qt.ConnectionType.QueuedConnection)
        self._fetch_signals.failed.connect(self._on_fetch_failed, Qt.ConnectionType.QueuedConnection)
        self._book_fetch_signals = _BookFetchSignals()
        self._book_fetch_signals.finished.connect(self._on_book_fetch_finished, Qt.ConnectionType.QueuedConnection)
        self._book_fetch_signals.failed.connect(self._on_book_fetch_failed, Qt.ConnectionType.QueuedConnection)
        self._book_speech_signals = _BookSpeechSignals()
        self._book_speech_signals.segment_started.connect(
            self._on_book_speech_segment_started,
            Qt.ConnectionType.QueuedConnection,
        )
        self._book_speech_signals.finished.connect(
            self._on_book_speech_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._fetch_thread: threading.Thread | None = None
        self._fetch_active = False
        self._fetch_token = 0
        self._book_fetch_thread: threading.Thread | None = None
        self._book_fetch_active = False
        self._book_fetch_token = 0
        self._closing = False
        self._displayed_signature: tuple[tuple[str, str, str], ...] = ()
        self._displayed_response_signature: tuple[tuple[str, str], ...] = ()
        self._displayed_choice_signature: tuple[tuple[str, int], ...] = ()
        self._displayed_keyword_signature: tuple[str, ...] = ()
        self._displayed_actor_signature = ""
        self._auto_speak_generation = 0
        self._auto_spoken_keys: set[tuple[int, int, str, str]] = set()
        self._auto_spoken_order: list[tuple[int, int, str, str]] = []
        self._auto_speak_pending_items: list[tuple[tuple[int, int, str, str], str]] = []
        self._auto_speak_pending_keys: set[tuple[int, int, str, str]] = set()
        self._auto_speak_timer_active = False
        self._message_widget: QWidget | None = None
        self._message_text = ""
        self._response_frames: list[QFrame] = []
        self._choice_set_frame: QFrame | None = None
        self._choice_set_parent_layout: QVBoxLayout | None = None
        self._selected_frame: QFrame | None = None
        self._actor_name = ""
        self._npc_detail_actor_key = ""
        self._npc_detail_mod_count = -1
        self._follow_latest_selection = True
        self._manual_fetch_active = False
        self._loading_tts_controls = False
        self._voicevox_available = False
        self._voicevox_speakers: list[dict] = []
        self._journal_entries: list[_ReadableEntry] = []
        self._journal_source_path = ""
        self._journal_year: int | None = None
        self._journal_response_dial_cache: dict[str, str] = {}
        self._journal_response_dial_cache_mod_count = -1
        self._current_book_entry: _ReadableEntry | None = None
        self._book_entries: list[_ReadableEntry] = []
        self._book_history_entries: list[_ReadableEntry] = []
        self._book_show_all = False
        self._conversation_font_size = self._settings.get_conversation_font_size()
        self._journal_date_font_size = self._settings.get_journal_date_font_size()
        self._journal_title_font_size = self._settings.get_journal_title_font_size()
        self._journal_body_font_size = self._settings.get_journal_body_font_size()
        self._book_font_size = self._settings.get_book_font_size()
        self._book_line_height_percent = self._settings.get_book_line_height_percent()
        self._book_speech_segments: list[dict[str, object]] = []
        self._book_current_segment_index = -1
        self._book_speech_token = 0
        self._free_speech_signals = _BookSpeechSignals()
        self._free_speech_signals.segment_started.connect(
            self._on_free_speech_segment_started,
            Qt.ConnectionType.QueuedConnection,
        )
        self._free_speech_signals.finished.connect(
            self._on_free_speech_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._free_plain_text = ""
        self._free_speech_segments: list[dict[str, object]] = []
        self._free_current_segment_index = -1
        self._free_speech_token = 0
        self._dictionary_signals = _VoicevoxDictionarySignals()
        self._dictionary_signals.fetched.connect(
            self._on_voicevox_dictionary_fetched,
            Qt.ConnectionType.QueuedConnection,
        )
        self._dictionary_signals.registered.connect(
            self._on_voicevox_dictionary_registered,
            Qt.ConnectionType.QueuedConnection,
        )
        self._loading_dictionary_table = False
        self._dictionary_thread: threading.Thread | None = None
        self._dictionary_task_active = False
        self._dictionary_token = 0
        self._conversation_was_active = False

        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(self._settings.get_conversation_poll_interval_ms())
        self._auto_timer.timeout.connect(self._on_auto_timeout)

        self._setup_ui()
        self._apply_tts_settings(save=False)

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        self._status = QLabel(self.tr("未取得"), self)
        self._status.hide()
        self._debug = QTextEdit(self)
        self._debug.setReadOnly(True)
        self._debug.hide()

        body = QHBoxLayout()
        body.setSpacing(8)
        self._reading_tabs = QTabWidget(self)
        self._reading_tabs.addTab(self._build_conversation_area(), self.tr("会話"))
        self._reading_tabs.addTab(self._build_journal_tab(), self.tr("ジャーナル"))
        self._reading_tabs.addTab(self._build_book_tab(), self.tr("本"))
        self._reading_tabs.addTab(self._build_free_tab(), self.tr("自由"))
        self._reading_tabs.addTab(self._build_dictionary_tab(), self.tr("辞書"))
        self._reading_tabs.currentChanged.connect(self._on_reading_tab_changed)
        body.addWidget(self._reading_tabs, 1)
        body.addWidget(self._build_tts_panel())
        root.addLayout(body, 1)

        selected_bar = QHBoxLayout()
        selected_bar.setContentsMargins(0, 0, 0, 0)
        selected_bar.addWidget(QLabel(self.tr("選択した項目:")))
        selected_bar.addStretch(1)
        self._copy_selected_btn = QPushButton(self.tr("コピー"))
        self._copy_selected_btn.clicked.connect(self._copy_selected_text)
        selected_bar.addWidget(self._copy_selected_btn)
        root.addLayout(selected_bar)

        self._selected_text = QTextEdit()
        self._selected_text.setReadOnly(True)
        self._selected_text.setMaximumHeight(120)
        self._selected_text.setPlaceholderText(self.tr("項目を選択すると、ここにコピー用の文字列を表示します。"))
        root.addWidget(self._selected_text)

        self.setStyleSheet(
            """
            QFrame#conversationSurface {
                background: #242321;
                border: 1px solid #5f594d;
                border-radius: 4px;
            }
            QFrame#conversationHeader {
                background: #31302c;
                border: none;
                border-bottom: 1px solid #5f594d;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QLabel#conversationActor {
                color: #f0d88a;
                font-weight: 700;
                font-size: 15px;
            }
            QScrollArea#historyScroll {
                background: #242321;
                border: none;
            }
            QWidget#historyViewport {
                background: #242321;
            }
            QFrame#responseFrame {
                background: #efe2bd;
                border: 1px solid #9a8358;
                border-radius: 4px;
            }
            QFrame#responseFrame[selected="true"] {
                background: #fff0c7;
                border: 2px solid #d4aa43;
            }
            QFrame#choiceSetFrame {
                background: rgba(36, 35, 33, 30);
                border: 1px solid rgba(154, 131, 88, 130);
                border-radius: 3px;
            }
            QFrame#choiceSetFrame[selected="true"] {
                background: rgba(150, 38, 28, 55);
                border: 1px solid #c84a35;
                border-radius: 3px;
            }
            QLabel#responseTitle {
                color: #4a3519;
                font-weight: 700;
            }
            QLabel#responseText {
                color: #221b14;
            }
            QLabel#choiceText {
                color: #d64935;
                font-weight: 600;
            }
            QLabel#conversationMessage {
                color: #d7d1c6;
                padding: 10px;
            }
            QToolButton#responseSpeak {
                background: #f8edcf;
                border: 1px solid #9a8358;
                border-radius: 4px;
                min-width: 26px;
                min-height: 24px;
            }
            QToolButton#responseSpeak:hover {
                background: #fff6dc;
            }
            QToolButton#choiceSpeak {
                background: rgba(248, 237, 207, 180);
                border: 1px solid #9a8358;
                border-radius: 4px;
                min-width: 26px;
                min-height: 24px;
            }
            QToolButton#choiceSpeak:hover {
                background: #fff6dc;
            }
            QScrollArea#journalScroll {
                background: #242321;
                border: none;
            }
            QWidget#journalViewport {
                background: #242321;
            }
            QFrame#journalEntryFrame {
                background: #efe2bd;
                border: 1px solid #9a8358;
                border-radius: 4px;
            }
            QFrame#journalEntryFrame[selected="true"] {
                background: #fff0c7;
                border: 2px solid #d4aa43;
            }
            QLabel#journalDate,
            QLabel#journalQuest {
                color: #b1261d;
                font-weight: 700;
            }
            QLabel#journalText {
                color: #16110d;
            }
            QToolButton#journalSpeak {
                background: #f8edcf;
                border: 1px solid #9a8358;
                border-radius: 4px;
                min-width: 26px;
                min-height: 24px;
            }
            QToolButton#journalSpeak:hover {
                background: #fff6dc;
            }
            QFrame#npcStatusPanel {
                background: #20242a;
                border: 1px solid #555f6c;
                border-radius: 4px;
            }
            QLabel#npcName {
                color: #f0d88a;
                font-weight: 700;
                font-size: 15px;
            }
            QLabel#npcSubtle {
                color: #aeb7c3;
            }
            QLabel#npcSection {
                color: #d7dce3;
                font-weight: 700;
                border-top: 1px solid #46515f;
                padding-top: 6px;
            }
            QLabel#npcKey {
                color: #9aa7b5;
            }
            QLabel#npcValue {
                color: #edf0f4;
                font-weight: 600;
            }
            QProgressBar#npcBar {
                background: #14181e;
                border: 1px solid #4d5967;
                border-radius: 3px;
                color: #edf0f4;
                text-align: center;
                min-height: 16px;
                max-height: 16px;
            }
            QProgressBar#npcBar::chunk {
                background: #4c8a6a;
                border-radius: 2px;
            }
            QProgressBar#npcBar[stat="health"]::chunk {
                background: #b84a4a;
            }
            QProgressBar#npcBar[stat="magicka"]::chunk {
                background: #416fbd;
            }
            QProgressBar#npcBar[stat="fatigue"]::chunk {
                background: #4f965f;
            }
            QListWidget#npcList {
                background: #171b21;
                border: 1px solid #46515f;
                border-radius: 3px;
                color: #edf0f4;
            }
            """
        )
        self._apply_conversation_font_sizes()
        self._apply_journal_font_sizes()

    def _build_conversation_area(self) -> QWidget:
        area = QWidget(self)
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(self._build_conversation_toolbar())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        body.addWidget(self._build_conversation_surface(), 1)
        body.addWidget(self._build_npc_status_panel())
        layout.addLayout(body, 1)
        return area

    def _build_conversation_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        self._fetch_btn = QPushButton(self.tr("取得"))
        self._fetch_btn.setToolTip(self.tr("OpenMW の実行中プロセスから会話履歴を取得します。"))
        self._fetch_btn.clicked.connect(self._on_fetch_clicked)
        toolbar.addWidget(self._fetch_btn)

        self._rescan_btn = QPushButton(self.tr("再探索"))
        self._rescan_btn.setToolTip(self.tr("キャッシュを使わずに mHistoryContents 候補を再探索します。"))
        self._rescan_btn.clicked.connect(self._on_rescan_clicked)
        toolbar.addWidget(self._rescan_btn)

        self._auto_check = QCheckBox(self.tr("自動更新"))
        self._auto_check.setToolTip(self.tr("同じ OpenMW 起動中はキャッシュを使って短い間隔で再読込します。"))
        self._auto_check.toggled.connect(self._on_auto_toggled)
        toolbar.addWidget(self._auto_check)

        toolbar.addWidget(QLabel(self.tr("間隔:")))
        self._poll_combo = QComboBox()
        for ms in (50, 100, 150, 300, 500, 1000):
            self._poll_combo.addItem(f"{ms} ms", ms)
        current_poll = self._settings.get_conversation_poll_interval_ms()
        index = self._poll_combo.findData(current_poll)
        if index < 0:
            self._poll_combo.addItem(f"{current_poll} ms", current_poll)
            index = self._poll_combo.findData(current_poll)
        self._poll_combo.setCurrentIndex(index)
        self._poll_combo.currentIndexChanged.connect(self._on_poll_interval_changed)
        toolbar.addWidget(self._poll_combo)

        self._pause_btn = QPushButton(self.tr("一時停止"))
        self._pause_btn.clicked.connect(self._tts.pause_speaking)
        toolbar.addWidget(self._pause_btn)

        self._resume_btn = QPushButton(self.tr("再開"))
        self._resume_btn.clicked.connect(self._tts.resume_speaking)
        toolbar.addWidget(self._resume_btn)

        self._stop_btn = QPushButton(self.tr("停止"))
        self._stop_btn.clicked.connect(self._tts.stop_speaking)
        toolbar.addWidget(self._stop_btn)

        toolbar.addWidget(QLabel(self.tr("文字:")))
        self._conversation_font_slider = QSlider(Qt.Orientation.Horizontal)
        self._conversation_font_slider.setRange(10, 28)
        self._conversation_font_slider.setValue(self._conversation_font_size)
        self._conversation_font_slider.setFixedWidth(120)
        self._conversation_font_slider.valueChanged.connect(self._on_conversation_font_size_changed)
        toolbar.addWidget(self._conversation_font_slider)
        self._conversation_font_value = QLabel(str(self._conversation_font_size))
        self._conversation_font_value.setMinimumWidth(24)
        self._conversation_font_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        toolbar.addWidget(self._conversation_font_value)

        toolbar.addStretch(1)
        self._actor_label = _ClickableLabel(self.tr("未取得"))
        self._actor_label.setMinimumWidth(120)
        self._actor_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._actor_label.clicked.connect(self._select_actor_name)
        toolbar.addWidget(self._actor_label)
        return toolbar

    def _build_journal_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        self._journal_refresh_btn = QPushButton(self.tr("更新"), tab)
        self._journal_refresh_btn.clicked.connect(self._refresh_journal_entries)
        toolbar.addWidget(self._journal_refresh_btn)
        self._journal_speak_btn = QPushButton(self.tr("読み上げ"), tab)
        self._journal_speak_btn.clicked.connect(self._speak_selected_journal)
        toolbar.addWidget(self._journal_speak_btn)
        self._journal_pause_btn = QPushButton(self.tr("一時停止"), tab)
        self._journal_pause_btn.clicked.connect(self._tts.pause_speaking)
        toolbar.addWidget(self._journal_pause_btn)
        self._journal_resume_btn = QPushButton(self.tr("再開"), tab)
        self._journal_resume_btn.clicked.connect(self._tts.resume_speaking)
        toolbar.addWidget(self._journal_resume_btn)
        self._journal_stop_btn = QPushButton(self.tr("停止"), tab)
        self._journal_stop_btn.clicked.connect(self._tts.stop_speaking)
        toolbar.addWidget(self._journal_stop_btn)

        self._add_journal_font_slider(
            toolbar,
            tab,
            self.tr("日付"),
            self._journal_date_font_size,
            self._on_journal_date_font_size_changed,
            "_journal_date_font_value",
        )
        self._add_journal_font_slider(
            toolbar,
            tab,
            self.tr("題名"),
            self._journal_title_font_size,
            self._on_journal_title_font_size_changed,
            "_journal_title_font_value",
        )
        self._add_journal_font_slider(
            toolbar,
            tab,
            self.tr("本文"),
            self._journal_body_font_size,
            self._on_journal_body_font_size_changed,
            "_journal_body_font_value",
        )

        toolbar.addWidget(QLabel(self.tr("表示:"), tab))
        self._journal_mode_combo = QComboBox(tab)
        self._journal_mode_combo.addItem(self.tr("日付別"), "date")
        self._journal_mode_combo.addItem(self.tr("クエスト別"), "quest")
        mode_index = self._journal_mode_combo.findData(self._settings.get_journal_view_mode())
        self._journal_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self._journal_mode_combo.currentIndexChanged.connect(self._on_journal_view_changed)
        toolbar.addWidget(self._journal_mode_combo)
        toolbar.addWidget(QLabel(self.tr("順序:"), tab))
        self._journal_order_combo = QComboBox(tab)
        self._journal_order_combo.addItem(self.tr("昇順"), "asc")
        self._journal_order_combo.addItem(self.tr("降順"), "desc")
        order_index = self._journal_order_combo.findData(self._settings.get_journal_sort_order())
        self._journal_order_combo.setCurrentIndex(order_index if order_index >= 0 else 0)
        self._journal_order_combo.currentIndexChanged.connect(self._on_journal_view_changed)
        toolbar.addWidget(self._journal_order_combo)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        self._journal_list = QListWidget(tab)
        self._journal_list.setMinimumWidth(260)
        self._journal_list.currentRowChanged.connect(self._on_journal_selected)
        body.addWidget(self._journal_list, 0)
        self._journal_scroll = QScrollArea(tab)
        self._journal_scroll.setObjectName("journalScroll")
        self._journal_scroll.setWidgetResizable(True)
        self._journal_contents = QWidget(self._journal_scroll)
        self._journal_contents.setObjectName("journalViewport")
        self._journal_layout = QVBoxLayout(self._journal_contents)
        self._journal_layout.setContentsMargins(10, 10, 10, 10)
        self._journal_layout.setSpacing(8)
        self._journal_layout.setAlignment(Qt.AlignTop)
        self._journal_scroll.setWidget(self._journal_contents)
        body.addWidget(self._journal_scroll, 1)
        layout.addLayout(body, 1)
        self._show_journal_message(self.tr("更新を押すと、OpenMWの最新保存データからジャーナルを表示します。"))
        return tab

    def _add_journal_font_slider(
        self,
        toolbar: QHBoxLayout,
        parent: QWidget,
        label: str,
        value: int,
        slot,
        value_attr: str,
    ) -> None:
        toolbar.addWidget(QLabel(label, parent))
        slider = QSlider(Qt.Orientation.Horizontal, parent)
        slider.setRange(10, 28)
        slider.setValue(value)
        slider.setFixedWidth(110)
        slider.valueChanged.connect(slot)
        toolbar.addWidget(slider)
        value_label = QLabel(str(value), parent)
        value_label.setMinimumWidth(22)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        toolbar.addWidget(value_label)
        setattr(self, value_attr, value_label)

    def _build_book_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        self._current_book_btn = QPushButton(self.tr("表示中を取得"), tab)
        self._current_book_btn.clicked.connect(self._on_current_book_clicked)
        toolbar.addWidget(self._current_book_btn)
        self._book_speak_btn = QPushButton(self.tr("読み上げ"), tab)
        self._book_speak_btn.clicked.connect(self._speak_selected_book)
        toolbar.addWidget(self._book_speak_btn)
        self._book_pause_btn = QPushButton(self.tr("一時停止"), tab)
        self._book_pause_btn.clicked.connect(self._tts.pause_speaking)
        toolbar.addWidget(self._book_pause_btn)
        self._book_resume_btn = QPushButton(self.tr("再開"), tab)
        self._book_resume_btn.clicked.connect(self._tts.resume_speaking)
        toolbar.addWidget(self._book_resume_btn)
        self._book_stop_btn = QPushButton(self.tr("停止"), tab)
        self._book_stop_btn.clicked.connect(self._stop_book_speech)
        toolbar.addWidget(self._book_stop_btn)

        self._book_show_all_check = QCheckBox(self.tr("すべて表示"), tab)
        self._book_show_all_check.toggled.connect(self._on_book_show_all_toggled)
        toolbar.addWidget(self._book_show_all_check)

        toolbar.addWidget(QLabel(self.tr("検索"), tab))
        self._book_search_box = QLineEdit(tab)
        self._book_search_box.setPlaceholderText(self.tr("Enterで検索"))
        self._book_search_box.setMinimumWidth(180)
        self._book_search_box.returnPressed.connect(self._refresh_book_list)
        toolbar.addWidget(self._book_search_box)

        toolbar.addWidget(QLabel(self.tr("開始"), tab))
        self._book_start_combo = QComboBox(tab)
        self._book_start_combo.setMinimumWidth(220)
        toolbar.addWidget(self._book_start_combo)

        slider_width = 150
        toolbar.addWidget(QLabel(self.tr("文字"), tab))
        self._book_font_slider = QSlider(Qt.Orientation.Horizontal, tab)
        self._book_font_slider.setRange(10, 28)
        self._book_font_slider.setValue(self._book_font_size)
        self._book_font_slider.setFixedWidth(slider_width)
        self._book_font_slider.valueChanged.connect(self._on_book_font_size_changed)
        toolbar.addWidget(self._book_font_slider)
        self._book_font_value = QLabel(str(self._book_font_size), tab)
        self._book_font_value.setMinimumWidth(24)
        self._book_font_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        toolbar.addWidget(self._book_font_value)

        toolbar.addWidget(QLabel(self.tr("行間"), tab))
        self._book_line_slider = QSlider(Qt.Orientation.Horizontal, tab)
        self._book_line_slider.setRange(100, 180)
        self._book_line_slider.setValue(self._book_line_height_percent)
        self._book_line_slider.setFixedWidth(slider_width)
        self._book_line_slider.valueChanged.connect(self._on_book_line_height_changed)
        toolbar.addWidget(self._book_line_slider)
        self._book_line_value = QLabel(f"{self._book_line_height_percent}%", tab)
        self._book_line_value.setMinimumWidth(38)
        self._book_line_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        toolbar.addWidget(self._book_line_value)

        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        self._book_list = QListWidget(tab)
        self._book_list.setMinimumWidth(240)
        self._book_list.currentRowChanged.connect(self._on_book_selected)
        body.addWidget(self._book_list, 0)
        self._book_text = QTextEdit(tab)
        self._book_text.setReadOnly(True)
        self._book_text.setPlaceholderText(self.tr("OpenMWで本を開いた状態で「表示中を取得」を押すと本文を表示します。"))
        self._book_text.setStyleSheet("background:#17120a;color:#f1e3b5;")
        body.addWidget(self._book_text, 1)
        layout.addLayout(body, 1)
        self._refresh_book_list()
        return tab

    def _build_free_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        self._free_speak_btn = QPushButton(self.tr("読み上げ"), tab)
        self._free_speak_btn.clicked.connect(self._speak_free_text)
        toolbar.addWidget(self._free_speak_btn)
        self._free_pause_btn = QPushButton(self.tr("一時停止"), tab)
        self._free_pause_btn.clicked.connect(self._tts.pause_speaking)
        toolbar.addWidget(self._free_pause_btn)
        self._free_resume_btn = QPushButton(self.tr("再開"), tab)
        self._free_resume_btn.clicked.connect(self._tts.resume_speaking)
        toolbar.addWidget(self._free_resume_btn)
        self._free_stop_btn = QPushButton(self.tr("停止"), tab)
        self._free_stop_btn.clicked.connect(self._stop_free_speech)
        toolbar.addWidget(self._free_stop_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self._free_text = QTextEdit(tab)
        self._free_text.setPlaceholderText(self.tr("読み上げたい文章を貼り付けてください。"))
        self._free_text.setStyleSheet("background:#17120a;color:#f1e3b5;")
        layout.addWidget(self._free_text, 1)
        return tab

    def _build_dictionary_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        self._dict_add_btn = QPushButton(self.tr("追加"), tab)
        self._dict_add_btn.clicked.connect(self._add_dictionary_entry)
        toolbar.addWidget(self._dict_add_btn)
        self._dict_delete_btn = QPushButton(self.tr("削除"), tab)
        self._dict_delete_btn.clicked.connect(self._delete_selected_dictionary_entries)
        toolbar.addWidget(self._dict_delete_btn)
        self._dict_save_btn = QPushButton(self.tr("保存"), tab)
        self._dict_save_btn.clicked.connect(self._save_dictionary_from_table)
        toolbar.addWidget(self._dict_save_btn)
        self._dict_fetch_btn = QPushButton(self.tr("VOICEVOXから取得"), tab)
        self._dict_fetch_btn.clicked.connect(self._fetch_voicevox_dictionary)
        toolbar.addWidget(self._dict_fetch_btn)
        self._dict_register_btn = QPushButton(self.tr("VOICEVOXへ登録"), tab)
        self._dict_register_btn.clicked.connect(self._register_voicevox_dictionary)
        toolbar.addWidget(self._dict_register_btn)
        toolbar.addStretch(1)
        self._dict_status = QLabel("", tab)
        self._dict_status.setMinimumWidth(180)
        toolbar.addWidget(self._dict_status)
        layout.addLayout(toolbar)

        self._dict_table = QTableWidget(0, 6, tab)
        self._dict_table.setHorizontalHeaderLabels(
            [
                self.tr("単語"),
                self.tr("よみ"),
                self.tr("アクセント"),
                self.tr("種類"),
                self.tr("優先度"),
                self.tr("UUID"),
            ]
        )
        self._dict_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._dict_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._dict_table.setAlternatingRowColors(True)
        self._dict_table.verticalHeader().setVisible(False)
        self._dict_table.setColumnWidth(0, 180)
        self._dict_table.setColumnWidth(1, 180)
        self._dict_table.setColumnWidth(2, 90)
        self._dict_table.setColumnWidth(3, 120)
        self._dict_table.setColumnWidth(4, 80)
        self._dict_table.setColumnHidden(5, True)
        layout.addWidget(self._dict_table, 1)

        self._populate_dictionary_table(self._settings.get_tts_voicevox_dictionary())
        self._dict_table.cellChanged.connect(self._on_dictionary_table_changed)
        return tab

    def _build_conversation_surface(self) -> QFrame:
        surface = QFrame(self)
        surface.setObjectName("conversationSurface")
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame(surface)
        header.setObjectName("conversationHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 8, 10, 8)
        self._surface_actor = _ClickableLabel(self.tr("会話"))
        self._surface_actor.setObjectName("conversationActor")
        self._surface_actor.setCursor(Qt.CursorShape.PointingHandCursor)
        self._surface_actor.clicked.connect(self._select_actor_name)
        header_layout.addWidget(self._surface_actor)
        header_layout.addStretch(1)
        layout.addWidget(header)

        self._scroll = QScrollArea(surface)
        self._scroll.setObjectName("historyScroll")
        self._scroll.setWidgetResizable(True)
        self._history_contents = QWidget(self._scroll)
        self._history_contents.setObjectName("historyViewport")
        self._history_layout = QVBoxLayout(self._history_contents)
        self._history_layout.setContentsMargins(10, 10, 10, 10)
        self._history_layout.setSpacing(8)
        self._history_layout.setAlignment(Qt.AlignTop)
        self._scroll.setWidget(self._history_contents)
        layout.addWidget(self._scroll, 1)

        self._show_message(self.tr("OpenMW でNPC会話ウィンドウを開き、取得を押してください。"))
        return surface

    def _build_tts_panel(self) -> QGroupBox:
        group = QGroupBox(self.tr("読み上げ"))
        group.setMinimumWidth(300)
        group.setMaximumWidth(360)
        outer = QVBoxLayout(group)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
        form_widget = QWidget(group)
        form = QFormLayout(form_widget)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(6)
        outer.addWidget(form_widget)

        self._loading_tts_controls = True

        self._auto_speak_check = QCheckBox(self.tr("新しい応答を自動で読み上げる"), group)
        self._auto_speak_check.setChecked(False)
        self._settings.set_tts_enabled(False)
        form.addRow(self.tr("自動読み上げ:"), self._auto_speak_check)

        self._engine_combo = QComboBox(group)
        self._engine_combo.addItem(self.tr("Windows標準 (SAPI5)"), "sapi5")
        self._voicevox_available = voicevox_client.is_available()
        vv_index = self._engine_combo.count()
        self._engine_combo.addItem(
            self.tr("VOICEVOX") if self._voicevox_available else self.tr("VOICEVOX (未検出)"),
            "voicevox",
        )
        if not self._voicevox_available:
            item = self._engine_combo.model().item(vv_index)
            if item is not None:
                item.setEnabled(False)
        saved_engine = self._settings.get_tts_engine()
        visible_engine = saved_engine if self._voicevox_available or saved_engine != "voicevox" else "sapi5"
        engine_index = self._engine_combo.findData(visible_engine)
        self._engine_combo.setCurrentIndex(engine_index if engine_index >= 0 else 0)
        form.addRow(self.tr("エンジン:"), self._engine_combo)

        self._voice_combo = QComboBox(group)
        self._voice_combo.addItem(self.tr("(既定)"), "")
        for desc in TTSService.list_voices(timeout=1.0):
            self._voice_combo.addItem(desc, desc)
        voice_index = self._voice_combo.findData(self._settings.get_tts_voice())
        self._voice_combo.setCurrentIndex(voice_index if voice_index >= 0 else 0)
        self._voice_label = QLabel(self.tr("音声:"))
        form.addRow(self._voice_label, self._voice_combo)

        self._vv_char_combo = QComboBox(group)
        self._vv_char_label = QLabel(self.tr("キャラクター:"))
        form.addRow(self._vv_char_label, self._vv_char_combo)

        vv_style_row = QWidget(group)
        vv_style_layout = QHBoxLayout(vv_style_row)
        vv_style_layout.setContentsMargins(0, 0, 0, 0)
        vv_style_layout.setSpacing(4)
        self._vv_style_combo = QComboBox(vv_style_row)
        vv_style_layout.addWidget(self._vv_style_combo, 1)
        self._vv_reload_btn = QPushButton(self.tr("再読込"), vv_style_row)
        self._vv_reload_btn.clicked.connect(self._on_voicevox_reload_clicked)
        vv_style_layout.addWidget(self._vv_reload_btn)
        self._vv_test_btn = QPushButton(self.tr("テスト"), vv_style_row)
        self._vv_test_btn.clicked.connect(self._on_tts_test_clicked)
        vv_style_layout.addWidget(self._vv_test_btn)
        self._vv_style_row = vv_style_row
        self._vv_style_label = QLabel(self.tr("スタイル:"))
        form.addRow(self._vv_style_label, self._vv_style_row)

        self._load_voicevox_speakers(self._settings.get_tts_vv_speaker())

        self._rate_combo = QComboBox(group)
        for label, value in _TTS_RATE_PRESETS:
            self._rate_combo.addItem(label, value)
        rate_index = self._rate_combo.findData(self._settings.get_tts_rate())
        self._rate_combo.setCurrentIndex(rate_index if rate_index >= 0 else 2)
        form.addRow(self.tr("速度:"), self._rate_combo)

        volume_row = QWidget(group)
        volume_layout = QHBoxLayout(volume_row)
        volume_layout.setContentsMargins(0, 0, 0, 0)
        self._volume_slider = QSlider(Qt.Orientation.Horizontal, volume_row)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(self._settings.get_tts_volume())
        self._volume_label = QLabel(str(self._volume_slider.value()), volume_row)
        self._volume_label.setMinimumWidth(28)
        self._volume_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        volume_layout.addWidget(self._volume_slider, 1)
        volume_layout.addWidget(self._volume_label)
        form.addRow(self.tr("音量:"), volume_row)

        self._interrupt_check = QCheckBox(
            self.tr("新しい読み上げで前の読み上げを止める"),
            group,
        )
        self._interrupt_check.setChecked(self._settings.get_tts_interrupt())
        form.addRow(self.tr("切り上げ:"), self._interrupt_check)

        self._journal_speak_date_check = QCheckBox(self.tr("日付を読み上げる"), group)
        self._journal_speak_date_check.setChecked(self._settings.get_journal_speak_date())
        form.addRow(self.tr("ジャーナル日付:"), self._journal_speak_date_check)

        self._journal_speak_date_title_check = QCheckBox(self.tr("日付別でクエスト名を読む"), group)
        self._journal_speak_date_title_check.setChecked(
            self._settings.get_journal_speak_quest_title_in_date_view()
        )
        form.addRow(self.tr("ジャーナル:"), self._journal_speak_date_title_check)

        outer.addStretch(1)

        self._auto_speak_check.toggled.connect(self._on_tts_control_changed)
        self._engine_combo.currentIndexChanged.connect(self._on_tts_control_changed)
        self._voice_combo.currentIndexChanged.connect(self._on_tts_control_changed)
        self._vv_char_combo.currentIndexChanged.connect(self._on_voicevox_character_changed)
        self._vv_style_combo.currentIndexChanged.connect(self._on_tts_control_changed)
        self._rate_combo.currentIndexChanged.connect(self._on_tts_control_changed)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        self._volume_slider.sliderReleased.connect(lambda: self._apply_tts_settings(save=True))
        self._interrupt_check.toggled.connect(self._on_tts_control_changed)
        self._journal_speak_date_check.toggled.connect(self._on_tts_control_changed)
        self._journal_speak_date_title_check.toggled.connect(self._on_tts_control_changed)

        self._loading_tts_controls = False
        self._update_engine_rows()
        return group

    def _build_npc_status_panel(self) -> QFrame:
        panel = QFrame(self)
        panel.setObjectName("npcStatusPanel")
        panel.setMinimumWidth(270)
        panel.setMaximumWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)

        self._npc_name = QLabel(self.tr("未取得"), panel)
        self._npc_name.setObjectName("npcName")
        self._npc_name.setWordWrap(True)
        layout.addWidget(self._npc_name)

        self._npc_match = QLabel("", panel)
        self._npc_match.setObjectName("npcSubtle")
        self._npc_match.setWordWrap(True)
        layout.addWidget(self._npc_match)

        self._npc_core_values: dict[str, QLabel] = {}
        core_grid = QGridLayout()
        core_grid.setContentsMargins(0, 0, 0, 0)
        core_grid.setHorizontalSpacing(8)
        core_grid.setVerticalSpacing(3)
        for row, (key, label) in enumerate(
            (
                ("id", "ID"),
                ("race", "種族"),
                ("class", "クラス"),
                ("faction", "所属"),
                ("sex", "性別"),
                ("cell", "配置"),
                ("info", "INFO"),
            )
        ):
            key_label = QLabel(label, panel)
            key_label.setObjectName("npcKey")
            value_label = QLabel("-", panel)
            value_label.setObjectName("npcValue")
            value_label.setWordWrap(True)
            core_grid.addWidget(key_label, row, 0, Qt.AlignTop)
            core_grid.addWidget(value_label, row, 1)
            self._npc_core_values[key] = value_label
        layout.addLayout(core_grid)

        layout.addWidget(self._npc_section_label("状態", panel))
        self._npc_status_values: dict[str, QLabel] = {}
        status_grid = QGridLayout()
        status_grid.setContentsMargins(0, 0, 0, 0)
        status_grid.setHorizontalSpacing(8)
        status_grid.setVerticalSpacing(3)
        for row, (key, label) in enumerate(
            (
                ("level", "Lv"),
                ("disposition", "好感"),
                ("reputation", "評判"),
                ("rank", "階級"),
                ("gold", "金"),
                ("ai", "AI"),
            )
        ):
            key_label = QLabel(label, panel)
            key_label.setObjectName("npcKey")
            value_label = QLabel("-", panel)
            value_label.setObjectName("npcValue")
            value_label.setWordWrap(True)
            status_grid.addWidget(key_label, row, 0, Qt.AlignTop)
            status_grid.addWidget(value_label, row, 1)
            self._npc_status_values[key] = value_label
        layout.addLayout(status_grid)

        self._npc_bars: dict[str, QProgressBar] = {}
        for key, label in (("health", "体力"), ("magicka", "マジカ"), ("fatigue", "疲労")):
            row_widget = QWidget(panel)
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            key_label = QLabel(label, row_widget)
            key_label.setObjectName("npcKey")
            key_label.setFixedWidth(44)
            bar = QProgressBar(row_widget)
            bar.setObjectName("npcBar")
            bar.setProperty("stat", key)
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setFormat("-")
            row_layout.addWidget(key_label)
            row_layout.addWidget(bar, 1)
            layout.addWidget(row_widget)
            self._npc_bars[key] = bar

        layout.addWidget(self._npc_section_label("能力値", panel))
        self._npc_attr_values: dict[str, QLabel] = {}
        attr_grid = QGridLayout()
        attr_grid.setContentsMargins(0, 0, 0, 0)
        attr_grid.setHorizontalSpacing(8)
        attr_grid.setVerticalSpacing(3)
        attr_names = ("筋力", "知力", "意志", "敏捷", "速度", "耐久", "魅力", "運")
        for index, name in enumerate(attr_names):
            row = index // 2
            col = (index % 2) * 2
            key_label = QLabel(name, panel)
            key_label.setObjectName("npcKey")
            value_label = QLabel("-", panel)
            value_label.setObjectName("npcValue")
            attr_grid.addWidget(key_label, row, col)
            attr_grid.addWidget(value_label, row, col + 1)
            self._npc_attr_values[name] = value_label
        layout.addLayout(attr_grid)

        layout.addWidget(self._npc_section_label("所持品", panel))
        self._npc_items = QListWidget(panel)
        self._npc_items.setObjectName("npcList")
        self._npc_items.setMaximumHeight(92)
        layout.addWidget(self._npc_items)

        layout.addWidget(self._npc_section_label("呪文", panel))
        self._npc_spells = QListWidget(panel)
        self._npc_spells.setObjectName("npcList")
        self._npc_spells.setMaximumHeight(76)
        layout.addWidget(self._npc_spells)

        layout.addStretch(1)
        self._clear_npc_status()
        return panel

    @staticmethod
    def _npc_section_label(text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("npcSection")
        return label

    def setFont(self, font: QFont) -> None:
        super().setFont(font)
        self._history_contents.setFont(font)
        self._selected_text.setFont(font)
        self._debug.setFont(font)
        if hasattr(self, "_journal_contents"):
            self._journal_contents.setFont(font)
            self._journal_list.setFont(font)
        if hasattr(self, "_book_text"):
            self._book_text.setFont(font)
            self._book_list.setFont(font)
        if hasattr(self, "_free_text"):
            self._free_text.setFont(font)
        if hasattr(self, "_npc_name"):
            self._npc_name.setFont(font)
        self._apply_conversation_font_sizes()
        self._apply_journal_font_sizes()

    @staticmethod
    def _set_widget_font_size(widget: QWidget, point_size: int) -> None:
        font = widget.font()
        font.setPointSize(int(point_size))
        widget.setFont(font)

    def _apply_conversation_label_font(self, label: QLabel) -> None:
        self._set_widget_font_size(label, self._conversation_font_size)

    def _apply_journal_label_font(self, label: QLabel) -> None:
        name = label.objectName()
        if name == "journalDate":
            self._set_widget_font_size(label, self._journal_date_font_size)
        elif name == "journalQuest":
            self._set_widget_font_size(label, self._journal_title_font_size)
        elif name == "journalText":
            self._set_widget_font_size(label, self._journal_body_font_size)
        elif name == "conversationMessage":
            self._set_widget_font_size(label, self._journal_body_font_size)

    def _apply_conversation_font_sizes(self) -> None:
        if not hasattr(self, "_history_contents"):
            return
        for label in self._history_contents.findChildren(QLabel):
            if label.objectName() in {"responseTitle", "responseText", "choiceText", "conversationMessage"}:
                self._apply_conversation_label_font(label)

    def _apply_journal_font_sizes(self) -> None:
        if not hasattr(self, "_journal_contents"):
            return
        for label in self._journal_contents.findChildren(QLabel):
            if label.objectName() in {"journalDate", "journalQuest", "journalText", "conversationMessage"}:
                self._apply_journal_label_font(label)

    def _on_conversation_font_size_changed(self, value: int) -> None:
        self._conversation_font_size = int(value)
        self._conversation_font_value.setText(str(self._conversation_font_size))
        self._settings.set_conversation_font_size(self._conversation_font_size)
        self._apply_conversation_font_sizes()

    def _on_journal_date_font_size_changed(self, value: int) -> None:
        self._journal_date_font_size = int(value)
        self._journal_date_font_value.setText(str(self._journal_date_font_size))
        self._settings.set_journal_date_font_size(self._journal_date_font_size)
        self._apply_journal_font_sizes()

    def _on_journal_title_font_size_changed(self, value: int) -> None:
        self._journal_title_font_size = int(value)
        self._journal_title_font_value.setText(str(self._journal_title_font_size))
        self._settings.set_journal_title_font_size(self._journal_title_font_size)
        self._apply_journal_font_sizes()

    def _on_journal_body_font_size_changed(self, value: int) -> None:
        self._journal_body_font_size = int(value)
        self._journal_body_font_value.setText(str(self._journal_body_font_size))
        self._settings.set_journal_body_font_size(self._journal_body_font_size)
        self._apply_journal_font_sizes()

    def shutdown(self) -> None:
        self._auto_timer.stop()
        self._tts.stop_speaking()
        self._closing = True
        self._fetch_token += 1
        self._fetch_active = False
        self._book_fetch_token += 1
        self._book_fetch_active = False
        self._dictionary_token += 1
        self._dictionary_task_active = False

    def _on_fetch_clicked(self) -> None:
        self._start_fetch(force_rescan=False, show_message=True)

    def _on_rescan_clicked(self) -> None:
        self._start_fetch(force_rescan=True, show_message=True)

    def _start_fetch(self, *, force_rescan: bool, show_message: bool) -> None:
        if self._closing or self._fetch_active:
            return
        self._manual_fetch_active = show_message
        if show_message:
            self._fetch_btn.setEnabled(False)
            self._rescan_btn.setEnabled(False)
            self._status.setText(self.tr("取得中..."))
            if not self._displayed_signature:
                if force_rescan:
                    self._show_message(self.tr("OpenMW の会話履歴を再探索中です。しばらくお待ちください。"))
                    self._set_debug_text(self.tr("キャッシュを使わずに mHistoryContents 候補探索を開始しました。"))
                else:
                    self._show_message(self.tr("OpenMW の会話履歴を取得中です。しばらくお待ちください。"))
                    self._set_debug_text(self.tr("キャッシュ確認と mHistoryContents 候補探索を開始しました。"))

        self._fetch_active = True
        self._fetch_token += 1
        token = self._fetch_token
        thread = threading.Thread(
            target=self._run_fetch_thread,
            args=(token, force_rescan),
            daemon=True,
            name="RTESEditorConversationFetch",
        )
        self._fetch_thread = thread
        thread.start()

    def _run_fetch_thread(self, token: int, force_rescan: bool) -> None:
        try:
            result = self._reader.read_history(force_rescan=force_rescan)
            self._fetch_signals.finished.emit(result, token)
        except Exception:  # noqa: BLE001
            self._fetch_signals.failed.emit(traceback.format_exc(), token)

    def _accept_fetch_result(self, token: int) -> bool:
        if self._closing or token != self._fetch_token:
            return False
        self._fetch_active = False
        self._fetch_thread = None
        return True

    @Slot(object, int)
    def _on_fetch_finished(self, result: OpenMwConversationResult, token: int) -> None:
        if not self._accept_fetch_result(token):
            return
        try:
            self._result = result
            self._restore_fetch_buttons()
            count = len(result.responses)
            choice_count = len(result.choices)
            conversation_active = bool(count or choice_count or result.actor_name.strip())
            if self._conversation_was_active and not conversation_active:
                self._cancel_pending_auto_speak()
                _log_tts_error("AUTO_TTS stop: conversation inactive")
                self._tts.stop_speaking()
            self._conversation_was_active = conversation_active
            previous_actor = self._actor_name.strip().casefold()
            current_actor = result.actor_name.strip().casefold()
            self._update_actor(result.actor_name)
            self._set_debug_text(self._format_diagnostics(result))

            if result.responses or result.choices:
                self._update_history_view(result)
            elif result.actor_name and current_actor and current_actor != previous_actor:
                self._show_message(self.tr("会話履歴を待機中です。"))
            elif not self._displayed_signature:
                if result.process is None:
                    self._show_message(self.tr("OpenMW の実行中プロセスが見つかりません。"))
                else:
                    self._show_message(
                        self.tr("会話履歴候補が見つかりません。NPC会話ウィンドウを開いた状態で再取得してください。")
                    )

            if result.process is None:
                self._status.setText(self.tr("OpenMW 未検出"))
            elif count or choice_count:
                choice_suffix = self.tr(f" / 選択肢 {choice_count}") if choice_count else ""
                actor_suffix = f" / {result.actor_name}" if result.actor_name else ""
                self._status.setText(self.tr(f"PID {result.process.pid}: {count} 件") + choice_suffix + actor_suffix)
            elif result.actor_name:
                self._status.setText(self.tr(f"PID {result.process.pid}: 会話開始") + f" / {result.actor_name}")
            else:
                self._status.setText(self.tr(f"PID {result.process.pid}: 候補なし"))
        finally:
            self._manual_fetch_active = False

    @Slot(str, int)
    def _on_fetch_failed(self, message: str, token: int) -> None:
        if not self._accept_fetch_result(token):
            return
        try:
            self._restore_fetch_buttons()
            self._status.setText(self.tr("取得失敗"))
            if not self._displayed_signature:
                self._show_message(self.tr("取得に失敗しました。OpenMWの状態を確認して再取得してください。"))
            self._set_debug_text(message)
        finally:
            self._manual_fetch_active = False

    def _restore_fetch_buttons(self) -> None:
        if self._manual_fetch_active:
            self._fetch_btn.setEnabled(True)
            self._rescan_btn.setEnabled(True)

    def _on_auto_toggled(self, checked: bool) -> None:
        if checked:
            self._auto_timer.start()
            if not self._fetch_active:
                self._start_fetch(force_rescan=False, show_message=not bool(self._result))
        else:
            self._auto_timer.stop()

    def _on_auto_timeout(self) -> None:
        if not self._fetch_active:
            self._start_fetch(force_rescan=False, show_message=False)

    def _on_poll_interval_changed(self) -> None:
        value = int(self._poll_combo.currentData() or 150)
        self._auto_timer.setInterval(value)
        self._settings.set_conversation_poll_interval_ms(value)

    def _update_history_view(self, result: OpenMwConversationResult) -> None:
        response_signature = self._response_signature(result)
        choice_signature = self._choice_signature(result)
        keyword_signature = self._keyword_signature(result)
        actor_signature = result.actor_name.strip().casefold()
        if (
            response_signature == self._displayed_response_signature
            and choice_signature == self._displayed_choice_signature
            and keyword_signature == self._displayed_keyword_signature
            and actor_signature == self._displayed_actor_signature
        ):
            return

        old_response_signature = self._displayed_response_signature
        append_from = 0
        self._clear_choice_set_widget()
        if actor_signature != self._displayed_actor_signature:
            self._clear_history_widgets()
        elif keyword_signature != self._displayed_keyword_signature:
            self._clear_history_widgets()
        elif old_response_signature and response_signature[: len(old_response_signature)] == old_response_signature:
            append_from = len(old_response_signature)
        else:
            self._clear_history_widgets()

        new_responses = result.responses[append_from:]
        latest_index = len(result.responses) - 1
        for offset, response in enumerate(new_responses, start=append_from):
            choices = result.choices if offset == latest_index else []
            self._add_response_widget(response, choices=choices, keywords=result.keywords)

        if not new_responses and result.choices and self._response_frames:
            self._add_choice_set_widget(self._response_frames[-1], result.choices)

        self._displayed_response_signature = response_signature
        self._displayed_choice_signature = choice_signature
        self._displayed_keyword_signature = keyword_signature
        self._displayed_actor_signature = actor_signature
        self._displayed_signature = self._result_signature(result)
        if result.responses:
            if self._follow_latest_selection and self._response_frames:
                self._select_response(result.responses[-1], self._response_frames[-1], user=False)
            self._auto_speak_new_responses(new_responses, append_from=append_from, keywords=result.keywords)
            QTimer.singleShot(0, self._scroll_to_bottom)

    def _add_response_widget(
        self,
        response: OpenMwDialogueResponse,
        *,
        choices: list[OpenMwDialogueChoice] | None = None,
        keywords: list[str] | None = None,
    ) -> None:
        frame = _ResponseFrame(self._history_contents)
        frame.setObjectName("responseFrame")
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        frame_layout = QHBoxLayout(frame)
        frame_layout.setContentsMargins(10, 8, 8, 8)
        frame_layout.setSpacing(8)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        title = response.title.strip()
        body = response.text.strip()
        if title:
            title_label = QLabel(title, frame)
            title_label.setObjectName("responseTitle")
            title_label.setWordWrap(True)
            title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._apply_conversation_label_font(title_label)
            text_layout.addWidget(title_label)
        if body:
            formatted_body = self._format_dialogue_text(body, keywords or [])
            text_label = QLabel(body, frame)
            text_label.setObjectName("responseText")
            text_label.setTextFormat(Qt.TextFormat.RichText)
            text_label.setText(formatted_body.rich)
            text_label.setWordWrap(True)
            text_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._apply_conversation_label_font(text_label)
            text_layout.addWidget(text_label)
        elif title:
            pass
        else:
            empty_label = QLabel(self.tr("(空の応答)"), frame)
            empty_label.setObjectName("responseText")
            self._apply_conversation_label_font(empty_label)
            text_layout.addWidget(empty_label)

        if choices:
            self._add_choice_set_widget(frame, choices, text_layout)

        frame_layout.addLayout(text_layout, 1)

        speak_btn = QToolButton(frame)
        speak_btn.setObjectName("responseSpeak")
        speak_btn.setText("🔊")
        speak_btn.setToolTip(self.tr("この応答を読み上げ"))
        speak_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        speech_text = self._response_speech_text(response, keywords or [])
        speak_btn.clicked.connect(lambda _checked=False, text=speech_text: self._tts.speak_now(text))
        frame_layout.addWidget(speak_btn, 0, Qt.AlignTop)

        frame.clicked.connect(lambda response=response, frame=frame: self._select_response(response, frame, user=True))
        self._response_frames.append(frame)
        self._history_layout.addWidget(frame)

    def _add_choice_set_widget(
        self,
        response_frame: QFrame,
        choices: list[OpenMwDialogueChoice],
        text_layout: QVBoxLayout | None = None,
    ) -> None:
        if not choices:
            return
        if text_layout is None:
            text_item = response_frame.layout().itemAt(0) if response_frame.layout() is not None else None
            text_layout = text_item.layout() if text_item is not None else None
        if text_layout is None:
            return

        frame = _ResponseFrame(response_frame)
        frame.setObjectName("choiceSetFrame")
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)
        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(3)
        for choice in choices:
            label = QLabel(choice.text.strip(), frame)
            label.setObjectName("choiceText")
            label.setWordWrap(True)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._apply_conversation_label_font(label)
            text_column.addWidget(label)
        layout.addLayout(text_column, 1)

        speak_btn = QToolButton(frame)
        speak_btn.setObjectName("choiceSpeak")
        speak_btn.setText("🔊")
        speak_btn.setToolTip(self.tr("この選択肢をまとめて読み上げ"))
        speak_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        speak_text = self._choices_speech_text(choices)
        speak_btn.clicked.connect(lambda _checked=False, text=speak_text: self._tts.speak_now(text))
        layout.addWidget(speak_btn, 0, Qt.AlignTop)

        frame.clicked.connect(lambda choices=tuple(choices), frame=frame: self._select_choices(list(choices), frame))
        text_layout.addWidget(frame)
        self._choice_set_frame = frame
        self._choice_set_parent_layout = text_layout

    def _auto_speak_new_responses(
        self,
        responses: list[OpenMwDialogueResponse],
        *,
        append_from: int,
        keywords: list[str],
    ) -> None:
        if not self._auto_speak_check.isChecked() or not responses:
            return
        if append_from <= 0 and self._manual_fetch_active:
            return
        queued_count = 0
        for response in responses:
            key = self._auto_speak_key(response)
            if key in self._auto_spoken_keys:
                _log_tts_error(
                    "AUTO_TTS skip duplicate: "
                    f"pid={key[0]} addr=0x{key[1]:X} title_len={len(key[2])} text_len={len(key[3])}"
                )
                continue
            if key in self._auto_speak_pending_keys:
                _log_tts_error(
                    "AUTO_TTS skip pending duplicate: "
                    f"pid={key[0]} addr=0x{key[1]:X} title_len={len(key[2])} text_len={len(key[3])}"
                )
                continue
            speech_text = self._response_speech_text(response, keywords)
            if not speech_text.strip():
                continue
            self._auto_speak_pending_items.append((key, speech_text))
            self._auto_speak_pending_keys.add(key)
            queued_count += 1
        if queued_count:
            _log_tts_error(
                "AUTO_TTS queue: "
                f"new={queued_count} pending={len(self._auto_speak_pending_items)} append_from={append_from}"
            )
            self._schedule_auto_speak_flush()

    def _schedule_auto_speak_flush(self) -> None:
        if self._auto_speak_timer_active:
            return
        self._auto_speak_timer_active = True
        generation = self._auto_speak_generation
        QTimer.singleShot(120, lambda generation=generation: self._flush_auto_speak_pending(generation))

    def _flush_auto_speak_pending(self, generation: int) -> None:
        self._auto_speak_timer_active = False
        if generation != self._auto_speak_generation or not self._auto_speak_check.isChecked():
            _log_tts_error(
                "AUTO_TTS discard pending: "
                f"generation={generation} current={self._auto_speak_generation} "
                f"pending={len(self._auto_speak_pending_items)}"
            )
            self._auto_speak_pending_items.clear()
            self._auto_speak_pending_keys.clear()
            return
        pending = list(self._auto_speak_pending_items)
        self._auto_speak_pending_items.clear()
        self._auto_speak_pending_keys.clear()
        text = "\n\n".join(item for _key, item in pending if item.strip())
        if not text:
            return
        try:
            _log_tts_error(
                "AUTO_TTS fire: "
                f"generation={generation} responses={len(pending)} chars={len(text)}"
            )
            self._tts.speak(text)
            for key, _text in pending:
                self._remember_auto_speak_key(key)
        except Exception as exc:  # noqa: BLE001
            self._auto_speak_check.setChecked(False)
            self._status.setText(self.tr("自動読み上げ停止"))
            self._set_debug_text(f"auto_speak_error: {type(exc).__name__}: {exc}")

    def _cancel_pending_auto_speak(self) -> None:
        self._auto_speak_generation += 1
        self._auto_speak_timer_active = False
        self._auto_speak_pending_items.clear()
        self._auto_speak_pending_keys.clear()

    def _auto_speak_key(self, response: OpenMwDialogueResponse) -> tuple[int, int, str, str]:
        pid = self._result.process.pid if self._result is not None and self._result.process is not None else 0
        return (pid, int(response.address or 0), response.title.strip(), response.text.strip())

    def _remember_auto_speak_key(self, key: tuple[int, int, str, str]) -> None:
        self._auto_spoken_keys.add(key)
        self._auto_spoken_order.append(key)
        while len(self._auto_spoken_order) > 256:
            old = self._auto_spoken_order.pop(0)
            self._auto_spoken_keys.discard(old)

    def _select_response(
        self,
        response: OpenMwDialogueResponse,
        frame: QFrame,
        *,
        user: bool,
    ) -> None:
        if user:
            self._follow_latest_selection = frame is self._response_frames[-1] if self._response_frames else True
        if self._selected_frame is frame:
            self._selected_text.setPlainText(self._response_plain_text(response))
            return
        if self._selected_frame is not None:
            self._selected_frame.setProperty("selected", False)
            self._refresh_widget_style(self._selected_frame)
        self._selected_frame = frame
        frame.setProperty("selected", True)
        self._refresh_widget_style(frame)
        self._selected_text.setPlainText(self._response_plain_text(response))

    def _select_choices(self, choices: list[OpenMwDialogueChoice], frame: QFrame) -> None:
        self._follow_latest_selection = False
        if self._selected_frame is frame:
            self._selected_text.setPlainText(self._choices_plain_text(choices))
            return
        if self._selected_frame is not None:
            self._selected_frame.setProperty("selected", False)
            self._refresh_widget_style(self._selected_frame)
        self._selected_frame = frame
        frame.setProperty("selected", True)
        self._refresh_widget_style(frame)
        self._selected_text.setPlainText(self._choices_plain_text(choices))

    def _select_actor_name(self) -> None:
        actor = self._actor_name.strip()
        if not actor:
            return
        if self._selected_frame is not None:
            self._selected_frame.setProperty("selected", False)
            self._refresh_widget_style(self._selected_frame)
            self._selected_frame = None
        self._follow_latest_selection = False
        self._selected_text.setPlainText(actor)
        self._update_actor_detail_panel(actor, force=True)

    def _copy_selected_text(self) -> None:
        text = self._selected_text.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def _on_reading_tab_changed(self, index: int) -> None:
        if index == 1 and not self._journal_entries:
            self._refresh_journal_entries()
        elif index == 2 and self._current_book_entry is None and not self._book_fetch_active:
            self._start_book_fetch(force_rescan=False)

    def _refresh_journal_entries(self) -> None:
        self._journal_entries = self._collect_journal_entries()
        self._journal_list.clear()
        for entry in self._journal_entries:
            self._journal_list.addItem(entry.title)
        if self._journal_entries:
            self._journal_list.setCurrentRow(0)
            self._status.setText(self.tr(f"ジャーナル {len(self._journal_entries)} 件"))
        else:
            self._show_journal_message(
                self.tr(
                    "ジャーナル項目が見つかりません。"
                    "OpenMW保存データ（.omwsave）が見つからないか、JOURレコードがありません。"
                )
            )
            self._selected_text.clear()
            self._status.setText(self.tr("ジャーナル 0 件"))

    def _on_journal_view_changed(self) -> None:
        if hasattr(self, "_journal_list"):
            self._settings.set_journal_view_mode(self._journal_view_mode())
            self._settings.set_journal_sort_order("desc" if self._journal_sort_descending() else "asc")
            self._refresh_journal_entries()

    def _on_journal_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._journal_entries):
            return
        entry = self._journal_entries[row]
        self._render_journal_entry(entry)
        self._selected_text.setPlainText(entry.text)

    def _speak_selected_journal(self) -> None:
        row = self._journal_list.currentRow()
        if row < 0 or row >= len(self._journal_entries):
            return
        self._tts.speak_now(self._journal_entry_speech_text(self._journal_entries[row]))

    def _clear_journal_widgets(self) -> None:
        if (
            self._selected_frame is not None
            and hasattr(self, "_journal_contents")
            and self._journal_contents.isAncestorOf(self._selected_frame)
        ):
            self._selected_frame = None
        while self._journal_layout.count():
            item = self._journal_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _show_journal_message(self, message: str) -> None:
        self._clear_journal_widgets()
        label = QLabel(message, self._journal_contents)
        label.setObjectName("conversationMessage")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._apply_journal_label_font(label)
        self._journal_layout.addWidget(label)

    def _render_journal_entry(self, entry: _ReadableEntry) -> None:
        self._clear_journal_widgets()
        if not entry.items:
            self._show_journal_message(entry.text or self.tr("表示できるジャーナル本文がありません。"))
            return
        for item in entry.items:
            self._add_journal_item_widget(item)

    def _add_journal_item_widget(self, item: dict[str, str]) -> None:
        frame = _ResponseFrame(self._journal_contents)
        frame.setObjectName("journalEntryFrame")
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(8)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)
        date_text = item.get("date", "").strip()
        if date_text:
            date_label = QLabel(date_text, frame)
            date_label.setObjectName("journalDate")
            date_label.setWordWrap(True)
            date_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._apply_journal_label_font(date_label)
            text_layout.addWidget(date_label)

        title = item.get("title", "").strip()
        if title:
            title_label = QLabel(title, frame)
            title_label.setObjectName("journalQuest")
            title_label.setWordWrap(True)
            title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._apply_journal_label_font(title_label)
            text_layout.addWidget(title_label)

        body = item.get("text", "").strip()
        if body:
            body_label = QLabel(body, frame)
            body_label.setObjectName("journalText")
            body_label.setWordWrap(True)
            body_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._apply_journal_label_font(body_label)
            text_layout.addWidget(body_label)

        layout.addLayout(text_layout, 1)
        speak_btn = QToolButton(frame)
        speak_btn.setObjectName("journalSpeak")
        speak_btn.setText("🔊")
        speak_btn.setToolTip(self.tr("このジャーナル項目を読み上げ"))
        speak_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        speak_btn.clicked.connect(lambda _checked=False, item=item: self._tts.speak_now(self._journal_item_speech_text(item)))
        layout.addWidget(speak_btn, 0, Qt.AlignTop)

        frame.clicked.connect(lambda item=item, frame=frame: self._select_journal_item(item, frame))
        self._journal_layout.addWidget(frame)

    def _select_journal_item(self, item: dict[str, str], frame: QFrame) -> None:
        self._follow_latest_selection = False
        if self._selected_frame is frame:
            self._selected_text.setPlainText(self._journal_item_plain_text(item))
            return
        if self._selected_frame is not None:
            self._selected_frame.setProperty("selected", False)
            self._refresh_widget_style(self._selected_frame)
        self._selected_frame = frame
        frame.setProperty("selected", True)
        self._refresh_widget_style(frame)
        self._selected_text.setPlainText(self._journal_item_plain_text(item))

    def _journal_entry_text(self, items: list[dict[str, str]]) -> str:
        return "\n\n".join(
            self._journal_item_plain_text(item)
            for item in items
            if item.get("date", "").strip() or item.get("title", "").strip() or item.get("text", "").strip()
        )

    def _journal_entry_speech_text(self, entry: _ReadableEntry) -> str:
        if entry.items:
            return "\n\n".join(self._journal_item_speech_text(item) for item in entry.items)
        return entry.text

    @staticmethod
    def _journal_item_plain_text(item: dict[str, str]) -> str:
        return "\n".join(
            part
            for part in (
                item.get("date", "").strip(),
                item.get("title", "").strip(),
                item.get("text", "").strip(),
            )
            if part
        )

    def _journal_item_speech_text(self, item: dict[str, str]) -> str:
        date_text = item.get("date_speech", "").strip() if self._settings.get_journal_speak_date() else ""
        title = item.get("title", "").strip()
        if (
            item.get("view_mode") == "date"
            and item.get("title_only") != "1"
            and not self._settings.get_journal_speak_quest_title_in_date_view()
        ):
            title = ""
        return "\n".join(
            part
            for part in (
                date_text,
                title,
                item.get("text", "").strip(),
            )
            if part
        )

    def _collect_journal_entries(self) -> list[_ReadableEntry]:
        self._journal_source_path = ""
        self._journal_year = self._saved_journal_year()
        records = self._collect_saved_journal_records()

        if not records:
            result = self._journal_reader.read_latest_save()
            if result.source_path is not None:
                self._journal_source_path = str(result.source_path)
            self._journal_year = result.year
            records = self._journal_records_to_dicts(result.records)

        journal_entries = [entry for entry in records if entry["type"] == 0]
        if not journal_entries:
            journal_entries = [entry for entry in records if entry["type"] == 2]
        if self._journal_view_mode() == "quest":
            return self._group_journal_entries_by_quest(
                journal_entries,
                reverse=self._journal_sort_descending(),
            )
        return self._group_journal_entries_by_date(
            journal_entries,
            reverse=self._journal_sort_descending(),
        )

    def _collect_saved_journal_records(self) -> list[dict]:
        if not self._main.manager.mod_files:
            return []
        all_records = self._main.manager.all_records
        raw_entries: list[dict] = []
        order = 0
        for info in all_records.get_info_list("JOUR"):
            for record in info.records:
                entry = self._saved_journal_record_data(record, order)
                order += 1
                if entry is not None:
                    raw_entries.append(entry)
        return raw_entries

    def _journal_view_mode(self) -> str:
        if not hasattr(self, "_journal_mode_combo"):
            return "date"
        return str(self._journal_mode_combo.currentData() or "date")

    def _journal_sort_descending(self) -> bool:
        if not hasattr(self, "_journal_order_combo"):
            return False
        return self._journal_order_combo.currentData() == "desc"

    def _journal_records_to_dicts(self, records: list[OpenMwJournalRecord]) -> list[dict]:
        return [
            {
                "type": record.entry_type,
                "topic": record.topic,
                "info_id": record.info_id,
                "text": record.text,
                "day": record.day,
                "month": record.month,
                "day_of_month": record.day_of_month,
                "order": record.order,
            }
            for record in records
        ]

    def _saved_journal_record_data(self, record, order: int) -> dict | None:
        entry_type = self._field_int32(record, "JETY", default=0)
        if entry_type not in {0, 2}:
            return None
        topic = self._field_save_ref_id(record, "YETO")
        info_id = self._field_save_ref_id(record, "YEIN")
        text = self._field_save_string(record, "TEXT").strip()
        if not text:
            return None
        day = self._field_int32(record, "JEDA", default=0)
        month = self._field_int32(record, "JEMO", default=0)
        day_of_month = self._field_int32(record, "JEDM", default=0)
        return {
            "type": entry_type,
            "topic": topic,
            "info_id": info_id,
            "text": text,
            "day": day,
            "month": month,
            "day_of_month": day_of_month,
            "order": order,
        }

    def _group_journal_entries_by_quest(self, records: list[dict], *, reverse: bool) -> list[_ReadableEntry]:
        grouped: dict[str, list[dict]] = {}
        topic_order: dict[str, int] = {}
        for record in records:
            key = (record["topic"] or "").strip().casefold()
            if not key:
                key = f"__journal_{record['order']}"
            grouped.setdefault(key, []).append(record)
            topic_order.setdefault(key, record["order"])

        entries: list[_ReadableEntry] = []
        for key, items in grouped.items():
            items.sort(key=lambda item: item["order"])
            topic = items[0]["topic"]
            info_id = items[0].get("info_id", "")
            title = (
                self._journal_topic_title(topic, info_id, str(items[0].get("text", "") or ""))
                or topic
                or self.tr("ジャーナル")
            )
            journal_items = [
                {
                    "date": "",
                    "date_speech": "",
                    "date_speech_year": "",
                    "title": title,
                    "text": "",
                    "topic": topic,
                    "info_id": str(info_id or ""),
                    "view_mode": "quest",
                    "title_only": "1",
                }
            ]
            journal_items.extend(self._journal_record_item(item, include_title=False, view_mode="quest") for item in items)
            entries.append(
                _ReadableEntry(
                    title=title,
                    text=self._journal_entry_text(journal_items),
                    key=topic,
                    items=tuple(journal_items),
                )
            )

        entries.sort(
            key=lambda entry: topic_order.get((entry.key or "").strip().casefold(), 0),
            reverse=reverse,
        )
        return entries

    def _group_journal_entries_by_date(self, records: list[dict], *, reverse: bool) -> list[_ReadableEntry]:
        grouped: dict[tuple[int, int, int], list[dict]] = {}
        for record in records:
            key = (
                int(record.get("day", 0) or 0),
                int(record.get("month", 0) or 0),
                int(record.get("day_of_month", 0) or 0),
            )
            grouped.setdefault(key, []).append(record)

        entries_with_order: list[tuple[int, _ReadableEntry]] = []
        for key, items in grouped.items():
            items.sort(key=lambda item: item["order"])
            day, month, day_of_month = key
            title = self._journal_date_text(day, month, day_of_month) or self.tr("日付なし")
            journal_items = []
            for item in items:
                journal_items.append(self._journal_record_item(item, include_title=True, view_mode="date"))
            entries_with_order.append(
                (
                    items[0]["order"],
                    _ReadableEntry(
                        title=title,
                        text=self._journal_entry_text(journal_items),
                        key=title,
                        items=tuple(journal_items),
                    ),
                )
            )

        entries_with_order.sort(key=lambda item: item[0], reverse=reverse)
        return [entry for _order, entry in entries_with_order]

    def _on_current_book_clicked(self) -> None:
        self._start_book_fetch(force_rescan=False)

    def _start_book_fetch(self, *, force_rescan: bool) -> None:
        if self._closing or self._book_fetch_active:
            return
        self._book_fetch_token += 1
        token = self._book_fetch_token
        self._book_fetch_active = True
        self._current_book_btn.setEnabled(False)
        self._status.setText(self.tr("表示中の本を取得中..."))
        thread = threading.Thread(
            target=self._run_book_fetch_thread,
            args=(token, force_rescan),
            daemon=True,
            name="RTESEditorBookFetch",
        )
        self._book_fetch_thread = thread
        thread.start()

    def _run_book_fetch_thread(self, token: int, force_rescan: bool) -> None:
        try:
            result = self._book_reader.read_current_book(force_rescan=force_rescan)
            self._book_fetch_signals.finished.emit(result, token)
        except Exception:  # noqa: BLE001
            self._book_fetch_signals.failed.emit(traceback.format_exc(), token)

    def _accept_book_fetch_result(self, token: int) -> bool:
        if self._closing or token != self._book_fetch_token:
            return False
        self._book_fetch_active = False
        self._book_fetch_thread = None
        self._current_book_btn.setEnabled(True)
        return True

    @Slot(object, int)
    def _on_book_fetch_finished(self, result: OpenMwDisplayedBookResult, token: int) -> None:
        if not self._accept_book_fetch_result(token):
            return
        self._set_debug_text(self._format_book_diagnostics(result))
        if result.book is None:
            self._status.setText(self.tr("表示中の本なし"))
            return

        self._current_book_entry = self._entry_from_openmw_book(result)
        self._add_book_history_entry(self._current_book_entry)
        self._show_current_book_entry()
        title = self._current_book_entry.title
        self._status.setText(self.tr("表示中の本: ") + title)

    @Slot(str, int)
    def _on_book_fetch_failed(self, message: str, token: int) -> None:
        if not self._accept_book_fetch_result(token):
            return
        self._status.setText(self.tr("本の取得失敗"))
        self._set_debug_text(message)

    def _add_book_history_entry(self, entry: _ReadableEntry) -> None:
        row = -1
        for index, existing in enumerate(self._book_history_entries):
            if existing.key == entry.key:
                row = index
                break
        if row >= 0:
            self._book_history_entries[row] = entry
        else:
            self._book_history_entries.append(entry)
        if not self._book_show_all:
            self._refresh_book_list(select_key=entry.key)

    def _on_book_show_all_toggled(self, checked: bool) -> None:
        self._book_show_all = bool(checked)
        self._refresh_book_list(select_key=self._current_book_entry.key if self._current_book_entry else "")

    def _refresh_book_list(self, *, select_key: str = "") -> None:
        if not hasattr(self, "_book_list"):
            return
        entries = self._all_book_entries() if self._book_show_all else self._filtered_book_history_entries()
        current_key = select_key or (self._current_book_entry.key if self._current_book_entry else "")
        self._book_list.blockSignals(True)
        self._book_list.clear()
        self._book_entries = entries
        for entry in entries:
            self._book_list.addItem(entry.title)
        target_row = -1
        if current_key:
            for index, entry in enumerate(entries):
                if entry.key == current_key:
                    target_row = index
                    break
        if target_row < 0 and entries:
            target_row = 0
        self._book_list.setCurrentRow(target_row)
        self._book_list.blockSignals(False)
        if 0 <= target_row < len(entries):
            self._current_book_entry = entries[target_row]
            self._show_current_book_entry()

    def _filtered_book_history_entries(self) -> list[_ReadableEntry]:
        terms = self._book_search_terms()
        if not terms:
            return list(self._book_history_entries)
        return [
            entry
            for entry in self._book_history_entries
            if self._book_entry_matches(entry, terms)
        ]

    def _all_book_entries(self) -> list[_ReadableEntry]:
        terms = self._book_search_terms()
        entries: list[_ReadableEntry] = []
        for info in self._main.manager.all_records.get_info_list("BOOK"):
            if terms and not self._book_record_info_matches(info, terms):
                continue
            entry = self._entry_from_book_record_info(info)
            if entry is not None:
                entries.append(entry)
        entries.sort(key=lambda item: (item.title.casefold(), item.key.casefold()))
        return entries

    def _book_search_terms(self) -> list[str]:
        if not hasattr(self, "_book_search_box"):
            return []
        return [term.casefold() for term in self._book_search_box.text().split() if term.strip()]

    @staticmethod
    def _book_entry_matches(entry: _ReadableEntry, terms: list[str]) -> bool:
        blob = "\n".join((entry.key, entry.title, entry.text, entry.raw_text)).casefold()
        return all(term in blob for term in terms)

    def _book_record_info_matches(self, info, terms: list[str]) -> bool:
        records = list(info.records)
        for term in terms:
            if not any(term in self._book_record_search_text(record).casefold() for record in records):
                return False
        return True

    def _book_record_search_text(self, record) -> str:
        enc = self._record_encoding(record)
        return "\n".join(
            self._field_str(record, field_name, enc)
            for field_name in ("NAME", "FNAM", "TEXT")
        )

    def _entry_from_book_record_info(self, info) -> _ReadableEntry | None:
        record = info.main_record
        if record is None:
            return None
        enc = self._record_encoding(record)
        book_id = self._field_str(record, "NAME", enc).strip() or info.key
        title = self._field_str(record, "FNAM", enc).strip() or book_id or self.tr("無題")
        raw_text = self._field_str(record, "TEXT", enc)
        text = self._plain_book_text(raw_text)
        if text:
            body = text if text.startswith(title) else f"{title}\n\n{text}"
        else:
            body = title
        return _ReadableEntry(
            title=title,
            text=body,
            key=f"book:{book_id.casefold()}",
            raw_text=raw_text,
        )

    def _on_book_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._book_entries):
            return
        self._current_book_entry = self._book_entries[row]
        self._show_current_book_entry()

    def _on_book_font_size_changed(self, value: int) -> None:
        self._book_font_size = int(value)
        self._book_font_value.setText(str(self._book_font_size))
        self._settings.set_book_font_size(self._book_font_size)
        self._show_current_book_entry()

    def _on_book_line_height_changed(self, value: int) -> None:
        self._book_line_height_percent = int(value)
        self._book_line_value.setText(f"{self._book_line_height_percent}%")
        self._settings.set_book_line_height_percent(self._book_line_height_percent)
        self._show_current_book_entry()

    def _show_current_book_entry(self) -> None:
        entry = self._current_book_entry
        if entry is None:
            return
        self._refresh_book_start_combo(entry)
        if self._book_current_segment_index >= 0:
            self._book_text.setHtml(self._book_highlight_html(entry))
            anchor = f"bookseg{self._book_current_segment_index}"
            if hasattr(self._book_text, "scrollToAnchor"):
                QTimer.singleShot(0, lambda anchor=anchor: self._book_text.scrollToAnchor(anchor))
        elif entry.raw_text:
            self._book_text.setHtml(self._book_rich_text(entry.title, entry.raw_text, entry.text))
        elif entry.rich_text:
            self._book_text.setHtml(entry.rich_text)
        else:
            self._book_text.setPlainText(entry.text)
        self._selected_text.setPlainText(entry.text)

    def _speak_selected_book(self) -> None:
        entry = self._current_book_entry
        if entry is None:
            return
        start = int(self._book_start_combo.currentData() or 0) if hasattr(self, "_book_start_combo") else 0
        segments = self._book_speech_segments_for_entry(entry, start)
        if not any(str(segment.get("text", "")).strip() for segment in segments):
            return
        self._book_speech_segments = segments
        self._book_current_segment_index = -1
        self._book_speech_token += 1
        token = self._book_speech_token
        self._show_current_book_entry()
        self._tts.speak_segments_now(
            tuple(str(segment.get("text", "")) for segment in segments),
            on_segment_start=lambda index, _text, token=token: self._book_speech_signals.segment_started.emit(
                token,
                index,
            ),
            on_finished=lambda ok, token=token: self._book_speech_signals.finished.emit(token, ok),
        )

    def _stop_book_speech(self) -> None:
        self._book_speech_token += 1
        self._book_current_segment_index = -1
        self._tts.stop_speaking()
        self._show_current_book_entry()

    def _on_book_speech_segment_started(self, token: int, index: int) -> None:
        if token != self._book_speech_token:
            return
        if index < 0 or index >= len(self._book_speech_segments):
            return
        self._book_current_segment_index = index
        self._show_current_book_entry()

    def _on_book_speech_finished(self, token: int, _ok: bool) -> None:
        if token != self._book_speech_token:
            return
        self._book_current_segment_index = -1
        self._show_current_book_entry()

    def _refresh_book_start_combo(self, entry: _ReadableEntry) -> None:
        if not hasattr(self, "_book_start_combo"):
            return
        paragraphs = self._book_entry_paragraphs(entry)
        current = self._book_start_combo.currentData()
        self._book_start_combo.blockSignals(True)
        self._book_start_combo.clear()
        for index, paragraph in enumerate(paragraphs):
            snippet = re.sub(r"\s+", " ", paragraph).strip()
            if not snippet:
                continue
            label = snippet[:36] + ("..." if len(snippet) > 36 else "")
            self._book_start_combo.addItem(label, index)
        restore_index = self._book_start_combo.findData(current)
        self._book_start_combo.setCurrentIndex(restore_index if restore_index >= 0 else 0)
        self._book_start_combo.blockSignals(False)

    @staticmethod
    def _book_entry_paragraphs(entry: _ReadableEntry) -> list[str]:
        value = (entry.text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not value:
            return []
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", value) if part.strip()]
        return paragraphs or [value]

    @staticmethod
    def _book_paragraph_segments(paragraph: str) -> list[str]:
        value = re.sub(r"[ \t]*\n[ \t]*", " ", paragraph or "").strip()
        return [segment for segment in TTSService._split_voicevox_text(value) if segment]

    def _book_speech_segments_for_entry(self, entry: _ReadableEntry, start_paragraph: int) -> list[dict[str, object]]:
        paragraphs = self._book_entry_paragraphs(entry)
        start = max(0, min(start_paragraph, max(0, len(paragraphs) - 1)))
        segments: list[dict[str, object]] = []
        for paragraph_index in range(start, len(paragraphs)):
            for part_index, text in enumerate(self._book_paragraph_segments(paragraphs[paragraph_index])):
                segments.append(
                    {
                        "text": text,
                        "paragraph": paragraph_index,
                        "part": part_index,
                    }
                )
            if paragraph_index < len(paragraphs) - 1:
                segments.append({"text": "", "paragraph": paragraph_index, "part": -1})
        return segments

    def _book_highlight_html(self, entry: _ReadableEntry) -> str:
        paragraphs = self._book_entry_paragraphs(entry)
        current = (
            self._book_speech_segments[self._book_current_segment_index]
            if 0 <= self._book_current_segment_index < len(self._book_speech_segments)
            else {}
        )
        paragraph_html: list[str] = []
        for paragraph_index, paragraph in enumerate(paragraphs):
            pieces: list[str] = []
            for part_index, segment in enumerate(self._book_paragraph_segments(paragraph)):
                active = (
                    current.get("paragraph") == paragraph_index
                    and current.get("part") == part_index
                )
                pieces.append(self._book_highlight_span(segment, active, self._book_current_segment_index))
            if not pieces:
                pieces.append("&nbsp;")
            paragraph_html.append(f'<p class="book-paragraph">{"".join(pieces)}</p>')
        body = "\n".join(paragraph_html)
        return (
            "<html><head><style>"
            "body{font-family:'Yu Gothic UI','Meiryo',sans-serif;"
            f"font-size:{self._book_font_size}px;"
            f"line-height:{self._book_line_height_percent}%;"
            "color:#f1e3b5;background:#17120a;margin:0;}"
            ".book-paragraph{margin:0 0 0.85em 0;padding:0;}"
            ".book-current-text{color:#ffd34a;font-weight:700;}"
            "</style></head><body>"
            f"{body}"
            "</body></html>"
        )

    def _book_highlight_span(self, text: str, active: bool, segment_index: int) -> str:
        escaped = html.escape(text)
        if not active:
            return escaped
        class_name = "book-current-text"
        return f'<a name="bookseg{segment_index}"></a><span class="{class_name}">{escaped}</span>'

    def _speak_free_text(self) -> None:
        text = self._free_plain_text if self._free_current_segment_index >= 0 else self._free_text.toPlainText()
        self._free_plain_text = text
        entry = _ReadableEntry(title=self.tr("自由"), text=text)
        segments = self._book_speech_segments_for_entry(entry, 0)
        if not any(str(segment.get("text", "")).strip() for segment in segments):
            return
        self._free_speech_segments = segments
        self._free_current_segment_index = -1
        self._free_speech_token += 1
        token = self._free_speech_token
        self._show_free_text()
        self._tts.speak_segments_now(
            tuple(str(segment.get("text", "")) for segment in segments),
            on_segment_start=lambda index, _text, token=token: self._free_speech_signals.segment_started.emit(
                token,
                index,
            ),
            on_finished=lambda ok, token=token: self._free_speech_signals.finished.emit(token, ok),
        )

    def _stop_free_speech(self) -> None:
        self._free_speech_token += 1
        self._free_current_segment_index = -1
        self._tts.stop_speaking()
        self._show_free_text()

    def _on_free_speech_segment_started(self, token: int, index: int) -> None:
        if token != self._free_speech_token:
            return
        if index < 0 or index >= len(self._free_speech_segments):
            return
        self._free_current_segment_index = index
        self._show_free_text()

    def _on_free_speech_finished(self, token: int, _ok: bool) -> None:
        if token != self._free_speech_token:
            return
        self._free_current_segment_index = -1
        self._show_free_text()

    def _show_free_text(self) -> None:
        if not hasattr(self, "_free_text"):
            return
        if self._free_current_segment_index >= 0:
            self._free_text.setHtml(self._free_highlight_html())
            anchor = f"freeseg{self._free_current_segment_index}"
            if hasattr(self._free_text, "scrollToAnchor"):
                QTimer.singleShot(0, lambda anchor=anchor: self._free_text.scrollToAnchor(anchor))
        else:
            self._free_text.setPlainText(self._free_plain_text)

    def _free_highlight_html(self) -> str:
        entry = _ReadableEntry(title=self.tr("自由"), text=self._free_plain_text)
        paragraphs = self._book_entry_paragraphs(entry)
        current = (
            self._free_speech_segments[self._free_current_segment_index]
            if 0 <= self._free_current_segment_index < len(self._free_speech_segments)
            else {}
        )
        paragraph_html: list[str] = []
        for paragraph_index, paragraph in enumerate(paragraphs):
            pieces: list[str] = []
            for part_index, segment in enumerate(self._book_paragraph_segments(paragraph)):
                active = (
                    current.get("paragraph") == paragraph_index
                    and current.get("part") == part_index
                )
                escaped = html.escape(segment)
                if active:
                    escaped = (
                        f'<a name="freeseg{self._free_current_segment_index}"></a>'
                        f'<span class="book-current-text">{escaped}</span>'
                    )
                pieces.append(escaped)
            paragraph_html.append(f'<p class="book-paragraph">{"".join(pieces) or "&nbsp;"}</p>')
        return (
            "<html><head><style>"
            "body{font-family:'Yu Gothic UI','Meiryo',sans-serif;"
            f"font-size:{self._book_font_size}px;"
            f"line-height:{self._book_line_height_percent}%;"
            "color:#f1e3b5;background:#17120a;margin:0;}"
            ".book-paragraph{margin:0 0 0.85em 0;padding:0;}"
            ".book-current-text{color:#ffd34a;font-weight:700;}"
            "</style></head><body>"
            f"{''.join(paragraph_html)}"
            "</body></html>"
        )

    def _populate_dictionary_table(self, entries: list[dict]) -> None:
        if not hasattr(self, "_dict_table"):
            return
        self._loading_dictionary_table = True
        try:
            self._dict_table.setRowCount(0)
            for entry in entries:
                self._append_dictionary_row(entry)
        finally:
            self._loading_dictionary_table = False
        self._set_dictionary_status(self.tr("{0}件").format(self._dict_table.rowCount()))

    def _append_dictionary_row(self, entry: dict | None = None) -> None:
        entry = self._normalize_dictionary_entry(entry or {})
        row = self._dict_table.rowCount()
        self._dict_table.insertRow(row)
        self._dict_table.setItem(row, 0, QTableWidgetItem(entry["surface"]))
        self._dict_table.setItem(row, 1, QTableWidgetItem(entry["pronunciation"]))

        accent_spin = QSpinBox(self._dict_table)
        accent_spin.setRange(0, 99)
        accent_spin.setValue(int(entry["accent_type"]))
        accent_spin.valueChanged.connect(self._on_dictionary_table_changed)
        self._dict_table.setCellWidget(row, 2, accent_spin)

        type_combo = QComboBox(self._dict_table)
        for label, value in _VOICEVOX_WORD_TYPES:
            type_combo.addItem(label, value)
        type_index = type_combo.findData(entry["word_type"])
        type_combo.setCurrentIndex(type_index if type_index >= 0 else 0)
        type_combo.currentIndexChanged.connect(self._on_dictionary_table_changed)
        self._dict_table.setCellWidget(row, 3, type_combo)

        priority_spin = QSpinBox(self._dict_table)
        priority_spin.setRange(0, 10)
        priority_spin.setValue(int(entry["priority"]))
        priority_spin.valueChanged.connect(self._on_dictionary_table_changed)
        self._dict_table.setCellWidget(row, 4, priority_spin)

        uuid_item = QTableWidgetItem(entry["voicevox_uuid"])
        uuid_item.setFlags(uuid_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._dict_table.setItem(row, 5, uuid_item)

    def _add_dictionary_entry(self) -> None:
        self._loading_dictionary_table = True
        try:
            self._append_dictionary_row()
        finally:
            self._loading_dictionary_table = False
        self._dict_table.setCurrentCell(self._dict_table.rowCount() - 1, 0)
        self._set_dictionary_status(self.tr("行を追加しました。"))

    def _delete_selected_dictionary_entries(self) -> None:
        rows = sorted({index.row() for index in self._dict_table.selectedIndexes()}, reverse=True)
        if not rows:
            self._set_dictionary_status(self.tr("削除する行を選択してください。"))
            return
        for row in rows:
            self._dict_table.removeRow(row)
        self._save_dictionary_from_table()
        self._set_dictionary_status(self.tr("{0}行削除しました。").format(len(rows)))

    def _save_dictionary_from_table(self) -> None:
        entries, _errors = self._dictionary_entries_from_table(require_complete=False)
        self._settings.set_tts_voicevox_dictionary(entries)
        self._set_dictionary_status(self.tr("保存しました。"))

    def _on_dictionary_table_changed(self, *_args) -> None:
        if self._loading_dictionary_table:
            return
        entries, _errors = self._dictionary_entries_from_table(require_complete=False)
        self._settings.set_tts_voicevox_dictionary(entries)
        self._set_dictionary_status(self.tr("変更を保存しました。"))

    def _fetch_voicevox_dictionary(self) -> None:
        if self._dictionary_task_active:
            return
        self._dictionary_token += 1
        token = self._dictionary_token
        self._dictionary_task_active = True
        self._set_dictionary_buttons_enabled(False)
        self._set_dictionary_status(self.tr("VOICEVOXから取得中..."))

        def worker() -> None:
            try:
                entries = voicevox_client.get_user_dict_words(timeout=5.0)
                self._dictionary_signals.fetched.emit(entries, "", token)
            except Exception as exc:  # noqa: BLE001
                self._dictionary_signals.fetched.emit([], str(exc), token)

        self._dictionary_thread = threading.Thread(target=worker, daemon=True)
        self._dictionary_thread.start()

    @Slot(object, str, int)
    def _on_voicevox_dictionary_fetched(self, entries: object, error: str, token: int) -> None:
        if token != self._dictionary_token:
            return
        self._dictionary_task_active = False
        self._set_dictionary_buttons_enabled(True)
        if error:
            self._set_dictionary_status(self.tr("取得失敗: {0}").format(error))
            return
        normalized = [
            self._normalize_dictionary_entry(entry)
            for entry in entries
            if isinstance(entry, dict)
        ]
        self._settings.set_tts_voicevox_dictionary(normalized)
        self._populate_dictionary_table(normalized)
        self._set_dictionary_status(self.tr("VOICEVOXから{0}件取得しました。").format(len(normalized)))

    def _register_voicevox_dictionary(self) -> None:
        if self._dictionary_task_active:
            return
        entries, errors = self._dictionary_entries_from_table(require_complete=True)
        if errors:
            self._set_dictionary_status(errors[0])
            return
        if not entries:
            self._set_dictionary_status(self.tr("登録する項目がありません。"))
            return
        self._settings.set_tts_voicevox_dictionary(entries)
        self._dictionary_token += 1
        token = self._dictionary_token
        self._dictionary_task_active = True
        self._set_dictionary_buttons_enabled(False)
        self._set_dictionary_status(self.tr("VOICEVOXへ登録中..."))

        def worker() -> None:
            registered = [dict(entry) for entry in entries]
            try:
                for updated in registered:
                    word_uuid = str(updated.get("voicevox_uuid", "") or "").strip()
                    if word_uuid:
                        voicevox_client.rewrite_user_dict_word(
                            word_uuid,
                            updated["surface"],
                            updated["pronunciation"],
                            int(updated["accent_type"]),
                            word_type=updated["word_type"],
                            priority=int(updated["priority"]),
                            timeout=5.0,
                        )
                    else:
                        word_uuid = voicevox_client.add_user_dict_word(
                            updated["surface"],
                            updated["pronunciation"],
                            int(updated["accent_type"]),
                            word_type=updated["word_type"],
                            priority=int(updated["priority"]),
                            timeout=5.0,
                        )
                        updated["voicevox_uuid"] = word_uuid
                self._dictionary_signals.registered.emit(registered, "", token)
            except Exception as exc:  # noqa: BLE001
                self._dictionary_signals.registered.emit(registered, str(exc), token)

        self._dictionary_thread = threading.Thread(target=worker, daemon=True)
        self._dictionary_thread.start()

    @Slot(object, str, int)
    def _on_voicevox_dictionary_registered(self, entries: object, error: str, token: int) -> None:
        if token != self._dictionary_token:
            return
        self._dictionary_task_active = False
        self._set_dictionary_buttons_enabled(True)
        registered = [entry for entry in entries if isinstance(entry, dict)]
        if registered:
            self._settings.set_tts_voicevox_dictionary(registered)
            self._populate_dictionary_table(registered)
        if error:
            self._set_dictionary_status(self.tr("登録失敗: {0}").format(error))
            return
        self._set_dictionary_status(self.tr("VOICEVOXへ{0}件登録しました。").format(len(registered)))

    def _dictionary_entries_from_table(self, *, require_complete: bool) -> tuple[list[dict], list[str]]:
        entries: list[dict] = []
        errors: list[str] = []
        for row in range(self._dict_table.rowCount()):
            entry = self._dictionary_entry_from_row(row)
            if not entry["surface"] and not entry["pronunciation"]:
                continue
            if require_complete and (not entry["surface"] or not entry["pronunciation"]):
                errors.append(self.tr("{0}行目は単語とよみの両方が必要です。").format(row + 1))
                continue
            entries.append(entry)
        return entries, errors

    def _dictionary_entry_from_row(self, row: int) -> dict:
        surface_item = self._dict_table.item(row, 0)
        pronunciation_item = self._dict_table.item(row, 1)
        uuid_item = self._dict_table.item(row, 5)
        accent_widget = self._dict_table.cellWidget(row, 2)
        type_widget = self._dict_table.cellWidget(row, 3)
        priority_widget = self._dict_table.cellWidget(row, 4)
        accent_type = accent_widget.value() if isinstance(accent_widget, QSpinBox) else 1
        word_type = type_widget.currentData() if isinstance(type_widget, QComboBox) else "PROPER_NOUN"
        priority = priority_widget.value() if isinstance(priority_widget, QSpinBox) else 5
        return self._normalize_dictionary_entry(
            {
                "surface": surface_item.text() if surface_item is not None else "",
                "pronunciation": (
                    self._normalize_dictionary_pronunciation(pronunciation_item.text())
                    if pronunciation_item is not None
                    else ""
                ),
                "accent_type": accent_type,
                "word_type": word_type,
                "priority": priority,
                "voicevox_uuid": uuid_item.text() if uuid_item is not None else "",
            }
        )

    @staticmethod
    def _normalize_dictionary_entry(entry: dict) -> dict:
        surface = str(entry.get("surface", "") or "").strip()
        pronunciation = ConversationPanel._normalize_dictionary_pronunciation(
            str(entry.get("pronunciation", "") or "")
        )
        try:
            accent_type = int(entry.get("accent_type", 1) or 1)
        except (TypeError, ValueError):
            accent_type = 1
        try:
            priority = int(entry.get("priority", 5) or 5)
        except (TypeError, ValueError):
            priority = 5
        word_type = str(entry.get("word_type", "PROPER_NOUN") or "PROPER_NOUN")
        if word_type not in {value for _label, value in _VOICEVOX_WORD_TYPES}:
            word_type = "PROPER_NOUN"
        return {
            "surface": surface,
            "pronunciation": pronunciation,
            "accent_type": max(0, min(99, accent_type)),
            "word_type": word_type,
            "priority": max(0, min(10, priority)),
            "voicevox_uuid": str(entry.get("voicevox_uuid", "") or "").strip(),
        }

    @staticmethod
    def _normalize_dictionary_pronunciation(value: str) -> str:
        text = str(value or "").strip()
        return "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in text)

    def _set_dictionary_status(self, message: str) -> None:
        if hasattr(self, "_dict_status"):
            self._dict_status.setText(message)

    def _set_dictionary_buttons_enabled(self, enabled: bool) -> None:
        for attr in (
            "_dict_add_btn",
            "_dict_delete_btn",
            "_dict_save_btn",
            "_dict_fetch_btn",
            "_dict_register_btn",
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def _entry_from_openmw_book(self, result: OpenMwDisplayedBookResult) -> _ReadableEntry:
        assert result.book is not None
        title = result.book.title.strip() or self.tr("無題")
        text = self._plain_book_text(result.book.text)
        if text:
            body = text if text.startswith(title) else f"{title}\n\n{text}"
        else:
            body = title
        key = f"book:0x{result.book.book_record:X}" if result.book.book_record else f"{title}\n{text}"
        return _ReadableEntry(title=title, text=body, key=key, raw_text=result.book.text)

    @staticmethod
    def _record_encoding(record) -> TesEncoding:
        return record.mod_file.encoding if record.mod_file else TesEncoding.CP1252

    def _journal_record_item(
        self,
        record: dict,
        *,
        include_title: bool,
        view_mode: str,
    ) -> dict[str, str]:
        topic = str(record.get("topic", "") or "")
        info_id = str(record.get("info_id", "") or "")
        text = str(record.get("text", "") or "").strip()
        title = self._journal_topic_title(topic, info_id, text) or topic or self.tr("ジャーナル")
        day = int(record.get("day", 0) or 0)
        month = int(record.get("month", 0) or 0)
        day_of_month = int(record.get("day_of_month", 0) or 0)
        return {
            "date": self._journal_date_text(day, month, day_of_month),
            "date_speech": self._journal_date_speech_text(day, month, day_of_month),
            "date_speech_year": "",
            "title": title if include_title else "",
            "text": text,
            "topic": topic,
            "info_id": info_id,
            "view_mode": view_mode,
            "title_only": "0",
        }

    def _journal_date_text(self, day: int, month: int, day_of_month: int) -> str:
        if day_of_month <= 0:
            return ""
        month_name = self._journal_month_name(month)
        date_text = f"{month_name} {day_of_month}日"
        if day > 0:
            date_text += f" ({day}日目)"
        modern_date = self._journal_modern_date_text(month, day_of_month, self._journal_year)
        if modern_date:
            date_text += f" [{modern_date}]"
        return date_text

    def _journal_date_speech_text(self, day: int, month: int, day_of_month: int) -> str:
        if day_of_month <= 0:
            return ""
        text = f"{self._journal_month_name(month)} {day_of_month}日"
        if day > 0:
            text += f" ({day}日目)"
        return text

    def _journal_month_name(self, month: int) -> str:
        if 0 <= month < len(_JOURNAL_MONTH_GMST_IDS):
            gmst = self._gmst_string(_JOURNAL_MONTH_GMST_IDS[month])
            if gmst:
                return gmst
            return _JOURNAL_MONTH_FALLBACK_NAMES[month]
        return self.tr("不明な月")

    @staticmethod
    def _journal_modern_date_text(month: int, day_of_month: int, year: int | None = None) -> str:
        if day_of_month <= 0 or month < 0:
            return ""
        value = f"{month + 1:02d}/{day_of_month:02d}"
        if year is not None and year > 0:
            value = f"{year}/{value}"
        return value

    def _journal_year_from_date(self, day: int, month: int, day_of_month: int) -> int | None:
        return self._journal_year

    def _saved_journal_year(self) -> int | None:
        if not self._main.manager.mod_files:
            return None
        info = self._main.manager.all_records.find_record_info("GLOB", "Year")
        if info is None or info.main_record is None:
            return None
        value = self._field_float32(info.main_record, "FLTV")
        if value is None or value <= 0:
            return None
        return int(round(value))

    def _gmst_string(self, gmst_id: str) -> str:
        info = self._main.manager.all_records.find_record_info("GMST", gmst_id)
        if info is None or info.main_record is None:
            return ""
        record = info.main_record
        enc = self._record_encoding(record)
        return self._field_str(record, "STRV", enc)

    def _journal_topic_title(self, topic: str, info_id: str = "", entry_text: str = "") -> str:
        value = (topic or "").strip()
        info_value = (info_id or "").strip()
        if not value and not info_value and not (entry_text or "").strip():
            return ""
        all_records = self._main.manager.all_records
        candidates: list[str] = []
        if value:
            candidates.append(value)
        if info_value and hasattr(all_records, "get_dial_key_for_info"):
            dial_key = all_records.get_dial_key_for_info(info_value)
            if dial_key and dial_key not in candidates:
                candidates.append(dial_key)

        fallback = ""
        for candidate in candidates:
            title = self._journal_quest_name_for_dial(candidate)
            if title:
                return title
            title = self._dial_display_name(candidate)
            if not title:
                continue
            if candidate.casefold() != title.casefold():
                return title
            if not fallback:
                fallback = title
        response_dial = self._journal_dial_key_for_response_text(entry_text)
        if response_dial and response_dial not in candidates:
            title = self._journal_quest_name_for_dial(response_dial)
            if title:
                return title
            title = self._dial_display_name(response_dial)
            if title:
                return title
        return fallback or value

    def _dial_display_name(self, dial_key: str) -> str:
        info = self._main.manager.all_records.get_dial_record_info_with_aliases(dial_key)
        if info is None or info.main_record is None:
            return ""
        record = info.main_record
        enc = self._record_encoding(record)
        return self._field_str(record, "NAME", enc)

    def _journal_quest_name_for_dial(self, dial_key: str) -> str:
        value = (dial_key or "").strip()
        if not value:
            return ""
        all_records = self._main.manager.all_records
        if not hasattr(all_records, "get_dial_children"):
            return ""
        for child in all_records.get_dial_children(value):
            record = child.main_record
            if record is None or "QSTN" not in record.fields_map:
                continue
            enc = self._record_encoding(record)
            title = self._field_str(record, "NAME", enc).strip()
            if title:
                return title
        return ""

    def _journal_dial_key_for_response_text(self, text: str) -> str:
        key = self._normalize_journal_response_text(text)
        if not key or not self._main.manager.mod_files:
            return ""
        mod_count = len(self._main.manager.mod_files)
        if self._journal_response_dial_cache_mod_count != mod_count:
            self._build_journal_response_dial_cache()
        return self._journal_response_dial_cache.get(key, "")

    def _build_journal_response_dial_cache(self) -> None:
        self._journal_response_dial_cache = {}
        self._journal_response_dial_cache_mod_count = len(self._main.manager.mod_files)
        all_records = self._main.manager.all_records
        for info in all_records.get_info_list("INFO"):
            record = info.main_record
            if record is None or record.parent_group is None:
                continue
            enc = self._record_encoding(record)
            response = self._normalize_journal_response_text(self._field_str(record, "NAME", enc))
            if not response:
                continue
            dial_key = record.parent_group.label
            if hasattr(all_records, "get_canonical_dial_key"):
                dial_key = all_records.get_canonical_dial_key(dial_key)
            self._journal_response_dial_cache.setdefault(response, dial_key)

    @staticmethod
    def _normalize_journal_response_text(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip())

    @staticmethod
    def _field_int32(record, field_type: str, *, default: int = 0) -> int:
        field = record.fields_map.get(field_type)
        raw = field.data.raw() if field else b""
        if len(raw) < 4:
            return default
        try:
            return struct.unpack_from("<i", raw, 0)[0]
        except struct.error:
            return default

    @staticmethod
    def _field_float32(record, field_type: str) -> float | None:
        field = record.fields_map.get(field_type)
        raw = field.data.raw() if field else b""
        if len(raw) < 4:
            return None
        try:
            return struct.unpack_from("<f", raw, 0)[0]
        except struct.error:
            return None

    @staticmethod
    def _field_save_ref_id(record, field_type: str) -> str:
        field = record.fields_map.get(field_type)
        if field is None:
            return ""
        raw = field.data.raw().rstrip(b"\x00")
        if raw and raw[0] < 0x20:
            raw = raw[1:]
        return ConversationPanel._decode_save_string(raw, record)

    @staticmethod
    def _field_save_string(record, field_type: str) -> str:
        field = record.fields_map.get(field_type)
        if field is None:
            return ""
        raw = field.data.raw().rstrip(b"\x00")
        return ConversationPanel._decode_save_string(raw, record)

    @staticmethod
    def _decode_save_string(raw: bytes, record) -> str:
        if not raw:
            return ""
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            enc = record.mod_file.encoding if record.mod_file else TesEncoding.CP1252
            return raw.decode(enc.value, errors="replace")

    def _book_rich_text(self, title: str, raw_text: str, plain_body: str) -> str:
        source = raw_text or ""
        has_markup = bool(re.search(r"(?i)</?[a-z][^>]*>", source))
        if has_markup:
            body = ConversationPanel._normalize_openmw_book_markup(source)
        else:
            body = self._book_plain_to_html(source)
        if not body.strip():
            body = html.escape(plain_body).replace("\n", "<br>")
        return (
            "<html><head><style>"
            "body{font-family:'Yu Gothic UI','Meiryo',sans-serif;"
            f"font-size:{self._book_font_size}px;"
            f"line-height:{self._book_line_height_percent}%;"
            "color:#f1e3b5;background:#17120a;margin:0;}"
            "div,p{margin:0;padding:0;}"
            "a{color:#80a8ff;}"
            "</style></head><body>"
            f"{body}"
            "</body></html>"
        )

    def _book_plain_to_html(self, text: str) -> str:
        value = html.escape(text or "").replace("\r\n", "\n").replace("\r", "\n")
        paragraph_break = self._book_paragraph_break()
        value = re.sub(r"\n{2,}", paragraph_break, value)
        return value.replace("\n", "<br>")

    @staticmethod
    def _normalize_openmw_book_markup(text: str) -> str:
        value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        value = value.replace("[pagebreak]\n", "<hr>")
        paragraph_break = ConversationPanel._book_paragraph_break()
        paragraph_marker = "__RTES_BOOK_PARAGRAPH_BREAK__"
        value = re.sub(r"(?i)<\s*br\s*/?\s*>", "<br>", value)
        value = re.sub(r"(?i)<\s*/\s*p\s*>", "", value)
        value = re.sub(r"(?i)<\s*p(?:\s+[^>]*)?\s*>", paragraph_marker, value)
        value = re.sub(r"(?i)(?:<br>\s*){2,}", paragraph_marker, value)
        value = re.sub(r"\n{2,}", paragraph_marker, value)
        value = value.replace(paragraph_marker, paragraph_break)
        value = re.sub(
            r"(?is)<\s*div([^>]*)>",
            lambda match: ConversationPanel._book_div_open_tag(match.group(1)),
            value,
        )
        value = re.sub(r"(?i)<\s*/\s*div\s*>", "</div>", value)
        value = re.sub(
            r"(?is)<\s*font([^>]*)>",
            lambda match: ConversationPanel._book_font_open_tag(match.group(1)),
            value,
        )
        value = re.sub(r"(?i)<\s*/\s*font\s*>", "</span>", value)
        return value.strip()

    @staticmethod
    def _book_paragraph_break() -> str:
        return "<br><br>"

    @staticmethod
    def _book_div_open_tag(attrs: str) -> str:
        align = ConversationPanel._book_tag_attr(attrs, "align").casefold()
        if align not in {"center", "right", "left"}:
            align = "left"
        return f'<div style="text-align:{align}; margin:0; padding:0;">'

    @staticmethod
    def _book_font_open_tag(attrs: str) -> str:
        styles: list[str] = []
        color = ConversationPanel._book_tag_attr(attrs, "color")
        if color:
            color = color.strip()
            if not color.startswith("#"):
                color = f"#{color}"
            styles.append(f"color:{html.escape(color)}")
        face = ConversationPanel._book_tag_attr(attrs, "face")
        if face:
            styles.append(f"font-family:{html.escape(face)}")
        if not styles:
            return "<span>"
        return f'<span style="{";".join(styles)}">'

    @staticmethod
    def _book_tag_attr(attrs: str, name: str) -> str:
        pattern = rf"""(?i)\b{re.escape(name)}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))"""
        match = re.search(pattern, attrs or "")
        if not match:
            return ""
        return next((group for group in match.groups() if group is not None), "")

    @staticmethod
    def _plain_book_text(text: str) -> str:
        value = html.unescape(text or "")
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        value = re.sub(r"(?i)<br\s*/?>", "\n", value)
        value = re.sub(r"(?i)</p\s*>", "\n\n", value)
        value = re.sub(r"<[^>]+>", "", value)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    def _response_plain_text(self, response: OpenMwDialogueResponse) -> str:
        title = response.title.strip()
        keywords = self._result.keywords if self._result is not None else []
        text = self._format_dialogue_text(response.text.strip(), keywords).plain
        if title and text:
            return f"{title}\n{text}"
        return title or text

    def _response_speech_text(self, response: OpenMwDialogueResponse, keywords: list[str]) -> str:
        title = response.title.strip()
        text = self._format_dialogue_text(response.text.strip(), keywords).plain.strip()
        if title and text:
            if title[-1] not in "。.!?！？、，;；:：）)]】」』":
                title = f"{title}。"
            return f"{title}\n\n{text}"
        return title or text

    def _format_dialogue_text(self, text: str, keywords: list[str]) -> _FormattedDialogueText:
        segments = self._parse_openmw_hyperlinks(text)
        if keywords:
            segments = self._mark_keyword_links(segments, keywords)
        plain = "".join(part for part, _is_link in segments)
        rich_parts = []
        for part, is_link in segments:
            escaped = html.escape(part).replace("\n", "<br>")
            if is_link and keywords:
                rich_parts.append(f'<span style="color:#2b65c8; font-weight:600;">{escaped}</span>')
            else:
                rich_parts.append(escaped)
        return _FormattedDialogueText(plain=plain, rich="".join(rich_parts))

    @staticmethod
    def _parse_openmw_hyperlinks(text: str) -> list[tuple[str, bool]]:
        segments: list[tuple[str, bool]] = []
        cursor = 0
        while cursor < len(text):
            start = text.find("@", cursor)
            if start < 0:
                segments.append((text[cursor:], False))
                break
            end = text.find("#", start + 1)
            eq = text.find("=", start + 1)
            if end < 0 or (eq >= 0 and eq > end):
                segments.append((text[cursor:], False))
                break
            if start > cursor:
                segments.append((text[cursor:start], False))
            link_text = text[start + 1:eq if eq >= 0 else end]
            if link_text:
                segments.append((link_text, True))
            cursor = end + 1
        return [(part, is_link) for part, is_link in segments if part]

    @staticmethod
    def _mark_keyword_links(
        segments: list[tuple[str, bool]],
        keywords: list[str],
    ) -> list[tuple[str, bool]]:
        clean_keywords = sorted(
            {keyword.strip() for keyword in keywords if len(keyword.strip()) >= 2},
            key=len,
            reverse=True,
        )
        if not clean_keywords:
            return segments

        marked: list[tuple[str, bool]] = []
        for text, is_link in segments:
            if is_link or not text:
                marked.append((text, is_link))
                continue
            mask = [False] * len(text)
            for keyword in clean_keywords:
                start = 0
                while True:
                    pos = text.find(keyword, start)
                    if pos < 0:
                        break
                    end = pos + len(keyword)
                    if not any(mask[pos:end]):
                        for idx in range(pos, end):
                            mask[idx] = True
                    start = pos + 1
            chunk_start = 0
            current = mask[0] if mask else False
            for idx, value in enumerate(mask):
                if value == current:
                    continue
                marked.append((text[chunk_start:idx], current))
                chunk_start = idx
                current = value
            if chunk_start < len(text):
                marked.append((text[chunk_start:], current))
        return [(part, is_link) for part, is_link in marked if part]

    @staticmethod
    def _choices_plain_text(choices: list[OpenMwDialogueChoice]) -> str:
        lines = []
        for choice in choices:
            text = choice.text.strip()
            if text:
                lines.append(text)
        return "\n".join(lines)

    @staticmethod
    def _choices_speech_text(choices: list[OpenMwDialogueChoice]) -> str:
        lines = []
        for choice in choices:
            text = choice.text.strip()
            if text:
                lines.append(text)
        return "\n\n".join(lines)

    def _update_actor_detail_panel(self, actor_name: str, *, force: bool = False) -> None:
        if not hasattr(self, "_npc_name"):
            return
        actor = actor_name.strip()
        mod_count = len(self._main.manager.mod_files)
        key = actor.casefold()
        if not force and key == self._npc_detail_actor_key and mod_count == self._npc_detail_mod_count:
            return
        self._npc_detail_actor_key = key
        self._npc_detail_mod_count = mod_count
        self._clear_npc_status(actor or self.tr("未取得"))
        if not actor or not self._main.manager.mod_files:
            return

        matches = self._find_npc_infos_by_display_name(actor)
        if not matches:
            self._npc_match.setText(self.tr("読み込み済みESP/ESMに一致するNPC_がありません"))
            return

        info = matches[0]
        record = info.main_record
        if record is None:
            self._npc_match.setText(self.tr("NPC_レコードを取得できません"))
            return

        enc = record.mod_file.encoding if record.mod_file else TesEncoding.CP1252
        npc_id = self._field_str(record, "NAME", enc) or info.key
        display_name = self._field_str(record, "FNAM", enc) or actor
        attrs = self._main.manager.all_records.get_npc_attributes(npc_id) or {}
        cells = self._main.manager.all_records.get_npc_cells(npc_id)
        info_count = len(self._main.manager.all_records.get_infos_by_actor(npc_id))
        stats = self._npc_static_stats(record)
        ai_values = self._npc_ai_values(record)

        self._npc_name.setText(display_name)
        if len(matches) > 1:
            self._npc_match.setText(self.tr(f"候補 {len(matches)} 件。先頭候補を表示中"))
        elif display_name != actor:
            self._npc_match.setText(actor)
        else:
            self._npc_match.setText("")

        self._set_npc_core("id", npc_id)
        self._set_npc_core("race", self._resolve_object_display_name(attrs.get("rnam", ""), ("RACE",)))
        self._set_npc_core("class", self._resolve_object_display_name(attrs.get("cnam", ""), ("CLAS",)))
        self._set_npc_core("faction", self._resolve_object_display_name(attrs.get("faction", ""), ("FACT",)))
        if "is_female" in attrs:
            self._set_npc_core("sex", self.tr("女性") if attrs.get("is_female") else self.tr("男性"))
        self._set_npc_core("cell", " / ".join(cells[:3]))
        self._set_npc_core("info", f"{info_count} 件" if info_count else "")

        self._set_npc_status("level", stats.get("level"))
        self._set_npc_status("disposition", stats.get("disposition"))
        self._set_npc_status("reputation", stats.get("reputation"))
        self._set_npc_status("rank", stats.get("rank"))
        self._set_npc_status("gold", stats.get("gold"))
        if ai_values:
            self._set_npc_status(
                "ai",
                " / ".join(
                    f"{label} {ai_values[key]}"
                    for key, label in (("hello", "Hello"), ("fight", "Fight"), ("flee", "Flee"), ("alarm", "Alarm"))
                ),
            )

        self._set_npc_bar("health", stats.get("health"))
        self._set_npc_bar("magicka", stats.get("magicka"))
        self._set_npc_bar("fatigue", stats.get("fatigue"))

        attributes = stats.get("attributes")
        if isinstance(attributes, dict):
            for name, label in self._npc_attr_values.items():
                self._set_label_value(label, attributes.get(name))

        self._fill_npc_list(self._npc_items, self._npc_carried_items(record, enc))
        self._fill_npc_list(self._npc_spells, self._npc_spells_for_record(record, enc))

    def _clear_npc_status(self, actor_name: str | None = None) -> None:
        if not hasattr(self, "_npc_name"):
            return
        self._npc_name.setText(actor_name or self.tr("未取得"))
        self._npc_match.setText("")
        for label in self._npc_core_values.values():
            label.setText("-")
        for label in self._npc_status_values.values():
            label.setText("-")
        for label in self._npc_attr_values.values():
            label.setText("-")
        for bar in self._npc_bars.values():
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setFormat("-")
        self._fill_npc_list(self._npc_items, [])
        self._fill_npc_list(self._npc_spells, [])

    def _set_npc_core(self, key: str, value) -> None:
        label = self._npc_core_values.get(key)
        if label is not None:
            self._set_label_value(label, value)

    def _set_npc_status(self, key: str, value) -> None:
        label = self._npc_status_values.get(key)
        if label is not None:
            self._set_label_value(label, value)

    @staticmethod
    def _set_label_value(label: QLabel, value) -> None:
        text = "" if value is None else str(value).strip()
        label.setText(text or "-")

    def _set_npc_bar(self, key: str, value) -> None:
        bar = self._npc_bars.get(key)
        if bar is None:
            return
        try:
            amount = int(value)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setFormat("-")
            return
        bar.setRange(0, amount)
        bar.setValue(amount)
        bar.setFormat(str(amount))

    @staticmethod
    def _fill_npc_list(widget: QListWidget, values: list[str]) -> None:
        widget.clear()
        if not values:
            widget.addItem("-")
            return
        for value in values[:24]:
            widget.addItem(value)
        if len(values) > 24:
            widget.addItem(f"ほか {len(values) - 24} 件")

    def _find_npc_infos_by_display_name(self, actor_name: str):
        target = actor_name.strip().casefold()
        if not target:
            return []
        if not self._main.manager.mod_files:
            return []
        indexed = self._main.manager.all_records.find_npc_infos_by_name_or_id(actor_name)
        if indexed:
            return indexed
        matches = []
        for info in self._main.manager.all_records.get_info_list("NPC_"):
            record = info.main_record
            if record is None:
                continue
            enc = record.mod_file.encoding if record.mod_file else TesEncoding.CP1252
            npc_id = self._field_str(record, "NAME", enc)
            display_name = self._field_str(record, "FNAM", enc)
            if display_name.strip().casefold() == target or npc_id.strip().casefold() == target:
                matches.append(info)
        return matches

    @staticmethod
    def _npc_static_stats(record) -> dict[str, object]:
        field = record.fields_map.get("NPDT")
        if field is None:
            return {}
        raw = field.data.raw()
        try:
            if len(raw) >= 52:
                level = struct.unpack_from("<H", raw, 0)[0]
                attrs = list(struct.unpack_from("<8B", raw, 2))
                health, magicka, fatigue = struct.unpack_from("<HHH", raw, 38)
                disposition, reputation, rank = struct.unpack_from("<BBB", raw, 44)
                gold = struct.unpack_from("<I", raw, 48)[0]
                attr_names = ("筋力", "知力", "意志", "敏捷", "速度", "耐久", "魅力", "運")
                return {
                    "level": level,
                    "attributes": dict(zip(attr_names, attrs, strict=False)),
                    "health": health,
                    "magicka": magicka,
                    "fatigue": fatigue,
                    "disposition": disposition,
                    "reputation": reputation,
                    "rank": rank,
                    "gold": gold,
                }
            if len(raw) >= 9:
                level = struct.unpack_from("<H", raw, 0)[0]
                disposition, reputation, rank = struct.unpack_from("<BBB", raw, 2)
                gold = raw[8]
                return {
                    "level": level,
                    "disposition": disposition,
                    "reputation": reputation,
                    "rank": rank,
                    "gold": gold,
                }
        except struct.error:
            return {}
        return {}

    def _npc_carried_items(self, record, enc: TesEncoding) -> list[str]:
        items: list[str] = []
        for field in record.fields:
            if field.field_type != "NPCO":
                continue
            raw = field.data.raw()
            if len(raw) < 36:
                continue
            try:
                count = struct.unpack_from("<i", raw, 0)[0]
            except struct.error:
                continue
            if count <= 0:
                continue
            item_id = raw[4:36].rstrip(b"\x00").decode(enc.value, errors="replace").strip()
            if item_id:
                item_name = self._resolve_object_display_name(item_id, _ITEM_RECORD_TYPES)
                items.append(f"{item_name} x{count}")
        return items

    def _npc_spells_for_record(self, record, enc: TesEncoding) -> list[str]:
        spells: list[str] = []
        for field in record.fields:
            if field.field_type != "NPCS":
                continue
            spell_id = field.data.raw().rstrip(b"\x00").decode(enc.value, errors="replace").strip()
            if spell_id:
                spells.append(self._resolve_object_display_name(spell_id, ("SPEL",)))
        return spells

    def _resolve_object_display_name(self, object_id, record_types: tuple[str, ...]) -> str:
        value = "" if object_id is None else str(object_id).strip()
        if not value:
            return ""
        all_records = self._main.manager.all_records
        for record_type in record_types:
            info = all_records.find_record_info(record_type, value)
            if info is None or info.main_record is None:
                continue
            record = info.main_record
            enc = record.mod_file.encoding if record.mod_file else TesEncoding.CP1252
            display_name = self._field_str(record, "FNAM", enc)
            if display_name:
                return display_name
            name = self._field_str(record, "NAME", enc)
            if name:
                return name
        return value

    @staticmethod
    def _npc_ai_values(record) -> dict[str, int]:
        field = record.fields_map.get("AIDT")
        if field is None:
            return {}
        raw = field.data.raw()
        if len(raw) < 5:
            return {}
        return {
            "hello": raw[0],
            "fight": raw[2],
            "flee": raw[3],
            "alarm": raw[4],
        }

    @staticmethod
    def _field_str(record, field_type: str, enc: TesEncoding) -> str:
        field = record.fields_map.get(field_type)
        if field is None:
            return ""
        return field.to_display_str(enc).strip()

    @staticmethod
    def _refresh_widget_style(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _show_message(self, message: str) -> None:
        if not self._displayed_signature and self._message_widget is not None and self._message_text == message:
            return
        self._clear_history_widgets()
        if hasattr(self, "_selected_text"):
            self._selected_text.clear()
        label = QLabel(message, self._history_contents)
        label.setObjectName("conversationMessage")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._apply_conversation_label_font(label)
        self._history_layout.addWidget(label)
        self._message_widget = label
        self._message_text = message
        self._displayed_signature = ()
        self._displayed_response_signature = ()
        self._displayed_choice_signature = ()
        self._displayed_keyword_signature = ()
        self._displayed_actor_signature = ""

    def _clear_history_widgets(self) -> None:
        while self._history_layout.count():
            item = self._history_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._message_widget = None
        self._message_text = ""
        self._response_frames.clear()
        self._choice_set_frame = None
        self._choice_set_parent_layout = None
        self._selected_frame = None
        self._follow_latest_selection = True
        self._displayed_keyword_signature = ()

    def _clear_choice_set_widget(self) -> None:
        frame = self._choice_set_frame
        if frame is None:
            return
        if self._selected_frame is frame:
            self._selected_frame = None
            self._selected_text.clear()
        if self._choice_set_parent_layout is not None:
            self._choice_set_parent_layout.removeWidget(frame)
        frame.deleteLater()
        self._choice_set_frame = None
        self._choice_set_parent_layout = None

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    @staticmethod
    def _result_signature(result: OpenMwConversationResult) -> tuple[tuple[str, str, str], ...]:
        responses = tuple(("R", r.title, r.text) for r in result.responses)
        choices = tuple(("C", c.text, str(c.choice_id)) for c in result.choices)
        keywords = tuple(("K", keyword, "") for keyword in result.keywords)
        return responses + choices + keywords

    @staticmethod
    def _response_signature(result: OpenMwConversationResult) -> tuple[tuple[str, str], ...]:
        return tuple((r.title, r.text) for r in result.responses)

    @staticmethod
    def _choice_signature(result: OpenMwConversationResult) -> tuple[tuple[str, int], ...]:
        return tuple((c.text, c.choice_id) for c in result.choices)

    @staticmethod
    def _keyword_signature(result: OpenMwConversationResult) -> tuple[str, ...]:
        return tuple(result.keywords)

    def _update_actor(self, actor_name: str) -> None:
        actor = actor_name.strip() or self.tr("未特定")
        self._actor_name = actor_name.strip()
        if self._actor_label.text() != actor:
            self._actor_label.setText(actor)
        if actor_name.strip():
            self._actor_label.setToolTip("")
        else:
            self._actor_label.setToolTip(
                self.tr("NPC名を OpenMW の会話ウィンドウ情報から取得できませんでした。")
            )
        surface_actor = actor if actor_name.strip() else self.tr("会話")
        if self._surface_actor.text() != surface_actor:
            self._surface_actor.setText(surface_actor)
        self._update_actor_detail_panel(actor_name)

    def _set_debug_text(self, text: str) -> None:
        if self._debug.toPlainText() == text:
            return
        bar = self._debug.verticalScrollBar()
        old_value = bar.value()
        old_max = bar.maximum()
        was_at_bottom = old_value >= max(0, old_max - 2)
        should_restore = self._debug.isVisible()
        self._debug.setPlainText(text)
        if not should_restore:
            return

        def restore_scroll() -> None:
            if was_at_bottom:
                bar.setValue(bar.maximum())
            else:
                bar.setValue(min(old_value, bar.maximum()))

        QTimer.singleShot(0, restore_scroll)

    def _load_voicevox_speakers(self, select_id: int | None = None) -> None:
        self._voicevox_speakers = []
        if self._voicevox_available:
            self._voicevox_speakers = voicevox_client.list_speakers(timeout=1.0)

        self._vv_char_combo.blockSignals(True)
        self._vv_style_combo.blockSignals(True)
        self._vv_char_combo.clear()
        self._vv_style_combo.clear()
        for speaker in self._voicevox_speakers:
            self._vv_char_combo.addItem(speaker.get("name", ""), speaker)

        init_char = 0
        if select_id is not None:
            for index, speaker in enumerate(self._voicevox_speakers):
                styles = speaker.get("styles", [])
                if any(int(style.get("id", -1)) == int(select_id) for style in styles):
                    init_char = index
                    break
        if self._voicevox_speakers:
            self._vv_char_combo.setCurrentIndex(init_char)
            self._fill_voicevox_styles(init_char, select_id)
        self._vv_char_combo.blockSignals(False)
        self._vv_style_combo.blockSignals(False)

    def _fill_voicevox_styles(self, char_index: int, select_id: int | None = None) -> None:
        self._vv_style_combo.blockSignals(True)
        self._vv_style_combo.clear()
        if 0 <= char_index < len(self._voicevox_speakers):
            for style in self._voicevox_speakers[char_index].get("styles", []):
                try:
                    self._vv_style_combo.addItem(style.get("name", ""), int(style["id"]))
                except (KeyError, TypeError, ValueError):
                    continue
        if select_id is not None:
            index = self._vv_style_combo.findData(int(select_id))
            if index >= 0:
                self._vv_style_combo.setCurrentIndex(index)
        self._vv_style_combo.blockSignals(False)

    def _on_voicevox_character_changed(self, index: int) -> None:
        self._fill_voicevox_styles(index)
        self._apply_tts_settings(save=True)

    def _on_voicevox_reload_clicked(self) -> None:
        self._voicevox_available = voicevox_client.is_available()
        vv_index = self._engine_combo.findData("voicevox")
        if vv_index >= 0:
            self._engine_combo.setItemText(
                vv_index,
                self.tr("VOICEVOX") if self._voicevox_available else self.tr("VOICEVOX (未検出)"),
            )
            item = self._engine_combo.model().item(vv_index)
            if item is not None:
                item.setEnabled(self._voicevox_available)
        self._load_voicevox_speakers(self._settings.get_tts_vv_speaker())
        if self._voicevox_available and self._settings.get_tts_engine() == "voicevox":
            self._engine_combo.setCurrentIndex(vv_index)
        elif not self._voicevox_available and self._engine_combo.currentData() == "voicevox":
            self._engine_combo.setCurrentIndex(0)
        self._update_engine_rows()
        self._apply_tts_settings(save=False)

    def _on_tts_test_clicked(self) -> None:
        self._apply_tts_settings(save=True)
        selected = self._selected_text.toPlainText().strip()
        latest = (
            self._response_speech_text(self._result.latest, self._result.keywords)
            if self._result and self._result.latest
            else ""
        )
        self._tts.speak_now(selected or latest or self.tr("これは読み上げのテストです。"))

    def _on_tts_control_changed(self) -> None:
        if self._loading_tts_controls:
            return
        if hasattr(self, "_auto_speak_check") and not self._auto_speak_check.isChecked():
            self._cancel_pending_auto_speak()
        self._update_engine_rows()
        self._apply_tts_settings(save=True)

    def _on_volume_changed(self, value: int) -> None:
        self._volume_label.setText(str(value))
        if not self._loading_tts_controls:
            self._apply_tts_settings(save=False)

    def _update_engine_rows(self) -> None:
        is_voicevox = self._engine_combo.currentData() == "voicevox"
        self._voice_label.setVisible(not is_voicevox)
        self._voice_combo.setVisible(not is_voicevox)
        self._vv_char_label.setVisible(is_voicevox)
        self._vv_char_combo.setVisible(is_voicevox)
        self._vv_style_label.setVisible(is_voicevox)
        self._vv_style_row.setVisible(is_voicevox)

    def _selected_voicevox_speaker(self) -> int:
        data = self._vv_style_combo.currentData()
        if data is None:
            return self._settings.get_tts_vv_speaker()
        try:
            return int(data)
        except (TypeError, ValueError):
            return 0

    def _apply_tts_settings(self, *, save: bool) -> None:
        enabled = self._auto_speak_check.isChecked()
        engine = self._engine_combo.currentData() or "sapi5"
        voice = self._voice_combo.currentData() or ""
        vv_speaker = self._selected_voicevox_speaker()
        rate = int(self._rate_combo.currentData() or 0)
        volume = int(self._volume_slider.value())
        interrupt = self._interrupt_check.isChecked()

        self._tts.set_enabled(enabled)
        self._tts.set_engine(engine)
        self._tts.set_voice(voice)
        self._tts.set_vv_speaker(vv_speaker)
        self._tts.set_rate(rate)
        self._tts.set_volume(volume)
        self._tts.set_interrupt(interrupt)

        if save:
            self._settings.set_tts_enabled(enabled)
            self._settings.set_tts_engine(engine)
            self._settings.set_tts_voice(voice)
            self._settings.set_tts_vv_speaker(vv_speaker)
            self._settings.set_tts_rate(rate)
            self._settings.set_tts_volume(volume)
            self._settings.set_tts_interrupt(interrupt)
            if hasattr(self, "_journal_speak_date_check"):
                self._settings.set_journal_speak_date(self._journal_speak_date_check.isChecked())
            if hasattr(self, "_journal_speak_date_title_check"):
                self._settings.set_journal_speak_quest_title_in_date_view(
                    self._journal_speak_date_title_check.isChecked()
                )

    def _format_diagnostics(self, result: OpenMwConversationResult) -> str:
        lines: list[str] = []
        process = result.process
        if process is not None:
            lines.append(f"process: {process.name}  pid={process.pid}")
            if process.path:
                lines.append(f"path: {process.path}")
        else:
            lines.append("process: not found")
        lines.append(f"actor_name: {result.actor_name or '(not resolved)'}")

        latest = result.latest
        if latest is not None:
            lines.append(f"latest: index={latest.index} addr=0x{latest.address:X}")
            if latest.title:
                lines.append(f"latest_title: {latest.title}")
        diag = result.diagnostics
        keys = [
            "status",
            "direct_dialogue_window",
            "direct_history_vector",
            "region_count",
            "object_scan_mb",
            "array_scan_mb",
            "response_objects",
            "array_candidates",
            "best_score",
            "history_array",
            "history_vector",
            "cache_array",
            "cache_vector",
            "choices_vector",
            "choice_count",
            "keyword_count",
            "actor_name_status",
            "dialogue_window",
            "actor_ref",
            "actor_cell",
            "actor_name_address",
            "actor_name_score",
            "elapsed_ms",
            "read_failures",
            "object_scan_limited",
            "array_scan_limited",
        ]
        for key in keys:
            if key in diag:
                lines.append(f"{key}: {diag[key]}")

        process_results = diag.get("process_results")
        if isinstance(process_results, list) and len(process_results) > 1:
            lines.append("")
            lines.append("process_results:")
            for item in process_results:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  "
                    f"pid={item.get('pid')} "
                    f"status={item.get('status')} "
                    f"responses={item.get('response_objects')} "
                    f"arrays={item.get('array_candidates')} "
                    f"score={item.get('best_score')} "
                    f"elapsed_ms={item.get('elapsed_ms')}"
                )
        return "\n".join(lines)

    def _format_book_diagnostics(self, result: OpenMwDisplayedBookResult) -> str:
        lines: list[str] = []
        process = result.process
        if process is not None:
            lines.append(f"book_process: {process.name}  pid={process.pid}")
            if process.path:
                lines.append(f"book_path: {process.path}")
        else:
            lines.append("book_process: not found")
        if result.book is not None:
            lines.append(f"book_title: {result.book.title}")
            lines.append(f"book_text_len: {len(result.book.text)}")
        diag = result.diagnostics
        for key in (
            "status",
            "window_candidates",
            "window_base",
            "ptr_offset",
            "book_record",
            "live_ref",
            "visible_state",
            "elapsed_ms",
            "region_count",
            "array_scan_mb",
            "read_failures",
            "array_scan_limited",
        ):
            if key in diag:
                lines.append(f"{key}: {diag[key]}")
        process_results = diag.get("process_results")
        if isinstance(process_results, list) and len(process_results) > 1:
            lines.append("")
            lines.append("book_process_results:")
            for item in process_results:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  "
                    f"pid={item.get('pid')} "
                    f"status={item.get('status')} "
                    f"title={item.get('title', '')} "
                    f"elapsed_ms={item.get('elapsed_ms')}"
                )
        return "\n".join(lines)


__all__ = ["ConversationPanel"]
