@echo off
setlocal
cd /d "%~dp0"

set "PYINSTALLER=pyinstaller"
if exist "%~dp0..\.venv-build\Scripts\pyinstaller.exe" (
    set "PYINSTALLER=%~dp0..\.venv-build\Scripts\pyinstaller.exe"
)

"%PYINSTALLER%" RTESEditor.spec --noconfirm
if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Build completed.
pause
