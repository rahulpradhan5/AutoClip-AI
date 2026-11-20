@echo off
title Pushing AutoClip-AI to GitHub...
echo ========================================================
echo Pushing AutoClip-AI to https://github.com/rahulpradhan5/AutoClip-AI.git
echo ========================================================
git push -u origin main --force
if %ERRORLEVEL% EQU 0 (
    echo ========================================================
    echo SUCCESS: Project pushed successfully to GitHub!
    echo Check it live at: https://github.com/rahulpradhan5/AutoClip-AI
    echo ========================================================
) else (
    echo ========================================================
    echo ERROR: Push failed. Check your GitHub permissions/login.
    echo ========================================================
)
pause
