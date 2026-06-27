from __future__ import annotations

import faulthandler
import sys
import threading
import time
import traceback
from pathlib import Path


_LOG_FILE = None
_LOG_LOCK = threading.Lock()
_WATCHDOG_THREAD = None
_WATCHDOG_TIMER = None


def _log_path() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).parent
    else:
        root = Path(__file__).resolve().parent.parent / "dist" / "RTESEditor"
    root.mkdir(parents=True, exist_ok=True)
    return root / "rteseditor_crash.log"


def install() -> None:
    global _LOG_FILE
    try:
        _LOG_FILE = _log_path().open("a", encoding="utf-8")
        _LOG_FILE.write("\n--- RTESEditor diagnostics start ---\n")
        _LOG_FILE.flush()
        faulthandler.enable(_LOG_FILE)
    except Exception:  # noqa: BLE001
        _LOG_FILE = None

    previous_hook = sys.excepthook

    def _hook(exc_type, exc, tb) -> None:
        try:
            path = _log_path()
            with path.open("a", encoding="utf-8") as fp:
                fp.write("\nUnhandled exception:\n")
                traceback.print_exception(exc_type, exc, tb, file=fp)
        except Exception:  # noqa: BLE001
            pass
        previous_hook(exc_type, exc, tb)

    sys.excepthook = _hook


def install_qt_stall_watchdog(app, *, interval_ms: int = 500, threshold_seconds: float = 6.0) -> None:
    """Qt UI thread stalls のスタックをログに残す。

    例外やクラッシュではないフリーズは faulthandler だけでは残らないため、
    UIスレッドのQTimer更新が一定時間止まった場合に全スレッドのPythonスタックを出力する。
    """
    global _WATCHDOG_THREAD, _WATCHDOG_TIMER
    if _WATCHDOG_THREAD is not None:
        return
    try:
        from PySide6.QtCore import QTimer
    except Exception:  # noqa: BLE001
        return

    state = {"last_tick": time.monotonic(), "reported": False}
    timer = QTimer(app)
    timer.setInterval(max(100, interval_ms))

    def tick() -> None:
        state["last_tick"] = time.monotonic()
        state["reported"] = False

    timer.timeout.connect(tick)
    timer.start()
    _WATCHDOG_TIMER = timer

    def watch() -> None:
        while True:
            time.sleep(max(0.5, threshold_seconds / 2))
            elapsed = time.monotonic() - state["last_tick"]
            if elapsed < threshold_seconds or state["reported"]:
                continue
            state["reported"] = True
            try:
                with _LOG_LOCK:
                    if _LOG_FILE is not None:
                        _LOG_FILE.write(f"\n--- Qt UI stall detected ({elapsed:.1f}s) ---\n")
                        faulthandler.dump_traceback(file=_LOG_FILE, all_threads=True)
                        _LOG_FILE.flush()
                    else:
                        path = _log_path()
                        with path.open("a", encoding="utf-8") as fp:
                            fp.write(f"\n--- Qt UI stall detected ({elapsed:.1f}s) ---\n")
                            faulthandler.dump_traceback(file=fp, all_threads=True)
            except Exception:  # noqa: BLE001
                pass

    _WATCHDOG_THREAD = threading.Thread(target=watch, daemon=True)
    _WATCHDOG_THREAD.start()


__all__ = ["install", "install_qt_stall_watchdog"]
