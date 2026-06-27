from __future__ import annotations

import ctypes
import struct
import sys
import time
from dataclasses import dataclass, field
from ctypes import wintypes

from app.openmw_conversation_reader import (
    OpenMwProcessInfo,
    _OPENMW_EXE_NAME,
    _PROCESS_QUERY_INFORMATION,
    _PROCESS_QUERY_LIMITED_INFORMATION,
    _PROCESS_VM_READ,
    _TH32CS_SNAPPROCESS,
    _INVALID_HANDLE_VALUE,
    _WinApi,
    _PROCESSENTRY32W,
    _ProcessScanner,
    _as_int,
    _last_error,
    _looks_like_asset_or_path,
    _u64,
)


_BOOK_LAYOUT_NAME = "openmw_book.layout"
_SCROLL_LAYOUT_NAME = "openmw_scroll.layout"
_LAYOUT_NAME_OFFSET = 0x30
_BOOK_PTR_OFFSETS = (0x2C0,)
_SCROLL_PTR_OFFSETS = (0x278, 0x280, 0x270)
_WINDOW_SCAN_SIZE = 0xA00
_LIVE_CELL_REF_BASE_OFFSET = 0x1E8
_ESM_BOOK_NAME_OFFSET = 0x18
_ESM_BOOK_TEXT_OFFSET = 0x78


@dataclass(frozen=True)
class OpenMwDisplayedBook:
    title: str
    text: str
    source: str
    book_record: int = 0
    live_ref: int = 0
    window_base: int = 0
    ptr_offset: int = 0


@dataclass
class OpenMwDisplayedBookResult:
    process: OpenMwProcessInfo | None = None
    book: OpenMwDisplayedBook | None = None
    diagnostics: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class _WindowCandidate:
    base: int
    layout_name: str
    source: str


@dataclass(frozen=True)
class _BookWindowCache:
    pid: int
    process_path: str
    base: int
    layout_name: str
    ptr_offset: int


