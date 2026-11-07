@echo off
rem Launch kana-ime (double-click to run).
rem The console stays open on error so the message can be read.
cd /d "%~dp0"
python main.py
if errorlevel 1 (
    echo.
    echo Failed to start. Please check the error above.
    pause
)
