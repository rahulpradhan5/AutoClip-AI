@echo off
setlocal
cd /d "%~dp0"
echo ========================================================
echo  UNIVERSAL CLIPPER - 1-CLICK LAUNCHER
echo ========================================================
echo Reading target from CLIP_INPUT.txt...
.\.venv\Scripts\python.exe clip.py %*
pause
