import sys
import tempfile
from pathlib import Path
from app.runtime_diagnostics import (
    install as install_runtime_diagnostics,
    install_qt_stall_watchdog,
)
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QLockFile, QTranslator, QLocale, QLibraryInfo
from ui.main_window import MainWindow
from app.settings import Settings
from ui.theme import apply_theme
from version import version_string


def _acquire_single_instance_lock() -> QLockFile | None:
    lock_path = Path(tempfile.gettempdir()) / "RTESEditor.lock"
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(30000)
    if not lock.tryLock(0):
        print("RTESEditor is already running.")
        return None
    return lock


def main():
    install_runtime_diagnostics()
    instance_lock = _acquire_single_instance_lock()
    if instance_lock is None:
        return

    app = QApplication(sys.argv)
    install_qt_stall_watchdog(app)
    app.setApplicationName("RTESEditor")
    app.setApplicationVersion(version_string())

    settings = Settings.instance()
    apply_theme(app, settings.get_theme())

    # ロケールに応じた翻訳ファイルを読込む
    locale = QLocale.system().name()  # 例: "ja_JP"
    translator = QTranslator()
    if getattr(sys, "frozen", False):
        i18n_path = Path(sys._MEIPASS) / "i18n"
    else:
        i18n_path = Path(__file__).parent / "i18n"
    if translator.load(str(i18n_path / locale)):
        app.installTranslator(translator)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
