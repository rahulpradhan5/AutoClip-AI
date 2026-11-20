@echo off
title AutoClip-AI System Doctor
echo ========================================================
echo  AutoClip-AI System Diagnostics & Environment Doctor
echo ========================================================
.\.venv\Scripts\python.exe scripts\doctor.py
if %ERRORLEVEL% NEQ 0 (
    python scripts\doctor.py
)
pause
