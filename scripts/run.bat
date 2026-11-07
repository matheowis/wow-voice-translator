@echo off
rem Starts WoW Voice Translator in the background (tray icon). Add --show to open the window.
cd /d "%~dp0.."
start "" ".venv\Scripts\pythonw.exe" -m wvt %*
