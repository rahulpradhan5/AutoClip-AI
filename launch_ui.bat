@echo off
setlocal
cd /d "%~dp0"
echo ========================================================
echo  UNIVERSAL CLIPPER STUDIO - LAUNCHING LOCAL WEB UI
echo ========================================================
echo Opening browser at http://localhost:8888 ...
start http://localhost:8888
.\.venv\Scripts\python.exe app.py
pause
