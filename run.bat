@echo off
setlocal
cd /d "%~dp0"
start http://localhost:8888
.\.venv\Scripts\python.exe app.py
