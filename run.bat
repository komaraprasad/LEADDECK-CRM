@echo off
setlocal
cd /d "%~dp0"
title LeadDeck - Phase 5 (analytics)
echo ============================================================
echo   LeadDeck - Phase 5 (analytics) (leads layer)  http://localhost:3000
echo ============================================================
where python >nul 2>nul || (echo Python is not installed or not on PATH. & pause & exit /b 1)
if not exist .venv ( python -m venv .venv || (pause & exit /b 1) )
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt || (echo Package install failed. Check your internet connection. & pause & exit /b 1)
python scripts\init_env.py
set PORT=3000
start "" "http://localhost:3000"
echo Keep this window open. Close it to stop.
python wsgi.py
pause
