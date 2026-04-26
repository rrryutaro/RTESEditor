@echo off
cd /d "%~dp0"
pyinstaller RTESEditor.spec --noconfirm
echo.
echo ビルド完了
pause
