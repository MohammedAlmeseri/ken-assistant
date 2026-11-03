@echo off
rem Double-click to start the assistant (no console window stays open).
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m assistant.ui
