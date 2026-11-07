@echo off
rem kana-ime first-time setup (double-click to run).
rem Japanese messages are printed by setup_kana_ime.py
rem (cmd.exe misreads batch files that contain Japanese text).
cd /d "%~dp0"
python -c "import sys, struct; sys.exit(0 if sys.version_info >= (3, 10) and struct.calcsize('P') == 8 else 1)" 2>nul
if errorlevel 1 (
    echo.
    echo Python 3.10 or later ^(64-bit^) was not found.
    echo Install it from https://www.python.org/downloads/
    echo and check "Add python.exe to PATH" during installation.
    echo.
    pause
    exit /b 1
)
python setup_kana_ime.py
set RESULT=%ERRORLEVEL%
echo.
pause
exit /b %RESULT%
