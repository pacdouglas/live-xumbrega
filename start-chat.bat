@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "http://localhost:8080/xumbr3ga-multichat.html"
python server.py
pause