class OpenMwBookReader:
    """Windows専用の OpenMW 表示中Book/Scrollリーダー。"""

    def __init__(
        self,
        *,
        scan_limit_mb: int = 256,
        chunk_size: int = 4 * 1024 * 1024,
    ) -> None:
        self._scan_limit = max(64, scan_limit_mb) * 1024 * 1024
        self._chunk_size = max(256 * 1024, chunk_size)
        self._api: _WinApi | None = None
        self._window_cache: dict[int, _BookWindowCache] = {}

    def read_current_book(self, *, force_rescan: bool = False) -> OpenMwDisplayedBookResult:
        if not sys.platform == "win32":
            return OpenMwDisplayedBookResult(
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
            return OpenMwDisplayedBookResult(diagnostics=diagnostics)

        results: list[OpenMwDisplayedBookResult] = []
        if not force_rescan:
            for process in processes:
                cached = self._read_cached_process(process)
                if cached.book is not None:
                    cached.diagnostics = {**diagnostics, **cached.diagnostics}
                    return cached
                if cached.diagnostics:
                    results.append(cached)

        for process in processes:
            results.append(self._scan_process(process))

        best = max(results, key=self._result_score)
        diagnostics["status"] = "ok" if best.book is not None else "no_displayed_book"
        diagnostics["process_results"] = [r.diagnostics for r in results]
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

    def _read_cached_process(self, process: OpenMwProcessInfo) -> OpenMwDisplayedBookResult:
        cache = self._window_cache.get(process.pid)
        if cache is None or cache.process_path != process.path:
            return OpenMwDisplayedBookResult(
                process=process,
                diagnostics={"pid": process.pid, "status": "cache_missing"},
            )
        scan = self._open_scanner(process)
        if scan is None:
            return OpenMwDisplayedBookResult(
                process=process,
                diagnostics={"pid": process.pid, "status": "cache_open_failed", "last_error": _last_error()},
            )
        handle, scanner, started = scan
        try:
            scanner.enumerate_regions()
            book = self._read_book_from_window(
                scanner,
                cache.base,
                cache.layout_name,
                preferred_offsets=(cache.ptr_offset,),
            )
            if book is None:
                return OpenMwDisplayedBookResult(
                    process=process,
                    diagnostics={"pid": process.pid, "status": "cache_book_not_found"},
                )
            return OpenMwDisplayedBookResult(
                process=process,
                book=book,
                diagnostics={
                    "pid": process.pid,
                    "status": "cache_ok",
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    **scanner.diagnostics(),
                    "window_base": f"0x{book.window_base:X}",
                    "ptr_offset": f"0x{book.ptr_offset:X}",
                    "visible_state": "not_verified",
                },
            )
        finally:
            self._api.CloseHandle(handle)

    def _scan_process(self, process: OpenMwProcessInfo) -> OpenMwDisplayedBookResult:
        scan = self._open_scanner(process)
        if scan is None:
            return OpenMwDisplayedBookResult(
                process=process,
                diagnostics={"pid": process.pid, "status": "open_failed", "last_error": _last_error()},
            )
        handle, scanner, started = scan
        try:
            regions = sorted(scanner.enumerate_regions(), key=lambda r: r.base, reverse=True)
            candidates = self._find_window_candidates(scanner, regions, _BOOK_LAYOUT_NAME, source="book")
            best_book = self._best_book_from_candidates(scanner, candidates)
            if best_book is None:
                scroll_candidates = self._find_window_candidates(scanner, regions, _SCROLL_LAYOUT_NAME, source="scroll")
                candidates.extend(scroll_candidates)
                best_book = self._best_book_from_candidates(scanner, scroll_candidates)
            diagnostics: dict[str, object] = {
                "pid": process.pid,
                "status": "window_candidates_found" if candidates else "window_not_found",
                "window_candidates": [f"0x{c.base:X}:{c.source}" for c in candidates[:8]],
            }
            if best_book is not None:
                self._window_cache[process.pid] = _BookWindowCache(
                    pid=process.pid,
                    process_path=process.path,
                    base=best_book.window_base,
                    layout_name=_BOOK_LAYOUT_NAME if best_book.source == "book" else _SCROLL_LAYOUT_NAME,
                    ptr_offset=best_book.ptr_offset,
                )
                diagnostics.update(
                    {
                        "status": "ok",
                        "window_base": f"0x{best_book.window_base:X}",
                        "ptr_offset": f"0x{best_book.ptr_offset:X}",
                        "book_record": f"0x{best_book.book_record:X}",
                        "live_ref": f"0x{best_book.live_ref:X}",
                        "title": best_book.title,
                        "text_len": len(best_book.text),
                        "visible_state": "not_verified",
                    }
                )
            elif candidates:
                candidate = candidates[0]
                self._window_cache[process.pid] = _BookWindowCache(
                    pid=process.pid,
                    process_path=process.path,
                    base=candidate.base,
                    layout_name=candidate.layout_name,
                    ptr_offset=_SCROLL_PTR_OFFSETS[0] if candidate.source == "scroll" else _BOOK_PTR_OFFSETS[0],
                )
            diagnostics.update(
                {
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    **scanner.diagnostics(),
                }
            )
            return OpenMwDisplayedBookResult(process=process, book=best_book, diagnostics=diagnostics)
        finally:
            self._api.CloseHandle(handle)

    def _best_book_from_candidates(
        self,
        scanner: _ProcessScanner,
        candidates: list[_WindowCandidate],
    ) -> OpenMwDisplayedBook | None:
        best_book: OpenMwDisplayedBook | None = None
        for candidate in candidates:
            book = self._read_book_from_window(scanner, candidate.base, candidate.layout_name)
            if book is None:
                continue
            if best_book is None or self._book_score(book) > self._book_score(best_book):
                best_book = book
        return best_book

    def _open_scanner(self, process: OpenMwProcessInfo):
        assert self._api is not None
        access = _PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ
        handle = self._api.OpenProcess(access, False, process.pid)
        if not handle:
            return None
        scanner = _ProcessScanner(
            self._api,
            handle,
            scan_limit=self._scan_limit,
            chunk_size=self._chunk_size,
            max_response_objects=1,
        )
        return handle, scanner, time.perf_counter()

    def _find_window_candidates(
        self,
        scanner: _ProcessScanner,
        regions,
        layout_name: str,
        *,
        source: str,
    ) -> list[_WindowCandidate]:
        pattern = layout_name.encode("utf-8")
        layout_buffers: list[int] = []
        for chunk_base, data in scanner._iter_scan_chunks(regions, purpose="layout"):
            start = 0
            while len(layout_buffers) < 8:
                pos = data.find(pattern, start)
                if pos < 0:
                    break
                address = chunk_base + pos
                if address not in layout_buffers:
                    layout_buffers.append(address)
                start = pos + 1
            if len(layout_buffers) >= 8:
                break
        if not layout_buffers:
            return []

        candidates: list[_WindowCandidate] = []
        seen: set[int] = set()
        pointer_patterns = [struct.pack("<Q", address) for address in layout_buffers]
        pointer_regions = self._nearby_regions(regions, layout_buffers) or regions
        for chunk_base, data in scanner._iter_scan_chunks(pointer_regions, purpose="layout"):
            for pointer_pattern in pointer_patterns:
                start = 0
                while True:
                    pos = data.find(pointer_pattern, start)
                    if pos < 0:
                        break
                    string_object = chunk_base + pos
                    start = pos + 1
                    if string_object & 0x7:
                        continue
                    base = string_object - _LAYOUT_NAME_OFFSET
                    if base in seen:
                        continue
                    seen.add(base)
                    if not self._looks_like_window_base(scanner, base, layout_name):
                        continue
                    candidates.append(_WindowCandidate(base=base, layout_name=layout_name, source=source))
                    return candidates
        return candidates

    @staticmethod
    def _nearby_regions(regions, addresses: list[int]):
        ranges = [
            (max(0, address - 64 * 1024 * 1024), address + 256 * 1024 * 1024)
            for address in addresses
        ]
        nearby = [
            region
            for region in regions
            if any(region.end > start and region.base < end for start, end in ranges)
        ]
        return sorted(nearby, key=lambda r: r.base, reverse=True)

    def _looks_like_window_base(self, scanner: _ProcessScanner, base: int, layout_name: str) -> bool:
        if base <= 0 or base & 0x7:
            return False
        if scanner._read_std_string_at(
            base + _LAYOUT_NAME_OFFSET,
            max_len=128,
            allow_empty=False,
        ) != layout_name:
            return False
        raw = scanner._read(base, 16)
        if len(raw) != 16:
            return False
        vptr = _u64(raw, 0)
        main_widget = _u64(raw, 8)
        return scanner._is_image_pointer(vptr) and scanner._is_readable_address(main_widget, 8)

    def _read_book_from_window(
        self,
        scanner: _ProcessScanner,
        window_base: int,
        layout_name: str,
        *,
        preferred_offsets: tuple[int, ...] | None = None,
    ) -> OpenMwDisplayedBook | None:
        source = "scroll" if layout_name == _SCROLL_LAYOUT_NAME else "book"
        offsets = preferred_offsets or (_SCROLL_PTR_OFFSETS if source == "scroll" else _BOOK_PTR_OFFSETS)
        candidates: list[OpenMwDisplayedBook] = []
        for offset in offsets:
            book = self._read_book_from_ptr(scanner, window_base + offset, window_base, offset, source)
            if book is not None:
                candidates.append(book)

        raw = scanner._read(window_base, _WINDOW_SCAN_SIZE)
        for offset in range(0x100, max(0, len(raw) - 24), 8):
            if any(offset == known for known in offsets):
                continue
            live_ref = _u64(raw, offset)
            book = self._read_book_from_live_ref(scanner, live_ref, window_base, offset, source)
            if book is not None:
                candidates.append(book)

        if not candidates:
            return None
        return max(candidates, key=self._book_score)

    def _read_book_from_ptr(
        self,
        scanner: _ProcessScanner,
        ptr_address: int,
        window_base: int,
        ptr_offset: int,
        source: str,
    ) -> OpenMwDisplayedBook | None:
        raw = scanner._read(ptr_address, 24)
        if len(raw) != 24:
            return None
        live_ref = _u64(raw, 0)
        return self._read_book_from_live_ref(scanner, live_ref, window_base, ptr_offset, source)

    def _read_book_from_live_ref(
        self,
        scanner: _ProcessScanner,
        live_ref: int,
        window_base: int,
        ptr_offset: int,
        source: str,
    ) -> OpenMwDisplayedBook | None:
        if not live_ref or not scanner._is_readable_address(live_ref + _LIVE_CELL_REF_BASE_OFFSET, 8):
            return None
        raw = scanner._read(live_ref + _LIVE_CELL_REF_BASE_OFFSET, 8)
        if len(raw) != 8:
            return None
        book_record = _u64(raw, 0)
        if not book_record or not scanner._is_readable_address(book_record + _ESM_BOOK_TEXT_OFFSET, 0x20):
            return None

        title = scanner._read_std_string_at(
            book_record + _ESM_BOOK_NAME_OFFSET,
            max_len=512,
            allow_empty=True,
        )
        text = scanner._read_std_string_at(
            book_record + _ESM_BOOK_TEXT_OFFSET,
            max_len=200000,
            allow_empty=True,
        )
        if title is None or text is None:
            return None
        title = title.strip()
        text = text.strip()
        if not title and not text:
            return None
        if _looks_like_asset_or_path(title):
            return None
        return OpenMwDisplayedBook(
            title=title or "(無題)",
            text=text,
            source=source,
            book_record=book_record,
            live_ref=live_ref,
            window_base=window_base,
            ptr_offset=ptr_offset,
        )

    @staticmethod
    def _book_score(book: OpenMwDisplayedBook) -> int:
        score = 0
        if book.source == "book":
            score += 20
        if book.title and book.title != "(無題)":
            score += 30
        score += min(len(book.text) // 20, 80)
        if "<" in book.text and ">" in book.text:
            score += 8
        if any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in book.title + book.text[:200]):
            score += 20
        return score

    @staticmethod
    def _result_score(result: OpenMwDisplayedBookResult) -> int:
        if result.book is None:
            return 0
        return 1000 + OpenMwBookReader._book_score(result.book)
