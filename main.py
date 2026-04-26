import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTranslator, QLocale, QLibraryInfo
from ui.main_window import MainWindow
from app.settings import Settings
from ui.theme import apply_theme


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RTESEditor")
    app.setApplicationVersion("0.1.0")

    settings = Settings.instance()
    apply_theme(app, settings.get_theme())

    # ロケールに応じた翻訳ファイルを読込む
    locale = QLocale.system().name()  # 例: "ja_JP"
    translator = QTranslator()
    if getattr(sys, "frozen", False):
        from pathlib import Path
        i18n_path = Path(sys._MEIPASS) / "i18n"
    else:
        from pathlib import Path
        i18n_path = Path(__file__).parent / "i18n"
    if translator.load(str(i18n_path / locale)):
        app.installTranslator(translator)

    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
