from __future__ import annotations

import bisect
import ctypes
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from ctypes import wintypes


_OPENMW_EXE_NAME = "openmw.exe"

_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_VM_READ = 0x0010

_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_MEM_COMMIT = 0x1000
_MEM_PRIVATE = 0x20000
_MEM_MAPPED = 0x40000
_MEM_IMAGE = 0x1000000

_PAGE_NOACCESS = 0x01
_PAGE_READONLY = 0x02
_PAGE_READWRITE = 0x04
_PAGE_WRITECOPY = 0x08
_PAGE_EXECUTE_READ = 0x20
_PAGE_EXECUTE_READWRITE = 0x40
_PAGE_EXECUTE_WRITECOPY = 0x80
_PAGE_GUARD = 0x100

_READABLE_PROTECTS = {
    _PAGE_READONLY,
    _PAGE_READWRITE,
    _PAGE_WRITECOPY,
    _PAGE_EXECUTE_READ,
    _PAGE_EXECUTE_READWRITE,
    _PAGE_EXECUTE_WRITECOPY,
}

_MAX_APPLICATION_ADDRESS = 0x00007FFFFFFFFFFF
_RESPONSE_OBJECT_SIZE = 0x50
_STD_STRING_SIZE = 0x20
_DIALOGUE_LAYOUT_NAME = "openmw_dialogue_window.layout"
_DIALOGUE_LAYOUT_NAME_OFFSET = 0x30
_DIALOGUE_PTR_OFFSET = 0x268
_DIALOGUE_KEYWORDS_OFFSET = 0x288
_DIALOGUE_HISTORY_VECTOR_OFFSET = 0x298
_LIVE_CELL_REF_BASE_OFFSET = 0x1E8
_ESM_NPC_NAME_OFFSET = 0x128


@dataclass(frozen=True)
class OpenMwProcessInfo:
    pid: int
    name: str
    path: str = ""


@dataclass(frozen=True)
class OpenMwDialogueResponse:
    index: int
    title: str
    text: str
    address: int = 0
    score: int = 0

    @property
    def speech_text(self) -> str:
        title = self.title.strip()
        text = self.text.strip()
        if title and text:
            if title[-1] not in "。.!?！？、，;；:：）)]】」』":
                title = f"{title}。"
            return f"{title}\n\n{text}"
        return title or text


@dataclass(frozen=True)
class OpenMwDialogueChoice:
    index: int
    text: str
    choice_id: int = 0


@dataclass
class OpenMwConversationResult:
    process: OpenMwProcessInfo | None = None
    responses: list[OpenMwDialogueResponse] = field(default_factory=list)
    choices: list[OpenMwDialogueChoice] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    actor_name: str = ""
    diagnostics: dict[str, object] = field(default_factory=dict)

    @property
    def latest(self) -> OpenMwDialogueResponse | None:
        return self.responses[-1] if self.responses else None


@dataclass(frozen=True)
class _Region:
    base: int
    size: int
    state: int
    protect: int
    type: int

    @property
    def end(self) -> int:
        return self.base + self.size


@dataclass(frozen=True)
class _ResponseCandidate:
    address: int
    title: str
    text: str
    score: int


@dataclass(frozen=True)
class _HistoryArrayCandidate:
    address: int
    responses: tuple[_ResponseCandidate, ...]
    score: int


@dataclass(frozen=True)
class _HistoryVectorCandidate:
    address: int
    history: _HistoryArrayCandidate


@dataclass(frozen=True)
class _ChoiceCandidate:
    text: str
    choice_id: int


@dataclass(frozen=True)
class _ActorNameCandidate:
    text: str
    address: int
    score: int


@dataclass(frozen=True)
class _DialogueWindowCandidate:
    base: int
    history_vector: int
    history: _HistoryArrayCandidate | None
    choices: tuple[_ChoiceCandidate, ...]
    keywords: tuple[str, ...]
    actor_name: str
    actor_name_status: str
    actor_name_details: dict[str, object]


@dataclass
class _HistoryCache:
    pid: int
    process_path: str
    array_address: int
    response_count: int
    vector_address: int = 0


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", wintypes.LPVOID),
        ("AllocationBase", wintypes.LPVOID),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class _WinApi:
    def __init__(self) -> None:
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.OpenProcess = self.kernel32.OpenProcess
        self.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.OpenProcess.restype = wintypes.HANDLE

        self.CloseHandle = self.kernel32.CloseHandle
        self.CloseHandle.argtypes = [wintypes.HANDLE]
        self.CloseHandle.restype = wintypes.BOOL

        self.ReadProcessMemory = self.kernel32.ReadProcessMemory
        self.ReadProcessMemory.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.LPVOID,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.ReadProcessMemory.restype = wintypes.BOOL

        self.VirtualQueryEx = self.kernel32.VirtualQueryEx
        self.VirtualQueryEx.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            ctypes.POINTER(_MEMORY_BASIC_INFORMATION),
            ctypes.c_size_t,
        ]
        self.VirtualQueryEx.restype = ctypes.c_size_t

        self.CreateToolhelp32Snapshot = self.kernel32.CreateToolhelp32Snapshot
        self.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

        self.Process32FirstW = self.kernel32.Process32FirstW
        self.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        self.Process32FirstW.restype = wintypes.BOOL

        self.Process32NextW = self.kernel32.Process32NextW
        self.Process32NextW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        self.Process32NextW.restype = wintypes.BOOL

        self.QueryFullProcessImageNameW = self.kernel32.QueryFullProcessImageNameW
        self.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.QueryFullProcessImageNameW.restype = wintypes.BOOL


def _is_windows() -> bool:
    return sys.platform == "win32"


def _as_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return int(value)


def _last_error() -> int:
    return int(ctypes.get_last_error())


def _u64(data: bytes | bytearray | memoryview, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def _readable_protect(protect: int) -> bool:
    if protect & _PAGE_GUARD:
        return False
    base = protect & 0xFF
    if base == _PAGE_NOACCESS:
        return False
    return base in _READABLE_PROTECTS


def _has_japanese(text: str) -> bool:
    return any(
        "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff"
        for ch in text
    )


def _looks_like_text(text: str, *, allow_empty: bool) -> bool:
    if not text:
        return allow_empty
    if "\x00" in text:
        return False
    stripped = text.strip()
    if not stripped:
        return allow_empty
    control_count = 0
    for ch in text:
        code = ord(ch)
        if code < 32 and ch not in "\r\n\t":
            control_count += 1
    return control_count == 0


def _looks_like_response_body(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if _looks_like_asset_or_path(stripped):
        return False
    has_sentence_mark = any(ch in stripped for ch in "。.!?！？")
    if _has_japanese(stripped):
        return len(stripped) >= 12 or (len(stripped) >= 8 and has_sentence_mark)
    return len(stripped) >= 24 and (has_sentence_mark or " " in stripped)


def _looks_like_asset_or_path(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    asset_markers = (
        ".nif",
        ".kf",
        ".dds",
        ".tga",
        ".bsa",
        ".wav",
        ".mp3",
        "meshes\\",
        "textures\\",
        "sound\\",
        "icons\\",
    )
    return any(marker in lowered for marker in asset_markers)


class OpenMwConversationReader:
    """Windows専用の OpenMW 会話履歴リーダー。

    OpenMW の MWGui::DialogueWindow::mHistoryContents が保持する
    std::vector<std::unique_ptr<DialogueText>> のポインタ配列候補を探し、
    Response::mTitle / Response::mText を MSVC std::string レイアウトとして読む。
    """

    def __init__(
        self,
        *,
        scan_limit_mb: int = 256,
        chunk_size: int = 4 * 1024 * 1024,
        max_response_objects: int = 20000,
    ) -> None:
        self._scan_limit = max(64, scan_limit_mb) * 1024 * 1024
        self._chunk_size = max(256 * 1024, chunk_size)
        self._max_response_objects = max_response_objects
        self._api: _WinApi | None = None
        self._history_cache: dict[int, _HistoryCache] = {}

    def read_history(self, *, force_rescan: bool = False) -> OpenMwConversationResult:
        if not _is_windows():
            return OpenMwConversationResult(
                diagnostics={"status": "unsupported_os", "message": "Windows専用機能です。"}
            )

        self._api = _WinApi()
        processes = self.find_processes()
        diagnostics: dict[str, object] = {
            "status": "started",
            "openmw_processes": [p.pid for p in processes],
        }
        if not processes:
            diagnostics["status"] = "openmw_not_found"
            return OpenMwConversationResult(diagnostics=diagnostics)

        if not force_rescan:
            cached_results = [
                self._read_cached_process(process)
                for process in processes
                if process.pid in self._history_cache
            ]
            cached_hits = [r for r in cached_results if self._result_has_active_dialogue(r)]
            if cached_hits:
                best_cached = max(
                    cached_hits,
                    key=lambda r: (
                        1 if r.responses else 0,
                        1 if r.actor_name or r.choices else 0,
                        len(r.responses),
                        int(r.diagnostics.get("best_score", 0) or 0),
                    ),
                )
                diagnostics["status"] = "ok"
                diagnostics["process_results"] = [r.diagnostics for r in cached_results]
                best_cached.diagnostics = {**diagnostics, **best_cached.diagnostics}
                return best_cached
            cached_inactive = [
                r
                for r in cached_results
                if r.diagnostics.get("status") == "cache_dialogue_inactive"
            ]
            if cached_inactive:
                best_inactive = cached_inactive[0]
                diagnostics["status"] = "cache_dialogue_inactive"
                diagnostics["process_results"] = [r.diagnostics for r in cached_results]
                best_inactive.diagnostics = {**diagnostics, **best_inactive.diagnostics}
                return best_inactive

        results: list[OpenMwConversationResult] = []
        for process in processes:
            results.append(self._scan_process(process))

        best = max(
            results,
            key=lambda r: (
                len(r.responses),
                int(r.diagnostics.get("best_score", 0) or 0),
            ),
        )
        diagnostics["status"] = "ok" if best.responses else "no_history_candidate"
        diagnostics["process_results"] = [
            r.diagnostics for r in results
        ]
        best.diagnostics = {**diagnostics, **best.diagnostics}
        return best

    def find_processes(self) -> list[OpenMwProcessInfo]:
        if self._api is None:
            self._api = _WinApi()
        api = self._api
        snapshot = api.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if _as_int(snapshot) == _INVALID_HANDLE_VALUE:
            return []
        processes: list[OpenMwProcessInfo] = []
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = api.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                name = entry.szExeFile
                if name.lower() == _OPENMW_EXE_NAME:
                    pid = int(entry.th32ProcessID)
                    processes.append(OpenMwProcessInfo(pid, name, self._process_path(pid)))
                ok = api.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            api.CloseHandle(snapshot)
        return processes

    def _process_path(self, pid: int) -> str:
        if self._api is None:
            return ""
        handle = self._api.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if self._api.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            self._api.CloseHandle(handle)

    def _scan_process(self, process: OpenMwProcessInfo) -> OpenMwConversationResult:
        assert self._api is not None
        access = _PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ
        handle = self._api.OpenProcess(access, False, process.pid)
        if not handle:
            return OpenMwConversationResult(
                process=process,
                diagnostics={
                    "pid": process.pid,
                    "status": "open_failed",
                    "last_error": _last_error(),
                },
            )

        started = time.perf_counter()
        scanner = _ProcessScanner(
            self._api,
            handle,
            scan_limit=self._scan_limit,
            chunk_size=self._chunk_size,
            max_response_objects=self._max_response_objects,
        )
        try:
            regions = scanner.enumerate_regions()
            scan_regions = sorted(regions, key=lambda r: r.base, reverse=True)
            dialogue_state = scanner.find_dialogue_window_state_by_layout(scan_regions)
            if dialogue_state is not None and dialogue_state.history is not None:
                best = dialogue_state.history
                vector_address = dialogue_state.history_vector
                responses = [
                    OpenMwDialogueResponse(
                        index=i,
                        title=r.title,
                        text=r.text,
                        address=r.address,
                        score=r.score,
                    )
                    for i, r in enumerate(best.responses)
                ]
                choices = [
                    OpenMwDialogueChoice(index=i, text=c.text, choice_id=c.choice_id)
                    for i, c in enumerate(dialogue_state.choices)
                ]
                keywords = list(dialogue_state.keywords)
                if not self._has_active_dialogue_context(
                    dialogue_state.actor_name,
                    dialogue_state.actor_name_status,
                    choices,
                ):
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    diagnostics = scanner.diagnostics()
                    diagnostics.update(
                        {
                            "pid": process.pid,
                            "process_path": process.path,
                            "status": "inactive_dialogue_window",
                            "direct_dialogue_window": True,
                            "response_objects": len(responses),
                            "choice_count": len(choices),
                            "keyword_count": len(keywords),
                            "actor_name_status": dialogue_state.actor_name_status,
                            "elapsed_ms": elapsed_ms,
                        }
                    )
                    diagnostics.update(dialogue_state.actor_name_details)
                    return OpenMwConversationResult(process=process, diagnostics=diagnostics)
                self._history_cache[process.pid] = _HistoryCache(
                    pid=process.pid,
                    process_path=process.path,
                    array_address=best.address,
                    response_count=len(best.responses),
                    vector_address=vector_address,
                )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                diagnostics = scanner.diagnostics()
                diagnostics.update(
                    {
                        "pid": process.pid,
                        "process_path": process.path,
                        "status": "ok",
                        "direct_dialogue_window": True,
                        "response_objects": len(responses),
                        "array_candidates": 1,
                        "best_score": best.score,
                        "history_array": f"0x{best.address:X}",
                        "history_vector": f"0x{vector_address:X}",
                        "choices_vector": f"0x{vector_address + 24:X}",
                        "choice_count": len(choices),
                        "keyword_count": len(keywords),
                        "actor_name_status": dialogue_state.actor_name_status,
                        "elapsed_ms": elapsed_ms,
                    }
                )
                diagnostics.update(dialogue_state.actor_name_details)
                return OpenMwConversationResult(
                    process=process,
                    responses=responses,
                    choices=choices,
                    keywords=keywords,
                    actor_name=dialogue_state.actor_name,
                    diagnostics=diagnostics,
                )
            if dialogue_state is not None:
                choices = [
                    OpenMwDialogueChoice(index=i, text=c.text, choice_id=c.choice_id)
                    for i, c in enumerate(dialogue_state.choices)
                ]
                keywords = list(dialogue_state.keywords)
                active_context = self._has_active_dialogue_context(
                    dialogue_state.actor_name,
                    dialogue_state.actor_name_status,
                    choices,
                )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                diagnostics = scanner.diagnostics()
                self._history_cache[process.pid] = _HistoryCache(
                    pid=process.pid,
                    process_path=process.path,
                    array_address=dialogue_state.history.address if dialogue_state.history is not None else 0,
                    response_count=len(dialogue_state.history.responses) if dialogue_state.history is not None else 0,
                    vector_address=dialogue_state.history_vector,
                )
                diagnostics.update(
                    {
                        "pid": process.pid,
                        "process_path": process.path,
                        "status": "dialogue_window_empty_history" if active_context else "inactive_dialogue_window",
                        "direct_dialogue_window": True,
                        "response_objects": 0,
                        "array_candidates": 0,
                        "best_score": 0,
                        "history_array": "",
                        "history_vector": f"0x{dialogue_state.history_vector:X}",
                        "choices_vector": f"0x{dialogue_state.history_vector + 24:X}",
                        "choice_count": len(choices),
                        "keyword_count": len(keywords),
                        "actor_name_status": dialogue_state.actor_name_status,
                        "elapsed_ms": elapsed_ms,
                    }
                )
                diagnostics.update(dialogue_state.actor_name_details)
                return OpenMwConversationResult(
                    process=process,
                    choices=choices if active_context else [],
                    keywords=keywords if active_context else [],
                    actor_name=dialogue_state.actor_name if active_context else "",
                    diagnostics=diagnostics,
                )

            history_vector = scanner.find_history_vector(scan_regions)
            if history_vector is not None:
                best = history_vector.history
                vector_address = history_vector.address
                responses = [
                    OpenMwDialogueResponse(
                        index=i,
                        title=r.title,
                        text=r.text,
                        address=r.address,
                        score=r.score,
                    )
                    for i, r in enumerate(best.responses)
                ]
                choices = [
                    OpenMwDialogueChoice(index=i, text=c.text, choice_id=c.choice_id)
                    for i, c in enumerate(scanner.read_choices_from_history_vector(vector_address))
                ]
                keywords = scanner.read_keywords_from_history_vector(vector_address)
                actor_name, actor_name_status, actor_name_details = scanner.read_actor_name_from_history_vector(
                    vector_address
                )
                if not actor_name and dialogue_state is not None:
                    actor_name = dialogue_state.actor_name
                    actor_name_status = dialogue_state.actor_name_status
                    actor_name_details = dialogue_state.actor_name_details
                if not self._has_active_dialogue_context(actor_name, actor_name_status, choices):
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    diagnostics = scanner.diagnostics()
                    diagnostics.update(
                        {
                            "pid": process.pid,
                            "process_path": process.path,
                            "status": "inactive_history_vector",
                            "direct_history_vector": True,
                            "response_objects": len(responses),
                            "history_array": f"0x{best.address:X}",
                            "history_vector": f"0x{vector_address:X}",
                            "choice_count": len(choices),
                            "keyword_count": len(keywords),
                            "actor_name_status": actor_name_status,
                            "elapsed_ms": elapsed_ms,
                        }
                    )
                    diagnostics.update(actor_name_details)
                    return OpenMwConversationResult(process=process, diagnostics=diagnostics)
                self._history_cache[process.pid] = _HistoryCache(
                    pid=process.pid,
                    process_path=process.path,
                    array_address=best.address,
                    response_count=len(best.responses),
                    vector_address=vector_address,
                )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                diagnostics = scanner.diagnostics()
                diagnostics.update(
                    {
                        "pid": process.pid,
                        "process_path": process.path,
                        "status": "ok",
                        "direct_history_vector": True,
                        "response_objects": len(responses),
                        "array_candidates": 1,
                        "best_score": best.score,
                        "history_array": f"0x{best.address:X}",
                        "history_vector": f"0x{vector_address:X}",
                        "choices_vector": f"0x{vector_address + 24:X}",
                        "choice_count": len(choices),
                        "keyword_count": len(keywords),
                        "actor_name_status": actor_name_status,
                        "elapsed_ms": elapsed_ms,
                    }
                )
                diagnostics.update(actor_name_details)
                return OpenMwConversationResult(
                    process=process,
                    responses=responses,
                    choices=choices,
                    keywords=keywords,
                    actor_name=actor_name,
                    diagnostics=diagnostics,
                )

            responses_by_addr = scanner.find_response_objects(scan_regions)
            arrays = scanner.find_history_arrays(scan_regions, responses_by_addr)
            best = scanner.choose_best_array(arrays, responses_by_addr)
            responses = []
            choices = []
            keywords: list[str] = []
            vector_address = 0
            actor_name = ""
            actor_name_status = "history_vector_not_found"
            actor_name_details: dict[str, object] = {}
            if best is not None:
                if best.address:
                    vector_address = scanner.find_vector_object(best, scan_regions)
                    self._history_cache[process.pid] = _HistoryCache(
                        pid=process.pid,
                        process_path=process.path,
                        array_address=best.address,
                        response_count=len(best.responses),
                        vector_address=vector_address,
                    )
                responses = [
                    OpenMwDialogueResponse(
                        index=i,
                        title=r.title,
                        text=r.text,
                        address=r.address,
                        score=r.score,
                    )
                    for i, r in enumerate(best.responses)
                ]
                if vector_address:
                    choices = [
                        OpenMwDialogueChoice(index=i, text=c.text, choice_id=c.choice_id)
                        for i, c in enumerate(scanner.read_choices_from_history_vector(vector_address))
                    ]
                    keywords = scanner.read_keywords_from_history_vector(vector_address)
                    actor_name, actor_name_status, actor_name_details = (
                        scanner.read_actor_name_from_history_vector(vector_address)
                    )
                if not actor_name:
                    if dialogue_state is None:
                        dialogue_state = scanner.find_dialogue_window_state_by_layout(scan_regions)
                    if dialogue_state is not None:
                        actor_name = dialogue_state.actor_name
                        actor_name_status = dialogue_state.actor_name_status
                        actor_name_details = dialogue_state.actor_name_details
                if responses and not self._has_active_dialogue_context(actor_name, actor_name_status, choices):
                    responses = []
                    choices = []
                    keywords = []
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            diagnostics = scanner.diagnostics()
            diagnostics.update(
                {
                    "pid": process.pid,
                    "process_path": process.path,
                    "status": "ok" if responses else "no_history_candidate",
                    "response_objects": len(responses_by_addr),
                    "array_candidates": len(arrays),
                    "best_score": best.score if best else 0,
                    "history_array": f"0x{best.address:X}" if best else "",
                    "history_vector": f"0x{vector_address:X}" if vector_address else "",
                    "choices_vector": f"0x{vector_address + 24:X}" if vector_address else "",
                    "choice_count": len(choices),
                    "keyword_count": len(keywords),
                    "actor_name_status": actor_name_status,
                    "elapsed_ms": elapsed_ms,
                }
            )
            diagnostics.update(actor_name_details)
            return OpenMwConversationResult(
                process=process,
                responses=responses,
                choices=choices,
                keywords=keywords,
                actor_name=actor_name,
                diagnostics=diagnostics,
            )
        finally:
            self._api.CloseHandle(handle)

    def _read_cached_process(self, process: OpenMwProcessInfo) -> OpenMwConversationResult:
        assert self._api is not None
        cache = self._history_cache.get(process.pid)
        if cache is None:
            return OpenMwConversationResult(
                process=process,
                diagnostics={"pid": process.pid, "status": "cache_missing"},
            )
        access = _PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ
        handle = self._api.OpenProcess(access, False, process.pid)
        if not handle:
            self._history_cache.pop(process.pid, None)
            return OpenMwConversationResult(
                process=process,
                diagnostics={
                    "pid": process.pid,
                    "status": "cached_open_failed",
                    "last_error": _last_error(),
                },
            )

        started = time.perf_counter()
        scanner = _ProcessScanner(
            self._api,
            handle,
            scan_limit=self._scan_limit,
            chunk_size=self._chunk_size,
            max_response_objects=self._max_response_objects,
        )
        try:
            scanner.enumerate_regions()
            dialogue_state: _DialogueWindowCandidate | None = None
            best: _HistoryArrayCandidate | None = None
            recovered_from_dialogue = False
            recovered_from_history_vector = False
            if cache.vector_address:
                dialogue_base = scanner._find_dialogue_window_base(cache.vector_address)
                if dialogue_base > 0:
                    dialogue_state = scanner.read_dialogue_window_state(dialogue_base)
                    if dialogue_state is not None:
                        if dialogue_state.history is not None:
                            best = dialogue_state.history
                            cache.array_address = best.address
                            cache.response_count = len(best.responses)
                            cache.vector_address = dialogue_state.history_vector
                        else:
                            choices = [
                                OpenMwDialogueChoice(index=i, text=c.text, choice_id=c.choice_id)
                                for i, c in enumerate(dialogue_state.choices)
                            ]
                            keywords = list(dialogue_state.keywords)
                            elapsed_ms = int((time.perf_counter() - started) * 1000)
                            diagnostics = scanner.diagnostics()
                            diagnostics.update(
                                {
                                    "pid": process.pid,
                                    "process_path": process.path,
                                    "status": (
                                        "cache_dialogue_empty_history"
                                        if self._has_active_dialogue_context(
                                            dialogue_state.actor_name,
                                            dialogue_state.actor_name_status,
                                            choices,
                                        )
                                        else "cache_dialogue_inactive"
                                    ),
                                    "direct_cached_dialogue_window": True,
                                    "response_objects": 0,
                                    "history_vector": f"0x{dialogue_state.history_vector:X}",
                                    "cache_vector": f"0x{cache.vector_address:X}",
                                    "choices_vector": f"0x{dialogue_state.history_vector + 24:X}",
                                    "choice_count": len(choices),
                                    "keyword_count": len(keywords),
                                    "actor_name_status": dialogue_state.actor_name_status,
                                    "elapsed_ms": elapsed_ms,
                                }
                            )
                            diagnostics.update(dialogue_state.actor_name_details)
                            return OpenMwConversationResult(
                                process=process,
                                choices=choices,
                                keywords=keywords,
                                actor_name=dialogue_state.actor_name,
                                diagnostics=diagnostics,
                            )

            if best is None:
                best = scanner.read_cached_history(cache)
            if best is None or not cache.vector_address:
                scan_regions = sorted(scanner._regions, key=lambda r: r.base, reverse=True)
                history_vector = scanner.find_history_vector(scan_regions)
                if history_vector is not None:
                    best = history_vector.history
                    cache.array_address = best.address
                    cache.response_count = len(best.responses)
                    cache.vector_address = history_vector.address
                    recovered_from_history_vector = True
                else:
                    dialogue_state = scanner.find_dialogue_window_state_by_layout(scan_regions)
                    if dialogue_state is not None and dialogue_state.history is not None:
                        best = dialogue_state.history
                        cache.array_address = best.address
                        cache.response_count = len(best.responses)
                        cache.vector_address = dialogue_state.history_vector
                        recovered_from_dialogue = True

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if best is None:
                return OpenMwConversationResult(
                    process=process,
                    diagnostics={
                        **scanner.diagnostics(),
                        "pid": process.pid,
                        "process_path": process.path,
                        "status": "cache_miss",
                        "cache_array": f"0x{cache.array_address:X}",
                        "cache_vector": f"0x{cache.vector_address:X}" if cache.vector_address else "",
                        "elapsed_ms": elapsed_ms,
                    },
                )
            responses = [
                OpenMwDialogueResponse(
                    index=i,
                    title=r.title,
                    text=r.text,
                    address=r.address,
                    score=r.score,
                )
                for i, r in enumerate(best.responses)
            ]
            choices = []
            keywords: list[str] = []
            actor_name = ""
            actor_name_status = "history_vector_not_found"
            actor_name_details: dict[str, object] = {}
            if cache.vector_address:
                choices = [
                    OpenMwDialogueChoice(index=i, text=c.text, choice_id=c.choice_id)
                    for i, c in enumerate(scanner.read_choices_from_history_vector(cache.vector_address))
                ]
                keywords = scanner.read_keywords_from_history_vector(cache.vector_address)
                actor_name, actor_name_status, actor_name_details = scanner.read_actor_name_from_history_vector(
                    cache.vector_address
                )
            if not actor_name:
                if dialogue_state is None:
                    scan_regions = sorted(scanner._regions, key=lambda r: r.base, reverse=True)
                    dialogue_state = scanner.find_dialogue_window_state_by_layout(scan_regions)
                if dialogue_state is not None:
                    if not choices and cache.vector_address == dialogue_state.history_vector:
                        choices = [
                            OpenMwDialogueChoice(index=i, text=c.text, choice_id=c.choice_id)
                            for i, c in enumerate(dialogue_state.choices)
                        ]
                    if not keywords and cache.vector_address == dialogue_state.history_vector:
                        keywords = list(dialogue_state.keywords)
                    actor_name = dialogue_state.actor_name
                    actor_name_status = dialogue_state.actor_name_status
                    actor_name_details = dialogue_state.actor_name_details
            if responses and not self._has_active_dialogue_context(actor_name, actor_name_status, choices):
                return OpenMwConversationResult(
                    process=process,
                    diagnostics={
                        **scanner.diagnostics(),
                        "pid": process.pid,
                        "process_path": process.path,
                        "status": "cache_inactive_dialogue",
                        "cache_array": f"0x{cache.array_address:X}",
                        "cache_vector": f"0x{cache.vector_address:X}" if cache.vector_address else "",
                        "response_objects": len(responses),
                        "choice_count": len(choices),
                        "keyword_count": len(keywords),
                        "actor_name_status": actor_name_status,
                        "elapsed_ms": elapsed_ms,
                        **actor_name_details,
                    },
                )
            cache.array_address = best.address
            cache.response_count = len(best.responses)
            diagnostics = scanner.diagnostics()
            diagnostics.update(
                {
                    "pid": process.pid,
                    "process_path": process.path,
                    "status": (
                        "cache_recovered"
                        if recovered_from_dialogue or recovered_from_history_vector
                        else "cache_hit"
                    ),
                    "direct_dialogue_window": recovered_from_dialogue,
                    "direct_history_vector": recovered_from_history_vector,
                    "response_objects": len(best.responses),
                    "array_candidates": 1,
                    "best_score": best.score,
                    "history_array": f"0x{best.address:X}",
                    "history_vector": f"0x{cache.vector_address:X}" if cache.vector_address else "",
                    "cache_vector": f"0x{cache.vector_address:X}" if cache.vector_address else "",
                    "choices_vector": f"0x{cache.vector_address + 24:X}" if cache.vector_address else "",
                    "choice_count": len(choices),
                    "keyword_count": len(keywords),
                    "actor_name_status": actor_name_status,
                    "elapsed_ms": elapsed_ms,
                }
            )
            diagnostics.update(actor_name_details)
            return OpenMwConversationResult(
                process=process,
                responses=responses,
                choices=choices,
                keywords=keywords,
                actor_name=actor_name,
                diagnostics=diagnostics,
            )
        finally:
            self._api.CloseHandle(handle)

    @staticmethod
    def _result_has_active_dialogue(result: OpenMwConversationResult) -> bool:
        return bool(result.responses or result.choices or result.actor_name.strip())

    @staticmethod
    def _has_active_dialogue_context(
        actor_name: str,
        actor_name_status: str,
        choices: list[OpenMwDialogueChoice] | tuple[_ChoiceCandidate, ...],
    ) -> bool:
        if actor_name.strip():
            return True
        if actor_name_status == "ok":
            return True
        return bool(choices)

class _ProcessScanner:
    def __init__(
        self,
        api: _WinApi,
        handle,
        *,
        scan_limit: int,
        chunk_size: int,
        max_response_objects: int,
    ) -> None:
        self._api = api
        self._handle = handle
        self._scan_limit = scan_limit
        self._array_scan_limit = scan_limit * 4
        self._layout_scan_limit = scan_limit * 16
        self._chunk_size = chunk_size
        self._max_response_objects = max_response_objects
        self._bases: list[int] = []
        self._regions: list[_Region] = []
        self._object_scan_bytes = 0
        self._array_scan_bytes = 0
        self._layout_scan_bytes = 0
        self._read_failures = 0
        self._object_scan_limited = False
        self._array_scan_limited = False
        self._layout_scan_limited = False
        self._response_cache: dict[int, _ResponseCandidate | None] = {}

    def enumerate_regions(self) -> list[_Region]:
        regions: list[_Region] = []
        address = 0
        mbi = _MEMORY_BASIC_INFORMATION()
        mbi_size = ctypes.sizeof(_MEMORY_BASIC_INFORMATION)
        while address < _MAX_APPLICATION_ADDRESS:
            ret = self._api.VirtualQueryEx(
                self._handle,
                ctypes.c_void_p(address),
                ctypes.byref(mbi),
                mbi_size,
            )
            if ret == 0:
                break
            base = _as_int(mbi.BaseAddress)
            size = int(mbi.RegionSize)
            if size <= 0:
                address = max(address + 0x1000, base + 0x1000)
                continue
            region = _Region(
                base=base,
                size=size,
                state=int(mbi.State),
                protect=int(mbi.Protect),
                type=int(mbi.Type),
            )
            regions.append(region)
            next_address = base + size
            if next_address <= address:
                next_address = address + 0x1000
            address = next_address

        self._regions = regions
        self._bases = [r.base for r in regions]
        return regions

    def find_response_objects(
        self,
        regions: list[_Region],
    ) -> dict[int, _ResponseCandidate]:
        responses: dict[int, _ResponseCandidate] = {}
        for chunk_base, data in self._iter_scan_chunks(regions, purpose="object"):
            if len(data) < _RESPONSE_OBJECT_SIZE:
                continue
            aligned_offset = (-chunk_base) & 7
            usable = len(data) - ((len(data) - aligned_offset) % 8)
            if aligned_offset >= usable:
                continue
            try:
                qwords = memoryview(data)[aligned_offset:usable].cast("Q")
            except TypeError:
                continue
            for idx, value in enumerate(qwords):
                offset = aligned_offset + idx * 8
                if offset + _RESPONSE_OBJECT_SIZE > len(data):
                    break
                if not self._is_image_pointer(int(value)):
                    continue
                address = chunk_base + offset
                candidate = self._parse_response_bytes(
                    address,
                    data[offset:offset + _RESPONSE_OBJECT_SIZE],
                )
                self._response_cache[address] = candidate
                if candidate is None:
                    continue
                responses[address] = candidate
                if len(responses) >= self._max_response_objects:
                    self._object_scan_limited = True
                    return responses
        return responses

    def find_history_arrays(
        self,
        regions: list[_Region],
        responses_by_addr: dict[int, _ResponseCandidate],
    ) -> list[_HistoryArrayCandidate]:
        if not responses_by_addr:
            return []
        arrays: list[_HistoryArrayCandidate] = []
        seen: set[tuple[int, ...]] = set()
        seeds = sorted(
            responses_by_addr.values(),
            key=lambda r: (r.score, len(r.text)),
            reverse=True,
        )[:64]
        pattern_to_seed = {
            struct.pack("<Q", seed.address): seed
            for seed in seeds
        }
        seed_regex = re.compile(
            b"|".join(re.escape(pattern) for pattern in pattern_to_seed)
        )

        def add_run(run_addr: int, run: list[_ResponseCandidate]) -> _HistoryArrayCandidate | None:
            if not run:
                return None
            key = tuple(r.address for r in run)
            if key in seen:
                return None
            seen.add(key)
            score = self._score_history_array(run)
            candidate = _HistoryArrayCandidate(
                address=run_addr,
                responses=tuple(run),
                score=score,
            )
            arrays.append(candidate)
            return candidate

        array_regions = self._array_candidate_regions(regions, seeds)
        for chunk_base, data in self._iter_scan_chunks(array_regions, purpose="array"):
            if len(data) < 8:
                continue
            for match in seed_regex.finditer(data):
                pos = match.start()
                if (chunk_base + pos) & 0x7:
                    continue
                seed = pattern_to_seed.get(match.group(0))
                if seed is None:
                    continue
                run_addr, run = self._expand_history_array_from_seed(
                    chunk_base,
                    data,
                    pos,
                    seed,
                )
                candidate = add_run(run_addr, run)
                if candidate is not None and len(candidate.responses) >= 2:
                    return [candidate]

        multi = [a for a in arrays if len(a.responses) >= 2]
        return multi or arrays

    def _array_candidate_regions(
        self,
        regions: list[_Region],
        seeds: list[_ResponseCandidate],
    ) -> list[_Region]:
        if not seeds:
            return regions
        seed_addresses = [seed.address for seed in seeds]
        near = 512 * 1024 * 1024
        selected: list[_Region] = []
        for region in regions:
            if not self._should_scan_region(region):
                continue
            for address in seed_addresses:
                if region.base <= address + near and region.end >= address - near:
                    selected.append(region)
                    break
        if not selected:
            return regions

        def distance(region: _Region) -> int:
            best = sys.maxsize
            for address in seed_addresses:
                if region.base <= address < region.end:
                    return 0
                best = min(best, abs(region.base - address), abs(region.end - address))
            return best

        selected.sort(key=distance)
        return selected

    def choose_best_array(
        self,
        arrays: list[_HistoryArrayCandidate],
        responses_by_addr: dict[int, _ResponseCandidate],
    ) -> _HistoryArrayCandidate | None:
        if arrays:
            return max(
                arrays,
                key=lambda a: (len(a.responses), a.score),
            )
        return None

    def read_cached_history(self, cache: _HistoryCache) -> _HistoryArrayCandidate | None:
        if cache.vector_address:
            candidate = self._read_vector_history(cache.vector_address)
            if candidate is not None:
                return candidate
        if cache.array_address:
            max_count = max(32, min(512, cache.response_count + 16))
            return self._read_history_array(cache.array_address, max_count=max_count)
        return None

    def find_dialogue_window_state_by_layout(
        self,
        regions: list[_Region],
    ) -> _DialogueWindowCandidate | None:
        first_state: _DialogueWindowCandidate | None = None
        for base in self._iter_dialogue_window_bases_by_layout(regions):
            state = self.read_dialogue_window_state(base)
            if state is None:
                continue
            if first_state is None:
                first_state = state
            if state.history is not None:
                return state
        return first_state

    def read_dialogue_window_state(self, dialogue_base: int) -> _DialogueWindowCandidate | None:
        if dialogue_base <= 0:
            return None
        history_vector = dialogue_base + _DIALOGUE_HISTORY_VECTOR_OFFSET
        history = self._read_vector_history(history_vector)
        choices = tuple(self._read_choice_vector(history_vector + 24))
        keywords = tuple(self._read_keyword_list(dialogue_base + _DIALOGUE_KEYWORDS_OFFSET))
        actor_name, actor_name_status, actor_name_details = self.read_actor_name_from_dialogue_base(dialogue_base)
        return _DialogueWindowCandidate(
            base=dialogue_base,
            history_vector=history_vector,
            history=history,
            choices=choices,
            keywords=keywords,
            actor_name=actor_name,
            actor_name_status=actor_name_status,
            actor_name_details=actor_name_details,
        )

    def find_history_vector(self, regions: list[_Region]) -> _HistoryVectorCandidate | None:
        best: _HistoryVectorCandidate | None = None
        seen_arrays: set[int] = set()
        for chunk_base, data in self._iter_scan_chunks(regions, purpose="array"):
            if len(data) < 24:
                continue
            aligned_offset = (-chunk_base) & 7
            usable = len(data) - ((len(data) - aligned_offset) % 8)
            if aligned_offset >= usable:
                continue
            try:
                qwords = memoryview(data)[aligned_offset:usable].cast("Q")
            except TypeError:
                continue
            for idx in range(0, max(0, len(qwords) - 2)):
                begin = int(qwords[idx])
                end = int(qwords[idx + 1])
                capacity = int(qwords[idx + 2])
                if begin <= 0 or end < begin or capacity < end:
                    continue
                if begin & 0x7 or end & 0x7 or capacity & 0x7:
                    continue
                count = (end - begin) // 8
                capacity_count = (capacity - begin) // 8
                if count <= 0 or count > 512 or capacity_count > 512:
                    continue
                if begin in seen_arrays:
                    continue
                seen_arrays.add(begin)
                if not self._is_readable_address(begin, count * 8):
                    continue
                history = self._read_history_array(begin, max_count=count, exact_count=count)
                if history is None:
                    continue
                vector_address = chunk_base + aligned_offset + idx * 8
                candidate = _HistoryVectorCandidate(address=vector_address, history=history)
                if self._find_dialogue_window_base(vector_address) > 0:
                    return candidate
                if best is None or (
                    len(candidate.history.responses),
                    candidate.history.score,
                ) > (
                    len(best.history.responses),
                    best.history.score,
                ):
                    best = candidate
        return best

    def read_choices_from_history_vector(self, history_vector_address: int) -> list[_ChoiceCandidate]:
        # DialogueWindow の private メンバ順は
        # mHistoryContents(vector<unique_ptr<DialogueText>>) の直後に
        # mChoices(vector<pair<string,int>>) が続く。
        if history_vector_address <= 0:
            return []
        return self._read_choice_vector(history_vector_address + 24)

    def read_keywords_from_history_vector(self, history_vector_address: int) -> list[str]:
        dialogue_base = self._find_dialogue_window_base(history_vector_address)
        if dialogue_base <= 0:
            return []
        return self._read_keyword_list(dialogue_base + _DIALOGUE_KEYWORDS_OFFSET)

    def read_actor_name_from_history_vector(
        self,
        history_vector_address: int,
    ) -> tuple[str, str, dict[str, object]]:
        if history_vector_address <= 0:
            return "", "history_vector_not_found", {}

        dialogue_base = self._find_dialogue_window_base(history_vector_address)
        if dialogue_base <= 0:
            return "", "dialogue_window_not_found", {}
        return self.read_actor_name_from_dialogue_base(dialogue_base)

    def read_actor_name_from_dialogue_base(
        self,
        dialogue_base: int,
    ) -> tuple[str, str, dict[str, object]]:
        if dialogue_base <= 0:
            return "", "dialogue_window_not_found", {}

        details: dict[str, object] = {
            "dialogue_window": f"0x{dialogue_base:X}",
        }
        ptr_raw = self._read(dialogue_base + _DIALOGUE_PTR_OFFSET, 24)
        if len(ptr_raw) != 24:
            return "", "actor_ptr_unreadable", details
        actor_ref = _u64(ptr_raw, 0)
        actor_cell = _u64(ptr_raw, 8)
        details["actor_ref"] = f"0x{actor_ref:X}" if actor_ref else ""
        details["actor_cell"] = f"0x{actor_cell:X}" if actor_cell else ""
        if not actor_ref or not self._is_readable_address(actor_ref, 8):
            return "", "actor_ptr_empty", details

        candidate = self._read_actor_name_from_live_cell_ref(actor_ref)
        if candidate is None:
            return "", "actor_name_not_found", details

        details["actor_name_address"] = f"0x{candidate.address:X}"
        details["actor_name_score"] = candidate.score
        return candidate.text, "ok", details

    def find_dialogue_window_base_by_layout(self, regions: list[_Region]) -> int:
        state = self.find_dialogue_window_state_by_layout(regions)
        return state.base if state is not None else 0

    def _iter_dialogue_window_bases_by_layout(self, regions: list[_Region]):
        pattern = _DIALOGUE_LAYOUT_NAME.encode("utf-8")
        if not pattern:
            return
        seen: set[int] = set()
        layout_buffers: list[int] = []
        for chunk_base, data in self._iter_scan_chunks(regions, purpose="layout"):
            start = 0
            while True:
                pos = data.find(pattern, start)
                if pos < 0:
                    break
                start = pos + 1
                string_address = chunk_base + pos
                if string_address not in layout_buffers:
                    layout_buffers.append(string_address)
                base = string_address - _DIALOGUE_LAYOUT_NAME_OFFSET
                if base not in seen and self._looks_like_dialogue_window_base(base):
                    seen.add(base)
                    yield base
                if len(layout_buffers) >= 32:
                    break
            if len(layout_buffers) >= 32:
                break

        pointer_patterns = [struct.pack("<Q", address) for address in layout_buffers]
        pointer_regions = self._nearby_regions(regions, layout_buffers) or regions
        for chunk_base, data in self._iter_scan_chunks(pointer_regions, purpose="layout"):
            for pointer_pattern in pointer_patterns:
                start = 0
                while True:
                    pos = data.find(pointer_pattern, start)
                    if pos < 0:
                        break
                    start = pos + 1
                    string_object = chunk_base + pos
                    if string_object & 0x7:
                        continue
                    base = string_object - _DIALOGUE_LAYOUT_NAME_OFFSET
                    if base in seen:
                        continue
                    if not self._looks_like_dialogue_window_base(base):
                        continue
                    seen.add(base)
                    yield base

    @staticmethod
    def _nearby_regions(regions: list[_Region], addresses: list[int]) -> list[_Region]:
        ranges = [
            (max(0, address - 64 * 1024 * 1024), address + 256 * 1024 * 1024)
            for address in addresses
        ]
        return sorted(
            [
                region
                for region in regions
                if any(region.end > start and region.base < end for start, end in ranges)
            ],
            key=lambda r: r.base,
            reverse=True,
        )

    def _looks_like_dialogue_window_base(self, base: int) -> bool:
        if base <= 0 or base & 0x7:
            return False
        if self._read_std_string_at(
            base + _DIALOGUE_LAYOUT_NAME_OFFSET,
            max_len=128,
            allow_empty=False,
        ) != _DIALOGUE_LAYOUT_NAME:
            return False
        raw = self._read(base, 16)
        if len(raw) != 16:
            return False
        vptr = _u64(raw, 0)
        main_widget = _u64(raw, 8)
        return self._is_image_pointer(vptr) and self._is_readable_address(main_widget, 8)

    def _find_dialogue_window_base(self, history_vector_address: int) -> int:
        expected_base = history_vector_address - _DIALOGUE_HISTORY_VECTOR_OFFSET
        layout_name = self._read_std_string_at(
            expected_base + _DIALOGUE_LAYOUT_NAME_OFFSET,
            max_len=128,
            allow_empty=False,
        )
        if layout_name == _DIALOGUE_LAYOUT_NAME:
            return expected_base

        region = self._region_at(history_vector_address)
        if region is None:
            return 0
        start = max(region.base, history_vector_address - 0x800)
        end = min(region.end, history_vector_address + 0x80)
        data = self._read(start, end - start)
        if not data:
            return 0
        for offset in range(0, max(0, len(data) - _STD_STRING_SIZE), 8):
            text = self._read_std_string(data, offset, max_len=128, allow_empty=False)
            if text != _DIALOGUE_LAYOUT_NAME:
                continue
            base = start + offset - _DIALOGUE_LAYOUT_NAME_OFFSET
            if base + _DIALOGUE_HISTORY_VECTOR_OFFSET == history_vector_address:
                return base
        return 0

    def _read_actor_name_from_live_cell_ref(self, actor_ref: int) -> _ActorNameCandidate | None:
        direct = self._read_actor_name_from_live_cell_ref_base_pointer(
            actor_ref,
            _LIVE_CELL_REF_BASE_OFFSET,
            preferred=True,
        )
        if direct is not None:
            return direct

        region = self._region_at(actor_ref)
        if region is None:
            return None
        size = min(0x400, region.end - actor_ref)
        raw = self._read(actor_ref, size)
        candidates: list[_ActorNameCandidate] = []
        for offset in range(0, max(0, len(raw) - 8), 8):
            if offset == _LIVE_CELL_REF_BASE_OFFSET:
                continue
            candidate = self._read_actor_name_from_live_cell_ref_base_pointer(
                actor_ref,
                offset,
                preferred=False,
            )
            if candidate is not None:
                distance = abs(offset - _LIVE_CELL_REF_BASE_OFFSET)
                candidates.append(
                    _ActorNameCandidate(
                        text=candidate.text,
                        address=candidate.address,
                        score=candidate.score - min(distance // 8, 24),
                    )
                )
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.score)

    def _read_keyword_list(self, list_address: int) -> list[str]:
        raw = self._read(list_address, 16)
        if len(raw) != 16:
            return []
        head = _u64(raw, 0)
        count = _u64(raw, 8)
        if count <= 0 or count > 256:
            return []
        if not self._is_readable_address(head, 16):
            return []

        head_raw = self._read(head, 16)
        if len(head_raw) != 16:
            return []
        node = _u64(head_raw, 0)
        keywords: list[str] = []
        seen: set[int] = {head}
        for _ in range(int(count)):
            if node in seen or not self._is_readable_address(node, 0x10 + _STD_STRING_SIZE):
                return keywords
            seen.add(node)
            node_raw = self._read(node, 0x10 + _STD_STRING_SIZE)
            if len(node_raw) != 0x10 + _STD_STRING_SIZE:
                return keywords
            text = self._read_std_string(node_raw, 0x10, max_len=256, allow_empty=False)
            if text is not None and _looks_like_text(text, allow_empty=False):
                keywords.append(text)
            node = _u64(node_raw, 0)
        return keywords

    def _read_actor_name_from_live_cell_ref_base_pointer(
        self,
        actor_ref: int,
        offset: int,
        *,
        preferred: bool,
    ) -> _ActorNameCandidate | None:
        raw = self._read(actor_ref + offset, 8)
        if len(raw) != 8:
            return None
        base_record = _u64(raw, 0)
        if not self._is_readable_address(base_record, _STD_STRING_SIZE):
            return None
        candidate = self._read_actor_name_from_base_record(base_record)
        if candidate is None:
            return None
        bonus = 24 if preferred else 0
        return _ActorNameCandidate(
            text=candidate.text,
            address=candidate.address,
            score=candidate.score + bonus,
        )

    def _read_actor_name_from_base_record(self, base_record: int) -> _ActorNameCandidate | None:
        region = self._region_at(base_record)
        if region is None:
            return None
        size = min(0x360, region.end - base_record)
        data = self._read(base_record, size)
        if len(data) < _STD_STRING_SIZE:
            return None

        candidates: list[_ActorNameCandidate] = []
        for offset in range(0, max(0, len(data) - _STD_STRING_SIZE), 8):
            text = self._read_std_string(data, offset, max_len=512, allow_empty=False)
            if text is None:
                continue
            score = self._score_actor_name(text, offset)
            if score <= 0:
                continue
            candidates.append(
                _ActorNameCandidate(
                    text=text.strip(),
                    address=base_record + offset,
                    score=score,
                )
            )
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.score)

    def _score_actor_name(self, text: str, offset: int) -> int:
        stripped = text.strip()
        if not stripped or len(stripped) > 80:
            return -100
        lowered = stripped.lower()
        if any(marker in lowered for marker in (".nif", ".kf", ".dds", ".tga", ".bsa", "meshes\\", "textures\\")):
            return -100
        if any(marker in stripped for marker in ("#{", "::", "<", ">", "%", "\x00")):
            return -100
        if not _looks_like_text(stripped, allow_empty=False):
            return -100

        score = 4
        if _has_japanese(stripped):
            score += 18
        if any(ch in stripped for ch in (" ", "　", "・", "'", "-")):
            score += 4
        if offset == _ESM_NPC_NAME_OFFSET:
            score += 30
        elif 0x80 <= offset <= 0x180:
            score += 6
        if "." in stripped or "\\" in stripped or "/" in stripped:
            score -= 30
        if "_" in stripped:
            score -= 8
        if stripped.islower() and stripped.isascii():
            score -= 4
        return score

    def find_vector_object(
        self,
        history: _HistoryArrayCandidate,
        regions: list[_Region],
    ) -> int:
        if history.address <= 0 or len(history.responses) < 1:
            return 0
        begin = history.address
        end = begin + len(history.responses) * 8
        pattern = struct.pack("<QQ", begin, end)
        for chunk_base, data in self._iter_scan_chunks(regions, purpose="array"):
            if len(data) < 24:
                continue
            start = 0
            while True:
                pos = data.find(pattern, start)
                if pos < 0:
                    break
                start = pos + 1
                address = chunk_base + pos
                if address & 0x7 or pos + 24 > len(data):
                    continue
                capacity = _u64(data, pos + 16)
                if capacity < end:
                    continue
                if capacity - begin > 512 * 8:
                    continue
                return address
        return 0

    def _read_vector_history(self, vector_address: int) -> _HistoryArrayCandidate | None:
        if not self._is_readable_address(vector_address, 24):
            return None
        raw = self._read(vector_address, 24)
        if len(raw) != 24:
            return None
        begin = _u64(raw, 0)
        end = _u64(raw, 8)
        capacity = _u64(raw, 16)
        if begin <= 0 or end < begin or capacity < end:
            return None
        if begin & 0x7 or end & 0x7 or capacity & 0x7:
            return None
        count = (end - begin) // 8
        capacity_count = (capacity - begin) // 8
        if count <= 0 or count > 512 or capacity_count > 512:
            return None
        return self._read_history_array(begin, max_count=count, exact_count=count)

    def _read_choice_vector(self, vector_address: int) -> list[_ChoiceCandidate]:
        raw = self._read_vector_header(vector_address)
        if raw is None:
            return []
        begin, end, capacity = raw
        if begin == 0 and end == 0 and capacity == 0:
            return []
        if begin <= 0 or end < begin or capacity < end:
            return []
        if begin & 0x7 or end & 0x7 or capacity & 0x7:
            return []

        best: list[_ChoiceCandidate] = []
        for stride in (0x28, 0x30):
            size = end - begin
            capacity_size = capacity - begin
            if size % stride != 0 or capacity_size % stride != 0:
                continue
            count = size // stride
            capacity_count = capacity_size // stride
            if count <= 0 or count > 64 or capacity_count > 64:
                continue
            data = self._read(begin, size)
            if len(data) != size:
                continue
            choices: list[_ChoiceCandidate] = []
            for index in range(count):
                offset = index * stride
                text = self._read_std_string(data, offset, max_len=1024, allow_empty=False)
                if text is None or not _looks_like_text(text, allow_empty=False):
                    break
                try:
                    choice_id = struct.unpack_from("<i", data, offset + 0x20)[0]
                except struct.error:
                    break
                choices.append(_ChoiceCandidate(text=text, choice_id=choice_id))
            if len(choices) == count and len(choices) > len(best):
                best = choices
        return best

    def _read_vector_header(self, vector_address: int) -> tuple[int, int, int] | None:
        if not self._is_readable_address(vector_address, 24):
            return None
        raw = self._read(vector_address, 24)
        if len(raw) != 24:
            return None
        return _u64(raw, 0), _u64(raw, 8), _u64(raw, 16)

    def _read_history_array(
        self,
        array_address: int,
        *,
        max_count: int,
        exact_count: int | None = None,
    ) -> _HistoryArrayCandidate | None:
        if array_address <= 0 or array_address & 0x7:
            return None
        if not self._is_readable_address(array_address, 8):
            return None
        region = self._region_at(array_address)
        if region is None:
            return None
        available_count = (region.end - array_address) // 8
        count_to_read = min(max_count, available_count)
        if exact_count is not None:
            if exact_count > available_count:
                return None
            count_to_read = exact_count
        if count_to_read <= 0:
            return None
        raw = self._read(array_address, count_to_read * 8)
        if len(raw) < count_to_read * 8:
            return None
        responses: list[_ResponseCandidate] = []
        for i in range(count_to_read):
            ptr = _u64(raw, i * 8)
            candidate = self._parse_response_at(ptr)
            if candidate is None:
                if exact_count is not None:
                    return None
                break
            responses.append(candidate)
        if not responses:
            return None
        score = self._score_history_array(responses)
        return _HistoryArrayCandidate(
            address=array_address,
            responses=tuple(responses),
            score=score,
        )

    def _expand_history_array_from_seed(
        self,
        chunk_base: int,
        data: bytes,
        seed_offset: int,
        seed: _ResponseCandidate,
    ) -> tuple[int, list[_ResponseCandidate]]:
        left = seed_offset
        prefix: list[_ResponseCandidate] = []
        while left >= 8:
            ptr = _u64(data, left - 8)
            candidate = self._parse_response_at(ptr)
            if candidate is None:
                break
            prefix.insert(0, candidate)
            left -= 8

        right = seed_offset + 8
        suffix: list[_ResponseCandidate] = []
        while right + 8 <= len(data):
            ptr = _u64(data, right)
            candidate = self._parse_response_at(ptr)
            if candidate is None:
                break
            suffix.append(candidate)
            right += 8

        return chunk_base + left, prefix + [seed] + suffix

    def diagnostics(self) -> dict[str, object]:
        return {
            "region_count": len(self._regions),
            "object_scan_mb": round(self._object_scan_bytes / (1024 * 1024), 1),
            "array_scan_mb": round(self._array_scan_bytes / (1024 * 1024), 1),
            "layout_scan_mb": round(self._layout_scan_bytes / (1024 * 1024), 1),
            "read_failures": self._read_failures,
            "object_scan_limited": self._object_scan_limited,
            "array_scan_limited": self._array_scan_limited,
            "layout_scan_limited": self._layout_scan_limited,
        }

    def _iter_scan_chunks(self, regions: list[_Region], *, purpose: str):
        scanned = 0
        if purpose == "object":
            limit = self._scan_limit
        elif purpose == "layout":
            limit = self._layout_scan_limit
        else:
            limit = self._array_scan_limit
        for region in regions:
            if not self._should_scan_region(region):
                continue
            cursor = region.base
            while cursor < region.end:
                if scanned >= limit:
                    if purpose == "object":
                        self._object_scan_limited = True
                    elif purpose == "layout":
                        self._layout_scan_limited = True
                    else:
                        self._array_scan_limited = True
                    return
                size = min(self._chunk_size, region.end - cursor)
                data = self._read(cursor, size)
                if data:
                    scanned += len(data)
                    if purpose == "object":
                        self._object_scan_bytes += len(data)
                    elif purpose == "layout":
                        self._layout_scan_bytes += len(data)
                    else:
                        self._array_scan_bytes += len(data)
                    yield cursor, data
                cursor += size

    def _should_scan_region(self, region: _Region) -> bool:
        if region.state != _MEM_COMMIT:
            return False
        if not _readable_protect(region.protect):
            return False
        if region.type not in (_MEM_PRIVATE, _MEM_MAPPED):
            return False
        if region.size < 8:
            return False
        return True

    def _read(self, address: int, size: int) -> bytes:
        if size <= 0:
            return b""
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t(0)
        ok = self._api.ReadProcessMemory(
            self._handle,
            ctypes.c_void_p(address),
            buf,
            size,
            ctypes.byref(read),
        )
        if not ok or read.value <= 0:
            self._read_failures += 1
            return b""
        return bytes(buf.raw[:read.value])

    def _region_at(self, address: int) -> _Region | None:
        idx = bisect.bisect_right(self._bases, address) - 1
        if idx < 0:
            return None
        region = self._regions[idx]
        if region.base <= address < region.end:
            return region
        return None

    def _is_readable_address(self, address: int, size: int = 1) -> bool:
        if address <= 0x10000:
            return False
        region = self._region_at(address)
        if region is None:
            return False
        if region.state != _MEM_COMMIT or not _readable_protect(region.protect):
            return False
        return address + max(1, size) <= region.end

    def _is_image_pointer(self, address: int) -> bool:
        if address <= 0x10000 or address & 0x7:
            return False
        region = self._region_at(address)
        return region is not None and region.type == _MEM_IMAGE

    def _parse_response_bytes(
        self,
        address: int,
        data: bytes,
    ) -> _ResponseCandidate | None:
        if len(data) < _RESPONSE_OBJECT_SIZE:
            return None
        text = self._read_std_string(data, 0x08, max_len=12000, allow_empty=False)
        if text is None:
            return None
        if not _looks_like_response_body(text):
            return None
        title = self._read_std_string(data, 0x28, max_len=512, allow_empty=True)
        if title is None:
            title = ""
        if _looks_like_asset_or_path(title):
            return None
        need_margin = data[0x48] if title else 0
        score = self._score_response(title, text, need_margin)
        if score < 5:
            return None
        return _ResponseCandidate(address=address, title=title, text=text, score=score)

    def _parse_response_at(self, address: int) -> _ResponseCandidate | None:
        cached = self._response_cache.get(address)
        if cached is not None or address in self._response_cache:
            return cached
        if not self._is_readable_address(address, _RESPONSE_OBJECT_SIZE):
            self._response_cache[address] = None
            return None
        data = self._read(address, _RESPONSE_OBJECT_SIZE)
        if len(data) != _RESPONSE_OBJECT_SIZE:
            self._response_cache[address] = None
            return None
        vptr = _u64(data, 0)
        if not self._is_image_pointer(vptr):
            self._response_cache[address] = None
            return None
        candidate = self._parse_response_bytes(address, data)
        self._response_cache[address] = candidate
        return candidate

    def _read_std_string(
        self,
        data: bytes,
        offset: int,
        *,
        max_len: int,
        allow_empty: bool,
    ) -> str | None:
        if offset + _STD_STRING_SIZE > len(data):
            return None
        size = _u64(data, offset + 0x10)
        capacity = _u64(data, offset + 0x18)
        if size > max_len or capacity > max(max_len * 2, 16 * 1024 * 1024):
            return None
        if capacity < size:
            return None
        if capacity < 16:
            if size > 15:
                return None
            raw = data[offset:offset + int(size)]
        else:
            ptr = _u64(data, offset)
            if not self._is_readable_address(ptr, int(size)):
                return None
            raw = self._read(ptr, int(size))
            if len(raw) != size:
                return None
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
        if not _looks_like_text(text, allow_empty=allow_empty):
            return None
        return text

    def _read_std_string_at(
        self,
        address: int,
        *,
        max_len: int,
        allow_empty: bool,
    ) -> str | None:
        if not self._is_readable_address(address, _STD_STRING_SIZE):
            return None
        data = self._read(address, _STD_STRING_SIZE)
        if len(data) != _STD_STRING_SIZE:
            return None
        return self._read_std_string(data, 0, max_len=max_len, allow_empty=allow_empty)

    def _score_response(self, title: str, text: str, need_margin: int) -> int:
        combined = f"{title}\n{text}"
        score = 0
        if _has_japanese(text):
            score += 8
        elif any(ch.isalpha() for ch in text):
            score += 3
        score += min(len(text) // 32, 10)
        if title.strip():
            score += 4
            if _has_japanese(title):
                score += 2
            if len(text) < 40 and (title == text or title.startswith(f"{text}:")):
                score -= 8
        if need_margin in (0, 1):
            score += 1
        for marker in ("%PCName", "sol::", "->", "::", "<script"):
            if marker in combined:
                score -= 6
        if len(text.splitlines()) > 20:
            score -= 3
        return score

    def _score_history_array(self, responses: list[_ResponseCandidate]) -> int:
        score = len(responses) * 20 + sum(max(0, r.score) for r in responses)
        if responses and not responses[0].title.strip():
            score += 4
        score += sum(3 for r in responses if r.title.strip())
        duplicate_count = len(responses) - len({(r.title, r.text) for r in responses})
        score -= duplicate_count * 8
        return score


__all__ = [
    "OpenMwConversationReader",
    "OpenMwConversationResult",
    "OpenMwDialogueChoice",
    "OpenMwDialogueResponse",
    "OpenMwProcessInfo",
]
